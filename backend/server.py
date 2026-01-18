from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, Query
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
import hashlib
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

# ============= OPERATIONAL CONFIG (V1.0) =============
OPERATIONAL_CONFIG = {
    "version": "1.1",
    "rolling_window_n": 15,
    "feature_list": ["diff_net_rating", "diff_pace", "diff_efg", "diff_tov_pct", 
                     "diff_orb_pct", "diff_ftr", "diff_rest", "home_advantage"],
    "algorithm": "Ridge",
    "alpha": 1.0,
    "signal_thresholds": {
        "green": 3.0,
        "yellow": 2.0
    },
    "operative_thresholds": {
        "min_edge": 3.5,
        "max_picks_per_day": None,  # None = sin límite, muestra todos los válidos
        "require_high_confidence": True,
        "require_green_signal": True,
        "require_pinnacle": True
    },
    "train_seasons": ["2021-22", "2022-23", "2023-24"],
    "test_season": "2024-25",
    "spread_convention": "HOME_PERSPECTIVE_SIGNED"
}

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

security = HTTPBearer()

# ============= TEAM MAPPING =============
TEAM_NAME_TO_ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}
ABBR_TO_TEAM_NAME = {v: k for k, v in TEAM_NAME_TO_ABBR.items()}

def get_team_abbr(full_name: str) -> Optional[str]:
    return TEAM_NAME_TO_ABBR.get(full_name)

def get_team_full_name(abbr: str) -> Optional[str]:
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

class SyncStatus(BaseModel):
    status: str
    message: str
    details: Optional[Dict[str, Any]] = None

class ConfigSnapshot(BaseModel):
    rolling_window_n: int
    feature_list: List[str]
    algorithm: str
    alpha: float
    signal_thresholds: Dict[str, float]
    operative_thresholds: Dict[str, Any]
    train_seasons: List[str]
    test_season: str
    spread_convention: str

class ModelMetrics(BaseModel):
    mae: float
    rmse: float
    train_mae: float
    train_rmse: float
    error_percentiles: Dict[str, float]
    pred_std_train: float
    pred_std_test: float

