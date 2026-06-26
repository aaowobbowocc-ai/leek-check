"""
Revenue YoY Hold Period Optimization

對每個 trigger 計算 hold = 20, 40, 60, 90, 120, 180, 252 日的 alpha
找最大 risk-adjusted alpha 的 hold period

用 alpha / vol 作為 sharpe-like metric
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
HOLDS = [20, 40, 60, 90, 120, 180, 252]


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
    max_hold = max(HOLDS)
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
            (rev["yoy"] > 0) & (rev["yoy"] < 200) &
            rev["yoy"].notna() &
            (rev["prior"] > 1e7)
        ]
        if triggers.empty: continue

        pp = TW_CACHE / f"{tk}.parquet"
        if not pp.exists() or pp.stat().st_size < 500: continue
        try: px = pd.read_parquet(pp)
        except: continue
        if px.empty or len(px) < max_hold + 60: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        # baseline samples for each hold
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px_idx) - max_hold - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px_idx) - max_hold), size=n_base, replace=False)
        baseline = {h: [] for h in HOLDS}
        for j in bidx:
            if px_idx.iloc[j] > 0:
                for h in HOLDS:
                    baseline[h].append((px_idx.iloc[j + h] / px_idx.iloc[j] - 1) * 100)

        for _, row in triggers.iterrows():
            sd = row["date"]
            future = px_idx[px_idx.index > sd]
            if len(future) <= max_hold: continue
            entry = future.iloc[0]
            if entry <= 0: continue
            event = {"ticker": tk, "year": sd.year}
            for h in HOLDS:
                event[f"fwd_{h}d"] = (future.iloc[h] / entry - 1) * 100
                event[f"base_{h}d"] = np.mean(baseline[h]) if baseline[h] else 0
                event[f"base_std_{h}d"] = np.std(baseline[h]) if baseline[h] else 0
            events.append(event)
        if (i + 1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def analyze_holds(events):
    print(f"\n  Total events: {len(events)}")
    print(f"\n  📊 Alpha by hold period:")
    print(f"  {'hold':<6} {'n':<7} {'mean':<8} {'baseline':<10} {'alpha':<8} {'t':<7} {'sharpe-like':<10}")
    print(f"  {'-'*65}")
    rows = []
    for h in HOLDS:
        col = f"fwd_{h}d"
        bcol = f"base_{h}d"
        scol = f"base_std_{h}d"
        sub = events.dropna(subset=[col, bcol])
        n = len(sub)
        mean = sub[col].mean()
        bm = sub[bcol].mean()
        bs = sub[scol].mean()
        alpha = mean - bm
        t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
        sig_std = sub[col].std()
        sharpe = alpha / sig_std * np.sqrt(252/h) if sig_std > 0 else 0  # annualized sharpe-like
        t_str = f"{t:+.2f}" if t else "n/a"
        print(f"  {h:<6} {n:<7} {mean:+.2f}%   {bm:+.2f}%      {alpha:+.2f}%   {t_str:<7} {sharpe:.2f}")
        rows.append({"hold": h, "n": n, "alpha": alpha, "t": t, "sharpe_like": sharpe})

    # OOS
    print(f"\n  📅 OOS each hold:")
    for h in HOLDS:
        col = f"fwd_{h}d"
        bcol = f"base_{h}d"
        scol = f"base_std_{h}d"
        for plabel, sub in [
            ("2020-22", events[(events["year"]>=2020)&(events["year"]<=2022)]),
            ("2023-25", events[events["year"]>=2023]),
        ]:
            if len(sub) < 100: continue
            n = len(sub)
            alpha = sub[col].mean() - sub[bcol].mean()
            bs = sub[scol].mean()
            t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
            t_str = f"{t:+.2f}" if t else "n/a"
            verdict = "✅" if alpha > 1.5 and (t or 0) > 2 else "⚠️"
            print(f"    {h}d {plabel}: n={n}, alpha={alpha:+.2f}%, t={t_str} {verdict}")


def main():
    print("=" * 80)
    print("  Revenue YoY Hold Period Optimization")
    print("=" * 80)
    universe = load_universe()
    market_median = compute_market_median()
    events = collect_events(universe, market_median)
    analyze_holds(events)


if __name__ == "__main__":
    main()
