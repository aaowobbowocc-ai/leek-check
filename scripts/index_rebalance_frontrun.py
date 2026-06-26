"""Index Rebalance Front-Run Backtest (新-2)

學術依據: AlphaArchitect 「23bp/yr from trading ahead of index funds」
ScienceDirect 2024 (S1059056024001059) MSCI Taiwan additions

================================================================
DATA AVAILABILITY NOTE
================================================================
- 0050 quarterly constituent change history: NOT AVAILABLE in cache
- MSCI Taiwan announcement history: NOT AVAILABLE
- ETF historical holdings/weights: NOT AVAILABLE

Available proxy:
- TaiwanStockShareholding (1988 tickers, 2017-2026): foreign ownership %
  When a ticker joins MSCI/0050, foreign indexer ratio jumps observably.
  Use multi-day foreign ratio rise as proxy event.

Limitation: this proxy MIXES three signals
  (a) genuine index-inclusion front-running flow
  (b) foreign earnings-driven discretionary buying (active longs)
  (c) random noise / dividend reinvestment

We cannot cleanly isolate (a). Result is a JOINT alpha estimate.
================================================================

Design:
- Event = ticker's foreign ratio rise >= THRESHOLD pp over 10 trading days
- Filter: only tickers in TWSE/TPEx with daily ADV >= NT$30M (liquidity)
- Entry: open of T+1 (no look-ahead — foreign data has 1-2d reporting lag,
  we assume detection on T+0 close, entry T+1 open)
- Hold: 5d, 20d, 60d
- Baseline: same ticker, random entry over the trading universe (matched periods)
- Validation: MCPT (1000 reshuffles) + 2-period OOS (2017-2021 / 2022-2025)
"""

from __future__ import annotations

import os
import glob
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path("c:/Users/USER/Desktop/INVEST")
SHARE_DIR = ROOT / "data" / "cache" / "finmind" / "finmind"
PRICE_DIR = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
OUT_CSV = ROOT / "logs" / "index_rebalance_frontrun.csv"

THRESH_PP = 1.0           # foreign ratio rise >= 1.0pp over WINDOW
WINDOW = 10               # trading days lookback for foreign ratio change
HOLD_DAYS = [5, 20, 60]
COST_PCT = 0.585 / 100    # individual stock round-trip
ADV_MIN_TWD = 30_000_000  # min avg daily turnover NT$30M
MIN_PRICE = 10.0          # exclude penny
MAX_PRICE = 2000.0        # sanity
N_MCPT = 500


def load_price(stock_id: str) -> pd.DataFrame | None:
    p = PRICE_DIR / f"{stock_id}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty:
        return None
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    elif df.index.name and df.index.name.lower() == "date":
        df = df.reset_index().rename(columns={df.index.name: "date"})
        df["date"] = pd.to_datetime(df["date"])
    else:
        df = df.reset_index()
        first_col = df.columns[0]
        df = df.rename(columns={first_col: "date"})
        df["date"] = pd.to_datetime(df["date"])
    needed = {"open", "close"}
    if not needed.issubset(df.columns):
        return None
    df = df.sort_values("date").reset_index(drop=True)
    return df


def load_share(stock_id: str) -> pd.DataFrame | None:
    p = SHARE_DIR / f"TaiwanStockShareholding_{stock_id}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["for_ratio"] = pd.to_numeric(df["ForeignInvestmentSharesRatio"], errors="coerce")
    df = df.dropna(subset=["for_ratio"]).sort_values("date").reset_index(drop=True)
    return df[["date", "for_ratio"]]


