from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


DEFAULT_STRATEGY_CONFIG: Dict[str, Any] = {
    "_id": "adaptive_v1",
    "strategy_profile": "adaptive_v1",
    "enabled_tiers": ["A"],
    "min_p_cover": 0.56,
    "max_p_cover": 0.58,
    "min_abs_model_edge": 3.0,
    "max_picks_per_day": 2,
    "use_clv_filter": True,
    "min_clv_for_live_validation": 0.0,
    "use_roi_guard": True,
    "roi_guard_threshold": -0.05,
    "use_market_beating_rate_guard": True,
    "min_market_beating_rate": 0.45,
    "use_drawdown_guard": True,
    "max_drawdown_threshold": 5.0,
    "conservative_min_p_cover": 0.57,
    "conservative_min_abs_model_edge": 4.0,
    "conservative_max_picks_per_day": 1,
    "restore_market_beating_rate": 0.55,
    "restore_roi_threshold": 0.0,
    "updated_at": None,
}


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _to_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _madrid_day_key(value: Any) -> Optional[str]:
    dt = _to_dt(value)
    if not dt:
        return None
    return dt.astimezone(ZoneInfo("Europe/Madrid")).date().isoformat()


def normalize_strategy_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = deepcopy(DEFAULT_STRATEGY_CONFIG)
    if raw:
        cfg.update({k: v for k, v in raw.items() if v is not None})
    enabled = cfg.get("enabled_tiers") or ["A"]
    cfg["enabled_tiers"] = [t for t in ["A", "B", "C"] if t in {str(x).upper() for x in enabled}] or ["A"]
    cfg["strategy_profile"] = cfg.get("strategy_profile") or cfg.get("_id") or "adaptive_v1"
    return cfg


def _compute_drawdown_units(pnl_values: List[float]) -> Dict[str, float]:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    current_dd = 0.0
    for v in pnl_values:
        equity += v
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        current_dd = dd
    return {
        "equity_last": equity,
        "current_drawdown_units": current_dd,
        "max_drawdown_total_units": max_dd,
    }


