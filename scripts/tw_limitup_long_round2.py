"""TW 量爆漲停 LATE_BULL → LONG 隔日 5d  Round 2 deep test.

Round 1 (H2 反向): n=2008 mean SHORT -2.34% t=-4.77 → LONG +2.34% 暗示有 alpha
本 Round 2 直接測 LONG + 完整 devil's advocate:

  Devil's advocate stack:
    1. Baseline: 同 ticker 隨機進場 (memory 真 alpha 標準)
    2. vs 0050 同期 hold (memory: 必須 outperform BTH)
    3. Cluster-SE (event-day mean)
    4. 6 period split
    5. +1% slippage stress (TW 個股)
    6. Recent 1y / 2y / 5y
    7. MCPT 1000 perm
    8. Liquidity check (avg dollar volume)
    9. Per-cap-size breakdown (small/mid/large)
    10. Drawdown / max single trade loss
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

LONG_COST = 0.585  # TW 個股 RT cost (CLAUDE.md)


def t_stat(arr):
    arr = np.asarray(arr, dtype=float)
    if len(arr) < 2 or arr.std(ddof=1) == 0:
        return 0.0
    return arr.mean() / (arr.std(ddof=1) / math.sqrt(len(arr)))


def get_regime():
    twii = CACHE / "^TWII.parquet"
    if not twii.exists():
        twii = CACHE / "0050.parquet"
    df = pd.read_parquet(twii)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ma200"] = df["close"].rolling(200).mean()
    df["dist"] = (df["close"]/df["ma200"]-1)*100
    df["late_bull"] = df["dist"] > 25
    return df


def get_0050_returns():
    """5d forward return for 0050 on each date."""
    p = CACHE / "0050.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["fwd5d"] = df["close"].shift(-5) / df["open"].shift(-1) - 1
    return df[["date", "fwd5d"]].set_index("date")


def find_events(regime_df, universe):
    """Returns DataFrame of events: 量爆漲停 + LATE_BULL across universe."""
    all_events = []
    print(f"  掃描 {len(universe)} ticker...")
    for i, tk in enumerate(universe):
        if i % 500 == 0:
            print(f"    {i}/{len(universe)}")
        try:
            df = pd.read_parquet(CACHE / f"{tk}.parquet")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) < 250:
                continue
            df = df.merge(regime_df[["date","late_bull","dist"]], on="date", how="left")
            df["ret"] = df["close"].pct_change()
            df["vol_20"] = df["volume"].rolling(20).mean()
            df["vol_std"] = df["volume"].rolling(20).std()
            df["vol_z"] = (df["volume"] - df["vol_20"]) / df["vol_std"]
            df["dollar_vol"] = df["close"] * df["volume"]
            df["avg_dollar_vol_20"] = df["dollar_vol"].rolling(20).mean()
            df["is_limitup"] = df["ret"] >= 0.095
            for i_row in range(20, len(df) - 5):
                if not df["is_limitup"].iloc[i_row]:
                    continue
                if pd.isna(df["vol_z"].iloc[i_row]) or df["vol_z"].iloc[i_row] < 2.0:
                    continue
                if not df["late_bull"].iloc[i_row]:
                    continue
                next_open = df["open"].iloc[i_row+1]
                exit_close = df["close"].iloc[i_row+5]
                if pd.isna(next_open) or pd.isna(exit_close) or next_open <= 0:
                    continue
                # LONG pnl
                gross = (exit_close - next_open) / next_open * 100
                net = gross - LONG_COST
                all_events.append({
                    "ticker": tk,
                    "date": df["date"].iloc[i_row],
                    "entry_date": df["date"].iloc[i_row+1],
                    "vol_z": df["vol_z"].iloc[i_row],
                    "dist_ma200": df["dist"].iloc[i_row],
                    "limitup_close": df["close"].iloc[i_row],
                    "entry_px": next_open,
                    "exit_px": exit_close,
                    "dollar_vol_20": df["avg_dollar_vol_20"].iloc[i_row],
                    "pnl_gross": gross,
                    "pnl_net": net,
                })
        except Exception:
            continue
    return pd.DataFrame(all_events)


def random_same_ticker_baseline(events_df, n_iter=500):
    """For each event ticker × event period, sample random entry day same ticker → 5d hold."""
    np.random.seed(42)
    results = []
    by_tk = events_df.groupby("ticker")
    for tk, grp in by_tk:
        try:
            df = pd.read_parquet(CACHE / f"{tk}.parquet")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) < 30:
                continue
            valid = df.iloc[20:-5].reset_index(drop=True)
            n_events = len(grp)
            if len(valid) < 6:
                continue
            for _ in range(n_iter):
                rand_idx = np.random.choice(len(valid), size=min(n_events, len(valid)), replace=False)
                for ri in rand_idx:
                    actual_idx = ri + 20   # back to original
                    if actual_idx + 5 >= len(df):
                        continue
                    next_open = df["open"].iloc[actual_idx+1]
                    exit_close = df["close"].iloc[actual_idx+5]
                    if pd.isna(next_open) or pd.isna(exit_close) or next_open <= 0:
                        continue
                    gross = (exit_close - next_open) / next_open * 100
                    results.append(gross - LONG_COST)
                if len(results) > 50000:
                    break   # 樣本夠大就停
        except Exception:
            continue
    return np.array(results)


if __name__ == "__main__":
    print(f"TW 量爆漲停 LONG Round 2 — {datetime.now().strftime('%H:%M')}\n")

    regime_df = get_regime()
    universe = sorted([p.stem for p in CACHE.glob("*.parquet") if p.stem.isdigit() and len(p.stem)==4])
    print(f"Universe: {len(universe)} tickers, LATE_BULL days: {regime_df['late_bull'].sum()}")

    print("\n[Step 1] Find events...")
    events = find_events(regime_df, universe)
    print(f"  Total events: {len(events)}")
    if events.empty:
        sys.exit(0)
    arr = events["pnl_net"].values
    print(f"  Baseline: mean_net={arr.mean():+.3f}%  t={t_stat(arr):+.2f}  WR={(arr>0).mean()*100:.0f}%")
    print(f"  Baseline gross: mean={events['pnl_gross'].mean():+.3f}%  (cost adj +0.585)")
    print(f"  Period: {events['entry_date'].min().date()} ~ {events['entry_date'].max().date()}")

    # ──────────────────────────────────
    # [2] vs 0050 same-period baseline
    # ──────────────────────────────────
    print("\n[2] vs 0050 same-period 5d forward")
    etf_df = get_0050_returns()
    events_m = events.merge(etf_df, left_on="date", right_index=True, how="left")
    events_m["etf_5d"] = events_m["fwd5d"] * 100
    events_m["alpha_vs_etf"] = events_m["pnl_net"] - events_m["etf_5d"]
    print(f"  ETF (0050) mean 5d at event dates: {events_m['etf_5d'].mean():+.3f}%")
    print(f"  Strategy mean net: {events_m['pnl_net'].mean():+.3f}%")
    print(f"  Alpha vs 0050: {events_m['alpha_vs_etf'].mean():+.3f}%")
    t_alpha = t_stat(events_m['alpha_vs_etf'].dropna().values)
    print(f"  Alpha t-stat: {t_alpha:+.2f}  {'✅' if t_alpha>2 else '⚠️' if t_alpha>1 else '❌'}")

    # ──────────────────────────────────
    # [3] Cluster-SE
    # ──────────────────────────────────
    print("\n[3] Cluster-SE (event-day mean)")
    by_date = events.groupby("entry_date")["pnl_net"].mean()
    print(f"  Unique event dates: {len(by_date)}  mean: {by_date.mean():+.3f}%  t: {t_stat(by_date.values):+.2f}")

    # ──────────────────────────────────
    # [4] Period split
    # ──────────────────────────────────
    print("\n[4] Period split (6 chunks by entry_date)")
    events_s = events.sort_values("entry_date").reset_index(drop=True)
    cs = max(1, len(events_s)//6)
    pos = 0
    for i in range(6):
        s = i*cs; e = (i+1)*cs if i<5 else len(events_s)
        c = events_s.iloc[s:e]
        if not len(c): continue
        mu = c["pnl_net"].mean()
        sign = "+" if mu>0 else ""
        print(f"  W{i+1} ({c['entry_date'].min().date()} ~ {c['entry_date'].max().date()}): n={len(c):>4} mean={sign}{mu:.3f}%")
        if mu > 0: pos += 1
    print(f"  Positive periods: {pos}/6  {'✅' if pos>=5 else '⚠️'}")

    # ──────────────────────────────────
    # [5] +1% slippage stress
    # ──────────────────────────────────
    print("\n[5] +1% slippage stress (TW 小型股 realistic)")
    stress = arr - 1.0
    print(f"  After +100bps: mean={stress.mean():+.3f}% t={t_stat(stress):+.2f} WR={(stress>0).mean()*100:.0f}% {'✅' if stress.mean()>0 else '❌'}")

    # ──────────────────────────────────
    # [6] Recent slices
    # ──────────────────────────────────
    print("\n[6] Recent slices")
    now_d = events["entry_date"].max()
    for years in [1, 2, 3, 5]:
        cutoff = now_d - pd.Timedelta(days=365*years)
        sub = events[events["entry_date"] >= cutoff]
        if len(sub) >= 5:
            t_ = t_stat(sub["pnl_net"].values)
            print(f"  Last {years}y: n={len(sub):>4} mean={sub['pnl_net'].mean():+.3f}% t={t_:+.2f}")

    # ──────────────────────────────────
    # [7] MCPT 1000 (random entry same universe)
    # ──────────────────────────────────
    print("\n[7] MCPT 1000 perm (random same-ticker entry)")
    null = random_same_ticker_baseline(events, n_iter=10)
    print(f"  Null pool size: {len(null)}")
    if len(null) >= 1000:
        n_real = len(events)
        np.random.seed(1)
        perm_means = []
        for trial in range(1000):
            s = np.random.choice(null, size=n_real, replace=False)
            perm_means.append(s.mean())
        real_mean = arr.mean()
        p = (sum(1 for m in perm_means if m >= real_mean) + 1) / (len(perm_means) + 1)
        print(f"  Real mean: {real_mean:+.3f}%  Null pool mean: {np.mean(null):+.3f}%  Null perm mean: {np.mean(perm_means):+.3f}%")
        print(f"  Real percentile: {(1 - sum(1 for m in perm_means if m >= real_mean)/len(perm_means))*100:.1f}%")
        print(f"  p-value: {p:.4f}  {'✅' if p<0.01 else '⚠️' if p<0.05 else '❌'}")

    # ──────────────────────────────────
    # [8] Liquidity / cap-size feasibility
    # ──────────────────────────────────
    print("\n[8] Liquidity check (avg dollar volume of event tickers)")
    events["dv_yi"] = events["dollar_vol_20"] / 1e8   # 億元
    # Categories
    bins = [0, 0.5, 2, 10, 50, 500, 1e6]
    labels = ["<0.5億/日 micro", "0.5-2億 small", "2-10億 mid", "10-50億 large", "50-500億 mega", ">500億 ultra"]
    events["dv_cat"] = pd.cut(events["dv_yi"], bins=bins, labels=labels)
    for cat, grp in events.groupby("dv_cat", observed=True):
        if len(grp) < 5: continue
        mu = grp["pnl_net"].mean()
        t_ = t_stat(grp["pnl_net"].values)
        print(f"  {cat:<22} n={len(grp):>4} mean={mu:+.3f}% t={t_:+.2f}")

    # ──────────────────────────────────
    # [9] Distribution & max single loss
    # ──────────────────────────────────
    print("\n[9] Distribution + tail risk")
    print(f"  Min: {arr.min():+.2f}%  Max: {arr.max():+.2f}%")
    print(f"  P5: {np.percentile(arr,5):+.2f}%  P95: {np.percentile(arr,95):+.2f}%")
    print(f"  Mean: {arr.mean():+.3f}%  Median: {np.median(arr):+.2f}%")
    print(f"  Std: {arr.std():.2f}%")
    big_losers = events[events["pnl_net"] < -10]
    print(f"  Trades < -10%: {len(big_losers)} ({len(big_losers)/len(arr)*100:.1f}%)")
    print(f"  Trades > +10%: {(events['pnl_net']>10).sum()} ({(events['pnl_net']>10).sum()/len(arr)*100:.1f}%)")

    # ──────────────────────────────────
    # FINAL
    # ──────────────────────────────────
    print("\n" + "═"*70)
    print("FINAL VERDICT")
    print("═"*70)
    print(f"  Strategy: 量爆漲停 + vol_z≥2 + LATE_BULL → LONG 隔日 5d")
    print(f"  Baseline n={len(events)}  mean_net={arr.mean():+.3f}%  t={t_stat(arr):+.2f}")
    print(f"  Cluster-SE t={t_stat(by_date.values):+.2f}")
    print(f"  Periods positive: {pos}/6")
    print(f"  Alpha vs 0050: {events_m['alpha_vs_etf'].mean():+.3f}% (t={t_alpha:+.2f})")
    print(f"  +100bps stress: {stress.mean():+.3f}%")
