#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from pymongo import MongoClient


def _key(doc: Dict) -> Tuple:
    return (
        doc.get("event_id"),
        doc.get("book"),
        doc.get("recommended_side"),
        doc.get("open_spread"),
        doc.get("commence_time"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive cross-user duplicate predictions")
    parser.add_argument("--operational-email", required=True, help="Email of the operational account")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(env_path)

    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = MongoClient(mongo_url)
    db = client[db_name]

    op_user = db.users.find_one(
        {"email": args.operational_email},
        {"_id": 0, "id": 1, "email": 1, "name": 1},
    )
    if not op_user:
        raise SystemExit(f"Operational user not found by email: {args.operational_email}")
    op_user_id = op_user["id"]

    users = {
        u["id"]: u
        for u in db.users.find({}, {"_id": 0, "id": 1, "email": 1, "name": 1})
        if u.get("id")
    }

    docs: List[Dict] = list(
        db.predictions.find(
            {"archived": {"$ne": True}},
            {
                "_id": 0,
                "id": 1,
                "user_id": 1,
                "event_id": 1,
                "book": 1,
                "recommended_side": 1,
                "open_spread": 1,
                "commence_time": 1,
                "result": 1,
                "created_at": 1,
            },
        )
    )

    by_key: Dict[Tuple, List[Dict]] = {}
    for d in docs:
        by_key.setdefault(_key(d), []).append(d)

    to_archive_ids: List[str] = []
    for _, group in by_key.items():
        if len(group) < 2:
            continue
        has_operational = any(g.get("user_id") == op_user_id for g in group)
        if not has_operational:
            continue
        for g in group:
            if g.get("user_id") != op_user_id and g.get("id"):
                to_archive_ids.append(g["id"])

    # Extra safety: archive obvious probe/admin rows to prevent future contamination.
    for d in docs:
        uid = str(d.get("user_id", "")).lower()
        user = users.get(d.get("user_id"), {})
        email = str(user.get("email", "")).lower()
        name = str(user.get("name", "")).lower()
        if uid == "admin" or email.startswith("probe_") or name.startswith("probe"):
            if d.get("id"):
                to_archive_ids.append(d["id"])

    to_archive_ids = sorted(set(to_archive_ids))
    if not args.apply:
        print(
            {
                "mode": "dry_run",
                "operational_user_id": op_user_id,
                "operational_email": args.operational_email,
                "n_predictions_scanned": len(docs),
                "n_to_archive": len(to_archive_ids),
                "sample_ids": to_archive_ids[:20],
            }
        )
        return

    if to_archive_ids:
        result = db.predictions.update_many(
            {"id": {"$in": to_archive_ids}},
            {"$set": {"archived": True, "archived_reason": "cross_user_duplicate_cleanup"}},
        )
        modified = result.modified_count
    else:
        modified = 0

    print(
        {
            "mode": "apply",
            "operational_user_id": op_user_id,
            "operational_email": args.operational_email,
            "n_predictions_scanned": len(docs),
            "n_to_archive": len(to_archive_ids),
            "modified_count": modified,
        }
    )


if __name__ == "__main__":
    main()
