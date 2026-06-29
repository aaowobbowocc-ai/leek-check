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

from backend.api import quote, health_check, strategy, ai, market, ranking, news, twse, alerts
from backend.lib.ticker_map import load_ticker_map

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — 每步都 try/except 防 startup crash loop
    print("[startup] STAGE 1: loading ticker map ...", flush=True)
    try:
        load_ticker_map()
        print("[startup] STAGE 1 ✓", flush=True)
    except Exception as e:
        print(f"[startup] STAGE 1 FAIL: {e}", flush=True)
        import traceback; traceback.print_exc()

    # ── 啟動 APScheduler:3 固定 slot + 30 分鐘輕量檢查 ──
    print("[startup] STAGE 2: APScheduler ...", flush=True)
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

        # TWSE ETL — 平日 14:30 跑(盤後 30min TWSE 公布完整)
        from backend.jobs.twse_daily_etl import run_etl as run_twse_etl
        from backend.lib import twse_cache as twse_cache_mod
        def _twse_etl_with_cache_clear():
            run_twse_etl()
            twse_cache_mod.clear_cache()
        sched_obj.add_job(
            _twse_etl_with_cache_clear, "cron",
            day_of_week="mon-fri", hour=14, minute=30, id="twse_etl",
        )

        # 價格警示 checker — 平日 9:00-13:30 每 3 分鐘檢查
        from backend.jobs.alert_checker import run_check as run_alert_check
        sched_obj.add_job(
            run_alert_check, "cron",
            day_of_week="mon-fri", hour="9-13", minute="*/3",
            id="alert_checker",
        )

        # ── Bootstrap:若 TWSE cache 空或 > 1 天舊,啟動後 10 秒先跑一次 ──
        from datetime import datetime, timedelta
        status = twse_cache_mod.cache_status()
        need_bootstrap = False
        for label in ("institutional", "per_pbr"):
            info = status.get(label, {})
            if not info.get("exists"):
                need_bootstrap = True
                break
            latest = info.get("latest_date")
            if not latest:
                need_bootstrap = True
                break
            try:
                age_days = (datetime.now().date() - datetime.fromisoformat(latest).date()).days
                if age_days > 1:
                    need_bootstrap = True
                    break
            except Exception:
                pass
        if need_bootstrap:
            sched_obj.add_job(_twse_etl_with_cache_clear, "date",
                              run_date=datetime.now() + timedelta(seconds=10),
                              id="twse_bootstrap")
            print("[startup] TWSE cache 過舊或缺,10 秒後 bootstrap ETL")

        sched_obj.start()
        app.state.scheduler = sched_obj
        print("[startup] STAGE 2 ✓ APScheduler 啟動", flush=True)
    except Exception as e:
        print(f"[startup] STAGE 2 FAIL APScheduler: {e}", flush=True)
        import traceback; traceback.print_exc()

    print("[startup] ✓✓✓ READY — service is live", flush=True)
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
app.include_router(twse.router)  # 已有 /api/twse prefix
app.include_router(alerts.router)  # 已有 /api/alerts prefix
