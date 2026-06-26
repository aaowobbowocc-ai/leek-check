"""
驗證 Revenue YoY 月份效應是否跨年 robust

User 提問：「每月營收每年不都差很多嗎？」
→ 4 月 alpha +7.50% 是平均值，可能某幾年 dominate

驗證：每月 × 每年的 alpha matrix
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
HOLD = 60


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def compute_market_median():
    print("  計算市場 median...")
    all_yoy = []
    for p in CACHE.glob("TaiwanStockMonthRevenue_*.parquet"):
        try:
            r = pd.read_parquet(p)
            if len(r) < 24: continue
            r = r.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
            r["prior"] = r["revenue"].shift(12)
            r["yoy"] = (r["revenue"] / r["prior"] - 1) * 100
            r = r[r["prior"] > 1e7]
            if r.empty: continue
            r["date"] = pd.to_datetime(r["date"])
            r2 = r[r["yoy"].abs() < 500][["date", "yoy"]]
            all_yoy.append(r2)
        except: continue
    df = pd.concat(all_yoy, ignore_index=True)
    df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym")["yoy"].median().to_dict()


def collect_events(universe, market_median):
    events = []
    for i, tk in enumerate(universe):
        rp = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
        if not rp.exists(): continue
        try: rev = pd.read_parquet(rp)
        except: continue
        if rev.empty or len(rev) < 24: continue
        rev = rev.sort_values(["revenue_year","revenue_month"]).reset_index(drop=True)
        rev["prior"] = rev["revenue"].shift(12)
        rev["yoy"] = (rev["revenue"]/rev["prior"]-1)*100
        rev["date"] = pd.to_datetime(rev["date"])
        rev["ym"] = rev["date"].dt.to_period("M")
        rev["mkt_med"] = rev["ym"].map(market_median)
        rev["excess"] = rev["yoy"] - rev["mkt_med"]
        triggers = rev[(rev["excess"]>30) & (rev["yoy"]<200) & rev["yoy"].notna()
                       & (rev["prior"]>1e7)]
        if triggers.empty: continue

        pp = TW_CACHE / f"{tk}.parquet"
        if not pp.exists() or pp.stat().st_size < 500: continue
        try: px = pd.read_parquet(pp)
        except: continue
        if px.empty or len(px) < HOLD + 60: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px_idx) - HOLD - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px_idx) - HOLD), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            if px_idx.iloc[j] > 0:
                baseline.append((px_idx.iloc[j+HOLD]/px_idx.iloc[j]-1)*100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        for _, row in triggers.iterrows():
            sd = row["date"]
            future = px_idx[px_idx.index > sd]
            if len(future) <= HOLD: continue
            entry = future.iloc[0]
            if entry > 0:
                fwd = (future.iloc[HOLD]/entry-1)*100
                events.append({
                    "ticker": tk, "fwd_60d": fwd,
                    "baseline_mean": bm, "baseline_std": bs,
                    "rev_month": int(row["revenue_month"]),
                    "rev_year": int(row["revenue_year"]),
                })
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def analyze(events):
    print(f"\n  Total events: {len(events)}")

    # Matrix: rev_month × rev_year
    print(f"\n  📊 Alpha matrix (月份 × 年份):")
    print(f"  {'month':<6}", end="")
    years = sorted(events["rev_year"].unique())
    for y in years:
        print(f"{y:<8}", end="")
    print()

    for m in range(1, 13):
        print(f"  m={m:<4}", end="")
        for y in years:
            sub = events[(events["rev_month"] == m) & (events["rev_year"] == y)]
            if len(sub) < 30:
                print(f"{'--':<8}", end="")
                continue
            alpha = sub["fwd_60d"].mean() - sub["baseline_mean"].mean()
            print(f"{alpha:+.1f}%   ", end="")
        print()

    # 7 月 deep dive (signal 反向)
    print(f"\n  🔍 m=7 by year detail:")
    print(f"  {'year':<6} {'n':<5} {'alpha':<8} {'win%':<6}")
    for y in years:
        sub = events[(events["rev_month"] == 7) & (events["rev_year"] == y)]
        if len(sub) < 20: continue
        n = len(sub)
        alpha = sub["fwd_60d"].mean() - sub["baseline_mean"].mean()
        win = (sub["fwd_60d"] > 0).mean() * 100
        print(f"  {y:<6} {n:<5} {alpha:+.2f}%  {win:.1f}%")

    # 4 月 deep dive (signal 最強)
    print(f"\n  🔍 m=4 by year detail:")
    for y in years:
        sub = events[(events["rev_month"] == 4) & (events["rev_year"] == y)]
        if len(sub) < 20: continue
        n = len(sub)
        alpha = sub["fwd_60d"].mean() - sub["baseline_mean"].mean()
        win = (sub["fwd_60d"] > 0).mean() * 100
        print(f"  {y:<6} {n:<5} {alpha:+.2f}%  {win:.1f}%")

    # Monthly avg with std across years
    print(f"\n  📊 Monthly avg + std across years (cross-year robustness):")
    print(f"  {'month':<6} {'avg_alpha':<11} {'std_across_yr':<14} {'min_yr':<10} {'max_yr':<10}")
    for m in range(1, 13):
        m_subs = []
        for y in years:
            sub = events[(events["rev_month"] == m) & (events["rev_year"] == y)]
            if len(sub) < 30: continue
            alpha_y = sub["fwd_60d"].mean() - sub["baseline_mean"].mean()
            m_subs.append(alpha_y)
        if len(m_subs) < 3: continue
        avg = np.mean(m_subs)
        std = np.std(m_subs)
        print(f"  m={m:<4} {avg:+.2f}%      ±{std:.2f}%        {min(m_subs):+.2f}%   {max(m_subs):+.2f}%")


def main():
    print("=" * 80)
    print("  Revenue YoY 月份 × 年份 alpha matrix")
    print("=" * 80)
    universe = load_universe()
    market_median = compute_market_median()
    events = collect_events(universe, market_median)
    analyze(events)


if __name__ == "__main__":
    main()
