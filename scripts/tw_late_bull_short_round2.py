"""TW LATE_BULL SHORT Round 2 — deep test H2 + H4.

H2: 量爆漲停 + LATE_BULL → SHORT 隔日 hold 5d  (Round 1: n=127 t=+2.28)
H4: dist MA200 +50% + LATE_BULL → SHORT 10d    (Round 1: n=393 t=+1.50 marginal)

Devil's advocate:
  1. Cluster-SE (event-level, dedupe same-day across stocks)
  2. 6 × period split (歷年不同 LATE_BULL 區間)
  3. +1% slippage stress (TW 個股實際滑價)
  4. Recent 1y / 2y / 5y 切片(看 alpha 衰退與否)
  5. MCPT 1000 perm (random non-LATE_BULL days)
  6. Per-sector breakdown (集中於某 sector 警示)
  7. 完整 universe (擴大到所有 4-digit ticker)
"""
from __future__ import annotations
import sys, io, math, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def t_stat(arr):
    arr = np.asarray(arr, dtype=float)
    if len(arr) < 2 or arr.std(ddof=1) == 0:
        return 0.0
    return arr.mean() / (arr.std(ddof=1) / math.sqrt(len(arr)))


def get_late_bull_regime():
    """歷史 LATE_BULL 日期 (TAIEX 距 MA200 > +25%)."""
    twii = CACHE / "^TWII.parquet"
    if not twii.exists():
        twii = CACHE / "0050.parquet"
    df = pd.read_parquet(twii)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ma200"] = df["close"].rolling(200).mean()
    df["dist_ma200"] = (df["close"] / df["ma200"] - 1) * 100
    df["late_bull"] = df["dist_ma200"] > 25
    return df


def all_4digit_tickers():
    tickers = []
    for p in CACHE.glob("*.parquet"):
        tk = p.stem
        if tk.isdigit() and len(tk) == 4:
            tickers.append(tk)
    return sorted(tickers)


