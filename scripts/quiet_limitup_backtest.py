"""
量縮漲停 vs 量爆漲停 alpha 對比

Hypothesis:
  量縮漲停 (vol_ratio < 1.0) = 主力吃貨，賣方惜售 → 後續延續強勢
  量爆漲停 (vol_ratio > 1.5) = 散戶追價 → 一日行情後回吐

設計:
  Trigger: 個股單日 ≥ +9.5% (漲停 / 接近漲停)
  分組:
    Q1: vol_ratio < 0.8     (量縮鎖死)
    Q2: 0.8 ≤ vr < 1.2      (中性)
    Q3: 1.2 ≤ vr < 2.0      (量增)
    Q4: vr ≥ 2.0             (量爆)
  Hold: 5d / 20d / 60d
  vs same-ticker random baseline

OOS + MCPT 驗證
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
HOLDS = [5, 20, 60]
N_PERMUTE = 1000


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def collect_events(universe):
    print("  收集量縮/量爆漲停 events...")
    events = []
    max_hold = max(HOLDS)
    for i, tk in enumerate(universe):
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

        # Trigger: 漲幅 >= 9.5%
        triggers = px[(px["pct"] >= 9.5) & (px["vol_ratio"].notna())].copy()
        if triggers.empty: continue

        # Forward returns
        for hold in HOLDS:
            triggers[f"fwd_{hold}d"] = (
                px["close"].shift(-hold).reindex(triggers.index) / triggers["close"] - 1
            ) * 100

        # Baseline (same ticker random)
        if len(px) < max_hold + 60: continue
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px) - max_hold - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px) - max_hold), size=n_base, replace=False)
        baseline = {h: [] for h in HOLDS}
        for j in bidx:
            entry = px["close"].iloc[j]
            if entry > 0:
                for h in HOLDS:
                    baseline[h].append((px["close"].iloc[j + h] / entry - 1) * 100)

        for _, row in triggers.iterrows():
            event = {
                "ticker": tk, "date": row["date"], "pct": row["pct"],
                "vol_ratio": row["vol_ratio"],
                "year": row["date"].year,
            }
            for h in HOLDS:
                event[f"fwd_{h}d"] = row[f"fwd_{h}d"]
                event[f"base_{h}d"] = np.mean(baseline[h]) if baseline[h] else 0
                event[f"base_std_{h}d"] = np.std(baseline[h]) if baseline[h] else 0
            events.append(event)
        if (i + 1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events).dropna(subset=[f"fwd_{HOLDS[-1]}d"])


def analyze_buckets(events):
    print(f"\n  Total events: {len(events)}")
    buckets = [
        ("Q1_quiet (vr<0.8)", events[events["vol_ratio"] < 0.8]),
        ("Q2_normal (0.8-1.2)", events[(events["vol_ratio"] >= 0.8) & (events["vol_ratio"] < 1.2)]),
        ("Q3_high (1.2-2.0)", events[(events["vol_ratio"] >= 1.2) & (events["vol_ratio"] < 2.0)]),
        ("Q4_burst (vr≥2.0)", events[events["vol_ratio"] >= 2.0]),
    ]
    print(f"\n  📊 By volume ratio bucket:")
    for hold in HOLDS:
        print(f"\n  --- hold={hold}d ---")
        print(f"  {'bucket':<25} {'n':<7} {'mean':<8} {'baseline':<10} {'alpha':<8} {'win%':<6} {'t':<7}")
        for label, sub in buckets:
            if len(sub) < 50: continue
            n = len(sub)
            sig = sub[f"fwd_{hold}d"].mean()
            bm = sub[f"base_{hold}d"].mean()
            bs = sub[f"base_std_{hold}d"].mean()
            alpha = sig - bm
            win = (sub[f"fwd_{hold}d"] > 0).mean() * 100
            t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
            t_str = f"{t:+.2f}" if t else "n/a"
            print(f"  {label:<25} {n:<7} {sig:+.2f}%  {bm:+.2f}%    {alpha:+.2f}%  {win:.1f}%  {t_str}")


def oos_check(events):
    print(f"\n  📅 OOS for Q1 (vr<0.8) and Q4 (vr≥2.0) — hold=20d:")
    for q_label, q_events in [
        ("Q1_quiet", events[events["vol_ratio"] < 0.8]),
        ("Q4_burst", events[events["vol_ratio"] >= 2.0]),
    ]:
        print(f"\n  --- {q_label} ---")
        for plabel, sub in [
            ("2017-2019", q_events[q_events["year"] <= 2019]),
            ("2020-2022", q_events[(q_events["year"] >= 2020) & (q_events["year"] <= 2022)]),
            ("2023-2025", q_events[q_events["year"] >= 2023]),
        ]:
            if len(sub) < 30: continue
            n = len(sub)
            alpha = sub["fwd_20d"].mean() - sub["base_20d"].mean()
            bs = sub["base_std_20d"].mean()
            t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
            t_str = f"{t:+.2f}" if t else "n/a"
            verdict = "✅" if alpha > 1 and (t or 0) > 2 else "⚠️"
            print(f"    {plabel}: n={n}, alpha={alpha:+.2f}%, t={t_str} {verdict}")


def mcpt_q1_vs_q4(events):
    print(f"\n  🎲 MCPT: Q1 (量縮) alpha 是否顯著 > Q4 (量爆)? (hold=20d)")
    q1 = events[events["vol_ratio"] < 0.8]
    q4 = events[events["vol_ratio"] >= 2.0]
    if len(q1) < 30 or len(q4) < 30: return

    real_q1 = q1["fwd_20d"].mean() - q1["base_20d"].mean()
    real_q4 = q4["fwd_20d"].mean() - q4["base_20d"].mean()
    real_diff = real_q1 - real_q4

    # Permutation: 把 vr 隨機 shuffle, 看 Q1-Q4 alpha 差異分布
    rng = np.random.RandomState(42)
    pool = pd.concat([q1, q4], ignore_index=True)
    n_q1 = len(q1)
    fakes = []
    for _ in range(N_PERMUTE):
        idx = rng.permutation(len(pool))
        fake_q1 = pool.iloc[idx[:n_q1]]
        fake_q4 = pool.iloc[idx[n_q1:]]
        fa = (fake_q1["fwd_20d"].mean() - fake_q1["base_20d"].mean()) - \
             (fake_q4["fwd_20d"].mean() - fake_q4["base_20d"].mean())
        fakes.append(fa)
    fakes = np.array(fakes)
    p = (fakes >= real_diff).sum() / N_PERMUTE
    print(f"    Real Q1 alpha: {real_q1:+.2f}%, Q4 alpha: {real_q4:+.2f}%")
    print(f"    Real diff (Q1-Q4): {real_diff:+.2f}%")
    print(f"    MCPT p (diff > random): {p:.4f} {'✅' if p<0.05 else '❌'}")


def main():
    print("=" * 80)
    print("  量縮漲停 vs 量爆漲停 alpha 對比")
    print("=" * 80)
    universe = load_universe()
    print(f"  Universe: {len(universe)}")
    events = collect_events(universe)
    analyze_buckets(events)
    oos_check(events)
    mcpt_q1_vs_q4(events)


if __name__ == "__main__":
    main()
