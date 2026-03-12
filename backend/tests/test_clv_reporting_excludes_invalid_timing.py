import asyncio
from datetime import datetime, timedelta, timezone

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
        self.last_update_one = None
        self._indexes = {
            "_id_": {"key": [("_id", 1)]},
            "uq_performance_daily_as_of_date": {"key": [("as_of_date", 1)]},
        }

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.docs)

    async def find_one(self, *_args, **_kwargs):
        return self.one

    async def update_one(self, filt, update, upsert=False):
        self.last_update_one = (filt, update, upsert)

    async def index_information(self):
        return dict(self._indexes)

    async def drop_index(self, name):
        self._indexes.pop(name, None)

    async def create_index(self, keys, unique=False, name=None):
        if name:
            self._indexes[name] = {"key": keys, "unique": unique}


class FakeDB:
    def __init__(self, picks):
        self.predictions = FakeCollection(docs=picks)
        self.outcome_calibrations = FakeCollection(one=None)
        self.performance_daily = FakeCollection()


def test_clv_reporting_excludes_invalid_timing():
    now = datetime.now(timezone.utc)
    picks = [
        {
            "result": "WIN",
            "profit_units": 0.9,
            "clv_spread": 1.0,
            "close_captured_at": (now - timedelta(minutes=30)).isoformat(),
            "commence_time": (now + timedelta(minutes=30)).isoformat(),
            "created_at": (now - timedelta(hours=2)).isoformat(),
            "settled_at": now.isoformat(),
        },
        {
            "result": "LOSS",
            "profit_units": -1.0,
            "clv_spread": -2.0,
            "close_captured_at": (now + timedelta(minutes=10)).isoformat(),
            "commence_time": now.isoformat(),
            "created_at": (now - timedelta(hours=3)).isoformat(),
            "settled_at": now.isoformat(),
        },
    ]
    db = FakeDB(picks)
    doc = asyncio.run(recompute_performance_daily(db))

    assert doc["n_clv_valid"] == 1
    assert doc["n_clv_invalid_timing"] == 1
    assert doc["mean_clv_valid"] == 1.0
    assert doc["median_clv_valid"] == 1.0
    assert doc["market_beating_rate_valid"] == 1.0
