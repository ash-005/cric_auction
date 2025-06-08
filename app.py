from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import uuid
import threading
import time
import random
import os

app = Flask(__name__)
# Use environment variable for secret key in production
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cricket_auction_secret')

# Configure SocketIO for production deployment
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',
    logger=False,
    engineio_logger=False
)

# Global state to store room information
rooms = {}
timers = {}
timer_threads = {}  # Store timer thread references

def calculate_base_price(skill_rating):
    """Calculate base price based on skill rating"""
    if skill_rating >= 91:
        return random.choice([4.5, 5.0]) * 10000000  # Convert to paise (4.5-5.0 Cr)
    elif skill_rating >= 86:
        return random.choice([3.0, 3.5, 4.0]) * 10000000  # 3.0-4.0 Cr
    elif skill_rating >= 77:
        return random.choice([1.5, 2.0, 2.5]) * 10000000  # 1.5-2.5 Cr
    elif skill_rating >= 70:
        return random.choice([1.0, 1.5]) * 10000000  # 1.0-1.5 Cr
    elif skill_rating >= 60:
        return 1.0 * 10000000  # 1.0 Cr
    else:
        return 0.5 * 10000000  # 0.5 Cr

class Room:
    """Represents an auction room."""
    def __init__(self, code, owner, player_list):
        self.code = code
        self.owner = owner
        self.bidders = [owner]  # List of users/bidders in the room
        self.auction_players = player_list # The list of players to be auctioned, from CSV
        self.current_player_index = 0
        self.current_bid = 0
        self.current_bidder = None
        self.auction_started = False
        self.timer_active = False
        self.sold_players = []
        self.auction_ended_early = False # New flag

        # New configuration options
        self.max_players = 8  # Default purse amount of players
        self.squad_size = 11  # Default squad size
        self.auction_order = 'random'  # Default auction order (random, alphabetical, skill_desc, skill_asc)
        self.team_purse = 100  # Default team purse in Cr (90, 100, 120, 150)
        self.team_compositions = {}  # Track team compositions for each bidder
        self.team_names = {}  # Map username to team name

    def add_bidder(self, username, team_name=None):
        """Adds a new user/bidder to the room."""
        if username not in self.bidders:
            self.bidders.append(username)
            # Store team name (default to username if not provided)
            self.team_names[username] = team_name or username
            # Initialize team composition for new bidder
            self.team_compositions[username] = {
                'players': [],
                'total_spent': 0,
                'remaining_purse': self.team_purse * 10000000,  # Convert Cr to paise
                'roles': {'Batsman': 0, 'Bowler': 0, 'All-rounder': 0, 'Wicket-keeper': 0}
            }

    def get_current_player(self):
        """Gets the current player being auctioned."""
        if self.current_player_index < len(self.auction_players):
            return self.auction_players[self.current_player_index]
        return None

    def next_player(self):
        """Moves to the next player in the auction list."""
        if self.current_player_index < len(self.auction_players) - 1:
            self.current_player_index += 1
            self.current_bid = 0
            self.current_bidder = None
            return True
        return False

    def update_team_composition(self, bidder, player, price):
        """Updates team composition when a player is sold."""
        if bidder and bidder in self.team_compositions:
            self.team_compositions[bidder]['players'].append(player)
            self.team_compositions[bidder]['total_spent'] += price
            self.team_compositions[bidder]['remaining_purse'] -= price
            role = player.get('role', 'Unknown')
            if role in self.team_compositions[bidder]['roles']:
                self.team_compositions[bidder]['roles'][role] += 1

    def sort_auction_players(self):
        """Sorts auction players based on the selected order."""
        if self.auction_order == 'alphabetical':
            self.auction_players.sort(key=lambda x: x['name'])
        elif self.auction_order == 'skill_desc':
            self.auction_players.sort(key=lambda x: x.get('overall', 0), reverse=True)
        elif self.auction_order == 'skill_asc':
            self.auction_players.sort(key=lambda x: x.get('overall', 0))
        elif self.auction_order == 'random':
            random.shuffle(self.auction_players)

def generate_room_code():
    """Generates a unique 6-character room code."""
    return str(uuid.uuid4())[:6].upper()

