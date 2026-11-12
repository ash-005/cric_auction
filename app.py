"""Cricket Auction - real-time, room-based auction server.

Design notes
------------
* The server is the single source of truth. Every room mutation happens while
  holding that room's lock, and after each change the full public room state
  is broadcast as one ``state`` event (with a monotonically increasing
  ``v`` version so clients can drop out-of-order snapshots).
* The bid clock is run by one server-side ticker task, not by any client, so a
  sleeping/disconnected owner tab can no longer freeze the auction.
* Every bid carries the ``lot_id`` it was made for and must equal exactly the
  next valid amount. Two people clicking at the same moment: the first wins,
  the second gets "bid out of date". Bids during the sold pause or for an old
  player are rejected.
* Identity comes from the socket session (sid -> team), never from a name the
  client sends. A secret token kept in the browser lets a team rejoin after a
  refresh or a dropped connection.
* All money is stored as integer lakhs (1 Cr = 100 L) to avoid float drift.
"""
import csv
import functools
import io
import logging
import math
import os
import random
import secrets
import threading
import time
import uuid
from collections import deque

from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, leave_room

def _detect_async_mode():
    """gevent when running under gunicorn's gevent worker (production: thousands
    of connections in one process); plain threads otherwise (python app.py)."""
    forced = os.environ.get('SOCKETIO_ASYNC_MODE')
    if forced:
        return forced
    try:
        from gevent import monkey
        if monkey.is_module_patched('socket'):
            return 'gevent'
    except ImportError:
        pass
    return 'threading'


ASYNC_MODE = _detect_async_mode()
app = Flask(__name__)
log = logging.getLogger('auction')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cricket_auction_secret')

# Production runs one gunicorn gevent worker (see render.yaml), which holds
# thousands of WebSocket connections in a single process. Locally, python app.py
# uses plain threads with simple-websocket. The mode is picked automatically.
socketio = SocketIO(
    app,
    cors_allowed_origins=os.environ.get('CORS_ORIGINS', '*'),
    async_mode=ASYNC_MODE,
    ping_interval=10,
    ping_timeout=20,
    max_http_buffer_size=3_000_000,
    logger=False,
    engineio_logger=False,
)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
CR = 100  # lakhs per crore
ROLES = ['Batsman', 'Bowler', 'All-rounder', 'Wicket-keeper']
TIERS = [  # (batch name, minimum overall rating)
    ('Top players', 85),
    ('Mid players', 72),
    ('Low players', 0),
]
SETTING_CHOICES = {
    'team_purse': [50, 90, 100, 120, 150],          # crore
    'squad_size': list(range(5, 26)),
    'max_teams': list(range(2, 13)),
    'bid_timer': list(range(5, 121)),                 # seconds, any value 5-120
    'break_seconds': list(range(5, 121)),             # pause between batches
    'auction_order': ['tiered', 'tiered_skill', 'roles', 'roles_skill', 'random', 'skill_desc', 'alphabetical'],
}
ROLE_BATCHES = [  # (role, batch name) in the order role batches run by default
    ('Batsman', 'Batsmen'),
    ('Wicket-keeper', 'Wicket-keepers'),
    ('All-rounder', 'All-rounders'),
    ('Bowler', 'Bowlers'),
]
DEFAULT_SETTINGS = {
    'team_purse': 100,
    'squad_size': 11,
    'max_teams': 8,
    'bid_timer': 15,
    'break_seconds': 20,
    'auction_order': 'tiered',
}
BID_INCREMENTS = [50, 100]          # +50 L, +1 Cr
SOLD_PAUSE_SECONDS = 3
TIMER_RANGE = (5, 120)
OWNER_HANDOVER_SECONDS = 45
RESTART_VOTE_SECONDS = 60
RESTART_VOTE_SHARE = 2 / 3      # share of connected teams that must vote yes
MAX_PLAYERS_IN_POOL = 2000
# Each open WebSocket holds one gunicorn thread (start command: --threads 400).
# Refusing connections past this cap keeps threads free for /health and for
# players already in a room, instead of the whole server going unresponsive.
MAX_CONNECTIONS = int(os.environ.get('MAX_CONNECTIONS', 5000 if ASYNC_MODE == 'gevent' else 320))
MAX_ROOMS = 1500                # memory guard: a room on the built-in list is ~15 KB
IDLE_ROOM_TTL = 30 * 60
FINISHED_ROOM_TTL = 6 * 60 * 60


# --------------------------------------------------------------------------
# Player data helpers
# --------------------------------------------------------------------------
def normalize_role(raw):
    """Map the many spellings seen in CSVs onto the four canonical roles."""
    s = ''.join(ch for ch in str(raw or '').lower() if ch.isalpha())
    if not s:
        return 'Batsman'
    if 'keep' in s or s in ('wk', 'wkt', 'wicketkeeper') or s.startswith('wk'):
        return 'Wicket-keeper'
    if 'all' in s or s in ('ar', 'allrounder'):
        return 'All-rounder'
    if 'bowl' in s or s in ('bwl', 'bw'):
        return 'Bowler'
    if 'bat' in s:
        return 'Batsman'
    return 'Batsman'


def base_price_for(overall):
    """Base price in lakhs, derived from the skill rating."""
    if overall >= 91:
        return random.choice([450, 500])
    if overall >= 86:
        return random.choice([300, 350, 400])
    if overall >= 77:
        return random.choice([150, 200, 250])
    if overall >= 70:
        return random.choice([100, 150])
    if overall >= 60:
        return 100
    return 50


