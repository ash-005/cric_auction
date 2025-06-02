import streamlit as st
import pandas as pd
import random
import time
import uuid
import os
import logging
from io import BytesIO
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import xlsxwriter
from datetime import datetime
import plotly.express as px

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
class AuctionStage(Enum):
    SETUP = "setup"
    AUCTION = "auction"
    RESULTS = "results"

class PlayerRole(Enum):
    BATSMAN = "Batsman"
    BOWLER = "Bowler"
    ALL_ROUNDER = "All-rounder"
    WICKET_KEEPER = "Wicket-keeper"

class PlayerStatus(Enum):
    UNSOLD = "unsold"
    SOLD = "sold"
    WITHDRAWN = "withdrawn"

# Data Classes
@dataclass
class PlayerStats:
    batting_avg: float
    bowling_avg: float
    matches_played: int
    skill_rating: int

@dataclass
class Player:
    id: str
    name: str
    role: str
    country: str
    base_price: float
    overall_rating: int
    stats: PlayerStats
    status: str = PlayerStatus.UNSOLD.value
    sold_to: Optional[str] = None
    sold_price: Optional[float] = None

@dataclass
class Team:
    id: str
    name: str
    purse: float
    original_purse: float
    players: List[Player]
    can_bid: bool = True
    
    def add_player(self, player: Player, price: float) -> bool:
        """Add a player to the team if affordable"""
        if self.purse >= price:
            player.sold_to = self.name
            player.sold_price = price
            player.status = PlayerStatus.SOLD.value
            self.players.append(player)
            self.purse -= price
            return True
        return False
    
    def get_role_count(self, role: str) -> int:
        """Get count of players by role"""
        return sum(1 for p in self.players if p.role == role)
    
    def total_spent(self) -> float:
        """Calculate total amount spent"""
        return self.original_purse - self.purse

