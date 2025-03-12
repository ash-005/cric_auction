import streamlit as st
import pandas as pd
import random
import time
import uuid
import os

# Set page config
st.set_page_config(
    page_title="Cricket Auction Simulator",
    page_icon="🏏",
    layout="wide"
)

# Initialize session state variables if they don't exist
if 'app_stage' not in st.session_state:
    st.session_state.app_stage = 'setup'
if 'teams' not in st.session_state:
    st.session_state.teams = []
if 'players' not in st.session_state:
    st.session_state.players = []
if 'current_player' not in st.session_state:
    st.session_state.current_player = None
if 'current_bid' not in st.session_state:
    st.session_state.current_bid = 0
if 'current_team' not in st.session_state:
    st.session_state.current_team = None
if 'auction_complete' not in st.session_state:
    st.session_state.auction_complete = False
if 'remaining_players' not in st.session_state:
    st.session_state.remaining_players = []
if 'last_bidder' not in st.session_state:
    st.session_state.last_bidder = None
if 'player_batches' not in st.session_state:
    st.session_state.player_batches = {}

# Load player data from CSV - MODIFIED FOR NEW CSV FORMAT
def load_players_from_csv(file_path='player_list.csv'):
    """
    Load players from a CSV file with columns: Name, Overall, Role, Nationality
    """
    try:
        df = pd.read_csv(file_path)
        
        # Check if required columns exist
        required_cols = ['Name', 'Overall', 'Role', 'Nationality']
        missing_cols = [col for col in required_cols if col not in df.columns]
        
        if missing_cols:
            st.error(f"Missing columns in CSV: {', '.join(missing_cols)}")
            # Generate sample data if CSV is invalid
            return generate_sample_players()
        
        players = []
        
        for _, row in df.iterrows():
            # Generate random stats based on Overall rating
            skill = float(row['Overall'])
            
            # Determine batting and bowling averages based on role and skill rating
            role = row['Role'].strip().lower()
            if role in ['batsman', 'wicket-keeper', 'batter', 'wicketkeeper']:
                batting_avg = int(skill * 0.5 + random.uniform(15, 25))  # Higher batting avg for batsmen
                bowling_avg = int((100 - skill) * 0.3 + random.uniform(25, 40))  # Lower bowling avg is better
            elif role in ['bowler']:
                batting_avg = int(skill * 0.2 + random.uniform(10, 20))  # Lower batting avg for bowlers
                bowling_avg = int((100 - skill) * 0.4 + random.uniform(15, 25))  # Better bowling avg for bowlers
            else:  # All-rounder or other
                batting_avg = int(skill * 0.4 + random.uniform(15, 20))
                bowling_avg = int((100 - skill) * 0.35 + random.uniform(20, 30))
            
            # Calculate matches based on skill (as a proxy for experience)
            matches_played = int(skill + random.randint(5, 50))
            
            # Determine base price based on overall rating
            if skill >= 86:
                base_price = random.choice([3.0, 3.5, 4.0, 5.0])
            elif skill >= 77:
                base_price = random.choice([1.5, 1.75, 2.0, 2.5])
            elif skill >= 70:
                base_price = random.choice([1.0, 1.25, 1.5])
            else:
                base_price = random.choice([0.5, 0.75, 1.0])
            
            # Standardize role names
            if role in ['batsman', 'batter']:
                standardized_role = 'Batsman'
            elif role in ['bowler']:
                standardized_role = 'Bowler'
            elif role in ['all-rounder', 'allrounder', 'all rounder']:
                standardized_role = 'All-rounder'
            elif role in ['wicket-keeper', 'wicketkeeper', 'keeper']:
                standardized_role = 'Wicket-keeper'
            else:
                standardized_role = 'All-rounder'  # Default case
            
            player = {
                'id': str(uuid.uuid4()),
                'name': row['Name'],
                'role': standardized_role,
                'country': row['Nationality'],
                'base_price': base_price,
                'overall_rating': int(skill),  # Store the overall rating from CSV
                'stats': {
                    'batting_avg': batting_avg,
                    'bowling_avg': bowling_avg,
                    'matches_played': matches_played,
                    'skill_rating': int(skill)
                },
                'status': 'unsold'
            }
            players.append(player)
        
        return players
    except Exception as e:
        st.error(f"Error loading CSV: {e}")
        # Fallback to sample data if CSV can't be loaded
        return generate_sample_players()

