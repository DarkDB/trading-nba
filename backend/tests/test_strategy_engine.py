from backend.strategy_engine import DEFAULT_STRATEGY_CONFIG, select_operational_picks


def _candidate(i, p_cover, edge, day="2026-03-17", confidence="high", blowout=False):
    return {
        "id": str(i),
        "event_id": f"evt-{i}",
        "commence_time": f"{day}T18:00:00+00:00",
        "p_cover": p_cover,
        "model_edge": edge,
        "passed_confidence": confidence == "high",
        "passed_blowout_filter": not blowout,
        "book": "pinnacle",
        "recommended_side": "HOME",
        "open_spread": -4.5,
    }


def test_strategy_engine_selects_only_in_base_range():
    candidates = [
        _candidate(1, 0.565, 4.0),
        _candidate(2, 0.59, 4.0),
        _candidate(3, 0.565, 2.5),
    ]
    result = select_operational_picks(candidates, DEFAULT_STRATEGY_CONFIG, {})
    assert result["post_filter_counts"]["A"] == 1
    assert result["selected_picks"][0]["id"] == "1"


def test_strategy_engine_switches_to_conservative_on_bad_clv():
    candidates = [_candidate(1, 0.569, 3.5), _candidate(2, 0.571, 4.2)]
    metrics = {"n_clv_valid": 25, "market_beating_rate_valid": 0.40}
    result = select_operational_picks(candidates, DEFAULT_STRATEGY_CONFIG, metrics)
    assert result["strategy_mode"] == "conservative"
    assert "market_beating_rate_guard" in result["dynamic_guardrails_triggered"]
    assert result["active_strategy_thresholds"]["min_p_cover"] == 0.57


def test_strategy_engine_reduces_picks_on_drawdown():
    candidates = [_candidate(1, 0.571, 4.5), _candidate(2, 0.572, 5.0)]
    metrics = {"max_drawdown_total_units": 6.0}
    result = select_operational_picks(candidates, DEFAULT_STRATEGY_CONFIG, metrics)
    assert result["active_strategy_thresholds"]["max_picks_per_day"] == 1
    assert result["post_filter_counts"]["A"] == 1
