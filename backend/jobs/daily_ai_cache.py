"""每日 AI cache 預生成 — 排程跑 3 次:7:30 / 14:00 / 20:30 台灣時間.

每次跑都會清掉當 slot cache,call 一次 API 重生,讓使用者讀到最新.

排程方式:
- Windows: Task Scheduler 排 3 個 trigger,run `python -m backend.jobs.daily_ai_cache`
- Linux/cron: 30 7,30 20 * * * cd /path/INVEST && python -m backend.jobs.daily_ai_cache
              0 14 * * 1-5 cd /path/INVEST && python -m backend.jobs.daily_ai_cache
"""
from __future__ import annotations

import requests
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


BACKEND = "http://localhost:8000"
TPE = ZoneInfo("Asia/Taipei")


def _now_slot():
    now = datetime.now(TPE)
    h = now.hour
    if h < 12:
        return "morning"
    if h < 19:
        return "noon"
    return "evening"


def regen_market_insight() -> bool:
    """從 dashboard 抓 live 數據再 call AI 生成 + cache."""
    print("[market-insight] fetching dashboard ...")
    try:
        d = requests.get(f"{BACKEND}/api/market/dashboard", timeout=30).json()
    except Exception as e:
        print(f"  ✗ dashboard fail: {e}")
        return False

    if not d.get("taiex"):
        print("  ✗ no taiex data, skip")
        return False

    taiex = d["taiex"]
    intl = {}
    for k in ["sp500", "nasdaq", "sox", "dxy", "btc", "gold", "oil"]:
        v = d.get(k)
        if v and v.get("price") is not None:
            intl[k] = {"price": v["price"], "change_pct": v["change_pct"]}

    inst = d.get("institutional") or {}
    payload = {
        "taiex_price": taiex["price"],
        "taiex_change_pct": taiex["change_pct"],
        "taiex_ma200_dist": taiex.get("ma200_dist_pct"),
        "taiex_temperature": taiex.get("temperature", ""),
        "vix": (d.get("vix") or {}).get("price"),
        "intl": intl,
        "institutional": {
            "foreign_20d": inst.get("foreign_20d", 0),
            "invtrust_20d": inst.get("invtrust_20d", 0),
            "dealer_20d": inst.get("dealer_20d", 0),
        },
        "style": "neutral",
        "timeframe": "mid",
    }
    # 先清 cache 強制重生
    requests.delete(f"{BACKEND}/api/ai/cache/market_insight", timeout=10)
    print("  → calling AI ...")
    try:
        r = requests.post(f"{BACKEND}/api/ai/market-insight", json=payload, timeout=180)
        if r.status_code != 200:
            print(f"  ✗ AI fail {r.status_code}: {r.text[:200]}")
            return False
        out = r.json()
        print(f"  ✓ ok, {len(out.get('text', ''))} chars saved")
        return True
    except Exception as e:
        print(f"  ✗ {e}")
        return False


def regen_news_sentiment() -> bool:
    """抓 market news → call AI 整理 + cache."""
    print("[news-sentiment] fetching market news ...")
    try:
        news = requests.get(f"{BACKEND}/api/news/market", timeout=30).json()
    except Exception as e:
        print(f"  ✗ news fail: {e}")
        return False

    titles = [n["title"] for n in news.get("items", [])[:15]]
    if not titles:
        print("  ✗ no news, skip")
        return False

    requests.delete(f"{BACKEND}/api/ai/cache/news_sentiment", timeout=10)
    print(f"  → calling AI with {len(titles)} titles ...")
    try:
        r = requests.post(f"{BACKEND}/api/ai/news-sentiment", json={
            "news_titles": titles,
            "style": "neutral", "timeframe": "mid",
        }, timeout=180)
        if r.status_code != 200:
            print(f"  ✗ AI fail {r.status_code}: {r.text[:200]}")
            return False
        out = r.json()
        print(f"  ✓ ok, {len(out.get('text', ''))} chars saved")
        return True
    except Exception as e:
        print(f"  ✗ {e}")
        return False


def main():
    slot = _now_slot()
    print(f"=== daily AI cache regen | slot={slot} | "
          f"{datetime.now(TPE).strftime('%Y-%m-%d %H:%M:%S %Z')} ===")
    ok = []
    ok.append(("market_insight", regen_market_insight()))
    ok.append(("news_sentiment", regen_news_sentiment()))
    succ = sum(1 for _, v in ok if v)
    print(f"=== done: {succ}/{len(ok)} ok ===")
    sys.exit(0 if succ == len(ok) else 1)


if __name__ == "__main__":
    main()
