# 🏏 Cricket Auction Simulator

A professional real-time cricket auction platform with advanced visualizations and team management features.

## ✨ Features

### 🎯 **Core Auction Features**
- **Real-time Bidding**: Synchronized 15-second timer with live bid updates
- **Skill-based Pricing**: Dynamic base prices based on player ratings
- **Team Management**: Complete squad composition tracking
- **Multiple Formats**: Support for different squad sizes and purse amounts

### 📊 **Advanced Visualizations**
- **Live Price Trends**: Real-time auction price progression charts
- **Role Distribution**: Dynamic team composition analysis
- **Spending Analytics**: Team budget utilization tracking
- **Market Insights**: Comprehensive auction statistics

### 🎨 **Modern UI/UX**
- **Spacious Design**: Clean, professional interface with generous spacing
- **Glass Morphism**: Modern design with backdrop blur effects
- **Responsive Layout**: Optimized for desktop and mobile devices
- **Dark Theme**: Professional dark theme with gradient accents

### 💾 **Export Options**
- **Multiple Formats**: JSON, CSV, and Excel export
- **Team Sheets**: Individual team composition exports
- **Statistics**: Comprehensive auction analytics

## 🚀 Deployment on Render

### **Method 1: Direct GitHub Deployment (Recommended)**

1. **Fork/Clone this repository** to your GitHub account

2. **Connect to Render**:
   - Go to [render.com](https://render.com) and sign up/login
   - Click "New +" → "Web Service"
   - Connect your GitHub repository

3. **Configure the service**:
   - **Name**: `cricket-auction-app` (or your preferred name)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT app:app`

4. **Environment Variables**:
   - `FLASK_ENV`: `production`
   - `PYTHON_VERSION`: `3.11.0`

5. **Deploy**: Click "Create Web Service"

### **Method 2: Manual Deployment**

1. **Prepare your files**:
   ```bash
   # Ensure you have all required files:
   # - app.py
   # - requirements.txt
   # - render.yaml
   # - Procfile
   # - templates/index.html
   ```

2. **Upload to GitHub** and follow Method 1

### **Method 3: Render Blueprint**

1. **Use the render.yaml file** included in this repository
2. **Deploy via Render Dashboard** using the Blueprint option

## 📋 Requirements

- Python 3.11+
- Flask 2.3.3
- Flask-SocketIO 5.3.6
- Gunicorn 21.2.0
- Eventlet 0.33.3

## 🎮 How to Use

### **1. Create an Auction Room**
- Enter your name and team name
- Upload a CSV file with player data (format: name, role, nationality, overall)
- Configure auction settings (max players, squad size, team purse)

### **2. Join Existing Room**
- Enter your name and team name
- Input the 6-character room code

### **3. Conduct Auction**
- Room owner starts the auction
- Players are presented with base prices based on skill ratings
- Bid using +₹0.5 Cr or +₹1 Cr increments
- 15-second timer resets with each new bid
- Track team compositions and spending in real-time

### **4. View Results**
- Comprehensive auction statistics
- Team-wise player distributions
- Export data in multiple formats

## 📊 CSV Format

Your player CSV should include these columns:
```csv
name,role,nationality,overall
Virat Kohli,Batsman,India,95
MS Dhoni,Wicket-keeper,India,85
Jasprit Bumrah,Bowler,India,94
Ravindra Jadeja,All-rounder,India,88
```

**Roles**: Batsman, Bowler, All-rounder, Wicket-keeper
**Overall**: Skill rating (0-100)

## 🔧 Technical Details

### **Architecture**
- **Backend**: Flask with SocketIO for real-time communication
- **Frontend**: Vanilla JavaScript with Chart.js for visualizations
- **Styling**: Tailwind CSS with custom glass morphism effects
- **Deployment**: Gunicorn with Eventlet workers for WebSocket support

### **Key Features**
- **Real-time Sync**: All clients stay synchronized during auctions
- **Scalable**: Supports multiple concurrent auction rooms
- **Responsive**: Works on desktop, tablet, and mobile devices
- **Professional**: Enterprise-grade UI/UX design

## 🌐 Live Demo

Once deployed on Render, your application will be available at:
`https://your-app-name.onrender.com`

## 🛠️ Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py

# Access at http://localhost:5000
```

## 📝 License

This project is open source and available under the MIT License.

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!

---

**Built with ❤️ for cricket auction enthusiasts**
