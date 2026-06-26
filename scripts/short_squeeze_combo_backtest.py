"""
中-4 backtest: 融資餘額 + 融券餘額 combo short squeeze indicator.

NOTE: TW retail SBL (借券) public data is sparse; we proxy "borrow demand" with
ShortSaleTodayBalance (融券餘額). Combined with MarginPurchaseTodayBalance
(融資餘額, retail long), high combo z-score = both retail long & retail short
crowded -> potential squeeze setup.

Tests 3 strategies:
  S1 risk-avoid: combo_z>3 next 5d/10d/20d return (we want to know if AVOID)
  S2 bear short: combo_z>3 -> next 5d short (informational)
  S3 squeeze long: combo_z>3 AND day-after break-out > 3% -> 5d/10d momentum
"""
from __future__ import annotations
import os, sys, math, json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MARGIN_DIR = ROOT / "data/cache/finmind/finmind"   # TaiwanStockMarginPurchaseShortSale_*
OHLCV_DIR  = ROOT / "data/cache/yfinance/tw_ohlcv"
LOG_OUT    = ROOT / "logs/short_squeeze_combo.csv"
SUMMARY    = ROOT / "logs/short_squeeze_combo_summary.json"

COST_RT = 0.00585  # 0.585% round-trip 個股
COMBO_Z_THRESH = 3.0
LOOKBACK = 60
HOLD_DAYS = [5, 10, 20]
BREAKOUT_NEXTDAY_PCT = 0.03  # +3% for S3

OOS_SPLIT_DATE = "2023-01-01"  # IS: 2017-2022, OOS: 2023-now
MIN_PRICE = 10.0
MIN_AVG_DV = 50_000_000  # NT$ avg daily turnover ~5kw / day liquid

def load_universe(top_n: int = 150) -> list[str]:
    """Pick tickers with both margin parquet & ohlcv, ranked by 90d avg dollar volume."""
    margin_files = list(MARGIN_DIR.glob("TaiwanStockMarginPurchaseShortSale_*.parquet"))
    candidates = []
    for f in margin_files:
        tk = f.stem.replace("TaiwanStockMarginPurchaseShortSale_", "")
        oh = OHLCV_DIR / f"{tk}.parquet"
        if not oh.exists():
            continue
        try:
            df = pd.read_parquet(oh, columns=["date","close","volume"])
        except Exception:
            continue
        if len(df) < 200:
            continue
        df = df.tail(180)
        dv = (df["close"] * df["volume"]).mean()
        last_close = df["close"].iloc[-1]
        if last_close < MIN_PRICE or dv < MIN_AVG_DV:
            continue
        candidates.append((tk, dv))
    candidates.sort(key=lambda x: -x[1])
    return [t for t, _ in candidates[:top_n]]

def load_ticker(tk: str) -> pd.DataFrame | None:
    mf = MARGIN_DIR / f"TaiwanStockMarginPurchaseShortSale_{tk}.parquet"
    of = OHLCV_DIR / f"{tk}.parquet"
    if not (mf.exists() and of.exists()):
        return None
    m = pd.read_parquet(mf)[["date","MarginPurchaseTodayBalance","ShortSaleTodayBalance"]]
    o = pd.read_parquet(of)
    m["date"] = pd.to_datetime(m["date"])
    o["date"] = pd.to_datetime(o["date"])
    df = pd.merge(o, m, on="date", how="left").sort_values("date").reset_index(drop=True)
    df = df.rename(columns={
        "MarginPurchaseTodayBalance":"margin_bal",
        "ShortSaleTodayBalance":"short_bal",
    })
    df["margin_bal"] = df["margin_bal"].ffill()
    df["short_bal"]  = df["short_bal"].ffill()
    return df

