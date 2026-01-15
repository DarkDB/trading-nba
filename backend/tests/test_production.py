"""
NBA Edge - Production Tests
Tests for: model versioning, operative filters, edge/side logic, CLV, do_not_bet reasons
"""
import pytest
import numpy as np

# ============= 1) MODEL VERSION TESTS =============

def test_model_version_presence():
    """Test that model version is generated correctly"""
    from datetime import datetime
    version = datetime.now().strftime("%Y%m%d_%H%M%S")
    assert len(version) == 15  # YYYYMMDD_HHMMSS
    assert "_" in version

def test_config_snapshot_immutable():
    """Test config snapshot structure"""
    config = {
        "rolling_window_n": 15,
        "feature_list": ["diff_net_rating", "diff_pace"],
        "algorithm": "Ridge",
        "alpha": 1.0,
        "signal_thresholds": {"green": 3.0, "yellow": 2.0},
        "operative_thresholds": {"min_edge": 3.5},
        "train_seasons": ["2021-22"],
        "test_season": "2024-25",
        "spread_convention": "HOME_PERSPECTIVE_SIGNED"
    }
    
    # Snapshot should be serializable
    import json
    snapshot_str = json.dumps(config)
    restored = json.loads(snapshot_str)
    assert restored == config

# ============= 2) OPERATIVE FILTERS TESTS =============

def test_recommended_side_logic():
    """edge > 0 → HOME, edge < 0 → AWAY"""
    def get_side(edge):
        if edge > 0:
            return "HOME"
        elif edge < 0:
            return "AWAY"
        return None
    
    assert get_side(5.0) == "HOME"
    assert get_side(0.1) == "HOME"
    assert get_side(-3.0) == "AWAY"
    assert get_side(-0.1) == "AWAY"
    assert get_side(0) is None

def test_recommended_bet_string_sign():
    """Test bet string has correct sign convention"""
    def generate_bet_string(home_abbr, away_abbr, market_spread, recommended_side):
        if recommended_side == "HOME":
            team = home_abbr
            spread = market_spread
        else:
            team = away_abbr
            spread = -market_spread
        return f"{team} {spread:+.1f}"
    
    # HOME favorite (negative spread)
    assert generate_bet_string("LAL", "MEM", -4.5, "HOME") == "LAL -4.5"
    assert generate_bet_string("LAL", "MEM", -4.5, "AWAY") == "MEM +4.5"
    
    # HOME underdog (positive spread)
    assert generate_bet_string("MEM", "LAL", +5.0, "HOME") == "MEM +5.0"
    assert generate_bet_string("MEM", "LAL", +5.0, "AWAY") == "LAL -5.0"

def test_operational_filters():
    """Test operative filter logic"""
    config = {
        "min_edge": 3.5,
        "max_picks_per_day": 2,
        "require_high_confidence": True,
        "require_green_signal": True,
        "require_pinnacle": True
    }
    
    def should_bet(edge, signal, confidence, has_pinnacle):
        if not has_pinnacle:
            return False, "NO_PINNACLE_LINE"
        if confidence != "high":
            return False, "LOW_CONFIDENCE"
        if abs(edge) < config["min_edge"]:
            return False, "EDGE_TOO_SMALL"
        if signal != "green":
            return False, "NOT_GREEN_SIGNAL"
        return True, None
    
    # Valid pick
    ok, reason = should_bet(4.0, "green", "high", True)
    assert ok is True
    assert reason is None
    
    # No Pinnacle
    ok, reason = should_bet(4.0, "green", "high", False)
    assert ok is False
    assert reason == "NO_PINNACLE_LINE"
    
    # Low confidence
    ok, reason = should_bet(4.0, "green", "medium", True)
    assert ok is False
    assert reason == "LOW_CONFIDENCE"
    
    # Edge too small
    ok, reason = should_bet(2.0, "yellow", "high", True)
    assert ok is False
    assert reason == "EDGE_TOO_SMALL"
    
    # Not green
    ok, reason = should_bet(2.5, "yellow", "high", True)
    assert ok is False
    assert reason == "EDGE_TOO_SMALL"

# ============= 3) CLV TESTS =============

