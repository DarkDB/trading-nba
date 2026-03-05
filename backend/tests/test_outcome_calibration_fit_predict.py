import asyncio

from backend.calibration_outcome import fit_outcome_calibration, predict_p_cover_outcome


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, _n):
        return list(self.docs)


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.inserted = []
        self.updated = []

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.docs)

    async def update_many(self, filt, update):
        self.updated.append((filt, update))

    async def insert_one(self, doc):
        self.inserted.append(doc)


class FakeDB:
    def __init__(self, predictions_docs):
        self.predictions = FakeCollection(predictions_docs)
        self.outcome_calibrations = FakeCollection([])


def test_outcome_calibration_fit_predict():
    picks = [
        {"result": "WIN", "model_edge": 2.0, "open_price": 1.91, "open_spread": -4.5},
        {"result": "LOSS", "model_edge": -1.0, "open_price": 1.91, "open_spread": -4.5},
        {"result": "WIN", "model_edge": 3.0, "open_price": 1.95, "open_spread": -5.0},
        {"result": "LOSS", "model_edge": -2.0, "open_price": 1.87, "open_spread": -3.5},
        {"result": "WIN", "model_edge": 1.5, "open_price": 1.90, "open_spread": -2.5},
        {"result": "LOSS", "model_edge": -0.5, "open_price": 1.92, "open_spread": -2.0},
        {"result": "WIN", "model_edge": 2.8, "open_price": 1.89, "open_spread": -6.0},
        {"result": "LOSS", "model_edge": -1.8, "open_price": 1.93, "open_spread": -6.0},
    ]
    db = FakeDB(picks)

    result = asyncio.run(fit_outcome_calibration(db=db, include_push_as_half=False, min_samples=6))
    assert result["status"] == "completed"
    assert result["n_samples"] == 8
    assert len(db.outcome_calibrations.inserted) == 1

    calibration_doc = db.outcome_calibrations.inserted[0]
    p = predict_p_cover_outcome(
        model_edge=2.2,
        open_price=1.91,
        open_spread=-4.5,
        calibration_doc=calibration_doc,
    )
    assert p is not None
    assert 0.0 < p < 1.0
