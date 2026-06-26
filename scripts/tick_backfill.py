"""
Tick 資料 backfill — Sponsor Pro 升級後的第一個工具。

每 ticker 一日抓一次（2330 約 8000 ticks），存成 daily parquet。
Cache 結構：data/cache/finmind/tick/{ticker}_{YYYYMMDD}.parquet

用法:
  python scripts/tick_backfill.py --ticker 2330 --start 2024-01-01 --end 2024-12-31
  python scripts/tick_backfill.py --ticker 2330  # 預設過去 1 年
"""
from __future__ import annotations

import argparse
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
    load_dotenv(ROOT / "config" / ".env", override=True)
except ImportError:
    pass

API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockPriceTick"
CACHE = ROOT / "data" / "cache" / "finmind" / "tick"
CACHE.mkdir(parents=True, exist_ok=True)


def fetch_tick_day(token: str, ticker: str, d: date) -> pd.DataFrame:
    """抓單日 tick；含 retry。"""
    params = {
        "dataset": DATASET, "data_id": ticker,
        "start_date": d.isoformat(), "end_date": d.isoformat(),
        "token": token,
    }
    for retry in range(3):
        try:
            resp = requests.get(API_URL, params=params, timeout=60)
            payload = resp.json()
            if payload.get("status") == 200:
                rows = payload.get("data") or []
                if not rows:
                    return pd.DataFrame()
                return pd.DataFrame(rows)
        except Exception as e:
            if retry == 2:
                print(f"    {ticker} {d} 失敗：{e}")
        time.sleep(1)
    return pd.DataFrame()


def cache_path(ticker: str, d: date) -> Path:
    return CACHE / f"{ticker}_{d.strftime('%Y%m%d')}.parquet"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", default="2330")
    p.add_argument("--start", type=str, default=None)
    p.add_argument("--end", type=str, default=None)
    args = p.parse_args()

    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("❌ FINMIND_TOKEN 未設"); return

    today = date.today()
    if args.start is None:
        start = today - timedelta(days=365)
    else:
        start = date.fromisoformat(args.start)
    if args.end is None:
        end = today
    else:
        end = date.fromisoformat(args.end)

    print(f"Backfill {args.ticker} {start} ~ {end}")

    cur = start
    days = []
    while cur <= end:
        if cur.weekday() < 5:    # 跳過週末
            days.append(cur)
        cur += timedelta(days=1)

    print(f"  共 {len(days)} 個交易日（含估算 weekend）")
    todo = [d for d in days if not cache_path(args.ticker, d).exists()]
    print(f"  已 cache: {len(days) - len(todo)}, 待抓: {len(todo)}")

    if not todo:
        print("  全部已 cache，跳過"); return

    t0 = time.time()
    ok, fail, empty = 0, 0, 0
    total_rows = 0
    for i, d in enumerate(todo, 1):
        df = fetch_tick_day(token, args.ticker, d)
        cp = cache_path(args.ticker, d)
        if df.empty:
            # 空檔（國定假日 / 停牌）→ 寫 sentinel 避免下次重抓
            pd.DataFrame([{"date": d.isoformat(), "stock_id": args.ticker,
                          "deal_price": 0.0, "volume": 0, "Time": "00:00:00",
                          "TickType": 0, "_empty": True}]).to_parquet(cp, index=False)
            empty += 1
        else:
            df.to_parquet(cp, index=False)
            ok += 1
            total_rows += len(df)
        if i % 20 == 0 or i == len(todo):
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (len(todo) - i) / rate
            print(f"  [{i:>4}/{len(todo)}] ok={ok} empty={empty} fail={fail}  "
                  f"rows={total_rows:,}  elapsed={elapsed/60:.1f}m  ETA={eta/60:.1f}m")

    print(f"\n✅ 完成：ok={ok}, empty={empty}, fail={fail}")
    print(f"   總 rows: {total_rows:,}")
    print(f"   耗時：{(time.time()-t0)/60:.1f} 分")
    # 量化磁碟用量
    files = list(CACHE.glob(f"{args.ticker}_*.parquet"))
    total_size = sum(f.stat().st_size for f in files)
    print(f"   {len(files)} 檔，總大小: {total_size/1e6:.1f} MB")


if __name__ == "__main__":
    main()
