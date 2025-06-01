import streamlit as st
import pandas as pd
import random
import time
import uuid
import os
from io import BytesIO
import xlsxwriter 

# Set page config
st.set_page_config(
    page_title="Cricket Auction Simulator",
    page_icon="🏏",
    layout="wide"
)

# Initialize session state variables
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

# Load player data from CSV
def load_players_from_csv(file_path='player_list_complete.csv'):
    try:
        df = pd.read_csv(file_path)
        required_cols = ['Name', 'Overall', 'Role', 'Nationality']
        missing_cols = [col for col in required_cols if col not in df.columns]
        
        if missing_cols:
            return generate_sample_players()
        
        players = []
        for _, row in df.iterrows():
            skill = float(row['Overall'])
            role = row['Role'].strip().lower()
            
            if role in ['batsman', 'wicket-keeper', 'batter', 'wicketkeeper']:
                batting_avg = int(skill * 0.5 + random.uniform(15, 25))
                bowling_avg = int((100 - skill) * 0.3 + random.uniform(25, 40))
            elif role in ['bowler']:
                batting_avg = int(skill * 0.2 + random.uniform(10, 20))
                bowling_avg = int((100 - skill) * 0.4 + random.uniform(15, 25))
            else:
                batting_avg = int(skill * 0.4 + random.uniform(15, 20))
                bowling_avg = int((100 - skill) * 0.35 + random.uniform(20, 30))
            
            matches_played = int(skill + random.randint(5, 50))
            
            if skill >= 91:
                base_price = random.choice([4.5,5.0])
            elif skill >= 86:
                base_price = random.choice([3.0, 3.5, 4.0])
            elif skill >= 77:
                base_price = random.choice([1.5, 2.0, 2.5])
            elif skill >= 70:
                base_price = random.choice([1.0, 1.5])
            elif skill >= 60:
                base_price = 1.0
            else:
                base_price = 0.5
            
            if role in ['batsman', 'batter']:
                standardized_role = 'Batsman'
            elif role in ['bowler']:
                standardized_role = 'Bowler'
            elif role in ['all-rounder', 'allrounder', 'all rounder']:
                standardized_role = 'All-rounder'
            elif role in ['wicket-keeper', 'wicketkeeper', 'keeper']:
                standardized_role = 'Wicket-keeper'
            else:
                standardized_role = 'All-rounder'
            
            player = {
                'id': str(uuid.uuid4()),
                'name': row['Name'],
                'role': standardized_role,
                'country': row['Nationality'],
                'base_price': base_price,
                'overall_rating': int(skill),
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
        return generate_sample_players()

def generate_sample_players():
    player_roles = ['Batsman', 'Bowler', 'All-rounder', 'Wicket-keeper']
    player_countries = ['India', 'Australia', 'England', 'New Zealand', 'South Africa', 'West Indies', 'Pakistan', 'Sri Lanka']
    
    players = []
    for i in range(100):
        skill_rating = random.randint(50, 95)
        player = {
            'id': str(uuid.uuid4()),
            'name': f"Player {i+1}",
            'role': random.choice(player_roles),
            'country': random.choice(player_countries),
            'base_price': random.choice([0.5, 0.75, 1.0, 1.5, 2.0]),
            'overall_rating': skill_rating,
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

def set_stage(stage):
    st.session_state.app_stage = stage

def organize_players_by_role(players):
    batches = {
        'Batsman': [],
        'Bowler': [],
        'All-rounder': [],
        'Wicket-keeper': []
    }
    
    for player in players:
        role = player['role']
        if role.lower() == 'batsman' or role.lower() == 'batter':
            batches['Batsman'].append(player)
        elif role.lower() == 'bowler':
            batches['Bowler'].append(player)
        elif role.lower() == 'all-rounder' or role.lower() == 'allrounder':
            batches['All-rounder'].append(player)
        elif role.lower() == 'wicket-keeper' or role.lower() == 'wicketkeeper' or role.lower() == 'keeper':
            batches['Wicket-keeper'].append(player)
        else:
            batches['All-rounder'].append(player)
    return batches

def check_auction_complete():
    if not st.session_state.remaining_players:
        next_batch = None
        found_current = False
        
        for role in st.session_state.auction_order:
            if role == st.session_state.current_batch:
                found_current = True
                continue
            if found_current and role in st.session_state.player_batches and st.session_state.player_batches[role]:
                next_batch = role
                break
        
        if not next_batch:
            for role in st.session_state.auction_order:
                if role in st.session_state.player_batches and st.session_state.player_batches[role]:
                    next_batch = role
                    break
        
        if next_batch:
            st.session_state.remaining_players = st.session_state.player_batches[next_batch].copy()
            st.session_state.player_batches[next_batch] = []
            st.session_state.current_batch = next_batch
        else:
            eligible_teams = [t for t in st.session_state.teams if t['can_bid']]
            if not eligible_teams:
                st.session_state.auction_complete = True
                set_stage('results')

def proceed_to_next_batch():
    for player in st.session_state.remaining_players:
        player['status'] = 'unsold'
    st.session_state.remaining_players = []
    st.session_state.current_player = None
    check_auction_complete()

def end_auction_early():
    # Mark all remaining players across all batches as unsold
    for role in st.session_state.player_batches:
        for player in st.session_state.player_batches[role]:
            player['status'] = 'unsold'
    for player in st.session_state.remaining_players:
        player['status'] = 'unsold'
    
    st.session_state.auction_complete = True
    set_stage('results')

def setup_teams():
    st.title("🏏 Cricket Player Auction Simulator")
    
    uploaded_file = st.file_uploader("Upload Player Data CSV (Optional)", type=['csv'])
    if uploaded_file is not None:
        with open('uploaded_player_data.csv', 'wb') as f:
            f.write(uploaded_file.getbuffer())
    
    num_teams = st.number_input("Number of Teams", min_value=2, max_value=10, value=3, step=1)
    default_purse = st.number_input("Default Purse Amount per Team (₹ Cr)", min_value=5.0, max_value=100.0, value=90.0, step=0.5)
    
    with st.form("team_setup_form"):
        teams = []
        cols = st.columns(2)
        
        for i in range(num_teams):
            with cols[i % 2]:
                st.subheader(f"Team {i+1}")
                name = st.text_input(f"Team Name", value=f"Team {i+1}", key=f"team_name_{i}")
                purse = st.number_input(f"Purse Amount (₹ Cr)", min_value=5.0, max_value=100.0, value=default_purse, step=0.5, key=f"team_purse_{i}")
                
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
        
        st.subheader("Auction Settings")
        auction_order = st.multiselect(
            "Auction Order",
            ["Batsman", "Bowler", "All-rounder", "Wicket-keeper"],
            default=["Wicket-keeper", "Batsman", "Bowler", "All-rounder"]
        )
        
        submit_button = st.form_submit_button("Start Auction")
        
        if submit_button:
            st.session_state.teams = teams
            st.session_state.max_squad_size = max_squad_size
            
            if uploaded_file is not None:
                st.session_state.players = load_players_from_csv('uploaded_player_data.csv')
            else:
                try:
                    st.session_state.players = load_players_from_csv()
                except:
                    st.session_state.players = generate_sample_players()
            
            st.session_state.player_batches = organize_players_by_role(st.session_state.players)
            st.session_state.auction_order = auction_order if auction_order else ["Wicket-keeper", "Batsman", "Bowler", "All-rounder"]
            
            if st.session_state.auction_order and st.session_state.auction_order[0] in st.session_state.player_batches:
                first_role = st.session_state.auction_order[0]
                st.session_state.remaining_players = st.session_state.player_batches[first_role].copy()
                st.session_state.current_batch = first_role
            else:
                st.session_state.remaining_players = st.session_state.players.copy()
                st.session_state.current_batch = "All Players"
            
            set_stage('auction')
            st.rerun()
            st.rerun()

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
            'Price': f"₹{p.get('sold_price', 0)} Cr",
            'Batting Avg': p['stats']['batting_avg'],
            'Bowling Avg': p['stats']['bowling_avg']
        })
    
    st.dataframe(pd.DataFrame(player_data), use_container_width=True)
