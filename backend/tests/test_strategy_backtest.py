import asyncio

from backend.strategy_backtest import run_strategy_backtest


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


def _pick(i, day, result="WIN", p_cover=0.565, edge=4.0, clv=0.5):
    return {
        "id": str(i),
        "event_id": f"evt-{i}",
        "result": result,
        "p_cover": p_cover,
        "model_edge": edge,
        "open_price": 1.91,
        "profit_units": 0.91 if result == "WIN" else -1.0,
        "created_at": f"2026-03-{day:02d}T12:00:00+00:00",
        "settled_at": f"2026-03-{day:02d}T23:00:00+00:00",
        "commence_time": f"2026-03-{day:02d}T20:00:00+00:00",
        "close_captured_at": f"2026-03-{day:02d}T19:30:00+00:00",
        "clv_spread": clv,
        "archived": False,
    }


def test_strategy_backtest_runs(tmp_path):
    docs = [
        _pick(1, 1, "WIN"),
        _pick(2, 2, "LOSS"),
        _pick(3, 3, "WIN", p_cover=0.575, edge=5.0),
        _pick(4, 4, "LOSS", p_cover=0.57, edge=4.1),
    ]
    db = FakeDB(docs)
    result = asyncio.run(run_strategy_backtest(db=db, out_path=str(tmp_path / "strategy_backtest.json")))
    assert result["status"] == "completed"
    assert "summary" in result
    assert "blocks" in result