def tier_for(overall):
    for name, minimum in TIERS:
        if overall >= minimum:
            return name
    return TIERS[-1][0]


HEADER_ALIASES = {
    'name': ['name', 'player', 'playername', 'player_name', 'fullname'],
    'overall': ['overall', 'rating', 'ovr', 'skill', 'skillrating', 'overallrating'],
    'role': ['role', 'type', 'playerrole', 'speciality', 'specialty'],
    'nationality': ['nationality', 'country', 'nation', 'team'],
}


def parse_players_csv(text):
    """Parse a player CSV. Columns are matched by header name (any order).
    Falls back to the legacy positional layout Name,Overall,Role,Nationality.
    Returns (players, skipped_row_count)."""
    text = (text or '').lstrip('﻿')
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return [], 0

    header = [''.join(ch for ch in h.lower() if ch.isalnum() or ch == '_') for h in rows[0]]
    index = {}
    for key, aliases in HEADER_ALIASES.items():
        for i, h in enumerate(header):
            if h in aliases and i not in index.values():
                index[key] = i
                break
    if 'name' in index and 'overall' in index:
        body = rows[1:]
    else:
        index = {'name': 0, 'overall': 1, 'role': 2, 'nationality': 3}
        # Skip the first row if it does not look like data.
        body = rows[1:] if not _is_int(rows[0][1] if len(rows[0]) > 1 else '') else rows

    players, skipped, seen = [], 0, set()
    for r in body:
        def col(key):
            i = index.get(key)
            return r[i].strip() if i is not None and i < len(r) else ''
        name = col('name')[:60]
        overall_raw = col('overall')
        if not name or not _is_int(overall_raw):
            skipped += 1
            continue
        overall = max(0, min(100, int(float(overall_raw))))
        key = name.lower()
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        players.append({
            'name': name,
            'overall': overall,
            'role': normalize_role(col('role')),
            'nationality': col('nationality')[:40] or '-',
        })
        if len(players) >= MAX_PLAYERS_IN_POOL:
            break
    return players, skipped


def _is_int(s):
    try:
        float(str(s).strip())
        return True
    except ValueError:
        return False


def load_default_players():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'players.csv')
    try:
        with open(path, encoding='utf-8') as fh:
            return parse_players_csv(fh.read())[0]
    except OSError:
        return []


DEFAULT_PLAYERS = load_default_players()


def make_records(players):
    """Immutable per-player records (no price). pids only need to be unique within a room."""
    records = {}
    for i, p in enumerate(players):
        overall = int(p['overall'])
        pid = f'p{i}'
        records[pid] = {
            'pid': pid,
            'name': p['name'],
            'overall': overall,
            'role': normalize_role(p.get('role')),
            'nationality': p.get('nationality') or '-',
            'tier': tier_for(overall),
        }
    return records


# Built once and shared by every room that uses the built-in list (saves ~180 KB per room).
DEFAULT_RECORDS = make_records(DEFAULT_PLAYERS)


class RoomPool:
    """A room's player pool: shared player records plus this room's base prices.
    pool[pid] returns a fresh dict that includes 'base', like a plain dict of dicts."""
    __slots__ = ('records', 'base')

    def __init__(self, records):
        self.records = records
        self.reroll()

    def reroll(self):
        self.base = {pid: base_price_for(r['overall']) for pid, r in self.records.items()}

    def __getitem__(self, pid):
        return {**self.records[pid], 'base': self.base[pid]}

    def __contains__(self, pid):
        return pid in self.records

    def __iter__(self):
        return iter(self.records)

    def __len__(self):
        return len(self.records)

    def values(self):
        return [self[pid] for pid in self.records]


# --------------------------------------------------------------------------
# Room model (pure logic, always called with room.lock held)
# --------------------------------------------------------------------------
class AuctionError(Exception):
    pass


