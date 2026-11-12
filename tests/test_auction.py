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
    assert len(room.skipped) == 3 and not room.unsold
    room.owner_action(a['id'], 'next_batch', 2)
    assert room.phase == 'bidding'
    assert room.pool[room.lot['pid']]['tier'] == 'Mid players'


def test_skipped_players_do_not_come_back():
    room, (a, b) = room_with_teams(players=tiered_players())
    started(room, a)
    room.owner_action(a['id'], 'skip_batch', 1)   # skip Top -> break before Mid
    room.owner_action(a['id'], 'skip_batch', 2)   # skip Mid during break -> break before Low
    assert room.batches[room.pending_batch]['name'] == 'Low players'
    room.owner_action(a['id'], 'skip_batch', 3)   # skip Low -> nothing left, no unsold round
    assert room.phase == 'finished'
    assert len(room.skipped) == 9 and not room.unsold
    snap = room.snapshot(3)
    assert snap['skipped_count'] == 9 and all(p['skipped'] for p in snap['unsold'])


def test_unsold_round_only_for_players_nobody_bid_on():
    room, (a, b) = room_with_teams(players=tiered_players())
    started(room, a)
    now = 0
    for _ in range(3):                              # Top batch: nobody bids
        now += 100
        room.tick(now)                              # lot times out -> unsold
        now += 10
        room.tick(now)                              # next lot / break
    assert room.phase == 'batch_break' and len(room.unsold) == 3
    room.owner_action(a['id'], 'skip_batch', now)   # skip Mid
    room.owner_action(a['id'], 'skip_batch', now)   # skip Low -> unsold round of the 3 Top players
    assert room.batches[room.pending_batch]['name'] == 'Unsold round'
    assert room.batches[room.pending_batch]['total'] == 3


def test_batch_break_auto_continues():
    room, (a, b) = room_with_teams(players=tiered_players())
    started(room, a)
    room.owner_action(a['id'], 'skip_batch', 1)
    room.tick(1 + room.settings['break_seconds'])
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


# ---------------------------------------------------------------- room lifecycle (sockets)
def test_rooms_are_isolated(clients):
    a1, a2, b1, b2 = clients(), clients(), clients(), clients()
    ra = a1.emit('create_room', {'name': 'A', 'team': 'A1'}, callback=True)
    rb = b1.emit('create_room', {'name': 'B', 'team': 'B1'}, callback=True)
    assert ra['room_code'] != rb['room_code']
    a2.emit('join_room', {'room_code': ra['room_code'], 'name': 'x', 'team': 'A2'}, callback=True)
    b2.emit('join_room', {'room_code': rb['room_code'], 'name': 'y', 'team': 'B2'}, callback=True)
    b1.get_received(); b2.get_received()
    assert a1.emit('start_auction', {}, callback=True)['ok']
    lot = last_state(a2)['lot']
    assert a2.emit('place_bid', {'lot_id': lot['lot_id'], 'amount': lot['next_bids'][0]}, callback=True)['ok']
    # Room B saw nothing and is still in the lobby
    assert last_state(b1) is None
    assert server.rooms[rb['room_code']].phase == 'lobby'
    # A team in room B cannot bid into room A's lot
    r = b2.emit('place_bid', {'lot_id': lot['lot_id'], 'amount': lot['next_bids'][0] + 50}, callback=True)
    assert not r['ok']


def test_join_errors(clients):
    owner, c = clients(), clients()
    assert not c.emit('join_room', {'room_code': 'NOPE1', 'name': 'x'}, callback=True)['ok']
    code = owner.emit('create_room', {'name': 'A', 'team': 'A'}, callback=True)['room_code']
    assert owner.emit('update_settings', {'max_teams': 2}, callback=True)['ok']
    assert clients().emit('join_room', {'room_code': code, 'name': 'b', 'team': 'B'}, callback=True)['ok']
    full = c.emit('join_room', {'room_code': code, 'name': 'c', 'team': 'C'}, callback=True)
    assert not full['ok'] and 'full' in full['error']
    assert not c.emit('join_room', {'room_code': code, 'name': '', 'team': 'D'}, callback=True)['ok']


