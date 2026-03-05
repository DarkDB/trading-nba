import asyncio

import pytest

from backend.calibration_outcome import fit_outcome_calibration
from backend.performance import recompute_performance_daily


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
        self.inserted = []
        self.updated = []
        self.last_update_one = None

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.docs)

    async def find_one(self, *_args, **_kwargs):
        return self.one

    async def update_many(self, filt, update):
        self.updated.append((filt, update))

    async def insert_one(self, doc):
        self.inserted.append(doc)
        self.one = doc

    async def update_one(self, filt, update, upsert=False):
        self.last_update_one = (filt, update, upsert)


class FakeDB:
    def __init__(self, predictions_docs, active_outcome_calibration=None):
        self.predictions = FakeCollection(docs=predictions_docs)
        self.outcome_calibrations = FakeCollection(one=active_outcome_calibration)
        self.performance_daily = FakeCollection()


def test_performance_computes_p_cover_real_when_missing_in_predictions():
    picks = [
        {
            "id": "1",
            "result": "WIN",
            "model_edge": 2.0,
            "open_price": 1.91,
            "open_spread": -4.5,
            "profit_units": 0.91,
            "created_at": "2026-03-01T00:00:00+00:00",
            "settled_at": "2026-03-02T00:00:00+00:00",
        },
        {
            "id": "2",
            "result": "LOSS",
            "model_edge": -1.5,
            "open_price": 1.91,
            "open_spread": -4.5,
            "profit_units": -1.0,
            "created_at": "2026-03-01T01:00:00+00:00",
            "settled_at": "2026-03-02T01:00:00+00:00",
        },
    ]
    active = {
        "feature_names": ["model_edge", "open_price", "abs_open_spread"],
        "coefficients": [0.6, 0.0, 0.0],
        "intercept": 0.0,
        "include_push_as_half": False,
    }
    db = FakeDB(picks, active_outcome_calibration=active)

    doc = asyncio.run(recompute_performance_daily(db))
    assert doc["avg_p_cover_real_50"] is not None
    assert doc["brier_score_50"] is not None
    assert 0.0 <= doc["avg_p_cover_real_50"] <= 1.0


def test_calibration_outcome_exposes_feature_names():
    picks = [
        {"result": "WIN", "model_edge": 1.0, "open_price": 1.9, "open_spread": -2.5},
        {"result": "LOSS", "model_edge": -1.0, "open_price": 1.9, "open_spread": -2.5},
        {"result": "WIN", "model_edge": 2.0, "open_price": 1.91, "open_spread": -3.5},
        {"result": "LOSS", "model_edge": -2.0, "open_price": 1.91, "open_spread": -3.5},
        {"result": "WIN", "model_edge": 3.0, "open_price": 1.95, "open_spread": -5.0},
        {"result": "LOSS", "model_edge": -3.0, "open_price": 1.95, "open_spread": -5.0},
    ]
    db = FakeDB(picks)

    result = asyncio.run(fit_outcome_calibration(db, include_push_as_half=False, min_samples=6))
    assert result["status"] == "completed"
    assert result["feature_names"] == ["model_edge", "open_price", "abs_open_spread"]
    assert db.outcome_calibrations.inserted[0]["feature_names"] == ["model_edge", "open_price", "abs_open_spread"]


def test_brier_score_computation_excludes_push():
    picks = [
        {
            "id": "1",
            "result": "WIN",
            "model_edge": 0.0,
            "open_price": 1.91,
            "open_spread": -4.5,
            "profit_units": 0.91,
            "created_at": "2026-03-01T00:00:00+00:00",
            "settled_at": "2026-03-02T00:00:00+00:00",
        },
        {
            "id": "2",
            "result": "LOSS",
            "model_edge": 0.0,
            "open_price": 1.91,
            "open_spread": -4.5,
            "profit_units": -1.0,
            "created_at": "2026-03-01T01:00:00+00:00",
            "settled_at": "2026-03-02T01:00:00+00:00",
        },
        {
            "id": "3",
            "result": "PUSH",
            "model_edge": 0.0,
            "open_price": 1.91,
            "open_spread": -4.5,
            "profit_units": 0.0,
            "created_at": "2026-03-01T02:00:00+00:00",
            "settled_at": "2026-03-02T02:00:00+00:00",
        },
    ]
    # With intercept=0 and coeffs=0 => p_cover_real=0.5 for all rows.
    active = {
        "feature_names": ["model_edge", "open_price", "abs_open_spread"],
        "coefficients": [0.0, 0.0, 0.0],
        "intercept": 0.0,
        "include_push_as_half": False,
    }
    db = FakeDB(picks, active_outcome_calibration=active)

    doc = asyncio.run(recompute_performance_daily(db))
    # PUSH must be excluded: mean([(0.5-1)^2, (0.5-0)^2]) = 0.25
    assert doc["brier_score_50"] == pytest.approx(0.25, rel=1e-9)