class Room:
    def __init__(self, code, players=None, records=None):
        self.code = code
        self.lock = threading.Lock()
        self.pool = RoomPool(records if records is not None else make_records(players or []))
        self.teams = {}          # team_id -> team dict, insertion order = join order
        self.owner_id = None
        self.settings = dict(DEFAULT_SETTINGS)
        self.phase = 'lobby'     # lobby | bidding | sold_pause | batch_break | finished
        self.batches = []        # [{'name', 'queue': [pid], 'total'}]
        self.batch_idx = -1
        self.pending_batch = None  # batch waiting to start during batch_break
        self.lot = None
        self.lot_seq = 0
        self.paused = False
        self.paused_remaining = None
        self.phase_until = None
        self.last_result = None
        self.unsold = []         # pids that went under the hammer with no sale
        self.skipped = []        # pids the auctioneer skipped (never re-auctioned)
        self.unsold_round_done = False
        self.sale_seq = 0
        self.log = deque(maxlen=40)
        self.log_seq = 0
        self.version = 0
        self.vote = None         # restart vote on the results screen, see propose_restart
        self.last_tick = None
        self.empty_since = None  # when the last connected team left (None while someone is here)
        self.created_at = time.time()
        self.last_active = time.time()

    # ---- helpers -------------------------------------------------------
    def _log(self, text, kind='info'):
        self.log_seq += 1
        self.log.append({'id': self.log_seq, 'text': text, 'kind': kind, 't': time.time()})

    def purse_total(self):
        return self.settings['team_purse'] * CR

    def team_spent(self, team):
        return sum(p['price'] for p in team['squad'])

    def purse_left(self, team):
        return self.purse_total() - self.team_spent(team)

    def slots_left(self, team):
        return self.settings['squad_size'] - len(team['squad'])

    def can_afford(self, team, amount):
        return self.slots_left(team) > 0 and self.purse_left(team) >= amount

    def anyone_can_buy(self, player):
        return any(self.can_afford(t, player['base']) for t in self.teams.values())

    def all_squads_full(self):
        return bool(self.teams) and all(self.slots_left(t) <= 0 for t in self.teams.values())

    def team_by_token(self, token):
        for t in self.teams.values():
            if token and secrets.compare_digest(t['token'], str(token)):
                return t
        return None

    def require_owner(self, team_id):
        if team_id != self.owner_id:
            raise AuctionError('Only the auctioneer (room owner) can do that.')

    # ---- lobby ---------------------------------------------------------
    def add_team(self, name, team_name, sid):
        name = (name or '').strip()[:30]
        team_name = (team_name or '').strip()[:30] or name
        if not name:
            raise AuctionError('Please enter your name.')
        if self.phase != 'lobby':
            raise AuctionError('This auction has already started. Ask the owner for a new room.')
        if len(self.teams) >= self.settings['max_teams']:
            raise AuctionError(f"Room is full ({self.settings['max_teams']} teams max).")
        if any(t['team'].lower() == team_name.lower() for t in self.teams.values()):
            raise AuctionError(f'Team name "{team_name}" is taken. Pick another.')
        team_id = uuid.uuid4().hex[:8]
        team = {
            'id': team_id,
            'name': name,
            'team': team_name,
            'token': secrets.token_urlsafe(18),
            'sid': sid,
            'connected': True,
            'disconnected_at': None,
            'squad': [],
        }
        self.teams[team_id] = team
        if self.owner_id is None:
            self.owner_id = team_id
        self._log(f"{team_name} joined", 'join')
        return team

    def remove_team(self, team_id):
        team = self.teams.pop(team_id, None)
        if not team:
            return
        self._log(f"{team['team']} left", 'leave')
        if self.owner_id == team_id:
            self.owner_id = next(iter(self.teams), None)
            if self.owner_id:
                self._log(f"{self.teams[self.owner_id]['team']} is now the auctioneer", 'info')

    def update_settings(self, team_id, data):
        self.require_owner(team_id)
        if self.phase != 'lobby':
            raise AuctionError('Settings are locked once the auction starts.')
        for key, choices in SETTING_CHOICES.items():
            if key in data:
                value = data[key]
                if key != 'auction_order':
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        raise AuctionError(f'Invalid value for {key}.')
                if value not in choices:
                    if key in ('bid_timer', 'break_seconds'):
                        raise AuctionError(f'Pick a time between {TIMER_RANGE[0]} and {TIMER_RANGE[1]} seconds.')
                    raise AuctionError(f'Invalid value for {key}.')
                self.settings[key] = value
        if self.settings['max_teams'] < len(self.teams):
            self.settings['max_teams'] = len(self.teams)

    # ---- auction flow --------------------------------------------------
    def start(self, team_id, now):
        self.require_owner(team_id)
        if self.phase != 'lobby':
            raise AuctionError('Auction already started.')
        if not self.pool:
            raise AuctionError('No players to auction.')
        self.batches = self._build_batches()
        self.batch_idx = -1
        self._log('Auction started', 'start')
        self._start_batch(0, now)

    def _build_batches(self):
        order = self.settings['auction_order']
        players = list(self.pool.values())
        if order.startswith('tiered'):
            batches = []
            for tier_name, _ in TIERS:
                group = [p for p in players if p['tier'] == tier_name]
                if not group:
                    continue
                if order == 'tiered_skill':
                    group.sort(key=lambda p: (-p['overall'], p['name']))
                else:
                    random.shuffle(group)
                batches.append({'name': tier_name, 'queue': [p['pid'] for p in group]})
        elif order.startswith('roles'):
            batches = []
            for role, batch_name in ROLE_BATCHES:
                group = [p for p in players if p['role'] == role]
                if not group:
                    continue
                if order == 'roles_skill':
                    group.sort(key=lambda p: (-p['overall'], p['name']))
                else:
                    random.shuffle(group)
                batches.append({'name': batch_name, 'queue': [p['pid'] for p in group]})
        else:
            if order == 'skill_desc':
                players.sort(key=lambda p: (-p['overall'], p['name']))
            elif order == 'alphabetical':
                players.sort(key=lambda p: p['name'].lower())
            else:
                random.shuffle(players)
            batches = [{'name': 'All players', 'queue': [p['pid'] for p in players]}]
        for b in batches:
            b['total'] = len(b['queue'])
        return batches

    def _start_batch(self, idx, now):
        self.batch_idx = idx
        self.pending_batch = None
        batch = self.batches[idx]
        self._log(f"{batch['name']} batch — {len(batch['queue'])} players", 'batch')
        self._open_next_lot(now)

    def _open_next_lot(self, now):
        if self.all_squads_full():
            self._finish('All squads are full')
            return
        batch = self.batches[self.batch_idx]
        while batch['queue']:
            pid = batch['queue'].pop(0)
            player = self.pool[pid]
            if not self.anyone_can_buy(player):
                self.unsold.append(pid)   # nobody has purse/slots for this base price
                continue
            self.lot_seq += 1
            self.lot = {
                'lot_id': self.lot_seq,
                'pid': pid,
                'bid': None,
                'bidder': None,
                'deadline': now + self.settings['bid_timer'],
                'bids': 0,
                'history': [],
            }
            self.phase = 'bidding'
            self.paused = False
            self.paused_remaining = None
            self.phase_until = None
            return
        self._end_of_batch(now)

    def _end_of_batch(self, now):
        self.lot = None
        next_idx = self.batch_idx + 1
        if next_idx >= len(self.batches) and self.unsold and not self.unsold_round_done:
            pool = sorted(self.unsold, key=lambda pid: -self.pool[pid]['overall'])
            self.unsold = []
            self.unsold_round_done = True
            self.batches.append({'name': 'Unsold round', 'queue': pool, 'total': len(pool)})
        if next_idx < len(self.batches):
            self.phase = 'batch_break'
            self.pending_batch = next_idx
            self.phase_until = now + self.settings['break_seconds']
            self._log(f"Break — next up: {self.batches[next_idx]['name']}", 'batch')
        else:
            self._finish('All players have been auctioned')

    def _resolve_lot(self, now, force_unsold=False):
        lot = self.lot
        player = self.pool[lot['pid']]
        winner = self.teams.get(lot['bidder']) if lot['bidder'] and not force_unsold else None
        if winner and self.can_afford(winner, lot['bid']):
            self.sale_seq += 1
            winner['squad'].append({**player, 'price': lot['bid'], 'seq': self.sale_seq})
            self.last_result = {'player': player, 'sold': True, 'price': lot['bid'],
                                'winner_id': winner['id'], 'winner': winner['team']}
            self._log(f"SOLD: {player['name']} to {winner['team']} for {fmt_money(lot['bid'])}", 'sold')
        else:
            self.unsold.append(player['pid'])
            self.last_result = {'player': player, 'sold': False, 'price': None,
                                'winner_id': None, 'winner': None}
            self._log(f"UNSOLD: {player['name']}", 'unsold')
        self.lot = None
        self.paused = False
        self.phase = 'sold_pause'
        self.phase_until = now + SOLD_PAUSE_SECONDS

    def _finish(self, reason):
        if self.lot:
            self.unsold.append(self.lot['pid'])
        self.lot = None
        self.pending_batch = None
        self.phase = 'finished'
        self.phase_until = None
        self.paused = False
        self._log(f"Auction complete — {reason}", 'end')

    def next_valid_bids(self, team):
        """Amounts this team may bid right now (empty list = cannot bid)."""
        if self.phase != 'bidding' or self.paused or not self.lot or not team:
            return []
        lot = self.lot
        if lot['bidder'] == team['id']:
            return []
        if lot['bid'] is None:
            options = [self.pool[lot['pid']]['base']]
        else:
            options = [lot['bid'] + inc for inc in BID_INCREMENTS]
        return [a for a in options if self.can_afford(team, a)]

    def bid(self, team_id, lot_id, amount, now):
        team = self.teams.get(team_id)
        if not team:
            raise AuctionError('You are not part of this auction.')
        if self.phase != 'bidding' or not self.lot:
            raise AuctionError('Bidding is closed for this player.')
        if self.paused:
            raise AuctionError('The auction is paused.')
        lot = self.lot
        if lot_id != lot['lot_id']:
            raise AuctionError('That player is no longer up for bidding.')
        if lot['bidder'] == team_id:
            raise AuctionError('You already hold the highest bid.')
        if self.slots_left(team) <= 0:
            raise AuctionError('Your squad is full.')
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise AuctionError('Invalid bid amount.')
        expected = ([self.pool[lot['pid']]['base']] if lot['bid'] is None
                    else [lot['bid'] + inc for inc in BID_INCREMENTS])
        if amount not in expected:
            raise AuctionError(f"Bid out of date — current bid is {fmt_money(lot['bid'] or 0)}.")
        if amount > self.purse_left(team):
            raise AuctionError(f"Not enough purse — you have {fmt_money(self.purse_left(team))} left.")
        lot['bid'] = amount
        lot['bidder'] = team_id
        lot['bids'] += 1
        lot['history'].append({'team_id': team_id, 'team': team['team'], 'amount': amount, 't': now})
        lot['deadline'] = now + self.settings['bid_timer']

    def owner_action(self, team_id, action, now, value=None):
        self.require_owner(team_id)
        if self.phase in ('lobby', 'finished'):
            raise AuctionError('The auction is not running.')
        if action == 'set_timer':
            self._set_timer(value, now)
        elif action == 'pause':
            if self.phase != 'bidding' or self.paused:
                raise AuctionError('Nothing to pause right now.')
            self.paused = True
            self.paused_remaining = max(0.0, self.lot['deadline'] - now)
            self._log('Auction paused', 'info')
        elif action == 'resume':
            if not self.paused:
                raise AuctionError('The auction is not paused.')
            self.paused = False
            self.lot['deadline'] = now + max(self.paused_remaining or 0, 5)
            self.paused_remaining = None
            self._log('Auction resumed', 'info')
        elif action == 'sell_now':
            if self.phase != 'bidding' or not self.lot or not self.lot['bidder']:
                raise AuctionError('No bid to sell on.')
            self._resolve_lot(now)
        elif action == 'skip_player':
            if self.phase != 'bidding':
                raise AuctionError('No player is up right now.')
            self._resolve_lot(now, force_unsold=True)
        elif action == 'skip_batch':
            # Skipped players are set aside for good: they are not brought back
            # in the unsold round (that round is for players nobody bid on).
            if self.phase == 'batch_break':
                idx = self.pending_batch
                batch = self.batches[idx]
                self.skipped.extend(batch['queue'])
                batch['queue'] = []
                self.batch_idx = idx
                self._log(f"Skipped batch: {batch['name']}", 'batch')
                self._end_of_batch(now)
            else:
                batch = self.batches[self.batch_idx]
                if self.lot:
                    self.skipped.append(self.lot['pid'])
                    self.lot = None
                self.skipped.extend(batch['queue'])
                batch['queue'] = []
                self._log(f"Skipped rest of batch: {batch['name']}", 'batch')
                self._end_of_batch(now)
        elif action == 'next_batch':
            if self.phase != 'batch_break':
                raise AuctionError('Not between batches.')
            self._start_batch(self.pending_batch, now)
        elif action == 'start_batch':
            self._choose_batch(value, now)
        elif action == 'end':
            self._finish('Ended by the auctioneer')
        else:
            raise AuctionError('Unknown action.')

    def _choose_batch(self, value, now):
        """During an interval, start any batch that has not run yet instead of
        the next one in line. The batches in between keep their order."""
        if self.phase != 'batch_break':
            raise AuctionError('You can only pick the next batch during an interval.')
        try:
            idx = int(value)
        except (TypeError, ValueError):
            raise AuctionError('Unknown batch.')
        if not self.pending_batch <= idx < len(self.batches):
            raise AuctionError('That batch has already run.')
        batch = self.batches.pop(idx)
        self.batches.insert(self.pending_batch, batch)
        self._start_batch(self.pending_batch, now)

    def _set_timer(self, value, now):
        """Change the bid timer mid-auction. Takes effect on the next bid or player;
        a clock that is already running is only ever shortened, never lengthened."""
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise AuctionError('Invalid timer value.')
        if not TIMER_RANGE[0] <= value <= TIMER_RANGE[1]:
            raise AuctionError(f'Pick a time between {TIMER_RANGE[0]} and {TIMER_RANGE[1]} seconds.')
        self.settings['bid_timer'] = value
        if self.phase == 'bidding' and self.lot:
            if self.paused:
                self.paused_remaining = min(self.paused_remaining or value, value)
            else:
                self.lot['deadline'] = min(self.lot['deadline'], now + value)
        self._log(f'Bid timer set to {value}s', 'info')

    # ---- restart vote (results screen) ---------------------------------
    def _voters(self):
        """Teams whose votes count: everyone currently connected."""
        return [t['id'] for t in self.teams.values() if t['connected']]

    def _votes_needed(self, voters):
        return max(1, math.ceil(len(voters) * RESTART_VOTE_SHARE - 1e-9))

    def propose_restart(self, team_id, now):
        if self.phase != 'finished':
            raise AuctionError('You can only start again once the auction has ended.')
        if self.vote:
            raise AuctionError('A restart vote is already running.')
        if team_id not in self.teams:
            raise AuctionError('You are not part of this auction.')
        self.vote = {'by': team_id, 'yes': {team_id}, 'no': set(), 'ends': now + RESTART_VOTE_SECONDS}
        self._log(f"{self.teams[team_id]['team']} proposed starting the auction again", 'vote')
        self._settle_vote(now)

    def cast_vote(self, team_id, yes, now):
        if self.phase != 'finished' or not self.vote:
            raise AuctionError('There is no vote running.')
        if team_id not in self.teams:
            raise AuctionError('You are not part of this auction.')
        v = self.vote
        (v['yes'] if yes else v['no']).add(team_id)
        (v['no'] if yes else v['yes']).discard(team_id)
        self._settle_vote(now)

    def _settle_vote(self, now):
        """Pass, fail or keep the vote open. Returns True if it closed."""
        v = self.vote
        if not v:
            return False
        voters = set(self._voters())
        need = self._votes_needed(voters)
        yes, no = v['yes'] & voters, v['no'] & voters
        undecided = len(voters) - len(yes) - len(no)
        if len(yes) >= need:
            self._log(f"Restart agreed ({len(yes)} of {len(voters)} voted yes). Back to the lobby", 'vote')
            self._reset_to_lobby()
            return True
        if len(yes) + undecided < need or now >= v['ends']:
            why = 'time ran out' if now >= v['ends'] else f"{len(no)} voted no"
            self._log(f"Restart vote failed ({why})", 'vote')
            self.vote = None
            return True
        return False

    def _reset_to_lobby(self):
        for t in self.teams.values():
            t['squad'] = []
        self.pool.reroll()             # fresh base prices for the new auction
        self.phase = 'lobby'
        self.batches, self.batch_idx, self.pending_batch = [], -1, None
        self.lot, self.paused, self.paused_remaining, self.phase_until = None, False, None, None
        self.last_result = None
        self.unsold, self.skipped, self.unsold_round_done = [], [], False
        self.sale_seq = 0
        self.vote = None

    def tick(self, now):
        """Advance timers. Returns True if anything changed."""
        dt = 0.0 if self.last_tick is None else max(0.0, now - self.last_tick)
        self.last_tick = now
        if not any(t['connected'] for t in self.teams.values()):
            # Nobody is here: freeze every clock so no one loses a player while
            # the whole room is reconnecting, and let cleanup see the room as idle.
            if self.empty_since is None:
                self.empty_since = now
            if self.lot:
                self.lot['deadline'] += dt
            if self.phase_until:
                self.phase_until += dt
            if self.vote:
                self.vote['ends'] += dt
            return False
        self.empty_since = None
        changed = False
        if self.vote and self._settle_vote(now):
            changed = True
        if self.phase == 'bidding' and not self.paused and self.lot and now >= self.lot['deadline']:
            self._resolve_lot(now)
            changed = True
        elif self.phase == 'sold_pause' and now >= self.phase_until:
            self._open_next_lot(now)
            changed = True
        elif self.phase == 'batch_break' and now >= self.phase_until:
            self._start_batch(self.pending_batch, now)
            changed = True

        # Hand the gavel to someone else if the owner has been gone too long.
        owner = self.teams.get(self.owner_id)
        if (owner and not owner['connected'] and owner['disconnected_at'] is not None
                and now - owner['disconnected_at'] > OWNER_HANDOVER_SECONDS):
            successor = next((t for t in self.teams.values() if t['connected']), None)
            if successor:
                self.owner_id = successor['id']
                self._log(f"{owner['team']} disconnected — {successor['team']} is now the auctioneer", 'info')
                changed = True
        return changed

    # ---- serialisation -------------------------------------------------
    def snapshot(self, now):
        self.version += 1
        teams = []
        for t in self.teams.values():
            roles = {r: 0 for r in ROLES}
            for p in t['squad']:
                roles[p['role']] = roles.get(p['role'], 0) + 1
            teams.append({
                'id': t['id'],
                'name': t['name'],
                'team': t['team'],
                'connected': t['connected'],
                'is_owner': t['id'] == self.owner_id,
                'squad': [{k: p[k] for k in ('pid', 'name', 'role', 'nationality', 'overall', 'price', 'base', 'seq', 'tier')}
                          for p in t['squad']],
                'spent': self.team_spent(t),
                'purse_left': self.purse_left(t),
                'slots_left': self.slots_left(t),
                'roles': roles,
            })

        lot = None
        if self.lot:
            remaining = self.paused_remaining if self.paused else max(0.0, self.lot['deadline'] - now)
            bidder = self.teams.get(self.lot['bidder'])
            lot = {
                'lot_id': self.lot['lot_id'],
                'player': self.pool[self.lot['pid']],
                'bid': self.lot['bid'],
                'bidder_id': self.lot['bidder'],
                'bidder': bidder['team'] if bidder else None,
                'bids': self.lot['bids'],
                'history': self.lot['history'][-8:],
                'remaining_ms': int(remaining * 1000),
                'next_bids': ([self.pool[self.lot['pid']]['base']] if self.lot['bid'] is None
                              else [self.lot['bid'] + inc for inc in BID_INCREMENTS]),
            }

        batches = []
        for i, b in enumerate(self.batches):
            batches.append({'name': b['name'], 'total': b['total'], 'remaining': len(b['queue']),
                            'status': ('done' if i < self.batch_idx else 'current' if i == self.batch_idx else 'upcoming')})
        upcoming = []
        if self.phase in ('bidding', 'sold_pause') and 0 <= self.batch_idx < len(self.batches):
            upcoming = [self.pool[pid] for pid in self.batches[self.batch_idx]['queue'][:5]]
        elif self.phase == 'batch_break' and self.pending_batch is not None:
            upcoming = [self.pool[pid] for pid in self.batches[self.pending_batch]['queue'][:5]]

        role_counts = {role: 0 for role in ROLES}
        for p in self.pool.values():
            role_counts[p['role']] = role_counts.get(p['role'], 0) + 1
        tier_counts = {name: 0 for name, _ in TIERS}
        for p in self.pool.values():
            tier_counts[p['tier']] += 1

        snap = {
            'v': self.version,
            'code': self.code,
            'phase': self.phase,
            'paused': self.paused,
            'owner_id': self.owner_id,
            'settings': dict(self.settings),
            'increments': BID_INCREMENTS,
            'teams': teams,
            'lot': lot,
            'phase_remaining_ms': int(max(0.0, self.phase_until - now) * 1000) if self.phase_until else None,
            'last_result': self.last_result if self.phase == 'sold_pause' else None,
            'batches': batches,
            'batch_idx': self.batch_idx,
            'lot_number': self.lot_seq,
            'pending_batch': self.batches[self.pending_batch]['name'] if self.pending_batch is not None else None,
            'upcoming': upcoming,
            'pool_size': len(self.pool),
            'tier_counts': tier_counts,
            'unsold_count': len(self.unsold),
            'skipped_count': len(self.skipped),
            'role_counts': role_counts,
            'sold_count': self.sale_seq,
            'log': list(self.log)[-25:],
            'vote': self._vote_snapshot(now),
        }
        if self.phase == 'finished':
            snap['unsold'] = ([{**self.pool[pid], 'skipped': False} for pid in self.unsold] +
                              [{**self.pool[pid], 'skipped': True} for pid in self.skipped])
            snap['stats'] = self._stats(teams)
        return snap

    def _vote_snapshot(self, now):
        v = self.vote
        if not v:
            return None
        voters = self._voters()
        return {
            'by': v['by'],
            'by_team': self.teams[v['by']]['team'] if v['by'] in self.teams else '',
            'yes': [t for t in v['yes'] if t in voters],
            'no': [t for t in v['no'] if t in voters],
            'voters': voters,
            'need': self._votes_needed(voters),
            'remaining_ms': int(max(0.0, v['ends'] - now) * 1000),
        }

    def _stats(self, teams):
        sold = [p for t in teams for p in t['squad']]
        if not sold:
            return {'most_expensive': None, 'average_price': 0, 'total_sold': 0, 'total_spent': 0}
        top = max(sold, key=lambda p: p['price'])
        owner = next(t['team'] for t in teams if any(p['pid'] == top['pid'] for p in t['squad']))
        total = sum(p['price'] for p in sold)
        return {
            'most_expensive': {**top, 'team': owner},
            'average_price': round(total / len(sold)),
            'total_sold': len(sold),
            'total_spent': total,
        }


