import asyncio

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

    def find(self, query, *_args, **_kwargs):
        if "settled_at" in query or "open_ts" in query or "created_at" in query:
            return FakeCursor(self.docs)
        if query.get("close_spread") is None:
            filtered = [d for d in self.docs if d.get("close_spread") is None]
            return FakeCursor(filtered)
        return FakeCursor(self.docs)

    async def update_one(self, filt, update):
        self.updates.append((filt, update))

    async def count_documents(self, query):
        if not query:
            return len(self.docs)
        if query.get("book") == "pinnacle":
            return sum(1 for d in self.docs if d.get("book") == "pinnacle")
        if query.get("close_spread") is None:
            return sum(1 for d in self.docs if d.get("close_spread") is None)
        return len(self.docs)


class FakeMarketLinesCollection:
    async def find_one(self, query, *_args, **_kwargs):
        if query.get("event_id") == "evt-1":
            return {
                "event_id": "evt-1",
                "bookmaker_key": "pinnacle",
                "spread_point_home": -6.0,
                "price_home_decimal": 1.95,
                "price_away_decimal": 1.87,
            }
        return None


class FakeDB:
    def __init__(self, picks):
        self.predictions = FakePredictionsCollection(picks)
        self.market_lines = FakeMarketLinesCollection()


def test_backfill_close_snapshot_updates_nulls_only():
    picks = [
        {
            "id": "p-1",
            "event_id": "evt-1",
            "recommended_side": "HOME",
            "open_spread": -5.0,
            "book": "pinnacle",
            "result": "WIN",
            "settled_at": "2026-03-04T04:00:00+00:00",
            "open_ts": "2026-03-04T02:10:00+00:00",
            "created_at": "2026-03-04T02:20:00+00:00",
            "commence_time": "2026-03-04T02:00:00+00:00",
            "close_spread": -5.5,
            "close_price": None,
            "clv_spread": None,
            "close_captured_at": None,
            "close_source": None,
        },
        {
            "id": "p-2",
            "event_id": "evt-1",
            "recommended_side": "AWAY",
            "open_spread": -5.0,
            "book": "pinnacle",
            "result": "LOSS",
            "settled_at": "2026-03-04T05:00:00+00:00",
            "open_ts": "2026-03-04T03:10:00+00:00",
            "created_at": "2026-03-04T03:20:00+00:00",
            "commence_time": "2026-03-04T03:00:00+00:00",
            "close_spread": None,
            "close_price": None,
            "clv_spread": None,
            "close_captured_at": None,
            "close_source": None,
        },
    ]
    db = FakeDB(picks)
    result = asyncio.run(backfill_close_snapshot(db=db, days=7, force=False))

    assert result["status"] == "completed"
    assert result["updated"] == 2

    updates_by_id = {u[0]["id"]: u[1]["$set"] for u in db.predictions.updates}

    # Existing non-null close_spread must not be overwritten when force=False.
    assert "close_spread" not in updates_by_id["p-1"]
    # Missing fields should be completed.
    assert updates_by_id["p-1"]["close_price"] == 1.95
    assert updates_by_id["p-1"]["clv_spread"] == 1.0

    # Fully missing close fields should be populated.
    assert updates_by_id["p-2"]["close_spread"] == -6.0
    assert updates_by_id["p-2"]["close_price"] == 1.87

    # Never write nulls.
    for _pid, fields in updates_by_id.items():
        assert all(v is not None for v in fields.values())
