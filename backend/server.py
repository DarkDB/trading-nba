from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, Query, Header
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
from zoneinfo import ZoneInfo
import jwt
import bcrypt
import httpx
import asyncio
from contextlib import asynccontextmanager
from backend.calibration_outcome import (
    fit_outcome_calibration,
    get_active_outcome_calibration,
    get_outcome_calibration_diagnostics,
    predict_p_cover_outcome,
)
from backend.market_eval import backfill_close_snapshot
from backend.performance import recompute_performance_daily, get_performance_summary
from backend.selection_backtest import run_selection_sweep
from backend.walkforward_selection import run_walkforward_selection

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
OPERATIONAL_USER_EMAIL = os.environ.get("OPERATIONAL_USER_EMAIL", "").strip().lower()
CRON_API_KEY = os.environ.get("CRON_API_KEY", "").strip()
CLOSE_CAPTURE_ALERT_HOURS = int(os.environ.get("CLOSE_CAPTURE_ALERT_HOURS", 12))
CLOSE_CAPTURE_ALERT_LOOKBACK_DAYS = int(os.environ.get("CLOSE_CAPTURE_ALERT_LOOKBACK_DAYS", 3))

# ============= OPERATIONAL CONFIG (V1.0) =============
OPERATIONAL_CONFIG = {
    "version": "2.0",
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
        "min_edge": 3.5,  # Legacy, kept for backward compatibility
        "min_ev": 0.02,   # NEW: Minimum EV (2%) for valid pick
        "max_picks_per_day": None,  # None = sin límite
        "require_high_confidence": True,
        "require_positive_ev": True,  # NEW: Use EV >= min_ev as main criterion
        "require_pinnacle": True
    },
    "calibration": {
        "sigma_global": 12.0,  # Default, will be recomputed from historical data
        "sigma_source": "default"  # "default" or "computed"
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


def is_operational_user(user: Dict[str, Any]) -> bool:
    user_id = str(user.get("id", "")).strip().lower()
    email = str(user.get("email", "")).strip().lower()
    name = str(user.get("name", "")).strip().lower()
    if user_id == "admin":
        return False
    if email.startswith("probe_") or name.startswith("probe"):
        return False
    if OPERATIONAL_USER_EMAIL and email != OPERATIONAL_USER_EMAIL:
        return False
    return True

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

# ============= PAPER TRADING v4.0 MODELS =============

class TradingSettings(BaseModel):
    """Paper Trading v4.0 configuration"""
    enabled_tiers: List[str] = Field(default=["A", "B"])
    blowout_filter_enabled: bool = Field(default=True)
    blowout_pred_margin_threshold: float = Field(default=12.0)
    max_picks_per_day: int = Field(default=3)
    min_abs_model_edge: float = Field(default=1.5)
    clv_gate_enabled: bool = Field(default=True)
    dd_gate_enabled: bool = Field(default=True)
    dd_gate_max_drawdown_threshold: float = Field(default=0.25)
    use_outcome_calibration: bool = Field(default=True)
    tier_a_min_p_cover_real: float = Field(default=0.54)
    tier_b_min_p_cover_real: float = Field(default=0.52)
    tier_c_min_p_cover_real: float = Field(default=0.50)
    stake_mode: str = Field(default="FLAT")  # FLAT or KELLY
    flat_stake_pct: float = Field(default=0.01)  # 1% of bankroll
    kelly_fraction: float = Field(default=0.20)  # 20% Kelly
    kelly_cap_pct: float = Field(default=0.02)  # Max 2% per bet

class TradingSettingsUpdate(BaseModel):
    """Partial update for trading settings"""
    enabled_tiers: Optional[List[str]] = None
    blowout_filter_enabled: Optional[bool] = None
    blowout_pred_margin_threshold: Optional[float] = None
    max_picks_per_day: Optional[int] = None
    min_abs_model_edge: Optional[float] = None
    clv_gate_enabled: Optional[bool] = None
    dd_gate_enabled: Optional[bool] = None
    dd_gate_max_drawdown_threshold: Optional[float] = None
    use_outcome_calibration: Optional[bool] = None
    tier_a_min_p_cover_real: Optional[float] = None
    tier_b_min_p_cover_real: Optional[float] = None
    tier_c_min_p_cover_real: Optional[float] = None
    stake_mode: Optional[str] = None
    flat_stake_pct: Optional[float] = None
    kelly_fraction: Optional[float] = None
    kelly_cap_pct: Optional[float] = None

class PickResultInput(BaseModel):
    """Input for registering pick results"""
    final_home_score: int
    final_away_score: int
    result_override: Optional[str] = None  # WIN/LOSS/PUSH/VOID (admin override)

class PickResult(BaseModel):
    """Pick result after grading"""
    result: str  # WIN/LOSS/PUSH/VOID
    margin_final: int  # actual margin (home - away)
    covered: Optional[bool]  # True if bet covered
    profit_units: float  # +price-1 for WIN, -1 for LOSS, 0 for PUSH
    settled_at: str

class SnapshotCloseInput(BaseModel):
    """Input for snapshot close operation"""
    window_minutes: int = Field(default=60)

class BankrollSimRequest(BaseModel):
    """Request for bankroll simulation"""
    bankrolls: List[float] = Field(default=[1000, 3000, 5000, 10000])
    tiers: List[str] = Field(default=["A", "B"])
    stake_mode: str = Field(default="FLAT")
    blowout_filter: bool = Field(default=True)

class OutcomeCalibrationRequest(BaseModel):
    include_push_as_half: bool = Field(default=False)
    min_samples: int = Field(default=50)

class ImportPredictionsRequest(BaseModel):
    path: str = Field(default="backend/data/predictions_settled.ndjson")
    dry_run: bool = Field(default=False)

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

def calculate_signal_ev(ev: float) -> str:
    """Calculate signal based on EV."""
    if ev >= 0.05:  # 5%+ EV is very good
        return "green"
    elif ev >= 0.02:  # 2%+ EV is acceptable
        return "yellow"
    return "red"

def normal_cdf(x: float) -> float:
    """Standard normal CDF using math.erf"""
    import math
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calculate_p_cover(pred_margin: float, cover_threshold: float, sigma: float, recommended_side: str) -> float:
    """
    DEPRECATED: Old approach that modeled margin as N(pred_margin, sigma).
    Use calculate_p_cover_vs_market instead.
    
    Raises error if called - this function should not be used.
    """
    raise NotImplementedError("calculate_p_cover is DEPRECATED. Use calculate_p_cover_vs_market instead.")


def calculate_p_cover_vs_market(
    model_edge: float, 
    alpha: float, 
    beta: float, 
    sigma_residual: float,
    recommended_side: str
) -> tuple[float, float]:
    """
    Calculate probability of covering using the MODEL VS MARKET approach.
    
    This models how the model's edge translates to actual outcomes:
    residual_real ~ N(beta * model_edge + alpha, sigma_residual)
    
    Where:
    - model_edge = pred_margin - cover_threshold (model's raw edge vs market)
    - beta: shrinkage factor (expected < 1, indicating overconfidence)
    - alpha: systematic bias
    - sigma_residual: uncertainty in residuals vs market
    
    For HOME cover: actual_margin > cover_threshold
    Equivalent to: residual_real > 0
    P(residual_real > 0) = P(Z > -mu/sigma) = Phi(mu/sigma)
    where mu = beta * model_edge + alpha
    
    Args:
        model_edge: pred_margin - cover_threshold
        alpha: calibrated intercept
        beta: calibrated slope (shrinkage)
        sigma_residual: calibrated std of residuals vs market (MUST NOT be 12.0)
        recommended_side: "HOME" or "AWAY"
    
    Returns:
        (p_cover, z_score)
    
    Raises:
        ValueError if sigma_residual is invalid
    """
    if sigma_residual <= 0 or sigma_residual == 12.0:
        raise ValueError(f"Invalid sigma_residual={sigma_residual}. Must be positive and not legacy default 12.0")
    
    # Expected value of residual vs market
    mu = beta * model_edge + alpha
    
    # z-score: how many sigmas away from 0 (the cover threshold in residual space)
    z = mu / sigma_residual
    
    if recommended_side == "HOME":
        # HOME covers if residual_real > 0
        # P(residual_real > 0) = Phi(mu / sigma_residual)
        p_cover = normal_cdf(z)
    else:
        # AWAY covers if residual_real < 0
        # P(residual_real < 0) = Phi(-mu / sigma_residual) = 1 - Phi(z)
        p_cover = normal_cdf(-z)
    
    return round(p_cover, 4), round(z, 4)

def calculate_ev(p_cover: float, price_decimal: float) -> float:
    """
    Calculate Expected Value given probability and decimal odds.
    
    EV = p_cover * price - 1
    
    Where:
    - p_cover: Our estimated probability of winning
    - price: Decimal odds (e.g., 1.91)
    - EV: Expected value per unit wagered
    
    Returns:
        EV as decimal (e.g., 0.05 = 5% edge)
    """
    if price_decimal <= 1.0:
        return -1.0  # Invalid odds
    
    ev = p_cover * price_decimal - 1.0
    return round(ev, 4)

def get_sigma_global() -> float:
    """Get the global sigma from config or computed value."""
    return OPERATIONAL_CONFIG["calibration"]["sigma_global"]

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

def madrid_day_key(dt_str: str) -> Optional[str]:
    """Return YYYY-MM-DD in Europe/Madrid for an ISO datetime string."""
    try:
        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        return dt.astimezone(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d")
    except Exception:
        return None

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


async def capture_closing_lines_task(window_minutes: int = 120, limit: int = 500) -> Dict[str, Any]:
    """
    Capture closing lines before event start (TheOddsAPI event odds endpoint expires after games).
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=5)
    cutoff = now + timedelta(minutes=window_minutes)
    now_iso = now.isoformat()

    query = {
        "result": None,
        "book": "pinnacle",
        "close_spread": None,
        "commence_time": {"$gte": window_start.isoformat(), "$lte": cutoff.isoformat()},
    }
    predictions = await db.predictions.find(query, {"_id": 0}).sort("commence_time", 1).to_list(limit)

    by_event: Dict[str, List[Dict[str, Any]]] = {}
    for p in predictions:
        event_id = p.get("event_id")
        if event_id:
            by_event.setdefault(event_id, []).append(p)

    n_events_considered = len(by_event)
    n_api_calls = 0
    n_updates = 0
    n_not_found = 0
    samples = []

    async with httpx.AsyncClient(timeout=20.0) as http_client:
        for event_id, event_preds in by_event.items():
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "eu",
                "markets": "spreads",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            }
            try:
                n_api_calls += 1
                resp = await http_client.get(
                    f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds",
                    params=params,
                )
            except Exception as e:
                n_not_found += len(event_preds)
                if len(samples) < 5:
                    samples.append({"event_id": event_id, "status": "request_error", "error": str(e)[:200]})
                continue

            if resp.status_code != 200:
                n_not_found += len(event_preds)
                if len(samples) < 5:
                    samples.append({"event_id": event_id, "status_code": resp.status_code})
                continue

            payload = resp.json()
            bookmakers = payload.get("bookmakers") if isinstance(payload, dict) else []
            pinnacle = next((b for b in bookmakers if b.get("key") == "pinnacle"), None)
            if not pinnacle:
                n_not_found += len(event_preds)
                if len(samples) < 5:
                    samples.append({"event_id": event_id, "status": "no_pinnacle"})
                continue

            spreads = next((m for m in (pinnacle.get("markets") or []) if m.get("key") == "spreads"), None)
            outcomes = (spreads or {}).get("outcomes") or []
            if len(outcomes) < 2:
                n_not_found += len(event_preds)
                if len(samples) < 5:
                    samples.append({"event_id": event_id, "status": "no_spread_outcomes"})
                continue

            for p in event_preds:
                home_team = p.get("home_team")
                away_team = p.get("away_team")
                recommended_side = p.get("recommended_side", "HOME")
                open_spread = p.get("open_spread")

                home_outcome = next((o for o in outcomes if o.get("name") == home_team), outcomes[0])
                away_outcome = next((o for o in outcomes if o.get("name") == away_team), outcomes[1] if len(outcomes) > 1 else outcomes[0])
                close_spread = home_outcome.get("point")
                close_price = home_outcome.get("price") if recommended_side == "HOME" else away_outcome.get("price")

                clv_spread = None
                if close_spread is not None and open_spread is not None:
                    if recommended_side == "HOME":
                        clv_spread = float(open_spread) - float(close_spread)
                    else:
                        clv_spread = float(close_spread) - float(open_spread)

                update_doc = {
                    "close_captured_at": now_iso,
                    "close_source": "theoddsapi",
                }
                if close_spread is not None:
                    update_doc["close_spread"] = close_spread
                if close_price is not None:
                    update_doc["close_price"] = round(float(close_price), 3)
                if clv_spread is not None:
                    update_doc["clv_spread"] = round(float(clv_spread), 2)

                if len(update_doc) > 2:
                    await db.predictions.update_one({"id": p["id"]}, {"$set": update_doc})
                    n_updates += 1

    return {
        "status": "completed",
        "window_minutes": window_minutes,
        "n_candidates": len(predictions),
        "n_events_considered": n_events_considered,
        "n_api_calls": n_api_calls,
        "n_updates_made": n_updates,
        "n_not_found": n_not_found,
        "samples": samples,
    }


async def collect_missing_closing_line_warnings(
    alert_hours: int = CLOSE_CAPTURE_ALERT_HOURS,
    lookback_days: int = CLOSE_CAPTURE_ALERT_LOOKBACK_DAYS,
    sample_limit: int = 10,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    alert_cutoff = now - timedelta(hours=max(1, int(alert_hours)))
    lookback_start = now - timedelta(days=max(1, int(lookback_days)))

    query = {
        "book": "pinnacle",
        "result": None,
        "close_spread": None,
        "commence_time": {"$gte": lookback_start.isoformat(), "$lte": alert_cutoff.isoformat()},
    }
    missing = await db.predictions.find(
        query,
        {"_id": 0, "id": 1, "event_id": 1, "commence_time": 1, "open_spread": 1, "recommended_side": 1},
    ).sort("commence_time", 1).to_list(5000)

    count = len(missing)
    if count > 0:
        logger.warning(
            "CLOSING_CAPTURE_DELAY_WARNING: %s picks missing close_spread after %sh (lookback=%sd)",
            count,
            alert_hours,
            lookback_days,
        )
    return {
        "missing_after_hours": alert_hours,
        "lookback_days": lookback_days,
        "count": count,
        "sample": missing[:sample_limit],
    }

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

async def closing_capture_scheduler_loop():
    """Simple scheduler: capture closing lines every 5 minutes."""
    while True:
        try:
            await capture_closing_lines_task(window_minutes=120, limit=500)
            await collect_missing_closing_line_warnings()
        except Exception as e:
            logger.error(f"closing_capture_scheduler_loop error: {e}")
        await asyncio.sleep(300)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting NBA Edge API v1.0 (Production)...")
    scheduler_task = asyncio.create_task(closing_capture_scheduler_loop())
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
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
    """Sync upcoming NBA events from The Odds API. Also cleans up past events."""
    
    # CLEANUP: Remove past events before syncing
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    cleanup_result = await db.upcoming_events.delete_many({
        "commence_time": {"$lt": now_str}
    })
    cleaned_count = cleanup_result.deleted_count
    
    # Also clean orphaned odds
    remaining_event_ids = [e['event_id'] async for e in db.upcoming_events.find({}, {"event_id": 1})]
    if remaining_event_ids:
        await db.market_lines.delete_many({"event_id": {"$nin": remaining_event_ids}})
    
    # Fetch and sync new events
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
    
    return SyncStatus(
        status="completed", 
        message=f"Synced {len(events)} events (cleaned {cleaned_count} past)", 
        details={"count": len(events), "cleaned": cleaned_count}
    )

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
async def snapshot_close_lines(minutes_before: int = 60, force: bool = False, user=Depends(get_current_user)):
    """Snapshot close lines for events starting soon"""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=minutes_before)
    
    # Find predictions for events starting soon
    predictions = await db.predictions.find({}).to_list(300)
    
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
            
            incoming_close_price = line['price_home_decimal'] if recommended_side == 'HOME' else line['price_away_decimal']
            now_ts = datetime.now(timezone.utc).isoformat()

            # Idempotent update:
            # - never overwrite non-null values unless force=true
            # - allow filling missing fields when new non-null values arrive
            update_doc = {}
            if close_spread is not None and (force or pred.get("close_spread") is None):
                update_doc["close_spread"] = close_spread
            if incoming_close_price is not None and (force or pred.get("close_price") is None):
                update_doc["close_price"] = incoming_close_price
            if clv_spread is not None and (force or pred.get("clv_spread") is None):
                update_doc["clv_spread"] = clv_spread
            if force or pred.get("close_source") is None:
                update_doc["close_source"] = "pinnacle"
            # Canonical timestamp field for writes
            if force or pred.get("close_captured_at") is None:
                update_doc["close_captured_at"] = now_ts

            if update_doc:
                await db.predictions.update_one(
                    {"id": pred['id']},
                    {"$set": update_doc}
                )
                updated_count += 1
    
    return SyncStatus(status="completed", message=f"Updated {updated_count} predictions with close lines",
                     details={"updated": updated_count, "minutes_before": minutes_before, "force": force})


@api_router.post("/admin/capture-closing-lines")
async def capture_closing_lines(window_minutes: int = 120, limit: int = 500, user=Depends(get_current_user)):
    """
    Capture close lines from TheOddsAPI before event start.
    """
    return await capture_closing_lines_task(window_minutes=window_minutes, limit=limit)


@api_router.post("/admin/capture-closing-lines/cron")
async def capture_closing_lines_cron(
    window_minutes: int = 120,
    limit: int = 500,
    x_cron_key: Optional[str] = Header(default=None, alias="X-CRON-KEY"),
):
    """
    Cron-safe endpoint for Render Cron Job.
    Requires CRON_API_KEY env and matching X-CRON-KEY header.
    """
    if not CRON_API_KEY:
        raise HTTPException(status_code=503, detail="CRON_API_KEY_NOT_CONFIGURED")
    if not x_cron_key or x_cron_key != CRON_API_KEY:
        raise HTTPException(status_code=401, detail="INVALID_CRON_KEY")
    capture = await capture_closing_lines_task(window_minutes=window_minutes, limit=limit)
    warnings = await collect_missing_closing_line_warnings()
    return {"status": "completed", "capture": capture, "warnings": warnings}


@api_router.get("/admin/diagnostics/closing-capture")
async def diagnostics_closing_capture(user=Depends(get_current_user)):
    now = datetime.now(timezone.utc).isoformat()
    open_query = {
        "result": None,
        "book": "pinnacle",
        "commence_time": {"$gte": now},
    }
    n_open_predictions = await db.predictions.count_documents(open_query)
    n_close_captured = await db.predictions.count_documents({**open_query, "close_spread": {"$ne": None}})
    pct_with_closing_line = (n_close_captured / n_open_predictions) if n_open_predictions > 0 else 0.0
    delayed = await collect_missing_closing_line_warnings(sample_limit=5)
    return {
        "n_open_predictions": n_open_predictions,
        "n_close_captured": n_close_captured,
        "pct_with_closing_line": pct_with_closing_line,
        "n_missing_after_alert_hours": delayed.get("count"),
        "missing_after_alert_hours_sample": delayed.get("sample", []),
    }

@api_router.post("/admin/refresh-results", response_model=SyncStatus)
async def refresh_results(user=Depends(get_current_user)):
    return SyncStatus(status="completed", message="Results refresh not yet implemented", details={})

# ============= PAPER TRADING v4.0 ENDPOINTS =============

# Default trading settings
DEFAULT_TRADING_SETTINGS = {
    "enabled_tiers": ["A", "B"],
    "blowout_filter_enabled": True,
    "blowout_pred_margin_threshold": 12.0,
    "max_picks_per_day": 3,
    "min_abs_model_edge": 1.5,
    "clv_gate_enabled": True,
    "dd_gate_enabled": True,
    "dd_gate_max_drawdown_threshold": 0.25,
    "use_outcome_calibration": True,
    "tier_a_min_p_cover_real": 0.54,
    "tier_b_min_p_cover_real": 0.52,
    "tier_c_min_p_cover_real": 0.50,
    "stake_mode": "FLAT",
    "flat_stake_pct": 0.01,
    "kelly_fraction": 0.20,
    "kelly_cap_pct": 0.02,
    "updated_at": None
}

@api_router.get("/admin/trading/settings")
async def get_trading_settings(user=Depends(get_current_user)):
    """Get current trading settings from DB"""
    settings = await db.trading_settings.find_one({"_id": "default"}, {"_id": 0})
    if not settings:
        # Return defaults if not configured
        return DEFAULT_TRADING_SETTINGS
    return settings

@api_router.post("/admin/trading/settings")
async def update_trading_settings(update: TradingSettingsUpdate, user=Depends(get_current_user)):
    """Update trading settings (persists to DB)"""
    # Get current settings
    current = await db.trading_settings.find_one({"_id": "default"})
    if not current:
        current = {**DEFAULT_TRADING_SETTINGS, "_id": "default"}
    
    # Apply updates
    update_dict = update.model_dump(exclude_none=True)
    for key, value in update_dict.items():
        current[key] = value
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    # Save to DB
    await db.trading_settings.update_one(
        {"_id": "default"},
        {"$set": current},
        upsert=True
    )
    
    # Return without _id
    del current["_id"]
    return current

@api_router.post("/admin/predictions/import")
async def import_predictions(payload: ImportPredictionsRequest, user=Depends(get_current_user)):
    """Import predictions from NDJSON with idempotent upsert rules."""
    from backend.migrate_predictions import import_predictions_from_ndjson

    try:
        result = await import_predictions_from_ndjson(
            db=db,
            path=payload.path,
            dry_run=payload.dry_run,
        )
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")

@api_router.post("/picks/{pick_id}/result")
async def register_pick_result(pick_id: str, result_input: PickResultInput, user=Depends(get_current_user)):
    """
    Register final result for a pick (Paper Trading v4.0).
    
    Calculates WIN/LOSS/PUSH based on spread and final scores.
    """
    # Find the pick
    pick = await db.predictions.find_one({"id": pick_id}, {"_id": 0})
    if not pick:
        raise HTTPException(status_code=404, detail=f"Pick not found: {pick_id}")
    
    # Calculate result
    final_home_score = result_input.final_home_score
    final_away_score = result_input.final_away_score
    margin_final = final_home_score - final_away_score
    
    open_spread = pick.get('open_spread', 0)
    open_price = pick.get('open_price', 1.91)
    recommended_side = pick.get('recommended_side', 'HOME')
    
    # Calculate if bet covered
    # HOME bet: covers if margin_final > -open_spread (or margin_final + spread > 0)
    # AWAY bet: covers if margin_final < -open_spread (or margin_final + spread < 0)
    # Note: spread is from home perspective (negative = home favorite)
    
    spread_adjusted_margin = margin_final + open_spread
    
    if result_input.result_override:
        # Admin override
        result = result_input.result_override.upper()
        if result not in ["WIN", "LOSS", "PUSH", "VOID"]:
            raise HTTPException(status_code=400, detail=f"Invalid result_override: {result}")
    else:
        # Calculate based on spread
        if recommended_side == "HOME":
            # Home bet wins if home margin exceeds the spread
            # E.g., spread=-5 means home must win by >5
            if spread_adjusted_margin > 0:
                result = "WIN"
            elif spread_adjusted_margin < 0:
                result = "LOSS"
            else:
                result = "PUSH"
        else:  # AWAY
            # Away bet wins if home margin is less than the spread
            if spread_adjusted_margin < 0:
                result = "WIN"
            elif spread_adjusted_margin > 0:
                result = "LOSS"
            else:
                result = "PUSH"
    
    # Calculate profit
    if result == "WIN":
        profit_units = open_price - 1  # e.g., 1.91 -> +0.91 units
        covered = True
    elif result == "LOSS":
        profit_units = -1.0
        covered = False
    elif result == "PUSH":
        profit_units = 0.0
        covered = None
    else:  # VOID
        profit_units = 0.0
        covered = None
    
    settled_at = datetime.now(timezone.utc).isoformat()
    
    # Update pick in DB
    update_doc = {
        "result": result,
        "final_home_score": final_home_score,
        "final_away_score": final_away_score,
        "margin_final": margin_final,
        "spread_adjusted_margin": spread_adjusted_margin,
        "covered": covered,
        "profit_units": round(profit_units, 4),
        "settled_at": settled_at
    }
    
    await db.predictions.update_one(
        {"id": pick_id},
        {"$set": update_doc}
    )
    
    return {
        "pick_id": pick_id,
        "home_team": pick.get('home_team'),
        "away_team": pick.get('away_team'),
        "recommended_side": recommended_side,
        "open_spread": open_spread,
        "open_price": open_price,
        "final_score": f"{final_home_score}-{final_away_score}",
        "margin_final": margin_final,
        "spread_adjusted_margin": round(spread_adjusted_margin, 1),
        "result": result,
        "profit_units": round(profit_units, 4),
        "covered": covered,
        "settled_at": settled_at
    }

@api_router.post("/admin/auto-grade-results")
async def auto_grade_results(days_back: int = 3, user=Depends(get_current_user)):
    """
    Auto-grade picks using scores from The Odds API.
    
    Fetches completed game scores and updates WIN/LOSS/PUSH for pending picks.
    """
    import httpx
    
    # Fetch scores from API
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{ODDS_API_BASE}/sports/basketball_nba/scores",
                params={
                    'apiKey': ODDS_API_KEY,
                    'daysFrom': days_back,
                    'dateFormat': 'iso'
                },
                timeout=30.0
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Odds API error: {resp.status_code}")
            
            games = resp.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch scores: {str(e)}")
    
    # Build lookup by event_id
    completed_games = {}
    for g in games:
        if g.get('completed') and g.get('scores'):
            scores = g['scores']
            home_score = next((int(s['score']) for s in scores if s['name'] == g['home_team']), None)
            away_score = next((int(s['score']) for s in scores if s['name'] == g['away_team']), None)
            if home_score is not None and away_score is not None:
                completed_games[g['id']] = {
                    'home_team': g['home_team'],
                    'away_team': g['away_team'],
                    'home_score': home_score,
                    'away_score': away_score
                }
    
    # Find pending picks
    pending_picks = await db.predictions.find(
        {"result": None},
        {"_id": 0}
    ).to_list(500)
    
    results = {
        "processed": 0,
        "graded": 0,
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "not_found": 0,
        "samples": []
    }
    
    now = datetime.now(timezone.utc).isoformat()
    
    for pick in pending_picks:
        event_id = pick.get('event_id')
        results["processed"] += 1
        
        if event_id not in completed_games:
            results["not_found"] += 1
            continue
        
        game = completed_games[event_id]
        home_score = game['home_score']
        away_score = game['away_score']
        margin_final = home_score - away_score
        
        open_spread = pick.get('open_spread', 0)
        open_price = pick.get('open_price', 1.91)
        recommended_side = pick.get('recommended_side', 'HOME')
        
        # Calculate spread-adjusted margin
        spread_adjusted_margin = margin_final + open_spread
        
        # Determine result
        if recommended_side == "HOME":
            if spread_adjusted_margin > 0:
                result = "WIN"
            elif spread_adjusted_margin < 0:
                result = "LOSS"
            else:
                result = "PUSH"
        else:  # AWAY
            if spread_adjusted_margin < 0:
                result = "WIN"
            elif spread_adjusted_margin > 0:
                result = "LOSS"
            else:
                result = "PUSH"
        
        # Calculate profit
        if result == "WIN":
            profit_units = open_price - 1
            covered = True
            results["wins"] += 1
        elif result == "LOSS":
            profit_units = -1.0
            covered = False
            results["losses"] += 1
        else:
            profit_units = 0.0
            covered = None
            results["pushes"] += 1
        
        # Update pick
        await db.predictions.update_one(
            {"id": pick['id']},
            {"$set": {
                "result": result,
                "final_home_score": home_score,
                "final_away_score": away_score,
                "margin_final": margin_final,
                "spread_adjusted_margin": round(spread_adjusted_margin, 1),
                "covered": covered,
                "profit_units": round(profit_units, 4),
                "settled_at": now
            }}
        )
        
        results["graded"] += 1
        
        if len(results["samples"]) < 10:
            results["samples"].append({
                "matchup": f"{pick.get('home_team', '?')[:15]} vs {pick.get('away_team', '?')[:15]}",
                "score": f"{home_score}-{away_score}",
                "spread": open_spread,
                "side": recommended_side,
                "result": result,
                "profit": round(profit_units, 2)
            })

    # Auto-trigger close snapshot backfill for recent games
    snapshot_info = await backfill_close_snapshot(db=db, days=max(days_back, 2), force=False)

    return {
        "status": "completed",
        "games_from_api": len(completed_games),
        "results": results,
        "close_snapshot_backfill": snapshot_info
    }

@api_router.post("/admin/snapshot-close")
async def snapshot_close_v2(window_minutes: int = 60, force: bool = False, user=Depends(get_current_user)):
    """
    Snapshot close lines for picks (Paper Trading v4.0).
    
    Captures close_spread, close_price for picks with events starting within window.
    Calculates CLV if open line exists.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=window_minutes)
    now_str = now.isoformat()
    cutoff_str = cutoff.isoformat()
    
    # Find picks in the target time window.
    # We keep selection broad and enforce idempotency at field update level.
    picks = await db.predictions.find({
        "commence_time": {"$lte": cutoff_str, "$gte": now_str}
    }, {"_id": 0}).to_list(300)
    
    results = {
        "updated": 0,
        "skipped": 0,
        "on_time": 0,
        "late": 0,
        "missing": 0,
        "samples": []
    }
    
    for pick in picks:
        event_id = pick.get('event_id')
        
        # Get latest Pinnacle line
        line = await db.market_lines.find_one({
            "event_id": event_id,
            "bookmaker_key": "pinnacle"
        }, {"_id": 0})
        
        if not line:
            results["missing"] += 1
            continue
        
        # Determine quality
        commence_time = pick.get('commence_time', '')
        try:
            commence_dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
            minutes_to_start = (commence_dt - now).total_seconds() / 60
            
            if minutes_to_start >= 30:
                quality = "ON_TIME"
                results["on_time"] += 1
            else:
                quality = "LATE"
                results["late"] += 1
        except:
            quality = "UNKNOWN"
        
        recommended_side = pick.get('recommended_side', 'HOME')
        open_spread = pick.get('open_spread', 0)
        close_spread = line.get('spread_point_home', 0)
        
        if recommended_side == 'HOME':
            close_price = line.get('price_home_decimal', 1.91)
            clv_spread = open_spread - close_spread
        else:
            close_price = line.get('price_away_decimal', 1.91)
            clv_spread = close_spread - open_spread
        
        # CLV price (optional)
        open_price = pick.get('open_price', 1.91)
        clv_price = close_price - open_price

        # Read-time fallback for legacy field (compat only).
        # Writes are canonical to close_captured_at.
        existing_captured_at = pick.get("close_captured_at") or pick.get("close_ts")

        # Idempotent update:
        # - never overwrite non-null with null
        # - fill missing fields when new non-null values arrive
        update_doc = {}
        if close_spread is not None and (force or pick.get("close_spread") is None):
            update_doc["close_spread"] = close_spread
        if close_price is not None and (force or pick.get("close_price") is None):
            update_doc["close_price"] = round(close_price, 3)
        if clv_spread is not None and (force or pick.get("clv_spread") is None):
            update_doc["clv_spread"] = round(clv_spread, 2)
        if clv_price is not None and (force or pick.get("clv_price") is None):
            update_doc["clv_price"] = round(clv_price, 3)
        if force or pick.get("close_quality") is None:
            update_doc["close_quality"] = quality
        if force or pick.get("close_source") is None:
            update_doc["close_source"] = "pinnacle"
        if force or existing_captured_at is None:
            update_doc["close_captured_at"] = now_str

        if update_doc:
            await db.predictions.update_one(
                {"id": pick['id']},
                {"$set": update_doc}
            )
            results["updated"] += 1
        else:
            results["skipped"] += 1
        
        if len(results["samples"]) < 5:
            results["samples"].append({
                "pick_id": pick['id'],
                "matchup": f"{pick.get('home_team')} vs {pick.get('away_team')}",
                "open_spread": open_spread,
                "close_spread": close_spread,
                "clv_spread": round(clv_spread, 2),
                "quality": quality
            })
    
    return {
        "status": "completed",
        "window_minutes": window_minutes,
        "force": force,
        "results": results
    }

@api_router.post("/admin/close-snapshot/backfill")
async def close_snapshot_backfill(
    days: int = 7,
    force: bool = False,
    debug: bool = False,
    debug_query: bool = False,
    fallback_time_field: str = "open_ts",
    user=Depends(get_current_user),
):
    """Backfill close lines for last N days with idempotent update semantics."""
    return await backfill_close_snapshot(
        db=db,
        days=days,
        force=force,
        debug=debug,
        debug_query=debug_query,
        fallback_time_field=fallback_time_field,
        odds_api_key=ODDS_API_KEY,
        odds_api_base=ODDS_API_BASE,
    )

@api_router.get("/admin/report/bankroll-sim")
async def bankroll_simulation(
    bankrolls: str = "1000,3000,5000,10000",
    tiers: str = "A,B",
    stake_mode: str = "FLAT",
    blowout_filter: bool = True,
    user=Depends(get_current_user)
):
    """
    Simulate bankroll performance across settled picks (Paper Trading v4.0).
    
    Returns profit/loss for each bankroll size.
    """
    # Parse params
    bankroll_list = [float(b.strip()) for b in bankrolls.split(",")]
    tier_list = [t.strip().upper() for t in tiers.split(",")]
    
    # Get trading settings
    settings = await db.trading_settings.find_one({"_id": "default"})
    if not settings:
        settings = DEFAULT_TRADING_SETTINGS
    
    flat_stake_pct = settings.get('flat_stake_pct', 0.01)
    kelly_fraction = settings.get('kelly_fraction', 0.20)
    kelly_cap_pct = settings.get('kelly_cap_pct', 0.02)
    blowout_threshold = settings.get('blowout_pred_margin_threshold', 12.0)
    
    # Get settled picks
    query = {
        "result": {"$in": ["WIN", "LOSS", "PUSH"]},
        "tier": {"$in": tier_list}
    }
    
    picks = await db.predictions.find(query, {"_id": 0}).sort("settled_at", 1).to_list(1000)
    
    # Apply blowout filter if enabled
    if blowout_filter:
        filtered_picks = []
        for p in picks:
            is_favorite = p.get('is_favorite_pick', False)
            pred_margin = abs(p.get('pred_margin', 0))
            
            # Re-calculate if not stored
            if 'is_favorite_pick' not in p:
                is_favorite = p.get('open_spread', 0) < 0 and p.get('recommended_side') == 'HOME'
                is_favorite = is_favorite or (p.get('open_spread', 0) > 0 and p.get('recommended_side') == 'AWAY')
            
            blowout_hit = is_favorite and pred_margin > blowout_threshold
            if not blowout_hit:
                filtered_picks.append(p)
        picks = filtered_picks
    
    # Simulate for each bankroll
    results = []
    
    for initial_bankroll in bankroll_list:
        bankroll = initial_bankroll
        peak = initial_bankroll
        max_drawdown = 0
        bets = 0
        wins = 0
        losses = 0
        pushes = 0
        
        for pick in picks:
            result = pick.get('result')
            p_cover = pick.get('p_cover', 0.5)
            open_price = pick.get('open_price', 1.91)
            profit_units = pick.get('profit_units', 0)
            
            # Calculate stake
            if stake_mode == "KELLY":
                # Kelly criterion: f* = (p*b - q) / b where b = price-1, p = p_cover, q = 1-p
                b = open_price - 1
                p = p_cover
                q = 1 - p
                kelly_optimal = (p * b - q) / b if b > 0 else 0
                kelly_stake = kelly_fraction * max(0, kelly_optimal)
                stake_pct = min(kelly_stake, kelly_cap_pct)
            else:  # FLAT
                stake_pct = flat_stake_pct
            
            stake = bankroll * stake_pct
            
            # Apply result
            if result == "WIN":
                profit = stake * (open_price - 1)
                bankroll += profit
                wins += 1
            elif result == "LOSS":
                bankroll -= stake
                losses += 1
            else:  # PUSH
                pushes += 1
            
            bets += 1
            
            # Track drawdown
            if bankroll > peak:
                peak = bankroll
            drawdown = (peak - bankroll) / peak if peak > 0 else 0
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        profit = bankroll - initial_bankroll
        roi_pct = (profit / initial_bankroll * 100) if initial_bankroll > 0 else 0
        winrate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
        
        results.append({
            "initial_bankroll": initial_bankroll,
            "final_bankroll": round(bankroll, 2),
            "profit": round(profit, 2),
            "roi_pct": round(roi_pct, 2),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "total_bets": bets,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "winrate_pct": round(winrate, 2)
        })
    
    # Summary by tier
    tier_summary = {}
    for tier in tier_list:
        tier_picks = [p for p in picks if p.get('tier') == tier]
        tier_wins = sum(1 for p in tier_picks if p.get('result') == 'WIN')
        tier_losses = sum(1 for p in tier_picks if p.get('result') == 'LOSS')
        tier_total = tier_wins + tier_losses
        tier_summary[tier] = {
            "total": len(tier_picks),
            "wins": tier_wins,
            "losses": tier_losses,
            "winrate_pct": round(tier_wins / tier_total * 100, 2) if tier_total > 0 else 0
        }
    
    return {
        "simulation_params": {
            "tiers": tier_list,
            "stake_mode": stake_mode,
            "blowout_filter": blowout_filter,
            "flat_stake_pct": flat_stake_pct if stake_mode == "FLAT" else None,
            "kelly_fraction": kelly_fraction if stake_mode == "KELLY" else None,
            "kelly_cap_pct": kelly_cap_pct if stake_mode == "KELLY" else None
        },
        "picks_analyzed": len(picks),
        "bankroll_results": results,
        "tier_summary": tier_summary
    }

@api_router.get("/admin/paper-trading/stats")
async def get_paper_trading_stats(
    from_date: str = None,
    to_date: str = None,
    tiers: str = "A,B",
    blowout_filter: bool = True,
    user=Depends(get_current_user)
):
    """
    Auditable Paper Trading Stats endpoint (v4.0).
    
    Returns calculated stats directly from DB predictions collection.
    This is the SOURCE OF TRUTH for the UI panel.
    """
    import numpy as np
    
    tier_list = [t.strip().upper() for t in tiers.split(",")]
    
    # Get trading settings for blowout threshold
    settings = await db.trading_settings.find_one({"_id": "default"})
    if not settings:
        settings = DEFAULT_TRADING_SETTINGS
    
    blowout_threshold = settings.get('blowout_pred_margin_threshold', 12.0)
    flat_stake_pct = settings.get('flat_stake_pct', 0.01)
    
    # Build query
    query = {
        "result": {"$in": ["WIN", "LOSS", "PUSH"]},
        "tier": {"$in": tier_list}
    }
    
    # Add date filters if provided
    if from_date:
        query["settled_at"] = {"$gte": from_date}
    if to_date:
        if "settled_at" in query:
            query["settled_at"]["$lte"] = to_date
        else:
            query["settled_at"] = {"$lte": to_date}
    
    # Get ALL settled picks matching criteria
    all_picks = await db.predictions.find(query, {"_id": 0}).sort("settled_at", 1).to_list(1000)
    
    # Apply blowout filter if enabled
    if blowout_filter:
        picks = []
        blowout_excluded = 0
        for p in all_picks:
            is_favorite = p.get('is_favorite_pick', False)
            pred_margin = abs(p.get('pred_margin', 0))
            blowout_hit = is_favorite and pred_margin > blowout_threshold
            if not blowout_hit:
                picks.append(p)
            else:
                blowout_excluded += 1
    else:
        picks = all_picks
        blowout_excluded = 0
    
    # Calculate stats
    wins = sum(1 for p in picks if p.get('result') == 'WIN')
    losses = sum(1 for p in picks if p.get('result') == 'LOSS')
    pushes = sum(1 for p in picks if p.get('result') == 'PUSH')
    total = len(picks)
    
    # Avg metrics
    odds_list = [p.get('open_price', 1.91) for p in picks]
    ev_list = [p.get('ev', 0) for p in picks]
    p_cover_list = [p.get('p_cover', 0.5) for p in picks]
    
    avg_odds = np.mean(odds_list) if odds_list else 0
    avg_ev = np.mean(ev_list) if ev_list else 0
    avg_p_cover = np.mean(p_cover_list) if p_cover_list else 0
    
    # Calculate profit/ROI with current flat_stake_pct
    bankroll = 1000.0  # Reference bankroll
    initial = bankroll
    peak = bankroll
    max_dd = 0
    
    for p in picks:
        result = p.get('result')
        price = p.get('open_price', 1.91)
        stake = bankroll * flat_stake_pct
        
        if result == 'WIN':
            bankroll += stake * (price - 1)
        elif result == 'LOSS':
            bankroll -= stake
        # PUSH = no change
        
        if bankroll > peak:
            peak = bankroll
        dd = (peak - bankroll) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    
    profit = bankroll - initial
    roi_pct = (profit / initial * 100) if initial > 0 else 0
    winrate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    
    # Tier breakdown
    tier_breakdown = {}
    for tier in tier_list:
        tier_picks = [p for p in picks if p.get('tier') == tier]
        tw = sum(1 for p in tier_picks if p.get('result') == 'WIN')
        tl = sum(1 for p in tier_picks if p.get('result') == 'LOSS')
        tp = sum(1 for p in tier_picks if p.get('result') == 'PUSH')
        tier_breakdown[tier] = {
            "wins": tw,
            "losses": tl,
            "pushes": tp,
            "total": len(tier_picks),
            "winrate_pct": round(tw / (tw + tl) * 100, 2) if (tw + tl) > 0 else 0
        }
    
    return {
        "source": "db.predictions",
        "query": {
            "result": ["WIN", "LOSS", "PUSH"],
            "tiers": tier_list,
            "from_date": from_date,
            "to_date": to_date,
            "blowout_filter": blowout_filter,
            "blowout_threshold": blowout_threshold
        },
        "counts": {
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "total": total,
            "blowout_excluded": blowout_excluded
        },
        "averages": {
            "avg_odds": round(avg_odds, 3),
            "avg_ev": round(avg_ev, 4),
            "avg_ev_pct": f"{avg_ev * 100:.2f}%",
            "avg_p_cover": round(avg_p_cover, 4),
            "avg_p_cover_pct": f"{avg_p_cover * 100:.2f}%"
        },
        "performance": {
            "winrate_pct": round(winrate, 2),
            "roi_pct": round(roi_pct, 2),
            "profit_eur": round(profit, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "reference_bankroll": initial,
            "flat_stake_pct": flat_stake_pct
        },
        "tier_breakdown": tier_breakdown,
        "picks_detail": [
            {
                "id": p.get('id'),
                "matchup": f"{p.get('home_team', '?')[:15]} vs {p.get('away_team', '?')[:15]}",
                "tier": p.get('tier'),
                "result": p.get('result'),
                "profit_units": p.get('profit_units'),
                "open_price": p.get('open_price'),
                "ev": p.get('ev'),
                "p_cover": p.get('p_cover'),
                "is_favorite": p.get('is_favorite_pick'),
                "settled_at": p.get('settled_at')
            }
            for p in picks
        ]
    }

@api_router.post("/admin/model/sigma/recompute")
async def recompute_sigma(season: str = None, min_games: int = 100, user=Depends(get_current_user)):
    """
    Compute sigma (prediction uncertainty) from historical PRE-MATCH residuals.
    
    residual = (home_pts - away_pts) - pred_margin_pre_match
    sigma = std(residuals)
    
    Uses rolling window features computed BEFORE each game to get pred_margin_pre_match.
    """
    import numpy as np
    import joblib
    import io
    
    # Get active model
    model_doc = await db.models.find_one({"is_active": True})
    if not model_doc:
        raise HTTPException(status_code=400, detail="No active model")
    
    # Load model data (contains model, scaler, features)
    model_data = joblib.load(io.BytesIO(model_doc['model_binary']))
    model = model_data['model']
    scaler = model_data['scaler']
    feature_cols = model_data['features']
    
    # Get all historical games
    query = {}
    if season:
        query["season"] = season
    
    games = await db.games.find(query, {"_id": 0}).sort("game_date", 1).to_list(10000)
    
    if len(games) < min_games:
        # Return warning with default sigma
        return {
            "status": "insufficient_data",
            "warning": f"Only {len(games)} games available, need at least {min_games}",
            "sigma_global": OPERATIONAL_CONFIG["calibration"]["sigma_global"],
            "sigma_source": "default",
            "n_games_available": len(games),
            "flags": ["INSUFFICIENT_DATA_USING_DEFAULT"]
        }
    
    # Build team game history for rolling window
    N = OPERATIONAL_CONFIG["rolling_window_n"]
    team_games = {}
    for game in games:
        for team_abbr in [game['home_team'], game['away_team']]:
            if team_abbr not in team_games:
                team_games[team_abbr] = []
            team_games[team_abbr].append(game)
    
    residuals = []
    residuals_by_season = {}
    analyzed_games = []
    
    for game in games:
        game_date = game['game_date']
        home_abbr, away_abbr = game['home_team'], game['away_team']
        
        # Get PRE-MATCH games (before this game date)
        home_prev = [g for g in team_games.get(home_abbr, []) if g['game_date'] < game_date][-N:]
        away_prev = [g for g in team_games.get(away_abbr, []) if g['game_date'] < game_date][-N:]
        
        if len(home_prev) < 5 or len(away_prev) < 5:
            continue
        
        # Calculate PRE-MATCH features
        home_stats = await calculate_team_stats_from_games(home_abbr, home_prev)
        away_stats = await calculate_team_stats_from_games(away_abbr, away_prev)
        
        if not home_stats or not away_stats:
            continue
        
        home_rest = calculate_rest_days(game_date, home_prev)
        away_rest = calculate_rest_days(game_date, away_prev)
        
        features = {
            "diff_net_rating": home_stats['net_rating'] - away_stats['net_rating'],
            "diff_pace": home_stats['pace'] - away_stats['pace'],
            "diff_efg": home_stats['efg'] - away_stats['efg'],
            "diff_tov_pct": home_stats['tov_pct'] - away_stats['tov_pct'],
            "diff_orb_pct": home_stats['orb_pct'] - away_stats['orb_pct'],
            "diff_ftr": home_stats['ftr'] - away_stats['ftr'],
            "diff_rest": home_rest - away_rest,
            "home_advantage": 1
        }
        
        # Build feature vector
        X = np.array([[features.get(col, 0) for col in feature_cols]])
        
        try:
            X_scaled = scaler.transform(X)
            pred_margin_pre_match = float(model.predict(X_scaled)[0])
        except Exception as e:
            continue
        
        # Actual margin
        actual_margin = game.get('home_pts', 0) - game.get('away_pts', 0)
        
        # Residual = actual - predicted
        residual = actual_margin - pred_margin_pre_match
        residuals.append(residual)
        
        # Track by season
        game_season = game.get('season', 'unknown')
        if game_season not in residuals_by_season:
            residuals_by_season[game_season] = []
        residuals_by_season[game_season].append(residual)
        
        analyzed_games.append({
            "game_id": game.get('game_id'),
            "season": game_season,
            "actual_margin": actual_margin,
            "pred_margin": round(pred_margin_pre_match, 2),
            "residual": round(residual, 2)
        })
    
    if len(residuals) < min_games:
        return {
            "status": "insufficient_data",
            "warning": f"Only {len(residuals)} games with valid features, need at least {min_games}",
            "sigma_global": OPERATIONAL_CONFIG["calibration"]["sigma_global"],
            "sigma_source": "default",
            "n_analyzed": len(residuals),
            "flags": ["INSUFFICIENT_DATA_USING_DEFAULT"]
        }
    
    # Calculate sigma and statistics
    sigma_global = float(np.std(residuals))
    mean_residual = float(np.mean(residuals))
    
    # Per-season sigmas
    sigma_by_season = {}
    for s, res in residuals_by_season.items():
        if len(res) >= 20:
            sigma_by_season[s] = {
                "sigma": round(float(np.std(res)), 2),
                "mean_residual": round(float(np.mean(res)), 2),
                "n_games": len(res)
            }
    
    # Update global config
    OPERATIONAL_CONFIG["calibration"]["sigma_global"] = round(sigma_global, 2)
    OPERATIONAL_CONFIG["calibration"]["sigma_source"] = "computed"
    OPERATIONAL_CONFIG["calibration"]["computed_at"] = datetime.now(timezone.utc).isoformat()
    OPERATIONAL_CONFIG["calibration"]["n_samples"] = len(residuals)
    
    # Save to database for persistence
    await db.model_calibration.update_one(
        {"key": "sigma"},
        {"$set": {
            "sigma_global": round(sigma_global, 2),
            "sigma_source": "computed",  # Mark as computed from historical data
            "mean_residual": round(mean_residual, 2),
            "sigma_by_season": sigma_by_season,
            "n_samples": len(residuals),
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "model_version": model_doc.get('model_version'),
            "residual_stats": {
                "mean": round(mean_residual, 2),
                "std": round(sigma_global, 2),
                "min": round(float(np.min(residuals)), 2),
                "max": round(float(np.max(residuals)), 2),
                "percentiles": {
                    "p25": round(float(np.percentile(residuals, 25)), 2),
                    "p50": round(float(np.percentile(residuals, 50)), 2),
                    "p75": round(float(np.percentile(residuals, 75)), 2),
                    "p90": round(float(np.percentile(residuals, 90)), 2)
                }
            }
        }},
        upsert=True
    )
    
    # Generate flags
    flags = []
    if sigma_global < 8:
        flags.append("WARNING_SIGMA_TOO_LOW (<8)")
    if sigma_global > 20:
        flags.append("WARNING_SIGMA_TOO_HIGH (>20)")
    if abs(mean_residual) > 2:
        flags.append(f"WARNING_MEAN_RESIDUAL_BIAS ({mean_residual:.2f})")
    
    return {
        "status": "completed",
        "sigma_global": round(sigma_global, 2),
        "sigma_source": "computed",
        "mean_residual": round(mean_residual, 2),
        "n_samples": len(residuals),
        "sigma_by_season": sigma_by_season,
        "residual_stats": {
            "min": round(float(np.min(residuals)), 2),
            "max": round(float(np.max(residuals)), 2),
            "p25": round(float(np.percentile(residuals, 25)), 2),
            "p50": round(float(np.percentile(residuals, 50)), 2),
            "p75": round(float(np.percentile(residuals, 75)), 2)
        },
        "flags": flags,
        "recommended_range": "8 <= sigma <= 20",
        "computed_at": datetime.now(timezone.utc).isoformat()
    }

@api_router.get("/admin/model/sigma")
async def get_sigma(user=Depends(get_current_user)):
    """Get current sigma calibration."""
    # Try to get from database first
    doc = await db.model_calibration.find_one({"key": "sigma"}, {"_id": 0})
    if doc:
        return doc
    
    # Return default from config
    return {
        "sigma_global": OPERATIONAL_CONFIG["calibration"]["sigma_global"],
        "sigma_source": OPERATIONAL_CONFIG["calibration"]["sigma_source"],
        "warning": "Using default sigma. Run /admin/model/sigma/recompute to calculate from historical data."
    }


@api_router.post("/admin/model/calibrate-vs-market")
async def calibrate_vs_market(min_games: int = 100, user=Depends(get_current_user)):
    """
    Calibrate the model's edge vs market spread using historical data.
    
    This learns how the model's raw edge translates to actual cover outcomes:
    residual_vs_market ~ N(beta * model_edge + alpha, sigma_residual)
    
    Where:
    - model_edge = pred_margin - cover_threshold
    - residual_vs_market = actual_margin - cover_threshold
    
    This is a LINEAR REGRESSION:
    Y = residual_vs_market
    X = model_edge
    Y ~ N(alpha + beta * X, sigma_residual)
    
    Expected results:
    - beta < 1: model is overconfident (typical)
    - beta ≈ 0: model provides no signal
    - beta > 1: model is underconfident (rare)
    - alpha ≈ 0: no systematic bias vs market
    """
    import numpy as np
    import joblib
    import io
    from scipy import stats as scipy_stats
    
    # Get active model
    model_doc = await db.models.find_one({"is_active": True})
    if not model_doc:
        raise HTTPException(status_code=400, detail="No active model")
    
    # Load model
    model_data = joblib.load(io.BytesIO(model_doc['model_binary']))
    model = model_data['model']
    scaler = model_data['scaler']
    feature_cols = model_data['features']
    
    # Get all historical games
    games = await db.games.find({}, {"_id": 0}).sort("game_date", 1).to_list(10000)
    
    if len(games) < min_games:
        return {
            "status": "insufficient_data",
            "warning": f"Only {len(games)} games available, need at least {min_games}",
            "flags": ["INSUFFICIENT_DATA"]
        }
    
    # Build team game history for rolling window
    N = OPERATIONAL_CONFIG["rolling_window_n"]
    team_games = {}
    for game in games:
        for team_abbr in [game['home_team'], game['away_team']]:
            if team_abbr not in team_games:
                team_games[team_abbr] = []
            team_games[team_abbr].append(game)
    
    # Collect calibration data points
    calibration_data = []
    calibration_with_spread = []
    
    for game in games:
        game_date = game['game_date']
        home_abbr, away_abbr = game['home_team'], game['away_team']
        
        # Get PRE-MATCH games (before this game date)
        home_prev = [g for g in team_games.get(home_abbr, []) if g['game_date'] < game_date][-N:]
        away_prev = [g for g in team_games.get(away_abbr, []) if g['game_date'] < game_date][-N:]
        
        if len(home_prev) < 5 or len(away_prev) < 5:
            continue
        
        # Check for historical spread data
        market_spread = None
        spread_source = None
        
        # Try predictions collection
        prediction = await db.predictions.find_one({
            "home_abbr": home_abbr,
            "away_abbr": away_abbr,
        }, {"_id": 0})
        
        if prediction and prediction.get('open_spread') is not None:
            market_spread = prediction['open_spread']
            spread_source = "predictions"
        
        # Calculate PRE-MATCH features
        home_stats = await calculate_team_stats_from_games(home_abbr, home_prev)
        away_stats = await calculate_team_stats_from_games(away_abbr, away_prev)
        
        if not home_stats or not away_stats:
            continue
        
        home_rest = calculate_rest_days(game_date, home_prev)
        away_rest = calculate_rest_days(game_date, away_prev)
        
        features = {
            "diff_net_rating": home_stats['net_rating'] - away_stats['net_rating'],
            "diff_pace": home_stats['pace'] - away_stats['pace'],
            "diff_efg": home_stats['efg'] - away_stats['efg'],
            "diff_tov_pct": home_stats['tov_pct'] - away_stats['tov_pct'],
            "diff_orb_pct": home_stats['orb_pct'] - away_stats['orb_pct'],
            "diff_ftr": home_stats['ftr'] - away_stats['ftr'],
            "diff_rest": home_rest - away_rest,
            "home_advantage": 1
        }
        
        X = np.array([[features.get(col, 0) for col in feature_cols]])
        
        try:
            X_scaled = scaler.transform(X)
            pred_margin = float(model.predict(X_scaled)[0])
        except Exception:
            continue
        
        actual_margin = game.get('home_pts', 0) - game.get('away_pts', 0)
        
        # Store basic calibration data (without spread)
        calibration_data.append({
            "game_id": game.get('game_id'),
            "season": game.get('season', 'unknown'),
            "pred_margin": round(pred_margin, 2),
            "actual_margin": actual_margin,
            "residual": round(actual_margin - pred_margin, 2)
        })
        
        # If we have spread data, store for beta estimation
        if market_spread is not None:
            cover_threshold = -market_spread
            model_edge = pred_margin - cover_threshold
            residual_vs_market = actual_margin - cover_threshold
            
            calibration_with_spread.append({
                "game_id": game.get('game_id'),
                "pred_margin": round(pred_margin, 2),
                "actual_margin": actual_margin,
                "market_spread": market_spread,
                "model_edge": round(model_edge, 2),
                "residual_vs_market": round(residual_vs_market, 2),
                "spread_source": spread_source
            })
    
    n_total = len(calibration_data)
    n_with_spread = len(calibration_with_spread)
    
    if n_total < 50:
        return {
            "status": "insufficient_data",
            "warning": f"Only {n_total} valid samples for calibration",
            "flags": ["INSUFFICIENT_DATA"]
        }
    
    # Calculate sigma_residual from all games (model prediction error)
    residuals = np.array([d['residual'] for d in calibration_data])
    sigma_from_model = float(np.std(residuals))
    mean_residual_model = float(np.mean(residuals))
    
    # Determine beta and alpha
    if n_with_spread >= 30:
        # Enough data to estimate beta via regression
        X_calib = np.array([d['model_edge'] for d in calibration_with_spread])
        Y_calib = np.array([d['residual_vs_market'] for d in calibration_with_spread])
        
        # Check for sufficient variance in X
        if np.std(X_calib) > 0.5:
            slope, intercept, r_value, p_value, std_err = scipy_stats.linregress(X_calib, Y_calib)
            beta = float(slope)
            alpha = float(intercept)
            r_squared = float(r_value ** 2)
            beta_source = "regression"
            
            # Recalculate sigma_residual from regression residuals
            Y_pred = alpha + beta * X_calib
            regression_residuals = Y_calib - Y_pred
            sigma_residual = float(np.std(regression_residuals))
        else:
            # Not enough variance, use defaults
            beta = 0.35  # Conservative shrinkage
            alpha = 0.0
            r_squared = 0.0
            p_value = 1.0
            std_err = 0.0
            sigma_residual = sigma_from_model
            beta_source = "default_low_variance"
    else:
        # Not enough spread data - use empirically-derived defaults
        # Research suggests beta ≈ 0.3-0.5 for NBA point spread models
        # Using 0.35 for conservative estimate (model edge is 35% predictive)
        beta = 0.35
        alpha = 0.0  # Assume no systematic bias
        r_squared = 0.0
        p_value = 1.0
        std_err = 0.0
        sigma_residual = sigma_from_model
        beta_source = f"default_insufficient_spread_data (n={n_with_spread})"
    
    # ============= BAYESIAN SHRINKAGE =============
    # Apply shrinkage to regularize beta and alpha based on sample size
    # This prevents overconfident estimates when n_spread_samples is small
    
    # Shrinkage parameters (configurable)
    BETA_PRIOR = 0.35  # Conservative prior for beta
    ALPHA_PRIOR = 0.0  # No systematic bias prior
    K_SHRINKAGE = 200  # Shrinkage strength (higher = more shrinkage)
    MIN_SAMPLES_FOR_FULL_TRUST = 200  # Below this, apply clamp
    # Safety clamps (not to fix constants, just safety bounds)
    BETA_CLAMP_MIN = 0.20
    BETA_CLAMP_MAX = 0.70
    ALPHA_CLAMP_MIN = -1.0
    ALPHA_CLAMP_MAX = 1.0
    
    # Calculate shrinkage weight
    # w = n / (n + k): when n is small, w is small, so we trust prior more
    w = n_with_spread / (n_with_spread + K_SHRINKAGE)
    
    # Store raw regression values
    beta_reg = beta
    alpha_reg = alpha
    
    # Apply shrinkage: effective = w * regression + (1-w) * prior
    beta_effective = w * beta_reg + (1 - w) * BETA_PRIOR
    alpha_effective = w * alpha_reg + (1 - w) * ALPHA_PRIOR
    
    # Safety clamp if sample size is below threshold
    beta_clamped = False
    alpha_clamped = False
    if n_with_spread < MIN_SAMPLES_FOR_FULL_TRUST:
        if beta_effective < BETA_CLAMP_MIN or beta_effective > BETA_CLAMP_MAX:
            beta_clamped = True
            beta_effective = max(BETA_CLAMP_MIN, min(BETA_CLAMP_MAX, beta_effective))
        if alpha_effective < ALPHA_CLAMP_MIN or alpha_effective > ALPHA_CLAMP_MAX:
            alpha_clamped = True
            alpha_effective = max(ALPHA_CLAMP_MIN, min(ALPHA_CLAMP_MAX, alpha_effective))
    
    # Use effective values for all downstream calculations
    beta = beta_effective
    alpha = alpha_effective
    
    # Generate calibration_id
    calibration_id = f"calib_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    computed_at = datetime.now(timezone.utc).isoformat()
    
    # Deactivate any previous active calibration
    await db.calibrations.update_many(
        {"is_active": True},
        {"$set": {"is_active": False}}
    )
    
    # Save new calibration as active (not locked by default)
    calibration_doc = {
        "calibration_id": calibration_id,
        "probability_mode": "VS_MARKET",
        # Effective values (used for calculations)
        "alpha": round(alpha_effective, 4),
        "beta": round(beta_effective, 4),
        "sigma_residual": round(sigma_residual, 2),
        # Aliases for clarity
        "alpha_effective": round(alpha_effective, 4),
        "beta_effective": round(beta_effective, 4),
        # Raw regression values
        "alpha_reg": round(alpha_reg, 4),
        "beta_reg": round(beta_reg, 4),
        # Prior values
        "alpha_prior": ALPHA_PRIOR,
        "beta_prior": BETA_PRIOR,
        # Shrinkage parameters
        "k_used": K_SHRINKAGE,
        "w_used": round(w, 4),
        "min_samples_full_trust": MIN_SAMPLES_FOR_FULL_TRUST,
        "beta_clamped": beta_clamped,
        "alpha_clamped": alpha_clamped,
        "clamp_ranges": {
            "beta": [BETA_CLAMP_MIN, BETA_CLAMP_MAX],
            "alpha": [ALPHA_CLAMP_MIN, ALPHA_CLAMP_MAX]
        },
        # Source info
        "beta_source": beta_source,
        "sigma_source": "historical_residuals",
        "n_spread_samples": n_with_spread,
        "n_residual_samples": n_total,
        "r_squared": round(r_squared, 4) if r_squared > 0 else None,
        "p_value": round(p_value, 6) if p_value != 1.0 else None,
        "computed_at": computed_at,
        "data_cutoff": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "model_version": model_doc.get('model_version'),
        "is_active": True,
        "is_locked": False,
        "is_auditable": True,
        "model_residual_stats": {
            "mean": round(mean_residual_model, 2),
            "std": round(sigma_from_model, 2),
        }
    }
    
    await db.calibrations.insert_one(calibration_doc)
    
    # Also update the legacy key for backward compatibility
    await db.model_calibration.update_one(
        {"key": "vs_market"},
        {"$set": {
            "calibration_id": calibration_id,
            "alpha": round(alpha_effective, 4),
            "beta": round(beta_effective, 4),
            "sigma_residual": round(sigma_residual, 2),
            "beta_source": beta_source,
            "computed_at": computed_at,
            "model_version": model_doc.get('model_version'),
        }},
        upsert=True
    )
    
    # Generate flags
    flags = []
    if beta_effective < 0.25:
        flags.append(f"WARNING_BETA_EFFECTIVE_LOW ({beta_effective:.3f})")
    if beta_effective > 0.65:
        flags.append(f"WARNING_BETA_EFFECTIVE_HIGH ({beta_effective:.3f})")
    if abs(alpha_effective) > 1.5:
        flags.append(f"WARNING_ALPHA_EFFECTIVE_BIAS ({alpha_effective:.2f})")
    if sigma_residual < 10:
        flags.append("WARNING_SIGMA_RESIDUAL_LOW")
    if sigma_residual > 20:
        flags.append("WARNING_SIGMA_RESIDUAL_HIGH")
    if n_with_spread < 50:
        flags.append(f"INFO_LOW_SPREAD_SAMPLES (n={n_with_spread}, high shrinkage applied)")
    if n_with_spread < MIN_SAMPLES_FOR_FULL_TRUST:
        flags.append(f"INFO_BETA_CLAMPED_TO_[{BETA_CLAMP_MIN},{BETA_CLAMP_MAX}]")
    
    return {
        "status": "completed",
        "calibration_id": calibration_id,
        "probability_mode": "VS_MARKET",
        # Effective values (what's actually used)
        "alpha": round(alpha_effective, 4),
        "beta": round(beta_effective, 4),
        "sigma_residual": round(sigma_residual, 2),
        # Shrinkage details
        "shrinkage": {
            "beta_reg": round(beta_reg, 4),
            "beta_prior": BETA_PRIOR,
            "beta_effective": round(beta_effective, 4),
            "alpha_reg": round(alpha_reg, 4),
            "alpha_prior": ALPHA_PRIOR,
            "alpha_effective": round(alpha_effective, 4),
            "k_used": K_SHRINKAGE,
            "w_used": round(w, 4),
            "n_spread_samples": n_with_spread,
            "min_samples_full_trust": MIN_SAMPLES_FOR_FULL_TRUST,
            "beta_clamped": n_with_spread < MIN_SAMPLES_FOR_FULL_TRUST,
            "interpretation": f"w={w:.2f} means {w*100:.0f}% regression, {(1-w)*100:.0f}% prior"
        },
        "beta_source": beta_source,
        "sigma_source": "historical_residuals",
        "n_spread_samples": n_with_spread,
        "n_residual_samples": n_total,
        "r_squared": round(r_squared, 4) if r_squared > 0 else None,
        "computed_at": computed_at,
        "data_cutoff": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "model_version": model_doc.get('model_version'),
        "is_active": True,
        "is_locked": False,
        "flags": flags
    }


@api_router.post("/admin/model/calibrate-outcome")
async def calibrate_outcome(payload: OutcomeCalibrationRequest, user=Depends(get_current_user)):
    """
    Fit binary outcome calibration on settled picks.
    Model is independent from Ridge and only maps model_edge/open line to p_cover_real.
    """
    return await fit_outcome_calibration(
        db=db,
        include_push_as_half=payload.include_push_as_half,
        min_samples=payload.min_samples,
    )


@api_router.get("/admin/model/calibration-outcome/current")
async def get_current_outcome_calibration(user=Depends(get_current_user)):
    doc = await get_active_outcome_calibration(db)
    if not doc:
        return {
            "error": "NO_ACTIVE_OUTCOME_CALIBRATION",
            "message": "Run POST /api/admin/model/calibrate-outcome first."
        }
    return {
        "n_samples": doc.get("n_samples"),
        "feature_names": doc.get("feature_names") or doc.get("features"),
        "coefficients": doc.get("coefficients"),
        "intercept": doc.get("intercept"),
        "data_cutoff": doc.get("data_cutoff"),
    }


@api_router.get("/admin/model/calibration-outcome/diagnostics")
async def get_outcome_calibration_diagnostics_route(bins: int = 5, user=Depends(get_current_user)):
    return await get_outcome_calibration_diagnostics(db=db, bins=bins)


@api_router.get("/admin/calibration/current")
async def get_current_calibration(user=Depends(get_current_user)):
    """
    Get the currently active calibration with full audit info.
    This is the single source of truth for all probability calculations.
    """
    # Get active calibration from DB
    calibration = await db.calibrations.find_one(
        {"is_active": True},
        {"_id": 0}
    )
    
    if not calibration:
        return {
            "error": "NO_ACTIVE_CALIBRATION",
            "message": "No active calibration found. Run POST /api/admin/model/calibrate-vs-market first.",
            "is_auditable": False
        }
    
    # Verify all required fields are present
    required_fields = ["calibration_id", "probability_mode", "alpha", "beta", "sigma_residual", 
                       "beta_source", "computed_at", "model_version"]
    missing_fields = [f for f in required_fields if f not in calibration or calibration[f] is None]
    
    if missing_fields:
        return {
            "error": "INCOMPLETE_CALIBRATION",
            "message": f"Calibration missing required fields: {missing_fields}",
            "is_auditable": False,
            "calibration": calibration
        }
    
    # Validate sigma is not 12.0 (legacy default)
    if calibration.get("sigma_residual") == 12.0:
        return {
            "error": "LEGACY_SIGMA_DETECTED",
            "message": "sigma_residual=12.0 is legacy default. Re-run calibration.",
            "is_auditable": False,
            "calibration": calibration
        }
    
    return {
        "calibration_id": calibration["calibration_id"],
        "probability_mode": calibration["probability_mode"],
        # Effective values (used for all calculations)
        "alpha": calibration["alpha"],
        "beta": calibration["beta"],
        "sigma_residual": calibration["sigma_residual"],
        # Effective values (aliases)
        "beta_effective": calibration.get("beta_effective", calibration["beta"]),
        "alpha_effective": calibration.get("alpha_effective", calibration["alpha"]),
        # Raw regression values
        "beta_reg": calibration.get("beta_reg"),
        "alpha_reg": calibration.get("alpha_reg"),
        # Prior values
        "beta_prior": calibration.get("beta_prior"),
        "alpha_prior": calibration.get("alpha_prior"),
        # Shrinkage parameters
        "k_used": calibration.get("k_used") or calibration.get("k_shrinkage"),
        "w_used": calibration.get("w_used") or calibration.get("w_shrinkage"),
        "beta_clamped": calibration.get("beta_clamped", False),
        "alpha_clamped": calibration.get("alpha_clamped", False),
        # Source info
        "beta_source": calibration.get("beta_source", "unknown"),
        "sigma_source": calibration.get("sigma_source", "unknown"),
        "n_spread_samples": calibration.get("n_spread_samples", 0),
        "n_residual_samples": calibration.get("n_residual_samples", 0),
        "computed_at": calibration["computed_at"],
        "data_cutoff": calibration.get("data_cutoff"),
        "model_version": calibration["model_version"],
        "is_active": calibration["is_active"],
        "is_locked": calibration.get("is_locked", False),
        "is_auditable": calibration.get("is_auditable", True)
    }


@api_router.post("/admin/calibration/lock")
async def lock_calibration(calibration_id: str, user=Depends(get_current_user)):
    """Lock a calibration to prevent accidental recalculation."""
    result = await db.calibrations.update_one(
        {"calibration_id": calibration_id},
        {"$set": {"is_locked": True}}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Calibration not found")
    return {"status": "locked", "calibration_id": calibration_id}


@api_router.get("/admin/model/calibration")
async def get_calibration(user=Depends(get_current_user)):
    """Get current calibration (vs_market or legacy sigma)."""
    # Try vs_market calibration first
    vs_market = await db.model_calibration.find_one({"key": "vs_market"}, {"_id": 0})
    sigma = await db.model_calibration.find_one({"key": "sigma"}, {"_id": 0})
    
    if vs_market:
        return {
            "calibration_type": "vs_market",
            "vs_market": vs_market,
            "legacy_sigma": sigma,
            "active": "vs_market"
        }
    elif sigma:
        return {
            "calibration_type": "legacy_sigma",
            "legacy_sigma": sigma,
            "active": "legacy_sigma",
            "warning": "Using legacy sigma calibration. Run /admin/model/calibrate-vs-market for better results."
        }
    else:
        return {
            "calibration_type": "default",
            "warning": "No calibration found. Using defaults.",
            "defaults": OPERATIONAL_CONFIG["calibration"]
        }

# ============= PIPELINE DEBUG ENDPOINT =============

@api_router.get("/admin/debug/pipeline-status")
async def debug_pipeline_status(user=Depends(get_current_user)):
    """
    Comprehensive pipeline diagnostic endpoint.
    Returns detailed telemetry for debugging pick generation issues.
    """
    import numpy as np
    from datetime import timedelta
    
    now = datetime.now(timezone.utc)
    result = {
        "timestamp": now.isoformat(),
        "sections": {}
    }
    
    # ============= A) UPCOMING EVENTS =============
    upcoming_section = {}
    
    # Total events
    upcoming_total = await db.upcoming_events.count_documents({})
    upcoming_pending = await db.upcoming_events.count_documents({"status": "pending"})
    
    # Future events (commence_time > now)
    upcoming_future = await db.upcoming_events.count_documents({
        "commence_time": {"$gt": now.isoformat()}
    })
    
    # Sample events
    upcoming_sample = await db.upcoming_events.find(
        {"status": "pending"},
        {"_id": 0, "event_id": 1, "home_team": 1, "away_team": 1, "commence_time": 1, "status": 1}
    ).sort("commence_time", 1).limit(5).to_list(5)
    
    # Add local time to samples
    for e in upcoming_sample:
        e['game_time_utc'] = e.get('commence_time')
        e['game_time_local'] = format_local_time(e.get('commence_time')) if e.get('commence_time') else None
        e['is_future'] = e.get('commence_time', '') > now.isoformat() if e.get('commence_time') else False
    
    upcoming_section = {
        "upcoming_events_total": upcoming_total,
        "upcoming_events_pending": upcoming_pending,
        "upcoming_events_future": upcoming_future,
        "upcoming_events_sample": upcoming_sample
    }
    result["sections"]["A_upcoming"] = upcoming_section
    
    # ============= B) ODDS =============
    odds_section = {}
    
    # Total odds in last 48h
    cutoff_48h = (now - timedelta(hours=48)).isoformat()
    odds_total = await db.market_lines.count_documents({})
    odds_recent = await db.market_lines.count_documents({"updated_at": {"$gte": cutoff_48h}})
    
    # Pinnacle odds
    odds_pinnacle = await db.market_lines.count_documents({"bookmaker_key": "pinnacle"})
    odds_pinnacle_recent = await db.market_lines.count_documents({
        "bookmaker_key": "pinnacle",
        "updated_at": {"$gte": cutoff_48h}
    })
    
    # Match odds to events
    all_event_ids = [e['event_id'] async for e in db.upcoming_events.find({"status": "pending"}, {"event_id": 1})]
    odds_matched = await db.market_lines.count_documents({"event_id": {"$in": all_event_ids}})
    odds_pinnacle_matched = await db.market_lines.count_documents({
        "event_id": {"$in": all_event_ids},
        "bookmaker_key": "pinnacle"
    })
    
    # Sample unmatched odds
    unmatched_sample = await db.market_lines.find(
        {"event_id": {"$nin": all_event_ids}},
        {"_id": 0, "bookmaker_key": 1, "home_team": 1, "away_team": 1, "spread_point_home": 1, "price_home_decimal": 1, "event_id": 1}
    ).limit(5).to_list(5)
    
    odds_section = {
        "odds_rows_total": odds_total,
        "odds_rows_recent_48h": odds_recent,
        "odds_rows_pinnacle_total": odds_pinnacle,
        "odds_rows_pinnacle_recent": odds_pinnacle_recent,
        "odds_rows_matched_to_events": odds_matched,
        "odds_rows_pinnacle_matched": odds_pinnacle_matched,
        "odds_rows_unmatched_sample": unmatched_sample
    }
    result["sections"]["B_odds"] = odds_section
    
    # ============= C) FEATURES =============
    features_section = {}
    
    events_with_features = 0
    events_missing_features = []
    
    # Check a sample of pending events for feature availability
    sample_events = await db.upcoming_events.find(
        {"status": "pending"},
        {"_id": 0}
    ).sort("commence_time", 1).limit(20).to_list(20)
    
    for event in sample_events:
        try:
            matchup_data = await calculate_matchup_features(event['home_team'], event['away_team'])
            if matchup_data and matchup_data.get('features'):
                events_with_features += 1
            else:
                events_missing_features.append({
                    "event_id": event['event_id'],
                    "home_team": event['home_team'],
                    "away_team": event['away_team'],
                    "reason": "MATCHUP_DATA_NULL"
                })
        except Exception as e:
            events_missing_features.append({
                "event_id": event['event_id'],
                "home_team": event['home_team'],
                "away_team": event['away_team'],
                "reason": f"FEATURE_ERROR: {str(e)[:50]}"
            })
    
    features_section = {
        "events_checked": len(sample_events),
        "events_with_features": events_with_features,
        "events_missing_features_count": len(events_missing_features),
        "events_missing_features_sample": events_missing_features[:5]
    }
    result["sections"]["C_features"] = features_section
    
    # ============= D) CALIBRATION =============
    calibration_section = {}
    
    calibration = await db.calibrations.find_one({"is_active": True}, {"_id": 0})
    model_data = await get_active_model()
    
    calibration_section = {
        "calibration_exists": calibration is not None,
        "calibration_id": calibration.get("calibration_id") if calibration else None,
        "calibration_is_auditable": calibration.get("is_auditable") if calibration else None,
        "beta_effective": calibration.get("beta") if calibration else None,
        "alpha_effective": calibration.get("alpha") if calibration else None,
        "sigma_residual": calibration.get("sigma_residual") if calibration else None,
        "model_exists": model_data is not None,
        "model_version": model_data.get("model_version") if model_data else None,
        "calibration_model_version": calibration.get("model_version") if calibration else None,
        "model_version_match": (
            model_data.get("model_version") == calibration.get("model_version")
            if model_data and calibration else False
        )
    }
    result["sections"]["D_calibration"] = calibration_section
    
    # ============= E) CANDIDATES (Pre-filter simulation) =============
    candidates_section = {}
    discard_reasons = {}
    discard_samples = []
    candidates_pre_filter = []
    
    def add_discard(reason, event_id=None, details=None):
        discard_reasons[reason] = discard_reasons.get(reason, 0) + 1
        if len(discard_samples) < 10 and event_id:
            discard_samples.append({"event_id": event_id, "reason": reason, "details": details})
    
    # Get pending events
    events = await db.upcoming_events.find({"status": "pending"}, {"_id": 0}).to_list(100)
    
    if not events:
        add_discard("NO_UPCOMING_EVENTS")
    
    if not calibration:
        add_discard("CALIBRATION_MISSING_OR_INACTIVE")
    
    if not model_data:
        add_discard("MODEL_NOT_AVAILABLE")
    
    # Process events if we have prerequisites
    if events and calibration and model_data:
        model = model_data['model']
        scaler = model_data['scaler']
        feature_cols = model_data['features']
        
        alpha = calibration.get('alpha')
        beta = calibration.get('beta')
        sigma_residual = calibration.get('sigma_residual')
        
        if alpha is None or beta is None or sigma_residual is None:
            add_discard("SIGMA_OR_BETA_NULL", details=f"alpha={alpha}, beta={beta}, sigma={sigma_residual}")
        
        for event in events:
            event_id = event['event_id']
            
            # Check odds
            lines = await db.market_lines.find({"event_id": event_id}, {"_id": 0}).to_list(20)
            if not lines:
                add_discard("ODDS_NOT_FOUND_FOR_EVENT", event_id, f"home={event['home_team']}")
                continue
            
            ref_line = select_reference_line(lines, require_pinnacle=True)
            if not ref_line:
                add_discard("BOOK_NOT_PINNACLE", event_id, f"books={[l.get('bookmaker_key') for l in lines]}")
                continue
            
            # Check spread/price
            spread = ref_line.get('spread_point_home')
            price_home = ref_line.get('price_home_decimal')
            price_away = ref_line.get('price_away_decimal')
            
            if spread is None or (price_home is None and price_away is None):
                add_discard("MISSING_SPREAD_OR_PRICE", event_id, f"spread={spread}, prices={price_home}/{price_away}")
                continue
            
            # Check features
            try:
                matchup_data = await calculate_matchup_features(event['home_team'], event['away_team'])
            except Exception as e:
                add_discard("FEATURES_MISSING", event_id, str(e)[:50])
                continue
            
            if not matchup_data:
                add_discard("FEATURES_MISSING", event_id, "matchup_data is None")
                continue
            
            # Check team mapping
            if not matchup_data.get('home_abbr') or not matchup_data.get('away_abbr'):
                add_discard("TEAM_MAPPING_MISMATCH", event_id, f"home={event['home_team']}, away={event['away_team']}")
                continue
            
            # Calculate prediction
            features = matchup_data['features']
            X = np.array([[features.get(col, 0) for col in feature_cols]])
            X_scaled = scaler.transform(X)
            pred_margin = float(model.predict(X_scaled)[0])
            
            market_spread = spread
            cover_threshold = -market_spread
            model_edge = pred_margin - cover_threshold
            
            home_covers = pred_margin > cover_threshold
            if home_covers:
                open_price = price_home or 1.91
            else:
                open_price = price_away or 1.91
            
            # Calculate p_cover and EV
            try:
                recommended_side = "HOME" if home_covers else "AWAY"
                p_cover, z = calculate_p_cover_vs_market(model_edge, alpha, beta, sigma_residual, recommended_side)
                ev = calculate_ev(p_cover, open_price)
                
                if np.isnan(ev) or np.isinf(ev):
                    add_discard("EV_NAN_OR_INF", event_id, f"p_cover={p_cover}, price={open_price}, ev={ev}")
                    continue
                    
            except Exception as e:
                add_discard("EV_NAN_OR_INF", event_id, str(e)[:50])
                continue
            
            confidence = matchup_data.get('confidence', 'low')
            
            # This is a valid candidate (pre-filter)
            candidate = {
                "event_id": event_id,
                "home_team": event['home_team'],
                "away_team": event['away_team'],
                "pred_margin": round(pred_margin, 2),
                "market_spread": market_spread,
                "price": round(open_price, 3),
                "p_cover": round(p_cover, 4),
                "ev": round(ev, 4),
                "ev_pct": f"{ev*100:.1f}%",
                "confidence": confidence,
                "book": ref_line.get('bookmaker_key')
            }
            candidates_pre_filter.append(candidate)
            
            # Now apply filters and track discards
            if confidence != 'high':
                add_discard("CONFIDENCE_NOT_HIGH", event_id, f"confidence={confidence}")
            
            if ev < -0.01:
                add_discard("EV_BELOW_THRESHOLD", event_id, f"ev={ev:.4f} < -0.01")
    
    # Calculate post-filter counts
    candidates_after_confidence = [c for c in candidates_pre_filter if c['confidence'] == 'high']
    candidates_after_ev = [c for c in candidates_after_confidence if c['ev'] >= -0.01]
    
    tier_a = [c for c in candidates_after_confidence if c['ev'] >= 0.05]
    tier_b = [c for c in candidates_after_confidence if 0.02 <= c['ev'] < 0.05]
    tier_c = [c for c in candidates_after_confidence if -0.01 <= c['ev'] <= 0.01]
    
    candidates_section = {
        "candidates_pre_filter_total": len(candidates_pre_filter),
        "candidates_pre_filter_sample": candidates_pre_filter[:5],
        "candidates_after_confidence": len(candidates_after_confidence),
        "candidates_after_ev_threshold": len(candidates_after_ev),
        "tier_a_total": len(tier_a),
        "tier_b_total": len(tier_b),
        "tier_c_total": len(tier_c),
        "tier_a_sample": tier_a[:3],
        "tier_b_sample": tier_b[:3],
        "tier_c_sample": tier_c[:3]
    }
    result["sections"]["E_candidates"] = candidates_section
    
    # ============= F) DISCARD REASONS =============
    result["discard_reasons"] = discard_reasons
    result["discard_samples"] = discard_samples
    
    # ============= G) DIAGNOSIS =============
    diagnosis = []
    
    if upcoming_future == 0:
        diagnosis.append("CRITICAL: No future events found. Run POST /api/admin/sync-upcoming")
    
    if odds_pinnacle_matched == 0:
        diagnosis.append("CRITICAL: No Pinnacle odds matched to events. Run POST /api/admin/sync-odds?book=pinnacle")
    
    if not calibration:
        diagnosis.append("CRITICAL: No active calibration. Run POST /api/admin/model/calibrate-vs-market")
    
    if candidates_pre_filter and len(candidates_after_confidence) == 0:
        diagnosis.append("WARNING: All candidates filtered by CONFIDENCE_NOT_HIGH")
    
    if len(candidates_after_confidence) > 0 and len(tier_a) + len(tier_b) + len(tier_c) == 0:
        diagnosis.append("WARNING: Candidates exist but none meet tier thresholds")
    
    if not diagnosis:
        diagnosis.append("OK: Pipeline appears healthy")
    
    result["diagnosis"] = diagnosis
    
    return result

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
async def generate_picks(user=Depends(get_current_user)):
    """
    Generate picks with VS_MARKET calibration - PAPER TRADING MODE v4.0
    
    Requirements:
    - MUST have an active, auditable calibration (no fallbacks)
    - All picks classified by tier (A, B, C) based on EV
    - Full traceability: every pick contains calibration_id and all parameters
    - Anti-blowout filter for favorites with high pred_margin
    - Conservative guardrails:
      * max_picks_per_day by day(Europe/Madrid) and market
      * min_abs_model_edge always active
      * CLV gate only when n_settled_50 >= 50 and clv_median_50 is available
      * DD gate only when n_picks_settled >= 100
    """
    import numpy as np

    if not is_operational_user(user):
        raise HTTPException(
            status_code=403,
            detail="USER_NOT_ALLOWED_FOR_OPERATIONAL_GENERATION: use the operational account only.",
        )
    
    model_data = await get_active_model()
    if not model_data:
        raise HTTPException(status_code=400, detail="No trained model available")
    
    model = model_data['model']
    scaler = model_data['scaler']
    feature_cols = model_data['features']
    model_version = model_data['model_version']
    model_id = model_data['model_id']
    
    # Get ACTIVE calibration from DB - REQUIRED, no fallback
    calibration = await db.calibrations.find_one({"is_active": True}, {"_id": 0})
    
    if not calibration:
        raise HTTPException(
            status_code=400, 
            detail="NO_ACTIVE_CALIBRATION: Run POST /api/admin/model/calibrate-vs-market first."
        )
    
    # PAPER TRADING v3.0: Require auditable calibration
    is_auditable = calibration.get('is_auditable', False)
    if not is_auditable:
        raise HTTPException(
            status_code=400,
            detail="CALIBRATION_NOT_AUDITABLE: The active calibration is not marked as auditable. Re-run calibration."
        )
    
    # Get trading settings for blowout filter (Paper Trading v4.0)
    trading_settings = await db.trading_settings.find_one({"_id": "default"})
    if not trading_settings:
        trading_settings = DEFAULT_TRADING_SETTINGS
    
    blowout_filter_enabled = trading_settings.get('blowout_filter_enabled', True)
    blowout_threshold = trading_settings.get('blowout_pred_margin_threshold', 12.0)
    enabled_tiers = trading_settings.get('enabled_tiers', ['A', 'B'])
    max_picks_per_day = int(trading_settings.get('max_picks_per_day', 3))
    min_abs_model_edge = float(trading_settings.get('min_abs_model_edge', 1.5))
    clv_gate_enabled = bool(trading_settings.get('clv_gate_enabled', True))
    dd_gate_enabled = bool(trading_settings.get('dd_gate_enabled', True))
    dd_gate_max_drawdown_threshold = float(trading_settings.get('dd_gate_max_drawdown_threshold', 0.25))
    use_outcome_calibration = bool(trading_settings.get('use_outcome_calibration', True))
    tier_a_min_p_cover_real = float(trading_settings.get('tier_a_min_p_cover_real', 0.54))
    tier_b_min_p_cover_real = float(trading_settings.get('tier_b_min_p_cover_real', 0.52))
    tier_c_min_p_cover_real = float(trading_settings.get('tier_c_min_p_cover_real', 0.50))
    
    # Extract ALL calibration parameters - no defaults allowed
    calibration_id = calibration.get('calibration_id')
    alpha = calibration.get('alpha')
    beta = calibration.get('beta')
    sigma_residual = calibration.get('sigma_residual')
    beta_source = calibration.get('beta_source', 'unknown')
    sigma_source = calibration.get('sigma_source', 'unknown')
    calibration_computed_at = calibration.get('computed_at')
    probability_mode = calibration.get('probability_mode', 'VS_MARKET')
    w_used = calibration.get('w_used') or calibration.get('w_shrinkage')
    k_used = calibration.get('k_used') or calibration.get('k_shrinkage')
    beta_reg = calibration.get('beta_reg')
    beta_prior = calibration.get('beta_prior')
    alpha_reg = calibration.get('alpha_reg')
    alpha_prior = calibration.get('alpha_prior')
    beta_effective = calibration.get('beta_effective', beta)
    alpha_effective = calibration.get('alpha_effective', alpha)
    
    if calibration_id is None or alpha is None or beta is None or sigma_residual is None:
        raise HTTPException(
            status_code=400,
            detail=f"INVALID_CALIBRATION: Missing required fields - calibration_id={calibration_id}, alpha={alpha}, beta={beta}, sigma_residual={sigma_residual}"
        )
    
    # Validate sigma_residual is not the forbidden 12.0 default
    if sigma_residual == 12.0:
        raise HTTPException(
            status_code=400,
            detail="LEGACY_SIGMA_DETECTED: sigma_residual=12.0 is forbidden. Re-run /api/admin/model/calibrate-vs-market."
        )

    # Load latest persistent performance snapshot (if available)
    perf = await db.performance_daily.find_one({"user_id": user["id"]}, {"_id": 0}, sort=[("as_of_date", -1)])
    clv_gate_active = False
    dd_gate_active = False
    clv_median_50 = None
    n_settled_50 = None
    n_picks_settled = None
    max_drawdown_total = None

    if perf:
        clv_median_50 = perf.get("clv_median_50")
        n_settled_50 = perf.get("n_settled_50")
        n_picks_settled = perf.get("n_picks_settled")
        max_drawdown_total = perf.get("max_drawdown_total")

        # CLV gate only if n_settled_50 >= 50 and CLV metric exists (non-null)
        if (
            clv_gate_enabled
            and n_settled_50 is not None
            and n_settled_50 >= 50
            and clv_median_50 is not None
            and clv_median_50 < 0
        ):
            clv_gate_active = True

        # DD gate only if n_picks_settled >= 100
        if (
            dd_gate_enabled
            and n_picks_settled is not None
            and n_picks_settled >= 100
            and max_drawdown_total is not None
            and max_drawdown_total > dd_gate_max_drawdown_threshold
        ):
            dd_gate_active = True

    effective_stake_mode = trading_settings.get('stake_mode', 'FLAT')
    if dd_gate_active and effective_stake_mode == "KELLY":
        effective_stake_mode = "FLAT"

    outcome_calibration = await get_active_outcome_calibration(db) if use_outcome_calibration else None
    using_outcome_calibration = outcome_calibration is not None
    warnings = []
    if use_outcome_calibration and not using_outcome_calibration:
        warnings.append("NO_ACTIVE_OUTCOME_CALIBRATION: using legacy tiering by EV")
    
    events = await db.upcoming_events.find({"status": "pending"}, {"_id": 0}).to_list(100)  # Increased limit

    # Existing picks count by Madrid day + market (book) for hard cap
    existing_picks = await db.predictions.find(
        {"user_id": user['id'], "archived": {"$ne": True}},
        {"_id": 0, "commence_time": 1, "book": 1},
    ).to_list(2000)
    day_market_count = {}
    for p in existing_picks:
        d_key = madrid_day_key(p.get("commence_time", ""))
        market_key = p.get("book")
        if not d_key or not market_key:
            continue
        key = (d_key, market_key)
        day_market_count[key] = day_market_count.get(key, 0) + 1
    
    picks = []
    tier_a_picks = []  # EV >= 5%
    tier_b_picks = []  # 2% <= EV < 5%
    tier_c_picks = []  # -1% <= EV <= 1% (control)
    blowout_filtered_picks = []  # Picks excluded by blowout filter
    blowout_filtered_count = 0
    global_duplicate_skipped_count = 0
    
    for event in events:
        lines = await db.market_lines.find({"event_id": event['event_id']}, {"_id": 0}).to_list(20)
        
        # PAPER TRADING: Require Pinnacle
        ref_line = select_reference_line(lines, require_pinnacle=True)
        has_pinnacle = ref_line is not None
        
        if not has_pinnacle:
            continue  # Skip non-Pinnacle for paper trading

        market_key = ref_line['bookmaker_key']
        event_day_madrid = madrid_day_key(event['commence_time'])
        if event_day_madrid:
            key = (event_day_madrid, market_key)
            if day_market_count.get(key, 0) >= max_picks_per_day:
                continue
        
        matchup_data = await calculate_matchup_features(event['home_team'], event['away_team'])
        if not matchup_data:
            continue
            
        # PAPER TRADING: Require HIGH confidence
        if matchup_data['confidence'] != 'high':
            continue
            
        features = matchup_data['features']
        home_abbr, away_abbr = matchup_data['home_abbr'], matchup_data['away_abbr']
        
        X = np.array([[features.get(col, 0) for col in feature_cols]])
        X_scaled = scaler.transform(X)
        pred_margin = float(model.predict(X_scaled)[0])
        
        market_spread = ref_line['spread_point_home']
        
        # Cover threshold calculation
        cover_threshold = -market_spread
        
        # model_edge = pred_margin - cover_threshold (raw edge vs market)
        model_edge = pred_margin - cover_threshold

        # Always-on guardrail: minimum absolute model edge
        if abs(model_edge) < min_abs_model_edge:
            continue
        
        home_covers = pred_margin > cover_threshold
        away_covers = pred_margin < cover_threshold
        
        if home_covers:
            recommended_side = "HOME"
            edge_points = model_edge
            open_price = ref_line['price_home_decimal']
        elif away_covers:
            recommended_side = "AWAY"
            edge_points = abs(model_edge)
            open_price = ref_line['price_away_decimal']
        else:
            recommended_side = "HOME"
            edge_points = 0.0
            open_price = ref_line['price_home_decimal']
        
        # Calculate probability and EV using VS_MARKET calibration
        p_cover, z = calculate_p_cover_vs_market(model_edge, alpha, beta, sigma_residual, recommended_side)
        implied_prob = 1.0 / open_price if open_price > 1.0 else 0.5
        ev = calculate_ev(p_cover, open_price)
        p_cover_real = None
        if using_outcome_calibration:
            p_cover_real = predict_p_cover_outcome(
                model_edge=model_edge,
                open_price=open_price,
                open_spread=market_spread,
                calibration_doc=outcome_calibration,
            )
        
        # Adjusted edge after shrinkage
        adjusted_edge = beta * model_edge + alpha
        
        # PAPER TRADING v4.0: Anti-blowout filter fields
        # is_favorite_pick: True if we're betting on the favorite (spread < 0 for home, spread > 0 for away)
        spread_abs = abs(market_spread)
        if recommended_side == "HOME":
            is_favorite_pick = market_spread < 0  # Home is favorite if spread is negative
        else:  # AWAY
            is_favorite_pick = market_spread > 0  # Away is favorite if spread is positive
        
        # Blowout filter: exclude favorites with high pred_margin predictions
        blowout_filter_hit = (
            blowout_filter_enabled and 
            is_favorite_pick and 
            abs(pred_margin) > blowout_threshold
        )
        
        # Tier classification
        # Conservative mode: use calibrated p_cover_real when available.
        # Fallback mode: legacy EV tiers.
        if p_cover_real is not None:
            if p_cover_real >= tier_a_min_p_cover_real:
                tier = "A"
            elif p_cover_real >= tier_b_min_p_cover_real:
                tier = "B"
            elif p_cover_real >= tier_c_min_p_cover_real:
                tier = "C"
            else:
                tier = None
        else:
            if ev >= 0.05:
                tier = "A"
            elif ev >= 0.02:
                tier = "B"
            elif -0.01 <= ev <= 0.01:
                tier = "C"
            else:
                tier = None

        # CLV gate (degrade to Tier C only) only when minimum data threshold is met
        if clv_gate_active and tier in ("A", "B"):
            tier = "C"
        
        # Signal based on EV (new system)
        signal_ev = calculate_signal_ev(ev)
        signal_edge = calculate_signal(edge_points)  # Keep for backward compat
        
        recommended_bet_string = generate_recommended_bet_string(
            event['home_team'], event['away_team'], home_abbr, away_abbr,
            market_spread, recommended_side
        )
        
        explanation = generate_explanation(
            event['home_team'], event['away_team'], home_abbr, away_abbr,
            pred_margin, market_spread, edge_points,
            recommended_side, matchup_data['confidence'], model_version
        )
        
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
            # Open line info
            "open_spread": market_spread,
            "open_price": round(open_price, 3),
            "open_ts": now_ts,
            "book": ref_line['bookmaker_key'],
            # Audit columns
            "cover_threshold": round(cover_threshold, 2),
            "model_edge": round(model_edge, 2),
            "adjusted_edge": round(adjusted_edge, 2),
            # VS_MARKET Calibration audit (REQUIRED for trazabilidad - Paper Trading v3.0)
            "calibration_id": calibration_id,
            "probability_mode": probability_mode,
            # Effective values (what's actually used)
            "beta_used": round(beta, 4),
            "alpha_used": round(alpha, 4),
            "sigma_used": round(sigma_residual, 2),
            # Shrinkage details for full audit
            "beta_effective": round(beta_effective, 4) if beta_effective else round(beta, 4),
            "alpha_effective": round(alpha_effective, 4) if alpha_effective else round(alpha, 4),
            "beta_reg": round(beta_reg, 4) if beta_reg else None,
            "beta_prior": beta_prior,
            "alpha_reg": round(alpha_reg, 4) if alpha_reg else None,
            "alpha_prior": alpha_prior,
            "w_used": round(w_used, 4) if w_used else None,
            "k_used": k_used,
            "beta_source": beta_source,
            "sigma_source": sigma_source,
            "calibration_computed_at": calibration_computed_at,
            "z": round(z, 4),
            # Probability and EV columns
            "implied_prob": round(implied_prob, 4),
            "p_cover": round(p_cover, 4),
            "p_cover_real": round(float(p_cover_real), 4) if p_cover_real is not None else None,
            "ev": round(ev, 4),
            # Tier classification (Paper Trading v3.0)
            "tier": tier,
            "signal_ev": signal_ev,
            "confidence": matchup_data['confidence'],
            "recommended_side": recommended_side,
            "recommended_bet_string": recommended_bet_string,
            "explanation": explanation,
            "model_id": model_id,
            "model_version": model_version,
            "created_at": now_ts,
            # Paper Trading v4.0: Anti-blowout fields
            "is_favorite_pick": is_favorite_pick,
            "spread_abs": round(spread_abs, 1),
            "blowout_filter_hit": blowout_filter_hit,
            # Close line info (to be filled later via snapshot)
            "close_spread": None,
            "close_price": None,
            "close_ts": None,
            "clv_spread": None,
            # Result info (to be filled later)
            "result": None,
            "final_home_score": None,
            "final_away_score": None,
            "margin_final": None,
            "covered": None,
            "profit_units": None,
            "settled_at": None
        }
        
        # Cross-user dedupe guardrail for pending picks:
        # prevent duplicate operational picks with same event/book/side/spread.
        existing_pending_same_pick = await db.predictions.find_one(
            {
                "event_id": event["event_id"],
                "book": ref_line["bookmaker_key"],
                "recommended_side": recommended_side,
                "open_spread": market_spread,
                "result": None,
                "archived": {"$ne": True},
            },
            {"_id": 0, "user_id": 1, "id": 1},
        )
        if existing_pending_same_pick and existing_pending_same_pick.get("user_id") != user["id"]:
            global_duplicate_skipped_count += 1
            continue

        await db.predictions.update_one(
            {"user_id": user['id'], "event_id": event['event_id']},
            {"$set": pick}, upsert=True
        )
        
        picks.append(pick)

        if event_day_madrid:
            key = (event_day_madrid, market_key)
            day_market_count[key] = day_market_count.get(key, 0) + 1
        
        # Paper Trading v4.0: Track blowout filtered picks
        if blowout_filter_hit:
            blowout_filtered_picks.append(pick)
            blowout_filtered_count += 1
            continue  # Don't add to tier lists if blowout filtered
        
        # Classify into tier lists (Paper Trading v3.0)
        if tier == "A":
            tier_a_picks.append(pick)
        elif tier == "B":
            tier_b_picks.append(pick)
        elif tier == "C":
            tier_c_picks.append(pick)
    
    # Sort each tier by EV (descending)
    tier_a_picks.sort(key=lambda p: p['ev'], reverse=True)
    tier_b_picks.sort(key=lambda p: p['ev'], reverse=True)
    tier_c_picks.sort(key=lambda p: abs(p['ev']))  # C closer to 0 EV first
    
    # Log stats
    logger.info(f"Generated {len(picks)} picks. Tier A: {len(tier_a_picks)}, Tier B: {len(tier_b_picks)}, Tier C: {len(tier_c_picks)}, Blowout filtered: {blowout_filtered_count}. Mode: {probability_mode}, beta={beta:.3f}, sigma={sigma_residual:.2f}")
    if global_duplicate_skipped_count > 0:
        warnings.append(f"GLOBAL_DUPLICATE_SKIPPED: {global_duplicate_skipped_count}")
    
    # PAPER TRADING v4.0 Response - All picks with tier classification and blowout info
    return {
        "status": "success",
        "paper_trading_mode": True,
        "paper_trading_version": "4.0",
        "calibration_id": calibration_id,
        "probability_mode": probability_mode,
        "calibration": {
            "calibration_id": calibration_id,
            "alpha_effective": alpha,
            "beta_effective": beta,
            "sigma_residual": sigma_residual,
            "w_used": w_used,
            "k_used": k_used,
            "beta_reg": beta_reg,
            "beta_prior": beta_prior,
            "alpha_reg": alpha_reg,
            "alpha_prior": alpha_prior,
            "beta_source": beta_source,
            "sigma_source": sigma_source,
            "computed_at": calibration_computed_at
        },
        "trading_settings": {
            "enabled_tiers": enabled_tiers,
            "blowout_filter_enabled": blowout_filter_enabled,
            "blowout_threshold": blowout_threshold,
            "max_picks_per_day": max_picks_per_day,
            "min_abs_model_edge": min_abs_model_edge,
            "stake_mode": effective_stake_mode,
            "use_outcome_calibration": use_outcome_calibration,
            "tier_a_min_p_cover_real": tier_a_min_p_cover_real,
            "tier_b_min_p_cover_real": tier_b_min_p_cover_real,
            "tier_c_min_p_cover_real": tier_c_min_p_cover_real
        },
        "tiering_mode": "P_COVER_REAL" if using_outcome_calibration else "EV_FALLBACK",
        "warnings": warnings,
        "guardrails": {
            "clv_gate_enabled": clv_gate_enabled,
            "clv_gate_active": clv_gate_active,
            "n_settled_50": n_settled_50,
            "clv_median_50": clv_median_50,
            "dd_gate_enabled": dd_gate_enabled,
            "dd_gate_active": dd_gate_active,
            "n_picks_settled": n_picks_settled,
            "max_drawdown_total": max_drawdown_total,
            "dd_gate_max_drawdown_threshold": dd_gate_max_drawdown_threshold
        },
        "tier_thresholds": (
            {
                "A": f"p_cover_real >= {tier_a_min_p_cover_real:.2f}",
                "B": f"{tier_b_min_p_cover_real:.2f} <= p_cover_real < {tier_a_min_p_cover_real:.2f}",
                "C": f"{tier_c_min_p_cover_real:.2f} <= p_cover_real < {tier_b_min_p_cover_real:.2f}",
            }
            if using_outcome_calibration
            else {
                "A": "EV >= 5%",
                "B": "2% <= EV < 5%",
                "C": "-1% <= EV <= +1%",
            }
        ),
        "summary": {
            "total_analyzed": len(events),
            "total_valid_picks": len(picks),
            "tier_a_count": len(tier_a_picks),
            "tier_b_count": len(tier_b_picks),
            "tier_c_count": len(tier_c_picks),
            "blowout_filtered_count": blowout_filtered_count,
            "global_duplicate_skipped_count": global_duplicate_skipped_count,
        },
        "tiers": {
            "A": tier_a_picks,
            "B": tier_b_picks,
            "C": tier_c_picks
        },
        "blowout_filtered": blowout_filtered_picks,
        "all_picks": picks,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

@api_router.get("/picks")
async def get_picks(user=Depends(get_current_user)):
    picks = await db.predictions.find({"user_id": user['id'], "archived": {"$ne": True}}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return {"picks": picks}

@api_router.get("/picks/operative")
async def get_operative_picks(user=Depends(get_current_user)):
    """Get only operative picks (ready to bet)"""
    picks = await db.predictions.find({
        "user_id": user['id'],
        "do_not_bet": False,
        "archived": {"$ne": True},
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
        "open_spread", "cover_threshold", "raw_edge_signed", "betting_edge",
        "open_price", "sigma", "z", "p_cover", "implied_prob", "ev",
        "close_spread", "close_price", "clv_spread",
        "signal", "signal_ev", "recommended_side", "recommended_bet_string",
        "actual_margin", "covered", "confidence", "model_version"
    ])
    writer.writeheader()
    
    for p in predictions:
        # Calculate audit columns if not present (for backward compatibility)
        spread = p.get('open_spread', 0)
        cover_threshold = -spread if spread else 0
        pred_margin = p.get('pred_margin', 0)
        raw_edge_signed = pred_margin - cover_threshold
        betting_edge = p.get('betting_edge') or p.get('edge_points', 0)
        sigma = p.get('sigma', 12.0)
        z = raw_edge_signed / sigma if sigma > 0 else 0
        
        writer.writerow({
            "created_at": p.get('created_at'), "home_team": p.get('home_team'),
            "away_team": p.get('away_team'), "commence_time": p.get('commence_time'),
            "pred_margin": p.get('pred_margin'), "open_spread": p.get('open_spread'),
            "cover_threshold": p.get('cover_threshold', cover_threshold),
            "raw_edge_signed": p.get('raw_edge_signed', round(raw_edge_signed, 2)),
            "betting_edge": betting_edge,
            "open_price": p.get('open_price'),
            "sigma": sigma,
            "z": round(z, 3),
            "p_cover": p.get('p_cover'),
            "implied_prob": p.get('implied_prob'),
            "ev": p.get('ev'),
            "close_spread": p.get('close_spread'),
            "close_price": p.get('close_price'), "clv_spread": p.get('clv_spread'),
            "signal": p.get('signal'),
            "signal_ev": p.get('signal_ev'),
            "recommended_side": p.get('recommended_side'),
            "recommended_bet_string": p.get('recommended_bet_string'),
            "actual_margin": p.get('actual_margin'), "covered": p.get('covered'),
            "confidence": p.get('confidence'), "model_version": p.get('model_version')
        })
    
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                            headers={"Content-Disposition": "attachment; filename=nba_edge_history.csv"})

# ============= MODEL AUDIT ROUTES =============

@api_router.get("/audit/model-sanity")
async def get_model_sanity_report(n: int = 200, user=Depends(get_current_user)):
    """
    Generate a comprehensive sanity report for model predictions.
    Uses VS_MARKET calibration (alpha, beta, sigma_residual) for p_cover and EV.
    """
    import numpy as np
    import joblib
    import io
    
    # Get model
    model_doc = await db.models.find_one({"is_active": True})
    if not model_doc:
        return {"error": "No active model", "flags": ["NO_MODEL"]}
    
    # Get upcoming events
    events = await db.upcoming_events.find({"status": "pending"}, {"_id": 0}).to_list(n)
    if not events:
        return {"error": "No upcoming events", "flags": ["NO_EVENTS"]}
    
    # Load model data (contains model, scaler, features)
    model_data = joblib.load(io.BytesIO(model_doc['model_binary']))
    model = model_data['model']
    scaler = model_data['scaler']
    feature_cols = model_data['features']
    
    # Get ACTIVE calibration from DB - REQUIRED, no fallback
    calibration = await db.calibrations.find_one({"is_active": True}, {"_id": 0})
    
    if not calibration:
        return {
            "error": "No active calibration found",
            "flags": ["NO_ACTIVE_CALIBRATION"],
            "action_required": "Run POST /api/admin/model/calibrate-vs-market first",
            "is_auditable": False
        }
    
    # Extract calibration parameters - no defaults allowed
    calibration_id = calibration.get('calibration_id')
    alpha = calibration.get('alpha')
    beta = calibration.get('beta')
    sigma_residual = calibration.get('sigma_residual')
    beta_source = calibration.get('beta_source', 'unknown')
    sigma_source = calibration.get('sigma_source', 'unknown')
    calibration_computed_at = calibration.get('computed_at')
    probability_mode = calibration.get('probability_mode', 'VS_MARKET')
    
    if calibration_id is None or alpha is None or beta is None or sigma_residual is None:
        return {
            "error": f"Invalid calibration: calibration_id={calibration_id}, alpha={alpha}, beta={beta}, sigma_residual={sigma_residual}",
            "flags": ["INVALID_CALIBRATION"],
            "is_auditable": False
        }
    
    # Validate sigma_residual is not the forbidden 12.0 default
    if sigma_residual == 12.0:
        return {
            "error": "sigma_residual=12.0 detected (legacy default). Re-run calibration.",
            "flags": ["LEGACY_SIGMA_DETECTED"],
            "is_auditable": False
        }
    
    # Analyze predictions
    analysis_data = []
    
    for event in events:
        lines = await db.market_lines.find({"event_id": event['event_id']}, {"_id": 0}).to_list(20)
        ref_line = select_reference_line(lines, require_pinnacle=True)
        if not ref_line:
            continue
        
        home_abbr = TEAM_NAME_TO_ABBR.get(event['home_team'], event['home_team'][:3].upper())
        away_abbr = TEAM_NAME_TO_ABBR.get(event['away_team'], event['away_team'][:3].upper())
        
        matchup_data = await calculate_matchup_features(event['home_team'], event['away_team'])
        if not matchup_data:
            continue
        
        features = matchup_data['features']
        X = np.array([[features.get(col, 0) for col in feature_cols]])
        X_scaled = scaler.transform(X)
        pred_margin = float(model.predict(X_scaled)[0])
        
        market_spread = ref_line['spread_point_home']
        cover_threshold = -market_spread
        
        # model_edge = pred_margin - cover_threshold (raw edge vs market)
        model_edge = pred_margin - cover_threshold
        betting_edge = abs(model_edge)
        
        # Determine side
        if pred_margin > cover_threshold:
            recommended_side = "HOME"
            open_price = ref_line['price_home_decimal']
        else:
            recommended_side = "AWAY"
            open_price = ref_line['price_away_decimal']
        
        # Calculate p_cover using VS_MARKET calibration
        # mu = beta * model_edge + alpha
        # z = mu / sigma_residual
        # p_cover = Phi(z) for HOME, Phi(-z) for AWAY
        p_cover, z = calculate_p_cover_vs_market(model_edge, alpha, beta, sigma_residual, recommended_side)
        
        implied_prob = 1.0 / open_price if open_price > 1.0 else 0.5
        ev = calculate_ev(p_cover, open_price)
        
        # Adjusted expected edge after shrinkage
        adjusted_edge = beta * model_edge + alpha
        
        # Feature contributions
        contributions = {}
        for i, col in enumerate(feature_cols):
            contributions[col] = round(float(model.coef_[i]) * X_scaled[0][i], 4)
        
        analysis_data.append({
            "home_team": event['home_team'],
            "away_team": event['away_team'],
            "pred_margin": round(pred_margin, 2),
            "market_spread": market_spread,
            "cover_threshold": round(cover_threshold, 2),
            "model_edge": round(model_edge, 2),
            "adjusted_edge": round(adjusted_edge, 2),
            "z": round(z, 4),
            "recommended_side": recommended_side,
            "open_price": round(open_price, 3),
            "implied_prob": round(implied_prob, 4),
            "p_cover": round(p_cover, 4),
            "ev": round(ev, 4),
            "features_raw": {k: round(v, 4) for k, v in features.items()},
            "feature_contributions": contributions
        })
    
    if not analysis_data:
        return {"error": "No data to analyze", "flags": ["NO_DATA"]}
    
    # Extract arrays for statistics
    pred_margins = [d['pred_margin'] for d in analysis_data]
    model_edges = [d['model_edge'] for d in analysis_data]
    adjusted_edges = [d['adjusted_edge'] for d in analysis_data]
    p_covers = [d['p_cover'] for d in analysis_data]
    evs = [d['ev'] for d in analysis_data]
    n_samples = len(pred_margins)
    
    # Calculate comprehensive statistics
    stats = {
        "n_samples": n_samples,
        "calibration_id": calibration_id,
        "probability_mode": probability_mode,
        "alpha_used": round(alpha, 4),
        "beta_used": round(beta, 4),
        "sigma_used": round(sigma_residual, 2),
        "beta_source": beta_source,
        "sigma_source": sigma_source,
        "calibration_computed_at": calibration_computed_at,
        "pred_margin": {
            "mean": round(np.mean(pred_margins), 2),
            "std": round(np.std(pred_margins), 2),
            "min": round(np.min(pred_margins), 2),
            "max": round(np.max(pred_margins), 2),
            "mean_abs": round(np.mean(np.abs(pred_margins)), 2),
        },
        "model_edge": {
            "mean": round(np.mean(model_edges), 2),
            "std": round(np.std(model_edges), 2),
            "min": round(np.min(model_edges), 2),
            "max": round(np.max(model_edges), 2),
        },
        "adjusted_edge": {
            "mean": round(np.mean(adjusted_edges), 2),
            "std": round(np.std(adjusted_edges), 2),
            "min": round(np.min(adjusted_edges), 2),
            "max": round(np.max(adjusted_edges), 2),
        },
        "p_cover": {
            "mean": round(np.mean(p_covers), 4),
            "std": round(np.std(p_covers), 4),
            "min": round(np.min(p_covers), 4),
            "max": round(np.max(p_covers), 4),
        },
        "ev": {
            "mean": round(np.mean(evs), 4),
            "std": round(np.std(evs), 4),
            "min": round(np.min(evs), 4),
            "max": round(np.max(evs), 4),
        },
        "distributions": {
            "pct_p_cover_gt_55": round(100 * sum(1 for p in p_covers if p > 0.55) / n_samples, 1),
            "pct_p_cover_gt_60": round(100 * sum(1 for p in p_covers if p > 0.60) / n_samples, 1),
            "pct_p_cover_gt_65": round(100 * sum(1 for p in p_covers if p > 0.65) / n_samples, 1),
            "pct_p_cover_gt_70": round(100 * sum(1 for p in p_covers if p > 0.70) / n_samples, 1),
            "pct_ev_positive": round(100 * sum(1 for e in evs if e > 0) / n_samples, 1),
            "pct_ev_gte_2pct": round(100 * sum(1 for e in evs if e >= 0.02) / n_samples, 1),
            "pct_ev_gte_5pct": round(100 * sum(1 for e in evs if e >= 0.05) / n_samples, 1),
            "pct_abs_model_edge_gt_5": round(100 * sum(1 for e in model_edges if abs(e) > 5) / n_samples, 1),
            "pct_abs_model_edge_gt_10": round(100 * sum(1 for e in model_edges if abs(e) > 10) / n_samples, 1),
        }
    }
    
    # Flags based on acceptance criteria
    flags = []
    
    # Check acceptance criteria
    mean_p_cover = stats["p_cover"]["mean"]
    mean_ev = stats["ev"]["mean"]
    pct_p_cover_gt_60 = stats["distributions"]["pct_p_cover_gt_60"]
    
    # Acceptance: mean(p_cover) ∈ [0.52, 0.56]
    if mean_p_cover < 0.52:
        flags.append(f"MEAN_P_COVER_TOO_LOW ({mean_p_cover:.4f} < 0.52)")
    elif mean_p_cover > 0.56:
        flags.append(f"MEAN_P_COVER_TOO_HIGH ({mean_p_cover:.4f} > 0.56)")
    else:
        flags.append(f"MEAN_P_COVER_OK ({mean_p_cover:.4f} in [0.52, 0.56])")
    
    # Acceptance: mean(EV) ∈ [-2%, +5%]
    if mean_ev < -0.02:
        flags.append(f"MEAN_EV_TOO_LOW ({mean_ev:.4f} < -0.02)")
    elif mean_ev > 0.05:
        flags.append(f"MEAN_EV_TOO_HIGH ({mean_ev:.4f} > 0.05)")
    else:
        flags.append(f"MEAN_EV_OK ({mean_ev:.4f} in [-0.02, 0.05])")
    
    # Acceptance: % picks with p_cover > 60% < 20%
    if pct_p_cover_gt_60 > 20:
        flags.append(f"TOO_MANY_HIGH_P_COVER ({pct_p_cover_gt_60:.1f}% > 20%)")
    else:
        flags.append(f"HIGH_P_COVER_OK ({pct_p_cover_gt_60:.1f}% <= 20%)")
    
    # Calibration warnings
    # Count passing criteria
    criteria_passed = sum(1 for f in flags if "_OK" in f)
    criteria_total = 3
    
    # Top 10 by EV
    sorted_by_ev = sorted(analysis_data, key=lambda x: x['ev'], reverse=True)
    top_10_by_ev = sorted_by_ev[:10]
    
    # Model info
    model_info = {
        "model_version": model_doc.get('model_version'),
        "probability_mode": probability_mode,
        "calibration": {
            "calibration_id": calibration_id,
            "alpha_used": round(alpha, 4),
            "beta_used": round(beta, 4),
            "sigma_used": round(sigma_residual, 2),
            "beta_source": beta_source,
            "sigma_source": sigma_source,
            "computed_at": calibration_computed_at
        },
        "intercept": round(float(model.intercept_), 4),
        "coefficients": {col: round(float(coef), 4) for col, coef in zip(feature_cols, model.coef_)},
        "mae": model_doc.get('metrics', {}).get('mae'),
        "rmse": model_doc.get('metrics', {}).get('rmse'),
    }
    
    return {
        "report_type": "MODEL_SANITY_AUDIT_V4",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "calibration_id": calibration_id,
        "probability_mode": probability_mode,
        "is_auditable": True,
        "model_info": model_info,
        "statistics": stats,
        "acceptance_criteria": {
            "mean_p_cover_range": "[0.52, 0.56]",
            "mean_ev_range": "[-0.02, 0.05]",
            "max_pct_p_cover_gt_60": "20%",
            "criteria_passed": f"{criteria_passed}/{criteria_total}"
        },
        "flags": flags,
        "flag_count": len(flags),
        "top_10_by_ev": top_10_by_ev,
        "all_analyzed_picks": analysis_data
    }

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


@api_router.post("/admin/performance/recompute")
async def recompute_performance(user=Depends(get_current_user)):
    snapshot = await recompute_performance_daily(db, user_id=user["id"])
    return {"status": "completed", "snapshot": snapshot}


@api_router.get("/admin/performance-summary")
async def performance_summary(days: int = 90, user=Depends(get_current_user)):
    return await get_performance_summary(db, days=days, user_id=user["id"])


@api_router.get("/admin/diagnostics/clv-coverage")
async def diagnostics_clv_coverage(last_n: int = 200, user=Depends(get_current_user)):
    last_n = max(1, min(int(last_n), 5000))
    picks = await db.predictions.find(
        {"user_id": user["id"], "archived": {"$ne": True}},
        {
            "_id": 0,
            "id": 1,
            "event_id": 1,
            "open_spread": 1,
            "close_spread": 1,
            "clv_spread": 1,
            "open_ts": 1,
            "close_captured_at": 1,
            "close_ts": 1,
            "book": 1,
            "created_at": 1,
        },
    ).sort("created_at", -1).to_list(last_n)

    n_checked = len(picks)
    n_with_close_spread = sum(1 for p in picks if p.get("close_spread") is not None)
    n_with_clv_spread = sum(1 for p in picks if p.get("clv_spread") is not None)
    pct_with_clv = (n_with_clv_spread / n_checked) if n_checked > 0 else 0.0

    missing_closing_line = []
    clv_not_computed_bug = []
    for p in picks:
        close_spread = p.get("close_spread")
        clv_spread = p.get("clv_spread")
        if close_spread is None:
            missing_closing_line.append(p)
        elif clv_spread is None:
            clv_not_computed_bug.append(p)

    examples = []
    for p in picks:
        issue = None
        if p.get("close_spread") is None:
            issue = "missing_closing_line"
        elif p.get("clv_spread") is None:
            issue = "clv_not_computed_bug"
        examples.append(
            {
                "id": p.get("id"),
                "event_id": p.get("event_id"),
                "open_spread": p.get("open_spread"),
                "close_spread": p.get("close_spread"),
                "clv_spread": p.get("clv_spread"),
                "open_ts": p.get("open_ts"),
                "close_captured_at": p.get("close_captured_at") or p.get("close_ts"),
                "book": p.get("book"),
                "issue": issue,
            }
        )
        if len(examples) >= 10:
            break

    return {
        "n_checked": n_checked,
        "n_with_close_spread": n_with_close_spread,
        "n_with_clv_spread": n_with_clv_spread,
        "pct_with_clv": pct_with_clv,
        "n_clv_not_computed_bug": len(clv_not_computed_bug),
        "n_missing_closing_line": len(missing_closing_line),
        "clv_not_computed_bug": {
            "count": len(clv_not_computed_bug),
            "sample_ids": [p.get("id") for p in clv_not_computed_bug[:10]],
        },
        "missing_closing_line": {
            "count": len(missing_closing_line),
            "sample_ids": [p.get("id") for p in missing_closing_line[:10]],
        },
        "ejemplos": examples,
    }


@api_router.get("/admin/report/selection-sweep")
async def selection_sweep_report(include_push_as_zero: bool = False, user=Depends(get_current_user)):
    result = await run_selection_sweep(
        db=db,
        out_path="backend/data/selection_sweep.json",
        include_push_as_zero=include_push_as_zero,
    )
    return {
        "status": result.get("status"),
        "generated_at": result.get("generated_at"),
        "dataset": result.get("dataset"),
        "n_configs_total": result.get("n_configs_total"),
        "n_configs_eligible_min_30": result.get("n_configs_eligible_min_30"),
        "baseline": result.get("baseline"),
        "top_20": result.get("top_20"),
    }


@api_router.get("/admin/report/walkforward-selection")
async def walkforward_selection_report(
    step_days: int = 7,
    train_min_samples: int = 50,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user=Depends(get_current_user),
):
    return await run_walkforward_selection(
        db=db,
        out_path="backend/data/walkforward_selection.json",
        train_min_samples=train_min_samples,
        step_days=step_days,
        start_date=start_date,
        end_date=end_date,
    )


@api_router.post("/admin/run-full-calibration")
async def run_full_calibration(user=Depends(get_current_user)):
    """
    Run full conservative calibration flow without touching Ridge/VS_MARKET:
    1) Outcome calibration (LogisticRegression)
    2) Performance recompute snapshot
    """
    calibration_result = await fit_outcome_calibration(
        db=db,
        include_push_as_half=False,
        min_samples=50,
    )
    active_outcome = await get_active_outcome_calibration(db)
    performance_latest = await recompute_performance_daily(db, user_id=user["id"])

    return {
        "calibration": {
            "n_samples": (active_outcome or {}).get("n_samples", calibration_result.get("n_samples")),
            "coefficients": (active_outcome or {}).get("coefficients", calibration_result.get("coefficients")),
            "intercept": (active_outcome or {}).get("intercept", calibration_result.get("intercept")),
            "data_cutoff": (active_outcome or {}).get("data_cutoff"),
        },
        "performance_latest": {
            "avg_p_cover_real_50": performance_latest.get("avg_p_cover_real_50"),
            "winrate_50": performance_latest.get("winrate_50"),
            "brier_score_50": performance_latest.get("brier_score_50"),
            "roi_total": performance_latest.get("roi_total"),
            "n_picks_total": performance_latest.get("n_picks_total"),
        },
    }


@api_router.post("/admin/run-daily-paper")
async def run_daily_paper(user=Depends(get_current_user)):
    """
    Daily paper trading runner (no model changes):
    1) generate picks
    2) auto-grade recent picks
    3) backfill close snapshot (2 days)
    4) recompute performance snapshot
    5) evaluate conservative gates from latest performance
    """
    generate_res = await generate_picks(user=user)
    auto_grade_res = await auto_grade_results(days_back=3, user=user)
    close_backfill_res = await backfill_close_snapshot(db=db, days=2, force=False)
    performance_latest = await recompute_performance_daily(db, user_id=user["id"])

    # Count picks generated "today" in Europe/Madrid.
    today_madrid = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Madrid")).date()
    picks_today = 0
    for p in generate_res.get("all_picks", []):
        created_at = p.get("created_at")
        if not created_at:
            continue
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt.astimezone(ZoneInfo("Europe/Madrid")).date() == today_madrid:
            picks_today += 1

    n_settled_50 = performance_latest.get("n_settled_50")
    clv_median_50 = performance_latest.get("clv_median_50")
    roi_50 = performance_latest.get("roi_50")

    warnings = []
    if n_settled_50 is not None and n_settled_50 >= 50:
        if clv_median_50 is not None and clv_median_50 < 0:
            warnings.append("STOP_STRATEGY")
        if (
            roi_50 is not None
            and roi_50 < -0.08
            and clv_median_50 is not None
            and clv_median_50 <= 0
        ):
            warnings.append("TIGHTEN_THRESHOLD")

    gates_status = {
        "n_settled_50": n_settled_50,
        "clv_median_50": clv_median_50,
        "roi_50": roi_50,
        "warnings": warnings,
    }

    return {
        "status": "completed",
        "picks_creados_hoy": picks_today,
        "performance_latest": performance_latest,
        "gates_status": gates_status,
        "steps": {
            "generate": {
                "status": generate_res.get("status"),
                "total_valid_picks": generate_res.get("summary", {}).get("total_valid_picks"),
            },
            "auto_grade": auto_grade_res.get("status"),
            "close_snapshot_backfill": close_backfill_res.get("status"),
            "performance_recompute": "completed",
        },
    }

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
