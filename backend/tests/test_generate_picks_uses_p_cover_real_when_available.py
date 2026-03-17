import asyncio

import numpy as np

from backend import server


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, _n):
        return list(self.docs)


class FakeCollection:
    def __init__(self, docs=None, one=None):
        self.docs = docs or []
        self.one = one
        self.updates = []

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.docs)

    async def find_one(self, *_args, **_kwargs):
        return self.one

    async def update_one(self, filt, update, upsert=False):
        self.updates.append((filt, update, upsert))


class FakeDB:
    def __init__(self):
        self.calibrations = FakeCollection(
            one={
                "is_active": True,
                "is_auditable": True,
                "calibration_id": "cal-vs-market-1",
                "alpha": 0.0,
                "beta": 1.0,
                "sigma_residual": 6.0,
                "beta_source": "unit_test",
                "sigma_source": "unit_test",
                "computed_at": "2026-03-05T00:00:00+00:00",
                "probability_mode": "VS_MARKET",
            }
        )
        self.trading_settings = FakeCollection(
            one={
                **server.DEFAULT_TRADING_SETTINGS,
                "use_outcome_calibration": True,
                "tier_a_min_p_cover_real": 0.54,
                "tier_b_min_p_cover_real": 0.52,
                "tier_c_min_p_cover_real": 0.50,
                "max_picks_per_day": 3,
                "min_abs_model_edge": 1.5,
            }
        )
        self.performance_daily = FakeCollection(one=None)
        self.strategy_configs = FakeCollection(
            one={
                "strategy_profile": "adaptive_v1",
                "enabled_tiers": ["A"],
                "min_p_cover": 0.56,
                "max_p_cover": 0.58,
                "min_abs_model_edge": 3.0,
                "max_picks_per_day": 2,
            }
        )
        self.upcoming_events = FakeCollection(
            docs=[
                {
                    "event_id": "evt-1",
                    "home_team": "Los Angeles Lakers",
                    "away_team": "Boston Celtics",
                    "commence_time": "2026-03-06T00:00:00+00:00",
                    "status": "pending",
                }
            ]
        )
        self.market_lines = FakeCollection(
            docs=[
                {
                    "event_id": "evt-1",
                    "bookmaker_key": "pinnacle",
                    "spread_point_home": -4.0,
                    "price_home_decimal": 1.91,
                    "price_away_decimal": 1.91,
                }
            ]
        )
        self.outcome_calibrations = FakeCollection(
            one={
                "is_active": True,
                "coefficients": [0.5, 0.0, 0.0],
                "intercept": 0.0,
            }
        )
        self.predictions = FakeCollection(docs=[])


class IdentityScaler:
    def transform(self, x):
        return x


class FixedModel:
    def predict(self, _x):
        return np.array([8.0])


def test_generate_picks_uses_p_cover_real_when_available(monkeypatch):
    fake_db = FakeDB()
    monkeypatch.setattr(server, "db", fake_db)

    async def fake_get_active_model():
        return {
            "model": FixedModel(),
            "scaler": IdentityScaler(),
            "features": ["f1", "f2"],
            "model_version": "unit-test",
            "model_id": "m1",
        }

    async def fake_matchup_features(_home, _away):
        return {
            "confidence": "high",
            "features": {"f1": 1.0, "f2": 2.0},
            "home_abbr": "LAL",
            "away_abbr": "BOS",
        }

    monkeypatch.setattr(server, "get_active_model", fake_get_active_model)
    monkeypatch.setattr(server, "calculate_matchup_features", fake_matchup_features)
    monkeypatch.setattr(server, "select_reference_line", lambda lines, require_pinnacle=True: lines[0] if lines else None)
    monkeypatch.setattr(server, "format_local_time", lambda ts: ts)
    monkeypatch.setattr(server, "generate_recommended_bet_string", lambda *_args, **_kwargs: "HOME -4.0")
    monkeypatch.setattr(server, "generate_explanation", lambda *_args, **_kwargs: "test")
    monkeypatch.setattr(server, "calculate_p_cover_vs_market", lambda *_args, **_kwargs: (0.565, 0.2))
    monkeypatch.setattr(server, "calculate_ev", lambda p, price: p * (price - 1) - (1 - p))
    monkeypatch.setattr(server, "calculate_signal_ev", lambda _ev: "green")
    monkeypatch.setattr(server, "calculate_signal", lambda _edge: "green")

    response = asyncio.run(server.generate_picks(user={"id": "u-1"}))
    assert response["tiering_mode"] == "P_COVER_STRATEGY_ENGINE"
    assert response["summary"]["total_valid_picks"] == 1
    pick = response["all_picks"][0]
    assert pick["p_cover_real"] is not None
    assert pick["passed_strategy_profile"] is True
    assert pick["tier"] == "A"
