"""Dividend drift backtest — 4 hypotheses tested across ETFs and large-cap stocks.

H1: Fill-the-gap alpha — D-5 entry, D+30 exit (does price recover ex-div drop?)
H2: Pre-ex-div drift up — D-10 -> D-1 close (chasing dividend?)
H3: Post-ex-div reversal — D+1 open -> D+5 close (selling pressure after?)
H4: Stock vs ETF — alpha differential

Data sources:
- yfinance Ticker.dividends for ex-dividend dates + cash amounts
- data/cache/yfinance/tw_ohlcv/{ticker}.parquet for OHLCV

Output: logs/dividend_drift.csv
"""
from __future__ import annotations
import os, sys, math
import numpy as np
import pandas as pd
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OHLCV_DIR = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
LOG_PATH = ROOT / "logs" / "dividend_drift.csv"

ETFS = ["0050", "0056", "00878", "00713", "00919"]
STOCKS = ["2330", "2317", "2454", "2308"]
ALL = ETFS + STOCKS

# Costs (round-trip)
COST_ETF = 0.0034
COST_STOCK = 0.00585


def load_ohlcv(ticker: str) -> pd.DataFrame | None:
    p = OHLCV_DIR / f"{ticker}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def get_div_events(ticker: str) -> pd.DataFrame:
    """Returns DataFrame with columns: ex_date (Timestamp, naive), cash"""
    sym = f"{ticker}.TW"
    div = yf.Ticker(sym).dividends
    if div is None or len(div) == 0:
        return pd.DataFrame(columns=["ex_date", "cash"])
    df = div.reset_index()
    df.columns = ["ex_date", "cash"]
    # Strip tz, keep date only
    df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.tz_localize(None).dt.normalize()
    return df


def event_returns(ohlcv: pd.DataFrame, ex_date: pd.Timestamp, cash: float) -> dict | None:
    """Return per-event drift metrics. None if data insufficient."""
    df = ohlcv
    # Find index of first trading day >= ex_date (= the ex-dividend trading day D)
    mask = df["date"] >= ex_date
    if not mask.any():
        return None
    d_idx = mask.idxmax()
    # require windows: D-10 .. D+30
    if d_idx < 11 or d_idx + 30 >= len(df):
        return None

    # Anchor prices
    px_d_minus_10 = float(df.loc[d_idx - 10, "close"])
    px_d_minus_5 = float(df.loc[d_idx - 5, "close"])
    px_d_minus_1_close = float(df.loc[d_idx - 1, "close"])
    px_d_open = float(df.loc[d_idx, "open"])
    px_d_close = float(df.loc[d_idx, "close"])
    px_d_plus_1_open = float(df.loc[d_idx + 1, "open"])
    px_d_plus_5_close = float(df.loc[d_idx + 5, "close"])
    px_d_plus_30_close = float(df.loc[d_idx + 30, "close"])

    # H1: Fill the gap -- enter D-5 close, exit D+30 close
    h1 = px_d_plus_30_close / px_d_minus_5 - 1.0

    # H2: Pre-drift: D-10 close -> D-1 close
    h2 = px_d_minus_1_close / px_d_minus_10 - 1.0

    # H3: Post-reversal: D+1 open -> D+5 close
    h3 = px_d_plus_5_close / px_d_plus_1_open - 1.0

    # Cash-dividend yield (relative to D-1 close) -- structural drop expected
    div_yield = cash / px_d_minus_1_close if px_d_minus_1_close > 0 else 0.0

    # Ex-div gap: D open vs D-1 close (should approx -div_yield)
    gap = px_d_open / px_d_minus_1_close - 1.0
    gap_anomaly = gap + div_yield  # negative => over-dropped, positive => under-dropped

    return {
        "ex_date": ex_date,
        "cash": cash,
        "div_yield": div_yield,
        "h1_fill_30d": h1,
        "h2_predrift_10d": h2,
        "h3_postrev_5d": h3,
        "gap_anomaly": gap_anomaly,
        "month": ex_date.month,
    }


def aggregate(records: list[dict], cost: float) -> dict:
    if not records:
        return {}
    df = pd.DataFrame(records)
    out = {"n": len(df)}
    for col in ["h1_fill_30d", "h2_predrift_10d", "h3_postrev_5d", "gap_anomaly", "div_yield"]:
        vals = df[col].dropna().values
        out[f"{col}_mean"] = float(np.mean(vals))
        out[f"{col}_median"] = float(np.median(vals))
        out[f"{col}_std"] = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
        # t-stat
        if len(vals) > 1 and out[f"{col}_std"] > 0:
            out[f"{col}_t"] = out[f"{col}_mean"] / (out[f"{col}_std"] / math.sqrt(len(vals)))
            out[f"{col}_winrate"] = float((vals > 0).mean())
        else:
            out[f"{col}_t"] = 0.0
            out[f"{col}_winrate"] = 0.0
    # net of costs
    out["h1_net"] = out["h1_fill_30d_mean"] - cost
    out["h2_net"] = out["h2_predrift_10d_mean"] - cost
    out["h3_short_net"] = -out["h3_postrev_5d_mean"] - cost  # short post
    return out


