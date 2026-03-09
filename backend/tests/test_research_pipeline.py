import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "nba_edge_test")
os.environ.setdefault("JWT_SECRET", "test_secret")

import backend.research_all_games as rag
import backend.research_grade_all as rgrade
import backend.research_metrics as rmetrics


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, limit):
        return self.docs[:limit]


def _match(doc, query):
    for k, v in query.items():
        if isinstance(v, dict):
            val = doc.get(k)
            if "$gte" in v and not (val is not None and val >= v["$gte"]):
                return False
            if "$lte" in v and not (val is not None and val <= v["$lte"]):
                return False
            if "$ne" in v and val == v["$ne"]:
                return False
            if "$in" in v and val not in v["$in"]:
                return False
            continue
        if doc.get(k) != v:
            return False
    return True


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.indexes = []

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))
        return kwargs.get("name", "idx")

    def find(self, query=None, *_args, **_kwargs):
        query = query or {}
        return _Cursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query=None, *_args, **_kwargs):
        query = query or {}
        for d in self.docs:
            if _match(d, query):
                return d.copy()
        return None

    async def insert_one(self, doc):
        self.docs.append(doc.copy())
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, filt, update, upsert=False):
        for i, d in enumerate(self.docs):
            if _match(d, filt):
                if "$set" in update:
                    d.update(update["$set"])
                self.docs[i] = d
                return SimpleNamespace(modified_count=1, matched_count=1, upserted_id=None)
        if upsert:
            new_doc = dict(filt)
            if "$set" in update:
                new_doc.update(update["$set"])
            self.docs.append(new_doc)
            return SimpleNamespace(modified_count=1, matched_count=0, upserted_id="upsert")
        return SimpleNamespace(modified_count=0, matched_count=0, upserted_id=None)

    async def count_documents(self, query=None):
        query = query or {}
        return len([d for d in self.docs if _match(d, query)])


class _FakeDB:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.models = _Collection([{"id": "m1", "is_active": True}])
        self.calibrations = _Collection(
            [
                {
                    "is_active": True,
                    "alpha": 0.0,
                    "beta": 0.35,
                    "sigma_residual": 15.0,
                    "calibration_id": "calib1",
                }
            ]
        )
        self.trading_settings = _Collection([{"_id": "default", "max_picks_per_day": 3, "min_abs_model_edge": 1.5, "tier_c_min_p_cover_real": 0.50}])
        self.upcoming_events = _Collection(
            [
                {
                    "event_id": "e1",
                    "status": "pending",
                    "home_team": "Home A",
                    "away_team": "Away A",
                    "commence_time": (now + timedelta(hours=5)).isoformat(),
                }
            ]
        )
        self.market_lines = _Collection(
            [
                {
                    "event_id": "e1",
                    "bookmaker_key": "pinnacle",
                    "spread_point_home": -4.0,
                    "price_home_decimal": 1.91,
                    "price_away_decimal": 1.91,
                    "updated_at": now.isoformat(),
                }
            ]
        )
        self.model_predictions_all = _Collection([])


class _FakeModel:
    def predict(self, _x):
        return [7.0]


class _FakeScaler:
    def transform(self, x):
        return x


class _FakeServerCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return SimpleNamespace(
            madrid_day_key=lambda _ts: "2026-03-10",
            select_reference_line=lambda lines, require_pinnacle=True: lines[0] if lines else None,
            calculate_matchup_features=self._matchup,
            calculate_p_cover_vs_market=lambda model_edge, alpha, beta, sigma, side: (0.53, 0.12),
        )

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _matchup(self, _home, _away):
        return {
            "features": {"diff_net_rating": 1, "diff_pace": 1, "diff_efg": 1, "diff_tov_pct": 1, "diff_orb_pct": 1, "diff_ftr": 1, "diff_rest": 1, "home_advantage": 1},
            "home_abbr": "HMA",
            "away_abbr": "AWA",
        }


