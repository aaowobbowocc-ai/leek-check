"""
SPY-QQQ Pair Daily Backtest (ES-NQ proxy)

ES = E-mini S&P 500 future, NQ = E-mini Nasdaq 100 future
SPY/QQQ 是對應的現貨 ETF，相關性 ~0.99 with ES/NQ。

對個人投資者:
  - SPY/QQQ pair 用 cash ETF 直接做（複委託即可，無期貨）
  - 或 ES/NQ micro 期貨（IB 帳戶，槓桿高）
  - Spread 動態本質一致

回測 2010-2025 (15 年):
  spread = log(QQQ) - log(SPY × hedge_ratio)
  spread mean reverts due to:
    - 共同 fundamental driver (US economy, Fed)
    - Sector weight differences (Nasdaq tech-heavy vs S&P 多元)
    - 交易資金 ETF rebalance dislocations

訊號:
  60 日 rolling z-score
  |z| > 2.0 進場
  |z| < 0.5 平倉
  20 日 timeout

Alpha 來源:
  Market-neutral mean reversion of NQ vs SP relative valuation
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

ROLLING_WINDOW = 60
Z_ENTRY = 2.0
Z_EXIT = 0.5
TIMEOUT_DAYS = 20
COST_PER_LEG = 0.05  # 0.05% one-way per leg (cash ETF + 複委託)


def load(name: str) -> pd.DataFrame:
    df = pd.read_parquet(GLOBAL / name)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def main():
    print("=" * 80)
    print("  SPY-QQQ Pair Daily Backtest (ES-NQ proxy)")
    print(f"  |z| > {Z_ENTRY} 進場 / |z| < {Z_EXIT} 出場 / timeout {TIMEOUT_DAYS}d")
    print(f"  Cost: {COST_PER_LEG}% one-way × 2 legs = {COST_PER_LEG*4}% per round trip")
    print("=" * 80)

    spy = load("SPY_full.parquet")[["date", "close"]].rename(columns={"close": "spy"})
    qqq = load("QQQ.parquet")[["date", "close"]].rename(columns={"close": "qqq"})
    df = spy.merge(qqq, on="date").sort_values("date").reset_index(drop=True)
    df = df[df["date"] >= "2010-01-01"].reset_index(drop=True)

    # Spread + z-score (rolling OLS-free, just log ratio)
    df["log_spy"] = np.log(df["spy"])
    df["log_qqq"] = np.log(df["qqq"])
    df["spread"] = df["log_qqq"] - df["log_spy"]
    df["spread_mean"] = df["spread"].rolling(ROLLING_WINDOW).mean()
    df["spread_std"] = df["spread"].rolling(ROLLING_WINDOW).std()
    df["z"] = (df["spread"] - df["spread_mean"]) / df["spread_std"]

    print(f"\n  Period: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
    print(f"  Total days: {len(df):,}")
    print(f"  Correlation (log returns): {df['log_qqq'].corr(df['log_spy']):.3f}")

    # Walk through and simulate trades
    trades = []
    in_pos = False
    pos_dir = 0  # +1 = long QQQ short SPY (z < -2), -1 = short QQQ long SPY (z > 2)
    entry_idx = None

    for i in range(ROLLING_WINDOW, len(df) - 1):
        row = df.iloc[i]
        z = row["z"]
        if pd.isna(z):
            continue

        if not in_pos:
            if z > Z_ENTRY:
                in_pos = True
                pos_dir = -1
                entry_idx = i
            elif z < -Z_ENTRY:
                in_pos = True
                pos_dir = +1
                entry_idx = i
        else:
            elapsed = i - entry_idx
            if abs(z) < Z_EXIT or elapsed >= TIMEOUT_DAYS:
                # PnL: long-short spread move
                spy0 = df.iloc[entry_idx]["spy"]
                qqq0 = df.iloc[entry_idx]["qqq"]
                spy1 = row["spy"]
                qqq1 = row["qqq"]
                spy_ret = (spy1 / spy0 - 1) * 100
                qqq_ret = (qqq1 / qqq0 - 1) * 100
                if pos_dir == +1:  # long QQQ, short SPY
                    gross = qqq_ret - spy_ret
                else:  # short QQQ, long SPY
                    gross = spy_ret - qqq_ret
                # Cost: 4 transactions (entry: long+short legs, exit: long+short legs)
                net = gross - COST_PER_LEG * 4
                trades.append({
                    "entry_date": df.iloc[entry_idx]["date"],
                    "exit_date": row["date"],
                    "hold_days": elapsed,
                    "z_entry": df.iloc[entry_idx]["z"],
                    "z_exit": z,
                    "direction": "long_QQQ" if pos_dir == +1 else "short_QQQ",
                    "spy_ret": spy_ret,
                    "qqq_ret": qqq_ret,
                    "gross_pct": gross,
                    "net_pct": net,
                    "exit_reason": "z_revert" if abs(z) < Z_EXIT else "timeout",
                })
                in_pos = False

    if not trades:
        print("  ❌ 無觸發交易")
        return

    tdf = pd.DataFrame(trades)
    tdf["year"] = tdf["entry_date"].dt.year

    print(f"\n  === Trade Stats (Full sample 2010-2025) ===")
    print(f"  Total trades: {len(tdf)}")
    print(f"  Mean net return: {tdf['net_pct'].mean():+.3f}%/trade")
    print(f"  Median: {tdf['net_pct'].median():+.3f}%/trade")
    print(f"  Win rate: {(tdf['net_pct'] > 0).mean()*100:.1f}%")
    print(f"  Cumulative net (compounded): "
          f"{(tdf['net_pct'].apply(lambda x: 1+x/100).prod()-1)*100:+.1f}%")
    print(f"  Avg hold days: {tdf['hold_days'].mean():.1f}")
    print(f"  Exit reasons: {tdf['exit_reason'].value_counts().to_dict()}")

    t, p = stats.ttest_1samp(tdf["net_pct"], 0, alternative="greater")
    print(f"  t-stat: {t:+.2f}, p-value: {p:.5f}")

    # Annualized return
    yrs = (tdf["exit_date"].iloc[-1] - tdf["entry_date"].iloc[0]).days / 365.25
    cum = tdf["net_pct"].apply(lambda x: 1+x/100).prod() - 1
    cagr = (1 + cum) ** (1 / yrs) - 1
    print(f"  Period: {yrs:.1f} years")
    print(f"  CAGR: {cagr*100:+.2f}%/yr")

    # By direction
    print(f"\n  === By Direction ===")
    for d in ["long_QQQ", "short_QQQ"]:
        sub = tdf[tdf["direction"] == d]["net_pct"]
        if len(sub) > 0:
            print(f"  {d}: n={len(sub)}, mean={sub.mean():+.2f}%, win={(sub>0).mean()*100:.1f}%")

    # Year by year
    print(f"\n  === Year-by-Year ===")
    print(f"  {'Year':<6} {'n':>4} {'mean':>8} {'cum':>8} {'win%':>6}")
    for yr in sorted(tdf["year"].unique()):
        sub = tdf[tdf["year"] == yr]
        cum_yr = (sub["net_pct"].apply(lambda x: 1+x/100).prod() - 1) * 100
        print(f"  {yr:<6} {len(sub):>4} {sub['net_pct'].mean():>+7.2f}% {cum_yr:>+7.1f}% {(sub['net_pct']>0).mean()*100:>5.1f}%")

    # OOS split
    print(f"\n  === OOS Walk-Forward ===")
    splits = [
        ("2010-2014", 2010, 2014),
        ("2015-2019", 2015, 2019),
        ("2020-2025", 2020, 2025),
    ]
    print(f"  {'Period':<12} {'n':>4} {'mean':>8} {'cum':>8} {'win%':>6} {'CAGR':>7}")
    for label, ys, ye in splits:
        sub = tdf[(tdf["year"] >= ys) & (tdf["year"] <= ye)]
        if len(sub) < 2:
            continue
        cum_p = (sub["net_pct"].apply(lambda x: 1+x/100).prod() - 1) * 100
        yrs_p = (sub["exit_date"].iloc[-1] - sub["entry_date"].iloc[0]).days / 365.25
        cagr_p = ((1 + cum_p / 100) ** (1 / yrs_p) - 1) * 100 if yrs_p > 0 else 0
        win_p = (sub["net_pct"] > 0).mean() * 100
        print(f"  {label:<12} {len(sub):>4} {sub['net_pct'].mean():>+7.2f}% {cum_p:>+7.1f}% {win_p:>5.1f}% {cagr_p:>+6.2f}%")

    # Compare to BTH SPY
    print(f"\n  === vs BTH SPY (same period) ===")
    spy_start = df[df["date"] >= "2010-01-01"]["spy"].iloc[0]
    spy_end = df["spy"].iloc[-1]
    spy_total = (spy_end / spy_start - 1) * 100
    spy_yrs = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    spy_cagr = ((spy_end / spy_start) ** (1 / spy_yrs) - 1) * 100
    print(f"  SPY BTH: total {spy_total:+.1f}%, CAGR {spy_cagr:+.2f}%/yr")
    print(f"  Pair:    total {cum*100:+.1f}%, CAGR {cagr*100:+.2f}%/yr")
    print(f"  → 結論: pair {'贏' if cagr > spy_cagr/100 else '輸'} BTH SPY")

    # Save
    out = ROOT / "logs" / "spy_qqq_pair_trades.csv"
    out.parent.mkdir(exist_ok=True)
    tdf.to_csv(out, index=False)
    print(f"\n  ✅ Saved {len(tdf)} trades to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
