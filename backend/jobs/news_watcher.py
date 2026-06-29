"""自動偵測大新聞 — 每 30 分鐘輕量檢查,有大事才 trigger AI cache 重生.

觸發條件(任一即重生):
1. 加權盤中 |變動| > 2.5%(平常 < 1%)
2. VIX 從 cache 時點上升 > 20%
3. 新聞 headline 含「黑天鵝級」關鍵字
4. cache > 6 小時老(避免長時間沒更新)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "ai_cache" / "daily"
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
import os as _os
BACKEND = _os.getenv("BACKEND_INTERNAL_URL", f"http://localhost:{_os.getenv('PORT', '8000')}")
TPE = ZoneInfo("Asia/Taipei")

# 黑天鵝關鍵字 — 出現就 trigger
CRISIS_KEYWORDS = [
    "崩盤", "暴跌", "閃崩", "黑天鵝", "系統性風險",
    "戰爭", "宣戰", "停火",
    "降息", "升息", "鷹派", "鴿派",
    "倒閉", "破產", "違約",
    "禁令", "制裁",
    "緊急", "警急", "急彈", "崩跌",
]


def _log(msg: str):
    """輸出到 console + log file(Windows console 可能 ASCII only)."""
    now = datetime.now(TPE).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # Windows cp950 console encoding,把無法表示的 char 替換掉
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    fp = LOG_DIR / "news_watcher.log"
    with fp.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _current_slot() -> str:
    now = datetime.now(TPE)
    h, m = now.hour, now.minute
    if h < 7 or (h == 7 and m < 30):
        import datetime as _dt
        yest = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        return f"{yest}_evening"
    if h < 14:
        return f"{now.strftime('%Y-%m-%d')}_morning"
    if h < 20 or (h == 20 and m < 30):
        return f"{now.strftime('%Y-%m-%d')}_noon"
    return f"{now.strftime('%Y-%m-%d')}_evening"


def _load_cache(kind: str) -> dict | None:
    fp = CACHE_DIR / f"{kind}_{_current_slot()}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _check_should_regen() -> tuple[bool, list[str]]:
    """檢查當前是否該強制 regen,return (yes/no, 觸發原因列表)."""
    reasons: list[str] = []
    cache = _load_cache("market_insight")

    # 1) cache 不存在 → 直接 regen
    if not cache:
        return True, ["cache 不存在"]

    # 2) cache 過老 (> 6h)
    try:
        cached_at = datetime.fromisoformat(cache["cached_at"])
        age_min = (datetime.now(TPE) - cached_at).total_seconds() / 60
        if age_min > 360:  # 6h
            reasons.append(f"cache {age_min:.0f} 分鐘前,> 6h 強制更新")
    except Exception:
        reasons.append("cached_at 解析失敗,更新")

    # 3) 加權盤中 > 2.5%
    try:
        d = requests.get(f"{BACKEND}/api/market/dashboard", timeout=20).json()
        taiex = d.get("taiex")
        if taiex and abs(taiex.get("change_pct", 0)) > 2.5:
            reasons.append(f"加權 {taiex['change_pct']:+.2f}% 異常波動")
        vix = d.get("vix")
        if vix and vix.get("change_pct", 0) > 20:
            reasons.append(f"VIX {vix['change_pct']:+.1f}% 急升")
    except Exception as e:
        _log(f"  dashboard fail: {e}")

    # 4) news headline 含黑天鵝關鍵字
    try:
        news = requests.get(f"{BACKEND}/api/news/market", timeout=20).json()
        # /api/news/market 直接回 list,/api/news/world 才包 items
        items = news if isinstance(news, list) else news.get("items", [])
        titles = [(n.get("title", "") if isinstance(n, dict) else "") for n in items[:15]]
        crisis_hits = []
        for t in titles:
            for kw in CRISIS_KEYWORDS:
                if kw in t:
                    crisis_hits.append(f"{kw}({t[:30]}...)")
                    break
        if crisis_hits:
            reasons.append(f"黑天鵝關鍵字 × {len(crisis_hits)}: " + ", ".join(crisis_hits[:3]))
    except Exception as e:
        _log(f"  news fail: {e}")

    return (len(reasons) > 0), reasons


def check_and_maybe_regen():
    """APScheduler 每 30 分鐘呼叫 — 有事才 regen."""
    _log("=== news watcher 輕量檢查 ===")
    should, reasons = _check_should_regen()
    if not should:
        _log("  ✓ 無異常,維持 cache")
        return False
    _log(f"  ⚠️ 觸發 regen,原因:")
    for r in reasons:
        _log(f"     • {r}")
    # 跑 regen
    from backend.jobs.daily_ai_cache import regen_market_insight, regen_news_sentiment
    ok1 = regen_market_insight()
    ok2 = regen_news_sentiment()
    _log(f"  regen 完成:market_insight={ok1}, news_sentiment={ok2}")
    return ok1 or ok2


if __name__ == "__main__":
    check_and_maybe_regen()
