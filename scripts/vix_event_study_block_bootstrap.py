"""
G-2 FIX: VIX≥35 訊號 真實 effective N 評估

Claim: alpha +9.05% (n=1067, t=13)
Reality (Gemini): N 看似 1067，實際只 3-4 個 VIX-spike clusters
  → 2018 Volmageddon
  → 2020 Q1 COVID
  → 2022 Q1 升息
  → 2024-08 日圓閃崩

Method: Block bootstrap by month — group events 同月 = 1 cluster, 計算 cluster-level alpha
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


def collect_extreme_events(universe, vix_map, direction="up"):
    events = []
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
            triggers = px[(px["pct"] >= 9.5) & (px["vol_ratio"] < 0.8)]
        else:
            triggers = px[(px["pct"] <= -9.5) & (px["vol_ratio"] < 0.8)]
        if triggers.empty: continue
        if len(px) < HOLD + 60: continue

        for _, row in triggers.iterrows():
            sd = row["date"]
            # VIX at signal date
            vix = None
            for off in range(7):
                d = sd - pd.Timedelta(days=off)
                if d in vix_map: vix = vix_map[d]; break
            if vix is None or vix < 35: continue
            future = px[px["date"] > sd]
            if len(future) <= HOLD: continue
            entry = future["close"].iloc[0]
            if entry <= 0: continue
            ret = (future["close"].iloc[HOLD]/entry - 1) * 100
            events.append({
                "ticker": tk, "date": sd, "fwd": ret, "vix": vix,
                "year_month": sd.to_period("M").strftime("%Y-%m"),
            })
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def block_analysis(events, label):
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"{'='*80}")
    if events.empty:
        print("  No events")
        return
    n = len(events)
    nominal_alpha = events["fwd"].mean()
    print(f"\n  Nominal: n={n}, mean_fwd={nominal_alpha:+.2f}%")

    # Cluster by year_month
    cluster_means = events.groupby("year_month")["fwd"].mean()
    n_clusters = len(cluster_means)
    print(f"\n  📊 Cluster analysis (group by year_month):")
    print(f"  Effective N (clusters): {n_clusters}")
    print(f"  Cluster mean returns:")
    for ym, m in cluster_means.sort_values(ascending=False).items():
        n_in = (events["year_month"] == ym).sum()
        print(f"    {ym}: n={n_in:<5} mean fwd={m:+.2f}%")

    # Cluster-level t-test (n=clusters)
    if n_clusters >= 3:
        cluster_t, cluster_p = stats.ttest_1samp(cluster_means, 0, alternative="greater")
        print(f"\n  📐 Cluster-level t-stat (one-sample, H0: mean=0):")
        print(f"    Cluster mean: {cluster_means.mean():+.2f}%")
        print(f"    Cluster std:  {cluster_means.std():.2f}%")
        print(f"    t = {cluster_t:+.2f}, p = {cluster_p:.4f}")
        if cluster_p < 0.05:
            print(f"    ✅ Cluster-level alpha 顯著")
        else:
            print(f"    ⚠️ Cluster-level alpha 不顯著 (n_clusters={n_clusters} 樣本太少)")
    else:
        print(f"  ⚠️ 只有 {n_clusters} 個 cluster，無法做 cluster-level t-test")

    # Block bootstrap
    print(f"\n  🎲 Block bootstrap (resample by month):")
    rng = np.random.default_rng(42)
    boot_alphas = []
    for _ in range(1000):
        sampled = rng.choice(cluster_means.values, size=n_clusters, replace=True)
        boot_alphas.append(sampled.mean())
    boot_alphas = np.array(boot_alphas)
    print(f"    Bootstrap mean: {boot_alphas.mean():+.2f}%")
    print(f"    95% CI: [{np.percentile(boot_alphas, 2.5):+.2f}%, "
          f"{np.percentile(boot_alphas, 97.5):+.2f}%]")
    print(f"    P(alpha <= 0): {(boot_alphas <= 0).mean():.4f}")


def main():
    print("=" * 80)
    print("  G-2 FIX: VIX≥35 真實 effective N (Block Bootstrap by Month)")
    print("=" * 80)
    universe = load_universe()
    vix_map = load_vix()
    print(f"  Universe: {len(universe)}")

    print("\n[1/2] Quiet Limitup × VIX≥35...")
    events = collect_extreme_events(universe, vix_map, "up")
    block_analysis(events, "Quiet Limitup × VIX≥35")

    print("\n[2/2] Quiet Limitdown × VIX≥35...")
    events = collect_extreme_events(universe, vix_map, "down")
    block_analysis(events, "Quiet Limitdown × VIX≥35")


if __name__ == "__main__":
    main()