# Sample player data (fallback in case CSV isn't available)
def generate_sample_players():
    player_roles = ['Batsman', 'Bowler', 'All-rounder', 'Wicket-keeper']
    player_countries = ['India', 'Australia', 'England', 'New Zealand', 'South Africa', 'West Indies', 'Pakistan', 'Sri Lanka']
    
    players = []
    # Generate sample player data
    for i in range(100):
        skill_rating = random.randint(50, 95)
        player = {
            'id': str(uuid.uuid4()),
            'name': f"Player {i+1}",
            'role': random.choice(player_roles),
            'country': random.choice(player_countries),
            'base_price': random.choice([0.5, 0.75, 1.0, 1.5, 2.0]),  # Base price in crores
            'overall_rating': skill_rating,  # Add overall rating
            'stats': {
                'batting_avg': int(random.uniform(20, 60)),
                'bowling_avg': int(random.uniform(18, 40)),
                'matches_played': random.randint(10, 200),
                'skill_rating': skill_rating
            },
            'status': 'unsold'
        }
        players.append(player)
    return players

# Initialize or change app stage
def set_stage(stage):
    st.session_state.app_stage = stage

# Organize players into batches by role
def organize_players_by_role(players):
    batches = {
        'Batsman': [],
        'Bowler': [],
        'All-rounder': [],
        'Wicket-keeper': []
    }
    
    for player in players:
        role = player['role']
        # Ensure compatibility with different case or naming conventions
        if role.lower() == 'batsman' or role.lower() == 'batter':
            batches['Batsman'].append(player)
        elif role.lower() == 'bowler':
            batches['Bowler'].append(player)
        elif role.lower() == 'all-rounder' or role.lower() == 'allrounder':
            batches['All-rounder'].append(player)
        elif role.lower() == 'wicket-keeper' or role.lower() == 'wicketkeeper' or role.lower() == 'keeper':
            batches['Wicket-keeper'].append(player)
        else:
            # If role doesn't match, put in a default category
            batches['All-rounder'].append(player)
    
    return batches

