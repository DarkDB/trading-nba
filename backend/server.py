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

# ============= NBA DATA SERVICE (nba_api) =============

def get_nba_seasons():
    """Return the fixed seasons for historical data"""
    return ["2021-22", "2022-23", "2023-24", "2024-25"]

async def sync_historical_data_task():
    """Sync historical NBA data - runs in background"""
    from nba_api.stats.endpoints import leaguegamefinder, boxscoretraditionalv2
    from nba_api.stats.static import teams as nba_teams
    import time
    
    seasons = get_nba_seasons()
    all_teams = nba_teams.get_teams()
    team_ids = {t['id']: t['abbreviation'] for t in all_teams}
    
    total_games = 0
    
    for season in seasons:
        logger.info(f"Syncing season {season}...")
        try:
            # Get all games for season
            await asyncio.sleep(1)  # Rate limiting
            gamefinder = leaguegamefinder.LeagueGameFinder(
                season_nullable=season,
                season_type_nullable='Regular Season',
                league_id_nullable='00'
            )
            games_df = gamefinder.get_data_frames()[0]
            
            # Process unique games
            game_ids = games_df['GAME_ID'].unique()
            
            for game_id in game_ids[:100]:  # Limit for MVP
                existing = await db.games.find_one({"game_id": game_id})
                if existing:
                    continue
                
                game_rows = games_df[games_df['GAME_ID'] == game_id]
                if len(game_rows) < 2:
                    continue
                
                # Determine home/away
                home_row = game_rows[game_rows['MATCHUP'].str.contains('vs.')].iloc[0] if len(game_rows[game_rows['MATCHUP'].str.contains('vs.')]) > 0 else game_rows.iloc[0]
                away_row = game_rows[game_rows['MATCHUP'].str.contains('@')].iloc[0] if len(game_rows[game_rows['MATCHUP'].str.contains('@')]) > 0 else game_rows.iloc[1]
                
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
    
    # Group stats by team and date
    team_games = {}
    for game in games:
        for team_key, team_id in [("home", game['home_team_id']), ("away", game['away_team_id'])]:
            if team_id not in team_games:
                team_games[team_id] = []
            team_games[team_id].append(game)
    
    features_count = 0
    
    for game in games:
        game_date = game['game_date']
        home_id = game['home_team_id']
        away_id = game['away_team_id']
        
        # Get last N games for each team BEFORE this game
        home_prev = [g for g in team_games.get(home_id, []) if g['game_date'] < game_date][-N:]
        away_prev = [g for g in team_games.get(away_id, []) if g['game_date'] < game_date][-N:]
        
        if len(home_prev) < 5 or len(away_prev) < 5:
            continue  # Not enough history
        
        # Calculate rolling stats for home team
        home_stats = await calculate_team_rolling_stats(home_id, home_prev)
        away_stats = await calculate_team_rolling_stats(away_id, away_prev)
        
        if not home_stats or not away_stats:
            continue
        
        # Calculate rest days
        home_rest = calculate_rest_days(game_date, home_prev)
        away_rest = calculate_rest_days(game_date, away_prev)
        
        feature_doc = {
            "game_id": game['game_id'],
            "season": game['season'],
            "game_date": game_date,
            "home_team_id": home_id,
            "away_team_id": away_id,
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

async def calculate_team_rolling_stats(team_id: int, prev_games: List[Dict]) -> Optional[Dict]:
    """Calculate rolling advanced stats for a team"""
    if not prev_games:
        return None
    
    # Get team stats for these games
    game_ids = [g['game_id'] for g in prev_games]
    stats = await db.team_game_stats.find({
        "game_id": {"$in": game_ids},
        "team_id": team_id
    }).to_list(100)
    
    if len(stats) < 5:
        return None
    
    # Calculate averages
    total_pts = sum(s['pts'] for s in stats)
    total_fga = sum(s['fga'] for s in stats)
    total_fgm = sum(s['fgm'] for s in stats)
    total_fg3m = sum(s['fg3m'] for s in stats)
    total_fta = sum(s['fta'] for s in stats)
    total_ftm = sum(s['ftm'] for s in stats)
    total_oreb = sum(s['oreb'] for s in stats)
    total_dreb = sum(s['dreb'] for s in stats)
    total_tov = sum(s['tov'] for s in stats)
    n = len(stats)
    
    # Offensive rating estimate (simplified)
    poss = total_fga - total_oreb + total_tov + 0.4 * total_fta
    ortg = (total_pts / poss * 100) if poss > 0 else 100
    
    # Defensive rating estimate - need opponent stats
    # For now, use league average approximation
    drtg = 110  # League average approximation
    
    # eFG%
    efg = ((total_fgm + 0.5 * total_fg3m) / total_fga * 100) if total_fga > 0 else 50
    
    # TOV%
    tov_pct = (total_tov / poss * 100) if poss > 0 else 15
    
    # ORB%
    orb_pct = (total_oreb / (total_oreb + (n * 35))) * 100  # Approximation
    
    # FTr
    ftr = (total_fta / total_fga) if total_fga > 0 else 0.3
    
    # Pace estimate
    pace = poss / n * 2  # Per game possessions
    
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
        return 3  # Default
    
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
    
    # Prepare data
    X_train = np.array([[f.get(col, 0) for col in feature_cols] for f in train_features])
    y_train = np.array([f['margin'] for f in train_features])
    
    X_test = np.array([[f.get(col, 0) for col in feature_cols] for f in test_features]) if test_features else np.array([])
    y_test = np.array([f['margin'] for f in test_features]) if test_features else np.array([])
    
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
    
    test_mae = 0
    test_rmse = 0
    if len(X_test) > 0:
        X_test_scaled = scaler.transform(X_test)
        test_pred = model.predict(X_test_scaled)
        test_mae = mean_absolute_error(y_test, test_pred)
        test_rmse = np.sqrt(mean_squared_error(y_test, test_pred))
    
    # Save model to database (as binary)
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
        "test_samples": len(test_features)
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
        "test_samples": len(test_features)
    }

