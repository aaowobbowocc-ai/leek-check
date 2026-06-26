"""
小型 IPO 蜜月期 backtest — 上市第 1-30 天 alpha。

研究：
  TW 股票上市後 1-30 天有「蜜月期」嗎？
  哪些條件下蜜月期最強？

策略：
  V1. 上市第 1 天 long → hold 5/10/20/30 天 close
  V2. 看「上市時市值大小」分組
  V3. cross-regime 4 期分層
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
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

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_IPO = ROOT / "data" / "cache" / "finmind" / "ipo"
CACHE_IPO.mkdir(parents=True, exist_ok=True)


def fetch_ipo_list(token: str) -> pd.DataFrame:
    """抓 TW IPO 列表 (TaiwanStockListInfo + TaiwanStockNewListing 等)"""
    cp = CACHE_IPO / "ipo_list.parquet"
    if cp.exists():
        return pd.read_parquet(cp)

    # 試多個 dataset
    candidates = ["TaiwanStockListInfo", "TaiwanStockNewListing", "TaiwanStockInfo"]
    for ds in candidates:
        try:
            r = requests.get("https://api.finmindtrade.com/api/v4/data",
                             params={"dataset": ds, "token": token,
                                     "start_date": "2017-01-01",
                                     "end_date": "2026-04-26"},
                             timeout=30)
            p = r.json()
            if p.get("status") == 200 and p.get("data"):
                df = pd.DataFrame(p["data"])
                print(f"  ✅ {ds}: {len(df)} rows, columns: {list(df.columns)[:8]}")
                df.to_parquet(cp, index=False)
                return df
        except Exception as e:
            print(f"  {ds}: {e}")
    return pd.DataFrame()


def load_ohlcv(tk: str) -> pd.DataFrame:
    p = CACHE_YF / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def main():
    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    print("=" * 80)
    print("小型 IPO 蜜月期 backtest")
    print("=" * 80)

    # 抓 IPO 列表
    print("\n[1/3] 抓 IPO 列表...")
    df_ipo = fetch_ipo_list(token)
    if df_ipo.empty:
        print("❌ FinMind 無 IPO dataset"); return
    print(f"  {len(df_ipo)} rows")

    # 看欄位結構
    print(f"\n欄位: {list(df_ipo.columns)}")
    print(f"\nSample:")
    print(df_ipo.head(3).to_string())

    # 找上市日 + ticker 欄位
    date_col = None
    for c in ["IPODate", "ListedDate", "date", "公開日期"]:
        if c in df_ipo.columns:
            date_col = c; break

    tk_col = None
    for c in ["stock_id", "ticker", "code"]:
        if c in df_ipo.columns:
            tk_col = c; break

    if not date_col or not tk_col:
        print(f"\n❌ 找不到日期/ticker 欄位，dataset 結構需研究")
        return

    print(f"\n  date_col={date_col}, ticker_col={tk_col}")

    df_ipo["ipo_date"] = pd.to_datetime(df_ipo[date_col], errors="coerce").dt.date
    df_ipo = df_ipo.dropna(subset=["ipo_date"])
    df_ipo = df_ipo[df_ipo["ipo_date"] >= date(2017, 1, 1)]
    df_ipo = df_ipo[df_ipo["ipo_date"] <= date(2026, 4, 26)]
    df_ipo[tk_col] = df_ipo[tk_col].astype(str)
    print(f"  {len(df_ipo)} 個 2017-2026 IPO 事件")

    # 對每個 IPO，看上市第 1/5/10/20/30 天表現
    rows = []
    for _, ipo in df_ipo.iterrows():
        tk = ipo[tk_col]
        ipo_date = ipo["ipo_date"]
        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            continue
        # 找上市日後第一個交易日
        future = [d for d in ohlcv["date"] if d >= ipo_date]
        if len(future) < 31:
            continue
        d0 = future[0]
        idx0 = list(ohlcv["date"]).index(d0)
        entry = float(ohlcv.iloc[idx0]["close"])

        for hold in [1, 5, 10, 20, 30]:
            if idx0 + hold >= len(ohlcv):
                continue
            exit_p = float(ohlcv.iloc[idx0 + hold]["close"])
            ret = (exit_p / entry - 1) * 100
            rows.append({"ticker": tk, "ipo_date": ipo_date,
                         "hold_days": hold, "ret_pct": ret})

    res = pd.DataFrame(rows)
    if res.empty:
        print("❌ 無匹配 IPO 與 cache"); return

    print(f"\n[2/3] 計算 alpha")
    print(f"  總 IPO 事件: {res['ticker'].nunique()}, trades: {len(res)}")
    print(f"\n各 hold 期表現:")
    print(f"{'hold':>5} {'n':>4} {'mean':>10} {'median':>10} {'win':>5}")
    for hold in [1, 5, 10, 20, 30]:
        sub = res[res["hold_days"] == hold]
        if sub.empty: continue
        m = sub["ret_pct"].mean()
        med = sub["ret_pct"].median()
        win = (sub["ret_pct"] > 0).mean() * 100
        print(f"{hold:>4}d {len(sub):>4} {m:>+8.2f}% {med:>+8.2f}% {win:>4.0f}%")

    # cross-regime
    print(f"\n[3/3] Cross-regime (hold 30d):")
    PERIODS = [
        ("A 2017-2019", date(2017, 1, 1), date(2019, 12, 31)),
        ("B 2020 covid", date(2020, 1, 1), date(2020, 12, 31)),
        ("C 2021-2022", date(2021, 1, 1), date(2022, 12, 31)),
        ("D 2023-2026", date(2023, 1, 1), date(2026, 4, 30)),
    ]
    sub30 = res[res["hold_days"] == 30].copy()
    sub30["ipo_date"] = pd.to_datetime(sub30["ipo_date"]).dt.date
    print(f"\n{'period':<18} {'n':>4} {'mean':>10} {'median':>10} {'win':>5}")
    for label, start, end in PERIODS:
        p = sub30[(sub30["ipo_date"] >= start) & (sub30["ipo_date"] <= end)]
        if len(p) < 5: continue
        m = p["ret_pct"].mean()
        print(f"  {label:<16} {len(p):>4} {m:>+8.2f}% "
              f"{p['ret_pct'].median():>+8.2f}% "
              f"{(p['ret_pct']>0).mean()*100:>4.0f}%")

    out = ROOT / "logs" / "ipo_honeymoon.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