def live_team_dashboard():
    """Displays a live dashboard of player type distribution per team."""
    st.subheader("📊 Live Team Composition Dashboard")
    
    team_data = []
    for team in st.session_state.teams:
        role_counts = {
            'Team': team['name'],
            'Batsman': 0,
            'Bowler': 0,
            'All-rounder': 0,
            'Wicket-keeper': 0
        }
        
        for player in team['players']:
            role_counts[player['role']] += 1
        
        team_data.append(role_counts)
    
    df_team_roles = pd.DataFrame(team_data)
    
    if not df_team_roles.empty:
        st.dataframe(df_team_roles, use_container_width=True)
        st.bar_chart(df_team_roles.set_index('Team'))
    else:
        st.info("No players have been assigned to teams yet.")
def auction_screen():
    st.title("🏏 Cricket Player Auction")
    
    # Batch control buttons
    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("Proceed to Next Batch"):
            proceed_to_next_batch()
            st.rerun()
    with col2:
        if st.button("🚨 End Auction Early", help="Mark all remaining players as unsold and complete the auction"):
            end_auction_early()
            st.rerun()
    
    st.markdown(f"### Currently Auctioning: {st.session_state.current_batch}s")
    st.markdown(f"*{len(st.session_state.remaining_players)} players remaining in this batch*")
    
    cols = st.columns(len(st.session_state.teams))
    for i, team in enumerate(st.session_state.teams):
        with cols[i]:
            st.subheader(team['name'])
            st.metric("Remaining Purse", f"₹{team['purse']} Cr")
            st.metric("Players", len(team['players']))
            
            if st.expander(f"View {team['name']} Squad"):
                view_team_players(team)
            
            if len(team['players']) >= st.session_state.max_squad_size:
                team['can_bid'] = False
                st.warning("Squad Full")
            
            if team['purse'] < 0.5:
                team['can_bid'] = False
                st.warning("Insufficient Funds")
    
    check_auction_complete()
    if st.session_state.auction_complete:
        st.rerun()
        st.rerun()
    
    if st.session_state.current_player is None and st.session_state.remaining_players:
        sorted_players = sorted(st.session_state.remaining_players, 
                               key=lambda x: (x['base_price'], x['stats']['skill_rating']), 
                               reverse=True)
        
        player_index = random.randint(0, min(9, len(sorted_players)-1))
        st.session_state.current_player = sorted_players[player_index]
        st.session_state.current_bid = st.session_state.current_player['base_price']
        st.session_state.current_team = None
        st.session_state.last_bidder = None
        
        st.session_state.remaining_players = [p for p in st.session_state.remaining_players 
                                              if p['id'] != st.session_state.current_player['id']]
    
    if st.session_state.current_player:
        st.markdown("---")
        player = st.session_state.current_player
    
        auction_col, bid_col = st.columns([3, 1])
    
        with auction_col:
            st.markdown(f"<h2 style='text-align: center; color: #ff4b4b;'>🎯 Now Auctioning:<span style='color:#FFD400; font-weight: bold;'>  {player['name']}</span></h2>", unsafe_allow_html=True)
            overall_rating = player.get('overall_rating', player['stats']['skill_rating'])
            st.markdown(f"<h3 style='text-align: center;'>Overall Rating: <span style='color: #7afbff;'>{overall_rating}</span> | Role:<span style='color:#7afbff; font-weight: bold;'> {player['role']} </span>| Country:<span style='color:#7afbff; font-weight: bold;'> {player['country']}</span></h3>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center; color: #007bff;'>Base Price: ₹{player['base_price']} Cr</h3>", unsafe_allow_html=True)
    
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
            st.markdown(f"<h1 style='text-align: center; color: #28a745;'>₹{st.session_state.current_bid} Cr</h1>", unsafe_allow_html=True)
            
            if st.session_state.current_team:
                team = next((t for t in st.session_state.teams if t['id'] == st.session_state.current_team), None)
                if team:
                    st.markdown(f"<h2 style='text-align: center; '>Current Bidder: <span style='color: #7afbff;'>{team['name']}</span></h2>", unsafe_allow_html=True)
                                                                                                        
        st.markdown("---")

        st.subheader("Place Your Bids")
        first_bid_made = st.session_state.current_team is not None
        
        for team in st.session_state.teams:
            can_bid = team['can_bid'] and team['id'] != st.session_state.last_bidder
            
            if first_bid_made:
                col1, col2, col3 = st.columns([2, 1, 1])
            else:
                col1, col2 = st.columns([2, 2])
            
            with col1:
                st.markdown(f"### {team['name']}")
                
                if not can_bid:
                    if team['id'] == st.session_state.last_bidder:
                        st.info("Waiting for another team to bid")
                    elif not team['can_bid']:
                        st.warning("Insufficient Funds" if team['purse'] < 0.5 else "Squad Full")
            
            if first_bid_made:
                half_cr_bid = round(st.session_state.current_bid + 0.5, 2)
                one_cr_bid = round(st.session_state.current_bid + 1.0, 2)
                
                can_bid_half_cr = can_bid and team['purse'] >= half_cr_bid
                can_bid_one_cr = can_bid and team['purse'] >= one_cr_bid
                
                with col2:
                    if can_bid_half_cr:
                        if st.button(f"Bid ₹{half_cr_bid} Cr (+0.5)", key=f"bid_half_{team['id']}"):
                            st.session_state.current_bid = half_cr_bid
                            st.session_state.current_team = team['id']
                            st.session_state.last_bidder = team['id']
                            st.rerun()
                            st.rerun()
                    else:
                        st.button(f"Bid ₹{half_cr_bid} Cr (+0.5)", disabled=True, key=f"disabled_half_{team['id']}")
                
                with col3:
                    if can_bid_one_cr:
                        if st.button(f"Bid ₹{one_cr_bid} Cr (+1.0)", key=f"bid_one_{team['id']}"):
                            st.session_state.current_bid = one_cr_bid
                            st.session_state.current_team = team['id']
                            st.session_state.last_bidder = team['id']
                            st.rerun()
                            st.rerun()
                    else:
                        st.button(f"Bid ₹{one_cr_bid} Cr (+1.0)", disabled=True, key=f"disabled_one_{team['id']}")
            else:
                base_price = player['base_price']
                can_bid_base = can_bid and team['purse'] >= base_price
                
                with col2:
                    if can_bid_base:
                        if st.button(f"Bid ₹{base_price} Cr (Base Price)", key=f"bid_base_{team['id']}"):
                            st.session_state.current_bid = base_price
                            st.session_state.current_team = team['id']
                            st.session_state.last_bidder = team['id']
                            st.rerun()
                            st.rerun()
                    else:
                        st.button(f"Bid ₹{base_price} Cr (Base Price)", disabled=True, key=f"disabled_base_{team['id']}")
        
        st.markdown("---")
        
        if not st.session_state.current_team:
            st.markdown(f"#### Starting bid: ₹{player['base_price']} Cr (Base Price)")
        
        action_col1, action_col2 = st.columns(2)
        
        with action_col1:
            if st.session_state.current_team:
                if st.button("SOLD! ⚡", key="sold_button"):
                    team = next((t for t in st.session_state.teams if t['id'] == st.session_state.current_team), None)
                    if team:
                        sold_player = dict(st.session_state.current_player)
                        sold_player.update({
                            'status': 'sold',
                            'sold_to': team['name'],
                            'sold_price': st.session_state.current_bid
                        })
                        
                        for idx, p in enumerate(st.session_state.players):
                            if p['id'] == sold_player['id']:
                                st.session_state.players[idx] = sold_player
                                break
                        
                        team['players'].append(sold_player)
                        team['purse'] -= st.session_state.current_bid
                        
                        st.success(f"{sold_player['name']} sold to {team['name']} for ₹{st.session_state.current_bid} Cr!")
                        time.sleep(1)
                        
                        st.session_state.current_player = None
                        st.rerun()
                        st.session_state.current_bid = 0
                        st.session_state.current_team = None
                        st.session_state.last_bidder = None
                        st.rerun()
            else:
                st.button("SOLD! ⚡", disabled=True)
        
        with action_col2:
            if not st.session_state.current_team:
                if st.button("Unsold ❌", key="unsold_button"):
                    player['status'] = 'unsold'
                    st.info(f"{player['name']} remains unsold.")
                    time.sleep(1)
                    
                    st.session_state.current_player = None
                    st.rerun()
                    st.session_state.current_bid = 0
                    st.session_state.current_team = None
                    st.session_state.last_bidder = None
                    st.rerun()
            else:
                st.button("Unsold ❌", key="unsold_button", disabled=True)
    
    st.markdown("---")
    st.subheader("Auction Progress")
    
    sold_players = [p for p in st.session_state.players if p['status'] == 'sold']
    unsold_players = [p for p in st.session_state.players if p['status'] == 'unsold']
    
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
    
    if st.checkbox("Show Transaction Log"):
        if sold_players:
            transactions = []
            for p in sold_players:
                transactions.append({
                    'Player': p['name'],
                    'Role': p['role'],
                    'Country': p['country'],
                    'Team': p.get('sold_to', ''),
                    'Price (Cr)': p.get('sold_price', 0),
                    'Skill Rating': p['stats']['skill_rating']
                })
            st.dataframe(pd.DataFrame(transactions).sort_values('Price (Cr)', ascending=False), use_container_width=True)
        else:
            st.info("No transactions yet.")
        
    live_team_dashboard()
