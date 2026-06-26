"""
修 #13 sector beta confound

舊：sector alpha = sector signal mean - same-ticker random baseline
    問題：baseline 是 same-ticker，但 ticker 在 high-alpha sector 整體都是 post-2020 大漲
    → sector "alpha" 實際上 = sector beta during bull market

新：sector alpha = sector signal mean - SAME SECTOR random baseline
    Control sector beta，得到 within-sector stock-selection alpha
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
HOLD = 60


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_industry():
    df = pd.read_parquet(ROOT / "data" / "cache" / "finmind" / "extras" / "stock_info.parquet")
    return dict(zip(df["stock_id"], df["industry_category"]))


def compute_market_median():
    print("  計算市場 median YoY...")
    all_yoy = []
    for p in CACHE.glob("TaiwanStockMonthRevenue_*.parquet"):
        try:
            r = pd.read_parquet(p)
            if len(r) < 24: continue
            r = r.sort_values(["revenue_year","revenue_month"]).reset_index(drop=True)
            r["prior"] = r["revenue"].shift(12)
            r["yoy"] = (r["revenue"]/r["prior"]-1)*100
            r = r[r["prior"] > 1e7]
            if r.empty: continue
            r["date"] = pd.to_datetime(r["date"])
            r2 = r[r["yoy"].abs() < 500][["date","yoy"]]
            all_yoy.append(r2)
        except: continue
    df = pd.concat(all_yoy, ignore_index=True)
    df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym")["yoy"].median().to_dict()


def collect_events_with_sector(universe, market_median, ind_map):
    """收集 Revenue YoY events + sector + same-ticker baseline"""
    events = []
    for i, tk in enumerate(universe):
        ind = ind_map.get(tk, "Unknown")
        rp = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
        if not rp.exists(): continue
        try: rev = pd.read_parquet(rp)
        except: continue
        if rev.empty or len(rev) < 24: continue
        rev = rev.sort_values(["revenue_year","revenue_month"]).reset_index(drop=True)
        rev["prior"] = rev["revenue"].shift(12)
        rev["yoy"] = (rev["revenue"]/rev["prior"]-1)*100
        rev["date"] = pd.to_datetime(rev["date"])
        # Use announce date
        if "create_time" in rev.columns:
            create_dt = pd.to_datetime(rev["create_time"], errors="coerce")
            rev["announce_date"] = rev["date"] + pd.Timedelta(days=14)
            rev["announce_date"] = rev["announce_date"].where(
                create_dt.isna() | (create_dt <= rev["date"]), create_dt)
        else:
            rev["announce_date"] = rev["date"] + pd.Timedelta(days=14)
        rev["ym"] = rev["date"].dt.to_period("M")
        rev["mkt_med"] = rev["ym"].map(market_median)
        rev["excess"] = rev["yoy"] - rev["mkt_med"]
        triggers = rev[(rev["excess"] > 30) & (rev["yoy"] < 200) & rev["yoy"].notna()
                       & (rev["prior"] > 1e7)]
        if triggers.empty: continue

        pp = TW_CACHE / f"{tk}.parquet"
        if not pp.exists() or pp.stat().st_size < 500: continue
        try: px = pd.read_parquet(pp)
        except: continue
        if px.empty or len(px) < HOLD + 60: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        # Same-ticker random baseline returns
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px_idx) - HOLD - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px_idx) - HOLD), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            entry = px_idx.iloc[j]
            if entry > 0:
                baseline.append((px_idx.iloc[j+HOLD]/entry - 1) * 100)

        for _, row in triggers.iterrows():
            ann = row["announce_date"]
            future = px_idx[px_idx.index > ann]
            if len(future) <= HOLD: continue
            entry = future.iloc[0]
            if entry > 0:
                ret = (future.iloc[HOLD]/entry - 1) * 100
                events.append({
                    "ticker": tk, "industry": ind, "date": ann,
                    "fwd": ret, "baseline_mean": np.mean(baseline),
                    "baseline_returns": baseline,  # full list for sector aggregation
                    "year": ann.year,
                })
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def analyze_sector_relative(events):
    """Compute sector-relative alpha (control sector beta)"""
    print(f"\n  Total events: {len(events)}")
    HIGH_ALPHA_SECTORS = ["資訊服務業", "半導體業", "通信網路業", "電腦及週邊設備業"]
    LOW_ALPHA_SECTORS = ["紡織纖維", "塑膠工業", "鋼鐵工業", "觀光餐旅", "電子通路業"]

    print(f"\n  📊 Sector alpha: same-ticker baseline (OLD) vs same-sector baseline (NEW)")
    print(f"  {'sector':<25} {'n':<6} {'old α':<8} {'sector beta':<12} {'new α (ticker-only)':<20}")

    for sector_label in HIGH_ALPHA_SECTORS + LOW_ALPHA_SECTORS:
        sub = events[events["industry"] == sector_label]
        if len(sub) < 50: continue
        n = len(sub)

        # OLD alpha = ticker_signal - same_ticker_baseline (already in event)
        old_alpha = sub["fwd"].mean() - sub["baseline_mean"].mean()

        # Sector beta = average return of ALL same-sector tickers' baselines
        # = "what does an average stock in this sector return over 60 days?"
        sector_baseline_all = []
        for _, ev in sub.iterrows():
            sector_baseline_all.extend(ev["baseline_returns"])
        sector_beta = np.mean(sector_baseline_all)

        # NEW alpha = signal - sector beta (control sector momentum)
        new_alpha = sub["fwd"].mean() - sector_beta
        # 同 ticker baseline 已經 ticker-specific，但混合 sector 的回報
        # 真正 within-sector excess: signal - sector mean of baseline returns

        # t-stat for new
        sig_arr = sub["fwd"].values
        base_arr = np.array(sector_baseline_all)
        if len(base_arr) > 30:
            t_new, p_new = stats.ttest_ind(sig_arr, base_arr, equal_var=False, alternative="greater")
        else:
            t_new, p_new = None, None

        t_str = f"{t_new:+.2f}" if t_new is not None else "n/a"
        p_str = f"{p_new:.4f}" if p_new is not None else "n/a"
        print(f"  {sector_label[:25]:<25} {n:<6} {old_alpha:+.2f}%   {sector_beta:+.2f}%      "
              f"{new_alpha:+.2f}% (t={t_str}, p={p_str})")


def main():
    print("=" * 80)
    print("  #13 Fix: Sector-relative alpha (剝離 sector beta)")
    print("=" * 80)
    universe = load_universe()
    ind_map = load_industry()
    market_median = compute_market_median()
    events = collect_events_with_sector(universe, market_median, ind_map)
    analyze_sector_relative(events)


if __name__ == "__main__":
    main()
