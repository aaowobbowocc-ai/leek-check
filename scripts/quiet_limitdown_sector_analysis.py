"""
量縮跌停反彈 × Sector segmentation

Hypothesis A: 反彈是 mean reversion，跟 sector 無關 → 各 sector alpha 接近
Hypothesis B: PEAD 失效的傳產（紡織/塑膠/鋼鐵）反彈 alpha 反而更強（純 technical）

驗證 + 找出最強反彈 sector
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
HOLD = 20
N_PERMUTE = 1000


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_industry():
    df = pd.read_parquet(ROOT / "data" / "cache" / "finmind" / "extras" / "stock_info.parquet")
    return dict(zip(df["stock_id"], df["industry_category"]))


def collect_events(universe, ind_map):
    print("  收集量縮跌停 events with sector...")
    events = []
    for i, tk in enumerate(universe):
        ind = ind_map.get(tk, "Unknown")
        p = TW_CACHE / f"{tk}.parquet"
        if not p.exists() or p.stat().st_size < 500: continue
        try: px = pd.read_parquet(p)
        except: continue
        if px.empty or len(px) < 200: continue
        px["date"] = pd.to_datetime(px["date"])
        px = px.sort_values("date").reset_index(drop=True)
        px["pct"] = px["close"].pct_change() * 100
        px["vol_ma60"] = px["volume"].rolling(60).mean()
        px["vol_ratio"] = px["volume"] / px["vol_ma60"]

        triggers = px[(px["pct"] <= -9.5) & (px["vol_ratio"] < 0.8) & px["vol_ratio"].notna()]
        if triggers.empty: continue

        if len(px) < HOLD + 60: continue
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px) - HOLD - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px) - HOLD), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            entry = px["close"].iloc[j]
            if entry > 0:
                baseline.append((px["close"].iloc[j+HOLD]/entry-1)*100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        for _, row in triggers.iterrows():
            sd = row["date"]
            future = px[px["date"] > sd]
            if len(future) <= HOLD: continue
            entry = future["close"].iloc[0]
            if entry <= 0: continue
            fwd = (future["close"].iloc[HOLD]/entry-1)*100
            events.append({
                "ticker": tk, "industry": ind, "date": sd,
                "fwd_20d": fwd, "baseline_mean": bm, "baseline_std": bs,
                "year": sd.year,
            })
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def analyze_sector(events):
    print(f"\n  Total events: {len(events)}")
    counts = events["industry"].value_counts()
    big = counts[counts >= 100].index.tolist()
    print(f"  Industries with n >= 100: {len(big)}")

    rows = []
    for ind in big:
        sub = events[events["industry"] == ind]
        n = len(sub)
        sig = sub["fwd_20d"].mean()
        bm = sub["baseline_mean"].mean()
        bs = sub["baseline_std"].mean()
        alpha = sig - bm
        win = (sub["fwd_20d"] > 0).mean() * 100
        t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
        rows.append({"industry": ind, "n": n, "alpha": alpha,
                    "win_pct": win, "t": t})
    grid = pd.DataFrame(rows).sort_values("alpha", ascending=False)
    print(f"\n  📊 Top 15 by alpha (hold=20d):")
    print(f"  {'industry':<25} {'n':<6} {'alpha':<8} {'win%':<6} {'t':<7}")
    for _, r in grid.head(15).iterrows():
        t_str = f"{r['t']:+.2f}" if r['t'] else "n/a"
        verdict = "⭐" if r['alpha'] > 8 and (r['t'] or 0) > 5 else ""
        print(f"  {r['industry'][:25]:<25} {r['n']:<6} {r['alpha']:+.2f}%  {r['win_pct']:.1f}%  {t_str}  {verdict}")

    print(f"\n  📊 Bottom 5 by alpha:")
    for _, r in grid.tail(5).iterrows():
        t_str = f"{r['t']:+.2f}" if r['t'] else "n/a"
        print(f"  {r['industry'][:25]:<25} {r['n']:<6} {r['alpha']:+.2f}%  {r['win_pct']:.1f}%  {t_str}")

    # 比較 PEAD 強 sector vs 失效 sector
    pead_strong = ["資訊服務業", "半導體業", "通信網路業", "電腦及週邊設備業"]
    pead_failed = ["紡織纖維", "塑膠工業", "鋼鐵工業", "觀光餐旅", "電子通路業"]

    print(f"\n  📊 PEAD 強 sector 跌停反彈:")
    for ind in pead_strong:
        sub = events[events["industry"] == ind]
        if len(sub) < 30: continue
        n = len(sub)
        alpha = sub["fwd_20d"].mean() - sub["baseline_mean"].mean()
        bs = sub["baseline_std"].mean()
        t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
        t_str = f"{t:+.2f}" if t else "n/a"
        print(f"    {ind}: n={n}, alpha={alpha:+.2f}%, t={t_str}")

    print(f"\n  📊 PEAD 失效 sector 跌停反彈:")
    for ind in pead_failed:
        sub = events[events["industry"] == ind]
        if len(sub) < 30: continue
        n = len(sub)
        alpha = sub["fwd_20d"].mean() - sub["baseline_mean"].mean()
        bs = sub["baseline_std"].mean()
        t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
        t_str = f"{t:+.2f}" if t else "n/a"
        print(f"    {ind}: n={n}, alpha={alpha:+.2f}%, t={t_str}")


def main():
    print("=" * 80)
    print("  量縮跌停反彈 × Sector segmentation")
    print("=" * 80)
    universe = load_universe()
    ind_map = load_industry()
    print(f"  Universe: {len(universe)}, industries: {len(set(ind_map.values()))}")
    events = collect_events(universe, ind_map)
    analyze_sector(events)


if __name__ == "__main__":
    main()
