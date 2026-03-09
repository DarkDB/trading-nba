import io
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import joblib
import numpy as np

from backend.calibration_outcome import get_active_outcome_calibration, predict_p_cover_outcome

logger = logging.getLogger(__name__)


def _safe_iso_to_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


async def _ensure_indexes(db) -> None:
    await db.model_predictions_all.create_index(
        [
            ("event_id", 1),
            ("book", 1),
            ("market_type", 1),
            ("model_version", 1),
            ("open_ts", 1),
        ],
        unique=True,
        name="uq_event_book_market_model_open_ts",
    )
    await db.model_predictions_all.create_index([("commence_time_dt", 1)], name="idx_commence_time_dt")
    await db.model_predictions_all.create_index([("created_at", 1)], name="idx_created_at")
    await db.model_predictions_all.create_index([("result", 1), ("settled_at", 1)], name="idx_result_settled")


@asynccontextmanager
async def _server_db_context(db):
    # Reuse existing business helpers without rewriting model/feature logic.
    from backend import server as s

    original_db = s.db
    s.db = db
    try:
        yield s
    finally:
        s.db = original_db


async def _get_active_model_bundle(db) -> Optional[Dict[str, Any]]:
    model_doc = await db.models.find_one({"is_active": True})
    if not model_doc:
        return None
    model_data = joblib.load(io.BytesIO(model_doc["model_binary"]))
    model_data["model_id"] = model_doc["id"]
    model_data["model_version"] = model_doc.get("model_version", "unknown")
    return model_data


