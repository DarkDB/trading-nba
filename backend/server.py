from fastapi import FastAPI, APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
import jwt
import bcrypt
import httpx
import asyncio
from contextlib import asynccontextmanager

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# JWT Config
JWT_SECRET = os.environ.get('JWT_SECRET', 'default_secret')
JWT_ALGORITHM = os.environ.get('JWT_ALGORITHM', 'HS256')
JWT_EXPIRATION_HOURS = int(os.environ.get('JWT_EXPIRATION_HOURS', 24))

# Odds API Config
ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

security = HTTPBearer()

# ============= TEAM MAPPING =============
# Maps full team names (The Odds API) to abbreviations (nba_api)
TEAM_NAME_TO_ABBR = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}

ABBR_TO_TEAM_NAME = {v: k for k, v in TEAM_NAME_TO_ABBR.items()}

def get_team_abbr(full_name: str) -> Optional[str]:
    """Convert full team name to abbreviation"""
    return TEAM_NAME_TO_ABBR.get(full_name)

def get_team_full_name(abbr: str) -> Optional[str]:
    """Convert abbreviation to full team name"""
    return ABBR_TO_TEAM_NAME.get(abbr)

# ============= PYDANTIC MODELS =============

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    created_at: datetime

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

class UpcomingEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_id: str
    sport_key: str = "basketball_nba"
    commence_time: datetime
    home_team: str
    away_team: str
    status: str = "pending"
    
