import json
from math import erf, sqrt
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional


SETTLED_RESULTS = {"WIN", "LOSS", "PUSH"}
TIERS = ("A", "B", "C")


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _pnl_recalc(result: str, open_price_decimal: float) -> float:
    if result == "WIN":
        return open_price_decimal - 1.0
    if result == "LOSS":
        return -1.0
    if result == "PUSH":
        return 0.0
    return 0.0


def _pcover_recalc(side: str, alpha: float, beta: float, model_edge: float, sigma: float) -> Optional[float]:
    if sigma is None or sigma == 0:
        return None
    mu = alpha + beta * model_edge
    z = mu / sigma
    if side == "HOME":
        return _normal_cdf(z)
    if side == "AWAY":
        return _normal_cdf(-z)
    return None


def _ev_recalc(p_cover: float, open_price_decimal: float) -> float:
    # EV = p*(odds-1) - (1-p)
    return p_cover * (open_price_decimal - 1.0) - (1.0 - p_cover)


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _resolve_out_path(out_path: str) -> Path:
    p = Path(out_path)
    if p.is_absolute():
        return p
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / p).resolve()


def _tier_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}

    wins = sum(1 for r in rows if r.get("result") == "WIN")
    losses = sum(1 for r in rows if r.get("result") == "LOSS")
    pushes = sum(1 for r in rows if r.get("result") == "PUSH")
    n = len(rows)

    pnl_vals = [r["pnl_recalc"] for r in rows]
    ev_saved_vals = [r["ev_saved"] for r in rows if r["ev_saved"] is not None]
    ev_recalc_vals = [r["ev_recalc"] for r in rows if r["ev_recalc"] is not None]
    pc_saved_vals = [r["p_cover_saved"] for r in rows if r["p_cover_saved"] is not None]
    pc_recalc_vals = [r["p_cover_recalc"] for r in rows if r["p_cover_recalc"] is not None]

    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "winrate": (wins / (wins + losses)) if (wins + losses) > 0 else None,
        "roi_recalc": (sum(pnl_vals) / n) if n > 0 else None,
        "avg_ev_saved": mean(ev_saved_vals) if ev_saved_vals else None,
        "avg_ev_recalc": mean(ev_recalc_vals) if ev_recalc_vals else None,
        "avg_p_cover_saved": mean(pc_saved_vals) if pc_saved_vals else None,
        "avg_p_cover_recalc": mean(pc_recalc_vals) if pc_recalc_vals else None,
    }


def run_forensic_report(db, out_path: str = "backend/data/forensic_report.json") -> Dict[str, Any]:
    """
    Recalculate forensic trading metrics from settled picks (WIN/LOSS/PUSH).
    Assumes open_price is DECIMAL.
    """
    settled = list(
        db.predictions.find(
            {"result": {"$in": list(SETTLED_RESULTS)}},
            {"_id": 0},
        )
    )

    rows: List[Dict[str, Any]] = []
    for p in settled:
        open_price = _to_float(p.get("open_price"))
        if open_price is None or open_price <= 1.0:
            # Keep row for discrepancy visibility but skip financial recompute precision.
            continue

        result = p.get("result")
        side = p.get("recommended_side")
        alpha = _to_float(p.get("alpha_used"))
        beta = _to_float(p.get("beta_used"))
        model_edge = _to_float(p.get("model_edge"))
        sigma = _to_float(p.get("sigma_used"))

        p_cover_recalc = None
        ev_recalc = None
        if None not in (alpha, beta, model_edge, sigma) and sigma != 0 and side in ("HOME", "AWAY"):
            p_cover_recalc = _pcover_recalc(side, alpha, beta, model_edge, sigma)
            if p_cover_recalc is not None:
                ev_recalc = _ev_recalc(p_cover_recalc, open_price)

        pnl_recalc = _pnl_recalc(result, open_price)

        ev_saved = _to_float(p.get("ev"))
        p_cover_saved = _to_float(p.get("p_cover"))
        profit_units_saved = _to_float(p.get("profit_units"))

        row = {
            "id": p.get("id"),
            "event_id": p.get("event_id"),
            "tier": p.get("tier"),
            "result": result,
            "recommended_side": side,
            "open_price": open_price,
            "model_edge": model_edge,
            "alpha_used": alpha,
            "beta_used": beta,
            "sigma_used": sigma,
            "p_cover_saved": p_cover_saved,
            "p_cover_recalc": p_cover_recalc,
            "ev_saved": ev_saved,
            "ev_recalc": ev_recalc,
            "profit_units_saved": profit_units_saved,
            "pnl_recalc": pnl_recalc,
        }

        # diffs
        row["diff_ev_abs"] = abs(ev_saved - ev_recalc) if ev_saved is not None and ev_recalc is not None else None
        row["diff_p_cover_abs"] = (
            abs(p_cover_saved - p_cover_recalc)
            if p_cover_saved is not None and p_cover_recalc is not None
            else None
        )
        row["diff_profit_abs"] = (
            abs(profit_units_saved - pnl_recalc)
            if profit_units_saved is not None
            else None
        )
        row["max_diff_abs"] = max(
            [d for d in [row["diff_ev_abs"], row["diff_p_cover_abs"], row["diff_profit_abs"]] if d is not None] or [0.0]
        )
        rows.append(row)

    n = len(rows)
    wins = sum(1 for r in rows if r["result"] == "WIN")
    losses = sum(1 for r in rows if r["result"] == "LOSS")
    pushes = sum(1 for r in rows if r["result"] == "PUSH")

    pnl_vals = [r["pnl_recalc"] for r in rows]
    ev_saved_vals = [r["ev_saved"] for r in rows if r["ev_saved"] is not None]
    ev_recalc_vals = [r["ev_recalc"] for r in rows if r["ev_recalc"] is not None]
    pc_saved_vals = [r["p_cover_saved"] for r in rows if r["p_cover_saved"] is not None]
    pc_recalc_vals = [r["p_cover_recalc"] for r in rows if r["p_cover_recalc"] is not None]

    summary = {
        "n_picks_settled": n,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "winrate": (wins / (wins + losses)) if (wins + losses) > 0 else None,
        "roi_total_recalc": (sum(pnl_vals) / n) if n > 0 else None,
        "avg_ev_saved": mean(ev_saved_vals) if ev_saved_vals else None,
        "avg_ev_recalc": mean(ev_recalc_vals) if ev_recalc_vals else None,
        "avg_p_cover_saved": mean(pc_saved_vals) if pc_saved_vals else None,
        "avg_p_cover_recalc": mean(pc_recalc_vals) if pc_recalc_vals else None,
    }

    by_tier = {}
    for tier in TIERS:
        by_tier[tier] = _tier_summary([r for r in rows if r.get("tier") == tier])

    discrepancies = sorted(rows, key=lambda r: r["max_diff_abs"], reverse=True)[:20]

    report = {
        "summary": summary,
        "by_tier": by_tier,
        "discrepancies_top20": discrepancies,
    }

    out = _resolve_out_path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    return report
