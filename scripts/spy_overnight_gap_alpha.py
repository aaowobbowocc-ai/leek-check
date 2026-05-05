"""
Cross-Market Overnight Gap: SPY 隔夜 → 0050 隔日 timing

假設:
  SPY 收盤 (US 04:00 TW time) 比 TW 收盤 (13:30 TW time) 晚 14.5 hours
  → SPY 隔夜的大幅變動 = TW 隔日 open 之前已 priced
  → 但 TW 散戶常 over-react (gap 太大)
  → 開盤 gap 後 intraday + N 日 mean reversion?

  特別: SPY 隔夜跌 X% → TW 隔日大跌 → 散戶恐慌 → 跌過頭 → reversion

訊號:
  spy_overnight = (今日 SPY close) / (昨日 SPY close) - 1
  Hypothesis 1: spy_overnight < -2% → TW 隔日進場做多 0050
  Hypothesis 2: spy_overnight > +2% → TW 隔日 fade (mean reversion)

Hold: 1d (T+1 close) / 5d / 20d
Cost: 0.05% one-way × 2 = 0.1% (0050 ETF 流動性極好)

對比:
  - 跟 BTH 0050 比
  - 跟「SPY 沒大跌」normal 期間 0050 fwd return 比
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
SPY_PATH = ROOT / "data" / "cache" / "yfinance" / "global" / "SPY_full.parquet"
TW_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "0050.parquet"

COST = 0.10  # round-trip 0050 ETF


def main():
    print("=" * 80)
    print("  Cross-Market Overnight Gap: SPY → 0050 隔日 timing")
    print("=" * 80)

    spy = pd.read_parquet(SPY_PATH)
    spy["date"] = pd.to_datetime(spy["date"]).dt.tz_localize(None)
    spy = spy.sort_values("date").reset_index(drop=True)
    spy["spy_overnight_ret"] = spy["close"].pct_change() * 100  # SPY today close vs yesterday close

    tw = pd.read_parquet(TW_PATH)
    tw["date"] = pd.to_datetime(tw["date"]).dt.tz_localize(None)
    tw = tw.sort_values("date").reset_index(drop=True)

    # Align: SPY close on date D corresponds to TW open on date D+1 (TW market closed when SPY closed)
    # We want: TW open at T+1 / fwd return → SPY close at T (so spy_T's overnight ret affects TW T+1)
    spy_signal = spy[["date", "spy_overnight_ret"]].copy()
    spy_signal["date"] = spy_signal["date"] + pd.Timedelta(days=1)  # SPY's "next day" maps to TW that day
    df = tw.merge(spy_signal, on="date", how="inner").sort_values("date").reset_index(drop=True)
    # df["date"] = TW trading day, df["spy_overnight_ret"] = SPY 變動 between previous TW close and current TW open
    # (approximately; ignoring weekends might miss some days)

    print(f"  Merged rows: {len(df):,} (TW trading days with SPY signal)")
    print(f"  Period: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")

    # Forward returns: from TW open (T) to TW close (T+N)
    for h in [1, 5, 20]:
        df[f"fwd_{h}d"] = (df["close"].shift(-h) / df["open"] - 1) * 100

    # Drop rows without forward returns
    df = df.dropna(subset=["spy_overnight_ret", "fwd_5d"]).reset_index(drop=True)

    print(f"\n  SPY overnight return distribution:")
    print(f"    p1: {df['spy_overnight_ret'].quantile(0.01):+.2f}%  "
          f"p5: {df['spy_overnight_ret'].quantile(0.05):+.2f}%  "
          f"median: {df['spy_overnight_ret'].median():+.2f}%  "
          f"p95: {df['spy_overnight_ret'].quantile(0.95):+.2f}%  "
          f"p99: {df['spy_overnight_ret'].quantile(0.99):+.2f}%")

    # Bucket by SPY overnight
    bucks = [
        ("Crash (< -3%)", df["spy_overnight_ret"] < -3),
        ("Big down (-3 ~ -2%)", (df["spy_overnight_ret"] >= -3) & (df["spy_overnight_ret"] < -2)),
        ("Mid down (-2 ~ -1%)", (df["spy_overnight_ret"] >= -2) & (df["spy_overnight_ret"] < -1)),
        ("Normal (-1 ~ +1%)", (df["spy_overnight_ret"] >= -1) & (df["spy_overnight_ret"] < 1)),
        ("Mid up (+1 ~ +2%)", (df["spy_overnight_ret"] >= 1) & (df["spy_overnight_ret"] < 2)),
        ("Big up (+2 ~ +3%)", (df["spy_overnight_ret"] >= 2) & (df["spy_overnight_ret"] < 3)),
        ("Surge (> +3%)", df["spy_overnight_ret"] >= 3),
    ]

    for hold in [1, 5, 20]:
        col = f"fwd_{hold}d"
        print(f"\n  === 0050 fwd {hold}d return by SPY overnight bucket ===")
        print(f"  {'Bucket':<22} {'n':>5} {'mean':>8} {'win%':>6} {'t':>6}")
        for label, mask in bucks:
            sub = df.loc[mask, col].dropna()
            if len(sub) < 5:
                print(f"  {label:<22} n={len(sub)} (太少)")
                continue
            t, p = stats.ttest_1samp(sub, 0, alternative="two-sided")
            sig = "✅" if abs(t) > 2 else ""
            print(f"  {label:<22} {len(sub):>5} {sub.mean():>+7.2f}% "
                  f"{(sub>0).mean()*100:>5.1f}% {t:>+5.2f}{sig}")

    # vs Normal baseline
    normal_mask = (df["spy_overnight_ret"] >= -1) & (df["spy_overnight_ret"] < 1)
    print(f"\n  === Excess vs Normal (-1~+1%) bucket ===")
    print(f"  {'Bucket':<22}", end="")
    for h in [1, 5, 20]:
        print(f"{'fwd '+str(h)+'d':>14}", end="")
    print()
    print(f"  {'-'*22}" + "  " + "-"*12 * 3)
    for label, mask in bucks:
        if "Normal" in label:
            continue
        line = f"  {label:<22}"
        for h in [1, 5, 20]:
            col = f"fwd_{h}d"
            target = df.loc[mask, col].dropna()
            normal = df.loc[normal_mask, col].dropna()
            if len(target) < 5:
                line += f"{'(n<5)':>14}"
                continue
            excess = target.mean() - normal.mean()
            t, p = stats.ttest_ind(target, normal, equal_var=False)
            sig = "✅" if abs(t) > 2 else ""
            line += f"  {excess:>+5.2f}pp(t={t:>+4.1f}){sig:>2}"
        print(line)

    # OOS Walk-Forward for crash + surge buckets
    print(f"\n  === OOS Walk-Forward: Big down (-3~-2%) fwd 5d ===")
    df["year"] = df["date"].dt.year
    big_down = df[(df["spy_overnight_ret"] >= -3) & (df["spy_overnight_ret"] < -2)]
    splits = [(2017, 2019), (2020, 2022), (2023, 2025)]
    for ys, ye in splits:
        sub = big_down[(big_down["year"] >= ys) & (big_down["year"] <= ye)]["fwd_5d"].dropna()
        if len(sub) < 3:
            print(f"  {ys}-{ye}: n={len(sub)} (太少)")
            continue
        print(f"  {ys}-{ye}: n={len(sub)}, mean {sub.mean():+.2f}%, win {(sub>0).mean()*100:.0f}%")

    print(f"\n  === OOS Walk-Forward: Crash (<-3%) fwd 5d ===")
    crash = df[df["spy_overnight_ret"] < -3]
    for ys, ye in splits:
        sub = crash[(crash["year"] >= ys) & (crash["year"] <= ye)]["fwd_5d"].dropna()
        if len(sub) < 3:
            print(f"  {ys}-{ye}: n={len(sub)} (太少)")
            continue
        print(f"  {ys}-{ye}: n={len(sub)}, mean {sub.mean():+.2f}%, win {(sub>0).mean()*100:.0f}%")

    # Save
    out = ROOT / "logs" / "spy_overnight_gap.csv"
    out.parent.mkdir(exist_ok=True)
    df[["date", "spy_overnight_ret", "open", "close", "fwd_1d", "fwd_5d", "fwd_20d"]].to_csv(out, index=False)
    print(f"\n  ✅ Saved {len(df)} obs to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