def fmt_money(lakhs):
    lakhs = int(lakhs or 0)
    if lakhs >= CR:
        cr = lakhs / CR
        return f"₹{cr:.2f}".rstrip('0').rstrip('.') + ' Cr'
    return f"₹{lakhs}L"


# --------------------------------------------------------------------------
# Global registry + ticker
# --------------------------------------------------------------------------
rooms = {}
sessions = {}        # sid -> (room_code, team_id)
registry_lock = threading.Lock()
_ticker_started = False


def generate_room_code():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    while True:
        code = ''.join(random.choice(alphabet) for _ in range(5))
        if code not in rooms:
            return code


def broadcast(room, snap):
    socketio.emit('state', snap, to=room.code)


def ticker():
    last_cleanup = time.time()
    while True:
        socketio.sleep(0.2)
        now = time.time()
        for room in list(rooms.values()):
            # One broken room must never stop the clock for every other room.
            try:
                snap = None
                with room.lock:
                    if room.tick(now):
                        room.last_active = now
                        snap = room.snapshot(now)
                if snap:
                    broadcast(room, snap)
            except Exception:
                log.exception('tick failed for room %s', room.code)
        if now - last_cleanup > 60:
            last_cleanup = now
            try:
                cleanup_rooms(now)
            except Exception:
                log.exception('room cleanup failed')


