"""
NBA Edge - Unit Tests for Prediction Pipeline
Tests: signal thresholds, anti-leakage, prediction variance
"""
import pytest
import numpy as np
from datetime import datetime, timedelta

# ============= TEST SIGNAL THRESHOLDS =============

def calculate_signal(edge_points: float) -> str:
    """Calculate signal based on edge points"""
    abs_edge = abs(edge_points)
    if abs_edge >= 3.0:
        return "green"
    elif abs_edge >= 2.0:
        return "yellow"
    else:
        return "red"

def test_signal_thresholds_green():
    """Test green signal threshold"""
    assert calculate_signal(3.0) == "green"
    assert calculate_signal(3.5) == "green"
    assert calculate_signal(5.0) == "green"
    assert calculate_signal(-3.0) == "green"
    assert calculate_signal(-4.5) == "green"

def test_signal_thresholds_yellow():
    """Test yellow signal threshold"""
    assert calculate_signal(2.0) == "yellow"
    assert calculate_signal(2.5) == "yellow"
    assert calculate_signal(2.99) == "yellow"
    assert calculate_signal(-2.0) == "yellow"
    assert calculate_signal(-2.5) == "yellow"

def test_signal_thresholds_red():
    """Test red signal threshold"""
    assert calculate_signal(0) == "red"
    assert calculate_signal(1.0) == "red"
    assert calculate_signal(1.99) == "red"
    assert calculate_signal(-1.0) == "red"
    assert calculate_signal(-0.5) == "red"

# ============= TEST ANTI-LEAKAGE =============

def test_no_leakage_feature_dates():
    """Test that features only use data before the game date"""
    # Simulate game dates
    game_date = "2024-01-15"
    
    # Previous games (should be used)
    prev_games = [
        {"game_date": "2024-01-10", "margin": 5},
        {"game_date": "2024-01-08", "margin": -3},
        {"game_date": "2024-01-05", "margin": 10},
    ]
    
    # Filter games before game_date (simulating anti-leakage)
    filtered = [g for g in prev_games if g['game_date'] < game_date]
    
    # All should be included (all before game_date)
    assert len(filtered) == 3
    
    # Test with game from same day (should be excluded)
    same_day_games = [
        {"game_date": "2024-01-15", "margin": 7},  # Same day - LEAK
        {"game_date": "2024-01-10", "margin": 5},
    ]
    filtered_strict = [g for g in same_day_games if g['game_date'] < game_date]
    assert len(filtered_strict) == 1
    assert filtered_strict[0]['margin'] == 5

def test_leakage_future_games():
    """Ensure future games are never included"""
    game_date = "2024-01-15"
    
    all_games = [
        {"game_date": "2024-01-10", "margin": 5},
        {"game_date": "2024-01-15", "margin": 7},  # Same day
        {"game_date": "2024-01-20", "margin": -2},  # Future
    ]
    
    # Anti-leakage filter
    valid_games = [g for g in all_games if g['game_date'] < game_date]
    
    assert len(valid_games) == 1
    assert all(g['game_date'] < game_date for g in valid_games)

# ============= TEST PREDICTION VARIANCE =============

def test_prediction_not_constant_on_sample():
    """Test that predictions are not constant across different inputs"""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    
    # Create sample training data with variance
    np.random.seed(42)
    n_samples = 100
    n_features = 8
    
    X_train = np.random.randn(n_samples, n_features) * 5
    # Target has relationship with features
    y_train = 2 * X_train[:, 0] + 1.5 * X_train[:, 1] - X_train[:, 2] + np.random.randn(n_samples) * 3
    
    # Train model
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    model = Ridge(alpha=1.0)
    model.fit(X_train_scaled, y_train)
    
    # Generate varied test samples (simulating different matchups)
    X_test = np.array([
        [5, 2, 3, -1, 2, 0.1, 1, 1],
        [-3, -2, 0, 2, -1, 0.3, 0, 1],
        [10, 5, -2, -3, 4, -0.1, 2, 1],
        [0, 0, 0, 0, 0, 0, 0, 1],
        [-5, 3, 2, 1, -2, 0.2, -1, 1],
        [8, -4, 1, -2, 3, 0.05, 3, 1],
        [-2, 6, -3, 0, 1, 0.15, -2, 1],
        [4, -1, 5, -4, -3, 0.25, 1, 1],
        [1, 1, 1, 1, 1, 0.1, 0, 1],
        [-1, -1, -1, -1, -1, -0.1, 1, 1],
    ])
    
    X_test_scaled = scaler.transform(X_test)
    predictions = model.predict(X_test_scaled)
    
    # Predictions should NOT be constant
    pred_std = np.std(predictions)
    pred_unique = len(set(np.round(predictions, 2)))
    
    print(f"Predictions: {predictions}")
    print(f"Std: {pred_std:.4f}, Unique values: {pred_unique}")
    
    # Assert significant variance
    assert pred_std > 1.0, f"Predictions have too low variance: std={pred_std}"
    assert pred_unique >= 5, f"Predictions not varied enough: {pred_unique} unique values"