class AuctionManager:
    """Main auction management class"""
    
    def __init__(self):
        self.teams: List[Team] = []
        self.players: List[Player] = []
        self.current_player: Optional[Player] = None
        self.current_bid: float = 0
        self.current_team_id: Optional[str] = None
        self.last_bidder_id: Optional[str] = None
        self.auction_complete: bool = False
        self.remaining_players: List[Player] = []
        self.player_batches: Dict[str, List[Player]] = {}
        self.current_batch: str = ""
        self.auction_order: List[str] = []
        self.max_squad_size: int = 15
        self.bid_history: List[Dict] = []
    
    def organize_players_by_role(self) -> Dict[str, List[Player]:
        """Organize players into role-based batches"""
        batches = {role.value: [] for role in PlayerRole}
        
        for player in self.players:
            role_key = self._normalize_role(player.role)
            if role_key in batches:
                batches[role_key].append(player)
            else:
                batches[PlayerRole.ALL_ROUNDER.value].append(player)
        
        return batches
    
    def _normalize_role(self, role: str) -> str:
        """Normalize role names to standard format"""
        role_lower = role.lower().strip()
        
        if role_lower in ['batsman', 'batter']:
            return PlayerRole.BATSMAN.value
        elif role_lower in ['bowler']:
            return PlayerRole.BOWLER.value
        elif role_lower in ['all-rounder', 'allrounder', 'all rounder']:
            return PlayerRole.ALL_ROUNDER.value
        elif role_lower in ['wicket-keeper', 'wicketkeeper', 'keeper']:
            return PlayerRole.WICKET_KEEPER.value
        else:
            return PlayerRole.ALL_ROUNDER.value
    
    def get_next_player(self) -> Optional[Player]:
        """Get the next player for auction using intelligent selection"""
        if not self.remaining_players:
            return None
        
        # Sort by base price and skill rating for better auction flow
        sorted_players = sorted(
            self.remaining_players, 
            key=lambda x: (x.base_price, x.stats.skill_rating), 
            reverse=True
        )
        
        # Select from top 10 players to add some randomness
        max_index = min(9, len(sorted_players) - 1)
        player_index = random.randint(0, max_index)
        selected_player = sorted_players[player_index]
        
        # Remove from remaining players
        self.remaining_players = [
            p for p in self.remaining_players 
            if p.id != selected_player.id
        ]
        
        return selected_player
    
    def place_bid(self, team_id: str, bid_amount: float) -> bool:
        """Place a bid for the current player"""
        team = self.get_team_by_id(team_id)
        if not team or not self.can_team_bid(team, bid_amount):
            return False
        
        self.current_bid = bid_amount
        self.current_team_id = team_id
        self.last_bidder_id = team_id
        
        # Record bid in history
        self.bid_history.append({
            'timestamp': datetime.now(),
            'team': team.name,
            'player': self.current_player.name if self.current_player else '',
            'bid_amount': bid_amount
        })
        
        return True
    
    def can_team_bid(self, team: Team, bid_amount: float) -> bool:
        """Check if team can place a bid"""
        return (team.can_bid and 
                team.purse >= bid_amount and 
                len(team.players) < self.max_squad_size and
                team.id != self.last_bidder_id)
    
    def sell_player(self) -> bool:
        """Sell current player to highest bidder"""
        if not self.current_player or not self.current_team_id:
            return False
        
        team = self.get_team_by_id(self.current_team_id)
        if not team:
            return False
        
        success = team.add_player(self.current_player, self.current_bid)
        if success:
            # Update team bid eligibility
            self._update_team_eligibility()
            self._reset_current_auction()
            return True
        
        return False
    
    def mark_unsold(self) -> None:
        """Mark current player as unsold"""
        if self.current_player:
            self.current_player.status = PlayerStatus.UNSOLD.value
            self._reset_current_auction()
    
    def _reset_current_auction(self) -> None:
        """Reset current auction state"""
        self.current_player = None
        self.current_bid = 0
        self.current_team_id = None
        self.last_bidder_id = None
    
    def _update_team_eligibility(self) -> None:
        """Update team bidding eligibility based on purse and squad size"""
        for team in self.teams:
            team.can_bid = (
                team.purse >= 0.5 and 
                len(team.players) < self.max_squad_size
            )
    
    def get_team_by_id(self, team_id: str) -> Optional[Team]:
        """Get team by ID"""
        return next((t for t in self.teams if t.id == team_id), None)
    
    def proceed_to_next_batch(self) -> bool:
        """Move to next batch of players"""
        # Mark remaining players as unsold
        for player in self.remaining_players:
            player.status = PlayerStatus.UNSOLD.value
        
        self.remaining_players = []
        self._reset_current_auction()
        
        # Find next batch
        next_batch = self._get_next_batch()
        if next_batch:
            self.remaining_players = self.player_batches[next_batch].copy()
            self.player_batches[next_batch] = []
            self.current_batch = next_batch
            return True
        
        # No more batches, check if auction is complete
        eligible_teams = [t for t in self.teams if t.can_bid]
        if not eligible_teams:
            self.auction_complete = True
        
        return False
    
    def _get_next_batch(self) -> Optional[str]:
        """Get the next batch to auction"""
        for role in self.auction_order:
            if role in self.player_batches and self.player_batches[role]:
                return role
        return None
    
    def end_auction_early(self) -> None:
        """End auction early and mark all remaining players as unsold"""
        # Mark all remaining players as unsold
        for role in self.player_batches:
            for player in self.player_batches[role]:
                player.status = PlayerStatus.UNSOLD.value
        
        for player in self.remaining_players:
            player.status = PlayerStatus.UNSOLD.value
        
        self.auction_complete = True
    
    def get_auction_stats(self) -> Dict:
        """Get comprehensive auction statistics"""
        sold_players = [p for p in self.players if p.status == PlayerStatus.SOLD.value]
        unsold_players = [p for p in self.players if p.status == PlayerStatus.UNSOLD.value]
        
        total_spent = sum(t.total_spent() for t in self.teams)
        
        stats = {
            'total_players': len(self.players),
            'sold_players': len(sold_players),
            'unsold_players': len(unsold_players),
            'total_spent': total_spent,
            'average_price': total_spent / len(sold_players) if sold_players else 0,
            'highest_paid': max(sold_players, key=lambda x: x.sold_price or 0) if sold_players else None,
            'remaining_in_batch': len(self.remaining_players),
            'total_remaining': len(self.remaining_players) + sum(len(batch) for batch in self.player_batches.values())
        }
        
        return stats

class DataManager:
    """Handle data loading and saving operations"""
    
    @staticmethod
    def load_players_from_csv(file_path: str = 'player_list_complete.csv') -> List[Player]:
        """Load players from CSV file with robust error handling"""
        try:
            df = pd.read_csv(file_path)
            required_cols = ['Name', 'Overall', 'Role', 'Nationality']
            missing_cols = [col for col in required_cols if col not in df.columns]
            
            if missing_cols:
                logger.warning(f"Missing columns in CSV: {missing_cols}")
                return DataManager.generate_sample_players()
            
            players = []
            for _, row in df.iterrows():
                try:
                    player = DataManager._create_player_from_row(row)
                    players.append(player)
                except Exception as e:
                    logger.error(f"Error processing player {row.get('Name', 'Unknown')}: {e}")
                    continue
            
            logger.info(f"Successfully loaded {len(players)} players from CSV")
            return players
            
        except FileNotFoundError:
            logger.warning(f"CSV file {file_path} not found, generating sample players")
            return DataManager.generate_sample_players()
        except Exception as e:
            logger.error(f"Error loading CSV: {e}")
            return DataManager.generate_sample_players()
    
    @staticmethod
    def _create_player_from_row(row) -> Player:
        """Create a Player object from CSV row"""
        skill = float(row['Overall'])
        role = row['Role'].strip()
        
        # Calculate stats based on role and skill
        if role.lower() in ['batsman', 'wicket-keeper', 'batter', 'wicketkeeper']:
            batting_avg = skill * 0.5 + random.uniform(15, 25)
            bowling_avg = (100 - skill) * 0.3 + random.uniform(25, 40)
        elif role.lower() in ['bowler']:
            batting_avg = skill * 0.2 + random.uniform(10, 20)
            bowling_avg = (100 - skill) * 0.4 + random.uniform(15, 25)
        else:  # All-rounder
            batting_avg = skill * 0.4 + random.uniform(15, 20)
            bowling_avg = (100 - skill) * 0.35 + random.uniform(20, 30)
        
        matches_played = int(skill + random.randint(5, 50))
        base_price = DataManager._calculate_base_price(skill)
        
        stats = PlayerStats(
            batting_avg=int(batting_avg),
            bowling_avg=int(bowling_avg),
            matches_played=matches_played,
            skill_rating=int(skill)
        )
        
        return Player(
            id=str(uuid.uuid4()),
            name=row['Name'],
            role=AuctionManager()._normalize_role(role),
            country=row['Nationality'],
            base_price=base_price,
            overall_rating=int(skill),
            stats=stats
        )
    
    @staticmethod
    def _calculate_base_price(skill: float) -> float:
        """Calculate base price based on skill rating"""
        if skill >= 91:
            return random.choice([4.5, 5.0])
        elif skill >= 86:
            return random.choice([3.0, 3.5, 4.0])
        elif skill >= 77:
            return random.choice([1.5, 2.0, 2.5])
        elif skill >= 70:
            return random.choice([1.0, 1.5])
        elif skill >= 60:
            return 1.0
        else:
            return 0.5
    
    @staticmethod
    def generate_sample_players(count: int = 100) -> List[Player]:
        """Generate sample players for testing"""
        roles = [role.value for role in PlayerRole]
        countries = ['India', 'Australia', 'England', 'New Zealand', 'South Africa', 'West Indies', 'Pakistan', 'Sri Lanka']
        
        players = []
        for i in range(count):
            skill_rating = random.randint(50, 95)
            base_price = DataManager._calculate_base_price(skill_rating)
            
            stats = PlayerStats(
                batting_avg=random.randint(20, 60),
                bowling_avg=random.randint(18, 40),
                matches_played=random.randint(10, 200),
                skill_rating=skill_rating
            )
            
            player = Player(
                id=str(uuid.uuid4()),
                name=f"Player {i+1}",
                role=random.choice(roles),
                country=random.choice(countries),
                base_price=base_price,
                overall_rating=skill_rating,
                stats=stats
            )
            players.append(player)
        
        logger.info(f"Generated {count} sample players")
        return players
    
    @staticmethod
    def export_to_excel(auction_manager: AuctionManager) -> BytesIO:
        """Export auction results to Excel file"""
        output = BytesIO()
        
        try:
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                # Create team sheets
                for team in auction_manager.teams:
                    if team.players:
                        team_data = []
                        for player in team.players:
                            team_data.append({
                                'Name': player.name,
                                'Role': player.role,
                                'Country': player.country,
                                'Price (₹ Cr)': player.sold_price or 0,
                                'Batting Avg': player.stats.batting_avg,
                                'Bowling Avg': player.stats.bowling_avg,
                                'Matches': player.stats.matches_played,
                                'Skill Rating': player.stats.skill_rating
                            })
                        
                        df = pd.DataFrame(team_data)
                        
                        # Add summary row
                        summary_data = {
                            'Name': f"SUMMARY - {team.name}",
                            'Role': f"Total Spent: ₹{team.total_spent():.1f} Cr",
                            'Country': f"Remaining: ₹{team.purse:.1f} Cr",
                            'Price (₹ Cr)': f"Players: {len(team.players)}",
                            'Batting Avg': '',
                            'Bowling Avg': '',
                            'Matches': '',
                            'Skill Rating': ''
                        }
                        
                        summary_df = pd.DataFrame([summary_data])
                        combined_df = pd.concat([df, summary_df], ignore_index=True)
                        
                        # Sanitize sheet name
                        sheet_name = team.name.replace('/', '_').replace('\\', '_')[:31]
                        combined_df.to_excel(writer, sheet_name=sheet_name, index=False)
                
                # Complete transaction log
                transactions = []
                for team in auction_manager.teams:
                    for player in team.players:
                        transactions.append({
                            'Team': team.name,
                            'Player': player.name,
                            'Role': player.role,
                            'Country': player.country,
                            'Price (₹ Cr)': player.sold_price or 0,
                            'Batting Avg': player.stats.batting_avg,
                            'Bowling Avg': player.stats.bowling_avg,
                            'Skill Rating': player.stats.skill_rating
                        })
                
                if transactions:
                    log_df = pd.DataFrame(transactions)
                    log_df.to_excel(writer, sheet_name='Complete Log', index=False)
                
                # Auction summary
                stats = auction_manager.get_auction_stats()
                summary_data = {
                    'Metric': ['Total Players', 'Players Sold', 'Players Unsold', 'Total Spent (₹ Cr)', 'Average Price (₹ Cr)'],
                    'Value': [stats['total_players'], stats['sold_players'], stats['unsold_players'], 
                             f"{stats['total_spent']:.1f}", f"{stats['average_price']:.1f}"]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Auction Summary', index=False)
            
            logger.info("Excel export completed successfully")
            return output
            
        except Exception as e:
            logger.error(f"Error exporting to Excel: {e}")
            raise

# Streamlit App
def initialize_session_state():
    """Initialize Streamlit session state"""
    if 'auction_manager' not in st.session_state:
        st.session_state.auction_manager = AuctionManager()
    if 'app_stage' not in st.session_state:
        st.session_state.app_stage = AuctionStage.SETUP.value

def setup_page():
    """Setup page for configuring teams and auction parameters"""
    st.title("🏏 Professional Cricket Auction Simulator")
    st.markdown("### Configure Your Auction")
    
    manager = st.session_state.auction_manager
    
    # File upload
    with st.expander("📁 Upload Player Data (Optional)", expanded=False):
        uploaded_file = st.file_uploader(
            "Upload CSV file with columns: Name, Overall, Role, Nationality", 
            type=['csv'],
            help="If no file is uploaded, sample players will be generated"
        )
        
        if uploaded_file is not None:
            try:
                # Save uploaded file temporarily
                with open('temp_player_data.csv', 'wb') as f:
                    f.write(uploaded_file.getbuffer())
                st.success("✅ Player data uploaded successfully!")
            except Exception as e:
                st.error(f"Error uploading file: {e}")
    
    # Team configuration
    st.subheader("🏆 Team Configuration")
    
    col1, col2 = st.columns(2)
    with col1:
        num_teams = st.number_input("Number of Teams", min_value=2, max_value=10, value=4, step=1)
        default_purse = st.number_input("Default Purse per Team (₹ Cr)", min_value=10.0, max_value=200.0, value=90.0, step=5.0)
    
    with col2:
        max_squad_size = st.number_input("Maximum Squad Size", min_value=11, max_value=25, value=15, step=1)
        min_bid_increment = st.number_input("Minimum Bid Increment (₹ Cr)", min_value=0.1, max_value=2.0, value=0.5, step=0.1)
    
    # Team details
    st.subheader("Team Details")
    teams = []
    
    # Use columns for better layout
    cols = st.columns(min(3, num_teams))
    
    for i in range(num_teams):
        with cols[i % len(cols)]:
            with st.container():
                st.markdown(f"**Team {i+1}**")
                name = st.text_input(f"Name", value=f"Team {i+1}", key=f"team_name_{i}")
                purse = st.number_input(f"Purse (₹ Cr)", min_value=10.0, max_value=200.0, value=default_purse, step=5.0, key=f"team_purse_{i}")
                
                team = Team(
                    id=str(uuid.uuid4()),
                    name=name,
                    purse=purse,
                    original_purse=purse,
                    players=[]
                )
                teams.append(team)
    
    # Auction settings
    st.subheader("⚙️ Auction Settings")
    
    col1, col2 = st.columns(2)
    with col1:
        auction_order = st.multiselect(
            "Auction Order (Role-wise batches)",
            [role.value for role in PlayerRole],
            default=[PlayerRole.WICKET_KEEPER.value, PlayerRole.BATSMAN.value, PlayerRole.BOWLER.value, PlayerRole.ALL_ROUNDER.value],
            help="Order in which player roles will be auctioned"
        )
    
    with col2:
        shuffle_batches = st.checkbox("Shuffle players within batches", value=True, help="Randomize player order within each role batch")
    
    # Start auction button
    if st.button("🚀 Start Auction", type="primary", use_container_width=True):
        if len(set(team.name for team in teams)) != len(teams):
            st.error("❌ Team names must be unique!")
            return
        
        if not auction_order:
            st.error("❌ Please select at least one role for auction order!")
            return
        
        try:
            # Load players
            if uploaded_file is not None:
                players = DataManager.load_players_from_csv('temp_player_data.csv')
                # Clean up temp file
                if os.path.exists('temp_player_data.csv'):
                    os.remove('temp_player_data.csv')
            else:
                players = DataManager.load_players_from_csv()
            
            # Initialize auction manager
            manager.teams = teams
            manager.players = players
            manager.max_squad_size = max_squad_size
            manager.auction_order = auction_order
            manager.player_batches = manager.organize_players_by_role()
            
            # Shuffle batches if requested
            if shuffle_batches:
                for batch in manager.player_batches.values():
                    random.shuffle(batch)
            
            # Start with first batch
            if auction_order and auction_order[0] in manager.player_batches:
                first_role = auction_order[0]
                manager.remaining_players = manager.player_batches[first_role].copy()
                manager.player_batches[first_role] = []
                manager.current_batch = first_role
            
            st.session_state.app_stage = AuctionStage.AUCTION.value
            st.success("🎉 Auction setup complete! Redirecting to auction...")
            time.sleep(1)
            st.rerun()
            
        except Exception as e:
            st.error(f"❌ Error starting auction: {e}")
            logger.error(f"Auction start error: {e}")

def auction_page():
    """Main auction page"""
    manager = st.session_state.auction_manager
    
    st.title("🏏 Live Cricket Auction")
    
    # Control buttons
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        if st.button("➡️ Next Batch", help="Move to next role batch"):
            if manager.proceed_to_next_batch():
                st.rerun()
            else:
                st.session_state.app_stage = AuctionStage.RESULTS.value
                st.rerun()
    
    with col2:
        if st.button("🏁 End Auction", type="secondary", help="End auction early"):
            manager.end_auction_early()
            st.session_state.app_stage = AuctionStage.RESULTS.value
            st.rerun()
    
    # Current batch info
    stats = manager.get_auction_stats()
    st.markdown(f"### Currently Auctioning: **{manager.current_batch}**")
    
    # Progress metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Remaining in Batch", stats['remaining_in_batch'])
    col2.metric("Total Remaining", stats['total_remaining'])
    col3.metric("Players Sold", stats['sold_players'])
    col4.metric("Total Spent", f"₹{stats['total_spent']:.1f} Cr")
    
    # Team dashboard
    display_team_dashboard(manager)
    
    # Current player auction
    if manager.current_player is None and manager.remaining_players:
        manager.current_player = manager.get_next_player()
        manager.current_bid = manager.current_player.base_price if manager.current_player else 0
        manager.current_team_id = None
        manager.last_bidder_id = None
    
    if manager.current_player:
        display_current_auction(manager)
    elif not manager.remaining_players:
        st.info("🏁 Current batch completed! Use 'Next Batch' to continue or 'End Auction' to finish.")
    
    # Live statistics
    display_live_stats(manager)

def display_team_dashboard(manager: AuctionManager):
    """Display team dashboard with current status"""
    st.subheader("🏆 Team Dashboard")
    
    cols = st.columns(len(manager.teams))
    for i, team in enumerate(manager.teams):
        with cols[i]:
            # Team status
            status_color = "🟢" if team.can_bid else "🔴"
            st.markdown(f"### {status_color} {team.name}")
            
            # Metrics
            col1, col2 = st.columns(2)
            col1.metric("Purse", f"₹{team.purse:.1f} Cr")
            col2.metric("Players", len(team.players))
            
            # Squad composition
            if team.players:
                st.markdown("**Squad:**")
                squad_data = []
                for player in team.players:
                    squad_data.append({
                        'Name': player.name,
                        'Role': player.role
                    })
                df = pd.DataFrame(squad_data)
                st.dataframe(df, use_container_width=True, hide_index=True, height=200)
            
            # Status indicators
            if not team.can_bid:
                if team.purse < 0.5:
                    st.warning("💰 Low Funds")
                if len(team.players) >= manager.max_squad_size:
                    st.warning("👥 Squad Full")

def display_current_auction(manager: AuctionManager):
    """Display current player auction interface"""
    player = manager.current_player
    
    st.markdown("---")
    st.markdown(f"## 🎯 Now Auctioning: **{player.name}**")
    
    # Player details
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # Player info with enhanced styling
        st.markdown(f"""
        <div style="background-color: #1e2530; padding: 1rem; border-radius: 0.5rem; margin-bottom: 1rem;">
            <h2 style="color: #1f77b4; margin: 0;">Player Information</h2>
            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; margin-top: 1rem;">
                <div>
                    <h3 style="color: #fafafa; font-size: 1.5rem; margin: 0;">Role</h3>
                    <p style="color: #1f77b4; font-size: 1.8rem; font-weight: bold; margin: 0.5rem 0;">{player.role}</p>
                </div>
                <div>
                    <h3 style="color: #fafafa; font-size: 1.5rem; margin: 0;">Country</h3>
                    <p style="color: #1f77b4; font-size: 1.8rem; font-weight: bold; margin: 0.5rem 0;">{player.country}</p>
                </div>
                <div>
                    <h3 style="color: #fafafa; font-size: 1.5rem; margin: 0;">Overall Rating</h3>
                    <p style="color: #1f77b4; font-size: 1.8rem; font-weight: bold; margin: 0.5rem 0;">{player.overall_rating}</p>
                </div>
                <div>
                    <h3 style="color: #fafafa; font-size: 1.5rem; margin: 0;">Base Price</h3>
                    <p style="color: #1f77b4; font-size: 1.8rem; font-weight: bold; margin: 0.5rem 0;">₹{player.base_price} Cr</p>
                </div>
            </div>
        </div>
        """
        , unsafe_allow_html=True)
        
    with col2:
        # Current bid display
        st.markdown("### Current Bid")
        st.markdown(f"# ₹{manager.current_bid:.1f} Cr")
        
        if manager.current_team_id:
            current_team = manager.get_team_by_id(manager.current_team_id)
            if current_team:
                st.markdown(f"""
                <div style="background: linear-gradient(90deg, #1f77b4, #2d91d1); 
                     padding: 1rem; border-radius: 0.5rem; margin-top: 0.5rem;">
                    <p style="color: white; font-size: 1.2rem; margin: 0;">Current Bidder</p>
                    <h2 style="color: white; font-size: 2rem; margin: 0.5rem 0; text-align: center;">
                        {current_team.name}
                    </h2>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background-color: #1e2530; padding: 1rem; border-radius: 0.5rem; margin-top: 0.5rem;">
                <p style="color: #fafafa; font-size: 1.2rem; margin: 0; text-align: center;">
                    Awaiting first bid
                </p>
            </div>
            """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Bidding interface
    st.subheader("💰 Place Your Bids")
    
    has_bids = manager.current_team_id is not None
    first_bid = manager.current_team_id is None

    # Create bid buttons for each team
    for team in manager.teams:
        # For first bid, only allow base price, and only if team can bid at base price
        if first_bid:
            if not manager.can_team_bid(team, player.base_price):
                continue
            columns = st.columns([2, 2])
            with columns[0]:
                st.markdown(f"**{team.name}** - ₹{team.purse:.1f} Cr remaining")
            with columns[1]:
                if st.button(f"₹{player.base_price:.1f} Cr (First Bid)", key=f"first_bid_{team.id}", use_container_width=True):
                    if manager.place_bid(team.id, player.base_price):
                        st.rerun()
        else:
            # For subsequent bids, allow increments above current bid
            if not manager.can_team_bid(team, manager.current_bid + 0.5):
                continue
            columns = st.columns([2, 1, 1])
            with columns[0]:
                st.markdown(f"**{team.name}** - ₹{team.purse:.1f} Cr remaining")
            with columns[1]:
                next_bid = manager.current_bid + 0.5
                if st.button(f"₹{next_bid:.1f} Cr", key=f"quick_bid_{team.id}", use_container_width=True):
                    if manager.place_bid(team.id, next_bid):
                        st.rerun()
            with columns[2]:
                custom_bid = st.number_input(
                    "Custom", 
                    min_value=manager.current_bid + 0.5,
                    max_value=min(team.purse, 50.0),
                    value=manager.current_bid + 1.0,
                    step=0.5,
                    key=f"custom_bid_{team.id}"
                )
                if st.button("Bid", key=f"custom_bid_btn_{team.id}", use_container_width=True):
                    if manager.place_bid(team.id, custom_bid):
                        st.rerun()
    
    # Action buttons
    st.markdown("---")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("✅ SOLD!", type="primary", use_container_width=True):
            # First make sure we have a valid team before trying to sell
            if not manager.current_team_id:
                st.error("❌ No team selected! Please make sure a team has placed a bid.")
            else:
                winning_team = manager.get_team_by_id(manager.current_team_id)
                if winning_team and manager.sell_player():
                    st.success(f"🎉 {player.name} sold to {winning_team.name} for ₹{manager.current_bid:.1f} Cr!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("❌ Error selling player!")
    
    with col2:
        # Only show UNSOLD button if no bids have been placed
        if not manager.current_team_id:
            if st.button("❌ UNSOLD", type="secondary", use_container_width=True):
                manager.mark_unsold()
                st.info(f"❌ {player.name} marked as unsold")
                time.sleep(1)
                st.rerun()
        else:
            # Show disabled button style when bids exist
            st.markdown("""
            <button class="stButton disabled" disabled style="width:100%; opacity:0.5; cursor:not-allowed">
                ❌ UNSOLD
            </button>
            """, unsafe_allow_html=True)

def display_live_stats(manager: AuctionManager):
    """Display live auction statistics"""
    st.markdown("---")
    st.subheader("📊 Live Statistics")
    
    stats = manager.get_auction_stats()
    
    # Overall progress
    col1, col2, col3 = st.columns(3)
    col1.metric("Auction Progress", f"{stats['sold_players']}/{stats['total_players']}")
    col2.metric("Total Revenue", f"₹{stats['total_spent']:.1f} Cr")
    col3.metric("Average Price", f"₹{stats['average_price']:.1f} Cr")
    
    # Most expensive player
    if stats['highest_paid']:
        st.info(f"💰 Most Expensive: {stats['highest_paid'].name} - ₹{stats['highest_paid'].sold_price:.1f} Cr")
    
    # Team spending comparison
    if any(team.players for team in manager.teams):
        st.subheader("💸 Team Spending")
        
        spending_data = []
        for team in manager.teams:
            spending_data.append({
                'Team': team.name,
                'Spent': team.total_spent(),
                'Remaining': team.purse,
                'Players': len(team.players)
            })
        
        df = pd.DataFrame(spending_data)
        st.bar_chart(df.set_index('Team')['Spent'])
        
        # Detailed spending table
        with st.expander("📋 Detailed Team Analysis"):
            st.dataframe(df, use_container_width=True)

def results_page():
    """Results and export page"""
    manager = st.session_state.auction_manager
    
    st.title("🏆 Auction Results")
    st.markdown("### Final Results and Export Options")
    
    # Final statistics
    stats = manager.get_auction_stats()
    
    # Updated metrics - removed total players, made highest paid more prominent
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Amount Spent", f"₹{stats['total_spent']:.1f} Cr")
    col2.metric("Average Price", f"₹{stats['average_price']:.1f} Cr")
    if stats['highest_paid']:
        col3.metric("Most Expensive Player", f"{stats['highest_paid'].name}", f"₹{stats['highest_paid'].sold_price:.1f} Cr")
    
    # Auction timeline
    if manager.bid_history:
        st.markdown("### 📈 Auction Timeline")
        timeline_df = pd.DataFrame(manager.bid_history)
        timeline_df['bid_number'] = range(1, len(timeline_df) + 1)
        
        fig = px.line(timeline_df, 
                     x='bid_number', 
                     y='bid_amount',
                     hover_data=['team', 'player'],
                     title='Auction Progress - Bid Amount Timeline',
                     labels={'bid_number': 'Bid Number', 'bid_amount': 'Bid Amount (₹ Cr)'})
        st.plotly_chart(fig, use_container_width=True, key="auction_timeline")
    
    # Team-wise results with enhanced visualization
    st.subheader("🏆 Final Team Squads")
    
    for team in manager.teams:
        with st.expander(f"{team.name} - {len(team.players)} players, ₹{team.total_spent():.1f} Cr spent"):
            if team.players:
                # Team summary
                col1, col2, col3 = st.columns(3)
                col1.metric("Total Spent", f"₹{team.total_spent():.1f} Cr")
                col2.metric("Remaining Purse", f"₹{team.purse:.1f} Cr")
                col3.metric("Squad Size", len(team.players))
                
                # Enhanced team composition visualization
                st.markdown("### Team Composition")
                viz_col1, viz_col2 = st.columns(2)
                
                with viz_col1:
                    # Role distribution pie chart
                    role_counts = {}
                    for role in PlayerRole:
                        count = team.get_role_count(role.value)
                        if count > 0:
                            role_counts[role.value] = count
                    
                    fig = px.pie(
                        values=list(role_counts.values()),
                        names=list(role_counts.keys()),
                        title='Squad Role Distribution'
                    )
                    st.plotly_chart(fig, use_container_width=True, key=f"pie_chart_{team.id}")
                
                with viz_col2:
                    # Price distribution by role
                    price_data = []
                    for player in team.players:
                        price_data.append({
                            'Player': player.name,
                            'Role': player.role,
                            'Price': player.sold_price or 0
                        })
                    
                    if price_data:
                        df = pd.DataFrame(price_data)
                        fig = px.bar(
                            df,
                            x='Player',
                            y='Price',
                            color='Role',
                            title='Player Price Distribution'
                        )
                        fig.update_layout(xaxis_tickangle=-45)
                        st.plotly_chart(fig, use_container_width=True, key=f"bar_chart_{team.id}")
                
                # Player list
                team_data = []
                for player in sorted(team.players, key=lambda x: x.sold_price or 0, reverse=True):
                    team_data.append({
                        'Name': player.name,
                        'Role': player.role,
                        'Country': player.country,
                        'Price (₹ Cr)': f"{player.sold_price:.1f}",
                        'Overall Rating': player.overall_rating,
                        'Batting Avg': player.stats.batting_avg,
                        'Bowling Avg': player.stats.bowling_avg
                    })
                
                if team_data:
                    df = pd.DataFrame(team_data)
                    st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No players purchased")
    
    # Unsold players
    unsold_players = [p for p in manager.players if p.status == PlayerStatus.UNSOLD.value]
    if unsold_players:
        with st.expander(f"❌ Unsold Players ({len(unsold_players)})"):
            unsold_data = []
            for player in sorted(unsold_players, key=lambda x: x.overall_rating, reverse=True):
                unsold_data.append({
                    'Name': player.name,
                    'Role': player.role,
                    'Country': player.country,
                    'Base Price (₹ Cr)': player.base_price,
                    'Overall Rating': player.overall_rating
                })
            
            df = pd.DataFrame(unsold_data)
            st.dataframe(df, use_container_width=True, hide_index=True)
    
    # Export options
    st.markdown("---")
    st.subheader("📤 Export Results")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📊 Download Excel Report", type="primary", use_container_width=True):
            try:
                excel_data = DataManager.export_to_excel(manager)
                st.download_button(
                    label="⬇️ Download Excel File",
                    data=excel_data.getvalue(),
                    file_name=f"cricket_auction_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
                st.success("✅ Excel report generated successfully!")
            except Exception as e:
                st.error(f"❌ Error generating Excel report: {e}")
    
    with col2:
        if st.button("🔄 New Auction", use_container_width=True):
            # Reset session state
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
    
    # Auction timeline
    if manager.bid_history:
        st.subheader("📈 Auction Timeline")
        with st.expander("View Bid History"):
            timeline_data = []
            for bid in manager.bid_history[-20:]:  # Show last 20 bids
                timeline_data.append({
                    'Time': bid['timestamp'].strftime('%H:%M:%S'),
                    'Team': bid['team'],
                    'Player': bid['player'],
                    'Bid Amount (₹ Cr)': bid['bid_amount']
                })
            
            if timeline_data:
                df = pd.DataFrame(timeline_data)
                st.dataframe(df, use_container_width=True, hide_index=True)

def main():
    """Main application function"""
    # Page configuration
    st.set_page_config(
        page_title="Cricket Auction Simulator",
        page_icon="🏏",
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    
    # Custom CSS for better styling
    st.markdown("""
    <style>
    .stApp {
        background-color: #0e1117;
        color: white;
    }
    
    .main > div {
        padding-top: 2rem;
    }
    
    .stMetric {
        background-color: #1e2530;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #1f77b4;
        color: white;
    }
    
    div[data-testid="stTable"] {
        background-color: #1e2530;
        border-radius: 0.5rem;
        padding: 1rem;
    }
    
    div.stDataFrame {
        background-color: #1e2530;
        border-radius: 0.5rem;
        padding: 1rem;
    }
    
    .stButton > button {
        width: 100%;
        border-radius: 0.5rem;
        font-weight: bold;
        background: linear-gradient(90deg, #1f77b4, #2d91d1);
        border: none !important;
        color: white;
    }

    .stButton > button:hover {
        background: linear-gradient(90deg, #2d91d1, #1f77b4);
        border: none !important;
        color: white;
    }
    
    .stExpander {
        border: 1px solid #2d3a4f;
        background-color: #1e2530;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
    }

    /* Tooltip styling */
    div[data-baseweb="tooltip"] {
        background-color: #1e2530 !important;
        color: white !important;
        border: 1px solid #2d3a4f !important;
    }
    
    .auction-header {
        background: linear-gradient(90deg, #1f77b4, #ff7f0e);
        padding: 1rem;
        border-radius: 0.5rem;
        color: white;
        text-align: center;
        margin-bottom: 2rem;
    }

    .stMarkdown {
        color: #fafafa;
    }

    div[data-baseweb="select"] {
        background-color: #1e2530;
    }

    div[data-baseweb="select"] > div {
        background-color: #1e2530;
        color: white;
    }

    .stNumberInput > div > div > input {
        background-color: #1e2530;
        color: white;
    }

    .stTextInput > div > div > input {
        background-color: #1e2530;
        color: white;
    }

    .stAlert {
        background-color: #1e2530;
        color: white;
    }
    
    .element-container .stTextInput input {
        background-color: #1e2530;
        color: white;
    }
    
    /* Player card styling */
    div[data-testid="column"] > div:first-child {
        background-color: #1e2530;
        padding: 1rem;
        border-radius: 0.5rem;
        border: 1px solid #2d3a4f;
        margin-bottom: 1rem;
    }

    /* Team dashboard card styling */
    div[data-testid="column"] div.stMarkdown > h3 {
        margin-top: 0;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid #2d3a4f;
    }

    /* Current bid display */
    div[data-testid="column"] div.stMarkdown > h1 {
        font-size: 2.5rem;
        color: #1f77b4;
        text-align: center;
        padding: 1rem;
        background-color: #1e2530;
        border-radius: 0.5rem;
        margin: 1rem 0;
        border: 1px solid #2d3a4f;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Initialize session state
    initialize_session_state()
    
    # Main app logic
    stage = st.session_state.app_stage
    
    if stage == AuctionStage.SETUP.value:
        setup_page()
    elif stage == AuctionStage.AUCTION.value:
        auction_page()
    elif stage == AuctionStage.RESULTS.value:
        results_page()
    else:
        st.error("Unknown application stage!")
        st.session_state.app_stage = AuctionStage.SETUP.value
        st.rerun()

if __name__ == "__main__":
    main()