def timer_countdown(room_code):
    """Handles the 15-second countdown timer for a bid with proper synchronization."""
    import time

    if room_code not in rooms:
        return

    room = rooms[room_code]
    countdown = 15

    # Store the current timer thread
    current_thread = threading.current_thread()
    timer_threads[room_code] = current_thread

    # The timer runs as long as it's marked as active for the room
    while countdown > 0 and timers.get(room_code, False) and not room.auction_ended_early:
        # Check if this thread is still the active timer thread
        if timer_threads.get(room_code) != current_thread:
            return  # Another timer has taken over

        socketio.emit('timer_tick', {'time': countdown}, to=room_code)
        time.sleep(1)  # Use regular time.sleep for timer threads
        countdown -= 1

    # Only proceed if this is still the active timer thread
    if timer_threads.get(room_code) == current_thread and timers.get(room_code, False) and not room.auction_ended_early:
        timers[room_code] = False
        socketio.emit('times_up', to=room_code)

def start_timer(room_code):
    """Start a new timer for the room, stopping any existing timer."""
    # Stop existing timer
    stop_timer(room_code)

    # Start new timer
    timers[room_code] = True
    timer_thread = threading.Thread(target=timer_countdown, args=(room_code,))
    timer_thread.daemon = True
    timer_thread.start()

def stop_timer(room_code):
    """Stop the timer for the room."""
    timers[room_code] = False
    if room_code in timer_threads:
        del timer_threads[room_code]
        

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

@app.route('/health')
def health_check():
    """Health check endpoint for monitoring."""
    return {
        'status': 'healthy',
        'active_rooms': len(rooms),
        'service': 'cricket-auction-app'
    }

@socketio.on('create_room')
def handle_create_room(data):
    """
    Handles room creation. Receives username and the player list from the CSV.
    """
    username = data['username']
    team_name = data.get('team_name', username)  # Use provided team name or default to username
    player_list = data['players'] # Player list from the uploaded CSV
    room_code = generate_room_code()

    # Update base prices using the new calculation
    for player in player_list:
        skill_rating = player.get('overall', 50)
        player['base_price'] = calculate_base_price(skill_rating)

    # Create a new Room instance with the provided player list
    room = Room(room_code, username, player_list)
    # Initialize owner with their chosen team name
    room.add_bidder(username, team_name)
    rooms[room_code] = room
    join_room(room_code)

    # Get team names for display
    team_names_list = [room.team_names.get(bidder, bidder) for bidder in room.bidders]

    emit('room_created', {
        'room_code': room_code,
        'players': team_names_list, # Show team names instead of usernames
        'room_settings': {
            'max_players': room.max_players,
            'squad_size': room.squad_size,
            'auction_order': room.auction_order,
            'team_purse': room.team_purse
        }
    })

@socketio.on('update_room_settings')
def handle_update_room_settings(data):
    """Handles room settings updates by the owner."""
    room_code = data['room_code']
    username = data['username']

    if room_code not in rooms:
        emit('error', {'message': 'Room not found'})
        return

    room = rooms[room_code]

    # Only the owner can update settings
    if room.owner != username:
        emit('error', {'message': 'Only the room owner can update settings.'})
        return

    # Update room settings
    if 'max_players' in data:
        room.max_players = data['max_players']
    if 'squad_size' in data:
        room.squad_size = data['squad_size']
    if 'auction_order' in data:
        room.auction_order = data['auction_order']
        room.sort_auction_players()  # Re-sort players based on new order
    if 'team_purse' in data:
        room.team_purse = data['team_purse']
        # Update remaining purse for all existing bidders
        for bidder in room.team_compositions:
            spent = room.team_compositions[bidder]['total_spent']
            room.team_compositions[bidder]['remaining_purse'] = (room.team_purse * 10000000) - spent

    # Broadcast updated settings to all players in the room
    emit('room_settings_updated', {
        'max_players': room.max_players,
        'squad_size': room.squad_size,
        'auction_order': room.auction_order,
        'team_purse': room.team_purse
    }, to=room_code)

@socketio.on('get_team_compositions')
def handle_get_team_compositions(data):
    """Returns current team compositions for all bidders."""
    room_code = data['room_code']

    if room_code not in rooms:
        emit('error', {'message': 'Room not found'})
        return

    room = rooms[room_code]
    # Include ALL teams in the response, even those with no players
    compositions_with_names = {}
    for username in room.bidders:  # Iterate through all bidders
        team_name = room.team_names.get(username, username)
        composition = room.team_compositions.get(username, {
            'players': [],
            'total_spent': 0,
            'remaining_purse': room.team_purse * 10000000,
            'roles': {'Batsman': 0, 'Bowler': 0, 'All-rounder': 0, 'Wicket-keeper': 0}
        })
        compositions_with_names[team_name] = composition

    emit('team_compositions_update', {
        'compositions': compositions_with_names
    })