def cleanup_rooms(now):
    with registry_lock:
        for code, room in list(rooms.items()):
            anyone_here = any(t['connected'] for t in room.teams.values())
            empty_for = now - room.empty_since if room.empty_since else (0 if anyone_here else now - room.last_active)
            idle = now - room.last_active
            if (not anyone_here and empty_for > IDLE_ROOM_TTL) or (room.phase == 'finished' and idle > FINISHED_ROOM_TTL):
                rooms.pop(code, None)
                log.info('removed room %s (%s)', code, 'empty' if not anyone_here else 'finished')


def ensure_ticker():
    global _ticker_started
    with registry_lock:
        if _ticker_started:
            return
        _ticker_started = True
    socketio.start_background_task(ticker)


def current(sid):
    """Return (room, team_id) for this socket or raise."""
    entry = sessions.get(sid)
    if not entry or entry[0] not in rooms:
        raise AuctionError('You are not in a room. Please join again.')
    return rooms[entry[0]], entry[1]


def guarded(handler):
    """Wrap a socket handler: non-dict payloads become {}, AuctionError becomes a
    clean refusal, and any unexpected error is logged and still answered."""
    @functools.wraps(handler)
    def wrapper(data=None, *args):
        try:
            return handler(data if isinstance(data, dict) else {})
        except AuctionError as exc:
            return {'ok': False, 'error': str(exc)}
        except Exception:
            log.exception('handler %s failed', handler.__name__)
            return {'ok': False, 'error': 'Something went wrong on the server. Please try again.'}
    return wrapper


