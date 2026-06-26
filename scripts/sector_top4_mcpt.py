"""
Sector Top 4 MCPT 驗證
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

TOP4 = ["資訊服務業", "半導體業", "通信網路業", "電腦及週邊設備業"]


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_industry_map():
    df = pd.read_parquet(ROOT / "data" / "cache" / "finmind" / "extras" / "stock_info.parquet")
    return dict(zip(df["stock_id"], df["industry_category"]))


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
    """Collect events for ALL tickers，標記 industry"""
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
                })
        if (i + 1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def mcpt_industry(events, industry):
    """MCPT: 該產業的 alpha vs 隨機抽 n_industry events from full universe"""
    sub = events[events["industry"] == industry]
    other = events[events["industry"] != industry]
    n_ind = len(sub)
    if n_ind < 100 or len(other) < 100:
        return None

    sig = sub["fwd_60d"].mean()
    bm = sub["baseline_mean"].mean()
    real_alpha = sig - bm

    rng = np.random.RandomState(42)
    other_fwd = other["fwd_60d"].values
    other_base = other["baseline_mean"].values
    fakes = []
    for _ in range(N_PERMUTE):
        idx = rng.choice(len(other_fwd), size=n_ind, replace=False)
        fake_alpha = other_fwd[idx].mean() - other_base[idx].mean()
        fakes.append(fake_alpha)
    fakes = np.array(fakes)
    p = (fakes >= real_alpha).sum() / N_PERMUTE
    return {"industry": industry, "n": n_ind, "real_alpha": real_alpha,
            "fake_mean": fakes.mean(), "fake_std": fakes.std(), "p": p}


def main():
    print("=" * 80)
    print("  Sector Top 4 MCPT 驗證")
    print("=" * 80)
    universe = load_universe()
    ind_map = load_industry_map()
    market_median = compute_market_median()
    events = collect_events(universe, market_median, ind_map)
    print(f"\n  Total events: {len(events)}")

    print("\n  🎲 MCPT for Top 4 (vs random sample from other industries):")
    for ind in TOP4:
        r = mcpt_industry(events, ind)
        if r is None: continue
        verdict = "✅" if r["p"] < 0.05 else "❌"
        print(f"  {ind}: n={r['n']}, alpha={r['real_alpha']:+.2f}%, "
              f"random_mean={r['fake_mean']:+.2f}%, "
              f"random_std={r['fake_std']:.2f}, p={r['p']:.4f} {verdict}")


if __name__ == "__main__":
    main()
