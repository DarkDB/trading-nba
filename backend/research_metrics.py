import math
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Dict, List, Optional


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


def _pnl_from_result(result: str, open_price: float) -> float:
    if result == "WIN":
        return float(open_price) - 1.0
    if result == "LOSS":
        return -1.0
    return 0.0


def _calibration_bins(rows: List[Dict[str, Any]], key: str, bins: int = 5) -> List[Dict[str, Any]]:
    values = []
    for r in rows:
        p = _to_float(r.get(key))
        if p is None:
            continue
        y = 1.0 if r.get("result") == "WIN" else 0.0 if r.get("result") == "LOSS" else None
        if y is None:
            continue
        values.append((p, y))

    if not values:
        return []

    out = []
    step = 1.0 / bins
    for i in range(bins):
        lo = i * step
        hi = (i + 1) * step
        bucket = [v for v in values if lo <= v[0] < hi or (i == bins - 1 and v[0] == 1.0)]
        if not bucket:
            out.append({"bin": i + 1, "range": [round(lo, 2), round(hi, 2)], "n": 0, "p_hat_mean": None, "winrate": None})
            continue
        p_mean = mean(v[0] for v in bucket)
        y_mean = mean(v[1] for v in bucket)
        out.append(
            {
                "bin": i + 1,
                "range": [round(lo, 2), round(hi, 2)],
                "n": len(bucket),
                "p_hat_mean": round(p_mean, 4),
                "winrate": round(y_mean, 4),
            }
        )
    return out


async def compute_research_metrics(db, days_back: int = 30, by: str = "day") -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(1, int(days_back)))
    docs = await db.model_predictions_all.find({"created_at": {"$gte": start}}, {"_id": 0}).to_list(100000)

    graded = [d for d in docs if d.get("result") in ("WIN", "LOSS", "PUSH")]
    with_margin = [d for d in graded if _to_float(d.get("margin_final")) is not None and _to_float(d.get("pred_margin")) is not None]

    abs_err = [abs(float(d["margin_final"]) - float(d["pred_margin"])) for d in with_margin]
    sq_err = [(float(d["margin_final"]) - float(d["pred_margin"])) ** 2 for d in with_margin]
    mae = mean(abs_err) if abs_err else None
    rmse = math.sqrt(mean(sq_err)) if sq_err else None

    brier_real_vals = []
    brier_legacy_vals = []
    for d in graded:
        y = 1.0 if d.get("result") == "WIN" else 0.0 if d.get("result") == "LOSS" else None
        if y is None:
            continue
        p_real = _to_float(d.get("p_cover_real"))
        p_legacy = _to_float(d.get("p_cover"))
        if p_real is not None:
            brier_real_vals.append((p_real - y) ** 2)
        if p_legacy is not None:
            brier_legacy_vals.append((p_legacy - y) ** 2)

    would_bet_docs = [d for d in graded if bool(d.get("would_bet"))]
    simulated_pnl = []
    for d in would_bet_docs:
        price = _to_float(d.get("open_price")) or 1.91
        simulated_pnl.append(_pnl_from_result(d.get("result"), price))
    roi_simulated = (sum(simulated_pnl) / len(simulated_pnl)) if simulated_pnl else None

    by_tier: Dict[str, Dict[str, Any]] = {}
    for tier in ("A", "B", "C", None):
        subset = [d for d in would_bet_docs if d.get("tier_if_bet") == tier]
        if not subset:
            continue
        pnl_vals = [_pnl_from_result(d.get("result"), _to_float(d.get("open_price")) or 1.91) for d in subset]
        key = tier if tier is not None else "null"
        by_tier[key] = {
            "n": len(subset),
            "roi": (sum(pnl_vals) / len(subset)) if subset else None,
            "pnl": sum(pnl_vals) if subset else 0.0,
        }

    clv_vals = [_to_float(d.get("clv_spread")) for d in docs if _to_float(d.get("clv_spread")) is not None]
    clv_vals = [x for x in clv_vals if x is not None]
    clv_sorted = sorted(clv_vals)
    clv_median = clv_sorted[len(clv_sorted) // 2] if clv_sorted else None

    report = {
        "status": "completed",
        "window_days": days_back,
        "group_by": by,
        "counts": {
            "n_docs": len(docs),
            "n_graded": len(graded),
            "n_would_bet": len(would_bet_docs),
            "n_with_clv": len(clv_vals),
        },
        "model_metrics": {
            "mae": mae,
            "rmse": rmse,
            "brier_p_cover_real": mean(brier_real_vals) if brier_real_vals else None,
            "brier_p_cover_legacy": mean(brier_legacy_vals) if brier_legacy_vals else None,
            "calibration_bins_p_cover_real": _calibration_bins(graded, "p_cover_real", bins=5),
        },
        "strategy_metrics": {
            "roi_simulated_would_bet": roi_simulated,
            "pnl_simulated_would_bet": sum(simulated_pnl) if simulated_pnl else 0.0,
            "by_tier_if_bet": by_tier,
        },
        "clv_metrics": {
            "mean": mean(clv_vals) if clv_vals else None,
            "median": clv_median,
            "min": min(clv_vals) if clv_vals else None,
            "max": max(clv_vals) if clv_vals else None,
        },
    }
    return report


async def research_coverage(db, days: int = 2) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=max(1, int(days)))
    upcoming = await db.upcoming_events.count_documents(
        {"status": "pending", "commence_time": {"$gte": now.isoformat(), "$lte": end.isoformat()}}
    )
    with_spread = await db.market_lines.count_documents({"bookmaker_key": "pinnacle"})
    n_docs = await db.model_predictions_all.count_documents({})
    n_graded = await db.model_predictions_all.count_documents({"result": {"$in": ["WIN", "LOSS", "PUSH"]}})
    n_with_close = await db.model_predictions_all.count_documents({"close_spread": {"$ne": None}})
    pct_graded = (n_graded / n_docs) if n_docs > 0 else 0.0
    return {
        "status": "completed",
        "upcoming_events": upcoming,
        "market_lines_with_spread": with_spread,
        "research_docs": n_docs,
        "graded_docs": n_graded,
        "pct_graded": pct_graded,
        "with_close_spread": n_with_close,
    }