@socketio.on('join_room')
def handle_join_room(data):
    """Handles a user joining an existing room."""
    username = data['username']
    team_name = data.get('team_name', username)  # Use team name if provided, else username
    room_code = data['room_code'].upper()

    if room_code not in rooms:
        emit('error', {'message': 'Room not found'})
        return

    room = rooms[room_code]
    room.add_bidder(username, team_name)
    join_room(room_code)
    
    # Notify the user who just joined
    # Get team names for display
    team_names_list = [room.team_names.get(bidder, bidder) for bidder in room.bidders]

    emit('room_joined', {
        'room_code': room_code,
        'players': team_names_list
    })

    # Notify everyone else in the room that a new player has joined
    emit('player_joined', {
        'username': username,
        'players': team_names_list
    }, to=room_code)

@socketio.on('start_auction')
def handle_start_auction(data):
    """Starts the auction for the first player."""
    room_code = data['room_code']

    if room_code not in rooms:
        emit('error', {'message': 'Room not found'})
        return

    room = rooms[room_code]
    # Note: Owner validation removed for simplicity, but can be added back

    # Sort players based on auction order before starting
    room.sort_auction_players()
    room.auction_started = True
    current_player = room.get_current_player()

    if not current_player:
        emit('error', {'message': 'No players to auction.'})
        return

    room.current_bid = current_player['base_price']

    # Start the timer using new timer system
    start_timer(room_code)

    # Get team name for current bidder display (if any)
    current_bidder_display = room.team_names.get(room.current_bidder, room.current_bidder) if room.current_bidder else None

    emit('auction_started', {
        'player': current_player,
        'current_bid': room.current_bid,
        'current_bidder': current_bidder_display
    }, to=room_code)

@socketio.on('place_bid')
def handle_place_bid(data):
    """Handles a new bid placed by a user."""
    room_code = data['room_code']
    bid_amount = data['bid_amount']
    bidder = data['bidder']

    if room_code not in rooms:
        emit('error', {'message': 'Room not found'})
        return

    room = rooms[room_code]

    # Validate bid increment (must be +0.5Cr or +1Cr only)
    increment = bid_amount - room.current_bid
    valid_increments = [5000000, 10000000]  # 0.5Cr and 1Cr in paise

    if increment not in valid_increments:
        emit('error', {'message': 'Bid increment must be +0.5 Cr or +1 Cr only.'})
        return

    # Prevent consecutive bids from the same bidder
    if room.current_bidder == bidder:
        emit('error', {'message': 'You cannot bid consecutively. Wait for another team to bid first.'})
        return

    if bid_amount > room.current_bid:
        room.current_bid = bid_amount
        room.current_bidder = bidder

        # Reset the timer using new timer system
        start_timer(room_code)

        # Get team name for display
        bidder_team_name = room.team_names.get(bidder, bidder)

        emit('bid_updated', {
            'player': room.get_current_player(),
            'current_bid': room.current_bid,
            'current_bidder': bidder_team_name
        }, to=room_code)

        # Broadcast updated team compositions with team names - include ALL teams
        compositions_with_names = {}
        for username in room.bidders:  # Iterate through all bidders
            team_name = room.team_names.get(username, username)
            composition = room.team_compositions.get(username, {
                'players': [],
                'total_spent': 0,
                'remaining_purse': room.team_purse * 10000000,
                'roles': {'Batsman': 0, 'Bowler': 0, 'All-rounder': 0, 'Wicket-keeper': 0}
            })
            compositions_with_names[team_name] = composition

        emit('team_compositions_update', {
            'compositions': compositions_with_names
        }, to=room_code)
    else:
        emit('error', {'message': 'Bid must be higher than current bid.'})