class MarketLine(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_id: str
    bookmaker_key: str
    bookmaker_title: str
    spread_point_home: float
    spread_point_away: float
    price_home_decimal: float
    price_away_decimal: float
    last_update: datetime

class Prediction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime
    reference_bookmaker_used: str
    market_spread_used: float
    pred_margin: float
    edge_points: float
    signal: str  # "green", "yellow", "red"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actual_margin: Optional[float] = None
    covered: Optional[bool] = None

class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    version: str
    mae: float
    rmse: float
    train_seasons: List[str]
    test_season: str
    feature_window: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True

class SyncStatus(BaseModel):
    status: str
    message: str
    details: Optional[Dict[str, Any]] = None

class DebugPrediction(BaseModel):
    event_id: str
    home_team: str
    away_team: str
    home_abbr: Optional[str]
    away_abbr: Optional[str]
    home_games_found: int
    away_games_found: int
    features_raw: Dict[str, float]
    features_scaled: List[float]
    model_id: str
    intercept: float
    coeff_summary: Dict[str, float]
    contributions: Dict[str, float]
    pred_margin: float
    market_spread: Optional[float] = None
    edge_points: Optional[float] = None
    recommended_side: Optional[str] = None
    recommended_bet: Optional[str] = None
    confidence: str
    warnings: List[str]

# ============= AUTH HELPERS =============

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.now(timezone.utc)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = await db.users.find_one({"id": user_id}, {"_id": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ============= ODDS API HELPERS =============

BOOKMAKERS = ["pinnacle", "betfair_ex_eu", "williamhill", "sport888", "matchbook", "betway"]

async def fetch_upcoming_events(days: int = 2) -> List[Dict]:
    """Fetch upcoming NBA events from The Odds API"""
    async with httpx.AsyncClient() as client:
        params = {
            "apiKey": ODDS_API_KEY,
            "dateFormat": "iso"
        }
        try:
            response = await client.get(
                f"{ODDS_API_BASE}/sports/basketball_nba/events",
                params=params,
                timeout=30.0
            )
            if response.status_code == 200:
                events = response.json()
                # Filter to next N days
                now = datetime.now(timezone.utc)
                cutoff = now + timedelta(days=days)
                filtered = []
                for e in events:
                    commence = datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00'))
                    if commence <= cutoff:
                        filtered.append(e)
                return filtered
            else:
                logger.error(f"Odds API error: {response.status_code} - {response.text}")
                return []
        except Exception as e:
            logger.error(f"Error fetching events: {e}")
            return []

async def fetch_odds(days: int = 2) -> List[Dict]:
    """Fetch NBA spread odds from The Odds API"""
    async with httpx.AsyncClient() as client:
        # Try EU region first
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": "spreads",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
            "bookmakers": ",".join(BOOKMAKERS)
        }
        try:
            response = await client.get(
                f"{ODDS_API_BASE}/sports/basketball_nba/odds",
                params=params,
                timeout=30.0
            )
            
            events = []
            if response.status_code == 200:
                events = response.json()
                logger.info(f"Fetched {len(events)} events from EU region")
            
            # If not enough coverage, try UK as fallback
            if len(events) < 3:
                params["regions"] = "uk"
                response_uk = await client.get(
                    f"{ODDS_API_BASE}/sports/basketball_nba/odds",
                    params=params,
                    timeout=30.0
                )
                if response_uk.status_code == 200:
                    uk_events = response_uk.json()
                    # Merge UK events
                    event_ids = {e['id'] for e in events}
                    for e in uk_events:
                        if e['id'] not in event_ids:
                            events.append(e)
                    logger.info(f"Added {len(uk_events)} events from UK region")
            
            # Filter by days
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(days=days)
            filtered = []
            for e in events:
                commence = datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00'))
                if commence <= cutoff:
                    filtered.append(e)
            
            return filtered
        except Exception as e:
            logger.error(f"Error fetching odds: {e}")
            return []

def select_reference_line(lines: List[Dict]) -> Optional[Dict]:
    """Select best reference line: Pinnacle > Betfair > Median"""
    if not lines:
        return None
    
    # Try Pinnacle first
    for line in lines:
        if line['bookmaker_key'] == 'pinnacle':
            return line
    
    # Try Betfair
    for line in lines:
        if line['bookmaker_key'] == 'betfair_ex_eu':
            return line
    
    # Calculate median spread
    spreads = [l['spread_point_home'] for l in lines]
    spreads.sort()
    n = len(spreads)
    if n == 0:
        return lines[0] if lines else None
    median_spread = spreads[n // 2]
    
    # Find line closest to median with price closest to 2.00
    best = None
    best_score = float('inf')
    for line in lines:
        spread_diff = abs(line['spread_point_home'] - median_spread)
        price_diff = abs(line['price_home_decimal'] - 2.0) + abs(line['price_away_decimal'] - 2.0)
        score = spread_diff * 10 + price_diff
        if score < best_score:
            best_score = score
            best = line
    
    return best

def calculate_signal(edge_points: float) -> str:
    """Calculate signal based on edge points"""
    abs_edge = abs(edge_points)
    if abs_edge >= 3.0:
        return "green"
    elif abs_edge >= 2.0:
        return "yellow"
    else:
        return "red"

# ============= FEATURE CALCULATION FOR UPCOMING GAMES =============

async def get_team_rolling_stats(team_abbr: str, before_date: str = None, n: int = 15) -> Optional[Dict]:
    """
    Calculate rolling stats for a team based on their last N games.
    Returns None if not enough historical data.
    """
    if not team_abbr:
        return None
    
    # Get team's games (where they played as home or away)
    query = {
        "$or": [
            {"home_team": team_abbr},
            {"away_team": team_abbr}
        ]
    }
    
    games = await db.games.find(query).sort("game_date", -1).to_list(n * 2)
    
    if len(games) < 5:  # Minimum games required
        logger.warning(f"Team {team_abbr}: only {len(games)} games found, need at least 5")
        return None
    
    # Take last N games
    recent_games = games[:n]
    
    # Get stats for these games
    game_ids = [g['game_id'] for g in recent_games]
    stats = await db.team_game_stats.find({
        "game_id": {"$in": game_ids},
        "team_abbr": team_abbr
    }).to_list(n)
    
    if len(stats) < 5:
        logger.warning(f"Team {team_abbr}: only {len(stats)} stat records found")
        return None
    
    # Calculate aggregated stats
    total_pts = sum(s.get('pts', 0) for s in stats)
    total_fga = sum(s.get('fga', 0) for s in stats)
    total_fgm = sum(s.get('fgm', 0) for s in stats)
    total_fg3m = sum(s.get('fg3m', 0) for s in stats)
    total_fta = sum(s.get('fta', 0) for s in stats)
    total_ftm = sum(s.get('ftm', 0) for s in stats)
    total_oreb = sum(s.get('oreb', 0) for s in stats)
    total_dreb = sum(s.get('dreb', 0) for s in stats)
    total_tov = sum(s.get('tov', 0) for s in stats)
    num_games = len(stats)
    
    # Possessions estimate
    poss = total_fga - total_oreb + total_tov + 0.4 * total_fta
    if poss <= 0:
        poss = num_games * 100  # Fallback
    
    # Offensive rating (points per 100 possessions)
    ortg = (total_pts / poss * 100) if poss > 0 else 100
    
    # For defensive rating, we need opponent stats - use league average approximation
    # In a full implementation, you'd calculate this from opponent data
    drtg = 112  # League average approximation
    
    # eFG% (effective field goal percentage)
    efg = ((total_fgm + 0.5 * total_fg3m) / total_fga * 100) if total_fga > 0 else 50
    
    # TOV% (turnover percentage)
    tov_pct = (total_tov / poss * 100) if poss > 0 else 15
    
    # ORB% approximation
    orb_pct = (total_oreb / (total_oreb + num_games * 35)) * 100 if num_games > 0 else 25
    
    # Free throw rate
    ftr = (total_fta / total_fga) if total_fga > 0 else 0.25
    
    # Pace (possessions per game)
    pace = (poss / num_games) * 2 if num_games > 0 else 100
    
    # Calculate rest days from most recent game
    if recent_games:
        last_game_date = recent_games[0].get('game_date', '')
        try:
            last_date = datetime.strptime(last_game_date, "%Y-%m-%d")
            today = datetime.now()
            rest_days = (today - last_date).days
        except:
            rest_days = 3
    else:
        rest_days = 3
    
    return {
        "net_rating": ortg - drtg,
        "pace": pace,
        "efg": efg,
        "tov_pct": tov_pct,
        "orb_pct": orb_pct,
        "ftr": ftr,
        "rest_days": rest_days,
        "is_b2b": 1 if rest_days == 1 else 0,
        "games_used": num_games,
        "stats_found": len(stats)
    }

async def calculate_matchup_features(home_team: str, away_team: str) -> Dict:
    """
    Calculate features for a matchup between two teams.
    Returns feature dict with confidence level and warnings.
    """
    warnings = []
    
    # Convert team names to abbreviations
    home_abbr = get_team_abbr(home_team)
    away_abbr = get_team_abbr(away_team)
    
    if not home_abbr:
        warnings.append(f"Unknown home team: {home_team}")
    if not away_abbr:
        warnings.append(f"Unknown away team: {away_team}")
    
    # Get rolling stats for each team
    home_stats = await get_team_rolling_stats(home_abbr) if home_abbr else None
    away_stats = await get_team_rolling_stats(away_abbr) if away_abbr else None
    
    # Determine confidence level
    confidence = "high"
    if not home_stats or not away_stats:
        confidence = "low"
        warnings.append("Missing team stats - using league averages")
    elif (home_stats.get('games_used', 0) < 10 or away_stats.get('games_used', 0) < 10):
        confidence = "medium"
        warnings.append(f"Limited data: home={home_stats.get('games_used', 0)}, away={away_stats.get('games_used', 0)} games")
    
    # Default league average stats
    default_stats = {
        "net_rating": 0,
        "pace": 100,
        "efg": 52,
        "tov_pct": 13,
        "orb_pct": 25,
        "ftr": 0.25,
        "rest_days": 2,
        "is_b2b": 0
    }
    
    home_stats = home_stats or default_stats
    away_stats = away_stats or default_stats
    
    # Calculate difference features (home - away)
    features = {
        "diff_net_rating": home_stats['net_rating'] - away_stats['net_rating'],
        "diff_pace": home_stats['pace'] - away_stats['pace'],
        "diff_efg": home_stats['efg'] - away_stats['efg'],
        "diff_tov_pct": home_stats['tov_pct'] - away_stats['tov_pct'],
        "diff_orb_pct": home_stats['orb_pct'] - away_stats['orb_pct'],
        "diff_ftr": home_stats['ftr'] - away_stats['ftr'],
        "diff_rest": home_stats['rest_days'] - away_stats['rest_days'],
        "home_advantage": 1
    }
    
    return {
        "features": features,
        "home_stats": home_stats,
        "away_stats": away_stats,
        "home_abbr": home_abbr,
        "away_abbr": away_abbr,
        "confidence": confidence,
        "warnings": warnings
    }

# ============= NBA DATA SERVICE (nba_api) =============

def get_nba_seasons():
    """Return the fixed seasons for historical data"""
    return ["2021-22", "2022-23", "2023-24", "2024-25"]

async def sync_historical_data_task():
    """Sync historical NBA data - runs in background"""
    from nba_api.stats.endpoints import leaguegamefinder
    import time
    
    seasons = get_nba_seasons()
    total_games = 0
    
    for season in seasons:
        logger.info(f"Syncing season {season}...")
        try:
            await asyncio.sleep(1)  # Rate limiting
            gamefinder = leaguegamefinder.LeagueGameFinder(
                season_nullable=season,
                season_type_nullable='Regular Season',
                league_id_nullable='00'
            )
            games_df = gamefinder.get_data_frames()[0]
            
            # Process unique games
            game_ids = games_df['GAME_ID'].unique()
            
            for game_id in game_ids[:200]:  # Process more games
                existing = await db.games.find_one({"game_id": game_id})
                if existing:
                    continue
                
                game_rows = games_df[games_df['GAME_ID'] == game_id]
                if len(game_rows) < 2:
                    continue
                
                # Determine home/away
                home_rows = game_rows[game_rows['MATCHUP'].str.contains('vs.')]
                away_rows = game_rows[game_rows['MATCHUP'].str.contains('@')]
                
                if len(home_rows) == 0 or len(away_rows) == 0:
                    continue
                    
                home_row = home_rows.iloc[0]
                away_row = away_rows.iloc[0]
                
                game_doc = {
                    "game_id": game_id,
                    "season": season,
                    "game_date": home_row['GAME_DATE'],
                    "home_team_id": int(home_row['TEAM_ID']),
                    "home_team": home_row['TEAM_ABBREVIATION'],
                    "away_team_id": int(away_row['TEAM_ID']),
                    "away_team": away_row['TEAM_ABBREVIATION'],
                    "home_pts": int(home_row['PTS']),
                    "away_pts": int(away_row['PTS']),
                    "margin": int(home_row['PTS']) - int(away_row['PTS'])
                }
                
                await db.games.update_one(
                    {"game_id": game_id},
                    {"$set": game_doc},
                    upsert=True
                )
                
                # Store team stats
                for row in [home_row, away_row]:
                    stat_doc = {
                        "game_id": game_id,
                        "team_id": int(row['TEAM_ID']),
                        "team_abbr": row['TEAM_ABBREVIATION'],
                        "pts": int(row['PTS']),
                        "fgm": int(row['FGM']),
                        "fga": int(row['FGA']),
                        "fg3m": int(row['FG3M']),
                        "fg3a": int(row['FG3A']),
                        "ftm": int(row['FTM']),
                        "fta": int(row['FTA']),
                        "oreb": int(row['OREB']),
                        "dreb": int(row['DREB']),
                        "reb": int(row['REB']),
                        "ast": int(row['AST']),
                        "stl": int(row['STL']),
                        "blk": int(row['BLK']),
                        "tov": int(row['TOV']),
                        "pf": int(row['PF']),
                        "plus_minus": int(row['PLUS_MINUS']) if row['PLUS_MINUS'] else 0
                    }
                    await db.team_game_stats.update_one(
                        {"game_id": game_id, "team_id": stat_doc['team_id']},
                        {"$set": stat_doc},
                        upsert=True
                    )
                
                total_games += 1
                
                if total_games % 50 == 0:
                    logger.info(f"Processed {total_games} games...")
                    await asyncio.sleep(0.5)  # Rate limiting
            
            logger.info(f"Completed season {season}")
            
        except Exception as e:
            logger.error(f"Error syncing season {season}: {e}")
            continue
    
    logger.info(f"Historical sync complete: {total_games} games processed")
    return total_games

async def build_features_task():
    """Build rolling features for all games"""
    N = 15  # Rolling window
    
    games = await db.games.find({}).sort("game_date", 1).to_list(10000)
    
    # Group games by team
    team_games = {}
    for game in games:
        for team_abbr in [game['home_team'], game['away_team']]:
            if team_abbr not in team_games:
                team_games[team_abbr] = []
            team_games[team_abbr].append(game)
    
    features_count = 0
    
    for game in games:
        game_date = game['game_date']
        home_abbr = game['home_team']
        away_abbr = game['away_team']
        
        # Get last N games for each team BEFORE this game (anti-leakage)
        home_prev = [g for g in team_games.get(home_abbr, []) if g['game_date'] < game_date][-N:]
        away_prev = [g for g in team_games.get(away_abbr, []) if g['game_date'] < game_date][-N:]
        
        if len(home_prev) < 5 or len(away_prev) < 5:
            continue  # Not enough history
        
        # Calculate rolling stats for home team
        home_stats = await calculate_team_stats_from_games(home_abbr, home_prev)
        away_stats = await calculate_team_stats_from_games(away_abbr, away_prev)
        
        if not home_stats or not away_stats:
            continue
        
        # Calculate rest days
        home_rest = calculate_rest_days(game_date, home_prev)
        away_rest = calculate_rest_days(game_date, away_prev)
        
        feature_doc = {
            "game_id": game['game_id'],
            "season": game['season'],
            "game_date": game_date,
            "home_team": home_abbr,
            "away_team": away_abbr,
            # Home features
            "home_net_rating": home_stats['net_rating'],
            "home_pace": home_stats['pace'],
            "home_efg": home_stats['efg'],
            "home_tov_pct": home_stats['tov_pct'],
            "home_orb_pct": home_stats['orb_pct'],
            "home_ftr": home_stats['ftr'],
            "home_rest_days": home_rest,
            "home_is_b2b": 1 if home_rest == 1 else 0,
            # Away features  
            "away_net_rating": away_stats['net_rating'],
            "away_pace": away_stats['pace'],
            "away_efg": away_stats['efg'],
            "away_tov_pct": away_stats['tov_pct'],
            "away_orb_pct": away_stats['orb_pct'],
            "away_ftr": away_stats['ftr'],
            "away_rest_days": away_rest,
            "away_is_b2b": 1 if away_rest == 1 else 0,
            # Matchup features (differences)
            "diff_net_rating": home_stats['net_rating'] - away_stats['net_rating'],
            "diff_pace": home_stats['pace'] - away_stats['pace'],
            "diff_efg": home_stats['efg'] - away_stats['efg'],
            "diff_tov_pct": home_stats['tov_pct'] - away_stats['tov_pct'],
            "diff_orb_pct": home_stats['orb_pct'] - away_stats['orb_pct'],
            "diff_ftr": home_stats['ftr'] - away_stats['ftr'],
            "diff_rest": home_rest - away_rest,
            "home_advantage": 1,
            # Target
            "margin": game['margin']
        }
        
        await db.game_features.update_one(
            {"game_id": game['game_id']},
            {"$set": feature_doc},
            upsert=True
        )
        features_count += 1
    
    logger.info(f"Built features for {features_count} games")
    return features_count

async def calculate_team_stats_from_games(team_abbr: str, prev_games: List[Dict]) -> Optional[Dict]:
    """Calculate rolling advanced stats for a team from previous games"""
    if not prev_games:
        return None
    
    # Get team stats for these games
    game_ids = [g['game_id'] for g in prev_games]
    stats = await db.team_game_stats.find({
        "game_id": {"$in": game_ids},
        "team_abbr": team_abbr
    }).to_list(100)
    
    if len(stats) < 5:
        return None
    
    # Calculate averages
    total_pts = sum(s['pts'] for s in stats)
    total_fga = sum(s['fga'] for s in stats)
    total_fgm = sum(s['fgm'] for s in stats)
    total_fg3m = sum(s['fg3m'] for s in stats)
    total_fta = sum(s['fta'] for s in stats)
    total_oreb = sum(s['oreb'] for s in stats)
    total_tov = sum(s['tov'] for s in stats)
    n = len(stats)
    
    # Possessions estimate
    poss = total_fga - total_oreb + total_tov + 0.4 * total_fta
    if poss <= 0:
        poss = n * 100
    
    ortg = (total_pts / poss * 100) if poss > 0 else 100
    drtg = 112  # League average approximation
    efg = ((total_fgm + 0.5 * total_fg3m) / total_fga * 100) if total_fga > 0 else 50
    tov_pct = (total_tov / poss * 100) if poss > 0 else 15
    orb_pct = (total_oreb / (total_oreb + n * 35)) * 100
    ftr = (total_fta / total_fga) if total_fga > 0 else 0.25
    pace = (poss / n) * 2 if n > 0 else 100
    
    return {
        "net_rating": ortg - drtg,
        "pace": pace,
        "efg": efg,
        "tov_pct": tov_pct,
        "orb_pct": orb_pct,
        "ftr": ftr
    }

def calculate_rest_days(game_date: str, prev_games: List[Dict]) -> int:
    """Calculate days since last game"""
    if not prev_games:
        return 3
    
    last_game = prev_games[-1]
    try:
        current = datetime.strptime(game_date, "%Y-%m-%d")
        last = datetime.strptime(last_game['game_date'], "%Y-%m-%d")
        return (current - last).days
    except:
        return 3

# ============= MODEL TRAINING =============

async def train_model_task():
    """Train Ridge Regression model"""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    import numpy as np
    import joblib
    import io
    
    # Get features
    train_seasons = ["2021-22", "2022-23", "2023-24"]
    test_season = "2024-25"
    
    train_features = await db.game_features.find({
        "season": {"$in": train_seasons}
    }).to_list(10000)
    
    test_features = await db.game_features.find({
        "season": test_season
    }).to_list(10000)
    
    if len(train_features) < 100:
        return {"error": "Not enough training data", "train_count": len(train_features)}
    
    feature_cols = [
        "diff_net_rating", "diff_pace", "diff_efg", "diff_tov_pct",
        "diff_orb_pct", "diff_ftr", "diff_rest", "home_advantage"
    ]
    
    # Prepare data - check for NaNs
    X_train = []
    y_train = []
    nan_count = 0
    
    for f in train_features:
        row = []
        has_nan = False
        for col in feature_cols:
            val = f.get(col, 0)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                has_nan = True
                nan_count += 1
                val = 0
            row.append(val)
        X_train.append(row)
        y_train.append(f['margin'])
    
    X_train = np.array(X_train)
    y_train = np.array(y_train)
    
    logger.info(f"Training data: {len(X_train)} samples, {nan_count} NaN values replaced")
    
    # Prepare test data
    X_test = []
    y_test = []
    for f in test_features:
        row = []
        for col in feature_cols:
            val = f.get(col, 0)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                val = 0
            row.append(val)
        X_test.append(row)
        y_test.append(f['margin'])
    
    X_test = np.array(X_test) if test_features else np.array([]).reshape(0, len(feature_cols))
    y_test = np.array(y_test) if test_features else np.array([])
    
    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    
    # Train model
    model = Ridge(alpha=1.0)
    model.fit(X_train_scaled, y_train)
    
    # Evaluate
    train_pred = model.predict(X_train_scaled)
    train_mae = mean_absolute_error(y_train, train_pred)
    train_rmse = np.sqrt(mean_squared_error(y_train, train_pred))
    
    # Check prediction variance
    train_pred_std = np.std(train_pred)
    logger.info(f"Train predictions: min={np.min(train_pred):.2f}, max={np.max(train_pred):.2f}, std={train_pred_std:.2f}")
    
    test_mae = 0
    test_rmse = 0
    test_pred_std = 0
    if len(X_test) > 0:
        X_test_scaled = scaler.transform(X_test)
        test_pred = model.predict(X_test_scaled)
        test_mae = mean_absolute_error(y_test, test_pred)
        test_rmse = np.sqrt(mean_squared_error(y_test, test_pred))
        test_pred_std = np.std(test_pred)
        logger.info(f"Test predictions: min={np.min(test_pred):.2f}, max={np.max(test_pred):.2f}, std={test_pred_std:.2f}")
    
    # Save model to database
    model_buffer = io.BytesIO()
    joblib.dump({"model": model, "scaler": scaler, "features": feature_cols}, model_buffer)
    model_bytes = model_buffer.getvalue()
    
    model_doc = {
        "id": str(uuid.uuid4()),
        "name": "ridge_v1",
        "version": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "mae": test_mae if test_mae > 0 else train_mae,
        "rmse": test_rmse if test_rmse > 0 else train_rmse,
        "train_mae": train_mae,
        "train_rmse": train_rmse,
        "train_seasons": train_seasons,
        "test_season": test_season,
        "feature_window": 15,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_active": True,
        "model_binary": model_bytes,
        "train_samples": len(train_features),
        "test_samples": len(test_features),
        "nan_values_replaced": nan_count,
        "train_pred_std": float(train_pred_std),
        "test_pred_std": float(test_pred_std) if test_pred_std else 0,
        "intercept": float(model.intercept_),
        "coefficients": {col: float(coef) for col, coef in zip(feature_cols, model.coef_)}
    }
    
    # Deactivate other models
    await db.models.update_many({}, {"$set": {"is_active": False}})
    
    # Save new model
    await db.models.insert_one(model_doc)
    
    return {
        "model_id": model_doc['id'],
        "mae": model_doc['mae'],
        "rmse": model_doc['rmse'],
        "train_mae": train_mae,
        "train_rmse": train_rmse,
        "train_samples": len(train_features),
        "test_samples": len(test_features),
        "train_pred_std": float(train_pred_std),
        "test_pred_std": float(test_pred_std) if test_pred_std else 0
    }

async def get_active_model():
    """Load active model from database"""
    import joblib
    import io
    
    model_doc = await db.models.find_one({"is_active": True})
    if not model_doc:
        return None
    
    model_data = joblib.load(io.BytesIO(model_doc['model_binary']))
    model_data['model_id'] = model_doc['id']
    model_data['intercept'] = model_doc.get('intercept', 0)
    model_data['coefficients'] = model_doc.get('coefficients', {})
    return model_data

# ============= CREATE APP =============

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting NBA Edge API...")
    yield
    client.close()
    logger.info("Shutdown complete")

app = FastAPI(title="NBA Edge API", lifespan=lifespan)
api_router = APIRouter(prefix="/api")

# ============= AUTH ROUTES =============

@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserCreate):
    existing = await db.users.find_one({"email": user_data.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    user_id = str(uuid.uuid4())
    user_doc = {
        "id": user_id,
        "email": user_data.email,
        "name": user_data.name,
        "password_hash": hash_password(user_data.password),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.users.insert_one(user_doc)
    token = create_token(user_id, user_data.email)
    
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user_id,
            email=user_data.email,
            name=user_data.name,
            created_at=datetime.fromisoformat(user_doc['created_at'])
        )
    )

@api_router.post("/auth/login", response_model=TokenResponse)
async def login(credentials: UserLogin):
    user = await db.users.find_one({"email": credentials.email})
    if not user or not verify_password(credentials.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_token(user['id'], user['email'])
    
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user['id'],
            email=user['email'],
            name=user['name'],
            created_at=datetime.fromisoformat(user['created_at']) if isinstance(user['created_at'], str) else user['created_at']
        )
    )

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(user = Depends(get_current_user)):
    return UserResponse(
        id=user['id'],
        email=user['email'],
        name=user['name'],
        created_at=datetime.fromisoformat(user['created_at']) if isinstance(user['created_at'], str) else user['created_at']
    )

# ============= ADMIN ROUTES =============

@api_router.post("/admin/sync-historical", response_model=SyncStatus)
async def sync_historical(user = Depends(get_current_user)):
    """Sync historical NBA data (seasons 2021-25)"""
    try:
        asyncio.create_task(sync_historical_data_task())
        return SyncStatus(
            status="started",
            message="Historical sync started in background. This may take several minutes.",
            details={"seasons": get_nba_seasons()}
        )
    except Exception as e:
        logger.error(f"Sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/admin/build-features", response_model=SyncStatus)
async def build_features(user = Depends(get_current_user)):
    """Build features from historical data"""
    try:
        count = await build_features_task()
        return SyncStatus(
            status="completed",
            message=f"Built features for {count} games",
            details={"feature_count": count, "window": 15}
        )
    except Exception as e:
        logger.error(f"Feature build error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/admin/train", response_model=SyncStatus)
async def train_model(user = Depends(get_current_user)):
    """Train prediction model"""
    try:
        result = await train_model_task()
        if "error" in result:
            return SyncStatus(
                status="error",
                message=result['error'],
                details=result
            )
        return SyncStatus(
            status="completed",
            message=f"Model trained successfully. MAE: {result['mae']:.2f}, RMSE: {result['rmse']:.2f}",
            details=result
        )
    except Exception as e:
        logger.error(f"Training error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/admin/sync-upcoming", response_model=SyncStatus)
async def sync_upcoming(days: int = 2, user = Depends(get_current_user)):
    """Sync upcoming NBA events"""
    try:
        events = await fetch_upcoming_events(days)
        
        for event in events:
            event_doc = {
                "event_id": event['id'],
                "sport_key": event.get('sport_key', 'basketball_nba'),
                "commence_time": event['commence_time'],
                "home_team": event['home_team'],
                "away_team": event['away_team'],
                "home_team_abbr": get_team_abbr(event['home_team']),
                "away_team_abbr": get_team_abbr(event['away_team']),
                "status": "pending",
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await db.upcoming_events.update_one(
                {"event_id": event['id']},
                {"$set": event_doc},
                upsert=True
            )
        
        return SyncStatus(
            status="completed",
            message=f"Synced {len(events)} upcoming events",
            details={"event_count": len(events), "days": days}
        )
    except Exception as e:
        logger.error(f"Upcoming sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/admin/sync-odds", response_model=SyncStatus)
async def sync_odds(days: int = 2, user = Depends(get_current_user)):
    """Sync spread odds for upcoming events"""
    try:
        events = await fetch_odds(days)
        lines_count = 0
        
        for event in events:
            event_doc = {
                "event_id": event['id'],
                "sport_key": event.get('sport_key', 'basketball_nba'),
                "commence_time": event['commence_time'],
                "home_team": event['home_team'],
                "away_team": event['away_team'],
                "home_team_abbr": get_team_abbr(event['home_team']),
                "away_team_abbr": get_team_abbr(event['away_team']),
                "status": "pending",
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await db.upcoming_events.update_one(
                {"event_id": event['id']},
                {"$set": event_doc},
                upsert=True
            )
            
            for bookmaker in event.get('bookmakers', []):
                for market in bookmaker.get('markets', []):
                    if market.get('key') == 'spreads':
                        outcomes = market.get('outcomes', [])
                        if len(outcomes) >= 2:
                            home_outcome = next((o for o in outcomes if o['name'] == event['home_team']), outcomes[0])
                            away_outcome = next((o for o in outcomes if o['name'] == event['away_team']), outcomes[1])
                            
                            line_doc = {
                                "event_id": event['id'],
                                "bookmaker_key": bookmaker['key'],
                                "bookmaker_title": bookmaker.get('title', bookmaker['key']),
                                "spread_point_home": home_outcome.get('point', 0),
                                "spread_point_away": away_outcome.get('point', 0),
                                "price_home_decimal": home_outcome.get('price', 1.91),
                                "price_away_decimal": away_outcome.get('price', 1.91),
                                "last_update": bookmaker.get('last_update', datetime.now(timezone.utc).isoformat())
                            }
                            
                            await db.market_lines.update_one(
                                {"event_id": event['id'], "bookmaker_key": bookmaker['key']},
                                {"$set": line_doc},
                                upsert=True
                            )
                            lines_count += 1
        
        return SyncStatus(
            status="completed",
            message=f"Synced {lines_count} market lines for {len(events)} events",
            details={"events": len(events), "lines": lines_count}
        )
    except Exception as e:
        logger.error(f"Odds sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/admin/refresh-results", response_model=SyncStatus)
async def refresh_results(user = Depends(get_current_user)):
    """Update predictions with actual results"""
    return SyncStatus(
        status="completed",
        message="Results refresh not yet implemented",
        details={}
    )

# ============= DEBUG ENDPOINT =============

@api_router.get("/admin/debug/predict", response_model=DebugPrediction)
async def debug_predict(event_id: str, user = Depends(get_current_user)):
    """Debug endpoint to see full prediction details for an event"""
    import numpy as np
    
    # Get event
    event = await db.upcoming_events.find_one({"event_id": event_id}, {"_id": 0})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Load model
    model_data = await get_active_model()
    if not model_data:
        raise HTTPException(status_code=400, detail="No trained model available")
    
    model = model_data['model']
    scaler = model_data['scaler']
    feature_cols = model_data['features']
    
    # Calculate features
    matchup_data = await calculate_matchup_features(event['home_team'], event['away_team'])
    features = matchup_data['features']
    
    # Count games found
    home_abbr = matchup_data['home_abbr']
    away_abbr = matchup_data['away_abbr']
    
    home_games = await db.games.count_documents({"$or": [{"home_team": home_abbr}, {"away_team": home_abbr}]}) if home_abbr else 0
    away_games = await db.games.count_documents({"$or": [{"home_team": away_abbr}, {"away_team": away_abbr}]}) if away_abbr else 0
    
    # Build feature vector
    X = np.array([[features.get(col, 0) for col in feature_cols]])
    X_scaled = scaler.transform(X)
    pred_margin = float(model.predict(X_scaled)[0])
    
    # Calculate contributions
    contributions = {"intercept": float(model.intercept_)}
    for i, (col, coef) in enumerate(zip(feature_cols, model.coef_)):
        contributions[col] = float(coef * X_scaled[0][i])
    
    # Get market line and calculate edge
    lines = await db.market_lines.find({"event_id": event_id}, {"_id": 0}).to_list(10)
    ref_line = select_reference_line(lines)
    
    market_spread = None
    edge_points = None
    recommended_side = None
    recommended_bet = None
    
    if ref_line:
        market_spread = ref_line['spread_point_home']
        edge_points = pred_margin - market_spread
        
        if edge_points > 0:
            recommended_side = "HOME"
            recommended_bet = f"{home_abbr or event['home_team'][:3].upper()} {market_spread:+.1f}"
        else:
            recommended_side = "AWAY"
            away_spread = -market_spread
            recommended_bet = f"{away_abbr or event['away_team'][:3].upper()} {away_spread:+.1f}"
    
    return DebugPrediction(
        event_id=event_id,
        home_team=event['home_team'],
        away_team=event['away_team'],
        home_abbr=home_abbr,
        away_abbr=away_abbr,
        home_games_found=home_games,
        away_games_found=away_games,
        features_raw=features,
        features_scaled=X_scaled[0].tolist(),
        model_id=model_data['model_id'],
        intercept=float(model.intercept_),
        coeff_summary={col: float(coef) for col, coef in zip(feature_cols, model.coef_)},
        contributions=contributions,
        pred_margin=pred_margin,
        market_spread=market_spread,
        edge_points=edge_points,
        recommended_side=recommended_side,
        recommended_bet=recommended_bet,
        confidence=matchup_data['confidence'],
        warnings=matchup_data['warnings']
    )

# ============= USER ROUTES =============

@api_router.get("/upcoming")
async def get_upcoming(user = Depends(get_current_user)):
    """Get upcoming events with market lines"""
    events = await db.upcoming_events.find(
        {"status": "pending"},
        {"_id": 0}
    ).sort("commence_time", 1).to_list(50)
    
    result = []
    for event in events:
        lines = await db.market_lines.find(
            {"event_id": event['event_id']},
            {"_id": 0}
        ).to_list(20)
        
        reference_line = select_reference_line(lines)
        
        result.append({
            **event,
            "lines": lines,
            "reference_line": reference_line
        })
    
    return {"events": result, "count": len(result)}

@api_router.post("/picks/generate")
async def generate_picks(user = Depends(get_current_user)):
    """Generate picks using active model with REAL features"""
    import numpy as np
    
    # Load model
    model_data = await get_active_model()
    if not model_data:
        raise HTTPException(status_code=400, detail="No trained model available. Please train a model first.")
    
    model = model_data['model']
    scaler = model_data['scaler']
    feature_cols = model_data['features']
    
    # Get upcoming events with lines
    events = await db.upcoming_events.find(
        {"status": "pending"},
        {"_id": 0}
    ).to_list(50)
    
    picks = []
    for event in events:
        lines = await db.market_lines.find(
            {"event_id": event['event_id']},
            {"_id": 0}
        ).to_list(20)
        
        if not lines:
            continue
        
        reference_line = select_reference_line(lines)
        if not reference_line:
            continue
        
        # Calculate REAL features for this matchup
        matchup_data = await calculate_matchup_features(event['home_team'], event['away_team'])
        features = matchup_data['features']
        
        # Make prediction
        X = np.array([[features.get(col, 0) for col in feature_cols]])
        X_scaled = scaler.transform(X)
        pred_margin = float(model.predict(X_scaled)[0])
        
        market_spread = reference_line['spread_point_home']
        edge_points = pred_margin - market_spread
        signal = calculate_signal(edge_points)
        
        pick = {
            "id": str(uuid.uuid4()),
            "user_id": user['id'],
            "event_id": event['event_id'],
            "home_team": event['home_team'],
            "away_team": event['away_team'],
            "commence_time": event['commence_time'],
            "reference_bookmaker_used": reference_line['bookmaker_key'],
            "market_spread_used": market_spread,
            "pred_margin": round(pred_margin, 2),
            "edge_points": round(edge_points, 2),
            "signal": signal,
            "confidence": matchup_data['confidence'],
            "warnings": matchup_data['warnings'],
            "features_used": {k: round(v, 4) for k, v in features.items()},
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Save prediction
        await db.predictions.update_one(
            {"user_id": user['id'], "event_id": event['event_id']},
            {"$set": pick},
            upsert=True
        )
        
        picks.append(pick)
    
    # Log prediction variance for debugging
    if picks:
        pred_margins = [p['pred_margin'] for p in picks]
        logger.info(f"Generated {len(picks)} picks. Pred margins: min={min(pred_margins):.2f}, max={max(pred_margins):.2f}, unique={len(set(pred_margins))}")
    
    return {"picks": picks, "count": len(picks)}

@api_router.get("/picks")
async def get_picks(user = Depends(get_current_user)):
    """Get user's current picks"""
    picks = await db.predictions.find(
        {"user_id": user['id']},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    
    return {"picks": picks}

@api_router.get("/history")
async def get_history(
    signal: Optional[str] = None,
    covered: Optional[bool] = None,
    user = Depends(get_current_user)
):
    """Get prediction history with optional filters"""
    query = {"user_id": user['id'], "actual_margin": {"$ne": None}}
    
    if signal:
        query["signal"] = signal
    if covered is not None:
        query["covered"] = covered
    
    predictions = await db.predictions.find(
        query,
        {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    
    total = len(predictions)
    covered_count = sum(1 for p in predictions if p.get('covered'))
    
    stats = {
        "total": total,
        "covered": covered_count,
        "hit_rate": (covered_count / total * 100) if total > 0 else 0
    }
    
    by_signal = {}
    for p in predictions:
        s = p.get('signal', 'unknown')
        if s not in by_signal:
            by_signal[s] = {"total": 0, "covered": 0}
        by_signal[s]["total"] += 1
        if p.get('covered'):
            by_signal[s]["covered"] += 1
    
    return {
        "predictions": predictions,
        "stats": stats,
        "by_signal": by_signal
    }

@api_router.get("/history/export")
async def export_history(user = Depends(get_current_user)):
    """Export history as CSV"""
    from fastapi.responses import StreamingResponse
    import csv
    import io
    
    predictions = await db.predictions.find(
        {"user_id": user['id']},
        {"_id": 0}
    ).sort("created_at", -1).to_list(1000)
    
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "created_at", "home_team", "away_team", "commence_time",
        "pred_margin", "market_spread_used", "edge_points", "signal",
        "actual_margin", "covered", "reference_bookmaker_used", "confidence"
    ])
    writer.writeheader()
    
    for p in predictions:
        writer.writerow({
            "created_at": p.get('created_at'),
            "home_team": p.get('home_team'),
            "away_team": p.get('away_team'),
            "commence_time": p.get('commence_time'),
            "pred_margin": p.get('pred_margin'),
            "market_spread_used": p.get('market_spread_used'),
            "edge_points": p.get('edge_points'),
            "signal": p.get('signal'),
            "actual_margin": p.get('actual_margin'),
            "covered": p.get('covered'),
            "reference_bookmaker_used": p.get('reference_bookmaker_used'),
            "confidence": p.get('confidence', 'unknown')
        })
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nba_edge_history.csv"}
    )

# ============= STATS ROUTES =============

@api_router.get("/stats/dataset")
async def get_dataset_stats(user = Depends(get_current_user)):
    """Get dataset statistics"""
    games_count = await db.games.count_documents({})
    features_count = await db.game_features.count_documents({})
    
    seasons = {}
    for season in get_nba_seasons():
        count = await db.games.count_documents({"season": season})
        seasons[season] = count
    
    # Team coverage
    teams = await db.games.distinct("home_team")
    
    return {
        "total_games": games_count,
        "total_features": features_count,
        "by_season": seasons,
        "teams_count": len(teams),
        "teams": sorted(teams)
    }

@api_router.get("/stats/model")
async def get_model_stats(user = Depends(get_current_user)):
    """Get active model statistics"""
    model = await db.models.find_one({"is_active": True}, {"_id": 0, "model_binary": 0})
    if not model:
        return {"active_model": None}
    return {"active_model": model}

# ============= ROOT =============

@api_router.get("/")
async def root():
    return {"message": "NBA Edge API", "version": "1.1.0"}

@api_router.get("/health")
async def health():
    return {"status": "healthy"}

# Include router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
