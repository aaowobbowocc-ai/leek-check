"""
回補 2017-2023 institutional 資料 → 完整 9 年 backtest base。

對既有 institutional cache（2024-2026）擴充至 2017。
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

CACHE_INST = ROOT / "data" / "cache" / "finmind" / "institutional"
TARGET_START = date(2017, 1, 1)
EXISTING_END = date(2024, 1, 1)


def fetch_range(token: str, ticker: str, start: date, end: date) -> pd.DataFrame:
    url = "https://api.finmindtrade.com/api/v4/data"
    for retry in range(3):
        try:
            r = requests.get(url, params={
                "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                "data_id": ticker,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "token": token,
            }, timeout=60)
            p = r.json()
            if p.get("status") == 200:
                rows = p.get("data") or []
                if not rows:
                    return pd.DataFrame()
                df = pd.DataFrame(rows)
                # normalize columns
                keep = ["date", "name", "buy", "sell", "stock_id"]
                cols = [c for c in keep if c in df.columns]
                df = df[cols].copy()
                df["buy"] = pd.to_numeric(df.get("buy", 0), errors="coerce").fillna(0).astype(int)
                df["sell"] = pd.to_numeric(df.get("sell", 0), errors="coerce").fillna(0).astype(int)
                df["net_buy"] = df["buy"] - df["sell"]
                return df
            print(f"  {ticker}: status={p.get('status')} {p.get('msg','')[:80]}")
        except Exception as e:
            if retry == 2:
                print(f"  {ticker}: {e}")
        time.sleep(1.0)
    return pd.DataFrame()


def main():
    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    cached_files = sorted(CACHE_INST.glob("*.parquet"))
    tickers = [p.stem for p in cached_files]
    print(f"=== 回補 institutional 資料 ({len(tickers)} ticker × 7 年) ===")

    t0 = time.time()
    n_done = 0
    n_skip = 0
    n_fetched = 0
    n_failed = 0

    for i, tk in enumerate(tickers):
        cp = CACHE_INST / f"{tk}.parquet"
        existing = pd.read_parquet(cp)
        existing["date"] = pd.to_datetime(existing["date"]).dt.date

        # 已有最早日期
        existing_min = existing["date"].min() if not existing.empty else date(2026, 1, 1)
        if existing_min <= date(2017, 1, 31):
            n_skip += 1
            continue

        # 抓回補
        new_df = fetch_range(token, tk, TARGET_START, existing_min)
        if new_df.empty:
            n_failed += 1
            continue
        new_df["date"] = pd.to_datetime(new_df["date"]).dt.date

        # 合併
        combined = pd.concat([new_df, existing], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "name"], keep="last")
        combined = combined.sort_values(["date", "name"]).reset_index(drop=True)
        combined.to_parquet(cp, index=False)
        n_fetched += 1

        if (i + 1) % 50 == 0 or (i + 1) <= 5:
            elapsed = time.time() - t0
            eta = elapsed / max(1, i + 1) * (len(tickers) - i - 1)
            print(f"  [{i+1}/{len(tickers)}] {tk} done. "
                  f"fetched={n_fetched}, skip={n_skip}, failed={n_failed}, "
                  f"elapsed={elapsed/60:.1f}m, eta={eta/60:.1f}m")

        n_done += 1

    elapsed = time.time() - t0
    print(f"\n完成 {n_done} ticker / {elapsed/60:.1f} 分鐘")
    print(f"  fetched: {n_fetched}, skip: {n_skip}, failed: {n_failed}")


if __name__ == "__main__":
    main()
