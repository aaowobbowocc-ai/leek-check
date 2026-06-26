"""
Revenue YoY × Sector segmentation

對每個產業跑 Revenue YoY 60d alpha
找 PEAD 最強的產業 → 可加 sector filter 給更高信心
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


def load_industry_map():
    df = pd.read_parquet(ROOT / "data" / "cache" / "finmind" / "extras" / "stock_info.parquet")
    return dict(zip(df["stock_id"], df["industry_category"]))


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


def collect_events(universe, market_median, ind_map):
    events = []
    for i, tk in enumerate(universe):
        ind = ind_map.get(tk, "Unknown")
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
            rev["yoy"].notna() & (rev["prior"] > 1e7)
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
                    "ticker": tk, "industry": ind,
                    "fwd_60d": fwd, "baseline_mean": bm, "baseline_std": bs,
                    "year": sd.year,
                })
        if (i + 1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def analyze_sector(events):
    print(f"\n  Total events: {len(events)}")
    print(f"  Industries: {events['industry'].nunique()}")

    # 篩 n >= 200 的產業
    ind_counts = events["industry"].value_counts()
    big_inds = ind_counts[ind_counts >= 200].index.tolist()
    print(f"  Industries with n >= 200: {len(big_inds)}")

    # 對每產業計算 alpha
    rows = []
    for ind in big_inds:
        sub = events[events["industry"] == ind]
        n = len(sub)
        sig = sub["fwd_60d"].mean()
        bm = sub["baseline_mean"].mean()
        bs = sub["baseline_std"].mean()
        alpha = sig - bm
        win = (sub["fwd_60d"] > 0).mean() * 100
        t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
        rows.append({"industry": ind, "n": n, "alpha": alpha,
                    "win_pct": win, "t": t})

    grid = pd.DataFrame(rows).sort_values("alpha", ascending=False)
    print(f"\n  📊 By industry (n >= 200):")
    print(f"  {'industry':<25} {'n':<6} {'alpha':<8} {'t':<7} {'win%':<6}")
    for _, r in grid.iterrows():
        t_str = f"{r['t']:+.2f}" if r['t'] else "n/a"
        verdict = "⭐" if r['alpha'] > 5 and (r['t'] or 0) > 5 else ""
        print(f"  {r['industry'][:25]:<25} {r['n']:<6} {r['alpha']:+.2f}%  {t_str:<7} {r['win_pct']:.1f}%  {verdict}")

    # 對 top 5 跑 OOS
    print(f"\n  📅 Top 5 OOS validation:")
    for _, r in grid.head(5).iterrows():
        ind = r["industry"]
        sub = events[events["industry"] == ind]
        print(f"\n  --- {ind} (full alpha {r['alpha']:+.2f}%) ---")
        for plabel, period_sub in [
            ("2020-22", sub[(sub["year"]>=2020) & (sub["year"]<=2022)]),
            ("2023-25", sub[sub["year"]>=2023]),
        ]:
            if len(period_sub) < 30: continue
            n = len(period_sub)
            alpha = period_sub["fwd_60d"].mean() - period_sub["baseline_mean"].mean()
            bs = period_sub["baseline_std"].mean()
            t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
            t_str = f"{t:+.2f}" if t else "n/a"
            verdict = "✅" if alpha > 1.5 and (t or 0) > 2 else "⚠️"
            print(f"    {plabel}: n={n}, alpha={alpha:+.2f}%, t={t_str} {verdict}")


def main():
    print("=" * 80)
    print("  Revenue YoY × Sector Segmentation")
    print("=" * 80)
    universe = load_universe()
    ind_map = load_industry_map()
    print(f"  Industry mapping: {len(ind_map)} tickers")
    market_median = compute_market_median()
    events = collect_events(universe, market_median, ind_map)
    analyze_sector(events)


if __name__ == "__main__":
    main()
