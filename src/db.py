"""
韭菜健檢 — DB 層(Supabase 或 file fallback)

設計原則:
- 有 SUPABASE_URL + SUPABASE_ANON_KEY → multi-user 模式,寫 Supabase
- 沒有環境變數 → file fallback(本地 dev 用 data/user_data/*.json,單機)
- 公共 helper 函式回傳的資料結構跟舊 load_json/save_json 完全一致,
  call site 改最少
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ── 環境偵測 ──
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()

# 也支援從 Streamlit secrets 拿(雲端模式)
try:
    import streamlit as _st
    if not SUPABASE_URL:
        SUPABASE_URL = (_st.secrets.get("SUPABASE_URL", "") or "").strip()
    if not SUPABASE_ANON_KEY:
        SUPABASE_ANON_KEY = (_st.secrets.get("SUPABASE_ANON_KEY", "") or "").strip()
except Exception:
    pass

USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_ANON_KEY)

# ── File fallback 路徑 ──
USER_DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "user_data"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Supabase client(lazy)──
_client = None


def get_client():
    """回傳 Supabase client(單例)。"""
    global _client
    if not USE_SUPABASE:
        return None
    if _client is None:
        try:
            from supabase import create_client
            _client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        except Exception as e:
            print(f"[db] Supabase init failed: {e}")
            return None
    return _client


def set_session_token(access_token: str | None, refresh_token: str | None = None):
    """登入後把 JWT 注入 client(讓 RLS 認得 user)。"""
    c = get_client()
    if c and access_token:
        try:
            c.auth.set_session(access_token, refresh_token or "")
        except Exception as e:
            print(f"[db] set_session failed: {e}")


# ═══════════════════════════════════════════════
# Watchlist(觀察清單 + 持股)
# ═══════════════════════════════════════════════

def load_watchlist(user_id: str | None = None) -> dict:
    """回傳跟舊 load_json('watchlist') 一樣的 shape: {tickers: [...]}"""
    if USE_SUPABASE and user_id:
        c = get_client()
        try:
            rows = (c.table("watchlists")
                       .select("*")
                       .eq("user_id", user_id)
                       .order("position")
                       .execute()).data or []
            items = []
            for r in rows:
                item = {
                    "ticker": r["ticker"],
                    "type": r.get("ticker_type", "twse"),
                    "note": r.get("note", "") or "",
                }
                if r.get("shares"):
                    item["shares"] = int(r["shares"])
                if r.get("cost_per_share"):
                    item["cost_per_share"] = float(r["cost_per_share"])
                if r.get("entry_date"):
                    item["entry_date"] = str(r["entry_date"])
                items.append(item)
            return {"tickers": items}
        except Exception as e:
            print(f"[db] load_watchlist failed: {e},fallback file")

    # File fallback
    p = USER_DATA_DIR / "watchlist.json"
    if not p.exists():
        return {"tickers": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"tickers": []}


def save_watchlist(data: dict, user_id: str | None = None):
    """寫回 — 全量覆蓋(同步 add/edit/delete/reorder)。"""
    items = data.get("tickers", []) if isinstance(data, dict) else []

    if USE_SUPABASE and user_id:
        c = get_client()
        try:
            # 取現有
            existing = (c.table("watchlists")
                         .select("ticker, ticker_type")
                         .eq("user_id", user_id)
                         .execute()).data or []
            existing_keys = {(r["ticker"], r["ticker_type"]) for r in existing}
            new_keys = set()
            # Upsert 每一檔
            for pos, t in enumerate(items):
                tk_type = t.get("type", "twse")
                key = (t["ticker"], tk_type)
                new_keys.add(key)
                payload = {
                    "user_id": user_id,
                    "ticker": t["ticker"],
                    "ticker_type": tk_type,
                    "note": t.get("note", "") or "",
                    "shares": int(t["shares"]) if t.get("shares") else None,
                    "cost_per_share": float(t["cost_per_share"]) if t.get("cost_per_share") else None,
                    "entry_date": t.get("entry_date") or None,
                    "position": pos,
                }
                c.table("watchlists").upsert(
                    payload,
                    on_conflict="user_id,ticker,ticker_type",
                ).execute()
            # 刪除不在 new_keys 的舊資料
            for tk, tt in existing_keys - new_keys:
                (c.table("watchlists")
                    .delete()
                    .eq("user_id", user_id)
                    .eq("ticker", tk)
                    .eq("ticker_type", tt)
                    .execute())
            return
        except Exception as e:
            print(f"[db] save_watchlist failed: {e},fallback file")

    # File fallback
    p = USER_DATA_DIR / "watchlist.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════
# User settings
# ═══════════════════════════════════════════════

DEFAULT_SETTINGS = {
    "buy_fee_pct": 0.1425,
    "sell_fee_pct": 0.1425,
    "sell_tax_pct": 0.3,
    "fee_rebate_pct": 70.0,
    "default_frame": "mid",
    "default_tone": "casual",
    "hide_amounts": False,
}


def load_settings(user_id: str | None = None) -> dict:
    if USE_SUPABASE and user_id:
        c = get_client()
        try:
            rows = (c.table("user_settings")
                       .select("*")
                       .eq("user_id", user_id)
                       .execute()).data or []
            if rows:
                r = rows[0]
                return {
                    "buy_fee_pct": float(r.get("buy_fee_pct", 0.1425)),
                    "sell_fee_pct": float(r.get("sell_fee_pct", 0.1425)),
                    "sell_tax_pct": float(r.get("sell_tax_pct", 0.3)),
                    "fee_rebate_pct": float(r.get("fee_rebate_pct", 70.0)),
                    "default_frame": r.get("default_frame", "mid"),
                    "default_tone": r.get("default_tone", "casual"),
                    "hide_amounts": bool(r.get("hide_amounts", False)),
                }
            return dict(DEFAULT_SETTINGS)
        except Exception as e:
            print(f"[db] load_settings failed: {e}")

    p = USER_DATA_DIR / "settings.json"
    if p.exists():
        try:
            return {**DEFAULT_SETTINGS, **json.loads(p.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict, user_id: str | None = None):
    if USE_SUPABASE and user_id:
        c = get_client()
        try:
            payload = {"user_id": user_id, **{k: settings[k] for k in DEFAULT_SETTINGS if k in settings}}
            c.table("user_settings").upsert(payload, on_conflict="user_id").execute()
            return
        except Exception as e:
            print(f"[db] save_settings failed: {e}")

    p = USER_DATA_DIR / "settings.json"
    p.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════
# Price alerts
# ═══════════════════════════════════════════════

def load_alerts(user_id: str | None = None) -> list[dict]:
    if USE_SUPABASE and user_id:
        c = get_client()
        try:
            rows = (c.table("price_alerts")
                       .select("*")
                       .eq("user_id", user_id)
                       .is_("triggered_at", None)
                       .execute()).data or []
            return [{
                "ticker": r["ticker"],
                "condition": r["condition"],
                "price": float(r["target_price"]),
                "note": r.get("note", "") or "",
            } for r in rows]
        except Exception as e:
            print(f"[db] load_alerts failed: {e}")

    # File fallback — YAML
    import yaml as _y
    p = Path(__file__).resolve().parents[1] / "config" / "price_alerts.yaml"
    if p.exists():
        try:
            return (_y.safe_load(p.read_text(encoding="utf-8")) or {}).get("rules", [])
        except Exception:
            pass
    return []


def add_alert(ticker: str, condition: str, price: float, user_id: str | None = None,
                note: str = ""):
    if USE_SUPABASE and user_id:
        c = get_client()
        try:
            c.table("price_alerts").insert({
                "user_id": user_id, "ticker": ticker,
                "condition": condition, "target_price": price, "note": note,
            }).execute()
            return
        except Exception as e:
            print(f"[db] add_alert failed: {e}")

    # File fallback
    import yaml as _y
    p = Path(__file__).resolve().parents[1] / "config" / "price_alerts.yaml"
    cfg = (_y.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}) or {}
    rules = cfg.get("rules", [])
    rules.append({"ticker": ticker, "condition": condition, "price": price, "note": note})
    cfg["rules"] = rules
    p.write_text(_y.dump(cfg, allow_unicode=True), encoding="utf-8")


# ═══════════════════════════════════════════════
# Status / debug
# ═══════════════════════════════════════════════

def status() -> str:
    if USE_SUPABASE:
        return f"☁️ Supabase mode → {SUPABASE_URL[:40]}..."
    return "📁 File mode(本機單機,user_data/*.json)"