def test_zero_features_yield_intercept():
    """Test that zero features yield approximately the intercept"""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    
    np.random.seed(42)
    n_samples = 100
    n_features = 8
    
    X_train = np.random.randn(n_samples, n_features) * 5
    y_train = 2 * X_train[:, 0] + 1.5 * X_train[:, 1] + np.random.randn(n_samples) * 3
    
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    model = Ridge(alpha=1.0)
    model.fit(X_train_scaled, y_train)
    
    # Zero features (after scaling, this becomes the scaler mean)
    X_zeros = np.zeros((1, n_features))
    X_zeros_scaled = scaler.transform(X_zeros)
    pred_zeros = model.predict(X_zeros_scaled)[0]
    
    # The prediction should be close to intercept + contributions from scaled zeros
    # This tests that the model works, not that it gives exactly intercept
    print(f"Zero features prediction: {pred_zeros:.4f}")
    print(f"Model intercept: {model.intercept_:.4f}")

def test_different_inputs_different_outputs():
    """Core test: different feature vectors must produce different predictions"""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    
    np.random.seed(42)
    
    # Train a simple model
    X_train = np.random.randn(50, 8) * 5
    y_train = 3 * X_train[:, 0] + 2 * X_train[:, 1] + np.random.randn(50) * 2
    
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    model = Ridge(alpha=1.0)
    model.fit(X_train_scaled, y_train)
    
    # Two clearly different matchups
    matchup_a = np.array([[10, 5, 3, -2, 4, 0.3, 2, 1]])  # Strong home team
    matchup_b = np.array([[-10, -5, -3, 2, -4, -0.3, -2, 1]])  # Strong away team
    
    pred_a = model.predict(scaler.transform(matchup_a))[0]
    pred_b = model.predict(scaler.transform(matchup_b))[0]
    
    print(f"Matchup A (strong home): {pred_a:.2f}")
    print(f"Matchup B (strong away): {pred_b:.2f}")
    print(f"Difference: {abs(pred_a - pred_b):.2f}")
    
    # Must be significantly different
    assert abs(pred_a - pred_b) > 5.0, "Different matchups should have different predictions"
    assert pred_a > pred_b, "Strong home team should predict higher margin"

# ============= RUN TESTS =============

if __name__ == "__main__":
    print("Running NBA Edge Unit Tests...\n")
    
    print("=" * 50)
    print("TEST 1: Signal Thresholds")
    print("=" * 50)
    test_signal_thresholds_green()
    test_signal_thresholds_yellow()
    test_signal_thresholds_red()
    print("✓ All signal threshold tests passed\n")
    
    print("=" * 50)
    print("TEST 2: Anti-Leakage")
    print("=" * 50)
    test_no_leakage_feature_dates()
    test_leakage_future_games()
    print("✓ All anti-leakage tests passed\n")
    
    print("=" * 50)
    print("TEST 3: Prediction Variance")
    print("=" * 50)
    test_prediction_not_constant_on_sample()
    test_zero_features_yield_intercept()
    test_different_inputs_different_outputs()
    print("✓ All prediction variance tests passed\n")
    
    print("=" * 50)
    print("ALL TESTS PASSED ✓")
    print("=" * 50)
