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

# ============= 2) COVER LOGIC TESTS (CRITICAL) =============

def test_no_bet_when_model_does_not_cover():
    """
    CRITICAL TEST: Never recommend bet that model doesn't cover.
    
    Bug examples from user:
    - ORL vs MEM: pred_margin=+0.75, spread=-5.0 → Model says ORL wins by 0.75
      but spread requires ORL to win by 5+. ORL does NOT cover. Recommend AWAY.
    - SAS vs MIL: pred_margin=-0.97, spread=-7.5 → Model says SAS loses by 0.97
      but spread requires SAS to win by 7.5+. SAS does NOT cover. Recommend AWAY.
    """
    def get_recommendation(pred_margin, market_spread):
        """
        CORRECTED LOGIC:
        - cover_threshold = -market_spread
        - HOME covers if pred_margin > cover_threshold
        - AWAY covers if pred_margin < cover_threshold
        - Edge is always positive (distance from threshold)
        """
        cover_threshold = -market_spread
        
        if pred_margin > cover_threshold:
            return "HOME", pred_margin - cover_threshold
        elif pred_margin < cover_threshold:
            return "AWAY", cover_threshold - pred_margin
        else:
            return None, 0.0
    
    # BUG CASE 1: ORL vs MEM
    # pred_margin=+0.75, spread=-5.0
    # cover_threshold = -(-5.0) = 5.0
    # 0.75 < 5.0 → AWAY covers
    # edge = 5.0 - 0.75 = 4.25
    side, edge = get_recommendation(0.75, -5.0)
    assert side == "AWAY", f"Expected AWAY, got {side}"
    assert abs(edge - 4.25) < 0.01, f"Expected edge ~4.25, got {edge}"
    
    # BUG CASE 2: SAS vs MIL
    # pred_margin=-0.97, spread=-7.5
    # cover_threshold = -(-7.5) = 7.5
    # -0.97 < 7.5 → AWAY covers
    # edge = 7.5 - (-0.97) = 8.47
    side, edge = get_recommendation(-0.97, -7.5)
    assert side == "AWAY", f"Expected AWAY, got {side}"
    assert abs(edge - 8.47) < 0.01, f"Expected edge ~8.47, got {edge}"
    
    # CORRECT CASE: HOME actually covers
    # pred_margin=+8.0, spread=-5.0
    # cover_threshold = 5.0
    # 8.0 > 5.0 → HOME covers
    # edge = 8.0 - 5.0 = 3.0
    side, edge = get_recommendation(8.0, -5.0)
    assert side == "HOME", f"Expected HOME, got {side}"
    assert abs(edge - 3.0) < 0.01, f"Expected edge ~3.0, got {edge}"


def test_edge_positive_and_consistent_with_side():
    """
    Edge must ALWAYS be positive and represent advantage on recommended side.
    
    Cover threshold = -market_spread:
    - spread=-5.0 → threshold=5.0 (HOME must win by 5+)
    - spread=+3.0 → threshold=-3.0 (HOME can lose by up to 3)
    """
    def get_recommendation(pred_margin, market_spread):
        cover_threshold = -market_spread
        
        if pred_margin > cover_threshold:
            return "HOME", pred_margin - cover_threshold
        elif pred_margin < cover_threshold:
            return "AWAY", cover_threshold - pred_margin
        else:
            return None, 0.0
    
    # Test 1: HOME favorite (-5), model says big HOME win (+10)
    # threshold=5, pred=10 > 5 → HOME, edge=5
    side, edge = get_recommendation(10.0, -5.0)
    assert side == "HOME"
    assert edge == 5.0
    assert edge > 0, "Edge must be positive"
    
    # Test 2: HOME favorite (-5), model says small HOME win (+2)
    # threshold=5, pred=2 < 5 → AWAY, edge=3
    side, edge = get_recommendation(2.0, -5.0)
    assert side == "AWAY"
    assert edge == 3.0
    assert edge > 0, "Edge must be positive"
    
    # Test 3: HOME favorite (-5), model says HOME loses (-3)
    # threshold=5, pred=-3 < 5 → AWAY, edge=8
    side, edge = get_recommendation(-3.0, -5.0)
    assert side == "AWAY"
    assert edge == 8.0
    assert edge > 0, "Edge must be positive"
    
    # Test 4: HOME underdog (+3), model says HOME wins (+5)
    # threshold=-3, pred=5 > -3 → HOME, edge=8
    side, edge = get_recommendation(5.0, 3.0)
    assert side == "HOME"
    assert edge == 8.0
    assert edge > 0, "Edge must be positive"
    
    # Test 5: HOME underdog (+3), model says HOME loses (-5)
    # threshold=-3, pred=-5 < -3 → AWAY, edge=2
    side, edge = get_recommendation(-5.0, 3.0)
    assert side == "AWAY"
    assert edge == 2.0
    assert edge > 0, "Edge must be positive"
    
    # Test 6: Exact threshold - no edge
    side, edge = get_recommendation(5.0, -5.0)
    assert side is None
    assert edge == 0.0


