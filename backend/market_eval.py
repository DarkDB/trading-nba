from datetime import datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo
from typing import Any, Dict, List
import httpx

logger = logging.getLogger(__name__)


def _safe_iso_to_dt(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


async def backfill_close_snapshot(
    db,
    days: int = 7,
    force: bool = False,
    debug: bool = False,
    debug_query: bool = False,
    fallback_time_field: str = "open_ts",
    odds_api_key: str = "",
    odds_api_base: str = "",
) -> Dict[str, Any]:
    """
    Backfill close lines for picks from last N days.
    Idempotent by field:
    - never overwrite non-null with null
    - fill missing fields when incoming value is non-null
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    if fallback_time_field not in ("open_ts", "created_at", ""):
        fallback_time_field = "open_ts"

    range_settled = {"$gte": start.isoformat(), "$lte": now.isoformat()}
    base_filters: Dict[str, Any] = {
        "book": "pinnacle",
        "result": {"$in": ["WIN", "LOSS", "PUSH"]},
        "$or": [{"close_spread": None}, {"close_price": None}],
    }
    query_settled = {**base_filters, "settled_at": range_settled}
    query_fallback = None
    picks = await db.predictions.find(query_settled, {"_id": 0}).to_list(10000)

    if fallback_time_field:
        query_fallback = {
            **base_filters,
            "settled_at": None,
            fallback_time_field: {"$gte": start.isoformat(), "$lte": now.isoformat()},
        }
        fallback_picks = await db.predictions.find(query_fallback, {"_id": 0}).to_list(10000)
        seen = {p.get("id") for p in picks}
        for p in fallback_picks:
            if p.get("id") not in seen:
                picks.append(p)
                seen.add(p.get("id"))

    def _pick_in_range(pick: Dict[str, Any]) -> bool:
        settled_raw = pick.get("settled_at")
        dt = _safe_iso_to_dt(settled_raw) if isinstance(settled_raw, str) else settled_raw
        if dt is None and fallback_time_field:
            fb_raw = pick.get(fallback_time_field)
            dt = _safe_iso_to_dt(fb_raw) if isinstance(fb_raw, str) else fb_raw
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return start <= dt <= now

    picks = [p for p in picks if _pick_in_range(p)]

    updated = 0
    skipped = 0
    missing = 0
    examples: List[Dict[str, Any]] = []
    sample_not_found: List[Dict[str, Any]] = []
    sample_raw_responses: List[Dict[str, Any]] = []
    n_api_calls = 0
    n_found_markets = 0
    invalid_timing_skipped = 0
    probed_event_ids = set()
    considered_event_ids = set()

    async def _probe_market_feed(event_id: str):
        nonlocal n_api_calls, n_found_markets
        if not debug or not odds_api_key or not odds_api_base or not event_id:
            return
        if event_id in probed_event_ids:
            return
        if len(sample_raw_responses) >= 2:
            return
        probed_event_ids.add(event_id)
        params = {
            "apiKey": odds_api_key,
            "regions": "eu",
            "markets": "spreads",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                n_api_calls += 1
                response = await client.get(
                    f"{odds_api_base}/sports/basketball_nba/events/{event_id}/odds",
                    params=params,
                )
            raw = response.text or ""
            if len(raw.encode("utf-8")) > 2048:
                raw = raw.encode("utf-8")[:2048].decode("utf-8", errors="ignore")
            has_markets = False
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    bookmakers = payload.get("bookmakers") or []
                    has_markets = len(bookmakers) > 0
            except Exception:
                has_markets = False
            if has_markets:
                n_found_markets += 1
            sample_raw_responses.append(
                {
                    "event_id": event_id,
                    "status_code": response.status_code,
                    "request_params": {
                        "regions": "eu",
                        "markets": "spreads",
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                    },
                    "response_truncated": raw,
                }
            )
        except Exception as e:
            sample_raw_responses.append(
                {
                    "event_id": event_id,
                    "status_code": None,
                    "request_params": {
                        "regions": "eu",
                        "markets": "spreads",
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                    },
                    "response_truncated": f"ERROR: {str(e)[:1900]}",
                }
            )

    for p in picks:
        event_id = p.get("event_id")
        if event_id:
            considered_event_ids.add(event_id)
        line = await db.market_lines.find_one(
            {"event_id": event_id, "bookmaker_key": "pinnacle"},
            {"_id": 0},
        )
        if not line:
            missing += 1
            if debug and len(sample_not_found) < 10:
                sample_not_found.append(
                    {
                        "event_id": event_id,
                        "commence_time": p.get("commence_time"),
                        "book": "pinnacle",
                        "market_type": "spreads",
                        "request_params": {
                            "regions": "eu",
                            "markets": "spreads",
                            "oddsFormat": "decimal",
                            "dateFormat": "iso",
                        },
                    }
                )
            await _probe_market_feed(event_id)
            continue
        n_found_markets += 1

        recommended_side = p.get("recommended_side", "HOME")
        open_spread = p.get("open_spread", 0)
        close_spread = line.get("spread_point_home")
        close_price = (
            line.get("price_home_decimal", 1.91)
            if recommended_side == "HOME"
            else line.get("price_away_decimal", 1.91)
        )
        if recommended_side == "HOME":
            clv_spread = open_spread - close_spread if close_spread is not None else None
        else:
            clv_spread = close_spread - open_spread if close_spread is not None else None

        now_ts = now.isoformat()
        commence_dt = _safe_iso_to_dt(p.get("commence_time", ""))
        if commence_dt is not None and commence_dt.tzinfo is None:
            commence_dt = commence_dt.replace(tzinfo=timezone.utc)
        # Data-quality guardrail:
        # do not persist close values captured at/after start unless force=true.
        if not force and commence_dt is not None and now >= commence_dt:
            await db.predictions.update_one(
                {"id": p["id"]},
                {"$set": {"close_capture_invalid_timing": True}},
            )
            invalid_timing_skipped += 1
            skipped += 1
            continue

        existing_captured = p.get("close_captured_at") or p.get("close_ts")
        update_doc: Dict[str, Any] = {}

        if close_spread is not None and (force or p.get("close_spread") is None):
            update_doc["close_spread"] = close_spread
        if close_price is not None and (force or p.get("close_price") is None):
            update_doc["close_price"] = round(float(close_price), 3)
        if clv_spread is not None and (force or p.get("clv_spread") is None):
            update_doc["clv_spread"] = round(float(clv_spread), 2)
        if force or p.get("close_source") is None:
            update_doc["close_source"] = "pinnacle"
        if force or existing_captured is None:
            update_doc["close_captured_at"] = now_ts

        if update_doc:
            await db.predictions.update_one({"id": p["id"]}, {"$set": update_doc})
            updated += 1
            if len(examples) < 5:
                examples.append({"id": p.get("id"), "action": "updated", "fields": sorted(update_doc.keys())})
        else:
            skipped += 1

    # Warning check:
    # If local time in Europe/Madrid is >= 12:00, warn for previous-day picks still missing close_spread.
    madrid = ZoneInfo("Europe/Madrid")
    now_madrid = now.astimezone(madrid)
    warn_docs = []
    if now_madrid.hour >= 12:
        prev_day = (now_madrid - timedelta(days=1)).date()
        candidates = await db.predictions.find(
            {"close_spread": None},
            {"_id": 0, "id": 1, "commence_time": 1},
        ).to_list(5000)
        for doc in candidates:
            dt = _safe_iso_to_dt(doc.get("commence_time", ""))
            if not dt:
                continue
            if dt.astimezone(madrid).date() == prev_day:
                warn_docs.append(doc)

    warnings = []
    if warn_docs:
        warning_msg = f"CLOSE_SNAPSHOT_MISSING_AFTER_12H: {len(warn_docs)} picks still have close_spread=null"
        warnings.append(warning_msg)
        logger.warning(warning_msg)

    response: Dict[str, Any] = {
        "status": "completed",
        "days": days,
        "force": force,
        "updated": updated,
        "skipped": skipped,
        "invalid_timing_skipped": invalid_timing_skipped,
        "missing_lines": missing,
        "warnings": warnings,
        "examples": examples,
    }
    if debug:
        response.update(
            {
                "n_events_considered": len(considered_event_ids),
                "n_api_calls": n_api_calls,
                "n_found_markets": n_found_markets,
                "n_updates_made": updated,
                "n_not_found": missing,
                "sample_not_found": sample_not_found,
                "sample_raw_responses": sample_raw_responses,
            }
        )
    if debug_query:
        total_predictions = await db.predictions.count_documents({})
        book_pinnacle = await db.predictions.count_documents({"book": "pinnacle"})
        close_spread_null = await db.predictions.count_documents({"close_spread": None})
        settled_in_range = await db.predictions.count_documents(query_settled)
        eligible_final = len(picks)
        sample_close_spread_null = await db.predictions.find(
            {"close_spread": None},
            {
                "_id": 0,
                "id": 1,
                "settled_at": 1,
                "created_at": 1,
                "commence_time": 1,
                "close_spread": 1,
                "close_captured_at": 1,
                "book": 1,
                "result": 1,
            },
        ).sort("created_at", -1).to_list(10)

        response.update(
            {
                "query_final": query_settled if not query_fallback else {"settled_query": query_settled, "fallback_query": query_fallback},
                "counts": {
                    "total_predictions": total_predictions,
                    "book_pinnacle": book_pinnacle,
                    "close_spread_null": close_spread_null,
                    "settled_in_range": settled_in_range,
                    "eligible_final": eligible_final,
                    "range_field_used": "settled_at" if not fallback_time_field else f"settled_at+{fallback_time_field}",
                },
                "sample_ids": sample_close_spread_null,
            }
        )
    return response
