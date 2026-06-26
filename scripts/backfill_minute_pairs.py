"""
背景補抓 5 個 pair 共 9 個新 ticker 的 minute K（2024-04 ~ 2026-04）。

3017 已 cached（從 ORB 測試），不重抓。
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockKBar"
CACHE = ROOT / "data" / "cache" / "finmind" / "minute"
CACHE.mkdir(parents=True, exist_ok=True)

START = date(2024, 4, 1)
END = date(2026, 4, 24)

# 要抓的 9 個 pair tickers（3017 已 cached 跳過）
PAIR_TICKERS = [
    "3231", "2382",        # AI server
    "2615", "2603",        # 貨櫃
    "8046", "3037",        # PCB
    "3324",                # 散熱（3017 已 cached）
    "2376", "3036",        # MB
]


def fetch_minute_day(token: str, ticker: str, d: date) -> pd.DataFrame:
    params = {
        "dataset": DATASET, "data_id": ticker,
        "start_date": d.isoformat(), "end_date": d.isoformat(),
        "token": token,
    }
    for retry in range(3):
        try:
            resp = requests.get(API_URL, params=params, timeout=30)
            payload = resp.json()
            if payload.get("status") == 200:
                rows = payload.get("data") or []
                if not rows:
                    return pd.DataFrame()
                df = pd.DataFrame(rows)
                df["dt"] = pd.to_datetime(
                    df["date"].astype(str) + " " + df["minute"].astype(str)
                )
                return df
        except Exception:
            pass
        time.sleep(0.5)
    return pd.DataFrame()


def get_or_fetch_month(token: str, ticker: str, year: int, month: int) -> pd.DataFrame:
    cache_p = CACHE / f"{ticker}_{year:04d}{month:02d}.parquet"
    if cache_p.exists():
        return pd.read_parquet(cache_p)
    cur = date(year, month, 1)
    next_month = date(year + (month == 12), (month % 12) + 1, 1)
    frames = []
    while cur < next_month:
        if cur.weekday() < 5 and START <= cur <= END:
            df = fetch_minute_day(token, ticker, cur)
            if not df.empty:
                frames.append(df)
            time.sleep(0.05)
        cur += timedelta(days=1)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out.to_parquet(cache_p, index=False)
    return out


def main() -> None:
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_KEY") or ""
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    months = []
    cur = date(START.year, START.month, 1)
    while cur <= END:
        months.append((cur.year, cur.month))
        nxt_m = (cur.month % 12) + 1
        nxt_y = cur.year + (cur.month == 12)
        cur = date(nxt_y, nxt_m, 1)

    print(f"Backfill {len(PAIR_TICKERS)} tickers × {len(months)} months")
    t0 = time.time()
    fetch_idx = 0
    total = len(PAIR_TICKERS) * len(months)
    for tk in PAIR_TICKERS:
        rows_total = 0
        for y, m in months:
            fetch_idx += 1
            df_month = get_or_fetch_month(token, tk, y, m)
            rows_total += len(df_month)
            if fetch_idx % 20 == 0:
                elapsed = time.time() - t0
                rate = fetch_idx / elapsed
                eta = (total - fetch_idx) / rate if rate > 0 else 0
                print(f"  [{fetch_idx}/{total}] elapsed {elapsed/60:.1f}m  ETA {eta/60:.1f}m")
        print(f"  {tk}: {rows_total:,} rows total")
    print(f"\n完成，總耗時 {(time.time()-t0)/60:.1f} 分")


if __name__ == "__main__":
    main()
