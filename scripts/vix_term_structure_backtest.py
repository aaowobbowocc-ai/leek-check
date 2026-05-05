"""
VIX Term Structure → SPY Timing Signal

原理:
  VIX 期貨曲線（contango / backwardation）反映市場恐慌結構。
  Contango (VIX < VIX3M): 90% 時間，正常，市場淡定
  Backwardation (VIX > VIX3M): 危機/恐慌，歷史 marks 市場底

訊號 (用 VIX/VIX3M ratio 作 proxy):
  ratio = VIX / VIX3M

  ratio > 1.0 (backwardation) → SPY 反彈訊號（抄底）
  ratio > 1.10 (deep backwardation) → 強反彈（capitulation）
  ratio < 0.85 (deep contango) → complacency（不一定壞，但邊際謹慎）

回測:
  Period: 2006-07 (VIX3M 開始) ~ 2025-12
  Target: SPY fwd 5d / 20d / 60d return
  Comparison: BTH SPY 同期

Alpha 來源:
  - backwardation = 強迫平倉到底 → 反彈期望值高
  - 學術研究 (Whaley 2009, Carr & Wu 2009) 確認 vol risk premium

Risk:
  - Tail event: backwardation 持續多週（COVID 2020, 雷曼 2008）
  - 樣本小: 真正 deep backwardation 9 年只 ~30 個事件
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
GLOBAL = ROOT / "data" / "cache" / "yfinance" / "global"


def load(name: str) -> pd.DataFrame:
    df = pd.read_parquet(GLOBAL / name)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def main():
    print("=" * 80)
    print("  VIX Term Structure → SPY Timing Backtest")
    print("=" * 80)

    vix = load("VIX_full.parquet")[["date", "close"]].rename(columns={"close": "vix"})
    vix3m = load("VIX3M.parquet")[["date", "close"]].rename(columns={"close": "vix3m"})
    spy = load("SPY_full.parquet")[["date", "close"]].rename(columns={"close": "spy"})

    df = vix.merge(vix3m, on="date").merge(spy, on="date")
    df = df.sort_values("date").reset_index(drop=True)
    df["ratio"] = df["vix"] / df["vix3m"]

    # Forward returns (multiple horizons)
    for h in [5, 20, 60]:
        df[f"fwd_{h}d"] = (df["spy"].shift(-h) / df["spy"] - 1) * 100

    # Backtest period
    df = df[df["date"] >= "2006-07-17"].reset_index(drop=True)
    df = df.dropna(subset=["fwd_60d"])

    print(f"\n  Total observations: {len(df):,}")
    print(f"  Period: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"  VIX/VIX3M ratio stats:")
    print(f"    min: {df['ratio'].min():.3f}")
    print(f"    p10: {df['ratio'].quantile(0.1):.3f}")
    print(f"    median: {df['ratio'].median():.3f}")
    print(f"    p90: {df['ratio'].quantile(0.9):.3f}")
    print(f"    max: {df['ratio'].max():.3f}")

    # Signal buckets
    bucks = [
        ("Deep contango (ratio < 0.85)", df["ratio"] < 0.85),
        ("Normal contango (0.85-0.95)", (df["ratio"] >= 0.85) & (df["ratio"] < 0.95)),
        ("Slight contango (0.95-1.0)", (df["ratio"] >= 0.95) & (df["ratio"] < 1.0)),
        ("Mild backwardation (1.0-1.05)", (df["ratio"] >= 1.0) & (df["ratio"] < 1.05)),
        ("Moderate backwardation (1.05-1.10)", (df["ratio"] >= 1.05) & (df["ratio"] < 1.10)),
        ("Deep backwardation (>= 1.10)", df["ratio"] >= 1.10),
    ]

    for hold in [5, 20, 60]:
        print(f"\n  === SPY fwd {hold}d return by ratio bucket ===")
        print(f"  {'Bucket':<38} {'n':>5}  {'mean':>7}  {'win%':>5}  {'t':>6}")
        for label, mask in bucks:
            sub = df.loc[mask, f"fwd_{hold}d"].dropna()
            if len(sub) < 5:
                print(f"  {label:<38} n={len(sub)} (太少)")
                continue
            m = sub.mean()
            w = (sub > 0).mean() * 100
            t, p = stats.ttest_1samp(sub, 0, alternative="greater")
            sig = "✅" if p < 0.05 else ("⚠️" if p < 0.15 else "")
            print(f"  {label:<38} {len(sub):>5}  {m:>+6.2f}%  {w:>4.1f}%  {t:>+5.2f}{sig}")

    # Compare backwardation vs all (BTH SPY)
    print(f"\n  === Backwardation vs Full Sample (SPY 20d) ===")
    full_mean = df["fwd_20d"].mean()
    full_t, full_p = stats.ttest_1samp(df["fwd_20d"], 0, alternative="greater")
    print(f"  Full sample SPY 20d: mean {full_mean:+.2f}%, t {full_t:+.2f}, p {full_p:.4f}")

    bw = df[df["ratio"] >= 1.0]["fwd_20d"].dropna()
    if len(bw) > 0:
        bw_t, bw_p = stats.ttest_1samp(bw, 0, alternative="greater")
        print(f"  Backwardation only:  n={len(bw):,}, mean {bw.mean():+.2f}%, t {bw_t:+.2f}, p {bw_p:.5f}")
        excess = bw.mean() - full_mean
        print(f"  Excess vs BTH: {excess:+.2f}pp")

    deep_bw = df[df["ratio"] >= 1.10]["fwd_20d"].dropna()
    if len(deep_bw) >= 5:
        dbw_t, dbw_p = stats.ttest_1samp(deep_bw, 0, alternative="greater")
        print(f"  Deep backwardation:  n={len(deep_bw):,}, mean {deep_bw.mean():+.2f}%, t {dbw_t:+.2f}, p {dbw_p:.5f}")
        excess = deep_bw.mean() - full_mean
        print(f"  Excess vs BTH: {excess:+.2f}pp ⭐")

    # Time series of backwardation events
    df["is_bw"] = df["ratio"] >= 1.0
    df["is_deep_bw"] = df["ratio"] >= 1.10
    df["year"] = df["date"].dt.year

    print(f"\n  === Backwardation events by year ===")
    yearly = df.groupby("year").agg(
        days=("is_bw", "sum"),
        deep_days=("is_deep_bw", "sum"),
        spy_yr_ret=("spy", lambda x: (x.iloc[-1]/x.iloc[0]-1)*100),
    ).round(1)
    print(yearly.to_string())

    # Trading rule simulation: long SPY when backwardation, cash otherwise
    print(f"\n  === Trading Rule Simulation ===")
    print(f"  Rule: long SPY 20d when ratio >= 1.0 trigger, else hold cash (0%)")

    df_sim = df.copy()
    df_sim["signal"] = (df_sim["ratio"] >= 1.0).astype(int)

    # Daily NAV simulation: cash returns 0, holding SPY returns daily SPY return
    df_sim["spy_ret"] = df_sim["spy"].pct_change()
    df_sim["holding"] = 0
    in_pos = False
    days_left = 0
    trades = []
    entry_idx = None
    for i in range(len(df_sim)):
        if in_pos:
            df_sim.loc[i, "holding"] = 1
            days_left -= 1
            if days_left <= 0:
                in_pos = False
                trades.append({
                    "entry_date": df_sim.iloc[entry_idx]["date"],
                    "exit_date": df_sim.iloc[i]["date"],
                    "entry_price": df_sim.iloc[entry_idx]["spy"],
                    "exit_price": df_sim.iloc[i]["spy"],
                })
        elif df_sim.iloc[i]["signal"] == 1:
            in_pos = True
            days_left = 20
            entry_idx = i

    if trades:
        trade_df = pd.DataFrame(trades)
        trade_df["return_pct"] = (trade_df["exit_price"] / trade_df["entry_price"] - 1) * 100
        print(f"  Total trades: {len(trade_df)}")
        print(f"  Mean return: {trade_df['return_pct'].mean():+.2f}%")
        print(f"  Win rate: {(trade_df['return_pct'] > 0).mean()*100:.1f}%")
        print(f"  Cumulative: {(trade_df['return_pct'].apply(lambda x: 1+x/100).prod()-1)*100:+.1f}%")

    # Save
    out = ROOT / "logs" / "vix_term_structure.csv"
    out.parent.mkdir(exist_ok=True)
    df[["date", "vix", "vix3m", "ratio", "spy", "fwd_5d", "fwd_20d", "fwd_60d"]].to_csv(out, index=False)
    print(f"\n  ✅ Saved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
