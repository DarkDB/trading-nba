import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "nba_edge_test")
os.environ.setdefault("JWT_SECRET", "test_secret")

from backend import server
from backend.performance import recompute_performance_daily
import backend.performance as performance_module


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, limit):
        return self.docs[:limit]


class _PredictionsCollection:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query, *_args, **_kwargs):
        if not query:
            return _Cursor(self.docs)
        out = []
        for d in self.docs:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict):
                    if "$ne" in v and d.get(k) == v["$ne"]:
                        ok = False
                        break
                    continue
                if d.get(k) != v:
                    ok = False
                    break
            if ok:
                out.append(d)
        return _Cursor(out)


class _PerformanceDailyCollection:
    def __init__(self):
        self.calls = []

    async def update_one(self, key, update, upsert=False):
        self.calls.append({"key": key, "update": update, "upsert": upsert})


class _FakeDB:
    def __init__(self, docs):
        self.predictions = _PredictionsCollection(docs)
        self.performance_daily = _PerformanceDailyCollection()


def test_is_operational_user_blocks_probe_and_admin():
    assert server.is_operational_user({"id": "admin", "email": "x@y.com", "name": "Admin"}) is False
    assert server.is_operational_user({"id": "u1", "email": "probe_register@test.com", "name": "Edu"}) is False
    assert server.is_operational_user({"id": "u2", "email": "ok@test.com", "name": "Probe"}) is False


def test_is_operational_user_allows_regular_user():
    assert server.is_operational_user({"id": "u1", "email": "edinho2391@gmail.com", "name": "Eduardo"}) is True


def test_recompute_performance_daily_is_user_scoped():
    docs = [
        {
            "id": "p1",
            "user_id": "u1",
            "result": "WIN",
            "profit_units": 1.0,
            "created_at": "2026-03-08T10:00:00+00:00",
            "settled_at": "2026-03-08T12:00:00+00:00",
        },
        {
            "id": "p2",
            "user_id": "u2",
            "result": "LOSS",
            "profit_units": -1.0,
            "created_at": "2026-03-08T10:01:00+00:00",
            "settled_at": "2026-03-08T12:01:00+00:00",
        },
    ]
    db = _FakeDB(docs)
    async def _fake_active_outcome(_db):
        return None
    performance_module.get_active_outcome_calibration = _fake_active_outcome
    snapshot = asyncio.run(recompute_performance_daily(db, user_id="u1"))
    assert snapshot["user_id"] == "u1"
    assert snapshot["n_picks_total"] == 1
    assert snapshot["n_picks_settled"] == 1
    assert db.performance_daily.calls[-1]["key"]["user_id"] == "u1"