def test_lobby_leave_transfers_owner_and_last_out_deletes_room(clients):
    owner, guest = clients(), clients()
    ack = owner.emit('create_room', {'name': 'A', 'team': 'A'}, callback=True)
    code = ack['room_code']
    g = guest.emit('join_room', {'room_code': code, 'name': 'b', 'team': 'B'}, callback=True)
    owner.emit('leave_room', {}, callback=True)
    room = server.rooms[code]
    assert ack['team_id'] not in room.teams and room.owner_id == g['team_id']
    guest.emit('leave_room', {}, callback=True)
    assert code not in server.rooms


def test_leave_during_auction_keeps_team_and_rejoin_restores(clients):
    owner, guest = clients(), clients()
    code = owner.emit('create_room', {'name': 'A', 'team': 'A'}, callback=True)['room_code']
    g = guest.emit('join_room', {'room_code': code, 'name': 'b', 'team': 'B'}, callback=True)
    owner.emit('start_auction', {}, callback=True)
    guest.emit('leave_room', {}, callback=True)
    room = server.rooms[code]
    assert g['team_id'] in room.teams and not room.teams[g['team_id']]['connected']
    back = clients().emit('rejoin', {'room_code': code, 'token': g['token']}, callback=True)
    assert back['ok'] and back['state']['phase'] == 'bidding'


def test_rejoin_from_second_socket_takes_over_seat(clients):
    first = clients()
    ack = first.emit('create_room', {'name': 'A', 'team': 'A'}, callback=True)
    second = clients()
    assert second.emit('rejoin', {'room_code': ack['room_code'], 'token': ack['token']}, callback=True)['ok']
    # The old socket no longer controls the seat
    assert not first.emit('start_auction', {}, callback=True)['ok']
    assert second.emit('start_auction', {}, callback=True)['ok']


def test_guest_cannot_drive_auction_over_socket(clients):
    owner, guest = clients(), clients()
    code = owner.emit('create_room', {'name': 'A', 'team': 'A'}, callback=True)['room_code']
    guest.emit('join_room', {'room_code': code, 'name': 'b', 'team': 'B'}, callback=True)
    assert not guest.emit('update_settings', {'squad_size': 20}, callback=True)['ok']
    owner.emit('start_auction', {}, callback=True)
    for action in ('pause', 'sell_now', 'skip_player', 'skip_batch', 'end'):
        assert not guest.emit('owner_action', {'action': action}, callback=True)['ok']
    assert server.rooms[code].phase == 'bidding'


def test_state_versions_increase(clients):
    owner, guest = clients(), clients()
    code = owner.emit('create_room', {'name': 'A', 'team': 'A'}, callback=True)['room_code']
    guest.emit('join_room', {'room_code': code, 'name': 'b', 'team': 'B'}, callback=True)
    owner.emit('start_auction', {}, callback=True)
    owner.emit('owner_action', {'action': 'pause'}, callback=True)
    owner.emit('owner_action', {'action': 'resume'}, callback=True)
    versions = [e['args'][0]['v'] for e in guest.get_received() if e['name'] == 'state']
    assert versions == sorted(versions) and len(set(versions)) == len(versions)


def test_cleanup_removes_idle_and_finished_rooms():
    server.rooms.clear()
    idle, done, live = Room('IDLE1', make_players()), Room('DONE1', make_players()), Room('LIVE1', make_players())
    for r in (idle, done, live):
        server.rooms[r.code] = r
    t = idle.add_team('a', 'A', 's1'); t['connected'] = False
    idle.last_active = 0
    done.add_team('b', 'B', 's2'); done.phase = 'finished'; done.last_active = 0
    live.add_team('c', 'C', 's3'); live.last_active = 0
    server.cleanup_rooms(server.FINISHED_ROOM_TTL + 1)
    assert set(server.rooms) == {'LIVE1'}


