from typing import Any, Dict, List


def _expected_clv_spread(open_spread: Any, close_spread: Any, recommended_side: str) -> float:
    o = float(open_spread)
    c = float(close_spread)
    if (recommended_side or "HOME").upper() == "HOME":
        return round(o - c, 2)
    return round(c - o, 2)


async def recompute_clv_audit(db, limit: int = 100) -> Dict[str, Any]:
    limit = max(1, int(limit))
    picks = await db.predictions.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)

    mismatches: List[Dict[str, Any]] = []
    for p in picks:
        open_spread = p.get("open_spread")
        close_spread = p.get("close_spread")
        saved = p.get("clv_spread")
        side = p.get("recommended_side", "HOME")
        if open_spread is None or close_spread is None or saved is None:
            continue

        expected = _expected_clv_spread(open_spread, close_spread, side)
        saved_rounded = round(float(saved), 2)
        if expected == saved_rounded:
            continue

        if abs(expected - saved_rounded) <= 0.01:
            classification = "rounding_only"
        elif abs(expected + saved_rounded) <= 0.01:
            classification = "sign_bug"
        elif (side or "").upper() not in ("HOME", "AWAY"):
            classification = "side_mapping_bug"
        else:
            classification = "null_handling_bug"

        mismatches.append(
            {
                "id": p.get("id"),
                "event_id": p.get("event_id"),
                "recommended_side": side,
                "open_spread": open_spread,
                "close_spread": close_spread,
                "clv_spread_saved": saved,
                "clv_spread_expected": expected,
                "classification": classification,
            }
        )

    n = len(picks)
    return {
        "n_checked": n,
        "mismatch_count": len(mismatches),
        "mismatch_rate": (len(mismatches) / n) if n else 0.0,
        "classification_counts": {
            "rounding_only": sum(1 for m in mismatches if m["classification"] == "rounding_only"),
            "sign_bug": sum(1 for m in mismatches if m["classification"] == "sign_bug"),
            "side_mapping_bug": sum(1 for m in mismatches if m["classification"] == "side_mapping_bug"),
            "null_handling_bug": sum(1 for m in mismatches if m["classification"] == "null_handling_bug"),
        },
        "top_mismatches": mismatches[:20],
    }
