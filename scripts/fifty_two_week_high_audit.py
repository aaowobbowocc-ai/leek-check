"""
52-Week High Momentum AUDIT (新-5)

Inputs:
  logs/52wk_high_momentum_events.csv (proximity>=0.95, 18.7K events 2018-2025)

Tests:
  1. Full sample alpha (event-only, fwd 20/60/120d)
  2. OOS split 2018-2019 vs 2020-2022 vs 2023-2025
  3. vs same-ticker random baseline + paired t-test
  4. vs 0050 baseline (same calendar window)
  5. Cluster-by-month SE (cluster-robust t-stat)
  6. MCPT (Monte-Carlo permutation, 1000 iter)
  7. TSMC + 0050 top-30 heavyweight contribution decomposition
  8. Compare with ordinary 6m momentum (Jegadeesh-Titman):
     past 6m return top decile -> fwd 60d return
  9. Liquidity tier sweep (L1-L4)

Output: logs/52wk_high_momentum_audit.csv + verdict log
"""
from __future__ import annotations
import io, sys, json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
EVENTS_CSV = ROOT / "logs" / "52wk_high_momentum_events.csv"
OUT_CSV = ROOT / "logs" / "52wk_high_momentum_audit.csv"
COST = 0.585  # round-trip per memory standard for individual stocks

# 0050 top-30 heavyweights (approx, market cap weighted)
HEAVYWEIGHT_TOP30 = {
    "2330","2317","2454","2308","2382","2891","2412","2881","3711","2882",
    "3231","6505","2884","1216","2885","3034","2002","5871","2207","2880",
    "3008","2886","5876","3017","2912","1303","2357","2603","5880","2887",
}

rng = np.random.default_rng(42)


def cluster_t(returns: np.ndarray, dates: pd.Series) -> tuple[float, float]:
    """Cluster-by-month SE. Returns (t, p)."""
    if len(returns) < 30:
        return float("nan"), float("nan")
    months = pd.to_datetime(dates).dt.to_period("M").astype(str)
    df = pd.DataFrame({"r": returns, "m": months})
    grp = df.groupby("m")["r"]
    g_mean = grp.mean()
    g_n = grp.size()
    overall_mean = returns.mean()
    G = len(g_mean)
    if G < 2:
        return float("nan"), float("nan")
    se = g_mean.std(ddof=1) / np.sqrt(G)
    t = overall_mean / se if se > 0 else 0
    p = 2 * (1 - stats.norm.cdf(abs(t)))
    return float(t), float(p)


