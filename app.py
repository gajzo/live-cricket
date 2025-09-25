from flask import Flask, jsonify, request, render_template_string, send_from_directory, Response
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import threading
import time
from datetime import datetime, timedelta
import os
import sys
import json
import logging

app = Flask(__name__)

# Configure logging for better debugging on Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Enhanced CORS configuration
cors_config = {
    "origins": [
        "http://localhost:*",
        "http://127.0.0.1:*",
        "https://live-cricket-k3it.onrender.com",
        "https://*.onrender.com",
        "https://*.vercel.app",
        "https://*.netlify.app",
        "file://",
    ],
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": [
        "Content-Type",
        "Authorization",
        "Access-Control-Allow-Credentials",
        "Access-Control-Allow-Origin",
        "Accept",
        "Cache-Control"
    ],
    "supports_credentials": True,
    "max_age": 3600
}

# Apply CORS with specific configuration
CORS(app, resources={
    r"/api/*": cors_config,
    r"/": cors_config,
    r"/live": cors_config,
    r"/test.html": cors_config,
    r"/events": cors_config
})

# Add after_request handler for additional CORS headers
@app.after_request
def after_request(response):
    origin = request.headers.get('Origin')
    
    # List of allowed origins
    allowed_origins = [
        'http://localhost:3000',
        'http://localhost:5000',
        'http://localhost:8080',
        'http://127.0.0.1:5000',
        'https://live-cricket-k3it.onrender.com',
    ]
    
    # If origin is in allowed list or in development
    if origin in allowed_origins or (app.debug and origin and origin.startswith('http://localhost')):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, Accept, Cache-Control'
    
    # For preflight requests
    if request.method == 'OPTIONS':
        response.headers['Access-Control-Max-Age'] = '3600'
        response.status_code = 200
    
    return response

# Add specific OPTIONS handlers for preflight requests
@app.route('/api/current-score', methods=['OPTIONS'])
@app.route('/api/scrape', methods=['OPTIONS'])
@app.route('/api/status', methods=['OPTIONS'])
@app.route('/api/set-url', methods=['OPTIONS'])
@app.route('/api/debug', methods=['OPTIONS'])
@app.route('/api/toggle-auto-update', methods=['OPTIONS'])
@app.route('/events', methods=['OPTIONS'])
def handle_preflight():
    return jsonify({'status': 'ok'}), 200

# Global variables
CURRENT_MATCH_URL = None
MATCH_DATA = {}
AUTO_UPDATE = True
UPDATE_INTERVAL = 30  # seconds
LAST_UPDATE_TIME = None
ACTIVE_CONNECTIONS = set()  # Track active SSE connections

