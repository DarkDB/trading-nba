from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


DEFAULT_STRATEGY_CONFIG: Dict[str, Any] = {
    "_id": "adaptive_v1",
    "strategy_profile": "adaptive_v1",
    "enabled_tiers": ["A", "B"],
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
    "profile_rules": {
        "HOME_DOG": {
            "operational_tiers": ["A"],
            "min_p_cover": 0.58,
            "min_abs_model_edge": 8.0,
        },
        "HOME_FAVORITE": {
            "operational_tiers": ["A", "B"],
            "min_p_cover": 0.56,
            "min_abs_model_edge": 5.0,
        },
        "AWAY_DOG": {
            "operational_tiers": ["A"],
            "min_p_cover": 0.60,
            "min_abs_model_edge": 10.0,
            "min_market_beating_rate_rolling": 0.35,
            "min_mean_clv_rolling": -0.25,
        },
        "AWAY_FAVORITE": {
            "operational_tiers": [],
            "shadow_only": True,
        },
    },
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
    cfg["profile_rules"] = deepcopy(cfg.get("profile_rules") or DEFAULT_STRATEGY_CONFIG["profile_rules"])
    return cfg


def classify_pick_profile(pick: Dict[str, Any]) -> Optional[str]:
    side = str(pick.get("recommended_side") or "").upper()
    favorite_or_dog = str(pick.get("favorite_or_dog") or "").lower().strip()
    if favorite_or_dog not in {"favorite", "dog"}:
        is_favorite_pick = pick.get("is_favorite_pick")
        if is_favorite_pick is not None:
            favorite_or_dog = "favorite" if bool(is_favorite_pick) else "dog"
    if side == "HOME" and favorite_or_dog == "dog":
        return "HOME_DOG"
    if side == "HOME" and favorite_or_dog == "favorite":
        return "HOME_FAVORITE"
    if side == "AWAY" and favorite_or_dog == "dog":
        return "AWAY_DOG"
    if side == "AWAY" and favorite_or_dog == "favorite":
        return "AWAY_FAVORITE"
    return None


def derive_base_tier(p_cover: Optional[float], thresholds: Dict[str, float]) -> Optional[str]:
    if p_cover is None:
        return None
    if p_cover >= thresholds["tier_a"]:
        return "A"
    if p_cover >= thresholds["tier_b"]:
        return "B"
    if p_cover >= thresholds["tier_c"]:
        return "C"
    return None


def get_profile_performance_metrics(profile: str, performance_metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metrics = performance_metrics or {}
    profile_metrics = metrics.get("profile_metrics") or {}
    raw = profile_metrics.get(profile) or {}
    return {
        "mean_clv_rolling": _to_float(raw.get("mean_clv_rolling")),
        "market_beating_rate_rolling": _to_float(raw.get("market_beating_rate_rolling")),
        "n_clv_valid": int(raw.get("n_clv_valid") or 0),
    }


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
        active["max_picks_per_day"] = min(active["max_picks_per_day"], int(cfg["conservative_max_picks_per_day"]))

    return {
        "strategy_profile": cfg["strategy_profile"],
        "strategy_mode": "conservative" if conservative or "drawdown_guard" in triggers else "normal",
        "active_strategy_thresholds": active,
        "dynamic_guardrails_triggered": triggers,
        "legacy_tier_filter_enabled": False,
    }


def select_operational_picks(
    candidates: List[Dict[str, Any]],
    strategy_config: Dict[str, Any],
    performance_metrics: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    state = evaluate_strategy_state(strategy_config, performance_metrics)
    active = state["active_strategy_thresholds"]
    mode = state["strategy_mode"]
    cfg = normalize_strategy_config(strategy_config)
    profile_rules = cfg.get("profile_rules") or {}
    tier_thresholds = {"tier_a": 0.58, "tier_b": 0.56, "tier_c": 0.54}

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
        "profile_shadow_only": 0,
        "rolling_profile_guard": 0,
        "legacy_tier_filter": 0,
        "duplicate_skipped": 0,
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
        profile = classify_pick_profile(pick)
        base_tier = derive_base_tier(p_cover, tier_thresholds)
        if base_tier in pre_filter_counts:
            pre_filter_counts[base_tier] += 1
        else:
            pre_filter_counts["below_C"] += 1

        profile_rule = profile_rules.get(profile or "", {})
        p_cover_operational_floor = _to_float(profile_rule.get("min_p_cover"))
        edge_operational_floor = _to_float(profile_rule.get("min_abs_model_edge"))
        allowed_tiers = {str(t).upper() for t in (profile_rule.get("operational_tiers") or [])}
        profile_shadow_only = bool(profile_rule.get("shadow_only"))
        is_mid_pcover_high_edge = (
            p_cover is not None
            and 0.54 <= p_cover < 0.58
            and model_edge >= 8.0
        )
        in_p_cover_range = (
            p_cover is not None
            and p_cover_operational_floor is not None
            and p_cover >= p_cover_operational_floor
        )
        in_edge_range = (
            edge_operational_floor is not None
            and model_edge >= edge_operational_floor
        )
        passes_global_edge_floor = model_edge >= 3.0
        profile_metrics = get_profile_performance_metrics(profile or "", performance_metrics)

        tier_candidate = base_tier
        strategy_exclusion_reason = None
        strategy_exclusion_layer = None
        passed_strategy_profile = False
        if not passes_global_edge_floor:
            strategy_exclusion_reason = "below_strategy_model_edge"
            strategy_exclusion_layer = "profile_rule"
        elif is_mid_pcover_high_edge:
            strategy_exclusion_reason = "outside_p_cover_range"
            strategy_exclusion_layer = "profile_rule"
        elif profile_shadow_only:
            strategy_exclusion_reason = "profile_shadow_only"
            strategy_exclusion_layer = "profile_rule"
        elif base_tier is None:
            strategy_exclusion_reason = "outside_p_cover_range"
            strategy_exclusion_layer = "profile_rule"
        elif not allowed_tiers or base_tier not in allowed_tiers:
            strategy_exclusion_reason = "tier_disabled"
            strategy_exclusion_layer = "profile_rule"
        elif not in_p_cover_range:
            strategy_exclusion_reason = "outside_p_cover_range"
            strategy_exclusion_layer = "profile_rule"
        elif not in_edge_range:
            strategy_exclusion_reason = "below_strategy_model_edge"
            strategy_exclusion_layer = "profile_rule"
        elif profile == "AWAY_DOG":
            min_mbr = _to_float(profile_rule.get("min_market_beating_rate_rolling"))
            min_mean_clv = _to_float(profile_rule.get("min_mean_clv_rolling"))
            if (
                profile_metrics["market_beating_rate_rolling"] is not None
                and min_mbr is not None
                and profile_metrics["market_beating_rate_rolling"] < min_mbr
            ):
                strategy_exclusion_reason = "rolling_profile_guard"
                strategy_exclusion_layer = "rolling_profile_guard"
            elif (
                profile_metrics["mean_clv_rolling"] is not None
                and min_mean_clv is not None
                and profile_metrics["mean_clv_rolling"] < min_mean_clv
            ):
                strategy_exclusion_reason = "rolling_profile_guard"
                strategy_exclusion_layer = "rolling_profile_guard"
        elif not pick.get("passed_confidence", True):
            strategy_exclusion_reason = "confidence_not_high"
            strategy_exclusion_layer = "profile_rule"
        elif not pick.get("passed_blowout_filter", True):
            strategy_exclusion_reason = "blowout_filtered"
            strategy_exclusion_layer = "profile_rule"

        day_key = _madrid_day_key(pick.get("commence_time")) or "unknown"
        if strategy_exclusion_reason is None:
            passed_strategy_profile = True
            existing_day_count = int(pick.get("existing_day_count") or 0)
            current = day_counts.get(day_key, 0) + existing_day_count
            if current >= int(active["max_picks_per_day"]):
                strategy_exclusion_reason = "max_picks_per_day_trim"
                strategy_exclusion_layer = "max_picks_trim"

        if strategy_exclusion_reason is not None:
            drop_reasons_summary[strategy_exclusion_reason] = drop_reasons_summary.get(strategy_exclusion_reason, 0) + 1
            pick["final_selected"] = False
            pick["tier"] = None
        else:
            pick["final_selected"] = True
            pick["tier"] = base_tier
            selected.append(pick)
            post_filter_counts[base_tier] = post_filter_counts.get(base_tier, 0) + 1
            day_counts[day_key] = day_counts.get(day_key, 0) + 1

        pick["tier_candidate"] = base_tier
        pick["selection_profile"] = profile
        pick["profile_metrics_used"] = profile_metrics
        pick["passed_strategy_profile"] = passed_strategy_profile
        pick["strategy_exclusion_reason"] = strategy_exclusion_reason
        pick["strategy_exclusion_layer"] = strategy_exclusion_layer
        pick["strategy_mode_used"] = mode
        pick["final_decision"] = "dropped"
        pick["final_decision_reason"] = strategy_exclusion_reason or "pending_finalization"
        pick["final_decision_layer"] = strategy_exclusion_layer or ("profile_rule" if not pick.get("final_selected") else "strategy_engine")
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
