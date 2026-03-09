import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


def _safe_iso_to_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


async def grade_all_predictions(db, days_back: int = 7) -> Dict[str, Any]:
    from backend import server as s

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(1, int(days_back)))
    start_iso = start.isoformat()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{s.ODDS_API_BASE}/sports/basketball_nba/scores",
            params={
                "apiKey": s.ODDS_API_KEY,
                "daysFrom": max(1, int(days_back)),
                "dateFormat": "iso",
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            return {"status": "error", "error": f"scores_api_status_{resp.status_code}"}
        games = resp.json()

    completed_games = {}
    for g in games:
        if not g.get("completed") or not g.get("scores"):
            continue
        home_team = g.get("home_team")
        away_team = g.get("away_team")
        home_score = next((int(x["score"]) for x in g["scores"] if x.get("name") == home_team), None)
        away_score = next((int(x["score"]) for x in g["scores"] if x.get("name") == away_team), None)
        if home_score is None or away_score is None:
            continue
        completed_games[g["id"]] = {"home_score": home_score, "away_score": away_score}

    query = {
        "result": None,
        "commence_time": {"$gte": start_iso},
        "commence_time_dt": {"$lte": now},
    }
    pending = await db.model_predictions_all.find(query, {"_id": 0}).to_list(10000)

    results = {
        "status": "completed",
        "processed": len(pending),
        "graded": 0,
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "not_found": 0,
        "skipped_already_graded": 0,
        "sample": [],
    }

    settled_at = now
    for doc in pending:
        event_id = doc.get("event_id")
        game = completed_games.get(event_id)
        if not game:
            results["not_found"] += 1
            continue

        margin_final = game["home_score"] - game["away_score"]
        open_spread = float(doc.get("open_spread", 0))
        recommended_side = doc.get("recommended_side", "HOME")
        spread_adjusted_margin = margin_final + open_spread

        if recommended_side == "HOME":
            if spread_adjusted_margin > 0:
                result = "WIN"
            elif spread_adjusted_margin < 0:
                result = "LOSS"
            else:
                result = "PUSH"
        else:
            if spread_adjusted_margin < 0:
                result = "WIN"
            elif spread_adjusted_margin > 0:
                result = "LOSS"
            else:
                result = "PUSH"

        open_price = float(doc.get("open_price", 1.91))
        if result == "WIN":
            profit_units = round(open_price - 1.0, 4)
            covered = True
            results["wins"] += 1
        elif result == "LOSS":
            profit_units = -1.0
            covered = False
            results["losses"] += 1
        else:
            profit_units = 0.0
            covered = None
            results["pushes"] += 1

        update_doc = {
            "result": result,
            "final_home_score": game["home_score"],
            "final_away_score": game["away_score"],
            "margin_final": margin_final,
            "covered": covered,
            "profit_units": profit_units,
            "settled_at": settled_at,
        }

        write = await db.model_predictions_all.update_one(
            {"id": doc["id"], "result": None},
            {"$set": update_doc},
        )
        if getattr(write, "modified_count", 0) == 0:
            results["skipped_already_graded"] += 1
            continue

        results["graded"] += 1
        if len(results["sample"]) < 10:
            results["sample"].append(
                {
                    "id": doc.get("id"),
                    "event_id": event_id,
                    "result": result,
                    "profit_units": profit_units,
                }
            )

    logger.info(
        "RESEARCH_GRADE_ALL processed=%s graded=%s not_found=%s",
        results["processed"],
        results["graded"],
        results["not_found"],
    )
    return results
