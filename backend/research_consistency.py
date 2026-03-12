from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _to_dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and v:
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


async def compare_research_vs_picks(
    db,
    open_ts_tolerance_minutes: int = 180,
    limit: int = 500,
) -> Dict[str, Any]:
    tolerance_seconds = max(1, int(open_ts_tolerance_minutes)) * 60

    picks = await db.predictions.find({"archived": {"$ne": True}}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    research = await db.model_predictions_all.find({}, {"_id": 0}).to_list(200000)

    by_event: Dict[str, List[Dict[str, Any]]] = {}
    for r in research:
        eid = r.get("event_id")
        if eid:
            by_event.setdefault(eid, []).append(r)

    n_matched = 0
    n_same_open_spread = 0
    n_same_pred_margin = 0
    n_same_p_cover = 0
    n_same_p_cover_real = 0
    n_expected_snapshot_drift = 0
    mismatches: List[Dict[str, Any]] = []

    for p in picks:
        eid = p.get("event_id")
        candidates = by_event.get(eid) or []
        if not candidates:
            continue
        n_matched += 1

        p_open = _to_dt(p.get("open_ts"))
        best = None
        best_diff = None
        for r in candidates:
            r_open = _to_dt(r.get("open_ts"))
            if p_open is None or r_open is None:
                diff = 10**9
            else:
                diff = abs((p_open - r_open).total_seconds())
            if best is None or diff < best_diff:
                best = r
                best_diff = diff

        if best is None:
            continue
        if best_diff is None:
            best_diff = 10**9

        if best_diff > tolerance_seconds:
            n_expected_snapshot_drift += 1
            continue

        po, ro = _to_float(p.get("open_spread")), _to_float(best.get("open_spread"))
        pp, rp = _to_float(p.get("pred_margin")), _to_float(best.get("pred_margin"))
        pc, rc = _to_float(p.get("p_cover")), _to_float(best.get("p_cover"))
        pr, rr = _to_float(p.get("p_cover_real")), _to_float(best.get("p_cover_real"))

        same_open = po is not None and ro is not None and abs(po - ro) < 1e-9
        same_pm = pp is not None and rp is not None and abs(pp - rp) < 1e-6
        same_pc = pc is not None and rc is not None and abs(pc - rc) < 1e-6
        same_pr = (pr is None and rr is None) or (
            pr is not None and rr is not None and abs(pr - rr) < 1e-6
        )

        n_same_open_spread += 1 if same_open else 0
        n_same_pred_margin += 1 if same_pm else 0
        n_same_p_cover += 1 if same_pc else 0
        n_same_p_cover_real += 1 if same_pr else 0

        if not (same_open and same_pm and same_pc and same_pr):
            if len(mismatches) < 20:
                mismatches.append(
                    {
                        "event_id": eid,
                        "prediction_id": p.get("id"),
                        "research_id": best.get("id"),
                        "open_ts_diff_seconds": best_diff,
                        "snapshot_source_prediction": p.get("snapshot_source"),
                        "snapshot_source_research": best.get("snapshot_source"),
                        "open_spread_prediction": po,
                        "open_spread_research": ro,
                        "pred_margin_prediction": pp,
                        "pred_margin_research": rp,
                        "p_cover_prediction": pc,
                        "p_cover_research": rc,
                        "p_cover_real_prediction": pr,
                        "p_cover_real_research": rr,
                    }
                )

    return {
        "status": "completed",
        "open_ts_tolerance_minutes": open_ts_tolerance_minutes,
        "n_matched": n_matched,
        "n_expected_snapshot_drift": n_expected_snapshot_drift,
        "n_same_open_spread": n_same_open_spread,
        "n_same_pred_margin": n_same_pred_margin,
        "n_same_p_cover": n_same_p_cover,
        "n_same_p_cover_real": n_same_p_cover_real,
        "n_mismatches": len(mismatches),
        "sample_mismatches": mismatches,
    }