def text(value, limit=60):
    """Payload field as a trimmed string, whatever type the client sent."""
    return value.strip()[:limit] if isinstance(value, str) else ''


def run(fn):
    """Run fn(room, team_id, now) under the room lock, broadcast, return an ack."""
    try:
        room, team_id = current(request.sid)
        now = time.time()
        with room.lock:
            result = fn(room, team_id, now) or {}
            room.last_active = now
            snap = room.snapshot(now)
        broadcast(room, snap)
        return {'ok': True, **result}
    except AuctionError as exc:
        return {'ok': False, 'error': str(exc)}


def attach(room, team, sid):
    team['sid'] = sid
    team['connected'] = True
    team['disconnected_at'] = None
    sessions[sid] = (room.code, team['id'])
    join_room(room.code)


# --------------------------------------------------------------------------
# HTTP routes
# --------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html', default_pool=len(DEFAULT_PLAYERS))


@app.route('/health')
def health_check():
    return {'status': 'healthy', 'active_rooms': len(rooms), 'connections': len(connected_sids), 'mode': ASYNC_MODE,
            'service': 'cricket-auction-app'}


# --------------------------------------------------------------------------
# Socket events (every handler returns an ack dict: {ok, error?, ...})
# --------------------------------------------------------------------------
connected_sids = set()


