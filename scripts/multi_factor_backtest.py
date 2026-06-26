"""
Multi-factor cross-section ranking backtest。

對 1977 ticker 月度排序，組合 factors:
  - Momentum (12-1, 6-1)
  - Volatility (low-vol)
  - Institutional flow (foreign net buy)
  - Volume trend

每月最後一天計算 score → 持下個月 → 看 forward return

驗收 vs 0050 baseline + vs 同 ticker random window
"""
from __future__ import annotations

import io
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_INST = ROOT / "data" / "cache" / "finmind" / "institutional"


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def compute_monthly_features(df: pd.DataFrame) -> pd.DataFrame:
    """月度 features: momentum 12-1, momentum 6-1, vol, vol_trend"""
    if df.empty or len(df) < 252:
        return pd.DataFrame()
    df = df.copy()
    df["month"] = df["date"].dt.to_period("M")
    monthly = df.groupby("month").agg(
        close=("close", "last"),
        avg_vol=("volume", "mean"),
        std=("close", "std"),
    ).reset_index()
    monthly["mom_12_1"] = monthly["close"].shift(1) / monthly["close"].shift(12) - 1
    monthly["mom_6_1"] = monthly["close"].shift(1) / monthly["close"].shift(6) - 1
    monthly["vol"] = monthly["std"] / monthly["close"]
    monthly["vol_trend"] = monthly["avg_vol"] / monthly["avg_vol"].shift(3) - 1
    monthly["forward_ret"] = monthly["close"].shift(-1) / monthly["close"] - 1
    return monthly.dropna(subset=["mom_12_1", "forward_ret"])


def main():
    tickers = sorted({p.stem for p in CACHE_YF.glob("*.parquet")
                      if p.stem.isdigit() and 4 <= len(p.stem) <= 6})
    print(f"Universe: {len(tickers)} ticker")

    print("\n[1/3] 載入月度 features...")
    t0 = time.time()
    all_features = []
    for i, tk in enumerate(tickers):
        df = load_ohlcv(tk)
        m = compute_monthly_features(df)
        if not m.empty:
            m["ticker"] = tk
            all_features.append(m)
        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(tickers)}] {time.time()-t0:.0f}s")
    full = pd.concat(all_features, ignore_index=True)
    print(f"  完成 {len(full):,} ticker-months")

    print(f"\n[2/3] 月度 cross-section ranking...")
    # 對每個月，計算各 factor rank
    full["mom_12_1_rank"] = full.groupby("month")["mom_12_1"].rank(pct=True)
    full["mom_6_1_rank"] = full.groupby("month")["mom_6_1"].rank(pct=True)
    full["vol_rank"] = full.groupby("month")["vol"].rank(pct=True, ascending=False)  # 低波動分高
    full["vol_trend_rank"] = full.groupby("month")["vol_trend"].rank(pct=True)

    # 組合 score
    full["combo_score"] = (full["mom_12_1_rank"] + full["mom_6_1_rank"]
                           + full["vol_rank"] + full["vol_trend_rank"]) / 4

    # 篩高 score
    full["quintile"] = full.groupby("month")["combo_score"].transform(
        lambda x: pd.qcut(x, 5, labels=False, duplicates="drop")
    )

    # 各 quintile forward return
    print(f"\n[3/3] 結果分析")
    for qtile in [0, 1, 2, 3, 4]:
        sub = full[full["quintile"] == qtile]
        if sub.empty:
            continue
        print(f"  Q{qtile+1} (combo score {'最低' if qtile==0 else '最高' if qtile==4 else '中'}): "
              f"n={len(sub):,}, 平均 forward 1m return = {sub['forward_ret'].mean()*100:+.2f}%, "
              f"win = {(sub['forward_ret']>0).mean()*100:.1f}%")

    # 各 factor 單獨表現
    print(f"\n=== 單一 factor IC (Information Coefficient) ===")
    for factor in ["mom_12_1", "mom_6_1", "vol", "vol_trend", "combo_score"]:
        ic = full.groupby("month").apply(
            lambda x: x[factor].corr(x["forward_ret"], method="spearman")
        ).dropna()
        print(f"  {factor:<14} mean IC: {ic.mean():>+5.3f}, "
              f"std: {ic.std():.3f}, IR: {ic.mean()/ic.std() if ic.std() > 0 else 0:>+5.2f}")

    out = ROOT / "logs" / "multi_factor.csv"
    full.to_csv(out, index=False)
    print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
