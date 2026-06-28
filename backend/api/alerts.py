"""價格警示 endpoint — 紀錄到 Supabase,backend 用 service_role key 讀寫.

User 透過 frontend Supabase RLS 自己管理(直接 supabase-js),
backend 這個 endpoint 只給「checker job」+ 健康度 query 用.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# 讀 .streamlit/secrets.toml 拿 Supabase URL + service key
SECRETS = Path(__file__).resolve().parents[2] / ".streamlit" / "secrets.toml"
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
if not SUPABASE_URL and SECRETS.exists():
    for line in SECRETS.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("SUPABASE_URL") and "=" in s:
            SUPABASE_URL = s.split("=", 1)[1].strip().strip('"').strip("'")
        elif s.startswith("SUPABASE_SERVICE_KEY") and "=" in s:
            SUPABASE_SERVICE_KEY = s.split("=", 1)[1].strip().strip('"').strip("'")


def _supabase_client():
    """lazy import + 用 service_role key bypass RLS(只給 backend 自己用)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as e:
        print(f"[alerts] supabase init: {e}")
        return None


class AlertOut(BaseModel):
    id: int
    user_id: str
    ticker: str
    condition: str           # 'above' / 'below'
    target_price: float
    note: str = ""
    triggered_at: str | None = None
    triggered_price: float | None = None
    is_read: bool = False
    created_at: str


@router.get("/active-count")
def active_count():
    """目前 active(未觸發)的 alert 總數 — 用於 cron 健康度."""
    sb = _supabase_client()
    if not sb:
        return {"count": 0, "error": "supabase 未設定"}
    try:
        r = sb.table("price_alerts").select("id", count="exact").is_("triggered_at", "null").execute()
        return {"count": r.count or 0}
    except Exception as e:
        return {"count": 0, "error": str(e)}


@router.post("/check-now")
def trigger_check_now():
    """手動立刻跑一次 alert checker — 給除錯 / 強制檢查用."""
    from backend.jobs.alert_checker import run_check
    triggered = run_check()
    return {"triggered": triggered}
