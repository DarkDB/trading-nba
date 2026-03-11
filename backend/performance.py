from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional

from backend.calibration_outcome import get_active_outcome_calibration, predict_p_cover_outcome


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _result_to_label(result: str, include_push_as_half: bool = False) -> Optional[float]:
    if result == "WIN":
        return 1.0
    if result == "LOSS":
        return 0.0
    if result == "PUSH" and include_push_as_half:
        return 0.5
    return None


def _rolling(values: List[float], n: int) -> Optional[float]:
    if not values:
        return None
    return mean(values[-n:]) if len(values) >= n else mean(values)


def _compute_drawdown(equity: List[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for x in equity:
        if x > peak:
            peak = x
        if peak > 0:
            dd = (peak - x) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


async def _ensure_performance_indexes(db) -> None:
    """
    Migrate legacy unique index on as_of_date to a scoped unique index.
    Legacy index caused collisions between different users on same day.
    """
    idx = await db.performance_daily.index_information()

    legacy_name = "uq_performance_daily_as_of_date"
    legacy = idx.get(legacy_name)
    if legacy and legacy.get("key") == [("as_of_date", 1)]:
        await db.performance_daily.drop_index(legacy_name)

    await db.performance_daily.create_index(
        [("as_of_date", 1), ("scope", 1), ("user_id", 1)],
        unique=True,
        name="uq_performance_daily_as_of_date_scope_user",
    )


async def recompute_performance_daily(db, user_id: Optional[str] = None) -> Dict[str, Any]:
    await _ensure_performance_indexes(db)
    now = datetime.now(timezone.utc)
    as_of_date = now.strftime("%Y-%m-%d")

    query: Dict[str, Any] = {}
    query["archived"] = {"$ne": True}
    if user_id:
        query["user_id"] = user_id

    picks = await db.predictions.find(query, {"_id": 0}).sort("created_at", 1).to_list(100000)
    settled = [p for p in picks if p.get("result") in ("WIN", "LOSS", "PUSH")]
    settled_sorted = sorted(settled, key=lambda p: p.get("settled_at") or "")
    outcome_calibration = await get_active_outcome_calibration(db)
    include_push_as_half = bool((outcome_calibration or {}).get("include_push_as_half", False))

    pnl = []
    clv = []
    p_cover_real = []
    labels = []
    equity = [0.0]

    for p in settled_sorted:
        pu = _to_float(p.get("profit_units"))
        if pu is not None:
            pnl.append(pu)
            equity.append(equity[-1] + pu)
        c = _to_float(p.get("clv_spread"))
        if c is not None:
            clv.append(c)
        pr = _to_float(p.get("p_cover_real"))
        if pr is None and outcome_calibration is not None:
            model_edge = _to_float(p.get("model_edge"))
            open_price = _to_float(p.get("open_price"))
            open_spread = _to_float(p.get("open_spread"))
            if model_edge is not None and open_price is not None and open_spread is not None:
                pr = predict_p_cover_outcome(
                    model_edge=model_edge,
                    open_price=open_price,
                    open_spread=open_spread,
                    calibration_doc=outcome_calibration,
                )
        lb = _result_to_label(p.get("result"), include_push_as_half=include_push_as_half)
        if pr is not None and lb is not None:
            p_cover_real.append(pr)
            labels.append(lb)

    n_settled = len(settled_sorted)
    n_settled_30 = min(30, n_settled)
    n_settled_50 = min(50, n_settled)

    pnl_total = sum(pnl) if pnl else 0.0
    pnl_30 = sum(pnl[-30:]) if pnl else 0.0
    pnl_50 = sum(pnl[-50:]) if pnl else 0.0

    roi_total = (pnl_total / n_settled) if n_settled > 0 else None
    roi_30 = (pnl_30 / n_settled_30) if n_settled_30 > 0 else None
    roi_50 = (pnl_50 / n_settled_50) if n_settled_50 > 0 else None

    wins = [p for p in settled_sorted if p.get("result") == "WIN"]
    losses = [p for p in settled_sorted if p.get("result") == "LOSS"]
    wl = len(wins) + len(losses)
    winrate_total = (len(wins) / wl) if wl > 0 else None

    last50 = settled_sorted[-50:]
    wins50 = sum(1 for p in last50 if p.get("result") == "WIN")
    losses50 = sum(1 for p in last50 if p.get("result") == "LOSS")
    winrate_50 = (wins50 / (wins50 + losses50)) if (wins50 + losses50) > 0 else None

    last30 = settled_sorted[-30:]
    wins30 = sum(1 for p in last30 if p.get("result") == "WIN")
    losses30 = sum(1 for p in last30 if p.get("result") == "LOSS")
    winrate_30 = (wins30 / (wins30 + losses30)) if (wins30 + losses30) > 0 else None

    brier_50 = None
    if p_cover_real and labels:
        pairs = list(zip(p_cover_real, labels))[-50:]
        if pairs:
            brier_50 = mean([(p - y) ** 2 for p, y in pairs])

    doc = {
        "as_of_date": as_of_date,
        "user_id": user_id,
        "scope": "user" if user_id else "global",
        "n_picks_total": len(picks),
        "n_picks_settled": n_settled,
        "n_settled_30": n_settled_30,
        "n_settled_50": n_settled_50,
        "clv_mean_30": _rolling(clv, 30),
        "clv_mean_50": _rolling(clv, 50),
        "clv_mean_total": mean(clv) if clv else None,
        "clv_median_50": sorted(clv[-50:])[len(clv[-50:]) // 2] if clv[-50:] else None,
        "roi_total": roi_total,
        "roi_30": roi_30,
        "roi_50": roi_50,
        "pnl_total": pnl_total,
        "pnl_30": pnl_30,
        "pnl_50": pnl_50,
        "max_drawdown_total": _compute_drawdown(equity),
        "equity_last": equity[-1],
        "last_pick_at": max([p.get("created_at") for p in picks if p.get("created_at")], default=None),
        "last_settle_at": max([p.get("settled_at") for p in settled_sorted if p.get("settled_at")], default=None),
        "avg_p_cover_real_30": _rolling(p_cover_real, 30),
        "avg_p_cover_real_50": _rolling(p_cover_real, 50),
        "winrate_total": winrate_total,
        "winrate_30": winrate_30,
        "winrate_50": winrate_50,
        "brier_score_50": brier_50,
        "updated_at": now.isoformat(),
    }

    key: Dict[str, Any] = {"as_of_date": as_of_date}
    if user_id:
        key["user_id"] = user_id
        key["scope"] = "user"
    else:
        key["scope"] = "global"
    await db.performance_daily.update_one(key, {"$set": doc}, upsert=True)
    return doc


async def get_performance_summary(db, days: int = 90, user_id: Optional[str] = None) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if user_id:
        query["user_id"] = user_id
    else:
        query["scope"] = "global"
    latest = await db.performance_daily.find_one(query, {"_id": 0}, sort=[("as_of_date", -1)])
    series = await db.performance_daily.find(query, {"_id": 0}).sort("as_of_date", -1).to_list(days)
    return {"latest": latest, "series": list(reversed(series))}
