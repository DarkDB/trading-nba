import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
from sklearn.linear_model import LogisticRegression


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _iso_to_dt(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        return None


def _pick_time(p: Dict[str, Any]) -> Optional[datetime]:
    return _iso_to_dt(p.get("settled_at")) or _iso_to_dt(p.get("commence_time"))


def _madrid_day_key_from_dt(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d")


def _build_xy(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    X = []
    y = []
    kept_rows = []
    for r in rows:
        result = r.get("result")
        if result not in ("WIN", "LOSS"):
            continue
        model_edge = _to_float(r.get("model_edge"))
        open_price = _to_float(r.get("open_price"))
        open_spread = _to_float(r.get("open_spread"))
        if model_edge is None or open_price is None or open_spread is None:
            continue
        X.append([model_edge, open_price, abs(open_spread)])
        y.append(1 if result == "WIN" else 0)
        kept_rows.append(r)
    return {"X": np.array(X), "y": np.array(y), "rows": kept_rows}


@dataclass
class TrainedOutcome:
    model: LogisticRegression
    n_samples: int
    train_start: str
    train_end: str


def _train_outcome(rows: List[Dict[str, Any]], train_min_samples: int) -> Optional[TrainedOutcome]:
    data = _build_xy(rows)
    X = data["X"]
    y = data["y"]
    if len(y) < train_min_samples:
        return None
    if len(set(y.tolist())) < 2:
        return None

    clf = LogisticRegression(penalty="l2", C=1.0, max_iter=1000, random_state=42)
    clf.fit(X, y)
    start_dt = min(_pick_time(r) for r in data["rows"] if _pick_time(r) is not None)
    end_dt = max(_pick_time(r) for r in data["rows"] if _pick_time(r) is not None)
    return TrainedOutcome(
        model=clf,
        n_samples=len(y),
        train_start=start_dt.isoformat() if start_dt else "",
        train_end=end_dt.isoformat() if end_dt else "",
    )


def _predict_p_cover_real(model: LogisticRegression, pick: Dict[str, Any]) -> Optional[float]:
    model_edge = _to_float(pick.get("model_edge"))
    open_price = _to_float(pick.get("open_price"))
    open_spread = _to_float(pick.get("open_spread"))
    if model_edge is None or open_price is None or open_spread is None:
        return None
    x = np.array([[model_edge, open_price, abs(open_spread)]])
    return float(model.predict_proba(x)[0, 1])


def _simulate_block(
    block_rows: List[Dict[str, Any]],
    model: LogisticRegression,
    min_abs_model_edge: float,
    p_cover_real_threshold: float,
    max_picks_per_day: int,
) -> Dict[str, Any]:
    candidates = []
    for p in block_rows:
        result = p.get("result")
        if result not in ("WIN", "LOSS"):
            continue
        edge = abs(_to_float(p.get("model_edge")) or 0.0)
        if edge < min_abs_model_edge:
            continue
        pcr = _predict_p_cover_real(model, p)
        if pcr is None or pcr < p_cover_real_threshold:
            continue
        time_dt = _pick_time(p)
        if time_dt is None:
            continue
        open_price = _to_float(p.get("open_price"))
        if open_price is None or open_price <= 1.0:
            continue
        sim_pnl = (open_price - 1.0) if result == "WIN" else -1.0
        row = dict(p)
        row["_p_cover_real_wf"] = pcr
        row["_sim_pnl"] = sim_pnl
        row["_time"] = time_dt
        candidates.append(row)

    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for p in candidates:
        by_day.setdefault(_madrid_day_key_from_dt(p["_time"]), []).append(p)

    selected = []
    for day in sorted(by_day.keys()):
        ranked = sorted(
            by_day[day],
            key=lambda r: (r["_p_cover_real_wf"], abs(_to_float(r.get("model_edge")) or 0.0)),
            reverse=True,
        )
        selected.extend(ranked[:max_picks_per_day])

    selected = sorted(selected, key=lambda r: r["_time"])
    pnl_seq = [float(r["_sim_pnl"]) for r in selected]
    pnl = float(sum(pnl_seq))
    n = len(selected)
    roi = (pnl / n) if n > 0 else None
    wins = sum(1 for r in selected if r.get("result") == "WIN")
    losses = sum(1 for r in selected if r.get("result") == "LOSS")
    wl = wins + losses
    winrate = (wins / wl) if wl > 0 else None

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for v in pnl_seq:
        equity += v
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    avg_pcr = mean([float(r["_p_cover_real_wf"]) for r in selected]) if selected else None
    return {
        "selected": selected,
        "metrics": {
            "n_picks": n,
            "roi": roi,
            "pnl": pnl,
            "winrate": winrate,
            "max_drawdown": max_dd,
            "avg_p_cover_real": avg_pcr,
        },
    }


async def run_walkforward_selection(
    db,
    out_path: str = "backend/data/walkforward_selection.json",
    train_min_samples: int = 50,
    step_days: int = 7,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    docs = await db.predictions.find({"result": {"$in": ["WIN", "LOSS", "PUSH"]}}, {"_id": 0}).to_list(50000)

    rows = []
    for p in docs:
        t = _pick_time(p)
        if t is None:
            continue
        row = dict(p)
        row["_time"] = t
        rows.append(row)

    rows = sorted(rows, key=lambda r: r["_time"])
    if not rows:
        result = {"status": "completed", "blocks": [], "summary": {"n_blocks": 0}}
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    default_start = rows[0]["_time"].date().isoformat()
    default_end = rows[-1]["_time"].date().isoformat()
    start_dt = datetime.fromisoformat((start_date or default_start) + "T00:00:00+00:00")
    end_dt = datetime.fromisoformat((end_date or default_end) + "T23:59:59+00:00")
    step = timedelta(days=max(1, int(step_days)))

    min_abs_model_edge = 1.5
    p_cover_real_threshold = 0.50
    max_picks_per_day = 3

    blocks = []
    all_selected = []
    block_start = start_dt
    while block_start <= end_dt:
        block_end = min(block_start + step, end_dt + timedelta(seconds=1))
        train_rows = [r for r in rows if r["_time"] < block_start]
        test_rows = [r for r in rows if block_start <= r["_time"] < block_end]

        trained = _train_outcome(train_rows, train_min_samples=train_min_samples)
        if trained is None:
            blocks.append(
                {
                    "block_start": block_start.isoformat(),
                    "block_end": (block_end - timedelta(seconds=1)).isoformat(),
                    "train_samples": len(_build_xy(train_rows)["y"]),
                    "status": "skipped_insufficient_train",
                    "metrics": {
                        "n_picks": 0,
                        "roi": None,
                        "pnl": 0.0,
                        "winrate": None,
                        "max_drawdown": 0.0,
                        "avg_p_cover_real": None,
                    },
                }
            )
            block_start = block_end
            continue

        sim = _simulate_block(
            block_rows=test_rows,
            model=trained.model,
            min_abs_model_edge=min_abs_model_edge,
            p_cover_real_threshold=p_cover_real_threshold,
            max_picks_per_day=max_picks_per_day,
        )
        selected = sim["selected"]
        all_selected.extend(selected)
        blocks.append(
            {
                "block_start": block_start.isoformat(),
                "block_end": (block_end - timedelta(seconds=1)).isoformat(),
                "train_samples": trained.n_samples,
                "train_range": {
                    "start": trained.train_start,
                    "end": trained.train_end,
                },
                "status": "ok",
                "metrics": sim["metrics"],
            }
        )
        block_start = block_end

    all_selected = sorted(all_selected, key=lambda r: r["_time"])
    pnl_seq = [float(r["_sim_pnl"]) for r in all_selected]
    equity_curve = []
    equity = 0.0
    for r in all_selected:
        equity += float(r["_sim_pnl"])
        equity_curve.append({"ts": r["_time"].isoformat(), "equity": round(equity, 4)})

    wins = sum(1 for r in all_selected if r.get("result") == "WIN")
    losses = sum(1 for r in all_selected if r.get("result") == "LOSS")
    wl = wins + losses

    peak = 0.0
    max_dd = 0.0
    cur = 0.0
    for v in pnl_seq:
        cur += v
        if cur > peak:
            peak = cur
        dd = peak - cur
        if dd > max_dd:
            max_dd = dd

    monthly: Dict[str, Dict[str, Any]] = {}
    for r in all_selected:
        month_key = r["_time"].astimezone(timezone.utc).strftime("%Y-%m")
        m = monthly.setdefault(month_key, {"n_picks": 0, "pnl": 0.0, "wins": 0, "losses": 0})
        m["n_picks"] += 1
        m["pnl"] += float(r["_sim_pnl"])
        if r.get("result") == "WIN":
            m["wins"] += 1
        elif r.get("result") == "LOSS":
            m["losses"] += 1

    monthly_rows = []
    for m in sorted(monthly.keys()):
        n = monthly[m]["n_picks"]
        wins_m = monthly[m]["wins"]
        losses_m = monthly[m]["losses"]
        wl_m = wins_m + losses_m
        monthly_rows.append(
            {
                "month": m,
                "n_picks": n,
                "pnl": monthly[m]["pnl"],
                "roi": (monthly[m]["pnl"] / n) if n > 0 else None,
                "winrate": (wins_m / wl_m) if wl_m > 0 else None,
            }
        )

    summary = {
        "strategy": {
            "min_abs_model_edge": min_abs_model_edge,
            "p_cover_real_threshold": p_cover_real_threshold,
            "max_picks_per_day": max_picks_per_day,
            "stake_units": 1.0,
            "exclude_push": True,
        },
        "window": {
            "start_date": start_dt.date().isoformat(),
            "end_date": end_dt.date().isoformat(),
            "step_days": max(1, int(step_days)),
            "train_min_samples": train_min_samples,
        },
        "n_blocks": len(blocks),
        "n_selected_total": len(all_selected),
        "pnl_total": float(sum(pnl_seq)),
        "roi_total": (float(sum(pnl_seq)) / len(all_selected)) if all_selected else None,
        "winrate_total": (wins / wl) if wl > 0 else None,
        "max_drawdown_total": max_dd,
    }

    result = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "blocks": blocks,
        "equity_curve": equity_curve,
        "by_month": monthly_rows,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result