def mcpt_pvalue(observed: float, ohlcv: pd.DataFrame, n_events: int, hypothesis: str,
                n_perm: int = 1000, seed: int = 42) -> float:
    """Random N events from non-event dates -> compute same drift -> p-value."""
    rng = np.random.default_rng(seed)
    # All eligible indices (need same window)
    eligible = list(range(11, len(ohlcv) - 30))
    if len(eligible) < n_events:
        return 1.0
    null_means = []
    for _ in range(n_perm):
        idxs = rng.choice(eligible, n_events, replace=False)
        rets = []
        for i in idxs:
            try:
                if hypothesis == "h1":
                    r = ohlcv.loc[i + 30, "close"] / ohlcv.loc[i - 5, "close"] - 1.0
                elif hypothesis == "h2":
                    r = ohlcv.loc[i - 1, "close"] / ohlcv.loc[i - 10, "close"] - 1.0
                elif hypothesis == "h3":
                    r = ohlcv.loc[i + 5, "close"] / ohlcv.loc[i + 1, "open"] - 1.0
                else:
                    r = 0.0
                rets.append(r)
            except Exception:
                pass
        if rets:
            null_means.append(np.mean(rets))
    if not null_means:
        return 1.0
    null_arr = np.array(null_means)
    # two-sided p
    p = float((np.abs(null_arr) >= abs(observed)).mean())
    return p


def main():
    rows = []
    per_ticker_events = {}

    for ticker in ALL:
        ohlcv = load_ohlcv(ticker)
        if ohlcv is None:
            print(f"[skip] {ticker}: no OHLCV")
            continue
        events = get_div_events(ticker)
        if len(events) == 0:
            print(f"[skip] {ticker}: no dividends")
            continue

        recs = []
        for _, ev in events.iterrows():
            r = event_returns(ohlcv, ev["ex_date"], ev["cash"])
            if r is not None:
                r["ticker"] = ticker
                recs.append(r)

        per_ticker_events[ticker] = (ohlcv, recs)
        cost = COST_ETF if ticker in ETFS else COST_STOCK
        agg = aggregate(recs, cost)
        agg["ticker"] = ticker
        agg["type"] = "ETF" if ticker in ETFS else "STOCK"
        rows.append(agg)
        print(f"[{ticker}] n={agg.get('n', 0):3d} "
              f"H1_fill={agg.get('h1_fill_30d_mean', 0)*100:+.2f}% "
              f"H2_pre={agg.get('h2_predrift_10d_mean', 0)*100:+.2f}% "
              f"H3_post={agg.get('h3_postrev_5d_mean', 0)*100:+.2f}% "
              f"gap_anom={agg.get('gap_anomaly_mean', 0)*100:+.3f}%")

    df_per = pd.DataFrame(rows)
    df_per.to_csv(LOG_PATH, index=False)
    print(f"\nSaved per-ticker -> {LOG_PATH}")

    # Aggregate by group + month filter (Jul/Aug Taiwan ex-div peak)
    print("\n=== Group aggregates ===")
    all_recs = []
    for tkr, (_, recs) in per_ticker_events.items():
        all_recs.extend(recs)
    df_all = pd.DataFrame(all_recs)
    if df_all.empty:
        print("no events")
        return

    # Splits
    splits = {
        "ALL": df_all,
        "ETF": df_all[df_all["ticker"].isin(ETFS)],
        "STOCK": df_all[df_all["ticker"].isin(STOCKS)],
        "JulAug_ETF": df_all[(df_all["ticker"].isin(ETFS)) & (df_all["month"].isin([7, 8]))],
        "JulAug_STOCK": df_all[(df_all["ticker"].isin(STOCKS)) & (df_all["month"].isin([7, 8]))],
        "OOS1_2017_2020": df_all[df_all["ex_date"] < "2021-01-01"],
        "OOS2_2021_2025": df_all[df_all["ex_date"] >= "2021-01-01"],
    }
    summary_rows = []
    for label, sub in splits.items():
        if len(sub) < 5:
            print(f"[{label}] n={len(sub)} (skipped, too few)")
            continue
        cost = COST_ETF if "ETF" in label else COST_STOCK
        agg = aggregate(sub.to_dict("records"), cost)
        agg["split"] = label
        summary_rows.append(agg)
        print(f"[{label:18s}] n={agg['n']:3d} "
              f"H1={agg['h1_fill_30d_mean']*100:+.2f}% (t={agg['h1_fill_30d_t']:+.2f}) "
              f"H2={agg['h2_predrift_10d_mean']*100:+.2f}% (t={agg['h2_predrift_10d_t']:+.2f}) "
              f"H3={agg['h3_postrev_5d_mean']*100:+.2f}% (t={agg['h3_postrev_5d_t']:+.2f}) "
              f"gap={agg['gap_anomaly_mean']*100:+.3f}% (t={agg['gap_anomaly_t']:+.2f})")

    # MCPT for ALL group on best-looking hypothesis (after we see results)
    print("\n=== MCPT permutation tests (ALL events vs random) ===")
    # Use 0050 OHLCV as proxy benchmark for null sampling (same regime)
    bench = load_ohlcv("0050")
    n_all = len(df_all)
    for h in ["h1", "h2", "h3"]:
        col = {"h1": "h1_fill_30d", "h2": "h2_predrift_10d", "h3": "h3_postrev_5d"}[h]
        observed = df_all[col].mean()
        p = mcpt_pvalue(observed, bench, n_all, h, n_perm=1000)
        print(f"  {h}: observed={observed*100:+.3f}%  p={p:.3f}")

    # Save summary
    summary_df = pd.DataFrame(summary_rows)
    summary_path = LOG_PATH.with_name("dividend_drift_summary.csv")
    summary_df.to_csv(summary_path, index=False)
    print(f"\nSaved summary -> {summary_path}")

    # Save raw events
    raw_path = LOG_PATH.with_name("dividend_drift_events.csv")
    df_all.to_csv(raw_path, index=False)
    print(f"Saved raw events -> {raw_path}")


if __name__ == "__main__":
    main()