async def build_all_game_predictions(
    db,
    days: int = 2,
    book: str = "pinnacle",
    market_type: str = "spreads",
) -> Dict[str, Any]:
    await _ensure_indexes(db)

    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=max(1, int(days)))
    created_at = now

    model_data = await _get_active_model_bundle(db)
    if not model_data:
        return {"status": "error", "error": "NO_ACTIVE_MODEL", "run_id": run_id}

    calibration = await db.calibrations.find_one({"is_active": True}, {"_id": 0})
    if not calibration:
        return {"status": "error", "error": "NO_ACTIVE_CALIBRATION", "run_id": run_id}

    trading_settings = await db.trading_settings.find_one({"_id": "default"}, {"_id": 0}) or {}
    max_picks_per_day = int(trading_settings.get("max_picks_per_day", 3))
    min_abs_model_edge = float(trading_settings.get("min_abs_model_edge", 1.5))
    tier_a_min = float(trading_settings.get("tier_a_min_p_cover_real", 0.54))
    tier_b_min = float(trading_settings.get("tier_b_min_p_cover_real", 0.52))
    tier_c_min = float(trading_settings.get("tier_c_min_p_cover_real", 0.50))

    model = model_data["model"]
    scaler = model_data["scaler"]
    feature_cols = model_data["features"]
    model_id = model_data["model_id"]
    model_version = model_data["model_version"]

    alpha = float(calibration["alpha"])
    beta = float(calibration["beta"])
    sigma = float(calibration["sigma_residual"])
    calibration_id = calibration.get("calibration_id")

    outcome_calib = await get_active_outcome_calibration(db)
    outcome_calibration_id = (outcome_calib or {}).get("outcome_calibration_id")

    events = await db.upcoming_events.find(
        {"status": "pending", "commence_time": {"$gte": now.isoformat(), "$lte": end.isoformat()}},
        {"_id": 0},
    ).sort("commence_time", 1).to_list(1000)

    existing_bets = await db.model_predictions_all.find(
        {"would_bet": True, "book": book, "market_type": market_type},
        {"_id": 0, "commence_time": 1},
    ).to_list(10000)

    day_market_count: Dict[str, int] = {}
    async with _server_db_context(db) as s:
        for row in existing_bets:
            d = s.madrid_day_key(row.get("commence_time", ""))
            if not d:
                continue
            key = f"{d}|{book}"
            day_market_count[key] = day_market_count.get(key, 0) + 1

        total_events_scanned = len(events)
        with_market = 0
        inserted = 0
        updated = 0
        skipped = 0
        sample: List[Dict[str, Any]] = []

        for event in events:
            lines = await db.market_lines.find({"event_id": event["event_id"]}, {"_id": 0}).to_list(30)
            ref_line = s.select_reference_line(lines, require_pinnacle=True)
            if not ref_line:
                continue
            if ref_line.get("bookmaker_key") != book:
                continue
            with_market += 1

            matchup = await s.calculate_matchup_features(event["home_team"], event["away_team"])
            if not matchup:
                continue

            features = matchup["features"]
            X = np.array([[features.get(col, 0.0) for col in feature_cols]])
            X_scaled = scaler.transform(X)
            pred_margin = float(model.predict(X_scaled)[0])

            market_spread = _to_float(ref_line.get("spread_point_home"))
            if market_spread is None:
                continue
            cover_threshold = -market_spread
            model_edge = pred_margin - cover_threshold

            if pred_margin > cover_threshold:
                recommended_side = "HOME"
                open_price = _to_float(ref_line.get("price_home_decimal")) or 1.91
            elif pred_margin < cover_threshold:
                recommended_side = "AWAY"
                open_price = _to_float(ref_line.get("price_away_decimal")) or 1.91
            else:
                recommended_side = "HOME"
                open_price = _to_float(ref_line.get("price_home_decimal")) or 1.91

            p_cover, z = s.calculate_p_cover_vs_market(model_edge, alpha, beta, sigma, recommended_side)
            adjusted_edge = beta * model_edge + alpha
            p_cover_real = None
            if outcome_calib is not None:
                p_cover_real = predict_p_cover_outcome(
                    model_edge=model_edge,
                    open_price=open_price,
                    open_spread=market_spread,
                    calibration_doc=outcome_calib,
                )

            tier_if_bet = None
            if p_cover_real is not None:
                if p_cover_real >= tier_a_min:
                    tier_if_bet = "A"
                elif p_cover_real >= tier_b_min:
                    tier_if_bet = "B"
                elif p_cover_real >= tier_c_min:
                    tier_if_bet = "C"

            event_day = s.madrid_day_key(event.get("commence_time", ""))
            day_key = f"{event_day}|{book}" if event_day else None
            under_cap = (day_market_count.get(day_key, 0) < max_picks_per_day) if day_key else True
            threshold_prob = tier_c_min
            if p_cover_real is not None:
                prob_for_gate = p_cover_real
            else:
                prob_for_gate = p_cover
            would_bet = bool(abs(model_edge) >= min_abs_model_edge and prob_for_gate >= threshold_prob and under_cap)
            if would_bet and day_key:
                day_market_count[day_key] = day_market_count.get(day_key, 0) + 1

            commence_time = event.get("commence_time")
            commence_time_dt = _safe_iso_to_dt(commence_time)
            open_ts = _safe_iso_to_dt(ref_line.get("updated_at")) or commence_time_dt or now
            if not commence_time_dt:
                continue

            doc = {
                "event_id": event["event_id"],
                "book": book,
                "market_type": market_type,
                "home_team": event.get("home_team"),
                "away_team": event.get("away_team"),
                "home_abbr": matchup.get("home_abbr"),
                "away_abbr": matchup.get("away_abbr"),
                "commence_time": commence_time,
                "commence_time_dt": commence_time_dt,
                "open_spread": market_spread,
                "open_price": round(float(open_price), 3),
                "open_ts": open_ts,
                "pred_margin": round(pred_margin, 2),
                "model_edge": round(model_edge, 2),
                "adjusted_edge": round(adjusted_edge, 2),
                "recommended_side": recommended_side,
                "p_cover": round(float(p_cover), 4),
                "p_cover_real": round(float(p_cover_real), 4) if p_cover_real is not None else None,
                "tier_if_bet": tier_if_bet,
                "would_bet": would_bet,
                "model_version": model_version,
                "model_id": model_id,
                "calibration_id": calibration_id,
                "alpha_used": alpha,
                "beta_used": beta,
                "sigma_used": sigma,
                "z": z,
                "outcome_calibration_id": outcome_calibration_id,
                "created_at": created_at,
                "source_run_id": run_id,
                "close_spread": None,
                "close_price": None,
                "close_captured_at": None,
                "clv_spread": None,
                "close_source": None,
                "result": None,
                "final_home_score": None,
                "final_away_score": None,
                "margin_final": None,
                "covered": None,
                "profit_units": None,
                "settled_at": None,
            }

            unique_filter = {
                "event_id": event["event_id"],
                "book": book,
                "market_type": market_type,
                "model_version": model_version,
                "open_ts": open_ts,
            }

            existing = await db.model_predictions_all.find_one(unique_filter, {"_id": 0, "id": 1, "would_bet": 1, "tier_if_bet": 1, "p_cover_real": 1, "p_cover": 1, "pred_margin": 1, "model_edge": 1})
            if not existing:
                insert_doc = {**doc, "id": str(uuid.uuid4())}
                await db.model_predictions_all.insert_one(insert_doc)
                inserted += 1
                if len(sample) < 5:
                    sample.append({"event_id": event["event_id"], "action": "inserted", "would_bet": would_bet, "tier_if_bet": tier_if_bet})
                continue

            compare_keys = ("would_bet", "tier_if_bet", "p_cover_real", "p_cover", "pred_margin", "model_edge")
            changed = any(existing.get(k) != doc.get(k) for k in compare_keys)
            if not changed:
                skipped += 1
                continue
            await db.model_predictions_all.update_one(unique_filter, {"$set": doc})
            updated += 1
            if len(sample) < 5:
                sample.append({"event_id": event["event_id"], "action": "updated", "would_bet": would_bet, "tier_if_bet": tier_if_bet})

    logger.info(
        "RESEARCH_ALL_GAMES run_id=%s scanned=%s with_market=%s inserted=%s updated=%s skipped=%s",
        run_id,
        total_events_scanned,
        with_market,
        inserted,
        updated,
        skipped,
    )
    return {
        "status": "completed",
        "run_id": run_id,
        "total_events_scanned": total_events_scanned,
        "with_market": with_market,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "sample": sample,
    }