def compute_combo_z(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # rolling 60d z-score on raw balances (proxy for "ratio surge" since shares-out is constant short term)
    for col in ("margin_bal","short_bal"):
        m = df[col].rolling(LOOKBACK).mean()
        s = df[col].rolling(LOOKBACK).std(ddof=0)
        df[f"{col}_z"] = (df[col] - m) / s.replace(0, np.nan)
    df["combo_z"] = df["margin_bal_z"].fillna(0) + df["short_bal_z"].fillna(0)
    return df

def collect_events(top_n=150) -> pd.DataFrame:
    rows = []
    universe = load_universe(top_n)
    print(f"[universe] {len(universe)} tickers (top by 90d turnover)")
    for i, tk in enumerate(universe):
        df = load_ticker(tk)
        if df is None or len(df) < LOOKBACK + 30:
            continue
        df = compute_combo_z(df)
        # require both balances meaningful
        df = df[df["margin_bal"] > 0]
        # event: combo_z > 3 (today close), entry next-day open
        sig = df[df["combo_z"] > COMBO_Z_THRESH].copy()
        if sig.empty:
            continue
        # de-dup: keep events spaced >=10 days apart
        sig = sig.sort_values("date").reset_index(drop=True)
        kept_idx = []
        last_date = None
        for idx, r in sig.iterrows():
            if last_date is None or (r["date"] - last_date).days >= 10:
                kept_idx.append(idx)
                last_date = r["date"]
        sig = sig.loc[kept_idx]
        for _, r in sig.iterrows():
            ev_date = r["date"]
            ev_idx = df.index[df["date"] == ev_date][0]
            # entry: next day open (avoid look-ahead)
            if ev_idx + 1 >= len(df):
                continue
            entry_open = df.iloc[ev_idx + 1]["open"]
            if pd.isna(entry_open) or entry_open <= 0:
                continue
            day1_close = df.iloc[ev_idx + 1]["close"]
            day1_ret = day1_close / entry_open - 1.0
            row = {
                "ticker": tk,
                "event_date": ev_date,
                "entry_date": df.iloc[ev_idx + 1]["date"],
                "entry_open": entry_open,
                "combo_z": r["combo_z"],
                "margin_bal_z": r["margin_bal_z"],
                "short_bal_z": r["short_bal_z"],
                "day1_ret": day1_ret,
            }
            for h in HOLD_DAYS:
                exit_idx = ev_idx + 1 + h
                if exit_idx >= len(df):
                    row[f"ret_{h}d"] = np.nan
                else:
                    exit_close = df.iloc[exit_idx]["close"]
                    row[f"ret_{h}d"] = exit_close / entry_open - 1.0
            # baseline: same-ticker random-day return for the same horizon (avg over later 60 days)
            for h in HOLD_DAYS:
                future_close = df["close"].iloc[ev_idx + 1 : ev_idx + 1 + 60]
                if len(future_close) < h + 5:
                    row[f"baseline_{h}d"] = np.nan
                    continue
                # rolling forward h-day return for next 60 days, mean
                fc = future_close.values
                rets = []
                for k in range(0, len(fc) - h):
                    rets.append(fc[k + h] / fc[k] - 1.0)
                row[f"baseline_{h}d"] = float(np.mean(rets)) if rets else np.nan
            rows.append(row)
        if (i+1) % 20 == 0:
            print(f"  scanned {i+1}/{len(universe)} ({tk})  events_so_far={len(rows)}")
    return pd.DataFrame(rows)

def mcpt(events: pd.DataFrame, ret_col: str, n_perm: int = 1000) -> float:
    """Monte Carlo permutation test: shuffle event dates within each ticker, recompute mean ret.
       Approximation: we permute by drawing random forward h-day returns from each ticker's history.
       We'll use a simpler bootstrap proxy via baseline_*: compare mean(ret) to mean(baseline)."""
    actual = events[ret_col].dropna().values
    base = events[ret_col.replace("ret_","baseline_")].dropna().values
    if len(actual) < 5 or len(base) < 5:
        return 1.0
    obs_diff = actual.mean() - base.mean()
    rng = np.random.default_rng(42)
    pool = np.concatenate([actual, base])
    n_a = len(actual)
    cnt = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        d = pool[:n_a].mean() - pool[n_a:].mean()
        if abs(d) >= abs(obs_diff):
            cnt += 1
    return cnt / n_perm

def summarize(events: pd.DataFrame) -> dict:
    out = {"n_total": len(events), "n_tickers": events["ticker"].nunique() if len(events) else 0}
    if len(events) == 0:
        return out
    events["entry_date"] = pd.to_datetime(events["entry_date"])
    is_mask = events["entry_date"] < OOS_SPLIT_DATE
    oos_mask = ~is_mask
    out["n_is"] = int(is_mask.sum())
    out["n_oos"] = int(oos_mask.sum())

    for h in HOLD_DAYS:
        rc = f"ret_{h}d"
        bc = f"baseline_{h}d"
        # net of cost (long)
        net = events[rc] - COST_RT
        # alpha vs baseline (informational - both gross)
        alpha = events[rc] - events[bc]

        sub = {
            "mean_gross": float(events[rc].mean(skipna=True)),
            "mean_net_long": float(net.mean(skipna=True)),
            "win_rate": float((events[rc] > 0).mean()),
            "win_rate_net": float((net > 0).mean()),
            "median": float(events[rc].median(skipna=True)),
            "std": float(events[rc].std(skipna=True)),
            "alpha_vs_baseline": float(alpha.mean(skipna=True)),
            "mcpt_p": mcpt(events, rc),
        }
        # IS / OOS
        sub["is_mean"] = float(events.loc[is_mask, rc].mean(skipna=True))
        sub["oos_mean"] = float(events.loc[oos_mask, rc].mean(skipna=True))
        sub["is_alpha"] = float((events.loc[is_mask, rc] - events.loc[is_mask, bc]).mean(skipna=True))
        sub["oos_alpha"] = float((events.loc[oos_mask, rc] - events.loc[oos_mask, bc]).mean(skipna=True))
        sub["is_n"] = int(events.loc[is_mask, rc].notna().sum())
        sub["oos_n"] = int(events.loc[oos_mask, rc].notna().sum())
        sub["is_win"] = float((events.loc[is_mask, rc] > 0).mean())
        sub["oos_win"] = float((events.loc[oos_mask, rc] > 0).mean())
        out[f"H{h}"] = sub

    # S3: squeeze + breakout next-day > 3% (entry at day-1 CLOSE so we don't
    #     double-count the breakout itself; ret measured from day1_close to day1_close+h)
    sq = events[events["day1_ret"] > BREAKOUT_NEXTDAY_PCT].copy()
    out["S3_n"] = len(sq)
    if len(sq) >= 10:
        # recompute returns from day1_close: ret_h_after_breakout = (1 + ret_h)/(1 + day1_ret) - 1
        for h in HOLD_DAYS:
            rc = f"ret_{h}d"
            bc = f"baseline_{h}d"
            adj = (1 + sq[rc]) / (1 + sq["day1_ret"]) - 1
            net = adj - COST_RT
            # OOS split for S3
            sq_is = sq[sq["entry_date"] < OOS_SPLIT_DATE]
            sq_oos = sq[sq["entry_date"] >= OOS_SPLIT_DATE]
            adj_is = (1 + sq_is[rc]) / (1 + sq_is["day1_ret"]) - 1
            adj_oos = (1 + sq_oos[rc]) / (1 + sq_oos["day1_ret"]) - 1
            # MCPT for S3 (post-breakout vs baseline)
            base = sq[bc].dropna().values
            actual = adj.dropna().values
            if len(actual) >= 5 and len(base) >= 5:
                obs_diff = actual.mean() - base.mean()
                pool = np.concatenate([actual, base])
                rng = np.random.default_rng(7)
                cnt = 0
                for _ in range(1000):
                    rng.shuffle(pool)
                    d = pool[:len(actual)].mean() - pool[len(actual):].mean()
                    if abs(d) >= abs(obs_diff):
                        cnt += 1
                p_s3 = cnt / 1000
            else:
                p_s3 = 1.0
            out[f"S3_H{h}"] = {
                "n": int(adj.notna().sum()),
                "mean_gross_post_breakout": float(adj.mean(skipna=True)),
                "mean_net_post_breakout": float(net.mean(skipna=True)),
                "win_rate": float((adj > 0).mean()),
                "alpha_vs_baseline_post_breakout": float((adj - sq[bc]).mean(skipna=True)),
                "is_n": int(adj_is.notna().sum()),
                "oos_n": int(adj_oos.notna().sum()),
                "is_mean": float(adj_is.mean(skipna=True)) if len(adj_is) else None,
                "oos_mean": float(adj_oos.mean(skipna=True)) if len(adj_oos) else None,
                "mcpt_p_post_breakout": p_s3,
                # also keep raw inclusive (for reference)
                "raw_inclusive_mean": float(sq[rc].mean(skipna=True)),
            }
    return out

def main():
    LOG_OUT.parent.mkdir(exist_ok=True, parents=True)
    print(f"[start] short squeeze combo backtest")
    events = collect_events(top_n=150)
    print(f"[events] total={len(events)}")
    if len(events) == 0:
        print("No events found.")
        return
    events.to_csv(LOG_OUT, index=False)
    print(f"[saved] {LOG_OUT}")

    s = summarize(events)
    SUMMARY.write_text(json.dumps(s, indent=2, default=str))
    print(f"[saved] {SUMMARY}")
    print(json.dumps(s, indent=2, default=str))

if __name__ == "__main__":
    main()
