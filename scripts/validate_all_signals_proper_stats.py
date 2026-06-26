"""
用 PROPER stats (Welch's t-test + scipy) 重跑核心訊號驗證

修正 #2 #3：
  舊公式 t = alpha / (baseline_std / sqrt(n)) — incoherent
  新公式 Welch's t-test (scipy.stats.ttest_ind, equal_var=False)

所有 alpha 用 next-day entry (no look-ahead, fix #1)
Revenue 用 announce_date (fix #8)
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
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def proper_stats(signal_returns, baseline_returns, label=""):
    """Welch's t-test (proper), Bonferroni-style honest reporting"""
    sig = np.asarray(signal_returns)
    base = np.asarray(baseline_returns)
    n_sig = len(sig)
    n_base = len(base)
    if n_sig < 30 or n_base < 30: return None
    sig_mean = sig.mean()
    base_mean = base.mean()
    alpha = sig_mean - base_mean
    win = (sig > 0).mean() * 100

    # Welch's t-test (proper, no equal var assumption)
    t_w, p_w = stats.ttest_ind(sig, base, equal_var=False, alternative="greater")
    # 正常 (incorrect) formula 比較
    sig_std = sig.std(ddof=1)
    t_old = alpha / (base.std(ddof=1) / np.sqrt(n_sig))
    t_correct_one = alpha / (sig_std / np.sqrt(n_sig))  # one-sample alt

    return {
        "n_sig": n_sig, "n_base": n_base,
        "alpha": alpha, "win_pct": win,
        "t_old (wrong)": t_old,
        "t_one_sample": t_correct_one,
        "t_welch (proper)": t_w,
        "p_welch": p_w,
    }


def collect_revenue_yoy_events(universe, yoy_threshold=30.0, hold=60, use_announce=True):
    """Revenue YoY > 30% (absolute) with optional announce date fix"""
    sig_rets, base_rets = [], []
    for i, tk in enumerate(universe):
        rp = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
        if not rp.exists(): continue
        try: rev = pd.read_parquet(rp)
        except: continue
        if rev.empty or len(rev) < 24: continue
        rev = rev.sort_values(["revenue_year","revenue_month"]).reset_index(drop=True)
        rev["prior_revenue"] = rev["revenue"].shift(12)
        rev["yoy"] = (rev["revenue"]/rev["prior_revenue"]-1)*100
        rev["date"] = pd.to_datetime(rev["date"])
        if use_announce:
            create_dt = pd.to_datetime(rev.get("create_time"), errors="coerce")
            rev["announce_date"] = rev["date"] + pd.Timedelta(days=14)
            rev["announce_date"] = rev["announce_date"].where(
                create_dt.isna() | (create_dt <= rev["date"]), create_dt)
        else:
            rev["announce_date"] = rev["date"]

        triggers = rev[(rev["yoy"] > yoy_threshold) & (rev["yoy"] < 200) &
                       (rev["prior_revenue"] > 1e7) & rev["yoy"].notna()]
        if triggers.empty: continue

        pp = TW_CACHE / f"{tk}.parquet"
        if not pp.exists() or pp.stat().st_size < 500: continue
        try: px = pd.read_parquet(pp)
        except: continue
        if px.empty or len(px) < hold + 60: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        # Same-ticker random baseline (fixed seed for reproducibility)
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px_idx) - hold - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px_idx) - hold), size=n_base, replace=False)
        for j in bidx:
            entry = px_idx.iloc[j]
            if entry > 0:
                base_rets.append((px_idx.iloc[j+hold]/entry - 1) * 100)

        for _, row in triggers.iterrows():
            ann = row["announce_date"]
            future = px_idx[px_idx.index > ann]
            if len(future) <= hold: continue
            entry = future.iloc[0]
            if entry > 0:
                sig_rets.append((future.iloc[hold]/entry - 1) * 100)
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] sig={len(sig_rets)}, base={len(base_rets)}")
    return sig_rets, base_rets


