import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def _safe_to_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _first_dt(*values: Any) -> Optional[datetime]:
    for value in values:
        dt = _safe_to_dt(value)
        if dt is not None:
            return dt
    return None


async def backfill_from_predictions(db, days_back: int = 30) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, int(days_back)))
    run_id = f"research_backfill_{now.strftime('%Y%m%d_%H%M%S')}"

    predictions = await db.predictions.find(
        {"archived": {"$ne": True}},
        {"_id": 0},
    ).to_list(200000)
    existing_research_event_ids = set(await db.model_predictions_all.distinct("event_id"))

    missing_candidates: List[Dict[str, Any]] = []
    for p in predictions:
        event_id = p.get("event_id")
        if not event_id or event_id in existing_research_event_ids:
            continue
        ref_dt = _first_dt(p.get("created_at"), p.get("open_ts"), p.get("commence_time"))
        if ref_dt is None or ref_dt < cutoff:
            continue
        missing_candidates.append(p)

    backfilled = 0
    skipped_existing_unique = 0
    not_recoverable = 0
    unrecoverable_examples: List[Dict[str, Any]] = []
    sample_backfilled: List[Dict[str, Any]] = []

    for p in missing_candidates:
        event_id = p.get("event_id")
        book = str(p.get("book") or "pinnacle").lower()
        market_type = str(p.get("market_type") or "spreads").lower()
        model_version = p.get("model_version") or "unknown"
        open_ts = _first_dt(p.get("open_ts"), p.get("created_at"), p.get("commence_time"))
        commence_time_dt = _first_dt(p.get("commence_time"), p.get("open_ts"), p.get("created_at"))

        if not event_id or open_ts is None:
            not_recoverable += 1
            if len(unrecoverable_examples) < 10:
                unrecoverable_examples.append(
                    {
                        "prediction_id": p.get("id"),
                        "event_id": event_id,
                        "reason": "missing_event_id_or_open_ts",
                    }
                )
            continue

        unique_filter = {
            "event_id": event_id,
            "book": book,
            "market_type": market_type,
            "model_version": model_version,
            "open_ts": open_ts,
        }
        existing = await db.model_predictions_all.find_one(unique_filter, {"_id": 0, "id": 1})
        if existing:
            skipped_existing_unique += 1
            continue

        doc = {
            "id": str(uuid.uuid4()),
            "event_id": event_id,
            "book": book,
            "market_type": market_type,
            "home_team": p.get("home_team"),
            "away_team": p.get("away_team"),
            "home_abbr": p.get("home_abbr"),
            "away_abbr": p.get("away_abbr"),
            "commence_time": p.get("commence_time"),
            "commence_time_dt": commence_time_dt,
            "open_spread": p.get("open_spread"),
            "open_price": p.get("open_price"),
            "open_ts": open_ts,
            "pred_margin": p.get("pred_margin"),
            "model_edge": p.get("model_edge"),
            "adjusted_edge": p.get("adjusted_edge"),
            "recommended_side": p.get("recommended_side"),
            "p_cover": p.get("p_cover"),
            "p_cover_real": p.get("p_cover_real"),
            "tier_if_bet": p.get("tier"),
            "would_bet": True,
            "model_version": model_version,
            "model_id": p.get("model_id"),
            "calibration_id": p.get("calibration_id"),
            "snapshot_source": p.get("snapshot_source") or "backfill_from_predictions",
            "alpha_used": p.get("alpha_used"),
            "beta_used": p.get("beta_used"),
            "sigma_used": p.get("sigma_used"),
            "outcome_calibration_id": p.get("outcome_calibration_id"),
            "close_spread": p.get("close_spread"),
            "close_price": p.get("close_price"),
            "close_captured_at": p.get("close_captured_at"),
            "close_source": p.get("close_source"),
            "clv_spread": p.get("clv_spread"),
            "result": p.get("result"),
            "final_home_score": p.get("final_home_score"),
            "final_away_score": p.get("final_away_score"),
            "margin_final": p.get("margin_final"),
            "covered": p.get("covered"),
            "profit_units": p.get("profit_units"),
            "settled_at": p.get("settled_at"),
            "created_at": now,
            "source_run_id": run_id,
            "backfilled_from_predictions": True,
            "source_prediction_id": p.get("id"),
        }

        await db.model_predictions_all.insert_one(doc)
        backfilled += 1
        if len(sample_backfilled) < 10:
            sample_backfilled.append(
                {
                    "prediction_id": p.get("id"),
                    "event_id": event_id,
                    "research_id": doc["id"],
                }
            )

    return {
        "status": "completed",
        "run_id": run_id,
        "days_back": days_back,
        "missing_before": len(missing_candidates),
        "backfilled": backfilled,
        "not_recoverable": not_recoverable,
        "skipped_existing_unique": skipped_existing_unique,
        "sample_backfilled": sample_backfilled,
        "sample_not_recoverable": unrecoverable_examples,
    }