class OperativePick(BaseModel):
    id: str
    event_id: str
    home_team: str
    away_team: str
    home_abbr: Optional[str]
    away_abbr: Optional[str]
    commence_time: str
    commence_time_local: str  # Europe/Madrid
    # Model prediction
    pred_margin: float
    # Market data
    open_spread: float
    open_price: float
    open_ts: str
    # Calculated edge
    edge_points: float
    signal: str
    confidence: str
    # Recommendation
    recommended_side: str
    recommended_bet_string: str
    explanation: str
    # Operational flags
    do_not_bet: bool
    do_not_bet_reason: Optional[str]
    # Model version
    model_id: str
    model_version: str
    # Close line (populated later)
    close_spread: Optional[float] = None
    close_price: Optional[float] = None
    close_ts: Optional[str] = None
    clv_spread: Optional[float] = None

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
    model_version: str
    intercept: float
    coeff_summary: Dict[str, float]
    contributions: Dict[str, float]
    pred_margin: float
    market_spread: Optional[float] = None
    edge_points: Optional[float] = None
    recommended_side: Optional[str] = None
    recommended_bet: Optional[str] = None
    explanation: Optional[str] = None
    confidence: str
    do_not_bet: bool
    do_not_bet_reason: Optional[str]
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
    async with httpx.AsyncClient() as http_client:
        params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso"}
        try:
            response = await http_client.get(
                f"{ODDS_API_BASE}/sports/basketball_nba/events",
                params=params, timeout=30.0
            )
            if response.status_code == 200:
                events = response.json()
                now = datetime.now(timezone.utc)
                cutoff = now + timedelta(days=days)
                return [e for e in events 
                        if datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00')) <= cutoff]
            return []
        except Exception as e:
            logger.error(f"Error fetching events: {e}")
            return []

async def fetch_odds(days: int = 2) -> List[Dict]:
    async with httpx.AsyncClient() as http_client:
        params = {
            "apiKey": ODDS_API_KEY, "regions": "eu", "markets": "spreads",
            "oddsFormat": "decimal", "dateFormat": "iso",
            "bookmakers": ",".join(BOOKMAKERS)
        }
        try:
            response = await http_client.get(
                f"{ODDS_API_BASE}/sports/basketball_nba/odds",
                params=params, timeout=30.0
            )
            events = response.json() if response.status_code == 200 else []
            
            if len(events) < 3:
                params["regions"] = "uk"
                response_uk = await http_client.get(
                    f"{ODDS_API_BASE}/sports/basketball_nba/odds",
                    params=params, timeout=30.0
                )
                if response_uk.status_code == 200:
                    uk_events = response_uk.json()
                    event_ids = {e['id'] for e in events}
                    events.extend([e for e in uk_events if e['id'] not in event_ids])
            
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(days=days)
            return [e for e in events 
                    if datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00')) <= cutoff]
        except Exception as e:
            logger.error(f"Error fetching odds: {e}")
            return []

def select_reference_line(lines: List[Dict], require_pinnacle: bool = True) -> Optional[Dict]:
    """Select Pinnacle first, then Betfair, then median"""
    if not lines:
        return None
    
    for line in lines:
        if line.get('bookmaker_key') == 'pinnacle':
            return line
    
    if require_pinnacle:
        return None  # No Pinnacle = no bet
    
    for line in lines:
        if line.get('bookmaker_key') == 'betfair_ex_eu':
            return line
    
    spreads = sorted([l['spread_point_home'] for l in lines])
    median_spread = spreads[len(spreads) // 2]
    
    best = min(lines, key=lambda l: abs(l['spread_point_home'] - median_spread) * 10 
               + abs(l['price_home_decimal'] - 2.0))
    return best

def calculate_signal(edge_points: float) -> str:
    """Calculate signal based on edge. Edge should always be positive."""
    # Edge is now always positive (advantage on the recommended side)
    if edge_points >= OPERATIONAL_CONFIG["signal_thresholds"]["green"]:
        return "green"
    elif edge_points >= OPERATIONAL_CONFIG["signal_thresholds"]["yellow"]:
        return "yellow"
    return "red"

def format_local_time(dt_str: str) -> str:
    """Convert to Europe/Madrid local time string"""
    try:
        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        # Simple offset for CET/CEST (±1-2h from UTC)
        madrid_offset = timedelta(hours=1)  # CET
        local_dt = dt + madrid_offset
        return local_dt.strftime("%d/%m %H:%M")
    except:
        return dt_str

def generate_explanation(home_team: str, away_team: str, home_abbr: str, away_abbr: str,
                        pred_margin: float, market_spread: float, edge_points: float,
                        recommended_side: str, confidence: str, model_version: str) -> str:
    """Generate clear explanation for the pick"""
    if recommended_side == "HOME":
        team_abbr = home_abbr or home_team[:3].upper()
        spread_str = f"{market_spread:+.1f}" if market_spread != 0 else "PK"
        bet_str = f"{team_abbr} {spread_str}"
    else:
        team_abbr = away_abbr or away_team[:3].upper()
        away_spread = -market_spread
        spread_str = f"{away_spread:+.1f}" if away_spread != 0 else "PK"
        bet_str = f"{team_abbr} {spread_str}"
    
    return (f"Pick: {recommended_side} ({bet_str}). "
            f"pred_margin={pred_margin:+.1f}, market_spread={market_spread:+.1f} => edge={edge_points:+.1f}. "
            f"confidence={confidence.upper()}. model_version={model_version}")

def generate_recommended_bet_string(home_team: str, away_team: str, home_abbr: str, away_abbr: str,
                                    market_spread: float, recommended_side: str) -> str:
    """Generate clear bet string like 'LAL -4.5' or 'MEM +5.5'"""
    if recommended_side == "HOME":
        team = home_abbr or home_team[:3].upper()
        spread = market_spread
    else:
        team = away_abbr or away_team[:3].upper()
        spread = -market_spread  # Flip for away perspective
    
    spread_str = f"{spread:+.1f}" if spread != 0 else "PK"
    return f"{team} {spread_str}"

# ============= FEATURE CALCULATION =============

async def get_team_rolling_stats(team_abbr: str, n: int = 15) -> Optional[Dict]:
    if not team_abbr:
        return None
    
    games = await db.games.find({
        "$or": [{"home_team": team_abbr}, {"away_team": team_abbr}]
    }).sort("game_date", -1).to_list(n * 2)
    
    if len(games) < 5:
        return None
    
    recent_games = games[:n]
    game_ids = [g['game_id'] for g in recent_games]
    stats = await db.team_game_stats.find({
        "game_id": {"$in": game_ids}, "team_abbr": team_abbr
    }).to_list(n)
    
    if len(stats) < 5:
        return None
    
    total_pts = sum(s.get('pts', 0) for s in stats)
    total_fga = sum(s.get('fga', 0) for s in stats)
    total_fgm = sum(s.get('fgm', 0) for s in stats)
    total_fg3m = sum(s.get('fg3m', 0) for s in stats)
    total_fta = sum(s.get('fta', 0) for s in stats)
    total_oreb = sum(s.get('oreb', 0) for s in stats)
    total_tov = sum(s.get('tov', 0) for s in stats)
    num_games = len(stats)
    
    poss = max(total_fga - total_oreb + total_tov + 0.4 * total_fta, num_games * 100)
    ortg = (total_pts / poss * 100) if poss > 0 else 100
    drtg = 112
    efg = ((total_fgm + 0.5 * total_fg3m) / total_fga * 100) if total_fga > 0 else 50
    tov_pct = (total_tov / poss * 100) if poss > 0 else 15
    orb_pct = (total_oreb / (total_oreb + num_games * 35)) * 100 if num_games > 0 else 25
    ftr = (total_fta / total_fga) if total_fga > 0 else 0.25
    pace = (poss / num_games) * 2 if num_games > 0 else 100
    
    rest_days = 3
    if recent_games:
        try:
            last_date = datetime.strptime(recent_games[0].get('game_date', ''), "%Y-%m-%d")
            rest_days = (datetime.now() - last_date).days
        except:
            pass
    
    return {
        "net_rating": ortg - drtg, "pace": pace, "efg": efg, "tov_pct": tov_pct,
        "orb_pct": orb_pct, "ftr": ftr, "rest_days": rest_days,
        "is_b2b": 1 if rest_days == 1 else 0, "games_used": num_games
    }

async def calculate_matchup_features(home_team: str, away_team: str) -> Dict:
    warnings = []
    home_abbr = get_team_abbr(home_team)
    away_abbr = get_team_abbr(away_team)
    
    if not home_abbr:
        warnings.append(f"Unknown home team: {home_team}")
    if not away_abbr:
        warnings.append(f"Unknown away team: {away_team}")
    
    home_stats = await get_team_rolling_stats(home_abbr) if home_abbr else None
    away_stats = await get_team_rolling_stats(away_abbr) if away_abbr else None
    
    confidence = "high"
    if not home_stats or not away_stats:
        confidence = "low"
        warnings.append("Missing team stats - using league averages")
    elif home_stats.get('games_used', 0) < 15 or away_stats.get('games_used', 0) < 15:
        if home_stats.get('games_used', 0) < 10 or away_stats.get('games_used', 0) < 10:
            confidence = "low"
        else:
            confidence = "medium"
        warnings.append(f"Limited data: home={home_stats.get('games_used', 0)}, away={away_stats.get('games_used', 0)} games")
    
    default_stats = {"net_rating": 0, "pace": 100, "efg": 52, "tov_pct": 13,
                     "orb_pct": 25, "ftr": 0.25, "rest_days": 2, "is_b2b": 0}
    
    home_stats = home_stats or default_stats
    away_stats = away_stats or default_stats
    
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
        "features": features, "home_stats": home_stats, "away_stats": away_stats,
        "home_abbr": home_abbr, "away_abbr": away_abbr,
        "confidence": confidence, "warnings": warnings
    }

# ============= MODEL FUNCTIONS =============

def get_nba_seasons():
    return ["2021-22", "2022-23", "2023-24", "2024-25"]

async def get_data_cutoff_date() -> str:
    """Get the latest game date in the database"""
    latest = await db.games.find_one({}, sort=[("game_date", -1)])
    return latest['game_date'] if latest else "unknown"

def generate_model_version() -> str:
    """Generate unique model version based on timestamp"""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

def generate_config_hash(config: Dict) -> str:
    """Generate hash of config for change detection"""
    config_str = str(sorted(config.items()))
    return hashlib.md5(config_str.encode()).hexdigest()[:8]

async def train_model_task():
    """Train Ridge Regression model with full versioning"""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    import numpy as np
    import joblib
    import io
    
    train_seasons = OPERATIONAL_CONFIG["train_seasons"]
    test_season = OPERATIONAL_CONFIG["test_season"]
    feature_cols = OPERATIONAL_CONFIG["feature_list"]
    
    train_features = await db.game_features.find({"season": {"$in": train_seasons}}).to_list(10000)
    test_features = await db.game_features.find({"season": test_season}).to_list(10000)
    
    if len(train_features) < 100:
        return {"error": "Not enough training data", "train_count": len(train_features)}
    
    # Prepare data
    X_train, y_train = [], []
    for f in train_features:
        row = [f.get(col, 0) or 0 for col in feature_cols]
        X_train.append(row)
        y_train.append(f['margin'])
    
    X_train = np.array(X_train)
    y_train = np.array(y_train)
    
    X_test, y_test = [], []
    for f in test_features:
        row = [f.get(col, 0) or 0 for col in feature_cols]
        X_test.append(row)
        y_test.append(f['margin'])
    
    X_test = np.array(X_test) if test_features else np.zeros((0, len(feature_cols)))
    y_test = np.array(y_test) if test_features else np.array([])
    
    # Train
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    model = Ridge(alpha=OPERATIONAL_CONFIG["alpha"])
    model.fit(X_train_scaled, y_train)
    
    # Evaluate
    train_pred = model.predict(X_train_scaled)
    train_mae = mean_absolute_error(y_train, train_pred)
    train_rmse = np.sqrt(mean_squared_error(y_train, train_pred))
    train_pred_std = float(np.std(train_pred))
    
    test_mae, test_rmse, test_pred_std = 0, 0, 0
    error_percentiles = {"p50": 0, "p75": 0, "p90": 0}
    
    if len(X_test) > 0:
        X_test_scaled = scaler.transform(X_test)
        test_pred = model.predict(X_test_scaled)
        test_errors = np.abs(y_test - test_pred)
        test_mae = float(mean_absolute_error(y_test, test_pred))
        test_rmse = float(np.sqrt(mean_squared_error(y_test, test_pred)))
        test_pred_std = float(np.std(test_pred))
        error_percentiles = {
            "p50": float(np.percentile(test_errors, 50)),
            "p75": float(np.percentile(test_errors, 75)),
            "p90": float(np.percentile(test_errors, 90))
        }
    
    # Save model
    model_buffer = io.BytesIO()
    joblib.dump({"model": model, "scaler": scaler, "features": feature_cols}, model_buffer)
    model_bytes = model_buffer.getvalue()
    
    model_version = generate_model_version()
    data_cutoff = await get_data_cutoff_date()
    
    config_snapshot = {
        "rolling_window_n": OPERATIONAL_CONFIG["rolling_window_n"],
        "feature_list": OPERATIONAL_CONFIG["feature_list"],
        "algorithm": OPERATIONAL_CONFIG["algorithm"],
        "alpha": OPERATIONAL_CONFIG["alpha"],
        "signal_thresholds": OPERATIONAL_CONFIG["signal_thresholds"],
        "operative_thresholds": OPERATIONAL_CONFIG["operative_thresholds"],
        "train_seasons": train_seasons,
        "test_season": test_season,
        "spread_convention": OPERATIONAL_CONFIG["spread_convention"]
    }
    
    model_doc = {
        "id": str(uuid.uuid4()),
        "name": "ridge_v1",
        "model_version": model_version,
        "config_snapshot": config_snapshot,
        "config_hash": generate_config_hash(config_snapshot),
        "data_cutoff_date": data_cutoff,
        "metrics": {
            "mae": test_mae if test_mae > 0 else train_mae,
            "rmse": test_rmse if test_rmse > 0 else train_rmse,
            "train_mae": train_mae,
            "train_rmse": train_rmse,
            "error_percentiles": error_percentiles,
            "pred_std_train": train_pred_std,
            "pred_std_test": test_pred_std
        },
        "train_seasons": train_seasons,
        "test_season": test_season,
        "feature_window": OPERATIONAL_CONFIG["rolling_window_n"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_active": True,
        "model_binary": model_bytes,
        "train_samples": len(train_features),
        "test_samples": len(test_features),
        "intercept": float(model.intercept_),
        "coefficients": {col: float(coef) for col, coef in zip(feature_cols, model.coef_)}
    }
    
    # Deactivate old models (don't delete - keep history)
    await db.models.update_many({}, {"$set": {"is_active": False}})
    await db.models.insert_one(model_doc)
    
    return {
        "model_id": model_doc['id'],
        "model_version": model_version,
        "mae": model_doc['metrics']['mae'],
        "rmse": model_doc['metrics']['rmse'],
        "train_samples": len(train_features),
        "test_samples": len(test_features),
        "pred_std_test": test_pred_std,
        "data_cutoff_date": data_cutoff
    }

async def get_active_model():
    import joblib
    import io
    
    model_doc = await db.models.find_one({"is_active": True})
    if not model_doc:
        return None
    
    model_data = joblib.load(io.BytesIO(model_doc['model_binary']))
    model_data['model_id'] = model_doc['id']
    model_data['model_version'] = model_doc.get('model_version', 'unknown')
    model_data['config_snapshot'] = model_doc.get('config_snapshot', {})
    model_data['intercept'] = model_doc.get('intercept', 0)
    model_data['coefficients'] = model_doc.get('coefficients', {})
    return model_data

# ============= HISTORICAL DATA =============

async def sync_historical_data_task():
    from nba_api.stats.endpoints import leaguegamefinder
    
    seasons = get_nba_seasons()
    total_games = 0
    
    for season in seasons:
        logger.info(f"Syncing season {season}...")
        try:
            await asyncio.sleep(1)
            gamefinder = leaguegamefinder.LeagueGameFinder(
                season_nullable=season, season_type_nullable='Regular Season', league_id_nullable='00'
            )
            games_df = gamefinder.get_data_frames()[0]
            game_ids = games_df['GAME_ID'].unique()
            
            for game_id in game_ids[:200]:
                if await db.games.find_one({"game_id": game_id}):
                    continue
                
                game_rows = games_df[games_df['GAME_ID'] == game_id]
                if len(game_rows) < 2:
                    continue
                
                home_rows = game_rows[game_rows['MATCHUP'].str.contains('vs.')]
                away_rows = game_rows[game_rows['MATCHUP'].str.contains('@')]
                
                if len(home_rows) == 0 or len(away_rows) == 0:
                    continue
                
                home_row, away_row = home_rows.iloc[0], away_rows.iloc[0]
                
                game_doc = {
                    "game_id": game_id, "season": season, "game_date": home_row['GAME_DATE'],
                    "home_team_id": int(home_row['TEAM_ID']), "home_team": home_row['TEAM_ABBREVIATION'],
                    "away_team_id": int(away_row['TEAM_ID']), "away_team": away_row['TEAM_ABBREVIATION'],
                    "home_pts": int(home_row['PTS']), "away_pts": int(away_row['PTS']),
                    "margin": int(home_row['PTS']) - int(away_row['PTS'])
                }
                
                await db.games.update_one({"game_id": game_id}, {"$set": game_doc}, upsert=True)
                
                for row in [home_row, away_row]:
                    stat_doc = {
                        "game_id": game_id, "team_id": int(row['TEAM_ID']),
                        "team_abbr": row['TEAM_ABBREVIATION'],
                        "pts": int(row['PTS']), "fgm": int(row['FGM']), "fga": int(row['FGA']),
                        "fg3m": int(row['FG3M']), "fg3a": int(row['FG3A']),
                        "ftm": int(row['FTM']), "fta": int(row['FTA']),
                        "oreb": int(row['OREB']), "dreb": int(row['DREB']), "reb": int(row['REB']),
                        "ast": int(row['AST']), "stl": int(row['STL']), "blk": int(row['BLK']),
                        "tov": int(row['TOV']), "pf": int(row['PF']),
                        "plus_minus": int(row['PLUS_MINUS']) if row['PLUS_MINUS'] else 0
                    }
                    await db.team_game_stats.update_one(
                        {"game_id": game_id, "team_id": stat_doc['team_id']},
                        {"$set": stat_doc}, upsert=True
                    )
                
                total_games += 1
                if total_games % 50 == 0:
                    logger.info(f"Processed {total_games} games...")
                    await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Error syncing season {season}: {e}")
            continue
    
    return total_games

async def build_features_task():
    N = OPERATIONAL_CONFIG["rolling_window_n"]
    games = await db.games.find({}).sort("game_date", 1).to_list(10000)
    
    team_games = {}
    for game in games:
        for team_abbr in [game['home_team'], game['away_team']]:
            if team_abbr not in team_games:
                team_games[team_abbr] = []
            team_games[team_abbr].append(game)
    
    features_count = 0
    
    for game in games:
        game_date = game['game_date']
        home_abbr, away_abbr = game['home_team'], game['away_team']
        
        home_prev = [g for g in team_games.get(home_abbr, []) if g['game_date'] < game_date][-N:]
        away_prev = [g for g in team_games.get(away_abbr, []) if g['game_date'] < game_date][-N:]
        
        if len(home_prev) < 5 or len(away_prev) < 5:
            continue
        
        home_stats = await calculate_team_stats_from_games(home_abbr, home_prev)
        away_stats = await calculate_team_stats_from_games(away_abbr, away_prev)
        
        if not home_stats or not away_stats:
            continue
        
        home_rest = calculate_rest_days(game_date, home_prev)
        away_rest = calculate_rest_days(game_date, away_prev)
        
        feature_doc = {
            "game_id": game['game_id'], "season": game['season'], "game_date": game_date,
            "home_team": home_abbr, "away_team": away_abbr,
            "diff_net_rating": home_stats['net_rating'] - away_stats['net_rating'],
            "diff_pace": home_stats['pace'] - away_stats['pace'],
            "diff_efg": home_stats['efg'] - away_stats['efg'],
            "diff_tov_pct": home_stats['tov_pct'] - away_stats['tov_pct'],
            "diff_orb_pct": home_stats['orb_pct'] - away_stats['orb_pct'],
            "diff_ftr": home_stats['ftr'] - away_stats['ftr'],
            "diff_rest": home_rest - away_rest,
            "home_advantage": 1,
            "margin": game['margin']
        }
        
        await db.game_features.update_one({"game_id": game['game_id']}, {"$set": feature_doc}, upsert=True)
        features_count += 1
    
    return features_count

async def calculate_team_stats_from_games(team_abbr: str, prev_games: List[Dict]) -> Optional[Dict]:
    if not prev_games:
        return None
    
    game_ids = [g['game_id'] for g in prev_games]
    stats = await db.team_game_stats.find({"game_id": {"$in": game_ids}, "team_abbr": team_abbr}).to_list(100)
    
    if len(stats) < 5:
        return None
    
    total_pts = sum(s['pts'] for s in stats)
    total_fga = sum(s['fga'] for s in stats)
    total_fgm = sum(s['fgm'] for s in stats)
    total_fg3m = sum(s['fg3m'] for s in stats)
    total_fta = sum(s['fta'] for s in stats)
    total_oreb = sum(s['oreb'] for s in stats)
    total_tov = sum(s['tov'] for s in stats)
    n = len(stats)
    
    poss = max(total_fga - total_oreb + total_tov + 0.4 * total_fta, n * 100)
    
    return {
        "net_rating": (total_pts / poss * 100) - 112 if poss > 0 else 0,
        "pace": (poss / n) * 2 if n > 0 else 100,
        "efg": ((total_fgm + 0.5 * total_fg3m) / total_fga * 100) if total_fga > 0 else 50,
        "tov_pct": (total_tov / poss * 100) if poss > 0 else 15,
        "orb_pct": (total_oreb / (total_oreb + n * 35)) * 100 if n > 0 else 25,
        "ftr": (total_fta / total_fga) if total_fga > 0 else 0.25
    }

def calculate_rest_days(game_date: str, prev_games: List[Dict]) -> int:
    if not prev_games:
        return 3
    try:
        current = datetime.strptime(game_date, "%Y-%m-%d")
        last = datetime.strptime(prev_games[-1]['game_date'], "%Y-%m-%d")
        return (current - last).days
    except:
        return 3

# ============= CREATE APP =============

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting NBA Edge API v1.0 (Production)...")
    yield
    client.close()

app = FastAPI(title="NBA Edge API", version="1.0.0", lifespan=lifespan)
api_router = APIRouter(prefix="/api")

# ============= AUTH ROUTES =============

@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserCreate):
    if await db.users.find_one({"email": user_data.email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    
    user_id = str(uuid.uuid4())
    user_doc = {
        "id": user_id, "email": user_data.email, "name": user_data.name,
        "password_hash": hash_password(user_data.password),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.users.insert_one(user_doc)
    
    return TokenResponse(
        access_token=create_token(user_id, user_data.email),
        user=UserResponse(id=user_id, email=user_data.email, name=user_data.name,
                         created_at=datetime.fromisoformat(user_doc['created_at']))
    )

@api_router.post("/auth/login", response_model=TokenResponse)
async def login(credentials: UserLogin):
    user = await db.users.find_one({"email": credentials.email})
    if not user or not verify_password(credentials.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    return TokenResponse(
        access_token=create_token(user['id'], user['email']),
        user=UserResponse(id=user['id'], email=user['email'], name=user['name'],
                         created_at=datetime.fromisoformat(user['created_at']) if isinstance(user['created_at'], str) else user['created_at'])
    )

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(user=Depends(get_current_user)):
    return UserResponse(id=user['id'], email=user['email'], name=user['name'],
                       created_at=datetime.fromisoformat(user['created_at']) if isinstance(user['created_at'], str) else user['created_at'])

# ============= ADMIN ROUTES =============

@api_router.post("/admin/sync-historical", response_model=SyncStatus)
async def sync_historical(user=Depends(get_current_user)):
    asyncio.create_task(sync_historical_data_task())
    return SyncStatus(status="started", message="Historical sync started", details={"seasons": get_nba_seasons()})

@api_router.post("/admin/build-features", response_model=SyncStatus)
async def build_features(user=Depends(get_current_user)):
    count = await build_features_task()
    return SyncStatus(status="completed", message=f"Built features for {count} games", details={"count": count})

@api_router.post("/admin/train", response_model=SyncStatus)
async def train_model(user=Depends(get_current_user)):
    result = await train_model_task()
    if "error" in result:
        return SyncStatus(status="error", message=result['error'], details=result)
    return SyncStatus(status="completed", 
                     message=f"Model v{result['model_version']} trained. MAE={result['mae']:.2f}",
                     details=result)

@api_router.post("/admin/sync-upcoming", response_model=SyncStatus)
async def sync_upcoming(days: int = 2, user=Depends(get_current_user)):
    events = await fetch_upcoming_events(days)
    for event in events:
        event_doc = {
            "event_id": event['id'], "sport_key": event.get('sport_key', 'basketball_nba'),
            "commence_time": event['commence_time'],
            "home_team": event['home_team'], "away_team": event['away_team'],
            "home_team_abbr": get_team_abbr(event['home_team']),
            "away_team_abbr": get_team_abbr(event['away_team']),
            "status": "pending", "updated_at": datetime.now(timezone.utc).isoformat()
        }
        await db.upcoming_events.update_one({"event_id": event['id']}, {"$set": event_doc}, upsert=True)
    return SyncStatus(status="completed", message=f"Synced {len(events)} events", details={"count": len(events)})

@api_router.post("/admin/sync-odds", response_model=SyncStatus)
async def sync_odds(days: int = 2, user=Depends(get_current_user)):
    events = await fetch_odds(days)
    lines_count = 0
    
    for event in events:
        await db.upcoming_events.update_one(
            {"event_id": event['id']},
            {"$set": {
                "event_id": event['id'], "commence_time": event['commence_time'],
                "home_team": event['home_team'], "away_team": event['away_team'],
                "home_team_abbr": get_team_abbr(event['home_team']),
                "away_team_abbr": get_team_abbr(event['away_team']),
                "status": "pending", "updated_at": datetime.now(timezone.utc).isoformat()
            }}, upsert=True
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
                            "home_team": event['home_team'],
                            "away_team": event['away_team'],
                            "spread_point_home": home_outcome.get('point', 0),
                            "spread_point_away": away_outcome.get('point', 0),
                            "price_home_decimal": home_outcome.get('price', 1.91),
                            "price_away_decimal": away_outcome.get('price', 1.91),
                            "last_update": bookmaker.get('last_update', datetime.now(timezone.utc).isoformat())
                        }
                        
                        await db.market_lines.update_one(
                            {"event_id": event['id'], "bookmaker_key": bookmaker['key']},
                            {"$set": line_doc}, upsert=True
                        )
                        lines_count += 1
    
    return SyncStatus(status="completed", message=f"Synced {lines_count} lines for {len(events)} events",
                     details={"events": len(events), "lines": lines_count})

@api_router.post("/admin/snapshot-close-lines", response_model=SyncStatus)
async def snapshot_close_lines(minutes_before: int = 60, user=Depends(get_current_user)):
    """Snapshot close lines for events starting soon"""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=minutes_before)
    
    # Find predictions for events starting soon
    predictions = await db.predictions.find({
        "close_spread": None,  # Not yet snapshotted
    }).to_list(100)
    
    updated_count = 0
    
    for pred in predictions:
        # Check if event is starting soon
        try:
            commence = datetime.fromisoformat(pred['commence_time'].replace('Z', '+00:00'))
            if commence > cutoff:
                continue  # Not yet close to start
        except:
            continue
        
        # Get latest Pinnacle line
        line = await db.market_lines.find_one({
            "event_id": pred['event_id'],
            "bookmaker_key": "pinnacle"
        }, {"_id": 0})
        
        if line:
            close_spread = line['spread_point_home']
            open_spread = pred.get('open_spread', pred.get('market_spread_used', 0))
            
            # CLV calculation: positive = line moved in our favor
            # If we bet HOME and spread moved more negative, that's good (CLV positive)
            # If we bet AWAY and spread moved more positive, that's good (CLV positive)
            recommended_side = pred.get('recommended_side', 'HOME')
            if recommended_side == 'HOME':
                clv_spread = open_spread - close_spread  # More negative close = good for HOME
            else:
                clv_spread = close_spread - open_spread  # More positive close = good for AWAY
            
            await db.predictions.update_one(
                {"id": pred['id']},
                {"$set": {
                    "close_spread": close_spread,
                    "close_price": line['price_home_decimal'] if recommended_side == 'HOME' else line['price_away_decimal'],
                    "close_ts": datetime.now(timezone.utc).isoformat(),
                    "clv_spread": clv_spread
                }}
            )
            updated_count += 1
    
    return SyncStatus(status="completed", message=f"Updated {updated_count} predictions with close lines",
                     details={"updated": updated_count, "minutes_before": minutes_before})

@api_router.post("/admin/refresh-results", response_model=SyncStatus)
async def refresh_results(user=Depends(get_current_user)):
    return SyncStatus(status="completed", message="Results refresh not yet implemented", details={})

# ============= DEBUG ENDPOINT =============

@api_router.get("/admin/debug/predict", response_model=DebugPrediction)
async def debug_predict(event_id: str, user=Depends(get_current_user)):
    import numpy as np
    
    event = await db.upcoming_events.find_one({"event_id": event_id}, {"_id": 0})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    model_data = await get_active_model()
    if not model_data:
        raise HTTPException(status_code=400, detail="No trained model available")
    
    model = model_data['model']
    scaler = model_data['scaler']
    feature_cols = model_data['features']
    model_version = model_data['model_version']
    
    matchup_data = await calculate_matchup_features(event['home_team'], event['away_team'])
    features = matchup_data['features']
    home_abbr, away_abbr = matchup_data['home_abbr'], matchup_data['away_abbr']
    
    home_games = await db.games.count_documents({"$or": [{"home_team": home_abbr}, {"away_team": home_abbr}]}) if home_abbr else 0
    away_games = await db.games.count_documents({"$or": [{"home_team": away_abbr}, {"away_team": away_abbr}]}) if away_abbr else 0
    
    X = np.array([[features.get(col, 0) for col in feature_cols]])
    X_scaled = scaler.transform(X)
    pred_margin = float(model.predict(X_scaled)[0])
    
    contributions = {"intercept": float(model.intercept_)}
    for i, (col, coef) in enumerate(zip(feature_cols, model.coef_)):
        contributions[col] = float(coef * X_scaled[0][i])
    
    lines = await db.market_lines.find({"event_id": event_id}, {"_id": 0}).to_list(10)
    ref_line = select_reference_line(lines, require_pinnacle=True)
    
    market_spread, edge_points = None, None
    recommended_side, recommended_bet, explanation = None, None, None
    do_not_bet, do_not_bet_reason = False, None
    
    if not ref_line:
        do_not_bet = True
        do_not_bet_reason = "NO_PINNACLE_LINE"
    else:
        market_spread = ref_line['spread_point_home']
        
        # CORRECTED COVER LOGIC
        # =====================
        # market_spread = -5.0 means HOME is 5-point favorite
        # market_spread = +3.0 means HOME is 3-point underdog
        #
        # For spread betting, the cover threshold is -market_spread:
        # - If spread=-5.0: HOME covers if pred_margin > 5 (wins by more than 5)
        # - If spread=+3.0: HOME covers if pred_margin > -3 (doesn't lose by more than 3)
        #
        # General rule using threshold = -market_spread:
        # - HOME covers if pred_margin > threshold (pred_margin > -market_spread)
        # - AWAY covers if pred_margin < threshold (pred_margin < -market_spread)
        # - Edge is distance from threshold (always positive)
        
        cover_threshold = -market_spread
        
        home_covers = pred_margin > cover_threshold
        away_covers = pred_margin < cover_threshold
        
        if home_covers:
            recommended_side = "HOME"
            edge_points = pred_margin - cover_threshold  # Always positive
        elif away_covers:
            recommended_side = "AWAY"
            edge_points = cover_threshold - pred_margin  # Always positive
        else:
            # pred_margin == cover_threshold exactly - no edge
            do_not_bet = True
            do_not_bet_reason = "NO_EDGE"
            edge_points = 0.0
        
        if recommended_side:
            recommended_bet = generate_recommended_bet_string(
                event['home_team'], event['away_team'], home_abbr, away_abbr,
                market_spread, recommended_side
            )
            explanation = generate_explanation(
                event['home_team'], event['away_team'], home_abbr, away_abbr,
                pred_margin, market_spread, edge_points,
                recommended_side, matchup_data['confidence'], model_version
            )
    
    # Check operative filters
    if not do_not_bet:
        if matchup_data['confidence'] != 'high':
            do_not_bet = True
            do_not_bet_reason = "LOW_CONFIDENCE"
        elif (edge_points or 0) < OPERATIONAL_CONFIG['operative_thresholds']['min_edge']:
            do_not_bet = True
            do_not_bet_reason = "EDGE_TOO_SMALL"
        elif calculate_signal(edge_points or 0) != 'green':
            do_not_bet = True
            do_not_bet_reason = "NOT_GREEN_SIGNAL"
    
    return DebugPrediction(
        event_id=event_id, home_team=event['home_team'], away_team=event['away_team'],
        home_abbr=home_abbr, away_abbr=away_abbr,
        home_games_found=home_games, away_games_found=away_games,
        features_raw=features, features_scaled=X_scaled[0].tolist(),
        model_id=model_data['model_id'], model_version=model_version,
        intercept=float(model.intercept_),
        coeff_summary={col: float(coef) for col, coef in zip(feature_cols, model.coef_)},
        contributions=contributions, pred_margin=pred_margin,
        market_spread=market_spread, edge_points=edge_points,
        recommended_side=recommended_side, recommended_bet=recommended_bet,
        explanation=explanation, confidence=matchup_data['confidence'],
        do_not_bet=do_not_bet, do_not_bet_reason=do_not_bet_reason,
        warnings=matchup_data['warnings']
    )

# ============= USER ROUTES =============

@api_router.get("/upcoming")
async def get_upcoming(user=Depends(get_current_user)):
    events = await db.upcoming_events.find({"status": "pending"}, {"_id": 0}).sort("commence_time", 1).to_list(50)
    result = []
    for event in events:
        lines = await db.market_lines.find({"event_id": event['event_id']}, {"_id": 0}).to_list(20)
        result.append({**event, "lines": lines, "reference_line": select_reference_line(lines, require_pinnacle=False)})
    return {"events": result, "count": len(result)}

@api_router.post("/picks/generate")
async def generate_picks(
    operative_mode: bool = Query(True, description="Apply operative filters"),
    user=Depends(get_current_user)
):
    """Generate picks with full operative controls"""
    import numpy as np
    
    model_data = await get_active_model()
    if not model_data:
        raise HTTPException(status_code=400, detail="No trained model available")
    
    model = model_data['model']
    scaler = model_data['scaler']
    feature_cols = model_data['features']
    model_version = model_data['model_version']
    model_id = model_data['model_id']
    
    events = await db.upcoming_events.find({"status": "pending"}, {"_id": 0}).to_list(50)
    
    picks = []
    operative_picks = []
    
    for event in events:
        lines = await db.market_lines.find({"event_id": event['event_id']}, {"_id": 0}).to_list(20)
        
        # Check Pinnacle availability
        ref_line = select_reference_line(lines, require_pinnacle=True)
        has_pinnacle = ref_line is not None
        
        if not has_pinnacle:
            ref_line = select_reference_line(lines, require_pinnacle=False)
        
        if not ref_line:
            continue
        
        matchup_data = await calculate_matchup_features(event['home_team'], event['away_team'])
        features = matchup_data['features']
        home_abbr, away_abbr = matchup_data['home_abbr'], matchup_data['away_abbr']
        
        X = np.array([[features.get(col, 0) for col in feature_cols]])
        X_scaled = scaler.transform(X)
        pred_margin = float(model.predict(X_scaled)[0])
        
        market_spread = ref_line['spread_point_home']
        
        # CORRECTED COVER LOGIC
        # =====================
        # market_spread = -5.0 means HOME is 5-point favorite
        # market_spread = +3.0 means HOME is 3-point underdog
        #
        # Cover threshold = -market_spread:
        # - If spread=-5.0: HOME covers if pred_margin > 5 (wins by more than 5)
        # - If spread=+3.0: HOME covers if pred_margin > -3 (doesn't lose by more than 3)
        #
        # HOME covers if pred_margin > cover_threshold
        # AWAY covers if pred_margin < cover_threshold
        # Edge is distance from threshold (always positive)
        
        cover_threshold = -market_spread
        
        home_covers = pred_margin > cover_threshold
        away_covers = pred_margin < cover_threshold
        
        if home_covers:
            recommended_side = "HOME"
            edge_points = pred_margin - cover_threshold  # Always positive
            open_price = ref_line['price_home_decimal']
        elif away_covers:
            recommended_side = "AWAY"
            edge_points = cover_threshold - pred_margin  # Always positive
            open_price = ref_line['price_away_decimal']
        else:
            # pred_margin == cover_threshold exactly - no edge
            recommended_side = "HOME"
            edge_points = 0.0
            open_price = ref_line['price_home_decimal']
        
        signal = calculate_signal(edge_points)
        
        recommended_bet_string = generate_recommended_bet_string(
            event['home_team'], event['away_team'], home_abbr, away_abbr,
            market_spread, recommended_side
        )
        
        explanation = generate_explanation(
            event['home_team'], event['away_team'], home_abbr, away_abbr,
            pred_margin, market_spread, edge_points,
            recommended_side, matchup_data['confidence'], model_version
        )
        
        # Determine do_not_bet and reason
        do_not_bet = False
        do_not_bet_reason = None
        
        if not has_pinnacle:
            do_not_bet = True
            do_not_bet_reason = "NO_PINNACLE_LINE"
        elif matchup_data['confidence'] != 'high':
            do_not_bet = True
            do_not_bet_reason = "LOW_CONFIDENCE"
        elif edge_points < OPERATIONAL_CONFIG['operative_thresholds']['min_edge']:
            do_not_bet = True
            do_not_bet_reason = "EDGE_TOO_SMALL"
        elif signal != 'green':
            do_not_bet = True
            do_not_bet_reason = "NOT_GREEN_SIGNAL"
        
        now_ts = datetime.now(timezone.utc).isoformat()
        
        pick = {
            "id": str(uuid.uuid4()),
            "user_id": user['id'],
            "event_id": event['event_id'],
            "home_team": event['home_team'],
            "away_team": event['away_team'],
            "home_abbr": home_abbr,
            "away_abbr": away_abbr,
            "commence_time": event['commence_time'],
            "commence_time_local": format_local_time(event['commence_time']),
            "pred_margin": round(pred_margin, 2),
            "open_spread": market_spread,
            "open_price": open_price,
            "open_ts": now_ts,
            "market_spread_used": market_spread,
            "edge_points": round(edge_points, 2),
            "signal": signal,
            "confidence": matchup_data['confidence'],
            "recommended_side": recommended_side,
            "recommended_bet_string": recommended_bet_string,
            "explanation": explanation,
            "do_not_bet": do_not_bet,
            "do_not_bet_reason": do_not_bet_reason,
            "model_id": model_id,
            "model_version": model_version,
            "reference_bookmaker_used": ref_line['bookmaker_key'],
            "features_used": {k: round(v, 4) for k, v in features.items()},
            "created_at": now_ts,
            "close_spread": None,
            "close_price": None,
            "close_ts": None,
            "clv_spread": None
        }
        
        await db.predictions.update_one(
            {"user_id": user['id'], "event_id": event['event_id']},
            {"$set": pick}, upsert=True
        )
        
        picks.append(pick)
        
        if not do_not_bet:
            operative_picks.append(pick)
    
    # Apply max_picks_per_day limit (only if configured)
    max_picks = OPERATIONAL_CONFIG['operative_thresholds']['max_picks_per_day']
    if operative_mode and max_picks is not None and len(operative_picks) > max_picks:
        # Sort by edge (descending - higher edge first) and take top N
        operative_picks.sort(key=lambda p: p['edge_points'], reverse=True)
        operative_picks = operative_picks[:max_picks]
    elif operative_mode:
        # Sort by edge even without limit for better display
        operative_picks.sort(key=lambda p: p['edge_points'], reverse=True)
    
    # Log stats
    logger.info(f"Generated {len(picks)} picks. Operative: {len(operative_picks)}")
    
    if operative_mode:
        return {
            "picks": operative_picks,
            "all_picks": picks,
            "count": len(operative_picks),
            "total_analyzed": len(picks),
            "operative_mode": True,
            "filters_applied": OPERATIONAL_CONFIG['operative_thresholds']
        }
    else:
        return {
            "picks": picks,
            "count": len(picks),
            "operative_mode": False
        }

@api_router.get("/picks")
async def get_picks(user=Depends(get_current_user)):
    picks = await db.predictions.find({"user_id": user['id']}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return {"picks": picks}

@api_router.get("/picks/operative")
async def get_operative_picks(user=Depends(get_current_user)):
    """Get only operative picks (ready to bet)"""
    picks = await db.predictions.find({
        "user_id": user['id'],
        "do_not_bet": False
    }, {"_id": 0}).sort("commence_time", 1).to_list(50)
    return {"picks": picks, "count": len(picks)}

@api_router.get("/history")
async def get_history(signal: Optional[str] = None, covered: Optional[bool] = None, user=Depends(get_current_user)):
    query = {"user_id": user['id'], "actual_margin": {"$ne": None}}
    if signal:
        query["signal"] = signal
    if covered is not None:
        query["covered"] = covered
    
    predictions = await db.predictions.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    
    total = len(predictions)
    covered_count = sum(1 for p in predictions if p.get('covered'))
    
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
        "stats": {"total": total, "covered": covered_count, "hit_rate": (covered_count / total * 100) if total > 0 else 0},
        "by_signal": by_signal
    }

@api_router.get("/history/export")
async def export_history(user=Depends(get_current_user)):
    from fastapi.responses import StreamingResponse
    import csv
    import io
    
    predictions = await db.predictions.find({"user_id": user['id']}, {"_id": 0}).sort("created_at", -1).to_list(1000)
    
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "created_at", "home_team", "away_team", "commence_time", "pred_margin",
        "open_spread", "open_price", "close_spread", "close_price", "clv_spread",
        "edge_points", "signal", "recommended_side", "recommended_bet_string",
        "actual_margin", "covered", "confidence", "model_version"
    ])
    writer.writeheader()
    
    for p in predictions:
        writer.writerow({
            "created_at": p.get('created_at'), "home_team": p.get('home_team'),
            "away_team": p.get('away_team'), "commence_time": p.get('commence_time'),
            "pred_margin": p.get('pred_margin'), "open_spread": p.get('open_spread'),
            "open_price": p.get('open_price'), "close_spread": p.get('close_spread'),
            "close_price": p.get('close_price'), "clv_spread": p.get('clv_spread'),
            "edge_points": p.get('edge_points'), "signal": p.get('signal'),
            "recommended_side": p.get('recommended_side'),
            "recommended_bet_string": p.get('recommended_bet_string'),
            "actual_margin": p.get('actual_margin'), "covered": p.get('covered'),
            "confidence": p.get('confidence'), "model_version": p.get('model_version')
        })
    
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                            headers={"Content-Disposition": "attachment; filename=nba_edge_history.csv"})

# ============= STATS ROUTES =============

@api_router.get("/stats/dataset")
async def get_dataset_stats(user=Depends(get_current_user)):
    games_count = await db.games.count_documents({})
    features_count = await db.game_features.count_documents({})
    seasons = {s: await db.games.count_documents({"season": s}) for s in get_nba_seasons()}
    teams = await db.games.distinct("home_team")
    return {"total_games": games_count, "total_features": features_count, "by_season": seasons, "teams_count": len(teams)}

@api_router.get("/stats/model")
async def get_model_stats(user=Depends(get_current_user)):
    model = await db.models.find_one({"is_active": True}, {"_id": 0, "model_binary": 0})
    if not model:
        return {"active_model": None}
    return {"active_model": model}

@api_router.get("/stats/config")
async def get_config(user=Depends(get_current_user)):
    """Get current operational config"""
    return {"config": OPERATIONAL_CONFIG}

# ============= ROOT =============

@api_router.get("/")
async def root():
    return {"message": "NBA Edge API", "version": "1.0.0"}

@api_router.get("/health")
async def health():
    return {"status": "healthy"}

app.include_router(api_router)
app.add_middleware(CORSMiddleware, allow_credentials=True,
                  allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
                  allow_methods=["*"], allow_headers=["*"])