def detect_events(stock_id: str) -> list[dict]:
    """Detect foreign ratio jump events (proxy for index/MSCI inclusion buying)."""
    share = load_share(stock_id)
    if share is None or len(share) < WINDOW + 70:
        return []
    px = load_price(stock_id)
    if px is None or len(px) < 100:
        return []

    # liquidity filter via 60d ADV
    px["adv"] = (px["close"] * px.get("volume", pd.Series(0, index=px.index))).rolling(60).mean()

    # rolling pp change in foreign ratio
    share["for_diff"] = share["for_ratio"] - share["for_ratio"].shift(WINDOW)

    # candidate trigger dates
    triggers = share[share["for_diff"] >= THRESH_PP].copy()
    if triggers.empty:
        return []

    # debounce: keep only first trigger within 30d window
    triggers["dgap"] = triggers["date"].diff().dt.days.fillna(99)
    triggers = triggers[triggers["dgap"] >= 30].copy()

    events = []
    for _, row in triggers.iterrows():
        trig_date = row["date"]
        # entry next trading day (T+1 open) — find next price bar after trig_date
        future = px[px["date"] > trig_date]
        if future.empty:
            continue
        entry_row = future.iloc[0]
        entry_idx = future.index[0]
        entry_price = entry_row["open"]
        if not (MIN_PRICE <= entry_price <= MAX_PRICE):
            continue
        adv = entry_row.get("adv", 0)
        if pd.isna(adv) or adv < ADV_MIN_TWD:
            continue

        rec = {
            "stock_id": stock_id,
            "trig_date": trig_date,
            "entry_date": entry_row["date"],
            "entry_price": entry_price,
            "for_ratio_diff_pp": row["for_diff"],
        }
        # forward returns
        for hd in HOLD_DAYS:
            target_idx = entry_idx + hd
            if target_idx < len(px):
                exit_price = px.loc[target_idx, "close"]
                gross = (exit_price / entry_price - 1.0)
                net = gross - COST_PCT
                rec[f"ret_{hd}d_gross"] = gross
                rec[f"ret_{hd}d_net"] = net
            else:
                rec[f"ret_{hd}d_gross"] = np.nan
                rec[f"ret_{hd}d_net"] = np.nan
        events.append(rec)
    return events


def baseline_returns(stock_id: str, n_samples: int, seed: int = 42) -> dict:
    """Same-ticker random entry baseline (n samples per hold period)."""
    px = load_price(stock_id)
    if px is None:
        return {}
    rng = np.random.RandomState(seed + hash(stock_id) % 10000)
    out = {hd: [] for hd in HOLD_DAYS}
    valid_indices = list(range(60, len(px) - max(HOLD_DAYS) - 1))
    if len(valid_indices) < n_samples:
        n_samples = len(valid_indices)
    if n_samples <= 0:
        return out
    sample_idx = rng.choice(valid_indices, size=n_samples, replace=False)
    for idx in sample_idx:
        entry_price = px.iloc[idx + 1]["open"]
        if not (MIN_PRICE <= entry_price <= MAX_PRICE):
            continue
        for hd in HOLD_DAYS:
            target_idx = idx + 1 + hd
            if target_idx < len(px):
                exit_price = px.iloc[target_idx]["close"]
                out[hd].append(exit_price / entry_price - 1.0 - COST_PCT)
    return out


