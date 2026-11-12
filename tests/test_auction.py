import os
import threading

os.environ.setdefault('SOCKETIO_ASYNC_MODE', 'threading')

import pytest  # noqa: E402

import app as server  # noqa: E402
from app import AuctionError, Room, fmt_money, normalize_role, parse_players_csv  # noqa: E402


def make_players(n=6, overall=80):
    return [{'name': f'P{i}', 'overall': overall - i, 'role': 'Batsman', 'nationality': 'X'} for i in range(n)]


def room_with_teams(n_teams=2, players=None, **settings):
    room = Room('TEST1', players or make_players())
    room.settings.update(settings)
    teams = [room.add_team(f'user{i}', f'Team {i}', f'sid{i}') for i in range(n_teams)]
    return room, teams


# ---------------------------------------------------------------- parsing
def test_role_normalisation():
    assert normalize_role('Wicketkeeper') == 'Wicket-keeper'
    assert normalize_role('WK-Batsman') == 'Wicket-keeper'
    assert normalize_role('All Rounder') == 'All-rounder'
    assert normalize_role('Batter') == 'Batsman'
    assert normalize_role('bowler') == 'Bowler'


def test_csv_any_column_order_and_quotes():
    text = 'role,name,nationality,overall\nBatter,"Smith, Steve",Australia,90\nBowler,Bumrah,India,94\nBad,,X,1\n'
    players, skipped = parse_players_csv(text)
    assert [p['name'] for p in players] == ['Smith, Steve', 'Bumrah']
    assert players[0]['role'] == 'Batsman' and players[0]['overall'] == 90
    assert skipped == 1


def test_csv_legacy_layout():
    players, _ = parse_players_csv('Name,Overall,Role,Nationality\nA,90,Wicketkeeper,India\r\n')
    assert players == [{'name': 'A', 'overall': 90, 'role': 'Wicket-keeper', 'nationality': 'India'}]


def test_default_dataset_loads_with_wicketkeepers():
    assert len(server.DEFAULT_PLAYERS) > 500
    assert any(p['role'] == 'Wicket-keeper' for p in server.DEFAULT_PLAYERS)


def test_money_format():
    assert fmt_money(50) == '₹50L'
    assert fmt_money(100) == '₹1 Cr'
    assert fmt_money(150) == '₹1.5 Cr'
    assert fmt_money(1025) == '₹10.25 Cr'


# ---------------------------------------------------------------- lobby
def test_owner_gets_a_real_team():
    room, (owner, guest) = room_with_teams()
    assert room.owner_id == owner['id']
    assert room.purse_left(owner) == 100 * 100


def test_duplicate_team_name_rejected():
    room, _ = room_with_teams()
    with pytest.raises(AuctionError):
        room.add_team('someone', 'team 0', 'sidX')


def test_max_teams_enforced():
    room, _ = room_with_teams(2, max_teams=2)
    with pytest.raises(AuctionError):
        room.add_team('third', 'Team 3', 'sid3')


def test_only_owner_can_start_or_change_settings():
    room, (owner, guest) = room_with_teams()
    with pytest.raises(AuctionError):
        room.start(guest['id'], 0)
    with pytest.raises(AuctionError):
        room.update_settings(guest['id'], {'squad_size': 15})
    room.update_settings(owner['id'], {'squad_size': 15})
    assert room.settings['squad_size'] == 15


def test_settings_locked_after_start():
    room, (owner, _) = room_with_teams()
    room.start(owner['id'], 0)
    with pytest.raises(AuctionError):
        room.update_settings(owner['id'], {'auction_order': 'random'})


def test_no_join_after_start():
    room, (owner, _) = room_with_teams()
    room.start(owner['id'], 0)
    with pytest.raises(AuctionError):
        room.add_team('late', 'Late', 'sidL')


# ---------------------------------------------------------------- bidding
def started(room, owner):
    room.start(owner['id'], 0)
    assert room.phase == 'bidding'
    return room.lot


def test_owner_purchase_updates_owner_squad_and_purse():
    room, (owner, guest) = room_with_teams()
    lot = started(room, owner)
    base = room.pool[lot['pid']]['base']
    room.bid(owner['id'], lot['lot_id'], base, 1)
    room.tick(1 + room.settings['bid_timer'])
    assert room.phase == 'sold_pause'
    assert len(owner['squad']) == 1
    assert room.purse_left(owner) == 100 * 100 - base


def test_opening_bid_at_base_then_increments():
    room, (a, b) = room_with_teams()
    lot = started(room, a)
    base = room.pool[lot['pid']]['base']
    with pytest.raises(AuctionError):
        room.bid(a['id'], lot['lot_id'], base + 50, 1)
    room.bid(a['id'], lot['lot_id'], base, 1)
    with pytest.raises(AuctionError):
        room.bid(a['id'], lot['lot_id'], base + 50, 1)       # consecutive
    room.bid(b['id'], lot['lot_id'], base + 100, 2)
    assert lot['bid'] == base + 100 and lot['bidder'] == b['id']


