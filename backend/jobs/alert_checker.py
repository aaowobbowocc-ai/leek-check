"""價格警示 checker — 每 5 分鐘掃所有 active alert,觸發後寫 triggered_at.

觸發後 Discord webhook 推一筆通知(如有設 webhook URL).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# stdout utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
TPE = ZoneInfo("Asia/Taipei")
SECRETS = ROOT / ".streamlit" / "secrets.toml"
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 環境變數 (服務端用)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_ALERT_WEBHOOK", "")
if (not SUPABASE_URL or not SUPABASE_SERVICE_KEY) and SECRETS.exists():
    for line in SECRETS.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("SUPABASE_URL") and "=" in s:
            SUPABASE_URL = s.split("=", 1)[1].strip().strip('"').strip("'")
        elif s.startswith("SUPABASE_SERVICE_KEY") and "=" in s:
            SUPABASE_SERVICE_KEY = s.split("=", 1)[1].strip().strip('"').strip("'")
        elif s.startswith("DISCORD_ALERT_WEBHOOK") and "=" in s:
            DISCORD_WEBHOOK = s.split("=", 1)[1].strip().strip('"').strip("'")


def _log(msg: str):
    line = f"[{datetime.now(TPE).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    with (LOG_DIR / "alert_checker.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _send_discord(msg: str):
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=8)
    except Exception as e:
        _log(f"  discord webhook fail: {e}")


def run_check() -> int:
    """掃所有 active alert,return 觸發數量."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        _log("Supabase 沒設定,skip")
        return 0
    try:
        from supabase import create_client
    except ImportError:
        _log("supabase-py 沒裝,pip install supabase")
        return 0

    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # 1) 撈所有未觸發 alert
    try:
        res = sb.table("price_alerts").select("*").is_("triggered_at", "null").execute()
        alerts = res.data or []
    except Exception as e:
        _log(f"撈 alert 失敗: {e}")
        return 0
    if not alerts:
        _log("無 active alert,skip")
        return 0
    _log(f"=== alert checker | {len(alerts)} 筆 active ===")

    # 2) 批次抓 quote(去重 ticker)
    tickers = list({a["ticker"] for a in alerts})
    try:
        from backend.lib.quote import fetch_quotes_batch
        quotes = fetch_quotes_batch(tickers)
    except Exception as e:
        _log(f"抓 quote 失敗: {e}")
        return 0

    # 3) 逐 alert 比對 → 觸發 → update row + Discord 推
    triggered_count = 0
    now_iso = datetime.now(TPE).isoformat()
    for a in alerts:
        q = quotes.get(a["ticker"])
        if not q:
            continue
        price = q.get("price")
        if price is None:
            continue
        cond = a.get("condition", "")
        target = float(a.get("target_price", 0))
        hit = False
        if cond == "above" and price >= target:
            hit = True
        elif cond == "below" and price <= target:
            hit = True
        if not hit:
            continue
        # 觸發 — update row
        try:
            sb.table("price_alerts").update({
                "triggered_at": now_iso,
                "triggered_price": price,
            }).eq("id", a["id"]).execute()
            triggered_count += 1
            note = a.get("note", "") or ""
            symbol = "🔼" if cond == "above" else "🔽"
            msg = (f"{symbol} 警示觸發!\n"
                   f"{a['ticker']} {symbol} ${price:.2f}\n"
                   f"目標 {'≥' if cond == 'above' else '≤'} ${target:.2f}"
                   + (f"\n備註: {note}" if note else ""))
            _log(f"  ✓ {a['ticker']} {cond} ${target:.2f} → 現價 ${price:.2f} 觸發")
            _send_discord(msg)
        except Exception as e:
            _log(f"  ✗ {a['ticker']} update 失敗: {e}")
    _log(f"=== 結束 | 觸發 {triggered_count}/{len(alerts)} ===")
    return triggered_count


def main():
    run_check()


if __name__ == "__main__":
    main()
