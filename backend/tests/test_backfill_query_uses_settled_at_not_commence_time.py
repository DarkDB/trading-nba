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
        self.last_query = None

    def find(self, query, *_args, **_kwargs):
        self.last_query = query
        # Main backfill query path should be settled_at-based.
        if "settled_at" in query:
            return FakeCursor(self.docs)
        if query.get("close_spread") is None:
            return FakeCursor([d for d in self.docs if d.get("close_spread") is None])
        return FakeCursor(self.docs)

    async def update_one(self, filt, update):
        self.updates.append((filt, update))

    async def count_documents(self, query):
        if not query:
            return len(self.docs)
        if query.get("book") == "pinnacle":
            return len([d for d in self.docs if d.get("book") == "pinnacle"])
        if query.get("close_spread") is None:
            return len([d for d in self.docs if d.get("close_spread") is None])
        if "settled_at" in query:
            return len(self.docs)
        return len(self.docs)


class FakeMarketLinesCollection:
    async def find_one(self, query, *_args, **_kwargs):
        if query.get("event_id") == "evt-1":
            return {
                "event_id": "evt-1",
                "bookmaker_key": "pinnacle",
                "spread_point_home": -4.5,
                "price_home_decimal": 1.91,
                "price_away_decimal": 1.91,
            }
        return None


class FakeDB:
    def __init__(self, docs):
        self.predictions = FakePredictionsCollection(docs)
        self.market_lines = FakeMarketLinesCollection()


def test_backfill_query_uses_settled_at_not_commence_time():
    now = datetime.now(timezone.utc)
    old_commence = (now - timedelta(days=30)).isoformat()
    recent_settled = (now - timedelta(hours=12)).isoformat()
    docs = [
        {
            "id": "p-1",
            "event_id": "evt-1",
            "recommended_side": "HOME",
            "book": "pinnacle",
            "result": "WIN",
            "open_spread": -4.0,
            "open_price": 1.91,
            "close_spread": None,
            "close_price": None,
            "clv_spread": None,
            "commence_time": old_commence,   # deliberately out of range
            "settled_at": recent_settled,    # in range: must be selected
            "created_at": recent_settled,
            "open_ts": recent_settled,
        }
    ]
    db = FakeDB(docs)

    res = asyncio.run(backfill_close_snapshot(db=db, days=2, force=False, debug=True, debug_query=True))
    assert res["status"] == "completed"
    assert res["updated"] == 1
    assert "query_final" in res
    assert "settled_query" in res["query_final"] or "settled_at" in res["query_final"]
