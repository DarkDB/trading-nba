from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional

from backend.strategy_engine import (
    DEFAULT_STRATEGY_CONFIG,
    build_strategy_performance_metrics,
    normalize_strategy_config,
    select_operational_picks,
)


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _to_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _pnl(doc: Dict[str, Any]) -> Optional[float]:
    result = doc.get("result")
    if result == "WIN":
        price = _to_float(doc.get("open_price"))
        return None if price is None else price - 1.0
    if result == "LOSS":
        return -1.0
    if result == "PUSH":
        return 0.0
    return None


def _summarize(selected: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnl_values = [_pnl(p) for p in selected if _pnl(p) is not None]
    non_push = [p for p in selected if p.get("result") in ("WIN", "LOSS")]
    wins = sum(1 for p in non_push if p.get("result") == "WIN")
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for val in pnl_values:
        equity += val
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    clv_valid = []
    for p in selected:
        clv = _to_float(p.get("clv_spread"))
        close_dt = _to_dt(p.get("close_captured_at")) or _to_dt(p.get("close_ts"))
        commence_dt = _to_dt(p.get("commence_time"))
        if clv is not None and not (close_dt and commence_dt and close_dt >= commence_dt):
            clv_valid.append(clv)
    return {
        "n_picks": len(pnl_values),
        "pnl": sum(pnl_values) if pnl_values else 0.0,
        "roi": (sum(pnl_values) / len(pnl_values)) if pnl_values else None,
        "winrate": (wins / len(non_push)) if non_push else None,
        "max_drawdown": max_dd,
        "market_beating_rate": (sum(1 for x in clv_valid if x > 0) / len(clv_valid)) if clv_valid else None,
    }


async def run_strategy_backtest(
    db,
    out_path: str = "backend/data/strategy_backtest.json",
    strategy_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = normalize_strategy_config(strategy_config or DEFAULT_STRATEGY_CONFIG)
    settled = await db.predictions.find(
        {"result": {"$in": ["WIN", "LOSS", "PUSH"]}, "archived": {"$ne": True}},
        {"_id": 0},
    ).to_list(50000)
    settled = sorted(
        settled,
        key=lambda d: _to_dt(d.get("created_at")) or _to_dt(d.get("settled_at")) or datetime.min,
    )

    by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for pick in settled:
        dt = _to_dt(pick.get("created_at")) or _to_dt(pick.get("settled_at"))
        if not dt:
            continue
        by_day[dt.date().isoformat()].append(dict(pick))

    selected_history: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    mode_counts = {"normal": 0, "conservative": 0}

    for day in sorted(by_day.keys()):
        performance_metrics = build_strategy_performance_metrics(selected_history)
        result = select_operational_picks(by_day[day], cfg, performance_metrics)
        chosen = [dict(p) for p in result["selected_picks"]]
        selected_history.extend(chosen)
        mode_counts[result["strategy_mode"]] = mode_counts.get(result["strategy_mode"], 0) + 1
        block_metrics = _summarize(chosen)
        blocks.append(
            {
                "day": day,
                "strategy_mode": result["strategy_mode"],
                "active_strategy_thresholds": result["active_strategy_thresholds"],
                "dynamic_guardrails_triggered": result["dynamic_guardrails_triggered"],
                "metrics": block_metrics,
            }
        )

    summary = _summarize(selected_history)
    report = {
        "status": "completed",
        "strategy_profile": cfg["strategy_profile"],
        "summary": summary,
        "mode_counts": mode_counts,
        "blocks": blocks,
    }
    Path(out_path).write_text(json.dumps(report, indent=2, default=str))
    return report