@socketio.on('connect')
def on_connect(*_args):
    if len(connected_sids) >= MAX_CONNECTIONS:
        log.warning('refusing connection: %d open', len(connected_sids))
        return False
    connected_sids.add(request.sid)
    ensure_ticker()


@socketio.on('disconnect')
def on_disconnect(*_args):
    connected_sids.discard(request.sid)
    try:
        _on_disconnect()
    except Exception:
        log.exception('disconnect handling failed')


def _on_disconnect():
    entry = sessions.pop(request.sid, None)
    if not entry or entry[0] not in rooms:
        return
    room = rooms[entry[0]]
    now = time.time()
    with room.lock:
        team = room.teams.get(entry[1])
        if not team or team['sid'] != request.sid:
            return
        team['connected'] = False
        team['disconnected_at'] = now
        snap = room.snapshot(now)
    broadcast(room, snap)


@socketio.on('create_room')
@guarded
def on_create_room(data):
    data = data or {}
    if request.sid in sessions:
        _leave_current(request.sid)
    if data.get('source') == 'csv':
        csv_text = data.get('csv_text')
        players, skipped = parse_players_csv(csv_text if isinstance(csv_text, str) else '')
        if not players:
            return {'ok': False, 'error': 'Could not read any players from that CSV. '
                                          'It needs at least Name and Overall columns.'}
    else:
        players, skipped = DEFAULT_PLAYERS, 0
        if not players:
            return {'ok': False, 'error': 'Default player list is missing on the server. Upload a CSV.'}
    with registry_lock:
        if len(rooms) >= MAX_ROOMS:
            return {'ok': False, 'error': 'The server is full right now. Please try again in a few minutes.'}
        code = generate_room_code()
        room = Room(code, records=DEFAULT_RECORDS if players is DEFAULT_PLAYERS else make_records(players))
        rooms[code] = room
    try:
        now = time.time()
        with room.lock:
            team = room.add_team(text(data.get('name')), text(data.get('team')), request.sid)
            attach(room, team, request.sid)
            snap = room.snapshot(now)
    except AuctionError as exc:
        rooms.pop(code, None)
        return {'ok': False, 'error': str(exc)}
    return {'ok': True, 'room_code': code, 'team_id': team['id'], 'token': team['token'],
            'players_loaded': len(players), 'skipped_rows': skipped, 'state': snap}