def test_simultaneous_same_amount_only_first_wins():
    room, (a, b, c) = room_with_teams(3)
    lot = started(room, a)
    base = room.pool[lot['pid']]['base']
    room.bid(a['id'], lot['lot_id'], base, 1)
    room.bid(b['id'], lot['lot_id'], base + 50, 2)
    with pytest.raises(AuctionError, match='out of date'):
        room.bid(c['id'], lot['lot_id'], base + 50, 2)


def test_stale_lot_and_pause_window_rejected():
    room, (a, b) = room_with_teams()
    lot = started(room, a)
    old_id = lot['lot_id']
    room.tick(100)                       # times out -> unsold, sold_pause
    assert room.phase == 'sold_pause'
    with pytest.raises(AuctionError):
        room.bid(b['id'], old_id, 50, 100.5)
    room.tick(100 + server.SOLD_PAUSE_SECONDS)
    assert room.phase == 'bidding' and room.lot['lot_id'] != old_id
    with pytest.raises(AuctionError, match='no longer'):
        room.bid(b['id'], old_id, room.pool[room.lot['pid']]['base'], 104)


def test_purse_limit_enforced():
    players = [{'name': 'Star', 'overall': 95, 'role': 'Batsman', 'nationality': 'X'}]
    room, (a, b) = room_with_teams(players=players, team_purse=50)
    lot = started(room, a)
    lot['bid'], lot['bidder'] = 4950, b['id']   # 49.5 Cr
    with pytest.raises(AuctionError, match='Not enough purse'):
        room.bid(a['id'], lot['lot_id'], 4950 + 100, 1)
    room.bid(a['id'], lot['lot_id'], 4950 + 50, 1)


def test_squad_full_blocks_bidding_and_finishes():
    room, (a, b) = room_with_teams(squad_size=5)
    lot = started(room, a)
    a['squad'] = [{'price': 0}] * 5
    with pytest.raises(AuctionError, match='squad is full'):
        room.bid(a['id'], lot['lot_id'], room.pool[lot['pid']]['base'], 1)
    b['squad'] = [{'price': 0}] * 5
    room.tick(100)
    room.tick(200)
    assert room.phase == 'finished'


def test_timer_reset_on_bid():
    room, (a, b) = room_with_teams()
    lot = started(room, a)
    room.bid(a['id'], lot['lot_id'], room.pool[lot['pid']]['base'], 14)
    room.tick(15)
    assert room.phase == 'bidding'
    room.tick(14 + room.settings['bid_timer'])
    assert room.phase == 'sold_pause'


def test_pause_and_resume_freeze_clock():
    room, (a, b) = room_with_teams()
    started(room, a)
    room.owner_action(a['id'], 'pause', 5)
    room.tick(1000)
    assert room.phase == 'bidding' and room.paused
    with pytest.raises(AuctionError):
        room.bid(b['id'], room.lot['lot_id'], room.pool[room.lot['pid']]['base'], 1000)
    room.owner_action(a['id'], 'resume', 1000)
    assert room.lot['deadline'] == pytest.approx(1010)


def test_guest_cannot_use_owner_actions():
    room, (a, b) = room_with_teams()
    started(room, a)
    for action in ('pause', 'skip_player', 'skip_batch', 'end'):
        with pytest.raises(AuctionError):
            room.owner_action(b['id'], action, 1)


# ---------------------------------------------------------------- batches
def tiered_players():
    return ([{'name': f'T{i}', 'overall': 90, 'role': 'Batsman', 'nationality': 'X'} for i in range(3)] +
            [{'name': f'M{i}', 'overall': 78, 'role': 'Bowler', 'nationality': 'X'} for i in range(3)] +
            [{'name': f'L{i}', 'overall': 60, 'role': 'Bowler', 'nationality': 'X'} for i in range(3)])


def test_batches_top_mid_low_order():
    room, (a, b) = room_with_teams(players=tiered_players())
    started(room, a)
    assert [bt['name'] for bt in room.batches] == ['Top players', 'Mid players', 'Low players']
    assert room.pool[room.lot['pid']]['tier'] == 'Top players'


def test_skip_batch_moves_to_break_then_next_batch():
    room, (a, b) = room_with_teams(players=tiered_players())
    started(room, a)
    room.owner_action(a['id'], 'skip_batch', 1)
    assert room.phase == 'batch_break'
    assert room.batches[room.pending_batch]['name'] == 'Mid players'
    assert len(room.unsold) == 3
    room.owner_action(a['id'], 'next_batch', 2)
    assert room.phase == 'bidding'
    assert room.pool[room.lot['pid']]['tier'] == 'Mid players'