@socketio.on('sell_player')
def handle_sell_player(data):
    """Sells the current player and moves to the next one."""
    room_code = data['room_code']

    if room_code not in rooms:
        emit('error', {'message': 'Room not found'})
        return

    room = rooms[room_code]
    current_player = room.get_current_player()

    # This event is triggered when the timer runs out, so stop the timer
    stop_timer(room_code)

    # Get team name for display
    winner_team_name = room.team_names.get(room.current_bidder, room.current_bidder) if room.current_bidder else None

    sold_info = {
        'player': current_player,
        'final_bid': room.current_bid,
        'winner': winner_team_name
    }
    room.sold_players.append(sold_info)

    # Update team composition if player was sold
    if room.current_bidder:
        room.update_team_composition(room.current_bidder, current_player, room.current_bid)

    emit('player_sold', sold_info, to=room_code)

    # Immediately broadcast updated team compositions after player sale
    compositions_with_names = {}
    for username in room.bidders:
        team_name = room.team_names.get(username, username)
        composition = room.team_compositions.get(username, {
            'players': [],
            'total_spent': 0,
            'remaining_purse': room.team_purse * 10000000,
            'roles': {'Batsman': 0, 'Bowler': 0, 'All-rounder': 0, 'Wicket-keeper': 0}
        })
        compositions_with_names[team_name] = composition

    emit('team_compositions_update', {
        'compositions': compositions_with_names
    }, to=room_code)

    # Wait a moment before moving to the next player
    socketio.sleep(3)
    
    if room.next_player() and not room.auction_ended_early: # Check the new flag
        next_player = room.get_current_player()
        room.current_bid = next_player['base_price']
        
        # Start timer for the next player
        start_timer(room_code)
        
        emit('next_player', {
            'player': next_player,
            'current_bid': room.current_bid,
            'current_bidder': None
        }, to=room_code)
    else:
        # If no more players or auction ended early, the auction is complete
        # Calculate auction statistics
        sold_players_only = [p for p in room.sold_players if p['winner']]
        most_expensive = None
        total_spent = 0

        if sold_players_only:
            most_expensive = max(sold_players_only, key=lambda x: x['final_bid'])
            total_spent = sum(p['final_bid'] for p in sold_players_only)
            avg_price = total_spent / len(sold_players_only)
        else:
            avg_price = 0

        emit('auction_complete', {
            'sold_players': room.sold_players,
            'statistics': {
                'most_expensive': most_expensive,
                'average_price': avg_price,
                'total_players_sold': len(sold_players_only),
                'total_spent': total_spent
            }
        }, to=room_code)

@socketio.on('skip_player')
def handle_skip_player(data):
    """Skips the current player being auctioned."""
    room_code = data['room_code']

    if room_code not in rooms:
        emit('error', {'message': 'Room not found'})
        return

    room = rooms[room_code]
    current_player = room.get_current_player()

    # Stop the current timer
    stop_timer(room_code)

    # Mark player as unsold (no winner)
    sold_info = {
        'player': current_player,
        'final_bid': 0,
        'winner': None
    }
    room.sold_players.append(sold_info)

    emit('player_sold', sold_info, to=room_code)

    # Wait a moment before moving to the next player
    socketio.sleep(3)

    if room.next_player() and not room.auction_ended_early:
        next_player = room.get_current_player()
        room.current_bid = next_player['base_price']

        # Start timer for the next player
        start_timer(room_code)

        emit('next_player', {
            'player': next_player,
            'current_bid': room.current_bid,
            'current_bidder': None
        }, to=room_code)
    else:
        # If no more players or auction ended early, the auction is complete
        # Calculate auction statistics
        sold_players_only = [p for p in room.sold_players if p['winner']]
        most_expensive = None
        total_spent = 0

        if sold_players_only:
            most_expensive = max(sold_players_only, key=lambda x: x['final_bid'])
            total_spent = sum(p['final_bid'] for p in sold_players_only)
            avg_price = total_spent / len(sold_players_only)
        else:
            avg_price = 0

        emit('auction_complete', {
            'sold_players': room.sold_players,
            'statistics': {
                'most_expensive': most_expensive,
                'average_price': avg_price,
                'total_players_sold': len(sold_players_only),
                'total_spent': total_spent
            }
        }, to=room_code)

@socketio.on('end_auction_early')
def handle_end_auction_early(data):
    """Ends the auction prematurely."""
    room_code = data['room_code']
    
    if room_code not in rooms:
        emit('error', {'message': 'Room not found'})
        return
    
    room = rooms[room_code]
    
    # Only the owner can end the auction early
    # if room.owner != request.sid: 
    #     emit('error', {'message': 'Only the room owner can end the auction early.'})
    #     return

    room.auction_ended_early = True # Set the new flag
    
    # Stop the current timer if active
    stop_timer(room_code)

    # Calculate auction statistics
    sold_players_only = [p for p in room.sold_players if p['winner']]
    most_expensive = None
    total_spent = 0

    if sold_players_only:
        most_expensive = max(sold_players_only, key=lambda x: x['final_bid'])
        total_spent = sum(p['final_bid'] for p in sold_players_only)
        avg_price = total_spent / len(sold_players_only)
    else:
        avg_price = 0

    emit('auction_complete', {
        'sold_players': room.sold_players,
        'statistics': {
            'most_expensive': most_expensive,
            'average_price': avg_price,
            'total_players_sold': len(sold_players_only),
            'total_spent': total_spent
        }
    }, to=room_code)

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    socketio.run(app, debug=debug, host='0.0.0.0', port=port)