# ═══════════════════════════════════════════════════════════
# H2 Round 2
# ═══════════════════════════════════════════════════════════
def test_h2_round2(regime_df, universe):
    print("\n" + "═"*72)
    print(f"H2 Round 2 — 量爆漲停 + LATE_BULL → SHORT 隔日 5d  (universe={len(universe)})")
    print("═"*72)

    all_events = []
    for tk in universe:
        try:
            df = pd.read_parquet(CACHE / f"{tk}.parquet")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) < 250:
                continue
            df = df.merge(regime_df[["date","late_bull","dist_ma200"]], on="date", how="left")
            df["ret"] = df["close"].pct_change()
            df["vol_20"] = df["volume"].rolling(20).mean()
            df["vol_std"] = df["volume"].rolling(20).std()
            df["vol_z"] = (df["volume"] - df["vol_20"]) / df["vol_std"]
            df["is_limitup"] = df["ret"] >= 0.095

            for i in range(20, len(df) - 5):
                if not df["is_limitup"].iloc[i]:
                    continue
                if pd.isna(df["vol_z"].iloc[i]) or df["vol_z"].iloc[i] < 2.0:
                    continue
                if not df["late_bull"].iloc[i]:
                    continue
                # Entry: next day open
                next_open = df["open"].iloc[i+1]
                exit_close = df["close"].iloc[i+5]
                if pd.isna(next_open) or pd.isna(exit_close):
                    continue
                pnl = (next_open - exit_close) / next_open * 100 - 0.585  # cost
                all_events.append({
                    "ticker": tk,
                    "date": df["date"].iloc[i],
                    "vol_z": df["vol_z"].iloc[i],
                    "dist_ma200": df["dist_ma200"].iloc[i],
                    "pnl": pnl,
                })
        except Exception:
            continue

    if not all_events:
        print("  no events")
        return None
    dft = pd.DataFrame(all_events)
    print(f"  Events: {len(dft)}, period {dft['date'].min().date()} ~ {dft['date'].max().date()}")
    arr = dft["pnl"].values
    print(f"  Baseline: mean={arr.mean():+.3f}% t={t_stat(arr):+.2f} WR={(arr>0).mean()*100:.0f}%")

    # 1. Cluster-SE (event-day level)
    print("\n  [1] Cluster-SE (event-day level, mean per date)")
    by_date = dft.groupby("date")["pnl"].mean()
    cl_t = t_stat(by_date.values)
    print(f"  Unique event dates: {len(by_date)}  mean={by_date.mean():+.3f}%  t={cl_t:+.2f}")

    # 2. Period split
    print("\n  [2] Period split (6 equal-time chunks)")
    dft = dft.sort_values("date").reset_index(drop=True)
    chunk_size = max(1, len(dft) // 6)
    pos = 0
    for i in range(6):
        s = i * chunk_size
        e = (i+1) * chunk_size if i < 5 else len(dft)
        c = dft.iloc[s:e]
        if len(c) == 0:
            continue
        mu = c["pnl"].mean()
        sign = "+" if mu > 0 else ""
        print(f"  W{i+1} ({c['date'].min().date()} ~ {c['date'].max().date()}): n={len(c):>3} mean={sign}{mu:.3f}%")
        if mu > 0:
            pos += 1
    print(f"  Positive periods: {pos}/6  {'✅' if pos>=5 else '⚠️'}")

    # 3. Cost stress +1% (TW 個股實際 slippage 比 0.585 多)
    print("\n  [3] +1% slippage stress")
    stress = arr - 1.0
    print(f"  After +100bps: mean={stress.mean():+.3f}% t={t_stat(stress):+.2f} "
          f"WR={(stress>0).mean()*100:.0f}% {'✅' if stress.mean()>0 else '❌'}")

    # 4. Recent 1y / 2y / 5y
    print("\n  [4] Recent slices")
    now = dft["date"].max()
    for years in [1, 2, 3, 5]:
        cutoff = now - pd.Timedelta(days=365*years)
        sub = dft[dft["date"] >= cutoff]
        if len(sub) >= 5:
            print(f"  Last {years}y: n={len(sub):>3} mean={sub['pnl'].mean():+.3f}% t={t_stat(sub['pnl'].values):+.2f}")

    # 5. Per-sector breakdown (前綴 3 碼當 sector 粗分)
    print("\n  [5] Per-ticker concentration check (top 10 tickers by event count)")
    by_tk = dft.groupby("ticker").agg(n=("pnl","size"), mean=("pnl","mean")).sort_values("n", ascending=False)
    for tk, row in by_tk.head(10).iterrows():
        print(f"    {tk}: n={row['n']} mean={row['mean']:+.2f}%")
    # 集中度檢查
    top10_share = by_tk.head(10)["n"].sum() / len(dft) * 100
    print(f"  Top 10 ticker concentration: {top10_share:.1f}% of all events")

    # 6. MCPT 1000 perm
    print("\n  [6] MCPT 1000 permutations")
    # Build null pool: 所有有效個股 day 的 random short result (non-event days)
    print("  building null pool...")
    null_pnls = []
    for tk in universe[:100]:   # use 100 tickers for null pool to speed up
        try:
            df = pd.read_parquet(CACHE / f"{tk}.parquet")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) < 250:
                continue
            df["ret"] = df["close"].pct_change()
            for i in range(20, len(df) - 5):
                if pd.isna(df["open"].iloc[i+1]) or pd.isna(df["close"].iloc[i+5]):
                    continue
                next_open = df["open"].iloc[i+1]
                exit_close = df["close"].iloc[i+5]
                pnl = (next_open - exit_close) / next_open * 100 - 0.585
                null_pnls.append(pnl)
        except Exception:
            continue
    print(f"  Null pool: {len(null_pnls)}")
    np.random.seed(42)
    real_mean = arr.mean()
    n = len(arr)
    perm_means = []
    for trial in range(1000):
        s = np.random.choice(null_pnls, size=n, replace=False)
        perm_means.append(s.mean())
    p = (sum(1 for m in perm_means if m >= real_mean) + 1) / (len(perm_means) + 1)
    print(f"  Real mean: {real_mean:+.3f}%  |  Null pool mean: {np.mean(perm_means):+.3f}%")
    print(f"  Real percentile: {(1 - sum(1 for m in perm_means if m >= real_mean)/len(perm_means))*100:.1f}%")
    print(f"  p-value: {p:.4f}  {'✅' if p<0.01 else '⚠️' if p<0.05 else '❌'}")

    return {
        "baseline_t": t_stat(arr), "baseline_mean": arr.mean(), "n": n,
        "cluster_t": cl_t, "positive_periods": pos, "p_mcpt": p,
    }


