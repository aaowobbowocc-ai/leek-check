"""韭菜健檢 FastAPI 後端 — v2

部署: Render / Railway
本機: uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import quote, health_check, strategy, ai, market, ranking, news
from backend.lib.ticker_map import load_ticker_map

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("[startup] loading ticker map...")
    load_ticker_map()

    # ── 啟動 APScheduler:3 固定 slot + 30 分鐘輕量檢查 ──
    sched_obj = None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from backend.jobs.daily_ai_cache import main as run_daily_cache
        from backend.jobs.news_watcher import check_and_maybe_regen

        sched_obj = BackgroundScheduler(timezone="Asia/Taipei")
        sched_obj.add_job(run_daily_cache, "cron", hour=7, minute=30, id="cache_morning")
        sched_obj.add_job(run_daily_cache, "cron", hour=14, minute=0, id="cache_noon")
        sched_obj.add_job(run_daily_cache, "cron", hour=20, minute=30, id="cache_evening")
        sched_obj.add_job(check_and_maybe_regen, "interval", minutes=30, id="news_watcher")
        sched_obj.start()
        app.state.scheduler = sched_obj
        print("[startup] APScheduler 啟動 — 3 fixed slots + 每 30 min news watcher")
    except Exception as e:
        print(f"[startup] APScheduler failed: {e}")

    print("[startup] ready")
    yield

    if sched_obj:
        sched_obj.shutdown(wait=False)
        print("[shutdown] scheduler stopped")


app = FastAPI(
    title="韭菜健檢 API",
    description="買進前先做一次健檢 — backend v2",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS — 允許 Vercel preview + 本機 dev + Capacitor
allowed = [
    "http://localhost:3000",
    "http://localhost:3001",
    "https://leek-check-v2.vercel.app",
    "https://*.vercel.app",
    "capacitor://localhost",
    "https://localhost",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"service": "韭菜健檢 API", "version": "0.2.0", "status": "ok"}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# Routers
app.include_router(quote.router, prefix="/api")
app.include_router(health_check.router, prefix="/api")
app.include_router(strategy.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(ranking.router, prefix="/api")
app.include_router(news.router, prefix="/api")