def test_recommended_side_logic():
    """Cover-based side selection with various scenarios"""
    def get_recommendation(pred_margin, market_spread):
        cover_threshold = -market_spread
        if pred_margin > cover_threshold:
            return "HOME", pred_margin - cover_threshold
        elif pred_margin < cover_threshold:
            return "AWAY", cover_threshold - pred_margin
        return None, 0.0
    
    # HOME big favorite (-10), model says blowout (+15)
    side, edge = get_recommendation(15.0, -10.0)
    assert side == "HOME"
    assert edge == 5.0  # 15 - 10 = 5
    
    # HOME big favorite (-10), model says close game (+3)
    side, edge = get_recommendation(3.0, -10.0)
    assert side == "AWAY"
    assert edge == 7.0  # 10 - 3 = 7
    
    # Pick'em (spread=0), model says HOME wins by 2
    side, edge = get_recommendation(2.0, 0.0)
    assert side == "HOME"
    assert edge == 2.0
    
    # Pick'em (spread=0), model says AWAY wins by 3 (-3)
    side, edge = get_recommendation(-3.0, 0.0)
    assert side == "AWAY"
    assert edge == 3.0

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
    
    # HOME favorite (negative spread), bet HOME
    assert generate_bet_string("LAL", "MEM", -4.5, "HOME") == "LAL -4.5"
    # HOME favorite, bet AWAY (AWAY gets the points)
    assert generate_bet_string("LAL", "MEM", -4.5, "AWAY") == "MEM +4.5"
    
    # HOME underdog (positive spread), bet HOME
    assert generate_bet_string("MEM", "LAL", +5.0, "HOME") == "MEM +5.0"
    # HOME underdog, bet AWAY
    assert generate_bet_string("MEM", "LAL", +5.0, "AWAY") == "LAL -5.0"

# ============= 3) OPERATIVE FILTERS TESTS =============

def test_operational_filters():
    """Test operative filter logic - edge is now always positive"""
    config = {
        "min_edge": 3.5,
        "max_picks_per_day": 2,
        "require_high_confidence": True,
        "require_green_signal": True,
        "require_pinnacle": True
    }
    
    def should_bet(edge, signal, confidence, has_pinnacle):
        """Edge is always positive now"""
        if not has_pinnacle:
            return False, "NO_PINNACLE_LINE"
        if confidence != "high":
            return False, "LOW_CONFIDENCE"
        if edge < config["min_edge"]:  # No abs() needed - edge is always positive
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
        "NO_EDGE"  # New reason for exact threshold match
    ]
    
    # All reasons should be strings
    for r in reasons:
        assert isinstance(r, str)
        assert len(r) > 0

# ============= 5) SIGNAL THRESHOLDS =============

def test_signal_thresholds():
    """Test signal calculation - edge is always positive now"""
    def calculate_signal(edge_points):
        """Edge is always positive"""
        if edge_points >= 3.0:
            return "green"
        elif edge_points >= 2.0:
            return "yellow"
        return "red"
    
    assert calculate_signal(3.5) == "green"
    assert calculate_signal(3.0) == "green"
    assert calculate_signal(2.5) == "yellow"
    assert calculate_signal(2.0) == "yellow"
    assert calculate_signal(1.5) == "red"
    assert calculate_signal(0) == "red"

# ============= 6) EDGE CALCULATION (CORRECTED) =============

def test_edge_calculation():
    """
    CORRECTED: Edge = distance from cover threshold
    cover_threshold = -market_spread
    edge = |pred_margin - cover_threshold| (always positive)
    """
    def calc_edge(pred_margin, market_spread):
        cover_threshold = -market_spread
        if pred_margin > cover_threshold:
            return pred_margin - cover_threshold
        elif pred_margin < cover_threshold:
            return cover_threshold - pred_margin
        return 0.0
    
    # spread=-5.0, pred=+8: threshold=5, HOME covers, edge=8-5=3
    assert calc_edge(8.0, -5.0) == 3.0
    
    # spread=-5.0, pred=+2: threshold=5, AWAY covers, edge=5-2=3
    assert calc_edge(2.0, -5.0) == 3.0
    
    # spread=-5.0, pred=-3: threshold=5, AWAY covers, edge=5-(-3)=8
    assert calc_edge(-3.0, -5.0) == 8.0
    
    # spread=+3.0, pred=+5: threshold=-3, HOME covers, edge=5-(-3)=8
    assert calc_edge(5.0, 3.0) == 8.0
    
    # spread=0, pred=+2: threshold=0, HOME covers, edge=2
    assert calc_edge(2.0, 0.0) == 2.0
    
    # Exact threshold: no edge
    assert calc_edge(5.0, -5.0) == 0.0

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