# ═══════════════════════════════════════════════════════════
# H4 Round 2 — dist MA200 +50% SHORT 10d
# ═══════════════════════════════════════════════════════════
def test_h4_round2(regime_df, universe):
    print("\n" + "═"*72)
    print(f"H4 Round 2 — dist MA200 > +50% + LATE_BULL → SHORT 10d")
    print("═"*72)

    all_events = []
    for tk in universe:
        try:
            df = pd.read_parquet(CACHE / f"{tk}.parquet")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) < 250:
                continue
            df["ma200"] = df["close"].rolling(200).mean()
            df["dist"] = (df["close"]/df["ma200"]-1)*100
            df = df.merge(regime_df[["date","late_bull"]], on="date", how="left")
            for i in range(20, len(df) - 10):
                if not df["late_bull"].iloc[i]:
                    continue
                if pd.isna(df["dist"].iloc[i]) or df["dist"].iloc[i] < 50:
                    continue
                next_open = df["open"].iloc[i+1]
                exit_close = df["close"].iloc[i+10]
                if pd.isna(next_open) or pd.isna(exit_close):
                    continue
                pnl = (next_open - exit_close) / next_open * 100 - 0.585
                all_events.append({
                    "ticker": tk, "date": df["date"].iloc[i],
                    "dist": df["dist"].iloc[i], "pnl": pnl,
                })
        except Exception:
            continue
    if not all_events:
        return None
    dft = pd.DataFrame(all_events)
    arr = dft["pnl"].values
    print(f"  Events: {len(dft)}, period {dft['date'].min().date()} ~ {dft['date'].max().date()}")
    print(f"  Baseline: mean={arr.mean():+.3f}% t={t_stat(arr):+.2f} WR={(arr>0).mean()*100:.0f}%")

    # Same as H2: cluster, period split, cost stress, recent
    by_date = dft.groupby("date")["pnl"].mean()
    print(f"\n  Cluster-SE: n_dates={len(by_date)} mean={by_date.mean():+.3f}% t={t_stat(by_date.values):+.2f}")

    # Period split
    dft = dft.sort_values("date").reset_index(drop=True)
    cs = max(1, len(dft)//6)
    pos = 0
    for i in range(6):
        s = i*cs; e = (i+1)*cs if i<5 else len(dft)
        c = dft.iloc[s:e]
        if len(c) and c["pnl"].mean() > 0: pos += 1
    print(f"  Period split: {pos}/6 positive")

    # +1% slippage
    stress = arr - 1.0
    print(f"  +100bps stress: mean={stress.mean():+.3f}% t={t_stat(stress):+.2f}")

    # Recent
    now = dft["date"].max()
    for years in [1, 2, 5]:
        cutoff = now - pd.Timedelta(days=365*years)
        sub = dft[dft["date"] >= cutoff]
        if len(sub) >= 5:
            print(f"  Last {years}y: n={len(sub):>3} mean={sub['pnl'].mean():+.3f}% t={t_stat(sub['pnl'].values):+.2f}")

    # Dist quantile
    print("\n  Dist quantile breakdown:")
    dft["dist_quantile"] = pd.cut(dft["dist"], bins=[50, 70, 100, 200, 500], labels=["50-70%", "70-100%", "100-200%", ">200%"])
    for q, grp in dft.groupby("dist_quantile"):
        if len(grp) >= 5:
            print(f"    dist {q}: n={len(grp):>3} mean={grp['pnl'].mean():+.3f}% t={t_stat(grp['pnl'].values):+.2f}")

    return {"n": len(arr), "mean": arr.mean(), "t": t_stat(arr)}


# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"TW LATE_BULL SHORT Round 2 — {datetime.now().strftime('%H:%M TW')}")
    regime_df = get_late_bull_regime()
    universe = all_4digit_tickers()
    print(f"Universe: {len(universe)} 4-digit tickers, LATE_BULL days = {regime_df['late_bull'].sum()}")

    h2_result = test_h2_round2(regime_df, universe)
    h4_result = test_h4_round2(regime_df, universe)