def test_generate_all_idempotent(monkeypatch):
    db = _FakeDB()
    async def _model_bundle(_db):
        return {"model": _FakeModel(), "scaler": _FakeScaler(), "features": ["diff_net_rating", "diff_pace", "diff_efg", "diff_tov_pct", "diff_orb_pct", "diff_ftr", "diff_rest", "home_advantage"], "model_id": "m1", "model_version": "v1"}
    monkeypatch.setattr(rag, "_get_active_model_bundle", _model_bundle)
    monkeypatch.setattr(rag, "_server_db_context", lambda _db: _FakeServerCtx(_db))

    async def _no_outcome(_db):
        return None

    monkeypatch.setattr(rag, "get_active_outcome_calibration", _no_outcome)

    first = asyncio.run(rag.build_all_game_predictions(db, days=2))
    second = asyncio.run(rag.build_all_game_predictions(db, days=2))

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["skipped"] >= 1


def test_generate_all_has_commence_time_dt(monkeypatch):
    db = _FakeDB()
    async def _model_bundle(_db):
        return {"model": _FakeModel(), "scaler": _FakeScaler(), "features": ["diff_net_rating", "diff_pace", "diff_efg", "diff_tov_pct", "diff_orb_pct", "diff_ftr", "diff_rest", "home_advantage"], "model_id": "m1", "model_version": "v1"}
    monkeypatch.setattr(rag, "_get_active_model_bundle", _model_bundle)
    monkeypatch.setattr(rag, "_server_db_context", lambda _db: _FakeServerCtx(_db))

    async def _no_outcome(_db):
        return None

    monkeypatch.setattr(rag, "get_active_outcome_calibration", _no_outcome)

    asyncio.run(rag.build_all_game_predictions(db, days=2))
    assert len(db.model_predictions_all.docs) == 1
    assert isinstance(db.model_predictions_all.docs[0]["commence_time_dt"], datetime)


def test_grade_all_idempotent(monkeypatch):
    db = _FakeDB()
    now = datetime.now(timezone.utc)
    db.model_predictions_all.docs = [
        {
            "id": "r1",
            "event_id": "e1",
            "result": None,
            "commence_time": (now - timedelta(hours=2)).isoformat(),
            "commence_time_dt": now - timedelta(hours=2),
            "open_spread": -4.0,
            "open_price": 1.91,
            "recommended_side": "HOME",
        }
    ]

    class _Resp:
        status_code = 200

        def json(self):
            return [
                {
                    "id": "e1",
                    "completed": True,
                    "home_team": "Home A",
                    "away_team": "Away A",
                    "scores": [{"name": "Home A", "score": "110"}, {"name": "Away A", "score": "100"}],
                }
            ]

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_a, **_k):
            return _Resp()

    monkeypatch.setattr(rgrade.httpx, "AsyncClient", lambda *a, **k: _Client())
    import backend.server as s

    monkeypatch.setattr(s, "ODDS_API_BASE", "https://example.com")
    monkeypatch.setattr(s, "ODDS_API_KEY", "k")

    first = asyncio.run(rgrade.grade_all_predictions(db, days_back=7))
    second = asyncio.run(rgrade.grade_all_predictions(db, days_back=7))

    assert first["graded"] == 1
    assert second["graded"] == 0


def test_metrics_runs_on_small_sample():
    db = _FakeDB()
    now = datetime.now(timezone.utc)
    db.model_predictions_all.docs = [
        {
            "id": "r1",
            "created_at": now,
            "result": "WIN",
            "would_bet": True,
            "tier_if_bet": "A",
            "open_price": 1.91,
            "p_cover_real": 0.56,
            "p_cover": 0.54,
            "pred_margin": 6.0,
            "margin_final": 8.0,
            "clv_spread": 0.5,
        },
        {
            "id": "r2",
            "created_at": now,
            "result": "LOSS",
            "would_bet": False,
            "tier_if_bet": None,
            "open_price": 1.91,
            "p_cover_real": 0.49,
            "p_cover": 0.51,
            "pred_margin": -3.0,
            "margin_final": 1.0,
        },
    ]
    report = asyncio.run(rmetrics.compute_research_metrics(db, days_back=30))
    assert report["status"] == "completed"
    assert "model_metrics" in report
    assert "strategy_metrics" in report
    assert "counts" in report