# ---------------------------------------------------------------- room logic edge cases
def test_skip_player_discards_bids_and_keeps_purse():
    room, (a, b) = room_with_teams()
    lot = started(room, a)
    room.bid(b['id'], lot['lot_id'], room.pool[lot['pid']]['base'], 1)
    room.owner_action(a['id'], 'skip_player', 2)
    assert room.phase == 'sold_pause' and not room.last_result['sold']
    assert room.purse_left(b) == 100 * 100 and not b['squad']


def test_end_mid_lot_moves_player_to_unsold():
    room, (a, b) = room_with_teams()
    lot = started(room, a)
    room.owner_action(a['id'], 'end', 1)
    assert room.phase == 'finished' and lot['pid'] in room.unsold
    assert 'stats' in room.snapshot(1) and 'unsold' in room.snapshot(1)


def test_unaffordable_players_are_passed_over():
    players = [{'name': 'Star', 'overall': 95, 'role': 'Batsman', 'nationality': 'X'},
               {'name': 'Cheap', 'overall': 50, 'role': 'Bowler', 'nationality': 'X'}]
    room, (a, b) = room_with_teams(players=players, auction_order='skill_desc')
    for t in (a, b):
        t['squad'] = [{'price': 100 * 100 - 60}]   # 60 L left each
    room.start(a['id'], 0)
    assert room.pool[room.lot['pid']]['name'] == 'Cheap'
    assert any(room.pool[pid]['name'] == 'Star' for pid in room.unsold)


@pytest.mark.parametrize('seed', range(25))
def test_random_auctions_keep_invariants(seed):
    """Simulate whole auctions with random bids/owner actions; check the books always balance."""
    import random as rnd
    rng = rnd.Random(seed)
    server.random.seed(seed)
    pool = [{'name': f'P{i}', 'overall': rng.randint(45, 99), 'role': rng.choice(['Bat', 'Bowl', 'AR', 'WK']),
             'nationality': 'X'} for i in range(rng.randint(5, 40))]
    room = Room('FUZZ1', pool)
    room.settings.update(squad_size=rng.randint(5, 8), team_purse=rng.choice([50, 90, 100]),
                         auction_order=rng.choice(['tiered', 'tiered_skill', 'roles', 'roles_skill', 'random', 'skill_desc']))
    teams = [room.add_team(f'u{i}', f'T{i}', f's{i}') for i in range(rng.randint(2, 6))]
    now = 0.0
    room.start(teams[0]['id'], now)
    for _ in range(3000):
        if room.phase == 'finished':
            break
        now += rng.choice([0.1, 0.5, 1, 3, 25])
        roll = rng.random()
        try:
            if roll < 0.7 and room.phase == 'bidding':
                t = rng.choice(teams)
                opts = room.next_valid_bids(t) or [rng.randint(0, 5000)]
                room.bid(t['id'], room.lot['lot_id'] if rng.random() < .95 else -1, rng.choice(opts), now)
            elif roll < 0.74:
                room.owner_action(teams[0]['id'], rng.choice(['pause', 'resume', 'sell_now', 'skip_player', 'skip_batch', 'next_batch', 'start_batch']), now, value=rng.randint(0, 4))
        except AuctionError:
            pass
        room.tick(now)
        # invariants
        owned = [p['pid'] for t in teams for p in t['squad']]
        assert len(owned) == len(set(owned)), 'player in two squads'
        for t in teams:
            assert room.purse_left(t) >= 0
            assert len(t['squad']) <= room.settings['squad_size']
        queued = [pid for b in room.batches for pid in b['queue']]
        current = [room.lot['pid']] if room.lot else []
        accounted = owned + room.unsold + room.skipped + queued + current
        assert len(accounted) == len(set(accounted)) == len(room.pool), 'player lost or duplicated'
    else:
        room.owner_action(teams[0]['id'], 'end', now)
    assert room.phase == 'finished'
    snap = room.snapshot(now)
    assert snap['stats']['total_sold'] == sum(len(t['squad']) for t in teams)


# ---------------------------------------------------------------- timer
def test_custom_timer_in_lobby():
    room, (a, b) = room_with_teams()
    room.update_settings(a['id'], {'bid_timer': 42, 'break_seconds': 9})
    assert room.settings['bid_timer'] == 42 and room.settings['break_seconds'] == 9
    for bad in (4, 121, 'x'):
        with pytest.raises(AuctionError):
            room.update_settings(a['id'], {'bid_timer': bad})


