import asyncio
from datetime import datetime, timedelta, timezone

from backend.market_eval import backfill_close_snapshot


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, _n):
        return list(self.docs)


class FakePredictionsCollection:
    def __init__(self, docs):
        self.docs = docs
        self.updates = []

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.docs)

    async def update_one(self, filt, update):
        self.updates.append((filt, update))

    async def count_documents(self, _query):
        return len(self.docs)


class FakeMarketLinesCollection:
    async def find_one(self, _query, *_args, **_kwargs):
        return {
            "spread_point_home": -5.5,
            "price_home_decimal": 1.95,
            "price_away_decimal": 1.95,
        }


class FakeDB:
    def __init__(self, picks):
        self.predictions = FakePredictionsCollection(picks)
        self.market_lines = FakeMarketLinesCollection()


def test_close_capture_rejects_after_start_without_force():
    now = datetime.now(timezone.utc)
    picks = [
        {
            "id": "p1",
            "event_id": "evt-1",
            "book": "pinnacle",
            "result": "WIN",
            "recommended_side": "HOME",
            "open_spread": -5.0,
            "commence_time": (now - timedelta(hours=2)).isoformat(),
            "settled_at": (now - timedelta(hours=1)).isoformat(),
            "open_ts": (now - timedelta(hours=4)).isoformat(),
            "created_at": (now - timedelta(hours=4)).isoformat(),
            "close_spread": None,
            "close_price": None,
            "clv_spread": None,
        }
    ]
    db = FakeDB(picks)
    result = asyncio.run(backfill_close_snapshot(db=db, days=2, force=False))

    assert result["updated"] == 0
    assert result["invalid_timing_skipped"] == 1
    assert any(u[1]["$set"].get("close_capture_invalid_timing") is True for u in db.predictions.updates)
