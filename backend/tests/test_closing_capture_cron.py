import asyncio
import os
import sys
from pathlib import Path

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "nba_edge_test")
os.environ.setdefault("JWT_SECRET", "test_secret")

from backend import server


def test_capture_closing_lines_defaults_120():
    assert server.capture_closing_lines.__defaults__[0] == 120
    assert server.capture_closing_lines_task.__defaults__[0] == 120


def test_capture_closing_lines_cron_requires_key(monkeypatch):
    monkeypatch.setattr(server, "CRON_API_KEY", "")
    try:
        asyncio.run(server.capture_closing_lines_cron())
        assert False, "Expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 503


def test_capture_closing_lines_cron_success(monkeypatch):
    async def _fake_capture(window_minutes=120, limit=500):
        return {"window_minutes": window_minutes, "n_updates_made": 1}

    async def _fake_warn(*_args, **_kwargs):
        return {"count": 0, "sample": []}

    monkeypatch.setattr(server, "CRON_API_KEY", "secret")
    monkeypatch.setattr(server, "capture_closing_lines_task", _fake_capture)
    monkeypatch.setattr(server, "collect_missing_closing_line_warnings", _fake_warn)

    out = asyncio.run(server.capture_closing_lines_cron(window_minutes=120, limit=20, x_cron_key="secret"))
    assert out["status"] == "completed"
    assert out["capture"]["window_minutes"] == 120
    assert out["capture"]["n_updates_made"] == 1