def setup_teams():
    st.title("🏏 Cricket Player Auction Simulator")
    
    st.markdown("""
    ## Setup Teams
    Enter the number of teams participating in the auction and their details.
    Each team will have a purse amount to spend on players.
    """)
    
    # Option to upload a custom player data CSV
    uploaded_file = st.file_uploader("Upload Player Data CSV (Optional)", type=['csv'])
    if uploaded_file is not None:
        # Save the uploaded file temporarily
        with open('uploaded_player_data.csv', 'wb') as f:
            f.write(uploaded_file.getbuffer())
        st.success("CSV file uploaded successfully! Players will be loaded from this file.")
    
    num_teams = st.number_input("Number of Teams", min_value=2, max_value=10, value=3, step=1)
    default_purse = st.number_input("Default Purse Amount per Team (in crores)", min_value=5.0, max_value=100.0, value=90.0, step=0.5)
    
    with st.form("team_setup_form"):
        teams = []
        cols = st.columns(2)
        
        for i in range(num_teams):
            with cols[i % 2]:
                st.subheader(f"Team {i+1}")
                name = st.text_input(f"Team Name", value=f"Team {i+1}", key=f"team_name_{i}")
                purse = st.number_input(f"Purse Amount (in crores)", min_value=5.0, max_value=100.0, value=default_purse, step=0.5, key=f"team_purse_{i}")
                
                team = {
                    'id': str(uuid.uuid4()),
                    'name': name,
                    'purse': purse,
                    'original_purse': purse,
                    'players': [],
                    'can_bid': True
                }
                teams.append(team)
        
        max_squad_size = st.number_input("Maximum Squad Size per Team", min_value=11, max_value=25, value=15, step=1)
        
        # Auction settings
        st.subheader("Auction Settings")
        
        # Option to select which player types to auction first
        auction_order = st.multiselect(
            "Auction Order (Select types in the order you want to auction them)",
            ["Batsman", "Bowler", "All-rounder", "Wicket-keeper"],
            default=["Wicket-keeper", "Batsman", "Bowler", "All-rounder"]
        )
        
        submit_button = st.form_submit_button("Start Auction")
        
        if submit_button:
            st.session_state.teams = teams
            st.session_state.max_squad_size = max_squad_size
            
            # Load players from the uploaded CSV if it exists, otherwise use default or generate sample
            if uploaded_file is not None:
                st.session_state.players = load_players_from_csv('uploaded_player_data.csv')
            else:
                # Try to load from the default file path, fall back to sample data if it doesn't exist
                try:
                    st.session_state.players = load_players_from_csv()
                except:
                    st.session_state.players = generate_sample_players()
            
            # Organize players by role for batch auctioning
            st.session_state.player_batches = organize_players_by_role(st.session_state.players)
            
            # Store the auction order preference
            st.session_state.auction_order = auction_order if auction_order else ["Wicket-keeper", "Batsman", "Bowler", "All-rounder"]
            
            # Initialize remaining players with the first role in the auction order
            if st.session_state.auction_order and st.session_state.auction_order[0] in st.session_state.player_batches:
                first_role = st.session_state.auction_order[0]
                st.session_state.remaining_players = st.session_state.player_batches[first_role].copy()
                st.session_state.current_batch = first_role
            else:
                st.session_state.remaining_players = st.session_state.players.copy()
                st.session_state.current_batch = "All Players"
            
            set_stage('auction')
            st.experimental_rerun()

def check_auction_complete():
    # Check if current batch is empty
    if not st.session_state.remaining_players:
        # Find the next batch to auction based on auction order
        next_batch = None
        for role in st.session_state.auction_order:
            if role in st.session_state.player_batches and st.session_state.player_batches[role]:
                next_batch = role
                st.session_state.remaining_players = st.session_state.player_batches[role].copy()
                st.session_state.player_batches[role] = []  # Clear this batch so we don't reuse it
                st.session_state.current_batch = next_batch
                break
                
        # If no next batch, check if auction is complete
        if not next_batch:
            # Check if all teams have max players or can't bid
            eligible_teams = [t for t in st.session_state.teams if t['can_bid']]
            if not eligible_teams:
                st.session_state.auction_complete = True
                set_stage('results')

def view_team_players(team):
    players = team['players']
    if not players:
        st.info(f"{team['name']} hasn't acquired any players yet.")
        return
    
    player_data = []
    for p in players:
        player_data.append({
            'Name': p['name'],
            'Role': p['role'],
            'Country': p['country'],
            'Overall': p.get('overall_rating', p['stats']['skill_rating']),
            'Price': f"₹{p.get('sold_price', 0)} crores",
            'Batting Avg': p['stats']['batting_avg'],
            'Bowling Avg': p['stats']['bowling_avg']
        })
    
    st.dataframe(pd.DataFrame(player_data), use_container_width=True)

# In the bidding interface section, we need to modify the logic to handle the first bid differently
# Look for the section where the bid buttons are created

