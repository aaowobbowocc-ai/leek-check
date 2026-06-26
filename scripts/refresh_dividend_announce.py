"""Refresh ETF dividend announcement cache from FinMind.

從 FinMind TaiwanStockDividend 拉最新公告,強制覆寫
data/cache/finmind/dividend/{ticker}_announce.parquet

排程在 17:30 daily(daily_data_update.bat 後)。

執行:
  python scripts/refresh_dividend_announce.py            # 17 檔 deploy core
  python scripts/refresh_dividend_announce.py --all      # 全 46 ETF
"""
from __future__ import annotations
import sys, io, os, time
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from pathlib import Path
import argparse
from datetime import date, timedelta
import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "config" / ".env", override=True)

CACHE = ROOT / "data" / "cache" / "finmind" / "dividend"
CACHE.mkdir(parents=True, exist_ok=True)

# 17 檔 deploy core (validated alpha)
DEPLOY_CORE = [
    "0050", "0056", "00713", "00878", "00891", "00919", "00929",
    "00900", "00892", "00894", "00915", "00918", "006208",
    "00692", "00731", "00850", "00961",
]

# 排除的月配新 ETF (不部署 但仍 monitor 用)
EXCLUDED = ["00920", "00939", "00940", "00946"]

# 全 universe (deploy + 排除 + 其他追蹤)
ALL_TARGETS = DEPLOY_CORE + EXCLUDED + [
    "00701", "00881", "00936", "00772B", "00679B", "00687B",
]


def fetch_announce(ticker: str, start: date, end: date, token: str) -> pd.DataFrame:
    """直接呼叫 FinMind TaiwanStockDividend endpoint."""
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockDividend",
        "data_id": ticker,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "token": token,
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        payload = resp.json()
        if payload.get("status") != 200 or not payload.get("data"):
            return pd.DataFrame()
        df = pd.DataFrame(payload["data"])
        # ex_date 欄位 alias
        for c in ["CashExDividendTradingDate", "ExDividendTradingDate"]:
            if c in df.columns:
                df["ex_date"] = pd.to_datetime(df[c], errors="coerce").dt.date.astype(str)
                break
        return df
    except Exception as e:
        print(f"  ❌ {ticker}: {e}")
        return pd.DataFrame()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Refresh all 23 ETFs (default = 17 deploy core)")
    args = ap.parse_args()

    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("⚠️ FINMIND_TOKEN 未設,跳過")
        return

    targets = ALL_TARGETS if args.all else DEPLOY_CORE + EXCLUDED  # 都更新 (含 EXCLUDED 用於警示)
    end = date.today() + timedelta(days=120)   # 抓未來 4 個月
    start = date(2017, 1, 1)

    print(f"Refreshing {len(targets)} ETFs ({start} ~ {end})")
    success, total_events = 0, 0
    for i, t in enumerate(targets, 1):
        df = fetch_announce(t, start, end, token)
        if df.empty:
            print(f"  [{i}/{len(targets)}] {t}: empty")
            continue
        out = CACHE / f"{t}_announce.parquet"
        df.to_parquet(out, index=False)
        # Future events count
        if "ex_date" in df.columns:
            today_str = date.today().isoformat()
            n_future = (df["ex_date"] >= today_str).sum()
        else:
            n_future = 0
        total_events += n_future
        print(f"  [{i}/{len(targets)}] {t}: {len(df)} rows, {n_future} future ex-events")
        success += 1
        time.sleep(0.5)   # rate limit

    print(f"\n✅ Done. {success}/{len(targets)} 成功, {total_events} 個未來 ex-events")


if __name__ == "__main__":
    main()