def build_strategy_performance_metrics(settled_picks: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnl_values: List[float] = []
    clv_valid: List[float] = []
    for pick in settled_picks:
        pu = _to_float(pick.get("profit_units"))
        if pu is not None:
            pnl_values.append(pu)
        clv = _to_float(pick.get("clv_spread"))
        close_dt = _to_dt(pick.get("close_captured_at")) or _to_dt(pick.get("close_ts"))
        commence_dt = _to_dt(pick.get("commence_time"))
        invalid_timing = bool(close_dt and commence_dt and close_dt >= commence_dt)
        if clv is not None and not invalid_timing:
            clv_valid.append(clv)

    drawdown = _compute_drawdown_units(pnl_values)
    n20 = min(len(pnl_values), 20)
    n50 = min(len(pnl_values), 50)
    roi_rolling_20 = (sum(pnl_values[-20:]) / n20) if n20 else None
    roi_rolling_50 = (sum(pnl_values[-50:]) / n50) if n50 else None
    return {
        "roi_rolling_20": roi_rolling_20,
        "roi_rolling_50": roi_rolling_50,
        "n_clv_valid": len(clv_valid),
        "market_beating_rate_valid": (sum(1 for x in clv_valid if x > 0) / len(clv_valid)) if clv_valid else None,
        "mean_clv_valid": mean(clv_valid) if clv_valid else None,
        "median_clv_valid": sorted(clv_valid)[len(clv_valid) // 2] if clv_valid else None,
        "current_drawdown_units": drawdown["current_drawdown_units"],
        "max_drawdown_total_units": drawdown["max_drawdown_total_units"],
        "equity_last": drawdown["equity_last"],
    }


def evaluate_strategy_state(
    strategy_config: Dict[str, Any],
    performance_metrics: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    cfg = normalize_strategy_config(strategy_config)
    metrics = performance_metrics or {}
    active = {
        "min_p_cover": float(cfg["min_p_cover"]),
        "max_p_cover": float(cfg["max_p_cover"]),
        "min_abs_model_edge": float(cfg["min_abs_model_edge"]),
        "max_picks_per_day": int(cfg["max_picks_per_day"]),
        "enabled_tiers": list(cfg["enabled_tiers"]),
    }
    triggers: List[str] = []
    conservative = False

    roi_rolling_50 = _to_float(metrics.get("roi_rolling_50"))
    market_beating_rate_valid = _to_float(metrics.get("market_beating_rate_valid"))
    n_clv_valid = int(metrics.get("n_clv_valid") or 0)
    max_drawdown_total_units = _to_float(metrics.get("max_drawdown_total_units"))

    if (
        cfg.get("use_market_beating_rate_guard", True)
        and n_clv_valid >= 20
        and market_beating_rate_valid is not None
        and market_beating_rate_valid < float(cfg["min_market_beating_rate"])
    ):
        conservative = True
        triggers.append("market_beating_rate_guard")

    if (
        cfg.get("use_roi_guard", True)
        and roi_rolling_50 is not None
        and roi_rolling_50 < float(cfg["roi_guard_threshold"])
    ):
        conservative = True
        triggers.append("roi_guard")

    if (
        cfg.get("use_drawdown_guard", True)
        and max_drawdown_total_units is not None
        and max_drawdown_total_units > float(cfg["max_drawdown_threshold"])
    ):
        triggers.append("drawdown_guard")
        active["max_picks_per_day"] = min(active["max_picks_per_day"], 1)

    if conservative:
        active["min_p_cover"] = max(active["min_p_cover"], float(cfg["conservative_min_p_cover"]))
        active["min_abs_model_edge"] = max(active["min_abs_model_edge"], float(cfg["conservative_min_abs_model_edge"]))
        active["max_picks_per_day"] = min(active["max_picks_per_day"], int(cfg["conservative_max_picks_per_day"]))

    return {
        "strategy_profile": cfg["strategy_profile"],
        "strategy_mode": "conservative" if conservative or "drawdown_guard" in triggers else "normal",
        "active_strategy_thresholds": active,
        "dynamic_guardrails_triggered": triggers,
    }


def select_operational_picks(
    candidates: List[Dict[str, Any]],
    strategy_config: Dict[str, Any],
    performance_metrics: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    state = evaluate_strategy_state(strategy_config, performance_metrics)
    active = state["active_strategy_thresholds"]
    mode = state["strategy_mode"]

    pre_filter_counts = {"A": 0, "B": 0, "C": 0, "below_C": 0}
    post_filter_counts = {"A": 0, "B": 0, "C": 0}
    drop_reasons_summary = {
        "below_threshold": 0,
        "tier_disabled": 0,
        "blowout_filtered": 0,
        "max_picks_per_day_trim": 0,
        "confidence_not_high": 0,
        "missing_outcome_calibration": 0,
        "other": 0,
        "outside_p_cover_range": 0,
        "below_strategy_model_edge": 0,
    }

    selected: List[Dict[str, Any]] = []
    all_picks: List[Dict[str, Any]] = []
    day_counts: Dict[str, int] = {}

    sorted_candidates = sorted(
        candidates,
        key=lambda d: (_madrid_day_key(d.get("commence_time")) or "", -(float(d.get("p_cover") or 0.0)), -(abs(float(d.get("model_edge") or 0.0)))),
    )

    for pick in sorted_candidates:
        p_cover = _to_float(pick.get("p_cover"))
        model_edge = abs(_to_float(pick.get("model_edge")) or 0.0)
        in_p_cover_range = (
            p_cover is not None
            and p_cover >= active["min_p_cover"]
            and p_cover < active["max_p_cover"]
        )
        in_edge_range = model_edge >= active["min_abs_model_edge"]
        tier_candidate = "A" if in_p_cover_range and in_edge_range else None
        if tier_candidate:
            pre_filter_counts["A"] += 1
        else:
            pre_filter_counts["below_C"] += 1

        strategy_exclusion_reason = None
        passed_strategy_profile = tier_candidate == "A"
        if not in_p_cover_range:
            strategy_exclusion_reason = "outside_p_cover_range"
        elif not in_edge_range:
            strategy_exclusion_reason = "below_strategy_model_edge"
        elif not pick.get("passed_confidence", True):
            strategy_exclusion_reason = "confidence_not_high"
        elif not pick.get("passed_blowout_filter", True):
            strategy_exclusion_reason = "blowout_filtered"

        if strategy_exclusion_reason is None and "A" not in active["enabled_tiers"]:
            strategy_exclusion_reason = "tier_disabled"

        day_key = _madrid_day_key(pick.get("commence_time")) or "unknown"
        if strategy_exclusion_reason is None:
            existing_day_count = int(pick.get("existing_day_count") or 0)
            current = day_counts.get(day_key, 0) + existing_day_count
            if current >= int(active["max_picks_per_day"]):
                strategy_exclusion_reason = "max_picks_per_day_trim"

        if strategy_exclusion_reason is not None:
            drop_reasons_summary[strategy_exclusion_reason] = drop_reasons_summary.get(strategy_exclusion_reason, 0) + 1
            pick["final_selected"] = False
            pick["tier"] = None
        else:
            pick["final_selected"] = True
            pick["tier"] = "A"
            selected.append(pick)
            post_filter_counts["A"] += 1
            day_counts[day_key] = day_counts.get(day_key, 0) + 1

        pick["tier_candidate"] = tier_candidate
        pick["passed_strategy_profile"] = passed_strategy_profile
        pick["strategy_exclusion_reason"] = strategy_exclusion_reason
        pick["strategy_mode_used"] = mode
        all_picks.append(pick)

    return {
        **state,
        "selected_picks": selected,
        "all_picks": all_picks,
        "pre_filter_counts": pre_filter_counts,
        "post_filter_counts": post_filter_counts,
        "drop_reasons_summary": drop_reasons_summary,
        "tiers": {"A": [p for p in selected if p.get("tier") == "A"], "B": [], "C": []},
    }
