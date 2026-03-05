import asyncio

from backend.selection_backtest import _simulate_config, run_selection_sweep


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


class FakeOutcomeCalibrations:
    def __init__(self, one=None):
        self.one = one

    async def find_one(self, *_args, **_kwargs):
        return self.one


class FakeDB:
    def __init__(self, predictions_docs, active_outcome=None):
        self.predictions = FakePredictions(predictions_docs)
        self.outcome_calibrations = FakeOutcomeCalibrations(active_outcome)


def _make_pick(i, day, result="WIN", edge=2.5, pcr=0.55, spread=-5.0, price=1.91):
    return {
        "id": str(i),
        "result": result,
        "model_edge": edge,
        "p_cover_real": pcr,
        "open_spread": spread,
        "open_price": price,
        "commence_time": f"2026-03-{day:02d}T18:00:00+00:00",
        "settled_at": f"2026-03-{day:02d}T23:30:00+00:00",
        "clv_spread": 0.2,
    }


def test_selection_sweep_runs(tmp_path):
    docs = []
    # 40 resolved picks so some configs satisfy n>=30.
    for i in range(1, 41):
        result = "WIN" if i % 2 == 0 else "LOSS"
        docs.append(_make_pick(i, day=((i % 10) + 1), result=result, edge=2.0 + (i % 3) * 0.5, pcr=0.50 + (i % 4) * 0.02))
    db = FakeDB(docs)
    out_path = tmp_path / "selection_sweep.json"

    result = asyncio.run(run_selection_sweep(db=db, out_path=str(out_path)))
    assert result["status"] == "completed"
    assert result["n_configs_total"] > 0
    assert "top_20" in result
    assert out_path.exists()


def test_selection_sweep_respects_max_picks_per_day():
    settled = [
        dict(_make_pick(1, 5, "WIN", edge=3.0, pcr=0.60), sim_pnl=0.91),
        dict(_make_pick(2, 5, "WIN", edge=2.8, pcr=0.58), sim_pnl=0.91),
        dict(_make_pick(3, 5, "LOSS", edge=2.6, pcr=0.57), sim_pnl=-1.0),
        dict(_make_pick(4, 5, "LOSS", edge=2.4, pcr=0.56), sim_pnl=-1.0),
    ]

    row = _simulate_config(
        settled=settled,
        min_abs_model_edge=1.5,
        p_cover_real_threshold=0.50,
        max_picks_per_day=2,
        max_abs_open_spread=None,
        min_open_price=None,
    )
    assert row["n_picks"] == 2


def test_selection_sweep_filters_work():
    settled = [
        dict(_make_pick(1, 6, "WIN", edge=3.2, pcr=0.58, spread=-6.0, price=1.92), sim_pnl=0.92),
        dict(_make_pick(2, 6, "WIN", edge=1.7, pcr=0.53, spread=-9.0, price=1.82), sim_pnl=0.82),
        dict(_make_pick(3, 7, "LOSS", edge=2.2, pcr=0.49, spread=-5.0, price=1.91), sim_pnl=-1.0),
    ]

    row = _simulate_config(
        settled=settled,
        min_abs_model_edge=2.0,
        p_cover_real_threshold=0.54,
        max_picks_per_day=3,
        max_abs_open_spread=8.5,
        min_open_price=1.90,
    )
    # Only first pick passes all filters.
    assert row["n_picks"] == 1