def auction_screen():
    st.title("🏏 Cricket Player Auction")
    
    # Display current batch being auctioned
    st.markdown(f"### Currently Auctioning: {st.session_state.current_batch}s")
    st.markdown(f"*{len(st.session_state.remaining_players)} players remaining in this batch*")
    
    # Display teams and their status
    cols = st.columns(len(st.session_state.teams))
    for i, team in enumerate(st.session_state.teams):
        with cols[i]:
            st.subheader(team['name'])
            st.metric("Remaining Purse", f"₹{team['purse']} crores")
            st.metric("Players", len(team['players']))
            
            # Add dropdown to view current squad
            if st.expander(f"View {team['name']} Squad"):
                view_team_players(team)
            
            # Disable bidding if team has reached max squad size
            if len(team['players']) >= st.session_state.max_squad_size:
                team['can_bid'] = False
                st.warning("Squad Full")
            
            # Disable bidding if team has insufficient funds for minimum bid
            if team['purse'] < 0.5:  # Assuming 0.5 crore is the minimum bid possible
                team['can_bid'] = False
                st.warning("Insufficient Funds")
    
    # Check if auction is complete
    check_auction_complete()
    if st.session_state.auction_complete:
        st.experimental_rerun()
    
    # Player selection
    if st.session_state.current_player is None and st.session_state.remaining_players:
        # Get a new player for auction
        # Sort remaining players by base price and skill for more interesting auction experience
        sorted_players = sorted(st.session_state.remaining_players, 
                               key=lambda x: (x['base_price'], x['stats']['skill_rating']), 
                               reverse=True)
        
        player_index = random.randint(0, min(9, len(sorted_players)-1))  # Pick from top 10 players
        st.session_state.current_player = sorted_players[player_index]
        st.session_state.current_bid = st.session_state.current_player['base_price']  # Start bid at base price
        st.session_state.current_team = None
        st.session_state.last_bidder = None
        
        # Remove this player from remaining players
        st.session_state.remaining_players = [p for p in st.session_state.remaining_players 
                                              if p['id'] != st.session_state.current_player['id']]
    
    # Display current player for auction
    if st.session_state.current_player:
        st.markdown("---")
        player = st.session_state.current_player
    
        # Make the auctioning panel bigger
        auction_col, bid_col = st.columns([3, 1])  # Make auction details take more space
    
        with auction_col:
            #st.markdown(f"<h2 style='text-align: center; color: #ff4b4b;'>🎯 Now Auctioning: {player['name']}</h2>", unsafe_allow_html=True)
            st.markdown(f"<h2 style='text-align: center; color: #ff4b4b;'>🎯 Now Auctioning:<span style='color:#FFD400; font-weight: bold;'>  {player['name']}</span></h2>", unsafe_allow_html=True)
            # MODIFIED: Display Overall Rating prominently
            overall_rating = player.get('overall_rating', player['stats']['skill_rating'])
            st.markdown(f"<h3 style='text-align: center;'>Overall Rating: <span style='color: #7afbff;'>{overall_rating}</span> | Role:<span style='color:#7afbff; font-weight: bold;'> {player['role']} </span>| Country:<span style='color:#7afbff; font-weight: bold;'> {player['country']}</span></h3>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center; color: #007bff;'>Base Price: ₹{player['base_price']} crores</h3>", unsafe_allow_html=True)
    
            # Stats table
            stats_df = pd.DataFrame({
                'Stat': ['Overall Rating', 'Batting Average', 'Bowling Average', 'Matches Played'],
                'Value': [
                    overall_rating,
                    player['stats']['batting_avg'], 
                    player['stats']['bowling_avg'], 
                    player['stats']['matches_played']
                ]
            })
            st.table(stats_df)
    
        with bid_col:
            st.markdown("<h2 style='text-align: center;'>Current Bid</h2>", unsafe_allow_html=True)
            st.markdown(f"<h1 style='text-align: center; color: #28a745;'>₹{st.session_state.current_bid} crores</h1>", unsafe_allow_html=True)
            
            if st.session_state.current_team:
                team = next((t for t in st.session_state.teams if t['id'] == st.session_state.current_team), None)
                if team:
                    st.markdown(f"<h2 style='text-align: center; '>Current Bidder: <span style='color: #7afbff;'>{team['name']}</span></h2>", unsafe_allow_html=True)
                                                                                                        
        st.markdown("---")

        # Bidding interface - Show different options based on whether first bid has been made
        st.subheader("Place Your Bids")
        
        # Check if the first bid has been made yet
        first_bid_made = st.session_state.current_team is not None
        
        # For each team, display bidding options in a separate row
        for team in st.session_state.teams:
            # Check if this team is eligible to bid (can't bid if they were the last bidder)
            can_bid = team['can_bid'] and team['id'] != st.session_state.last_bidder
            
            # Create a row with columns for the team's bidding options
            if first_bid_made:
                # After first bid, show +0.5 and +1.0 increment options
                col1, col2, col3 = st.columns([2, 1, 1])
            else:
                # For first bid, just show one button to bid the base price
                col1, col2 = st.columns([2, 2])
            
            with col1:
                st.markdown(f"### {team['name']}")
                
                if not can_bid:
                    if team['id'] == st.session_state.last_bidder:
                        st.info("Waiting for another team to bid")
                    elif not team['can_bid']:
                        if len(team['players']) >= st.session_state.max_squad_size:
                            st.warning("Squad Full")
                        else:
                            st.warning("Insufficient Funds")
            
            # Different bidding UI based on whether first bid has been made
            if first_bid_made:
                # Calculate new bid amounts for increments
                half_cr_bid = round(st.session_state.current_bid + 0.5, 2)
                one_cr_bid = round(st.session_state.current_bid + 1.0, 2)
                
                # Check if team can afford the bids
                can_bid_half_cr = can_bid and team['purse'] >= half_cr_bid
                can_bid_one_cr = can_bid and team['purse'] >= one_cr_bid
                
                with col2:
                    # 0.5 Cr bid button
                    if can_bid_half_cr:
                        if st.button(f"Bid ₹{half_cr_bid} Cr (+0.5)", key=f"bid_half_{team['id']}"):
                            st.session_state.current_bid = half_cr_bid
                            st.session_state.current_team = team['id']
                            st.session_state.last_bidder = team['id']
                            st.experimental_rerun()
                    else:
                        st.button(f"Bid ₹{half_cr_bid} Cr (+0.5)", disabled=True, key=f"disabled_half_{team['id']}")
                
                with col3:
                    # 1 Cr bid button
                    if can_bid_one_cr:
                        if st.button(f"Bid ₹{one_cr_bid} Cr (+1.0)", key=f"bid_one_{team['id']}"):
                            st.session_state.current_bid = one_cr_bid
                            st.session_state.current_team = team['id']
                            st.session_state.last_bidder = team['id']
                            st.experimental_rerun()
                    else:
                        st.button(f"Bid ₹{one_cr_bid} Cr (+1.0)", disabled=True, key=f"disabled_one_{team['id']}")
            else:
                # For first bid, show base price bid button
                base_price = player['base_price']
                can_bid_base = can_bid and team['purse'] >= base_price
                
                with col2:
                    # Base price bid button
                    if can_bid_base:
                        if st.button(f"Bid ₹{base_price} Cr (Base Price)", key=f"bid_base_{team['id']}"):
                            st.session_state.current_bid = base_price
                            st.session_state.current_team = team['id']
                            st.session_state.last_bidder = team['id']
                            st.experimental_rerun()
                    else:
                        st.button(f"Bid ₹{base_price} Cr (Base Price)", disabled=True, key=f"disabled_base_{team['id']}")
        
        # Add a separator between teams and action buttons
        st.markdown("---")
        
        # For first-time bidding, show base price as the starting bid
        if not st.session_state.current_team:
            st.markdown(f"#### Starting bid: ₹{player['base_price']} crores (Base Price)")
        
        # Action buttons (Sold/Unsold)
        action_col1, action_col2 = st.columns(2)
        
        with action_col1:
            # Add the "Sold!" button
            if st.session_state.current_team:  # Only enable if someone has bid
                sold_button = st.button("SOLD! ⚡", key="sold_button")
                if sold_button:
                    # Add player to the team that won the bid
                    team = next((t for t in st.session_state.teams if t['id'] == st.session_state.current_team), None)
                    if team:
                        player['status'] = 'sold'
                        player['sold_to'] = team['name']
                        player['sold_price'] = st.session_state.current_bid
                        team['players'].append(player)
                        team['purse'] -= st.session_state.current_bid
                        
                        st.success(f"{player['name']} sold to {team['name']} for ₹{st.session_state.current_bid} crores!")
                        time.sleep(1)  # Pause briefly to show the success message
                        
                        # Reset for next player
                        st.session_state.current_player = None
                        st.session_state.current_bid = 0
                        st.session_state.current_team = None
                        st.session_state.last_bidder = None
                        st.experimental_rerun()
            else:
                st.button("SOLD! ⚡", disabled=True)
        
        with action_col2:
            # Add "Unsold" button - only available if no bids have been made
            if not st.session_state.current_team:
                unsold_button = st.button("Unsold ❌", key="unsold_button")
                if unsold_button:
                    player['status'] = 'unsold'
                    st.info(f"{player['name']} remains unsold.")
                    time.sleep(1)  # Pause briefly to show the info message
                    
                    # Reset for next player
                    st.session_state.current_player = None
                    st.session_state.current_bid = 0
                    st.session_state.current_team = None
                    st.session_state.last_bidder = None
                    st.experimental_rerun()
            else:
                # If a bid has been made, disable the unsold button
                st.button("Unsold ❌", key="unsold_button", disabled=True)
    
    # Show auction progress
    st.markdown("---")
    st.subheader("Auction Progress")
    
    sold_players = [p for p in st.session_state.players if p['status'] == 'sold']
    unsold_players = [p for p in st.session_state.players if p['status'] == 'unsold']
    
    # Count how many players are still waiting for auction across all batches
    remaining_count = len(st.session_state.remaining_players)
    for role in st.session_state.player_batches:
        remaining_count += len(st.session_state.player_batches[role])
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Players Sold", len(sold_players))
    with col2:
        st.metric("Players Unsold", len(unsold_players))
    with col3:
        st.metric("Players Remaining", remaining_count)
    with col4:
        st.metric("Current Batch", st.session_state.current_batch)
    
    # Option to view transaction log
    if st.checkbox("Show Transaction Log"):
        if sold_players:
            transactions = []
            for p in sold_players:
                transactions.append({
                    'Player': p['name'],
                    'Role': p['role'],
                    'Country': p['country'],
                    'Overall': p.get('overall_rating', p['stats']['skill_rating']),
                    'Team': p.get('sold_to', ''),
                    'Price': f"₹{p.get('sold_price', 0)} crores"
                })
            st.table(pd.DataFrame(transactions))
        else:
            st.info("No transactions yet.")

