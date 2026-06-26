"""
夜盤訊號驗證 — 美股 / SOX / NVDA / VIX 能否預測隔日台股？

先做方向預測（不是進出場 alpha），看 hit rate：
  TSM ADR ↑ → 隔日 0050 開盤 ↑ ?
  SOX ↑ → 隔日 2330 開盤 ↑ ?
  VIX ↑ → 隔日 0050 ↓ ?

然後試組合訊號預測幅度。
"""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def fetch_us(symbol: str) -> pd.DataFrame:
    df = yf.Ticker(symbol).history(start="2024-01-01", end="2026-04-26", auto_adjust=False)
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.date
    df["ret_pct"] = df["close"].pct_change() * 100
    return df[["date", "close", "ret_pct"]]


def load_tw(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df["prev_close"] = df["close"].shift(1)
    df["gap"] = (df["open"] / df["prev_close"] - 1) * 100
    df["intraday"] = (df["close"] / df["open"] - 1) * 100
    df["full_day"] = (df["close"] / df["prev_close"] - 1) * 100
    return df


def merge_signal(us_df: pd.DataFrame, tw_df: pd.DataFrame) -> pd.DataFrame:
    """美股 T 日（美時間，亦即台 T 收盤後）→ 台股 T+1 日。"""
    us_df = us_df.copy()
    us_df["match_date"] = pd.to_datetime(us_df["date"]) + pd.Timedelta(days=1)
    us_df["match_date"] = us_df["match_date"].dt.date
    us_df = us_df.rename(columns={"ret_pct": "us_ret"})
    return pd.merge(
        us_df[["match_date", "us_ret"]].rename(columns={"match_date": "date"}),
        tw_df[["date", "gap", "intraday", "full_day"]],
        on="date", how="inner"
    ).dropna()


def main():
    print("=" * 90)
    print("夜盤訊號 — 美股能否預測隔日台股")
    print("=" * 90)

    print("\n抓美股...")
    tsm = fetch_us("TSM")
    nvda = fetch_us("NVDA")
    sox = fetch_us("SOXX")  # SOX ETF
    vix = fetch_us("^VIX")
    spy = fetch_us("SPY")

    tw_targets = {"0050": "台灣50", "2330": "台積電", "00881": "5G+",
                   "006208": "富邦台50"}

    cases = [("TSM", tsm), ("NVDA", nvda), ("SOXX", sox), ("VIX", vix), ("SPY", spy)]

    for tw_tk, tw_name in tw_targets.items():
        tw_df = load_tw(tw_tk)
        print(f"\n=== TW: {tw_tk} {tw_name} ===")
        print(f"{'US':<8} {'相關性 vs gap':>14} {'vs intraday':>14} {'vs full':>14} "
              f"{'方向 hit (gap)':>16}")
        for us_name, us_df in cases:
            merged = merge_signal(us_df, tw_df)
            if merged.empty or len(merged) < 100:
                continue
            corr_gap = merged["us_ret"].corr(merged["gap"])
            corr_intra = merged["us_ret"].corr(merged["intraday"])
            corr_full = merged["us_ret"].corr(merged["full_day"])
            # 方向 hit rate（有預測力嗎？）
            same_dir = (np.sign(merged["us_ret"]) == np.sign(merged["gap"])).mean() * 100
            print(f"{us_name:<8} {corr_gap:>+13.3f} {corr_intra:>+13.3f} "
                  f"{corr_full:>+13.3f} {same_dir:>14.1f}%")


if __name__ == "__main__":
    main()
