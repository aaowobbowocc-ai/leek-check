"""
DCA Timing Strategy Backtest — 4 個日曆 / 極值 anomaly 測試。

對 0050 (台灣 50) 13 年 daily K 跑：
  1. RSI 極值 mean reversion (RSI > 70 / < 30)
  2. 月底 + 月初效應
  3. 春節前後效應
  4. 季底法人移倉效應

目的：找最佳 DCA 進場 timing
"""
from __future__ import annotations

import io
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def load_0050():
    df = pd.read_parquet(CACHE_YF / "0050.parquet")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ret_1d"] = df["close"].pct_change() * 100
    df["ret_5d"] = df["close"].pct_change(5).shift(-5) * 100  # forward 5d
    df["ret_10d"] = df["close"].pct_change(10).shift(-10) * 100
    df["ret_20d"] = df["close"].pct_change(20).shift(-20) * 100
    return df


# ────── RSI 計算 ──────
def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta).where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def test_rsi_mean_reversion(df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("【1. TAIEX/0050 RSI 極值 Mean Reversion】")
    print("=" * 80)
    print("假設：RSI > 70 (過買) → 短期回調 / RSI < 30 (過賣) → 反彈")

    df = df.copy()
    df["rsi14"] = compute_rsi(df["close"], 14)

    print(f"\n{'RSI 區間':<20} {'天數':>5} {'下5日 mean':>12} {'下10日 mean':>13} "
          f"{'下20日 mean':>13}")
    bins = [(0, 25, "極低 < 25"), (25, 30, "過低 25-30"), (30, 50, "中低 30-50"),
            (50, 70, "中高 50-70"), (70, 75, "過高 70-75"), (75, 100, "極高 > 75")]
    for lo, hi, label in bins:
        sub = df[(df["rsi14"] >= lo) & (df["rsi14"] < hi)]
        if len(sub) < 10:
            continue
        m5 = sub["ret_5d"].dropna().mean()
        m10 = sub["ret_10d"].dropna().mean()
        m20 = sub["ret_20d"].dropna().mean()
        marker = "🟢" if m20 > 1 else ("🔴" if m20 < -1 else "")
        print(f"  {label:<19} {len(sub):>5} {m5:>+10.2f}% {m10:>+11.2f}% "
              f"{m20:>+11.2f}% {marker}")

    # 整體基準
    base_5 = df["ret_5d"].dropna().mean()
    base_20 = df["ret_20d"].dropna().mean()
    print(f"\n  整體 baseline:        {len(df.dropna(subset=['ret_5d'])):>5} "
          f"{base_5:>+10.2f}% {df['ret_10d'].dropna().mean():>+11.2f}% "
          f"{base_20:>+11.2f}%")


def test_month_effects(df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("【2. 月底 + 月初效應】")
    print("=" * 80)
    print("假設：月底 window dressing / 月初新資金流入")

    df = df.copy()
    df["dom"] = df["date"].dt.day
    df["days_in_month"] = df["date"].dt.days_in_month
    df["from_end"] = df["days_in_month"] - df["dom"]

    print(f"\n{'時段':<22} {'天數':>5} {'當日報酬 mean':>13} {'win%':>6}")

    cases = [
        ("月初前 3 日", df[df["dom"] <= 3]),
        ("月初第 4-7 日", df[(df["dom"] >= 4) & (df["dom"] <= 7)]),
        ("月中 8-22 日", df[(df["dom"] >= 8) & (df["dom"] <= 22)]),
        ("月底前 5 日 (距月底 ≤ 5)", df[df["from_end"] <= 4]),
        ("月底前 1 日", df[df["from_end"] == 0]),
    ]
    for label, sub in cases:
        m = sub["ret_1d"].mean()
        win = (sub["ret_1d"] > 0).mean() * 100
        marker = "🟢" if m > 0.1 else ("🔴" if m < -0.1 else "")
        print(f"  {label:<21} {len(sub):>5} {m:>+12.3f}% {win:>5.1f}% {marker}")

    # 整體
    print(f"  整體 baseline          {len(df):>5} {df['ret_1d'].mean():>+12.3f}% "
          f"{(df['ret_1d']>0).mean()*100:>5.1f}%")


def test_chinese_new_year(df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("【3. 春節前後效應】")
    print("=" * 80)
    print("假設：春節前紅包行情 / 春節後開紅盤")

    # TW 春節日期 (2017-2026)
    cny_dates = [
        "2017-01-28", "2018-02-16", "2019-02-05", "2020-01-25", "2021-02-12",
        "2022-02-01", "2023-01-22", "2024-02-10", "2025-01-29", "2026-02-17",
    ]
    cny_dates = [pd.Timestamp(d) for d in cny_dates]

    df = df.copy()

    def days_to_cny(d):
        # 找最近的春節（前後 30 天內）
        for cny in cny_dates:
            delta = (d - cny).days
            if -30 <= delta <= 30:
                return delta
        return None

    df["days_to_cny"] = df["date"].apply(days_to_cny)

    print(f"\n{'時段':<22} {'天數':>5} {'mean ret':>10} {'累計':>9}")
    cases = [
        ("春節前 7 日", df[(df["days_to_cny"] >= -7) & (df["days_to_cny"] < 0)]),
        ("春節前 5 日", df[(df["days_to_cny"] >= -5) & (df["days_to_cny"] < 0)]),
        ("春節前 3 日", df[(df["days_to_cny"] >= -3) & (df["days_to_cny"] < 0)]),
        ("春節後第 1 日（開紅盤）", df[df["days_to_cny"].between(1, 3)]),
        ("春節後 5 日", df[df["days_to_cny"].between(1, 5)]),
        ("春節後 10 日", df[df["days_to_cny"].between(1, 10)]),
        ("非春節期間", df[df["days_to_cny"].isna()]),
    ]
    for label, sub in cases:
        if sub.empty:
            continue
        m = sub["ret_1d"].mean()
        cumul = (1 + sub["ret_1d"] / 100).prod() - 1
        marker = "🟢" if m > 0.1 else ("🔴" if m < -0.1 else "")
        print(f"  {label:<21} {len(sub):>5} {m:>+9.3f}% {cumul*100:>+8.2f}% {marker}")


def test_quarter_end(df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("【4. 季底法人移倉效應】")
    print("=" * 80)
    print("假設：3/6/9/12 月底前後 5 日波動異常")

    df = df.copy()
    df["month"] = df["date"].dt.month
    df["dom"] = df["date"].dt.day
    df["days_in_month"] = df["date"].dt.days_in_month
    df["from_end"] = df["days_in_month"] - df["dom"]
    df["is_quarter_end"] = df["month"].isin([3, 6, 9, 12])

    print(f"\n{'時段':<26} {'天數':>5} {'mean ret':>10} {'win%':>6}")
    cases = [
        ("季底月份 月底前 5 日", df[df["is_quarter_end"] & (df["from_end"] <= 4)]),
        ("季底月份 月底前 1 日", df[df["is_quarter_end"] & (df["from_end"] == 0)]),
        ("季初月份 (4/7/10/1) 前 5 日",
         df[df["month"].isin([4, 7, 10, 1]) & (df["dom"] <= 5)]),
        ("非季底月份 月底前 5 日",
         df[(~df["is_quarter_end"]) & (df["from_end"] <= 4)]),
    ]
    for label, sub in cases:
        if sub.empty:
            continue
        m = sub["ret_1d"].mean()
        win = (sub["ret_1d"] > 0).mean() * 100
        marker = "🟢" if m > 0.1 else ("🔴" if m < -0.1 else "")
        print(f"  {label:<25} {len(sub):>5} {m:>+9.3f}% {win:>5.1f}% {marker}")


def main():
    print("=" * 80)
    print("DCA Timing Backtest — 4 個 anomaly 在 0050 (2017-2026, 9 年)")
    print("=" * 80)
    df = load_0050()
    print(f"\n資料：0050 daily K {len(df)} days ({df.date.min().date()} ~ {df.date.max().date()})")

    test_rsi_mean_reversion(df)
    test_month_effects(df)
    test_chinese_new_year(df)
    test_quarter_end(df)

    print("\n" + "=" * 80)
    print("結論建議（看上方數字判斷）")
    print("=" * 80)


if __name__ == "__main__":
    main()