def save_team_to_csv(team):
    """Save individual team data to a CSV file"""
    if not os.path.exists('team_data'):
        os.makedirs('team_data')
        
    if not team['players']:
        return False
        
    player_data = []
    for p in team['players']:
        player_data.append({
            'Name': p['name'],
            'Role': p['role'],
            'Country': p['country'],
            'Overall': p.get('overall_rating', p['stats']['skill_rating']),
            'Price (crores)': p.get('sold_price', 0),
            'Batting Avg': p['stats']['batting_avg'],
            'Bowling Avg': p['stats']['bowling_avg'],
            'Matches': p['stats']['matches_played']
        })
    
    df = pd.DataFrame(player_data)
    
    # Add team summary at the bottom
    summary_df = pd.DataFrame([{
        'Name': f"TEAM SUMMARY: {team['name']}",
        'Role': '',
        'Country': '',
        'Overall': '',
        'Price (crores)': team['original_purse'] - team['purse'],
        'Batting Avg': '',
        'Bowling Avg': '',
        'Matches': ''
    }])
    
    result_df = pd.concat([df, summary_df])
    
    # Replace invalid characters in team name for filename
    filename = team['name'].replace(" ", "_").replace("/", "_").replace("\\", "_")
    filepath = f"team_data/{filename}.csv"
    result_df.to_csv(filepath, index=False)
    return filepath