class Colors:
    """Terminal colors"""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class CricketScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        }
        
    def scrape_crex_scores(self, match_url):
        """Scrape live scores from CREX"""
        try:
            logger.info(f"Scraping URL: {match_url}")
            response = requests.get(match_url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Get the title which contains score information
            title_elem = soup.find('title')
            title_text = title_elem.text.strip() if title_elem else ""
            
            # Parse the title to extract match data
            data = self.parse_title_data(title_text)
            data['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            logger.info("Successfully scraped match data")
            return data
            
        except Exception as e:
            logger.error(f"Error scraping: {str(e)}")
            print(f"{Colors.FAIL}Error scraping: {str(e)}{Colors.ENDC}")
            return None

    def parse_title_data(self, title_text):
        """Parse the title text to extract match information"""
        # Initialize data structure
        data = {
            'title': title_text,
            'update': 'Live',
            'livescore': title_text.split(' | ')[0] if ' | ' in title_text else title_text,
            'runrate': 'CRR: 0.00',
            'team1_name': 'Team 1',
            'team1_score': '0',
            'team1_wickets': '0',
            'team1_overs': '0.0',
            'team2_name': 'Team 2',
            'team2_score': '0',
            'team2_wickets': '0', 
            'team2_overs': '0.0',
            'team2_status': 'Yet to bat',
            'batterone': 'Batsman 1',
            'batsmanonerun': '0',
            'batsmanoneball': '(0)',
            'batsmanonesr': '0.00',
            'battertwo': 'Batsman 2',
            'batsmantworun': '0',
            'batsmantwoball': '(0)',
            'batsmantwosr': '0.00',
            'bowlerone': 'Bowler 1',
            'bowleroneover': '0',
            'bowleronerun': '0',
            'bowleronewickers': '0',
            'bowleroneeconomy': '0.00',
            'bowlertwo': 'Bowler 2',
            'bowlertwoover': '0',
            'bowlertworun': '0',
            'bowlertwowickers': '0',
            'bowlertwoeconomy': '0.00'
        }
        
        try:
            # Extract just the score part before the match details
            score_part = title_text.split(' | ')[0] if ' | ' in title_text else title_text
            
            print(f"Debug - Parsing: {score_part}")
            
            # Split by ' vs '
            if ' vs ' in score_part:
                vs_index = score_part.find(' vs ')
                team1_full = score_part[:vs_index].strip()
                team2_full = score_part[vs_index + 4:].strip()
                
                print(f"Debug - Team1 full: {team1_full}")
                print(f"Debug - Team2 full: {team2_full}")
                
                # Parse Team 1 (batting team)
                # Example: "IND U19 175-3 (25.5) (Abhigyan Kundu 46(55), Vedant Trivedi 53(59))"
                
                # Extract team name - everything until first number
                team1_name_match = re.match(r'^([^\d]+)', team1_full)
                if team1_name_match:
                    data['team1_name'] = team1_name_match.group(1).strip()
                    print(f"Debug - Team1 name: {data['team1_name']}")
                
                # Extract score and wickets - find pattern like "175-3"
                score_match = re.search(r'(\d+)-(\d+)', team1_full)
                if score_match:
                    data['team1_score'] = score_match.group(1)
                    data['team1_wickets'] = score_match.group(2)
                    print(f"Debug - Team1 score: {data['team1_score']}-{data['team1_wickets']}")
                
                # Extract overs - find pattern in parentheses like "(25.5)"
                overs_match = re.search(r'\((\d+\.\d+)\)', team1_full)
                if overs_match:
                    data['team1_overs'] = overs_match.group(1)
                    print(f"Debug - Team1 overs: {data['team1_overs']}")
                
                # Extract batsmen information - everything after the last space before batsmen section
                batsmen_match = re.search(r'\(([^)]+)\)', team1_full)
                if batsmen_match:
                    batsmen_str = batsmen_match.group(1)
                    print(f"Debug - Batsmen string: {batsmen_str}")
                    
                    # Split batsmen by comma
                    batsmen_list = [b.strip() for b in batsmen_str.split(',')]
                    print(f"Debug - Batsmen list: {batsmen_list}")
                    
                    for i, batsman_info in enumerate(batsmen_list[:2]):
                        # Parse batsman info like "Abhigyan Kundu 46(55)" or "Vedant Trivedi 53(59)"
                        bat_match = re.match(r'^(.+?)\s+(\d+)\((\d+)\)$', batsman_info.strip())
                        if bat_match:
                            name = bat_match.group(1).strip()
                            runs = bat_match.group(2)
                            balls = bat_match.group(3)
                            
                            if i == 0:
                                data['batterone'] = name
                                data['batsmanonerun'] = runs
                                data['batsmanoneball'] = f"({balls})"
                                balls_int = int(balls)
                                runs_int = int(runs)
                                if balls_int > 0:
                                    data['batsmanonesr'] = f"{(runs_int * 100 / balls_int):.2f}"
                                print(f"Debug - Batsman1: {data['batterone']} {data['batsmanonerun']}{data['batsmanoneball']}")
                            else:
                                data['battertwo'] = name
                                data['batsmantworun'] = runs
                                data['batsmantwoball'] = f"({balls})"
                                balls_int = int(balls)
                                runs_int = int(runs)
                                if balls_int > 0:
                                    data['batsmantwosr'] = f"{(runs_int * 100 / balls_int):.2f}"
                                print(f"Debug - Batsman2: {data['battertwo']} {data['batsmantworun']}{data['batsmantwoball']}")
                
                # Parse Team 2 (opponent team)
                # Example: "Australia U19 225-9 ((50.0)) Final live"
                
                # Extract team name - everything until first number
                team2_name_match = re.match(r'^([^\d]+)', team2_full)
                if team2_name_match:
                    data['team2_name'] = team2_name_match.group(1).strip()
                    print(f"Debug - Team2 name: {data['team2_name']}")
                
                # Extract score and wickets
                score_match = re.search(r'(\d+)-(\d+)', team2_full)
                if score_match:
                    data['team2_score'] = score_match.group(1)
                    data['team2_wickets'] = score_match.group(2)
                    print(f"Debug - Team2 score: {data['team2_score']}-{data['team2_wickets']}")
                
                # Extract overs - handle double parentheses first ((50.0))
                overs_match = re.search(r'\(\(\s*(\d+(?:\.\d+)?)\s*\)\)', team2_full)
                if overs_match:
                    data['team2_overs'] = overs_match.group(1)
                    print(f"Debug - Team2 overs (double parens): {data['team2_overs']}")
                else:
                    # Try single parentheses (50.0)
                    overs_match = re.search(r'\((\d+(?:\.\d+)?)\)', team2_full)
                    if overs_match:
                        data['team2_overs'] = overs_match.group(1)
                        print(f"Debug - Team2 overs (single parens): {data['team2_overs']}")
                
                # Update team 2 status
                if data['team2_score'] != '0':
                    data['team2_status'] = f"{data['team2_score']}-{data['team2_wickets']} ({data['team2_overs']} overs)"
                
                # Calculate run rates
                try:
                    team1_runs = int(data['team1_score'])
                    team1_overs = self.overs_to_decimal(data['team1_overs'])
                    
                    if team1_overs > 0:
                        crr = round(team1_runs / team1_overs, 2)
                        data['runrate'] = f'CRR: {crr}'
                        
                        # If chasing
                        team2_runs = int(data['team2_score'])
                        if team2_runs > 0:
                            target = team2_runs + 1
                            runs_needed = target - team1_runs
                            overs_left = 20.0 - team1_overs
                            
                            if overs_left > 0 and runs_needed > 0:
                                rrr = round(runs_needed / overs_left, 2)
                                data['runrate'] += f' | RRR: {rrr}'
                            
                            data['update'] = f"Target: {target}"
                            data['livescore'] = f"{data['team1_name']} {data['team1_score']}-{data['team1_wickets']} ({data['team1_overs']}) chasing {target}"
                except Exception as e:
                    print(f"Debug - Run rate calculation error: {e}")
                    
        except Exception as e:
            print(f"Error parsing title: {str(e)}")
            import traceback
            traceback.print_exc()
        
        print(f"\nDebug - Final data:")
        print(f"  Team1: {data['team1_name']} {data['team1_score']}-{data['team1_wickets']} ({data['team1_overs']})")
        print(f"  Team2: {data['team2_name']} {data['team2_score']}-{data['team2_wickets']} ({data['team2_overs']})")
        print(f"  Batsman1: {data['batterone']} - {data['batsmanonerun']}{data['batsmanoneball']} SR: {data['batsmanonesr']}")
        print(f"  Batsman2: {data['battertwo']} - {data['batsmantworun']}{data['batsmantwoball']} SR: {data['batsmantwosr']}")
        
        return data
    
    def overs_to_decimal(self, overs):
        """Convert overs like '4.3' to decimal 4.5"""
        try:
            if '.' in overs:
                parts = overs.split('.')
                return int(parts[0]) + (int(parts[1]) / 6)
            return float(overs)
        except:
            return 0.0

def should_update_data():
    """Check if data should be updated based on time"""
    global LAST_UPDATE_TIME
    if not LAST_UPDATE_TIME:
        return True
    
    time_diff = datetime.now() - LAST_UPDATE_TIME
    return time_diff.seconds >= UPDATE_INTERVAL

def update_match_data():
    """Update match data and notify SSE clients"""
    global CURRENT_MATCH_URL, MATCH_DATA, LAST_UPDATE_TIME, scraper
    
    if not CURRENT_MATCH_URL:
        return False
    
    try:
        logger.info("Updating match data...")
        data = scraper.scrape_crex_scores(CURRENT_MATCH_URL)
        if data:
            MATCH_DATA = data
            LAST_UPDATE_TIME = datetime.now()
            print_match_update(data)
            
            # Notify SSE clients
            notify_sse_clients(data)
            logger.info("Match data updated successfully")
            return True
        else:
            logger.error("Failed to scrape match data")
            return False
    except Exception as e:
        logger.error(f"Error updating match data: {str(e)}")
        return False

def notify_sse_clients(data):
    """Send updates to all SSE clients"""
    global ACTIVE_CONNECTIONS
    
    # Remove closed connections
    closed_connections = set()
    for connection in ACTIVE_CONNECTIONS.copy():
        try:
            connection.put_nowait(f"data: {json.dumps(data)}\n\n")
        except:
            closed_connections.add(connection)
    
    # Clean up closed connections
    ACTIVE_CONNECTIONS -= closed_connections

# Server-Sent Events endpoint for real-time updates
@app.route('/events')
def events():
    """Server-sent events endpoint for real-time score updates"""
    def event_stream():
        import queue
        # Create a queue for this connection
        q = queue.Queue()
        ACTIVE_CONNECTIONS.add(q)
        
        try:
            # Send initial data
            if MATCH_DATA:
                yield f"data: {json.dumps(MATCH_DATA)}\n\n"
            
            # Keep connection alive and send updates
            while True:
                try:
                    # Wait for updates with timeout
                    data = q.get(timeout=30)  # 30 second timeout
                    yield data
                except queue.Empty:
                    # Send keepalive
                    yield "data: {\"keepalive\": true}\n\n"
                except:
                    break
        finally:
            # Clean up
            ACTIVE_CONNECTIONS.discard(q)
    
    return Response(event_stream(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*'
    })

# HTML template for URL input page with SSE support
URL_INPUT_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Cricket Score Tracker - Control Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
            color: white;
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        
        .card {
            background: rgba(255, 255, 255, 0.1);
            padding: 30px;
            border-radius: 20px;
            backdrop-filter: blur(10px);
            margin-bottom: 20px;
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
        }
        
        h1 {
            margin-bottom: 30px;
            text-align: center;
            font-size: 2.5rem;
        }
        
        h2 {
            margin-bottom: 20px;
            color: #667eea;
        }
        
        input {
            width: 100%;
            padding: 15px;
            font-size: 16px;
            border: none;
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.2);
            color: white;
            margin-bottom: 20px;
        }
        
        input::placeholder {
            color: rgba(255, 255, 255, 0.7);
        }
        
        button {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 30px;
            font-size: 16px;
            border-radius: 50px;
            cursor: pointer;
            transition: all 0.3s ease;
            margin-right: 10px;
            margin-bottom: 10px;
        }
        
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(0, 0, 0, 0.3);
        }
        
        .status {
            padding: 15px;
            border-radius: 10px;
            margin-top: 20px;
        }
        
        .success { background: rgba(46, 204, 113, 0.2); color: #2ecc71; }
        .error { background: rgba(231, 76, 60, 0.2); color: #e74c3c; }
        .info { background: rgba(52, 152, 219, 0.2); color: #3498db; }
        
        .current-match {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-top: 20px;
        }
        
        .stat-box {
            background: rgba(255, 255, 255, 0.05);
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }
        
        .stat-label {
            font-size: 0.9rem;
            opacity: 0.8;
            margin-bottom: 5px;
        }
        
        .stat-value {
            font-size: 1.5rem;
            font-weight: bold;
            color: #ffd93d;
        }
        
        .endpoints {
            background: rgba(255, 255, 255, 0.05);
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
        }
        
        .endpoint {
            padding: 10px;
            margin: 5px 0;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 5px;
            font-family: monospace;
        }
        
        .live-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            background: #2ecc71;
            border-radius: 50%;
            animation: pulse 2s infinite;
            margin-right: 10px;
        }
        
        .connection-status {
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 15px;
            text-align: center;
        }
        
        .connected { background: rgba(46, 204, 113, 0.2); color: #2ecc71; }
        .disconnected { background: rgba(231, 76, 60, 0.2); color: #e74c3c; }
        
        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(46, 204, 113, 0.7); }
            70% { box-shadow: 0 0 0 10px rgba(46, 204, 113, 0); }
            100% { box-shadow: 0 0 0 0 rgba(46, 204, 113, 0); }
        }
        
        @media (max-width: 768px) {
            .current-match {
                grid-template-columns: 1fr;
            }
            
            button {
                width: 100%;
                margin-bottom: 10px;
                margin-right: 0;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>🏏 Cricket Score Tracker</h1>
            
            <div id="connectionStatus" class="connection-status disconnected">
                🔴 Disconnected - Real-time updates not available
            </div>
            
            <form onsubmit="setURL(event)">
                <input type="url" id="matchUrl" placeholder="https://crex.com/scoreboard/..." required
                       value="{{ current_url or '' }}">
                <div>
                    <button type="submit">Start Tracking</button>
                    <button type="button" onclick="refreshScores()">🔄 Refresh Now</button>
                    <button type="button" onclick="toggleAutoUpdate()">
                        <span id="autoUpdateBtn">{{ '⏸️ Pause' if auto_update else '▶️ Resume' }} Auto-Update</span>
                    </button>
                    <button type="button" onclick="window.location.href='/live'">📺 View Live Scores</button>
                </div>
            </form>
            
            <div id="status"></div>
        </div>
        
        <div class="card" id="matchCard" style="{{ 'display: none;' if not (current_url and match_data) else '' }}">
            <h2><span class="live-indicator"></span>Current Match</h2>
            <div class="current-match" id="currentMatchData">
                <!-- Match data will be populated here -->
            </div>
            
            <div class="stat-box" id="batsmenData" style="margin-top: 20px; display: none;">
                <h3 style="margin-bottom: 15px;">Current Batsmen</h3>
                <div id="batsmenInfo"></div>
            </div>
        </div>
        
        <div class="card">
            <h2>API Endpoints</h2>
            <div class="endpoints">
                <div class="endpoint">GET /api/current-score - Get current match scores</div>
                <div class="endpoint">GET /api/scrape?url={match_url} - Scrape specific match</div>
                <div class="endpoint">GET /api/status - Get API status</div>
                <div class="endpoint">POST /api/set-url - Set new match URL</div>
                <div class="endpoint">GET /live - View live scores page</div>
                <div class="endpoint">GET /api/debug - Debug information</div>
                <div class="endpoint">GET /events - Server-Sent Events for real-time updates</div>
            </div>
        </div>
    </div>
    
    <script>
        let autoUpdate = {{ 'true' if auto_update else 'false' }};
        let eventSource = null;
        let reconnectAttempts = 0;
        const maxReconnectAttempts = 5;
        
        // Initialize SSE connection
        function initSSE() {
            if (eventSource) {
                eventSource.close();
            }
            
            eventSource = new EventSource('/events');
            
            eventSource.onopen = function() {
                console.log('SSE connected');
                document.getElementById('connectionStatus').innerHTML = '🟢 Connected - Real-time updates active';
                document.getElementById('connectionStatus').className = 'connection-status connected';
                reconnectAttempts = 0;
            };
            
            eventSource.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (!data.keepalive) {
                        updateMatchDisplay(data);
                    }
                } catch (e) {
                    console.error('Error parsing SSE data:', e);
                }
            };
            
            eventSource.onerror = function() {
                console.error('SSE connection error');
                document.getElementById('connectionStatus').innerHTML = '🟡 Reconnecting...';
                document.getElementById('connectionStatus').className = 'connection-status info';
                
                eventSource.close();
                
                // Attempt to reconnect with exponential backoff
                if (reconnectAttempts < maxReconnectAttempts) {
                    setTimeout(() => {
                        reconnectAttempts++;
                        console.log(`Reconnection attempt ${reconnectAttempts}`);
                        initSSE();
                    }, Math.pow(2, reconnectAttempts) * 1000);
                } else {
                    document.getElementById('connectionStatus').innerHTML = '🔴 Connection failed - Using manual refresh only';
                    document.getElementById('connectionStatus').className = 'connection-status disconnected';
                }
            };
        }
        
        // Update match display with new data
        function updateMatchDisplay(data) {
            if (!data || data.team1_name === 'Team 1') return;
            
            const matchCard = document.getElementById('matchCard');
            const matchData = document.getElementById('currentMatchData');
            const batsmenData = document.getElementById('batsmenData');
            const batsmenInfo = document.getElementById('batsmenInfo');
            
            // Show match card
            matchCard.style.display = 'block';
            
            // Update match data
            matchData.innerHTML = `
                <div class="stat-box">
                    <div class="stat-label">${data.team1_name || 'Team 1'}</div>
                    <div class="stat-value">${data.team1_score || '0'}-${data.team1_wickets || '0'}</div>
                    <div style="opacity: 0.8;">(${data.team1_overs || '0'} overs)</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">${data.team2_name || 'Team 2'}</div>
                    ${data.team2_status !== 'Yet to bat' ? `
                        <div class="stat-value">${data.team2_score || '0'}-${data.team2_wickets || '0'}</div>
                        <div style="opacity: 0.8;">(${data.team2_overs || '0'} overs)</div>
                    ` : '<div class="stat-value">Yet to bat</div>'}
                </div>
                <div class="stat-box">
                    <div class="stat-label">Run Rate</div>
                    <div class="stat-value">${data.runrate || 'CRR: 0.00'}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Last Update</div>
                    <div class="stat-value">${data.timestamp || 'N/A'}</div>
                </div>
            `;
            
            // Update batsmen data
            if (data.batterone && data.batterone !== 'Batsman 1') {
                batsmenData.style.display = 'block';
                let batsmenHtml = `<p>🏏 ${data.batterone}: ${data.batsmanonerun} ${data.batsmanoneball} SR: ${data.batsmanonesr}</p>`;
                if (data.battertwo && data.battertwo !== 'Batsman 2') {
                    batsmenHtml += `<p>🏏 ${data.battertwo}: ${data.batsmantworun} ${data.batsmantwoball} SR: ${data.batsmantwosr}</p>`;
                }
                batsmenInfo.innerHTML = batsmenHtml;
            } else {
                batsmenData.style.display = 'none';
            }
        }
        
        async function setURL(event) {
            event.preventDefault();
            const url = document.getElementById('matchUrl').value;
            const status = document.getElementById('status');
            
            status.innerHTML = '⏳ Setting up tracking...';
            status.className = 'status info';
            
            try {
                const response = await fetch('/api/set-url', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url })
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    status.innerHTML = '✅ Tracking started successfully!';
                    status.className = 'status success';
                    
                    // Update display immediately
                    if (data.initial_data) {
                        updateMatchDisplay(data.initial_data);
                    }
                    
                    // Initialize SSE connection
                    if (!eventSource) {
                        initSSE();
                    }
                } else {
                    status.innerHTML = '❌ ' + (data.error || 'Failed to set URL');
                    status.className = 'status error';
                }
            } catch (error) {
                status.innerHTML = '❌ Error: ' + error.message;
                status.className = 'status error';
            }
        }
        
        async function refreshScores() {
            const status = document.getElementById('status');
            status.innerHTML = '🔄 Refreshing scores...';
            status.className = 'status info';
            
            try {
                const response = await fetch('/api/scrape');
                const data = await response.json();
                
                if (response.ok) {
                    status.innerHTML = '✅ Scores refreshed!';
                    status.className = 'status success';
                    updateMatchDisplay(data);
                    setTimeout(() => {
                        status.innerHTML = '';
                        status.className = '';
                    }, 2000);
                } else {
                    status.innerHTML = '❌ Failed to refresh scores';
                    status.className = 'status error';
                }
            } catch (error) {
                status.innerHTML = '❌ Error: ' + error.message;
                status.className = 'status error';
            }
        }
        
        async function toggleAutoUpdate() {
            autoUpdate = !autoUpdate;
            const btn = document.getElementById('autoUpdateBtn');
            btn.textContent = autoUpdate ? '⏸️ Pause Auto-Update' : '▶️ Resume Auto-Update';
            
            try {
                await fetch('/api/toggle-auto-update', { method: 'POST' });
            } catch (error) {
                console.error('Failed to toggle auto-update:', error);
            }
        }
        
        // Initialize on page load
        window.addEventListener('load', function() {
            // Initialize SSE if we have a match URL
            const matchUrl = document.getElementById('matchUrl').value;
            if (matchUrl) {
                initSSE();
            }
        });
        
        // Clean up on page unload
        window.addEventListener('beforeunload', function() {
            if (eventSource) {
                eventSource.close();
            }
        });
    </script>
