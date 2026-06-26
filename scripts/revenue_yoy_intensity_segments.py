"""
Revenue YoY 強度梯度分析

Hypothesis: 30-50% surprise vs 100%+ surprise alpha 是否不同？

Buckets:
  - 30-50%   (mild surprise)
  - 50-100%  (strong surprise)
  - 100-200% (explosive)

每 bucket 跑：alpha + OOS + MCPT
找最強強度區間
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
HOLD_DAYS = 60
N_PERMUTE = 1000


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
        rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
        rev["prior"] = rev["revenue"].shift(12)
        rev["yoy"] = (rev["revenue"] / rev["prior"] - 1) * 100
        rev["date"] = pd.to_datetime(rev["date"])
        rev["ym"] = rev["date"].dt.to_period("M")
        rev["mkt_med"] = rev["ym"].map(market_median)
        rev["excess"] = rev["yoy"] - rev["mkt_med"]
        triggers = rev[
            (rev["excess"] > 30) &
            (rev["yoy"] > 0) &
            (rev["yoy"] < 200) &
            rev["yoy"].notna() &
            (rev["prior"] > 1e7)
        ]
        if triggers.empty: continue

        pp = TW_CACHE / f"{tk}.parquet"
        if not pp.exists() or pp.stat().st_size < 500: continue
        try: px = pd.read_parquet(pp)
        except: continue
        if px.empty or len(px) < HOLD_DAYS + 60: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px_idx) - HOLD_DAYS - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px_idx) - HOLD_DAYS), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            if px_idx.iloc[j] > 0:
                baseline.append((px_idx.iloc[j + HOLD_DAYS] / px_idx.iloc[j] - 1) * 100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        for _, row in triggers.iterrows():
            sd = row["date"]
            future = px_idx[px_idx.index > sd]
            if len(future) <= HOLD_DAYS: continue
            entry = future.iloc[0]
            if entry > 0:
                fwd = (future.iloc[HOLD_DAYS] / entry - 1) * 100
                events.append({
                    "ticker": tk, "signal_date": sd, "fwd_60d": fwd,
                    "baseline_mean": bm, "baseline_std": bs,
                    "yoy": row["yoy"], "excess": row["excess"],
                    "year": sd.year,
                })
        if (i + 1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def segment_yoy(events):
    """Segment by YoY 強度"""
    print(f"\n  Total events: {len(events)}")
    print(f"  YoY range: {events['yoy'].min():.0f} ~ {events['yoy'].max():.0f}")

    buckets = [
        ("30-50%", events[(events["yoy"] >= 30) & (events["yoy"] < 50)]),
        ("50-100%", events[(events["yoy"] >= 50) & (events["yoy"] < 100)]),
        ("100-200%", events[(events["yoy"] >= 100) & (events["yoy"] < 200)]),
    ]
    print(f"\n  📊 By YoY 強度:")
    print(f"  {'bucket':<12} {'n':<7} {'alpha':<8} {'t':<7} {'win%':<6}")
    for label, sub in buckets:
        if len(sub) < 100: continue
        n = len(sub)
        sig = sub["fwd_60d"].mean()
        bm = sub["baseline_mean"].mean()
        bs = sub["baseline_std"].mean()
        alpha = sig - bm
        win = (sub["fwd_60d"] > 0).mean() * 100
        t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
        t_str = f"{t:+.2f}" if t else "n/a"
        print(f"  {label:<12} {n:<7} {alpha:+.2f}%  {t_str:<7} {win:.1f}%")

    # OOS for each
    print(f"\n  📅 OOS each:")
    for label, sub in buckets:
        if len(sub) < 100: continue
        print(f"\n  --- {label} ---")
        for plabel, period_sub in [
            ("2017-2019", sub[sub["year"] <= 2019]),
            ("2020-2022", sub[(sub["year"] >= 2020) & (sub["year"] <= 2022)]),
            ("2023-2025", sub[sub["year"] >= 2023]),
        ]:
            if len(period_sub) < 50: continue
            n = len(period_sub)
            alpha = period_sub["fwd_60d"].mean() - period_sub["baseline_mean"].mean()
            bs = period_sub["baseline_std"].mean()
            t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
            t_str = f"{t:+.2f}" if t else "n/a"
            verdict = "✅" if alpha > 1.5 and (t or 0) > 2 else "⚠️"
            print(f"    {plabel}: n={n}, alpha={alpha:+.2f}%, t={t_str} {verdict}")


def main():
    print("=" * 80)
    print("  Revenue YoY 強度梯度分析 (30-50% / 50-100% / 100-200%)")
    print("=" * 80)
    universe = load_universe()
    market_median = compute_market_median()
    events = collect_events(universe, market_median)
    segment_yoy(events)


if __name__ == "__main__":
    main()