@socketio.on('join_room')
@guarded
def on_join_room(data):
    data = data or {}
    code = text(data.get('room_code'), 8).upper()
    room = rooms.get(code)
    if not room:
        return {'ok': False, 'error': f'Room "{code}" not found. Check the code.'}
    if request.sid in sessions:
        _leave_current(request.sid)
    try:
        now = time.time()
        with room.lock:
            team = room.add_team(text(data.get('name')), text(data.get('team')), request.sid)
            attach(room, team, request.sid)
            snap = room.snapshot(now)
    except AuctionError as exc:
        return {'ok': False, 'error': str(exc)}
    broadcast(room, snap)
    return {'ok': True, 'room_code': code, 'team_id': team['id'], 'token': team['token'], 'state': snap}


@socketio.on('rejoin')
@guarded
def on_rejoin(data):
    data = data or {}
    code = text(data.get('room_code'), 8).upper()
    room = rooms.get(code)
    if not room:
        return {'ok': False, 'error': 'That room no longer exists.'}
    now = time.time()
    with room.lock:
        team = room.team_by_token(data.get('token'))
        if not team:
            return {'ok': False, 'error': 'Your seat in that room was not found.'}
        old_sid = team['sid']
        if old_sid and old_sid != request.sid:
            sessions.pop(old_sid, None)
        attach(room, team, request.sid)
        room.last_active = now
        snap = room.snapshot(now)
    broadcast(room, snap)
    return {'ok': True, 'room_code': code, 'team_id': team['id'], 'token': team['token'], 'state': snap}


def _leave_current(sid):
    entry = sessions.pop(sid, None)
    if not entry or entry[0] not in rooms:
        return
    room = rooms[entry[0]]
    now = time.time()
    with room.lock:
        if room.phase == 'lobby':
            room.remove_team(entry[1])
        else:
            team = room.teams.get(entry[1])
            if team:
                team['connected'] = False
                team['disconnected_at'] = now
        empty = not room.teams
        snap = None if empty else room.snapshot(now)
    leave_room(room.code, sid=sid)
    if empty:
        rooms.pop(room.code, None)
    elif snap:
        broadcast(room, snap)


@socketio.on('leave_room')
@guarded
def on_leave_room(_data=None):
    _leave_current(request.sid)
    return {'ok': True}


@socketio.on('update_settings')
@guarded
def on_update_settings(data):
    return run(lambda room, tid, now: room.update_settings(tid, data or {}))


@socketio.on('start_auction')
@guarded
def on_start_auction(_data=None):
    return run(lambda room, tid, now: room.start(tid, now))


@socketio.on('place_bid')
@guarded
def on_place_bid(data):
    data = data or {}
    return run(lambda room, tid, now: room.bid(tid, data.get('lot_id'), data.get('amount'), now))


@socketio.on('restart_vote')
@guarded
def on_restart_vote(data):
    """choice: 'propose', 'yes' or 'no'."""
    choice = data.get('choice')

    def act(room, tid, now):
        if choice == 'propose':
            room.propose_restart(tid, now)
        elif choice in ('yes', 'no'):
            room.cast_vote(tid, choice == 'yes', now)
        else:
            raise AuctionError('Unknown vote.')
    return run(act)


@socketio.on('owner_action')
@guarded
def on_owner_action(data):
    data = data or {}
    return run(lambda room, tid, now: room.owner_action(tid, data.get('action'), now, data.get('value')))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    socketio.run(app, debug=debug, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