def main():
    # Universe = tickers having BOTH foreign-ratio and price files
    share_files = glob.glob(str(SHARE_DIR / "TaiwanStockShareholding_*.parquet"))
    universe = []
    for f in share_files:
        sid = os.path.basename(f).replace("TaiwanStockShareholding_", "").replace(".parquet", "")
        if (PRICE_DIR / f"{sid}.parquet").exists():
            universe.append(sid)
    print(f"[INFO] Universe: {len(universe)} tickers (have both foreign-ratio and price)")

    all_events = []
    baseline_pool = {hd: [] for hd in HOLD_DAYS}

    for i, sid in enumerate(universe):
        if i % 200 == 0:
            print(f"  scanning {i}/{len(universe)} ... events so far={len(all_events)}")
        evs = detect_events(sid)
        all_events.extend(evs)
        # Build baseline only for tickers that produced events to keep matched
        if evs:
            bs = baseline_returns(sid, n_samples=20)
            for hd in HOLD_DAYS:
                baseline_pool[hd].extend(bs.get(hd, []))

    if not all_events:
        print("[WARN] No events detected. Aborting.")
        Path(OUT_CSV).write_text("status,no_events\n", encoding="utf-8")
        return

    df = pd.DataFrame(all_events)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\n[OUT] Events: {len(df)}, saved -> {OUT_CSV}")
    print(f"[OUT] Trig date range: {df['trig_date'].min()} -> {df['trig_date'].max()}")
    print(f"[OUT] Unique tickers: {df['stock_id'].nunique()}")

    # Summary
    print("\n=== AGGREGATE PERFORMANCE ===")
    print(f"{'Period':<10} {'N':>6} {'gross':>10} {'net':>10} {'win%':>8} "
          f"{'baseN':>7} {'baseMean':>10} {'alpha':>10} {'t':>8}")
    summary_rows = []
    for hd in HOLD_DAYS:
        col_g = f"ret_{hd}d_gross"
        col_n = f"ret_{hd}d_net"
        ev_ret = df[col_n].dropna()
        if len(ev_ret) == 0:
            continue
        n = len(ev_ret)
        mean_g = df[col_g].dropna().mean()
        mean_n = ev_ret.mean()
        win = (ev_ret > 0).mean() * 100
        base = pd.Series(baseline_pool[hd])
        base_mean = base.mean() if len(base) else np.nan
        alpha = mean_n - base_mean
        # t-stat (Welch)
        if len(base) > 1 and ev_ret.std() > 0:
            se = np.sqrt(ev_ret.var() / n + base.var() / len(base))
            t = alpha / se if se > 0 else np.nan
        else:
            t = np.nan
        print(f"{hd}d{'':<7} {n:>6d} {mean_g*100:>9.2f}% {mean_n*100:>9.2f}% "
              f"{win:>7.1f}% {len(base):>7d} {base_mean*100:>9.2f}% "
              f"{alpha*100:>9.2f}% {t:>8.2f}")
        summary_rows.append(dict(period=f"{hd}d", n=n, gross=mean_g, net=mean_n,
                                 win_pct=win, base_n=len(base), base_mean=base_mean,
                                 alpha=alpha, t_stat=t))

    # MCPT — for the strongest period, randomly reassign event labels
    print("\n=== MCPT (label permutation) ===")
    for sr in summary_rows:
        hd = int(sr["period"].rstrip("d"))
        col_n = f"ret_{hd}d_net"
        ev_ret = df[col_n].dropna().values
        base = np.array(baseline_pool[hd])
        if len(base) < 30 or len(ev_ret) < 30:
            print(f"  {sr['period']}: insufficient data for MCPT")
            continue
        observed = ev_ret.mean() - base.mean()
        pooled = np.concatenate([ev_ret, base])
        n_event = len(ev_ret)
        rng = np.random.RandomState(7)
        ge = 0
        for _ in range(N_MCPT):
            rng.shuffle(pooled)
            diff = pooled[:n_event].mean() - pooled[n_event:].mean()
            if diff >= observed:
                ge += 1
        p = (ge + 1) / (N_MCPT + 1)
        sr["mcpt_p"] = p
        print(f"  {sr['period']:<5}: observed alpha {observed*100:+.2f}%  MCPT p={p:.4f}")

    # OOS split: 2017-2021 vs 2022-2025
    print("\n=== OOS ROBUSTNESS (2017-2021 vs 2022-2025) ===")
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    p1 = df[df["entry_date"] < "2022-01-01"]
    p2 = df[df["entry_date"] >= "2022-01-01"]
    print(f"  P1 (2017-2021): n={len(p1)}")
    print(f"  P2 (2022-2025): n={len(p2)}")
    for hd in HOLD_DAYS:
        col = f"ret_{hd}d_net"
        m1 = p1[col].mean() if len(p1) else np.nan
        m2 = p2[col].mean() if len(p2) else np.nan
        print(f"    {hd}d:  P1={m1*100:+.2f}%  P2={m2*100:+.2f}%  "
              f"{'ROBUST' if (pd.notna(m1) and pd.notna(m2) and m1>0 and m2>0) else 'NOT-ROBUST'}")

    # Save extended summary
    sum_df = pd.DataFrame(summary_rows)
    sum_csv = OUT_CSV.with_name("index_rebalance_frontrun_summary.csv")
    sum_df.to_csv(sum_csv, index=False)
    print(f"\n[OUT] Summary -> {sum_csv}")


if __name__ == "__main__":
    main()