</body>
</html>
"""

scraper = CricketScraper()

@app.route('/')
def home():
    global CURRENT_MATCH_URL, MATCH_DATA, AUTO_UPDATE
    return render_template_string(
        URL_INPUT_PAGE, 
        current_url=CURRENT_MATCH_URL,
        match_data=MATCH_DATA,
        auto_update=AUTO_UPDATE
    )

@app.route('/live')
def live_scores():
    """Serve the live scores HTML page"""
    # Check if index.html exists in current directory
    if os.path.exists('index.html'):
        return send_from_directory('.', 'index.html')
    else:
        # Return a simple message if index.html doesn't exist
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Live Scores</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background: #1a1a2e;
                    color: white;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    margin: 0;
                }
                .message {
                    text-align: center;
                    padding: 40px;
                    background: rgba(255, 255, 255, 0.1);
                    border-radius: 10px;
                }
                a {
                    color: #667eea;
                    text-decoration: none;
                }
            </style>
        </head>
        <body>
            <div class="message">
                <h1>📁 index.html Not Found</h1>
                <p>Please create an index.html file in the same directory as app.py</p>
                <p>or use the API endpoint: <a href="/api/current-score">/api/current-score</a></p>
                <p><a href="/">← Back to Control Panel</a></p>
            </div>
        </body>
        </html>
        """