def test_set_timer_mid_auction():
    room, (a, b) = room_with_teams(bid_timer=30)
    lot = started(room, a)                       # deadline 30
    room.owner_action(a['id'], 'set_timer', 10, value=8)
    assert room.settings['bid_timer'] == 8
    assert lot['deadline'] == pytest.approx(18)  # shortened to now + 8
    room.owner_action(a['id'], 'set_timer', 11, value=60)
    assert lot['deadline'] == pytest.approx(18)  # never lengthens a running clock
    room.bid(b['id'], lot['lot_id'], room.pool[lot['pid']]['base'], 12)
    assert lot['deadline'] == pytest.approx(72)  # next bid uses the new timer
    with pytest.raises(AuctionError):
        room.owner_action(b['id'], 'set_timer', 13, value=20)
    with pytest.raises(AuctionError):
        room.owner_action(a['id'], 'set_timer', 13, value=500)


def test_set_timer_while_paused_caps_remaining():
    room, (a, b) = room_with_teams(bid_timer=30)
    started(room, a)
    room.owner_action(a['id'], 'pause', 2)       # 28s left
    room.owner_action(a['id'], 'set_timer', 3, value=10)
    assert room.paused_remaining == 10


def test_set_timer_over_socket(clients):
    owner = clients()
    owner.emit('create_room', {'name': 'A', 'team': 'A'}, callback=True)
    owner.emit('start_auction', {}, callback=True)
    r = owner.emit('owner_action', {'action': 'set_timer', 'value': 25}, callback=True)
    assert r['ok']
    assert last_state(owner)['settings']['bid_timer'] == 25


# ---------------------------------------------------------------- role batches
def role_players():
    roles = ['Batsman', 'Bowler', 'All-rounder', 'Wicket-keeper']
    return [{'name': f'{r[:3]}{i}', 'overall': 70 + i, 'role': r, 'nationality': 'X'} for r in roles for i in range(3)]


def test_role_batches_order_and_skip():
    room, (a, b) = room_with_teams(players=role_players(), auction_order='roles_skill')
    started(room, a)
    assert [bt['name'] for bt in room.batches] == ['Batsmen', 'Wicket-keepers', 'All-rounders', 'Bowlers']
    assert room.pool[room.lot['pid']]['role'] == 'Batsman'
    assert room.pool[room.lot['pid']]['overall'] == 72          # best first
    room.owner_action(a['id'], 'skip_batch', 1)                  # done with batsmen
    assert room.batches[room.pending_batch]['name'] == 'Wicket-keepers'


def test_pick_any_remaining_batch_during_interval():
    room, (a, b) = room_with_teams(players=role_players(), auction_order='roles')
    started(room, a)
    room.owner_action(a['id'], 'skip_batch', 1)                  # interval before Wicket-keepers
    bowlers = [bt['name'] for bt in room.batches].index('Bowlers')
    room.owner_action(a['id'], 'start_batch', 2, value=bowlers)
    assert room.phase == 'bidding' and room.pool[room.lot['pid']]['role'] == 'Bowler'
    assert [bt['name'] for bt in room.batches] == ['Batsmen', 'Bowlers', 'Wicket-keepers', 'All-rounders']
    with pytest.raises(AuctionError):
        room.owner_action(a['id'], 'start_batch', 3, value=3)      # not in an interval
    room.owner_action(a['id'], 'skip_batch', 4)
    with pytest.raises(AuctionError):
        room.owner_action(a['id'], 'start_batch', 5, value=0)      # Batsmen already ran
    with pytest.raises(AuctionError):
        room.owner_action(b['id'], 'start_batch', 5, value=3)      # guests cannot


def test_role_counts_in_snapshot():
    room, _ = room_with_teams(players=role_players())
    assert room.snapshot(0)['role_counts'] == {'Batsman': 3, 'Bowler': 3, 'All-rounder': 3, 'Wicket-keeper': 3}
