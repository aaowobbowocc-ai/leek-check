"""
修 #5: VIX-conditioned baseline

舊：baseline 用「同 ticker 全期 random」(混合所有 VIX regime)
新：對 VIX≥35 events，baseline 也限制在 VIX≥35 期間

否則 alpha = 「crash-recovery returns vs normal-period baseline」 = beta 不是 alpha
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
HOLD = 20


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_vix():
    import yfinance as yf
    h = yf.Ticker("^VIX").history(period="3500d", auto_adjust=False)
    df = pd.DataFrame({"date": pd.to_datetime(h.index).tz_localize(None),
                       "vix": h["Close"].values})
    return df.set_index("date")["vix"].to_dict()


def get_vix_for_date(d, vix_map):
    """Find VIX value for a date (or nearest before within 7 days)"""
    d_ts = pd.Timestamp(d)
    for offset in range(7):
        check = d_ts - pd.Timedelta(days=offset)
        if check in vix_map: return vix_map[check]
    return None


def collect_events_with_vix(universe, vix_map, direction="up", hold=HOLD):
    """Collect events with VIX context, also collect baseline by VIX regime"""
    events = []  # signal events
    # baseline_by_vix[vix_bucket] = list of returns
    baseline_by_vix = {"low": [], "mid": [], "high": [], "extreme": []}

    def vix_bucket(v):
        if v < 18: return "low"
        if v < 25: return "mid"
        if v < 35: return "high"
        return "extreme"

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

        if direction == "up":
            triggers = px[(px["pct"] >= 9.5) & (px["vol_ratio"] < 0.8) & px["vol_ratio"].notna()]
        else:
            triggers = px[(px["pct"] <= -9.5) & (px["vol_ratio"] < 0.8) & px["vol_ratio"].notna()]
        if triggers.empty: continue

        if len(px) < hold + 60: continue

        # Baseline: 50 random samples per ticker, but bucket by VIX at sample time
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px) - hold - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px) - hold), size=n_base, replace=False)
        for j in bidx:
            entry = px["close"].iloc[j]
            sample_date = px["date"].iloc[j]
            vix_sample = get_vix_for_date(sample_date, vix_map)
            if entry > 0 and vix_sample is not None:
                ret = (px["close"].iloc[j+hold]/entry - 1) * 100
                baseline_by_vix[vix_bucket(vix_sample)].append(ret)

        # Signal events
        for _, row in triggers.iterrows():
            sd = row["date"]
            future = px[px["date"] > sd]
            if len(future) <= hold: continue
            entry = future["close"].iloc[0]
            if entry <= 0: continue
            ret = (future["close"].iloc[hold]/entry - 1) * 100
            vix_sig = get_vix_for_date(sd, vix_map)
            if vix_sig is None: continue
            events.append({
                "ticker": tk, "date": sd, "fwd": ret,
                "vix": vix_sig, "vix_bucket": vix_bucket(vix_sig),
                "year": sd.year,
            })
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}, "
                  f"base_extreme={len(baseline_by_vix['extreme'])}")

    return pd.DataFrame(events), baseline_by_vix


def compare_alphas(events, baseline_by_vix, label):
    """Compare old (mixed baseline) vs new (regime-matched baseline)"""
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"{'='*80}")

    # 各 bucket signal 統計
    for bucket in ["low", "mid", "high", "extreme"]:
        sub = events[events["vix_bucket"] == bucket]
        if len(sub) < 30: continue

        # OLD: baseline = ALL VIX buckets pooled
        old_baseline = []
        for vb in ["low", "mid", "high", "extreme"]:
            old_baseline.extend(baseline_by_vix[vb])
        old_baseline = np.asarray(old_baseline)

        # NEW: baseline = SAME VIX bucket only
        new_baseline = np.asarray(baseline_by_vix[bucket])

        if len(new_baseline) < 30:
            print(f"  {bucket}: signal n={len(sub)}, baseline same-bucket n={len(new_baseline)} (太少)")
            continue

        sig_arr = sub["fwd"].values

        # Old comparison
        old_alpha = sig_arr.mean() - old_baseline.mean()
        old_t, old_p = stats.ttest_ind(sig_arr, old_baseline, equal_var=False, alternative="greater")

        # New comparison (VIX-matched)
        new_alpha = sig_arr.mean() - new_baseline.mean()
        new_t, new_p = stats.ttest_ind(sig_arr, new_baseline, equal_var=False, alternative="greater")

        change = new_alpha - old_alpha
        change_emoji = "🔻" if change < -2 else ("🔺" if change > 2 else "➖")
        print(f"\n  --- {bucket} (n={len(sub)}, base_n={len(new_baseline)}) ---")
        print(f"    OLD baseline (mixed): alpha={old_alpha:+.2f}%, t={old_t:+.2f}, p={old_p:.4f}")
        print(f"    NEW baseline (matched): alpha={new_alpha:+.2f}%, t={new_t:+.2f}, p={new_p:.4f}")
        print(f"    Change: {change:+.2f}pp {change_emoji}")


def main():
    print("=" * 80)
    print("  #5 Fix: VIX-conditioned baseline 驗證")
    print("=" * 80)
    universe = load_universe()
    vix_map = load_vix()
    print(f"  Universe: {len(universe)}")

    # Quiet Limitup
    print("\n[1/2] Quiet Limitup (vr<0.8)...")
    events, baseline = collect_events_with_vix(universe, vix_map, "up", HOLD)
    compare_alphas(events, baseline, "Quiet Limitup × VIX")

    # Quiet Limitdown
    print("\n[2/2] Quiet Limitdown (vr<0.8)...")
    events, baseline = collect_events_with_vix(universe, vix_map, "down", HOLD)
    compare_alphas(events, baseline, "Quiet Limitdown × VIX")


if __name__ == "__main__":
    main()
