import asyncio
from datetime import datetime, timedelta, timezone

from backend.research_consistency import compare_research_vs_picks


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, _n):
        return list(self.docs)


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.docs)


class FakeDB:
    def __init__(self, picks, research):
        self.predictions = FakeCollection(picks)
        self.model_predictions_all = FakeCollection(research)


def test_research_vs_picks_alignment_uses_open_ts_window():
    now = datetime.now(timezone.utc)
    pick = {
        "id": "p1",
        "event_id": "evt-1",
        "open_ts": now.isoformat(),
        "open_spread": -5.5,
        "pred_margin": 3.0,
        "p_cover": 0.54,
        "p_cover_real": 0.47,
    }
    # Same event but from a different snapshot far away in time.
    research_far = {
        "id": "r1",
        "event_id": "evt-1",
        "open_ts": (now + timedelta(hours=8)).isoformat(),
        "open_spread": -7.0,
        "pred_margin": 3.0,
        "p_cover": 0.56,
        "p_cover_real": 0.48,
    }

    db = FakeDB([pick], [research_far])
    result = asyncio.run(compare_research_vs_picks(db, open_ts_tolerance_minutes=180, limit=100))

    # Must be considered expected drift, not a hard mismatch.
    assert result["n_matched"] == 1
    assert result["n_expected_snapshot_drift"] == 1
    assert result["n_mismatches"] == 0
