import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


DEDUPE_COMPOSITE_FIELDS = [
    "event_id",
    "book",
    "recommended_side",
    "open_spread",
    "open_ts",
]

PROTECTED_FIELDS = {"result", "profit_units", "settled_at"}


def _resolve_import_path(path: str) -> Path:
    """Resolve relative import path from repository root."""
    p = Path(path)
    if p.is_absolute():
        return p
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / p).resolve()


def _build_dedupe_query(doc: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if doc.get("id") is not None:
        return {"id": doc["id"]}, None

    missing = [f for f in DEDUPE_COMPOSITE_FIELDS if doc.get(f) is None]
    if missing:
        return None, f"missing_dedupe_fields:{','.join(missing)}"

    query = {f: doc[f] for f in DEDUPE_COMPOSITE_FIELDS}
    return query, None


def _compute_update(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """
    Build idempotent update doc.
    Rules:
    - Never overwrite non-null with null.
    - Never overwrite result/profit_units/settled_at if destination already has non-null.
    - Fill missing fields when incoming has non-null.
    Returns: (update_doc, conflicts_count)
    """
    update_doc: Dict[str, Any] = {}
    conflicts = 0

    for key, in_val in incoming.items():
        ex_val = existing.get(key)

        if key in PROTECTED_FIELDS and ex_val is not None:
            if in_val is not None and in_val != ex_val:
                conflicts += 1
            continue

        if in_val is None:
            # Never overwrite with null
            continue

        if ex_val is None:
            update_doc[key] = in_val
            continue

        if ex_val != in_val:
            # Conservative: do not overwrite non-null existing values
            conflicts += 1

    return update_doc, conflicts


async def import_predictions_from_ndjson(db, path: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Import predictions from NDJSON into db.predictions with idempotent upsert semantics.
    """
    resolved = _resolve_import_path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"Import file not found: {resolved}")

    inserted = 0
    updated = 0
    skipped = 0
    conflicts = 0
    examples = []
    processed = 0

    with resolved.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            processed += 1

            try:
                doc = json.loads(raw)
            except json.JSONDecodeError:
                skipped += 1
                conflicts += 1
                if len(examples) < 5:
                    examples.append({"line": line_no, "action": "skipped", "reason": "invalid_json"})
                continue

            if not isinstance(doc, dict):
                skipped += 1
                conflicts += 1
                if len(examples) < 5:
                    examples.append({"line": line_no, "action": "skipped", "reason": "not_object"})
                continue

            query, q_error = _build_dedupe_query(doc)
            if q_error:
                skipped += 1
                conflicts += 1
                if len(examples) < 5:
                    examples.append({"line": line_no, "action": "skipped", "reason": q_error})
                continue

            existing = await db.predictions.find_one(query, {"_id": 0})
            if existing is None:
                inserted += 1
                if not dry_run:
                    await db.predictions.insert_one(doc)
                if len(examples) < 5:
                    examples.append({"line": line_no, "action": "inserted", "key": query})
                continue

            update_doc, row_conflicts = _compute_update(existing, doc)
            conflicts += row_conflicts

            if update_doc:
                updated += 1
                if not dry_run:
                    await db.predictions.update_one(query, {"$set": update_doc}, upsert=False)
                if len(examples) < 5:
                    examples.append(
                        {
                            "line": line_no,
                            "action": "updated",
                            "key": query,
                            "fields": sorted(update_doc.keys())[:10],
                        }
                    )
            else:
                skipped += 1
                if len(examples) < 5:
                    examples.append({"line": line_no, "action": "skipped", "key": query, "reason": "no_changes"})

    return {
        "path": str(resolved),
        "dry_run": dry_run,
        "processed": processed,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "conflicts": conflicts,
        "examples": examples[:5],
    }