def test_skip_upcoming_batch_during_break_and_unsold_round():
    room, (a, b) = room_with_teams(players=tiered_players())
    started(room, a)
    room.owner_action(a['id'], 'skip_batch', 1)   # skip Top -> break before Mid
    room.owner_action(a['id'], 'skip_batch', 2)   # skip Mid during break -> break before Low
    assert room.batches[room.pending_batch]['name'] == 'Low players'
    room.owner_action(a['id'], 'skip_batch', 3)   # skip Low -> unsold round appended
    assert room.batches[room.pending_batch]['name'] == 'Unsold round'
    assert room.batches[room.pending_batch]['total'] == 9
    room.owner_action(a['id'], 'skip_batch', 4)
    assert room.phase == 'finished'


def test_batch_break_auto_continues():
    room, (a, b) = room_with_teams(players=tiered_players())
    started(room, a)
    room.owner_action(a['id'], 'skip_batch', 1)
    room.tick(1 + server.BATCH_BREAK_SECONDS)
    assert room.phase == 'bidding'


def test_owner_handover_after_disconnect():
    room, (a, b) = room_with_teams()
    started(room, a)
    a['connected'], a['disconnected_at'] = False, 0
    room.tick(server.OWNER_HANDOVER_SECONDS + 1)
    assert room.owner_id == b['id']


def test_snapshot_never_leaks_tokens():
    room, _ = room_with_teams()
    assert 'token' not in str(room.snapshot(0))


# ---------------------------------------------------------------- sockets
@pytest.fixture
def clients():
    server.rooms.clear()
    server.sessions.clear()
    made = []

    def make():
        c = server.socketio.test_client(server.app)
        made.append(c)
        return c
    yield make
    for c in made:
        if c.is_connected():
            c.disconnect()


def last_state(client):
    states = [e['args'][0] for e in client.get_received() if e['name'] == 'state']
    return states[-1] if states else None


def test_socket_full_flow(clients):
    owner, guest = clients(), clients()
    ack = owner.emit('create_room', {'name': 'Ash', 'team': 'Ash XI'}, callback=True)
    assert ack['ok'], ack
    code = ack['room_code']
    g = guest.emit('join_room', {'room_code': code.lower(), 'name': 'Bo', 'team': 'Bo XI'}, callback=True)
    assert g['ok'], g
    assert not guest.emit('start_auction', {}, callback=True)['ok']
    assert owner.emit('start_auction', {}, callback=True)['ok']
    st = last_state(guest)
    assert st['phase'] == 'bidding' and len(st['teams']) == 2
    lot = st['lot']
    r = guest.emit('place_bid', {'lot_id': lot['lot_id'], 'amount': lot['next_bids'][0]}, callback=True)
    assert r['ok'], r
    st = last_state(owner)
    assert st['lot']['bidder_id'] == g['team_id']


def test_socket_rejoin_with_token(clients):
    owner = clients()
    ack = owner.emit('create_room', {'name': 'Ash', 'team': 'Ash XI'}, callback=True)
    owner.disconnect()
    room = server.rooms[ack['room_code']]
    assert not room.teams[ack['team_id']]['connected']
    again = clients()
    r = again.emit('rejoin', {'room_code': ack['room_code'], 'token': ack['token']}, callback=True)
    assert r['ok'] and r['team_id'] == ack['team_id']
    assert room.teams[ack['team_id']]['connected']
    bad = clients().emit('rejoin', {'room_code': ack['room_code'], 'token': 'nope'}, callback=True)
    assert not bad['ok']


def test_socket_csv_upload(clients):
    c = clients()
    ack = c.emit('create_room', {'name': 'A', 'team': 'A', 'source': 'csv',
                                 'csv_text': 'name,overall,role\nX,80,WK\nY,abc,Bat\n'}, callback=True)
    assert ack['ok'] and ack['players_loaded'] == 1 and ack['skipped_rows'] == 1


def test_concurrent_bids_threads():
    """Hammer one lot from many threads: the bid chain must stay consistent."""
    room, teams = room_with_teams(8, max_teams=8)
    room.start(teams[0]['id'], 0)
    lot_id = room.lot['lot_id']
    accepted = []

    def worker(team):
        for _ in range(50):
            with room.lock:
                options = room.next_valid_bids(team)
                if not options:
                    continue
                try:
                    room.bid(team['id'], lot_id, options[0], 1)
                    accepted.append(options[0])
                except AuctionError:
                    pass

    threads = [threading.Thread(target=worker, args=(t,)) for t in teams]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert accepted == sorted(accepted) and len(set(accepted)) == len(accepted)
    assert room.lot['bid'] == accepted[-1]
