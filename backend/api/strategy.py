"""策略掃描 — 讀 data/strategy_results.json."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from backend.lib.ticker_map import get_ticker_info

router = APIRouter(tags=["strategy"])

ROOT = Path(__file__).resolve().parents[2]
JSON_PATH = ROOT / "data" / "strategy_results.json"


class StrategyHit(BaseModel):
    ticker: str
    name: str
    industry: str
    metric: float | None = None
    extra: dict = {}


class StrategyResultsOut(BaseModel):
    updated_at: str
    age_hours: float
    fresh: bool
    strategies: dict[str, list[StrategyHit]]


@router.get("/strategy/results", response_model=StrategyResultsOut)
def get_strategy_results():
    if not JSON_PATH.exists():
        return StrategyResultsOut(
            updated_at="", age_hours=999, fresh=False, strategies={}
        )

    raw = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    updated = raw.get("updated_at", "")
    try:
        dt = datetime.fromisoformat(updated)
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600
    except Exception:
        age = 999

    strategies: dict[str, list[StrategyHit]] = {}
    for key, hits in raw.get("results", {}).items():
        out: list[StrategyHit] = []
        for h in hits:
            tk = h.get("tk") or h.get("ticker") or ""
            if not tk:
                continue
            info = get_ticker_info(tk) or {"name": "", "industry": ""}
            metric = (
                h.get("yoy") or h.get("retail_pct") or h.get("z")
                or h.get("score") or h.get("ret_5d")
            )
            extra = {k: v for k, v in h.items() if k not in ("tk", "ticker")}
            out.append(StrategyHit(
                ticker=tk, name=info["name"], industry=info["industry"],
                metric=float(metric) if metric is not None else None,
                extra=extra,
            ))
        strategies[key] = out

    return StrategyResultsOut(
        updated_at=updated,
        age_hours=round(age, 1),
        fresh=age < 24 * 7,
        strategies=strategies,
    )