def load_px(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def get_random_baseline(events: pd.DataFrame, hold_days: int, n_iter: int = 1) -> np.ndarray:
    """Same-ticker random-date baseline."""
    out = []
    cache: dict[str, pd.DataFrame] = {}
    for _ in range(n_iter):
        rets = []
        for _, ev in events.iterrows():
            tk = str(ev["ticker"])
            if tk not in cache:
                cache[tk] = load_px(tk)
            px = cache[tk]
            if px.empty or len(px) < hold_days + 5:
                continue
            # pick random date with same liquidity universe
            valid_idx = px[(px["date"].dt.year >= 2018) & (px["date"].dt.year <= 2025)].index
            if len(valid_idx) < hold_days + 2:
                continue
            i0 = rng.choice(valid_idx[:-hold_days - 1])
            entry = px.iloc[i0 + 1]["open"] if i0 + 1 < len(px) else px.iloc[i0]["close"]
            exit_p = px.iloc[i0 + hold_days]["close"]
            if entry <= 0 or pd.isna(entry) or pd.isna(exit_p):
                continue
            rets.append((exit_p / entry - 1) * 100 - COST)
        out.extend(rets)
    return np.array(out)


def mcpt(observed_mean: float, returns: np.ndarray, n_iter: int = 1000) -> float:
    """Sign-flip permutation test."""
    n = len(returns)
    if n < 10:
        return float("nan")
    cnt = 0
    abs_obs = abs(observed_mean)
    for _ in range(n_iter):
        signs = rng.choice([-1, 1], size=n)
        perm_mean = (signs * returns).mean()
        if abs(perm_mean) >= abs_obs:
            cnt += 1
    return cnt / n_iter


def jt_6m_momentum_baseline(events_df: pd.DataFrame, hold_days: int = 60) -> dict:
    """Generate Jegadeesh-Titman 6m momentum baseline:
       Top decile (by past 126d return) at each event date -> fwd 60d.
       Sample at same trigger dates (calendar match)."""
    universe = sorted({p.stem for p in TW_CACHE.glob("*.parquet")
                       if p.stem.isdigit() and len(p.stem) == 4 and not p.stem.startswith("00")})
    print(f"  JT 6m baseline universe: {len(universe)} tickers")
    pxs: dict[str, pd.DataFrame] = {}
    # preload subset to bound memory
    for tk in universe:
        pxs[tk] = load_px(tk)

    # sample 100 trigger dates spread across years
    trigger_dates = pd.to_datetime(events_df["date"]).drop_duplicates().sort_values()
    sample_dates = trigger_dates.sample(min(100, len(trigger_dates)), random_state=42)
    rets = []
    for d in sample_dates:
        # rank universe by past 126d return at this date
        ranks = []
        for tk, px in pxs.items():
            if px.empty:
                continue
            sub = px[px["date"] <= d]
            if len(sub) < 130:
                continue
            past = (sub.iloc[-1]["close"] / sub.iloc[-126]["close"] - 1)
            # liquidity check
            dv = (sub["close"] * sub["volume"]).iloc[-60:].mean()
            if dv < 1e8:
                continue
            ranks.append((tk, past))
        if len(ranks) < 50:
            continue
        ranks.sort(key=lambda x: -x[1])
        top_decile = ranks[: max(1, len(ranks) // 10)]
        for tk, _ in top_decile:
            px = pxs[tk]
            sub = px[px["date"] >= d]
            if len(sub) < hold_days + 2:
                continue
            entry = sub.iloc[1]["open"] if len(sub) > 1 else sub.iloc[0]["close"]
            exit_p = sub.iloc[hold_days]["close"]
            if entry <= 0:
                continue
            rets.append((exit_p / entry - 1) * 100 - COST)
    if not rets:
        return {"n": 0, "mean": float("nan"), "median": float("nan"), "win": float("nan"), "t": float("nan")}
    arr = np.array(rets)
    t, _ = stats.ttest_1samp(arr, 0)
    return {
        "n": len(arr),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "win": float((arr > 0).mean() * 100),
        "t": float(t),
    }


def main():
    print("=" * 80)
    print("  52-Week High Momentum AUDIT (新-5)")
    print("=" * 80)
    df = pd.read_csv(EVENTS_CSV)
    df["date"] = pd.to_datetime(df["date"])
    df["ticker"] = df["ticker"].astype(str)
    df["is_heavyweight"] = df["ticker"].isin(HEAVYWEIGHT_TOP30)
    df["is_tsmc"] = df["ticker"] == "2330"
    print(f"\n  Events: {len(df):,}, tickers={df['ticker'].nunique()}, "
          f"date range {df['date'].min().date()}~{df['date'].max().date()}")
    print(f"  Heavyweight events (top30 0050): {df['is_heavyweight'].sum():,} "
          f"({100*df['is_heavyweight'].mean():.1f}%)")
    print(f"  TSMC events: {df['is_tsmc'].sum()}")

    audit_rows = []

    # Test 1-3: full + OOS + heavyweight ablation
    for hold in [20, 60, 120]:
        col = f"fwd_{hold}d"
        for slice_name, slice_df in [
            ("full", df),
            ("ex_TSMC", df[~df["is_tsmc"]]),
            ("ex_heavyweight30", df[~df["is_heavyweight"]]),
            ("heavyweight30_only", df[df["is_heavyweight"]]),
            ("OOS_2018-2019", df[df["year"].between(2018, 2019)]),
            ("OOS_2020-2022", df[df["year"].between(2020, 2022)]),
            ("OOS_2023-2025", df[df["year"].between(2023, 2025)]),
            ("L4_dv>=10yi", df[df["dv_60d_yi"] >= 10]),
            ("L4_ex_TSMC_2020+", df[(df["dv_60d_yi"] >= 10) & (~df["is_tsmc"]) & (df["year"] >= 2020)]),
        ]:
            sub = slice_df[col].dropna()
            if len(sub) < 30:
                continue
            t, p = stats.ttest_1samp(sub, 0)
            ct, cp = cluster_t(sub.values, slice_df.loc[sub.index, "date"])
            audit_rows.append({
                "test": "event_alpha",
                "hold_days": hold,
                "slice": slice_name,
                "n": len(sub),
                "mean_pct": round(float(sub.mean()), 3),
                "median_pct": round(float(sub.median()), 3),
                "win_pct": round(float((sub > 0).mean() * 100), 1),
                "t_naive": round(float(t), 3),
                "p_naive": round(float(p), 5),
                "t_cluster_month": round(float(ct), 3) if not np.isnan(ct) else None,
                "p_cluster_month": round(float(cp), 5) if not np.isnan(cp) else None,
            })

    # Print main table
    print(f"\n  === Test 1: Event alpha (full, ex-TSMC, ex-heavyweight, OOS) ===")
    print(f"  {'slice':<26} {'hold':>5} {'n':>6} {'mean':>8} {'win%':>6} "
          f"{'t_naive':>9} {'t_clust':>9}")
    for r in audit_rows:
        if r["test"] != "event_alpha":
            continue
        tc = f"{r['t_cluster_month']:+.2f}" if r["t_cluster_month"] is not None else "  -  "
        print(f"  {r['slice']:<26} {r['hold_days']:>5} {r['n']:>6} {r['mean_pct']:>+7.2f}% "
              f"{r['win_pct']:>5.1f}% {r['t_naive']:>+8.2f}  {tc:>8}")

    # Test 4: vs 0050 baseline (paired)
    print(f"\n  === Test 2: vs 0050 baseline (paired same-window) ===")
    etf = load_px("0050")
    if not etf.empty:
        for hold in [20, 60, 120]:
            col = f"fwd_{hold}d"
            df_l4 = df[df["dv_60d_yi"] >= 10].copy()
            etf_rets = []
            strat_rets = []
            for _, row in df_l4.iterrows():
                etf_sub = etf[etf["date"] >= row["date"]].head(hold + 1)
                if len(etf_sub) < hold + 1:
                    continue
                etf_ret = (etf_sub.iloc[-1]["close"] / etf_sub.iloc[0]["open"] - 1) * 100 - 0.34  # ETF cost
                etf_rets.append(etf_ret)
                strat_rets.append(row[col])
            if len(strat_rets) < 30:
                continue
            arr_s = np.array(strat_rets)
            arr_b = np.array(etf_rets)
            excess = arr_s - arr_b
            t, p = stats.ttest_rel(arr_s, arr_b)
            audit_rows.append({
                "test": "vs_0050",
                "hold_days": hold,
                "slice": "L4",
                "n": len(arr_s),
                "strategy_mean": round(float(arr_s.mean()), 3),
                "baseline_0050_mean": round(float(arr_b.mean()), 3),
                "excess_pp": round(float(excess.mean()), 3),
                "t_paired": round(float(t), 3),
                "p_paired": round(float(p), 5),
            })
            print(f"  hold {hold}d  n={len(arr_s)}  strat={arr_s.mean():+.2f}%  "
                  f"0050={arr_b.mean():+.2f}%  excess={excess.mean():+.2f}pp  "
                  f"t={t:+.2f} p={p:.4f}")

    # Test 5: same-ticker random baseline + MCPT (subsample for speed)
    print(f"\n  === Test 3: Same-ticker random baseline + MCPT (subsample 2000) ===")
    sample = df.sample(min(2000, len(df)), random_state=42)
    for hold in [60]:
        col = f"fwd_{hold}d"
        observed = sample[col].dropna().values
        baseline = get_random_baseline(sample, hold, n_iter=1)
        if len(baseline) < 100:
            print("  baseline too small")
            continue
        excess = float(observed.mean() - baseline.mean())
        t, p = stats.ttest_ind(observed, baseline, equal_var=False)
        # MCPT on observed
        mcpt_p = mcpt(float(observed.mean()), observed, n_iter=1000)
        audit_rows.append({
            "test": "vs_random_same_ticker",
            "hold_days": hold,
            "slice": "subsample2000",
            "n": len(observed),
            "n_baseline": len(baseline),
            "strategy_mean": round(float(observed.mean()), 3),
            "random_baseline_mean": round(float(baseline.mean()), 3),
            "excess_pp": round(excess, 3),
            "t_welch": round(float(t), 3),
            "p_welch": round(float(p), 5),
            "mcpt_p_signflip": round(float(mcpt_p), 4),
        })
        print(f"  hold 60d  observed={observed.mean():+.2f}%  rand={baseline.mean():+.2f}%  "
              f"excess={excess:+.2f}pp  t_welch={t:+.2f}  p={p:.4f}  MCPT_p={mcpt_p:.4f}")

    # Test 6: JT 6m momentum baseline
    print(f"\n  === Test 4: vs Ordinary 6m Momentum (Jegadeesh-Titman) ===")
    print(f"  Sampling 100 trigger dates, ranking universe by past 126d return, top decile -> fwd 60d")
    jt = jt_6m_momentum_baseline(df[df["dv_60d_yi"] >= 5], hold_days=60)
    audit_rows.append({"test": "JT_6m_momentum", "hold_days": 60, **jt})
    print(f"  JT 6m momentum top decile: n={jt['n']} mean={jt['mean']:+.2f}%  "
          f"win={jt['win']:.1f}%  t={jt['t']:+.2f}")
    # Compare directly
    own_60d = df[df["dv_60d_yi"] >= 5]["fwd_60d"].dropna()
    print(f"  52w high own (L2 dv>=5): n={len(own_60d)} mean={own_60d.mean():+.2f}%  "
          f"win={(own_60d>0).mean()*100:.1f}%  t={stats.ttest_1samp(own_60d,0)[0]:+.2f}")

    # Save
    out_df = pd.DataFrame(audit_rows)
    OUT_CSV.parent.mkdir(exist_ok=True)
    out_df.to_csv(OUT_CSV, index=False)
    print(f"\n  Saved audit results: {OUT_CSV}")

    # Verdict
    print("\n" + "=" * 80)
    print("  VERDICT")
    print("=" * 80)
    full_60 = df["fwd_60d"].dropna()
    ex_tsmc_60 = df[~df["is_tsmc"]]["fwd_60d"].dropna()
    ex_hw_60 = df[~df["is_heavyweight"]]["fwd_60d"].dropna()
    print(f"  fwd_60d full        : mean={full_60.mean():+.2f}%  win={(full_60>0).mean()*100:.1f}%")
    print(f"  fwd_60d ex-TSMC     : mean={ex_tsmc_60.mean():+.2f}%  win={(ex_tsmc_60>0).mean()*100:.1f}%")
    print(f"  fwd_60d ex-top30 hw : mean={ex_hw_60.mean():+.2f}%  win={(ex_hw_60>0).mean()*100:.1f}%")


if __name__ == "__main__":
    main()
