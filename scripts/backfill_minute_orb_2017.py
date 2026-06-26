"""補抓 ORB whitelist (2408 + 2485) 2017-2023 minute K → 跨牛熊驗證 ORB 真 alpha。"""
from __future__ import annotations
import io, os, sys, time
from datetime import date, timedelta
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

API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockKBar"
CACHE = ROOT / "data" / "cache" / "finmind" / "minute"

TICKERS = ["2408", "2485"]
START = date(2017, 1, 1)
END = date(2024, 4, 1)  # 接到既有 cache


def fetch_day(token: str, ticker: str, d: date) -> pd.DataFrame:
    params = {"dataset": DATASET, "data_id": ticker,
              "start_date": d.isoformat(), "end_date": d.isoformat(), "token": token}
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


def get_or_fetch_month(token: str, ticker: str, year: int, month: int) -> bool:
    cp = CACHE / f"{ticker}_{year}{month:02d}.parquet"
    if cp.exists():
        return True
    cur = date(year, month, 1)
    next_m = date(year + (month == 12), (month % 12) + 1, 1)
    frames = []
    while cur < next_m:
        if cur.weekday() < 5 and START <= cur <= END:
            df = fetch_day(token, ticker, cur)
            if not df.empty:
                frames.append(df)
            time.sleep(0.05)
        cur += timedelta(days=1)
    if frames:
        out = pd.concat(frames, ignore_index=True)
        out.to_parquet(cp, index=False)
        return True
    return False


def main():
    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    months = []
    cur = date(START.year, START.month, 1)
    while cur < END:
        months.append((cur.year, cur.month))
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    print(f"=== 回補 ORB whitelist minute K ({len(TICKERS)} ticker × {len(months)} 月) ===")

    t0 = time.time()
    n_done = 0
    n_skip = 0
    for tk in TICKERS:
        for y, m in months:
            cp = CACHE / f"{tk}_{y}{m:02d}.parquet"
            if cp.exists():
                n_skip += 1
                n_done += 1
                continue
            ok = get_or_fetch_month(token, tk, y, m)
            n_done += 1
            if n_done % 10 == 0:
                elapsed = time.time() - t0
                eta = elapsed / max(1, n_done - n_skip) * (len(TICKERS) * len(months) - n_done)
                print(f"  [{n_done}/{len(TICKERS) * len(months)}] {tk} {y}-{m:02d}  "
                      f"elapsed {elapsed/60:.1f}m  eta {eta/60:.1f}m  (skip {n_skip})")

    elapsed = time.time() - t0
    print(f"\n完成 {n_done} months / {elapsed/60:.1f} 分鐘")


if __name__ == "__main__":
    main()
