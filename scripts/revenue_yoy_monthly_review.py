"""
Revenue YoY Portfolio Monthly Review

對比 paper trade 實際表現 vs backtest 預期 (+25.7%/yr Full / +23.3%/yr 1H)
偵測:
  - 實際 alpha 是否符合預期
  - Slippage 是否符合 0.78% round-trip 假設
  - Regime drift（是否在 STRONG_BULL/CRASH 表現不同）
  - Win rate 漂移
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
PAPER_LOG = ROOT / "data" / "paper_trades" / "revenue_yoy_paper.csv"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def load_paper() -> pd.DataFrame:
    if not PAPER_LOG.exists():
        return pd.DataFrame()
    return pd.read_csv(PAPER_LOG)


def main():
    print("=" * 76)
    print("  Revenue YoY Portfolio Monthly Review")
    print("=" * 76)

    paper = load_paper()
    if paper.empty:
        print("  📊 paper trade log 空白，先跑 paper_tracker 累積 closed positions")
        return

    closed = paper[paper["status"] == "closed"].copy()
    open_pos = paper[paper["status"] == "open"].copy()
    print(f"\n  Total records: {len(paper)} (closed: {len(closed)}, open: {len(open_pos)})")

    if closed.empty:
        print("\n  ⚠️ 無已平倉紀錄。最早的 open position 還在 hold 期內")
        if not open_pos.empty:
            print("  Open positions 進度:")
            for _, row in open_pos.iterrows():
                entry_dt = pd.to_datetime(row["entry_date"])
                target_dt = pd.to_datetime(row["scheduled_exit_date"])
                today = pd.Timestamp(date.today())
                if pd.notna(entry_dt) and pd.notna(target_dt):
                    progress = (today - entry_dt).days / max(1, (target_dt - entry_dt).days)
                    progress = min(1.0, max(0.0, progress))
                    print(f"    {row['ticker']}: {progress*100:.0f}% complete (entry {entry_dt.date()})")
        return

    closed["net_pct"] = pd.to_numeric(closed["net_pct"], errors="coerce")
    closed["vs_0050_alpha"] = pd.to_numeric(closed["vs_0050_alpha"], errors="coerce")
    closed["actual_exit_date"] = pd.to_datetime(closed["actual_exit_date"])
    closed["entry_date"] = pd.to_datetime(closed["entry_date"])

    # Aggregate stats
    print(f"\n  === Aggregate Stats (n={len(closed)}) ===")
    print(f"  Mean net return: {closed['net_pct'].mean():+.2f}%/trade")
    print(f"  Median: {closed['net_pct'].median():+.2f}%")
    print(f"  Win rate: {(closed['net_pct'] > 0).mean()*100:.1f}%")
    print(f"  Mean alpha vs 0050: {closed['vs_0050_alpha'].mean():+.2f}pp")
    print(f"  Cumulative net: {(closed['net_pct'].apply(lambda x: 1+x/100).prod()-1)*100:+.1f}%")
    print(f"  Cumulative alpha: {(closed['vs_0050_alpha'].apply(lambda x: 1+x/100).prod()-1)*100:+.1f}pp")

    # Backtest expectations
    print(f"\n  === vs Backtest Expectations ===")
    print(f"  Backtest L4 Full 2020-2025 CAGR: +25.7%")
    print(f"  Backtest L4 1H 2020-2022 CAGR: +23.3% (alpha vs 0050 +15.5pp)")
    print(f"  Backtest L4 2H 2023-2025 CAGR: +31.5% (alpha vs 0050 -5.8pp)")

    # Annualized paper performance estimate
    n_trades = len(closed)
    if n_trades >= 5:
        period_days = (closed["actual_exit_date"].max() - closed["entry_date"].min()).days
        if period_days > 0:
            cum_ret = closed["net_pct"].apply(lambda x: 1+x/100).prod() - 1
            ann_factor = 365 / period_days
            cagr_estimate = (1 + cum_ret) ** ann_factor - 1
            print(f"\n  📊 Paper trade annualized estimate (n={n_trades}, period={period_days}d):")
            print(f"  Estimated CAGR: {cagr_estimate*100:+.1f}% (vs backtest +25.7%)")
            cum_alpha = closed["vs_0050_alpha"].apply(lambda x: 1+x/100).prod() - 1
            ann_alpha = (1 + cum_alpha) ** ann_factor - 1
            print(f"  Estimated alpha vs 0050: {ann_alpha*100:+.1f}pp/yr")

    # Slippage analysis
    print(f"\n  === Slippage Sanity Check ===")
    if "gross_pct" in closed.columns:
        gross = pd.to_numeric(closed["gross_pct"], errors="coerce")
        net = closed["net_pct"]
        actual_cost = (gross - net).mean()
        print(f"  Assumed cost: 0.78%")
        print(f"  Implied actual cost (gross - net): {actual_cost:.2f}%")

    # Recent performance (last 30 days)
    print(f"\n  === Last 30 Days Performance ===")
    cutoff = pd.Timestamp(date.today()) - pd.Timedelta(days=30)
    recent = closed[closed["actual_exit_date"] >= cutoff]
    if recent.empty:
        print(f"  📅 過去 30 日無平倉")
    else:
        print(f"  n={len(recent)}, mean net {recent['net_pct'].mean():+.2f}%, "
              f"alpha {recent['vs_0050_alpha'].mean():+.2f}pp")

    # Drift detection
    print(f"\n  === Drift Detection ===")
    expected_alpha_pp = 4.0  # backtest +4pp Full vs 0050
    actual_alpha_pp = closed["vs_0050_alpha"].mean()
    drift_pp = actual_alpha_pp - expected_alpha_pp
    if abs(drift_pp) < 2:
        print(f"  ✅ Alpha 在預期範圍內（actual {actual_alpha_pp:+.2f} vs expected {expected_alpha_pp:+.1f}, diff {drift_pp:+.2f}pp）")
    elif drift_pp < -3:
        print(f"  🚨 Alpha 顯著低於預期 ({drift_pp:+.2f}pp 落差)，"
              f"可能 regime change 或 backtest over-fit")
    elif drift_pp > 3:
        print(f"  🌟 Alpha 顯著高於預期 ({drift_pp:+.2f}pp，可能 sample 太小")

    # Top + worst trades
    print(f"\n  === Top 5 Best ===")
    top5 = closed.nlargest(5, "net_pct")[["ticker", "entry_date", "net_pct", "vs_0050_alpha"]]
    print(top5.to_string(index=False))
    print(f"\n  === Bottom 5 Worst ===")
    bot5 = closed.nsmallest(5, "net_pct")[["ticker", "entry_date", "net_pct", "vs_0050_alpha"]]
    print(bot5.to_string(index=False))


if __name__ == "__main__":
    main()