@app.route('/api/set-url', methods=['POST'])
def set_url():
    global CURRENT_MATCH_URL, MATCH_DATA, LAST_UPDATE_TIME
    
    data = request.json
    url = data.get('url')
    
    if not url:
        return jsonify({"error": "URL is required"}), 400
    
    CURRENT_MATCH_URL = url
    LAST_UPDATE_TIME = None  # Reset update time
    
    # Do initial scrape
    scraped_data = scraper.scrape_crex_scores(url)
    if scraped_data:
        MATCH_DATA = scraped_data
        LAST_UPDATE_TIME = datetime.now()
        print_match_update(scraped_data)
        logger.info(f"Successfully set URL and scraped initial data: {url}")
        return jsonify({
            "message": "URL set successfully", 
            "url": url,
            "initial_data": scraped_data
        })
    
    logger.error("Failed to scrape initial data after setting URL")
    return jsonify({"error": "Failed to scrape initial data"}), 500

@app.route('/api/current-score')
def get_current_score():
    global CURRENT_MATCH_URL, MATCH_DATA
    
    response_headers = {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }
    
    if not CURRENT_MATCH_URL:
        return jsonify({"error": "No match URL set. Please visit the home page to set a URL."}), 400, response_headers
    
    # Check if we should update data
    if should_update_data():
        logger.info("Data is stale, triggering update...")
        success = update_match_data()
        if not success and not MATCH_DATA:
            return jsonify({"error": "Failed to fetch current scores"}), 503, response_headers
    
    # Return current data if available
    if MATCH_DATA and any(MATCH_DATA.values()):
        return jsonify(MATCH_DATA), 200, response_headers
    else:
        return jsonify({"error": "No data available yet. Please wait for the first update."}), 503, response_headers

