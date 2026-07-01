"""本地抓 yfinance 國際指數 → upload 到 Supabase market_snapshot table.

雲端 (Render) 抓不到 Yahoo,靠這個 script 定期更新。
Windows Task Scheduler 排每 15/30 分鐘跑一次即可。

用法:
  python scripts/update_market_snapshot.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# 強制 utf-8 防 Windows console emoji crash
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"
TPE = ZoneInfo("Asia/Taipei")

# 讀環境變數 (secrets.toml)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
if (not SUPABASE_URL or not SUPABASE_SERVICE_KEY) and SECRETS.exists():
    for line in SECRETS.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("SUPABASE_URL") and "=" in s:
            SUPABASE_URL = s.split("=", 1)[1].strip().strip('"').strip("'")
        elif s.startswith("SUPABASE_SERVICE_KEY") and "=" in s:
            SUPABASE_SERVICE_KEY = s.split("=", 1)[1].strip().strip('"').strip("'")

# 抓的 symbols (yfinance)
SYMBOLS = [
    ("^GSPC", "S&P 500"),
    ("^IXIC", "NASDAQ"),
    ("^SOX", "費城半導體"),
    ("^VIX", "美股恐慌指數"),
    ("DX-Y.NYB", "美元指數"),
    ("^N225", "日經 225"),
    ("DXJ", "日股 DXJ"),
    ("GC=F", "黃金"),
    ("CL=F", "WTI 原油"),
    ("SI=F", "白銀"),
]


def fetch_one(symbol: str, name: str) -> dict | None:
    try:
        import yfinance as yf
        import math
        t = yf.Ticker(symbol)
        h = t.history(period="10d", auto_adjust=False)
        if h.empty:
            return None
        close_raw = h["Close"].iloc[-1]
        if close_raw is None or (isinstance(close_raw, float) and math.isnan(close_raw)):
            return None
        close = float(close_raw)
        prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else close
        chg = (close / prev - 1) * 100 if prev else 0.0
        return {
            "price": round(close, 4),
            "change_pct": round(chg, 2),
            "asof": h.index[-1].strftime("%Y-%m-%d"),
        }
    except Exception as e:
        print(f"  ✗ {symbol}: {e}")
        return None


def main():
    print(f"=== market snapshot update | {datetime.now(TPE).strftime('%Y-%m-%d %H:%M:%S')} ===")
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("✗ Supabase secrets 沒設,skip")
        sys.exit(1)

    indices: dict[str, dict] = {}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {sym: ex.submit(fetch_one, sym, name) for sym, name in SYMBOLS}
        for sym, fut in futs.items():
            r = fut.result()
            if r:
                indices[sym] = r
                print(f"  ✓ {sym}: {r['price']} ({r['change_pct']:+.2f}%)")

    if not indices:
        print("✗ 全部抓失敗")
        sys.exit(1)

    # upsert 到 Supabase
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        payload = {
            "id": 1,
            "data": {"indices": indices},
            "updated_at": datetime.now(TPE).isoformat(),
        }
        sb.table("market_snapshot").upsert(payload).execute()
        print(f"✓ 上傳 {len(indices)} 個 index 到 Supabase")
    except Exception as e:
        print(f"✗ Supabase upsert 失敗: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