def save_team_to_csv(team):
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
            'Price (Cr)': p.get('sold_price', 0),
            'Batting Avg': p['stats']['batting_avg'],
            'Bowling Avg': p['stats']['bowling_avg'],
            'Matches': p['stats']['matches_played']
        })
    
    df = pd.DataFrame(player_data)
    summary_df = pd.DataFrame([{
        'Name': f"TEAM SUMMARY: {team['name']}",
        'Role': '',
        'Country': '',
        'Price (Cr)': team['original_purse'] - team['purse'],
        'Batting Avg': '',
        'Bowling Avg': '',
        'Matches': ''
    }])
    
    result_df = pd.concat([df, summary_df])
    filename = team['name'].replace(" ", "_").replace("/", "_").replace("\\", "_")
    filepath = f"team_data/{filename}.csv"
    result_df.to_csv(filepath, index=False)
    return filepath

def results_screen():
    st.title("🏆 Auction Results")
    
    # Summary statistics
    total_spent = sum(t['original_purse'] - t['purse'] for t in st.session_state.teams)
    total_players = sum(len(t['players']) for t in st.session_state.teams)
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Players Bought", total_players)
    col2.metric("Total Amount Spent", f"₹{total_spent:.2f} Cr")
    
    if total_players > 0:
        col3.metric("Average Price", f"₹{total_spent/total_players:.2f} Cr")
    with col4:
        if sold_players := [p for p in st.session_state.players if p['status'] == 'sold']:
            highest_paid = max(sold_players, key=lambda x: x.get('sold_price', 0))
            st.metric("Highest Paid Player", f"{highest_paid['name']} (₹{highest_paid.get('sold_price', 0)} Cr)")
        else:
            st.metric("Highest Paid Player", "None")
    
    # Team tabs
    tabs = st.tabs([t['name'] for t in st.session_state.teams] + ["Complete Log"])
    for i, tab in enumerate(tabs[:len(st.session_state.teams)]):
        with tab:
            team = st.session_state.teams[i]
            st.subheader(f"{team['name']} Summary")
            
            cols = st.columns(4)
            cols[0].metric("Players Bought", len(team['players']))
            cols[1].metric("Total Spent", f"₹{team['original_purse'] - team['purse']:.2f} Cr")
            cols[2].metric("Remaining Purse", f"₹{team['purse']:.2f} Cr")
            cols[3].metric("Original Purse", f"₹{team['original_purse']:.2f} Cr")
            
            if team['players']:
                role_counts = pd.DataFrame({
                    'Role': [p['role'] for p in team['players']]
                }).value_counts().reset_index()
                role_counts.columns = ['Role', 'Count']
                st.bar_chart(role_counts.set_index('Role'))
                
                st.dataframe(pd.DataFrame([{
                    'Player': p['name'],
                    'Role': p['role'],
                    'Price': f"₹{p.get('sold_price', 0):.2f} Cr",
                    'Batting': p['stats']['batting_avg'],
                    'Bowling': p['stats']['bowling_avg']
                } for p in team['players']]), hide_index=True)
            else:
                st.info("No players purchased")
    
    # Complete transaction log
    with tabs[-1]:
        transactions = pd.DataFrame([{
            'Team': t['name'],
            'Player': p['name'],
            'Role': p['role'],
            'Price (₹ Cr)': p.get('sold_price', 0),
            'Batting Avg': p['stats']['batting_avg'],
            'Bowling Avg': p['stats']['bowling_avg']
        } for t in st.session_state.teams for p in t['players']])
        
        if not transactions.empty:
            st.dataframe(
                transactions.sort_values('Price (₹ Cr)', ascending=False),
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("No transactions recorded")
    st.markdown("---")
    st.subheader("Download Complete Auction Data")
    
    # Create in-memory Excel file
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # Create team sheets
        for team in st.session_state.teams:
            player_data = []
            for p in team['players']:
                player_data.append({
                    'Name': p['name'],
                    'Role': p['role'],
                    'Country': p['country'],
                    'Price (₹ Cr)': p.get('sold_price', 0),
                    'Batting Avg': p['stats']['batting_avg'],
                    'Bowling Avg': p['stats']['bowling_avg'],
                    'Matches': p['stats']['matches_played'],
                    'Skill Rating': p['stats']['skill_rating']
                })
            
            df = pd.DataFrame(player_data)
            # Add team summary
            summary_df = pd.DataFrame([{
                'Name': f"Total Spent: ₹{team['original_purse'] - team['purse']} Cr",
                'Role': f"Remaining Purse: ₹{team['purse']} Cr",
                'Country': f"Original Purse: ₹{team['original_purse']} Cr",
                'Price (₹ Cr)': f"Players Bought: {len(team['players'])}",
                'Batting Avg': '',
                'Bowling Avg': '',
                'Matches': '',
                'Skill Rating': ''
            }])
            
            # Combine player data and summary
            combined_df = pd.concat([df, summary_df])
            combined_df.to_excel(writer, sheet_name=team['name'][:31], index=False)  # Sheet name max 31 chars
        
        # Add complete transaction log sheet
        transactions = [{
            'Team': team['name'],
            'Player': player['name'],
            'Role': player['role'],
            'Price (₹ Cr)': player.get('sold_price', 0),
            'Batting Avg': player['stats']['batting_avg'],
            'Bowling Avg': player['stats']['bowling_avg']
        } for team in st.session_state.teams for player in team['players']]
        
        log_df = pd.DataFrame(transactions)
        log_df.to_excel(writer, sheet_name='Complete Log', index=False)

    # Create download button
    st.download_button(
        label="📥 Download All Teams Data (Excel)",
        data=output.getvalue(),
        file_name="Auction_Results.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def main():
    if st.session_state.app_stage == 'setup':
        setup_teams()
    elif st.session_state.app_stage == 'auction':
        auction_screen()
    elif st.session_state.app_stage == 'results':
        results_screen()

if __name__ == "__main__":
    main()