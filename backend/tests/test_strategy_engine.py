from backend.strategy_engine import DEFAULT_STRATEGY_CONFIG, select_operational_picks


def _candidate(
    i,
    p_cover,
    edge,
    day="2026-03-17",
    confidence="high",
    blowout=False,
    side="HOME",
    favorite_or_dog="dog",
):
    return {
        "id": str(i),
        "event_id": f"evt-{i}",
        "commence_time": f"{day}T18:00:00+00:00",
        "p_cover": p_cover,
        "model_edge": edge,
        "passed_confidence": confidence == "high",
        "passed_blowout_filter": not blowout,
        "book": "pinnacle",
        "recommended_side": side,
        "favorite_or_dog": favorite_or_dog,
        "open_spread": -4.5,
    }


def test_strategy_engine_selects_only_in_base_range():
    candidates = [
        _candidate(1, 0.59, 9.0, side="HOME", favorite_or_dog="dog"),
        _candidate(2, 0.57, 5.5, side="HOME", favorite_or_dog="favorite"),
        _candidate(3, 0.57, 9.0, side="AWAY", favorite_or_dog="favorite"),
    ]
    result = select_operational_picks(candidates, DEFAULT_STRATEGY_CONFIG, {})
    assert result["post_filter_counts"]["A"] == 1
    assert result["post_filter_counts"]["B"] == 1
    assert result["selected_picks"][0]["id"] == "1"
    assert result["selected_picks"][1]["id"] == "2"


def test_strategy_engine_switches_to_conservative_on_bad_clv():
    candidates = [
        _candidate(1, 0.59, 9.0, side="HOME", favorite_or_dog="dog"),
        _candidate(2, 0.571, 5.2, side="HOME", favorite_or_dog="favorite"),
    ]
    metrics = {"n_clv_valid": 25, "market_beating_rate_valid": 0.40}
    result = select_operational_picks(candidates, DEFAULT_STRATEGY_CONFIG, metrics)
    assert result["strategy_mode"] == "conservative"
    assert "market_beating_rate_guard" in result["dynamic_guardrails_triggered"]
    assert result["legacy_tier_filter_enabled"] is False
    assert result["active_strategy_thresholds"]["max_picks_per_day"] == 1


def test_strategy_engine_reduces_picks_on_drawdown():
    candidates = [
        _candidate(1, 0.59, 9.0, side="HOME", favorite_or_dog="dog"),
        _candidate(2, 0.57, 5.2, side="HOME", favorite_or_dog="favorite"),
    ]
    metrics = {"max_drawdown_total_units": 6.0}
    result = select_operational_picks(candidates, DEFAULT_STRATEGY_CONFIG, metrics)
    assert result["active_strategy_thresholds"]["max_picks_per_day"] == 1
    assert result["post_filter_counts"]["A"] == 1


def test_strategy_engine_blocks_away_favorite_to_shadow_only():
    candidates = [_candidate(1, 0.61, 12.0, side="AWAY", favorite_or_dog="favorite")]
    result = select_operational_picks(candidates, DEFAULT_STRATEGY_CONFIG, {})
    assert result["post_filter_counts"]["A"] == 0
    assert result["all_picks"][0]["strategy_exclusion_reason"] == "profile_shadow_only"


def test_strategy_engine_blocks_away_dog_on_bad_profile_metrics():
    candidates = [_candidate(1, 0.61, 12.0, side="AWAY", favorite_or_dog="dog")]
    metrics = {
        "profile_metrics": {
            "AWAY_DOG": {
                "mean_clv_rolling": -0.4,
                "market_beating_rate_rolling": 0.2,
                "n_clv_valid": 25,
            }
        }
    }
    result = select_operational_picks(candidates, DEFAULT_STRATEGY_CONFIG, metrics)
    assert result["post_filter_counts"]["A"] == 0
    assert result["all_picks"][0]["strategy_exclusion_reason"] == "rolling_profile_guard"


def test_home_dog_strong_profile_is_not_blocked_by_legacy_tier_filter():
    candidates = [_candidate(1, 0.61, 12.0, side="HOME", favorite_or_dog="dog")]
    result = select_operational_picks(candidates, DEFAULT_STRATEGY_CONFIG, {"n_clv_valid": 25, "market_beating_rate_valid": 0.2})
    assert result["legacy_tier_filter_enabled"] is False
    assert result["post_filter_counts"]["A"] == 1
    assert result["all_picks"][0]["selection_profile"] == "HOME_DOG"
    assert result["all_picks"][0]["strategy_exclusion_reason"] is None
