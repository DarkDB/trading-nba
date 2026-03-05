import asyncio

from backend.walkforward_selection import run_walkforward_selection


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, _n):
        return list(self.docs)


class FakePredictions:
    def __init__(self, docs):
        self.docs = docs

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.docs)


class FakeDB:
    def __init__(self, docs):
        self.predictions = FakePredictions(docs)


def _mk(i: int, day: int, result: str, edge: float, price: float = 1.91):
    return {
        "id": str(i),
        "result": result,
        "model_edge": edge,
        "open_price": price,
        "open_spread": -4.5,
        "settled_at": f"2026-01-{day:02d}T10:00:00+00:00",
        "commence_time": f"2026-01-{day:02d}T01:00:00+00:00",
    }


def test_walkforward_no_leakage(tmp_path):
    docs = []
    # Build 70 rows over 14 days so each 7-day block can train on prior data.
    for i in range(1, 71):
        day = ((i - 1) % 14) + 1
        result = "WIN" if i % 2 == 0 else "LOSS"
        edge = 2.0 if result == "WIN" else -2.0
        docs.append(_mk(i, day, result, edge))

    db = FakeDB(docs)
    out = tmp_path / "wf.json"
    res = asyncio.run(
        run_walkforward_selection(
            db=db,
            out_path=str(out),
            train_min_samples=10,
            step_days=7,
            start_date="2026-01-01",
            end_date="2026-01-14",
        )
    )

    for b in res["blocks"]:
        if b["status"] != "ok":
            continue
        assert b["train_range"]["end"] < b["block_start"]


def test_walkforward_outputs_blocks(tmp_path):
    docs = []
    for i in range(1, 51):
        day = ((i - 1) % 10) + 1
        result = "WIN" if i % 3 != 0 else "LOSS"
        edge = 2.3 if result == "WIN" else -2.1
        docs.append(_mk(i, day, result, edge, price=1.93))

    db = FakeDB(docs)
    out = tmp_path / "wf2.json"
    res = asyncio.run(
        run_walkforward_selection(
            db=db,
            out_path=str(out),
            train_min_samples=8,
            step_days=7,
            start_date="2026-01-01",
            end_date="2026-01-10",
        )
    )

    assert res["status"] == "completed"
    assert isinstance(res["blocks"], list)
    assert len(res["blocks"]) >= 1
    assert "summary" in res
    assert "by_month" in res
    assert out.exists()