@app.route('/api/scrape')
def scrape_match():
    """Scrape match data from URL parameter or current URL"""
    global CURRENT_MATCH_URL, MATCH_DATA, LAST_UPDATE_TIME
    
    match_url = request.args.get('url') or CURRENT_MATCH_URL
    
    if not match_url:
        return jsonify({"error": "No match URL provided or set"}), 400
    
    data = scraper.scrape_crex_scores(match_url)
    
    if data:
        MATCH_DATA = data
        LAST_UPDATE_TIME = datetime.now()
        print_match_update(data)
        
        # Notify SSE clients
        notify_sse_clients(data)
        
        logger.info("Successfully scraped match data")
        return jsonify(data)
    
    logger.error("Failed to scrape match data")
    return jsonify({"error": "Unable to scrape match data"}), 500

@app.route('/api/status')
def get_status():
    global CURRENT_MATCH_URL, AUTO_UPDATE, MATCH_DATA, LAST_UPDATE_TIME
    return jsonify({
        "current_url": CURRENT_MATCH_URL,
        "auto_update": AUTO_UPDATE,
        "update_interval": UPDATE_INTERVAL,
        "has_data": bool(MATCH_DATA),
        "last_update": LAST_UPDATE_TIME.strftime("%Y-%m-%d %H:%M:%S") if LAST_UPDATE_TIME else None,
        "active_connections": len(ACTIVE_CONNECTIONS)
    })

