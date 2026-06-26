"""TWSE cache 查詢 + 手動 trigger ETL endpoint."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from backend.lib import twse_cache

router = APIRouter(prefix="/api/twse", tags=["twse"])


@router.get("/inst/{ticker}")
def get_inst(ticker: str):
    """個股 20 日法人累計(TWSE 官方)."""
    data = twse_cache.get_inst_20d(ticker)
    if not data:
        return {"ticker": ticker, "found": False}
    return {"ticker": ticker, "found": True, **data}


@router.get("/per/{ticker}")
def get_per(ticker: str):
    """個股最新 PER / PBR / 殖利率(TWSE 官方)."""
    data = twse_cache.get_per_latest(ticker)
    if not data:
        return {"ticker": ticker, "found": False}
    return {"ticker": ticker, "found": True, **data}


@router.get("/cache/status")
def cache_status():
    """TWSE cache 健康狀態."""
    return twse_cache.cache_status()


@router.post("/etl/run-now")
def run_etl_now(bg: BackgroundTasks):
    """手動立刻跑一次 ETL(非同步,30 秒 ~ 1 分鐘)."""
    from backend.jobs.twse_daily_etl import run_etl
    def _job():
        try:
            run_etl()
            twse_cache.clear_cache()
        except Exception as e:
            print(f"[twse etl bg] {e}")
    bg.add_task(_job)
    return {"status": "running_in_background"}