async def get_active_model():
    """Load active model from database"""
    import joblib
    import io
    
    model_doc = await db.models.find_one({"is_active": True})
    if not model_doc:
        return None
    
    model_data = joblib.load(io.BytesIO(model_doc['model_binary']))
    return model_data

# ============= CREATE APP =============

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting NBA Edge API...")
    yield
    # Shutdown
    client.close()
    logger.info("Shutdown complete")

app = FastAPI(title="NBA Edge API", lifespan=lifespan)
api_router = APIRouter(prefix="/api")

# ============= AUTH ROUTES =============

@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserCreate):
    # Check if email exists
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
        # Run in background
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
            # First ensure event exists
            event_doc = {
                "event_id": event['id'],
                "sport_key": event.get('sport_key', 'basketball_nba'),
                "commence_time": event['commence_time'],
                "home_team": event['home_team'],
                "away_team": event['away_team'],
                "status": "pending",
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await db.upcoming_events.update_one(
                {"event_id": event['id']},
                {"$set": event_doc},
                upsert=True
            )
            
            # Process bookmaker odds
            for bookmaker in event.get('bookmakers', []):
                for market in bookmaker.get('markets', []):
                    if market.get('key') == 'spreads':
                        outcomes = market.get('outcomes', [])
                        if len(outcomes) >= 2:
                            # Find home and away outcomes
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
    # This would fetch completed game scores and update predictions
    # For now, return placeholder
    return SyncStatus(
        status="completed",
        message="Results refresh not yet implemented",
        details={}
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
    """Generate picks using active model"""
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
        
        # For MVP: use simplified features (we don't have real-time team stats)
        # In production, you'd fetch current team rolling stats
        features = {
            "diff_net_rating": 0,  # Would need current team data
            "diff_pace": 0,
            "diff_efg": 0,
            "diff_tov_pct": 0,
            "diff_orb_pct": 0,
            "diff_ftr": 0,
            "diff_rest": 0,
            "home_advantage": 1
        }
        
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
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Save prediction
        await db.predictions.update_one(
            {"user_id": user['id'], "event_id": event['event_id']},
            {"$set": pick},
            upsert=True
        )
        
        picks.append(pick)
    
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
    
    # Calculate stats
    total = len(predictions)
    covered_count = sum(1 for p in predictions if p.get('covered'))
    
    stats = {
        "total": total,
        "covered": covered_count,
        "hit_rate": (covered_count / total * 100) if total > 0 else 0
    }
    
    # Group by signal
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
        "actual_margin", "covered", "reference_bookmaker_used"
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
            "reference_bookmaker_used": p.get('reference_bookmaker_used')
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
    
    # Count by season
    seasons = {}
    for season in get_nba_seasons():
        count = await db.games.count_documents({"season": season})
        seasons[season] = count
    
    return {
        "total_games": games_count,
        "total_features": features_count,
        "by_season": seasons
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
    return {"message": "NBA Edge API", "version": "1.0.0"}

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