@app.route('/api/debug')
def debug_info():
    """Debug endpoint to check current state"""
    global CURRENT_MATCH_URL, MATCH_DATA, LAST_UPDATE_TIME
    return jsonify({
        "current_url": CURRENT_MATCH_URL,
        "has_match_data": bool(MATCH_DATA),
        "last_update": LAST_UPDATE_TIME.strftime("%Y-%m-%d %H:%M:%S") if LAST_UPDATE_TIME else None,
        "match_data_keys": list(MATCH_DATA.keys()) if MATCH_DATA else [],
        "should_update": should_update_data(),
        "active_sse_connections": len(ACTIVE_CONNECTIONS),
        "match_data_sample": {
            "team1_name": MATCH_DATA.get('team1_name', 'N/A'),
            "team1_score": MATCH_DATA.get('team1_score', 'N/A'),
            "team1_overs": MATCH_DATA.get('team1_overs', 'N/A'),
            "team2_name": MATCH_DATA.get('team2_name', 'N/A'),
            "team2_score": MATCH_DATA.get('team2_score', 'N/A'),
            "team2_overs": MATCH_DATA.get('team2_overs', 'N/A'),
            "livescore": MATCH_DATA.get('livescore', 'N/A'),
            "batterone": MATCH_DATA.get('batterone', 'N/A'),
            "batsmanonerun": MATCH_DATA.get('batsmanonerun', 'N/A'),
            "battertwo": MATCH_DATA.get('battertwo', 'N/A'),
            "batsmantworun": MATCH_DATA.get('batsmantworun', 'N/A')
        } if MATCH_DATA else {}
    })

@app.route('/api/toggle-auto-update', methods=['POST'])
def toggle_auto_update():
    global AUTO_UPDATE
    AUTO_UPDATE = not AUTO_UPDATE
    logger.info(f"Auto-update {'enabled' if AUTO_UPDATE else 'disabled'}")
    print(f"\n{Colors.CYAN}Auto-update {'enabled' if AUTO_UPDATE else 'disabled'}{Colors.ENDC}")
    return jsonify({"auto_update": AUTO_UPDATE})