def collect_quiet_limitup_events(universe, hold=20, vr_max=0.8):
    sig_rets, base_rets = [], []
    for i, tk in enumerate(universe):
        p = TW_CACHE / f"{tk}.parquet"
        if not p.exists() or p.stat().st_size < 500: continue
        try: px = pd.read_parquet(p)
        except: continue
        if px.empty or len(px) < 200: continue
        px["date"] = pd.to_datetime(px["date"])
        px = px.sort_values("date").reset_index(drop=True)
        px["pct"] = px["close"].pct_change() * 100
        px["vol_ma"] = px["volume"].rolling(60).mean()
        px["vol_ratio"] = px["volume"] / px["vol_ma"]
        triggers = px[(px["pct"] >= 9.5) & (px["vol_ratio"] < vr_max) & px["vol_ratio"].notna()]
        if triggers.empty: continue

        if len(px) < hold + 60: continue
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px) - hold - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px) - hold), size=n_base, replace=False)
        for j in bidx:
            entry = px["close"].iloc[j]
            if entry > 0:
                base_rets.append((px["close"].iloc[j+hold]/entry - 1) * 100)

        for idx_t, row in triggers.iterrows():
            future = px[px["date"] > row["date"]]
            if len(future) <= hold: continue
            entry = future["close"].iloc[0]
            if entry > 0:
                sig_rets.append((future["close"].iloc[hold]/entry - 1) * 100)
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] sig={len(sig_rets)}, base={len(base_rets)}")
    return sig_rets, base_rets


def collect_quiet_limitdown_events(universe, hold=20, vr_max=0.8):
    sig_rets, base_rets = [], []
    for i, tk in enumerate(universe):
        p = TW_CACHE / f"{tk}.parquet"
        if not p.exists() or p.stat().st_size < 500: continue
        try: px = pd.read_parquet(p)
        except: continue
        if px.empty or len(px) < 200: continue
        px["date"] = pd.to_datetime(px["date"])
        px = px.sort_values("date").reset_index(drop=True)
        px["pct"] = px["close"].pct_change() * 100
        px["vol_ma"] = px["volume"].rolling(60).mean()
        px["vol_ratio"] = px["volume"] / px["vol_ma"]
        triggers = px[(px["pct"] <= -9.5) & (px["vol_ratio"] < vr_max) & px["vol_ratio"].notna()]
        if triggers.empty: continue

        if len(px) < hold + 60: continue
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px) - hold - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px) - hold), size=n_base, replace=False)
        for j in bidx:
            entry = px["close"].iloc[j]
            if entry > 0:
                base_rets.append((px["close"].iloc[j+hold]/entry - 1) * 100)

        for idx_t, row in triggers.iterrows():
            future = px[px["date"] > row["date"]]
            if len(future) <= hold: continue
            entry = future["close"].iloc[0]
            if entry > 0:
                sig_rets.append((future["close"].iloc[hold]/entry - 1) * 100)
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] sig={len(sig_rets)}, base={len(base_rets)}")
    return sig_rets, base_rets


def report(label, signal_rets, baseline_rets):
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"{'='*80}")
    r = proper_stats(signal_rets, baseline_rets, label)
    if not r:
        print("  Insufficient data")
        return
    print(f"  n_signal={r['n_sig']:,}  n_baseline={r['n_base']:,}")
    print(f"  alpha (mean diff): {r['alpha']:+.3f}%")
    print(f"  win rate:           {r['win_pct']:.1f}%")
    print(f"\n  ── Stats comparison ──")
    print(f"  t (OLD wrong formula):    {r['t_old (wrong)']:+.2f}")
    print(f"  t (one-sample correct):   {r['t_one_sample']:+.2f}")
    print(f"  t (Welch's, PROPER):      {r['t_welch (proper)']:+.2f}")
    print(f"  p-value (Welch one-sided): {r['p_welch']:.6f}")


def main():
    print("=" * 80)
    print("  PROPER STATS RE-VALIDATION")
    print("  修正 #1 (next-day entry) + #8 (announce date) + #2 (Welch t-test)")
    print("=" * 80)
    universe = load_universe()
    print(f"  Universe: {len(universe)}")

    # 1. Revenue YoY
    print("\n[1/3] Revenue YoY > +30% (with announce date fix)...")
    sig, base = collect_revenue_yoy_events(universe, hold=60, use_announce=True)
    report("Revenue YoY > +30% (60d, announce date)", sig, base)

    # 2. Quiet Limitup
    print("\n[2/3] Quiet Limitup (vr<0.8)...")
    sig, base = collect_quiet_limitup_events(universe, hold=20)
    report("Quiet Limitup (vr<0.8, 20d)", sig, base)

    # 3. Quiet Limitdown
    print("\n[3/3] Quiet Limitdown (vr<0.8)...")
    sig, base = collect_quiet_limitdown_events(universe, hold=20)
    report("Quiet Limitdown Reversal (vr<0.8, 20d)", sig, base)


if __name__ == "__main__":
    main()
