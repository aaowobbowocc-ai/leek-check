"""
月底 / 季底效應 Backtest（Window Dressing）

假設:
  基金經理人月底/季底為了報表好看，會買「強勢股」
  → 月底前 5 日 momentum 強的股票被推高
  → 月初 5 日 reversion (基金已不再買盤、實質價值揭露)

訊號:
  月底最後 3 日內: 過去 5 日漲幅 top decile
  進場: 月底最後一日 close
  Exit: T+5 / T+10 / T+20 days

Universe: 全市場個股 9 年
Cost: 0.78%
Test:
  - 月底 vs 季底（季底因財報效應更強？）
  - Top decile vs random baseline
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
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
COST = 0.78
HOLDS = [5, 10, 20]
MIN_LIQUIDITY = 1e8  # 1 億/日


def load_px(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    if len(df) < 100:
        return pd.DataFrame()
    df["pct"] = df["close"].pct_change() * 100
    df["mom_5d"] = df["close"].pct_change(5) * 100
    df["dv"] = df["close"] * df["volume"]
    df["dv_60d"] = df["dv"].rolling(60).mean()
    df["year_month"] = df["date"].dt.to_period("M")
    return df


def is_month_end(d: pd.Timestamp, month_end_dates: dict) -> bool:
    """Check if d is one of last 3 trading days of its month."""
    ym = d.to_period("M")
    if ym not in month_end_dates:
        return False
    return d in month_end_dates[ym][-3:]


def is_quarter_end(d: pd.Timestamp, month_end_dates: dict) -> bool:
    """Last 3 trading days of quarter-end month (Mar/Jun/Sep/Dec)"""
    if d.month not in [3, 6, 9, 12]:
        return False
    return is_month_end(d, month_end_dates)


def main():
    print("=" * 80)
    print("  月底 / 季底 Window Dressing Effect Backtest")
    print(f"  Hold {HOLDS}d, COST {COST}%, MIN liquidity {MIN_LIQUIDITY/1e8:.0f}億/日")
    print("=" * 80)

    universe = sorted([
        p.stem for p in TW_CACHE.glob("*.parquet")
        if p.stem.isdigit() and len(p.stem) == 4 and not p.stem.startswith("00")
    ])
    print(f"\n  Universe: {len(universe)} tickers")

    # Build month-end calendar from a representative ticker
    print("  Building month-end calendar...")
    cal = load_px("2330")
    if cal.empty:
        print("  ❌ 無 calendar 資料")
        return
    month_end_dates: dict = {}
    for ym, group in cal.groupby("year_month"):
        month_end_dates[ym] = sorted(group["date"].tolist())

    print(f"  Calendar: {len(month_end_dates)} months ({min(month_end_dates)} ~ {max(month_end_dates)})")

    # Scan events: month-end last 3 days, top decile mom_5d
    print(f"\n  Scanning events...")
    all_events = []
    for i, tk in enumerate(universe):
        px = load_px(tk)
        if px.empty:
            continue
        # For each month, find last 3 days
        for idx in range(20, len(px) - max(HOLDS) - 1):
            row = px.iloc[idx]
            if not is_month_end(row["date"], month_end_dates):
                continue
            if pd.isna(row["mom_5d"]) or pd.isna(row["dv_60d"]):
                continue
            if row["dv_60d"] < MIN_LIQUIDITY:
                continue

            # Quintile bucket within month (we'll bucket later globally)
            entry_close = row["close"]
            rec = {
                "ticker": tk,
                "date": row["date"],
                "year_month": str(row["year_month"]),
                "mom_5d": float(row["mom_5d"]),
                "dv_60d_yi": float(row["dv_60d"] / 1e8),
                "is_qe": is_quarter_end(row["date"], month_end_dates),
                "entry": entry_close,
            }
            for hold in HOLDS:
                exit_p = px.iloc[idx + hold]["close"]
                rec[f"fwd_{hold}d"] = (exit_p / entry_close - 1) * 100 - COST
            all_events.append(rec)
        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(universe)}] events: {len(all_events):,}")

    df = pd.DataFrame(all_events)
    if df.empty:
        print("  ❌ 無事件")
        return

    print(f"\n  Total month-end events: {len(df):,}")

    # Bucket by mom_5d quintile per month
    print(f"\n  === By mom_5d quintile (top = strongest at month-end) ===")
    df["mom_quintile"] = df.groupby("year_month")["mom_5d"].transform(
        lambda x: pd.qcut(x, 5, labels=["Q1-weak", "Q2", "Q3", "Q4", "Q5-strong"], duplicates="drop")
    )
    for hold in HOLDS:
        col = f"fwd_{hold}d"
        print(f"\n  fwd {hold}d:")
        print(f"  {'Quintile':<12} {'n':>6} {'mean':>8} {'win%':>6} {'t-stat':>7}")
        grp = df.groupby("mom_quintile", observed=True)[col]
        for q, sub in grp:
            sub = sub.dropna()
            if len(sub) < 30:
                continue
            t, p = stats.ttest_1samp(sub, 0, alternative="two-sided")
            print(f"  {str(q):<12} {len(sub):>6} {sub.mean():>+7.2f}% {(sub>0).mean()*100:>5.1f}% {t:>+6.2f}")

    # Reversion alpha: Q5 (strongest) → fwd return - Q1 (weakest) → fwd return
    print(f"\n  === Reversion Alpha (Q5 - Q1) ===")
    for hold in HOLDS:
        col = f"fwd_{hold}d"
        q5 = df[df["mom_quintile"] == "Q5-strong"][col].dropna()
        q1 = df[df["mom_quintile"] == "Q1-weak"][col].dropna()
        if len(q5) < 30 or len(q1) < 30:
            continue
        diff = q5.mean() - q1.mean()
        # Two-sample t
        t_diff, p_diff = stats.ttest_ind(q5, q1, equal_var=False)
        sig = "✅" if abs(t_diff) > 2 else "❌"
        print(f"  fwd {hold}d: Q5 mean {q5.mean():+.2f}% vs Q1 {q1.mean():+.2f}%  "
              f"diff {diff:+.2f}pp  t={t_diff:+.2f}  p={p_diff:.4f}{sig}")

    # Quarter-end vs Month-end
    print(f"\n  === Quarter-end vs Regular Month-end (fwd 20d) ===")
    for q_label, q_filter in [("月底 (all)", df["is_qe"] == False), ("季底", df["is_qe"] == True)]:
        sub = df[q_filter]
        for quintile in ["Q5-strong", "Q1-weak"]:
            qsub = sub[sub["mom_quintile"] == quintile]["fwd_20d"].dropna()
            if len(qsub) < 30:
                continue
            print(f"  {q_label:<12} {quintile:<10} n={len(qsub):>5}  mean={qsub.mean():+.2f}%")

    # Save
    out = ROOT / "logs" / "month_end_effect_events.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n  ✅ Saved {len(df)} events to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