@app.route('/test.html')
def test_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>API Test</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 20px;
                background: #f0f0f0;
            }
            button {
                padding: 10px 20px;
                margin: 5px;
                font-size: 16px;
                cursor: pointer;
            }
            pre {
                background: white;
                padding: 20px;
                border: 1px solid #ccc;
                border-radius: 5px;
                overflow-x: auto;
            }
        </style>
    </head>
    <body>
        <h1>Cricket Score API Test</h1>
        
        <button onclick="testAPI()">Test API</button>
        <button onclick="testDebug()">Debug Info</button>
        <button onclick="testStatus()">API Status</button>
        <button onclick="testSSE()">Test SSE Connection</button>
        
        <pre id="output">Click a button to test the API</pre>
        
        <script>
            async function testAPI() {
                const output = document.getElementById('output');
                try {
                    const response = await fetch('/api/current-score');
                    output.textContent = `Status: ${response.status}\\n`;
                    output.textContent += `Headers: ${JSON.stringify(Object.fromEntries(response.headers))}\\n\\n`;
                    
                    const text = await response.text();
                    try {
                        const data = JSON.parse(text);
                        output.textContent += `JSON Data:\\n${JSON.stringify(data, null, 2)}`;
                    } catch (e) {
                        output.textContent += `Raw Response:\\n${text.substring(0, 500)}`;
                    }
                } catch (error) {
                    output.textContent = `Error: ${error.message}`;
                }
            }
            
            async function testDebug() {
                const output = document.getElementById('output');
                try {
                    const response = await fetch('/api/debug');
                    const data = await response.json();
                    output.textContent = `Debug Info:\\n${JSON.stringify(data, null, 2)}`;
                } catch (error) {
                    output.textContent = `Error: ${error.message}`;
                }
            }
            
            async function testStatus() {
                const output = document.getElementById('output');
                try {
                    const response = await fetch('/api/status');
                    const data = await response.json();
                    output.textContent = `API Status:\\n${JSON.stringify(data, null, 2)}`;
                } catch (error) {
                    output.textContent = `Error: ${error.message}`;
                }
            }
            
            function testSSE() {
                const output = document.getElementById('output');
                output.textContent = 'Testing SSE connection...\\n';
                
                const eventSource = new EventSource('/events');
                let messageCount = 0;
                
                eventSource.onopen = function() {
                    output.textContent += 'SSE Connected!\\n';
                };
                
                eventSource.onmessage = function(event) {
                    messageCount++;
                    output.textContent += `Message ${messageCount}: ${event.data.substring(0, 100)}...\\n`;
                    
                    if (messageCount >= 3) {
                        eventSource.close();
                        output.textContent += 'SSE Test completed (received 3 messages)\\n';
                    }
                };
                
                eventSource.onerror = function() {
                    output.textContent += 'SSE Error occurred\\n';
                    eventSource.close();
                };
                
                // Close after 10 seconds if not already closed
                setTimeout(() => {
                    if (eventSource.readyState !== EventSource.CLOSED) {
                        eventSource.close();
                        output.textContent += 'SSE Test timeout (10s)\\n';
                    }
                }, 10000);
            }
        </script>
    </body>
    </html>
    """

def print_banner():
    """Print application banner"""
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"""
{Colors.CYAN}╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║  {Colors.BOLD}🏏  CRICKET SCORE TRACKER - CREX SCRAPER  🏏{Colors.ENDC}{Colors.CYAN}           ║
║                                                           ║
║  {Colors.GREEN}Made with ❤️  by Gajju{Colors.CYAN}                                  ║
║  {Colors.BLUE}Enhanced with Real-time SSE Updates{Colors.CYAN}                     ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝{Colors.ENDC}
    """)

def print_match_update(data):
    """Print match update in terminal"""
    print(f"\n{Colors.GREEN}━━━ Match Update ━━━{Colors.ENDC}")
    print(f"{Colors.BOLD}Match:{Colors.ENDC} {data.get('title', 'N/A')}")
    
    # Show the actual live score
    livescore = data.get('livescore', 'N/A')
    print(f"{Colors.BOLD}Score:{Colors.ENDC} {livescore}")
    
    # Show teams with correct overs
    print(f"{Colors.BOLD}Batting:{Colors.ENDC} {data.get('team1_name')} {data.get('team1_score')}-{data.get('team1_wickets')} ({data.get('team1_overs')})")
    if data.get('team2_score', '0') != '0':
        print(f"{Colors.BOLD}Opponent:{Colors.ENDC} {data.get('team2_name')} {data.get('team2_score')}-{data.get('team2_wickets')} ({data.get('team2_overs')})")
    
    # Show run rate
    runrate = data.get('runrate', 'N/A')
    if runrate != 'CRR: 0.00':
        print(f"{Colors.BOLD}Run Rate:{Colors.ENDC} {runrate}")
    
    # Show target/status if available
    if data.get('update') and data.get('update') != 'Live':
        print(f"{Colors.BOLD}Status:{Colors.ENDC} {data.get('update')}")
    
    # Print batsmen if available
    if data.get('batterone', 'Batsman 1') != 'Batsman 1':
        print(f"\n{Colors.CYAN}Batting:{Colors.ENDC}")
        print(f"  • {data.get('batterone')}: {data.get('batsmanonerun')} {data.get('batsmanoneball')} SR: {data.get('batsmanonesr')}")
        if data.get('battertwo', 'Batsman 2') != 'Batsman 2':
            print(f"  • {data.get('battertwo')}: {data.get('batsmantworun')} {data.get('batsmantwoball')} SR: {data.get('batsmantwosr')}")
    
    print(f"\n{Colors.BOLD}Time:{Colors.ENDC} {data.get('timestamp', 'N/A')}")
    print(f"Active SSE connections: {len(ACTIVE_CONNECTIONS)}")
    print(f"{Colors.GREEN}━━━━━━━━━━━━━━━━━━━{Colors.ENDC}\n")

def auto_update_scores():
    """Background thread to auto-update scores - now works better with deployments"""
    global CURRENT_MATCH_URL, MATCH_DATA, AUTO_UPDATE, UPDATE_INTERVAL, LAST_UPDATE_TIME
    
    logger.info("Auto-update thread started")
    
    while True:
        try:
            time.sleep(UPDATE_INTERVAL)
            
            if AUTO_UPDATE and CURRENT_MATCH_URL:
                logger.info("[Auto-Update] Checking if update needed...")
                
                if should_update_data():
                    logger.info("[Auto-Update] Updating scores...")
                    success = update_match_data()
                    
                    if success:
                        logger.info("[Auto-Update] Successfully updated scores")
                    else:
                        logger.error("[Auto-Update] Failed to update scores")
                else:
                    logger.debug("[Auto-Update] Data is still fresh, skipping update")
        except Exception as e:
            logger.error(f"[Auto-Update] Error in update loop: {str(e)}")
            time.sleep(5)  # Wait before retrying

def get_user_input():
    """Interactive terminal menu"""
    global CURRENT_MATCH_URL, UPDATE_INTERVAL, MATCH_DATA
    
    print_banner()
    
    print(f"\n{Colors.BOLD}Options:{Colors.ENDC}")
    print("1. Enter CREX match URL")
    print("2. Use sample URL (Demo)")
    print("3. Start without URL (set via web interface)")
    print("4. Configure update interval")
    print("5. Exit\n")
    
    choice = input(f"{Colors.CYAN}Select option (1-5): {Colors.ENDC}").strip()
    
    if choice == '1':
        url = input(f"\n{Colors.CYAN}Enter CREX match URL: {Colors.ENDC}").strip()
        
        if url:
            CURRENT_MATCH_URL = url
            print(f"\n{Colors.GREEN}✅ URL set successfully!{Colors.ENDC}")
            
            # Initial scrape
            print(f"{Colors.CYAN}📊 Fetching initial scores...{Colors.ENDC}")
            data = scraper.scrape_crex_scores(url)
            if data:
                MATCH_DATA = data
                LAST_UPDATE_TIME = datetime.now()
                print_match_update(data)
            else:
                print(f"{Colors.FAIL}❌ Failed to fetch initial scores{Colors.ENDC}")
    
    elif choice == '2':
        # Use sample URL
        CURRENT_MATCH_URL = "https://crex.com/scoreboard/WAX/1XP/3rd-Match/1I/16/aus-w-vs-ind-w-3rd-match-australia-women-tour-of-india-2025/live"
        print(f"\n{Colors.GREEN}✅ Using sample URL{Colors.ENDC}")
        
        # Initial scrape
        print(f"{Colors.CYAN}📊 Fetching initial scores...{Colors.ENDC}")
        data = scraper.scrape_crex_scores(CURRENT_MATCH_URL)
        if data:
            MATCH_DATA = data
            LAST_UPDATE_TIME = datetime.now()
            print_match_update(data)
    
    elif choice == '3':
        print(f"\n{Colors.CYAN}📌 Starting without URL. Set it via web interface.{Colors.ENDC}")
    
    elif choice == '4':
        interval = input(f"\n{Colors.CYAN}Enter update interval in seconds (current: {UPDATE_INTERVAL}): {Colors.ENDC}")
        try:
            UPDATE_INTERVAL = int(interval)
            print(f"{Colors.GREEN}✅ Update interval set to {UPDATE_INTERVAL} seconds{Colors.ENDC}")
        except ValueError:
            print(f"{Colors.FAIL}❌ Invalid interval{Colors.ENDC}")
    
    elif choice == '5':
        print(f"\n{Colors.CYAN}Goodbye! 👋{Colors.ENDC}")
        sys.exit(0)
    
    else:
        print(f"{Colors.FAIL}Invalid option!{Colors.ENDC}")
        time.sleep(1)
        return get_user_input()
    
    print_server_info()

def print_server_info():
    """Print server information"""
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}🌐 Server Information:{Colors.ENDC}")
    print(f"   • Web Interface: {Colors.CYAN}http://localhost:5000{Colors.ENDC}")
    print(f"   • Live Scores: {Colors.CYAN}http://localhost:5000/live{Colors.ENDC}")
    print(f"   • API Endpoint: {Colors.CYAN}http://localhost:5000/api/current-score{Colors.ENDC}")
    print(f"   • Real-time Events: {Colors.CYAN}http://localhost:5000/events{Colors.ENDC}")
    print(f"   • Test Page: {Colors.CYAN}http://localhost:5000/test.html{Colors.ENDC}")
    print(f"   • Debug API: {Colors.CYAN}http://localhost:5000/api/debug{Colors.ENDC}")
    print(f"   • Auto-Update: {Colors.GREEN if AUTO_UPDATE else Colors.FAIL}{'Enabled' if AUTO_UPDATE else 'Disabled'}{Colors.ENDC}")
    print(f"   • Update Interval: {Colors.CYAN}{UPDATE_INTERVAL} seconds{Colors.ENDC}")
    
    if CURRENT_MATCH_URL:
        print(f"   • Tracking: {Colors.GREEN}{CURRENT_MATCH_URL}{Colors.ENDC}")
    
    print(f"\n{Colors.BOLD}📝 Instructions:{Colors.ENDC}")
    print("   • Visit the web interface to manage settings")
    print("   • Real-time updates via Server-Sent Events (SSE)")
    print("   • The API will auto-update scores every " + str(UPDATE_INTERVAL) + " seconds")
    print("   • View live scores at /live endpoint")
    print("   • Use /test.html to debug API issues")
    print("   • Press Ctrl+C to stop the server")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")

if __name__ == '__main__':
    try:
        # Start background update thread
        update_thread = threading.Thread(target=auto_update_scores, daemon=True)
        update_thread.start()
        
        # Get user input if running interactively
        if os.isatty(sys.stdin.fileno()):
            get_user_input()
        else:
            # Running on server (like Render), just print info
            print_banner()
            print_server_info()
        
        # Run Flask app with proper host configuration
        port = int(os.environ.get('PORT', 5000))
        host = os.environ.get('HOST', '0.0.0.0')
        
        logger.info(f"Starting Flask app on {host}:{port}")
        
        app.run(
            debug=False, 
            host=host,
            port=port, 
            use_reloader=False
        )
        
    except KeyboardInterrupt:
        print(f"\n\n{Colors.CYAN}Server stopped. Goodbye! 👋{Colors.ENDC}")
        sys.exit(0)