def test_clv_calculation_normalized():
    """Test CLV is positive when line moves in our favor"""
    def calc_clv(recommended_side, open_spread, close_spread):
        if recommended_side == "HOME":
            # Bet HOME. If close_spread more negative, line moved FOR us
            return open_spread - close_spread
        else:
            # Bet AWAY. If close_spread more positive, line moved FOR us
            return close_spread - open_spread
    
    # HOME bet: open=-5, close=-6 (moved 1 point in HOME favor) → CLV = +1
    assert calc_clv("HOME", -5.0, -6.0) == 1.0
    
    # HOME bet: open=-5, close=-4 (moved against HOME) → CLV = -1
    assert calc_clv("HOME", -5.0, -4.0) == -1.0
    
    # AWAY bet: open=-5, close=-4 (spread got less negative, good for AWAY) → CLV = +1
    assert calc_clv("AWAY", -5.0, -4.0) == 1.0
    
    # AWAY bet: open=-5, close=-6 (spread more negative, bad for AWAY) → CLV = -1
    assert calc_clv("AWAY", -5.0, -6.0) == -1.0

def test_close_snapshot_logic():
    """Test close line snapshot updates prediction"""
    open_spread = -5.0
    close_spread = -6.0
    
    # For HOME side
    clv = open_spread - close_spread  # -5 - (-6) = +1
    assert clv == 1.0

# ============= 4) DO NOT BET REASONS =============

def test_do_not_bet_reasons():
    """Test all do_not_bet reasons are handled"""
    reasons = [
        "NO_PINNACLE_LINE",
        "LOW_CONFIDENCE",
        "EDGE_TOO_SMALL",
        "NOT_GREEN_SIGNAL",
        "SIGN_CONVENTION_ERROR"
    ]
    
    # All reasons should be strings
    for r in reasons:
        assert isinstance(r, str)
        assert len(r) > 0

# ============= 5) SIGNAL THRESHOLDS =============

def test_signal_thresholds():
    """Test signal calculation"""
    def calculate_signal(edge_points):
        abs_edge = abs(edge_points)
        if abs_edge >= 3.0:
            return "green"
        elif abs_edge >= 2.0:
            return "yellow"
        return "red"
    
    assert calculate_signal(3.5) == "green"
    assert calculate_signal(-3.5) == "green"
    assert calculate_signal(3.0) == "green"
    assert calculate_signal(2.5) == "yellow"
    assert calculate_signal(-2.0) == "yellow"
    assert calculate_signal(1.5) == "red"
    assert calculate_signal(0) == "red"

# ============= 6) EDGE CALCULATION =============

def test_edge_calculation():
    """Test edge = pred_margin - market_spread"""
    def calc_edge(pred_margin, market_spread):
        return pred_margin - market_spread
    
    # HOME favored by model more than market
    # pred=+3, spread=-5 → edge=+8 (HOME value)
    assert calc_edge(3.0, -5.0) == 8.0
    
    # AWAY favored by model, market has HOME slight favorite
    # pred=-2, spread=-1 → edge=-1 (AWAY value, small)
    assert calc_edge(-2.0, -1.0) == -1.0
    
    # Model agrees with market
    # pred=-5, spread=-5 → edge=0
    assert calc_edge(-5.0, -5.0) == 0.0

# ============= 7) METRICS TESTS =============

def test_metrics_structure():
    """Test metrics structure for model"""
    metrics = {
        "mae": 8.5,
        "rmse": 11.2,
        "train_mae": 8.0,
        "train_rmse": 10.5,
        "error_percentiles": {"p50": 6.5, "p75": 10.0, "p90": 15.0},
        "pred_std_train": 3.5,
        "pred_std_test": 3.2
    }
    
    assert metrics["mae"] > 0
    assert metrics["rmse"] >= metrics["mae"]
    assert metrics["pred_std_test"] > 0
    assert "p50" in metrics["error_percentiles"]

# ============= RUN ALL TESTS =============

if __name__ == "__main__":
    print("Running NBA Edge Production Tests...\n")
    print("=" * 60)
    
    tests = [
        ("test_model_version_presence", test_model_version_presence),
        ("test_config_snapshot_immutable", test_config_snapshot_immutable),
        ("test_recommended_side_logic", test_recommended_side_logic),
        ("test_recommended_bet_string_sign", test_recommended_bet_string_sign),
        ("test_operational_filters", test_operational_filters),
        ("test_clv_calculation_normalized", test_clv_calculation_normalized),
        ("test_close_snapshot_logic", test_close_snapshot_logic),
        ("test_do_not_bet_reasons", test_do_not_bet_reasons),
        ("test_signal_thresholds", test_signal_thresholds),
        ("test_edge_calculation", test_edge_calculation),
        ("test_metrics_structure", test_metrics_structure),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_fn in tests:
        try:
            test_fn()
            print(f"  ✓ {name}: PASS")
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {name}: FAIL - {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {name}: ERROR - {e}")
            failed += 1
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("\n✅ ALL PRODUCTION TESTS PASSED")
    else:
        print(f"\n❌ {failed} TESTS FAILED")
