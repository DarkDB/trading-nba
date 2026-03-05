import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.calibration_outcome import get_active_outcome_calibration, predict_p_cover_outcome


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _iso_to_dt(v: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        return None


def _madrid_day_key(doc: Dict[str, Any]) -> Optional[str]:
    madrid = ZoneInfo("Europe/Madrid")
    for field in ("commence_time", "created_at", "settled_at"):
        raw = doc.get(field)
        if not raw:
            continue
        dt = _iso_to_dt(raw)
        if dt is None:
            continue
        return dt.astimezone(madrid).strftime("%Y-%m-%d")
    return None


def _pick_score(doc: Dict[str, Any]) -> float:
    p = _to_float(doc.get("p_cover_real")) or 0.0
    edge = abs(_to_float(doc.get("model_edge")) or 0.0)
    return p * 10.0 + edge


def _max_drawdown_units(pnl_seq: List[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnl_seq:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _compute_metrics(picks: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not picks:
        return {
            "n_picks": 0,
            "winrate": None,
            "avg_odds": None,
            "roi": None,
            "pnl": 0.0,
            "max_drawdown": 0.0,
            "avg_clv_spread": None,
            "avg_p_cover_real": None,
        }

    wins = sum(1 for p in picks if p.get("result") == "WIN")
    losses = sum(1 for p in picks if p.get("result") == "LOSS")
    resolved = wins + losses
    winrate = (wins / resolved) if resolved > 0 else None

    odds_vals = [_to_float(p.get("open_price")) for p in picks]
    odds_vals = [o for o in odds_vals if o is not None]
    avg_odds = mean(odds_vals) if odds_vals else None

    pnl_seq = [_to_float(p.get("sim_pnl")) or 0.0 for p in picks]
    pnl = sum(pnl_seq)
    roi = (pnl / len(picks)) if picks else None
    max_dd = _max_drawdown_units(pnl_seq)

    clv_vals = [_to_float(p.get("clv_spread")) for p in picks]
    clv_vals = [c for c in clv_vals if c is not None]
    avg_clv = mean(clv_vals) if clv_vals else None

    pcr = [_to_float(p.get("p_cover_real")) for p in picks]
    pcr = [v for v in pcr if v is not None]
    avg_pcr = mean(pcr) if pcr else None

    return {
        "n_picks": len(picks),
        "winrate": winrate,
        "avg_odds": avg_odds,
        "roi": roi,
        "pnl": pnl,
        "max_drawdown": max_dd,
        "avg_clv_spread": avg_clv,
        "avg_p_cover_real": avg_pcr,
    }


def _simulate_config(
    settled: List[Dict[str, Any]],
    min_abs_model_edge: float,
    p_cover_real_threshold: float,
    max_picks_per_day: int,
    max_abs_open_spread: Optional[float],
    min_open_price: Optional[float],
) -> Dict[str, Any]:
    # Step 1: hard filters.
    filtered = []
    for p in settled:
        edge = abs(_to_float(p.get("model_edge")) or 0.0)
        pcr = _to_float(p.get("p_cover_real"))
        open_price = _to_float(p.get("open_price"))
        open_spread = _to_float(p.get("open_spread"))
        if edge < min_abs_model_edge:
            continue
        if pcr is None or pcr < p_cover_real_threshold:
            continue
        if min_open_price is not None and (open_price is None or open_price < min_open_price):
            continue
        if max_abs_open_spread is not None and (open_spread is None or abs(open_spread) > max_abs_open_spread):
            continue
        filtered.append(p)

    # Step 2: per-day cap by best score.
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for p in filtered:
        day = _madrid_day_key(p) or "unknown"
        by_day.setdefault(day, []).append(p)

    selected = []
    for day in sorted(by_day.keys()):
        ranked = sorted(by_day[day], key=_pick_score, reverse=True)
        selected.extend(ranked[:max_picks_per_day])

    selected = sorted(selected, key=lambda x: x.get("settled_at") or "")
    metrics = _compute_metrics(selected)
    return {
        "config": {
            "min_abs_model_edge": min_abs_model_edge,
            "p_cover_real_threshold": p_cover_real_threshold,
            "max_picks_per_day": max_picks_per_day,
            "max_abs_open_spread": max_abs_open_spread,
            "min_open_price": min_open_price,
        },
        **metrics,
    }


async def run_selection_sweep(
    db,
    out_path: str = "backend/data/selection_sweep.json",
    include_push_as_zero: bool = False,
) -> Dict[str, Any]:
    docs = await db.predictions.find({"result": {"$in": ["WIN", "LOSS", "PUSH"]}}, {"_id": 0}).to_list(50000)
    outcome_calibration = await get_active_outcome_calibration(db)

    settled = []
    for p in docs:
        result = p.get("result")
        if result == "PUSH" and not include_push_as_zero:
            continue

        open_price = _to_float(p.get("open_price"))
        if open_price is None or open_price <= 1.0:
            continue

        model_edge = _to_float(p.get("model_edge"))
        open_spread = _to_float(p.get("open_spread"))
        pcr = _to_float(p.get("p_cover_real"))
        if pcr is None and outcome_calibration and model_edge is not None and open_spread is not None:
            pcr = predict_p_cover_outcome(
                model_edge=model_edge,
                open_price=open_price,
                open_spread=open_spread,
                calibration_doc=outcome_calibration,
            )

        if result == "WIN":
            sim_pnl = open_price - 1.0
        elif result == "LOSS":
            sim_pnl = -1.0
        else:
            sim_pnl = 0.0

        row = dict(p)
        row["p_cover_real"] = pcr
        row["sim_pnl"] = sim_pnl
        settled.append(row)

    baseline = _compute_metrics(sorted(settled, key=lambda x: x.get("settled_at") or ""))
    baseline_row = {
        "config": {
            "name": "baseline_no_filters",
            "min_abs_model_edge": None,
            "p_cover_real_threshold": None,
            "max_picks_per_day": None,
            "max_abs_open_spread": None,
            "min_open_price": None,
        },
        **baseline,
    }

    min_abs_model_edge_vals = [1.5, 2.0, 2.5, 3.0]
    p_cover_real_threshold_vals = [0.50, 0.52, 0.54, 0.56]
    max_picks_per_day_vals = [1, 2, 3]
    max_abs_open_spread_vals = [None, 6.5, 8.5, 10.5]
    min_open_price_vals = [None, 1.80, 1.85, 1.90]

    rows: List[Dict[str, Any]] = []
    for edge_thr in min_abs_model_edge_vals:
        for p_thr in p_cover_real_threshold_vals:
            for per_day in max_picks_per_day_vals:
                for spread_lim in max_abs_open_spread_vals:
                    for odds_min in min_open_price_vals:
                        rows.append(
                            _simulate_config(
                                settled=settled,
                                min_abs_model_edge=edge_thr,
                                p_cover_real_threshold=p_thr,
                                max_picks_per_day=per_day,
                                max_abs_open_spread=spread_lim,
                                min_open_price=odds_min,
                            )
                        )

    eligible = [r for r in rows if (r.get("n_picks") or 0) >= 30 and r.get("roi") is not None]
    eligible_sorted = sorted(
        eligible,
        key=lambda r: (r["roi"], (r["n_picks"] or 0), -(r["max_drawdown"] or 0.0)),
        reverse=True,
    )

    all_ranked = sorted(
        rows,
        key=lambda r: (
            (r.get("roi") if r.get("roi") is not None else -9999.0),
            (r.get("n_picks") or 0),
            -(r.get("max_drawdown") or 0.0),
        ),
        reverse=True,
    )

    result = {
        "status": "completed",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "include_push_as_zero": include_push_as_zero,
        "dataset": {
            "n_settled_total_raw": len(docs),
            "n_settled_used": len(settled),
        },
        "baseline": baseline_row,
        "grid": {
            "min_abs_model_edge": min_abs_model_edge_vals,
            "p_cover_real_threshold": p_cover_real_threshold_vals,
            "max_picks_per_day": max_picks_per_day_vals,
            "max_abs_open_spread": max_abs_open_spread_vals,
            "min_open_price": min_open_price_vals,
        },
        "n_configs_total": len(rows),
        "n_configs_eligible_min_30": len(eligible_sorted),
        "top_20": eligible_sorted[:20],
        "top_200": all_ranked[:200],
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result