def results_screen():
    st.title("🏆 Auction Results")
    
    st.markdown("""
    ## Auction Completed!
    View the final team compositions and statistics below.
    """)
    
    # Summary statistics
    total_spent = sum(team['original_purse'] - team['purse'] for team in st.session_state.teams)
    avg_player_price = round(total_spent / sum(len(team['players']) for team in st.session_state.teams), 2) if sum(len(team['players']) for team in st.session_state.teams) > 0 else 0
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Amount Spent", f"₹{total_spent} crores")
    with col2:
        st.metric("Avg. Player Price", f"₹{avg_player_price} crores")
    with col3:
        sold_players = [p for p in st.session_state.players if p['status'] == 'sold']
        if sold_players:
            highest_paid = max(sold_players, key=lambda x: x.get('sold_price', 0))
            st.metric("Highest Paid Player", f"{highest_paid['name']} (₹{highest_paid.get('sold_price', 0)} crores)")
        else:
            st.metric("Highest Paid Player", "None")
    
    # Team tabs
    team_tabs = st.tabs([team['name'] for team in st.session_state.teams])
    
    team_files = []
    for i, tab in enumerate(team_tabs):
        with tab:
            team = st.session_state.teams[i]
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Players Acquired", len(team['players']))
            with col2:
                st.metric("Purse Spent", f"₹{team['original_purse'] - team['purse']} crores")
            with col3:
                st.metric("Purse Remaining", f"₹{team['purse']} crores")
            
            # Team composition by role
            roles = {}
            for player in team['players']:
                role = player['role']
                if role in roles:
                    roles[role] += 1
                else:
                    roles[role] = 1
            
            if roles:
                st.subheader("Team Composition")
                composition_df = pd.DataFrame({
                    'Role': list(roles.keys()),
                    'Count': list(roles.values())
                })
                st.bar_chart(composition_df.set_index('Role'))
            
            # Player details
            st.subheader("Player List")
            if team['players']:
                player_data = []
                for p in team['players']:
                    player_data.append({
                        'Name': p['name'],
                        'Role': p['role'],
                        'Country': p['country'],
                        'Price': f"₹{p.get('sold_price', 0)} crores",
                        'Batting Avg': p['stats']['batting_avg'],
                        'Bowling Avg': p['stats']['bowling_avg'],
                        'Matches': p['stats']['matches_played'],
                        'Skill Rating': p['stats']['skill_rating']
                    })
                st.dataframe(pd.DataFrame(player_data), use_container_width=True)
                
                # Save team data to CSV
                filepath = save_team_to_csv(team)
                if filepath:
                    team_files.append((team['name'], filepath))
                    
                    # Provide download button for this team's CSV
                    team_df = pd.read_csv(filepath)
                    csv = team_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label=f"Download {team['name']} Squad",
                        data=csv,
                        file_name=f"{team['name']}_squad.csv",
                        mime="text/csv",
                    )
            else:
                st.info("No players acquired.")
    
    # Download all results
    if st.button("Download Complete Auction Results"):
        results = []
        for team in st.session_state.teams:
            for player in team['players']:
                results.append({
                    'Team': team['name'],
                    'Player': player['name'],
                    'Role': player['role'],
                    'Country': player['country'],
                    'Price (crores)': player.get('sold_price', 0),
                    'Batting Avg': player['stats']['batting_avg'],
                    'Bowling Avg': player['stats']['bowling_avg'],
                    'Matches': player['stats']['matches_played']
                })
        
        results_df = pd.DataFrame(results)
        
        # Convert dataframe to CSV
        csv = results_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Complete Auction CSV",
            data=csv,
            file_name="cricket_auction_results.csv",
            mime="text/csv",
        )
    
    # Display information about saved files
    if team_files:
        st.markdown("---")
        st.subheader("Team Files Saved")
        st.write("Each team's data has been saved to a separate CSV file with their respective name.")
        for team_name, filepath in team_files:
            st.success(f"{team_name}: {filepath}")
    
    # Reset auction
    if st.button("Start New Auction"):
        for key in st.session_state.keys():
            del st.session_state[key]
        st.experimental_rerun()

# Main app logic
def main():
    if st.session_state.app_stage == 'setup':
        setup_teams()
    elif st.session_state.app_stage == 'auction':
        auction_screen()
    elif st.session_state.app_stage == 'results':
        results_screen()

if __name__ == "__main__":
    main()