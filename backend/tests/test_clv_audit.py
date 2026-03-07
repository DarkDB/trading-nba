import os
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "nba_edge_test")
os.environ.setdefault("JWT_SECRET", "test_secret")

from backend.clv_audit import recompute_clv_audit
from backend import server


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
        self.updates = []

    def find(self, *_args, **_kwargs):
        return _Cursor(self.docs)

    async def update_one(self, query, update):
        self.updates.append((query, update))
        target = query.get("id")
        for d in self.docs:
            if d.get("id") == target:
                d.update(update.get("$set", {}))
                break


class _FakeDB:
    def __init__(self, docs):
        self.predictions = _PredictionsCollection(docs)


def _calc_clv(open_spread, close_spread, side):
    if side == "HOME":
        return round(open_spread - close_spread, 2)
    return round(close_spread - open_spread, 2)


def test_clv_home_favorite_sign():
    assert _calc_clv(-7.0, -8.0, "HOME") == 1.0


def test_clv_away_dog_sign():
    assert _calc_clv(-7.0, -8.0, "AWAY") == -1.0


def test_clv_zero_when_same_line():
    assert _calc_clv(-6.5, -6.5, "HOME") == 0.0
    assert _calc_clv(6.5, 6.5, "AWAY") == 0.0


def test_clv_recompute_matches_saved():
    docs = [
        {
            "id": "p1",
            "event_id": "e1",
            "recommended_side": "HOME",
            "open_spread": -7.0,
            "close_spread": -8.0,
            "clv_spread": 1.0,
            "created_at": "2026-03-07T00:00:00Z",
        },
        {
            "id": "p2",
            "event_id": "e2",
            "recommended_side": "AWAY",
            "open_spread": -5.5,
            "close_spread": -5.0,
            "clv_spread": 0.5,
            "created_at": "2026-03-07T00:00:01Z",
        },
    ]
    db = _FakeDB(docs)
    audit = asyncio.run(recompute_clv_audit(db, limit=100))
    assert audit["mismatch_count"] == 0
    assert audit["mismatch_rate"] == 0.0


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *_args, **_kwargs):
        payload = {
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "markets": [
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Home Team", "point": -8.0, "price": 1.93},
                                {"name": "Away Team", "point": 8.0, "price": 1.95},
                            ],
                        }
                    ],
                }
            ]
        }
        return _FakeResponse(200, payload)


def test_capture_closing_lines_persists_clv(monkeypatch):
    now = datetime.now(timezone.utc)
    docs = [
        {
            "id": "p1",
            "event_id": "event_1",
            "home_team": "Home Team",
            "away_team": "Away Team",
            "recommended_side": "HOME",
            "open_spread": -7.0,
            "result": None,
            "book": "pinnacle",
            "close_spread": None,
            "commence_time": (now + timedelta(minutes=10)).isoformat(),
        }
    ]
    db = _FakeDB(docs)
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(server.capture_closing_lines_task(window_minutes=30, limit=50))

    assert out["n_candidates"] == 1
    assert out["n_updates_made"] == 1
    saved = db.predictions.docs[0]
    assert saved["close_spread"] == -8.0
    assert saved["close_price"] == 1.93
    assert saved["clv_spread"] == 1.0
    assert saved["close_source"] == "theoddsapi"
    assert saved["close_captured_at"] is not None
