"""Dividend drift FULL UNIVERSE stress test (survivor bias correction).

Re-runs H2 (D-10 close -> D-1 close) backtest on the entire viable TW dividend
ETF universe (46 candidates, filtered to >= 2 ex-div events with usable history).

Outputs:
- logs/dividend_drift_full_universe.csv (per-ETF stats)
- logs/dividend_drift_full_universe_events.csv (raw events)
- logs/dividend_drift_full_universe_summary.csv (aggregate splits)

Compared to the original 5-ETF basket (0050/0056/00878/00713/00919) to
quantify survivor bias.
"""
from __future__ import annotations
import math, sys
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OHLCV_DIR = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
ANNOUNCE_DIR = ROOT / "data" / "cache" / "finmind" / "dividend"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Cost (round-trip ETF)
COST_ETF = 0.0034

# Original 5-ETF basket (for survivor-bias comparison baseline)
ORIGINAL_5 = ["0050", "0056", "00878", "00713", "00919"]

# Full TW dividend ETF universe (probed via yfinance — all have >= 2 div events
# and >= 30 trading days of history). 00773 dropped: not on yfinance.
UNIVERSE = [
    "0050", "0051", "0052", "0053", "0055", "0056", "0057",
    "006203", "006204", "006208",
    "00692", "00701", "00713", "00730", "00731", "00733",
    "00850", "00878", "00881", "00891", "00892", "00894",
    "00900", "00904", "00907", "00915", "00918", "00919",
    "00920", "00921", "00922", "00923", "00927", "00929",
    "00930", "00932", "00934", "00935", "00936", "00939",
    "00940", "00943", "00944", "00946", "00947",
    "00961", "00963",
]

# Hand-curated quarterly-payout ETFs (季配息) — verified from public ETF docs.
# Note: many post-2023 ETFs are monthly (月配); quarterly subset is narrower.
QUARTERLY = ["00878", "00713", "00919", "00929", "00891", "00892", "00894",
             "00904", "00907", "00915", "00918", "00921", "00927", "00934"]


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame | None:
    """Coerce any of the cache shapes (multi-index cols + Date index, or flat
    cols + date col) into [date, open, high, low, close, volume]."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [str(c[0]).lower() for c in df.columns]
    else:
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
    if "date" not in df.columns:
        df = df.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
    if "date" not in df.columns:
        return None
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(df.columns)):
        return None
    df["date"] = pd.to_datetime(df["date"])
    try:
        df["date"] = df["date"].dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    df["date"] = df["date"].dt.normalize()
    df = df[["date", "open", "high", "low", "close", "volume"]]
    return df.sort_values("date").reset_index(drop=True)


def fetch_ohlcv(ticker: str) -> pd.DataFrame | None:
    """Try local cache first, then yfinance."""
    p = OHLCV_DIR / f"{ticker}.parquet"
    if p.exists():
        try:
            raw = pd.read_parquet(p)
            df = _normalize_df(raw)
            if df is not None and len(df) > 30:
                return df
        except Exception as e:
            print(f"[cache-warn] {ticker}: {e}")
    try:
        hist = yf.Ticker(f"{ticker}.TW").history(period="max", auto_adjust=False)
        if hist is None or len(hist) < 30:
            return None
        return _normalize_df(hist.reset_index())
    except Exception as e:
        print(f"[ohlcv-err] {ticker}: {e}")
        return None


def fetch_dividends(ticker: str) -> pd.DataFrame:
    try:
        div = yf.Ticker(f"{ticker}.TW").dividends
        if div is None or len(div) == 0:
            return pd.DataFrame(columns=["ex_date", "cash"])
        df = div.reset_index()
        df.columns = ["ex_date", "cash"]
        df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.tz_localize(None).dt.normalize()
        return df
    except Exception as e:
        print(f"[div-err] {ticker}: {e}")
        return pd.DataFrame(columns=["ex_date", "cash"])


def event_h2(ohlcv: pd.DataFrame, ex_date: pd.Timestamp, cash: float) -> dict | None:
    """H2 only: D-10 close -> D-1 close gross."""
    df = ohlcv
    mask = df["date"] >= ex_date
    if not mask.any():
        return None
    d_idx = mask.idxmax()
    if d_idx < 11 or d_idx + 1 >= len(df):
        return None
    px_d_minus_10 = float(df.loc[d_idx - 10, "close"])
    px_d_minus_1 = float(df.loc[d_idx - 1, "close"])
    if px_d_minus_10 <= 0:
        return None
    h2 = px_d_minus_1 / px_d_minus_10 - 1.0
    return {"ex_date": ex_date, "cash": cash, "h2_gross": h2,
            "div_yield": cash / px_d_minus_1 if px_d_minus_1 > 0 else 0.0,
            "month": ex_date.month}


def aggregate(records: list[dict]) -> dict:
    if not records:
        return {"n": 0}
    df = pd.DataFrame(records)
    vals = df["h2_gross"].dropna().values
    n = len(vals)
    out = {"n": n}
    if n == 0:
        return out
    mean = float(np.mean(vals))
    std = float(np.std(vals, ddof=1)) if n > 1 else 0.0
    sem = std / math.sqrt(n) if n > 1 else 0.0
    t = mean / sem if sem > 0 else 0.0
    out.update({
        "h2_gross_mean": mean,
        "h2_gross_median": float(np.median(vals)),
        "h2_gross_std": std,
        "h2_gross_sem": sem,
        "h2_gross_t": t,
        "h2_gross_winrate": float((vals > 0).mean()),
        "h2_net_mean": mean - COST_ETF,
        # 95% CI lower bound (one-sided 5% conservative)
        "h2_net_ci_lower_95": (mean - COST_ETF) - 1.645 * sem,
    })
    return out


def mcpt_pvalue(observed_mean: float, all_ohlcv: dict[str, pd.DataFrame],
                event_pool: list[tuple[str, int]], n_perm: int = 1000,
                seed: int = 42) -> float:
    """Permutation: for each permutation, sample n random (ticker, idx) tuples
    matched to event count from same eligible windows; compute null mean."""
    rng = np.random.default_rng(seed)
    n = len(event_pool)
    # Build pool of (ticker, eligible_idx) across all OHLCV
    pool = []
    for tkr, df in all_ohlcv.items():
        if len(df) < 12:
            continue
        for i in range(11, len(df)):
            pool.append((tkr, i))
    if len(pool) < n:
        return 1.0
    null_means = []
    for _ in range(n_perm):
        sample_idx = rng.choice(len(pool), n, replace=False)
        rets = []
        for si in sample_idx:
            tkr, i = pool[si]
            df = all_ohlcv[tkr]
            try:
                p10 = df.loc[i - 10, "close"]
                p1 = df.loc[i - 1, "close"]
                if p10 > 0:
                    rets.append(p1 / p10 - 1.0)
            except Exception:
                pass
        if rets:
            null_means.append(np.mean(rets))
    if not null_means:
        return 1.0
    arr = np.array(null_means)
    return float((np.abs(arr) >= abs(observed_mean)).mean())


def feasibility_check(sample_size: int = 10) -> pd.DataFrame:
    """Step 5: sample 10 random events from announce parquet (stocks have
    AnnouncementDate; ETFs typically don't in our cache). Compute
    AnnouncementDate vs ex_date gap. Use any tickers that have non-empty
    AnnouncementDate."""
    rows = []
    for f in ANNOUNCE_DIR.glob("*_announce.parquet"):
        ticker = f.stem.replace("_announce", "")
        df = pd.read_parquet(f)
        if "AnnouncementDate" not in df.columns:
            continue
        df["AnnouncementDate"] = df["AnnouncementDate"].astype(str).str.strip()
        df = df[df["AnnouncementDate"] != ""]
        if df.empty:
            continue
        for _, r in df.iterrows():
            try:
                ad = pd.to_datetime(r["AnnouncementDate"])
                ex = pd.to_datetime(r["ex_date"])
                gap = (ex - ad).days
                rows.append({"ticker": ticker, "announce": ad.date(), "ex": ex.date(), "gap_days": gap})
            except Exception:
                continue
    fdf = pd.DataFrame(rows)
    if len(fdf) > sample_size:
        fdf = fdf.sample(sample_size, random_state=42).sort_values("ex")
    return fdf


def main():
    print(f"=== Dividend Drift FULL UNIVERSE Stress Test ===")
    print(f"Universe size: {len(UNIVERSE)} ETFs")
    per_ticker_rows = []
    all_recs = []
    all_ohlcv = {}

    for ticker in UNIVERSE:
        ohlcv = fetch_ohlcv(ticker)
        if ohlcv is None:
            print(f"[skip] {ticker}: no OHLCV")
            continue
        events = fetch_dividends(ticker)
        if len(events) < 2:
            print(f"[skip] {ticker}: <2 div events")
            continue
        all_ohlcv[ticker] = ohlcv
        recs = []
        for _, ev in events.iterrows():
            r = event_h2(ohlcv, ev["ex_date"], ev["cash"])
            if r is not None:
                r["ticker"] = ticker
                recs.append(r)
        if not recs:
            continue
        all_recs.extend(recs)
        agg = aggregate(recs)
        agg["ticker"] = ticker
        agg["in_original_5"] = ticker in ORIGINAL_5
        agg["in_quarterly"] = ticker in QUARTERLY
        per_ticker_rows.append(agg)
        print(f"[{ticker:7s}] n={agg['n']:3d}  "
              f"gross={agg.get('h2_gross_mean', 0)*100:+.2f}%  "
              f"t={agg.get('h2_gross_t', 0):+.2f}  "
              f"win={agg.get('h2_gross_winrate', 0)*100:.0f}%  "
              f"net={agg.get('h2_net_mean', 0)*100:+.2f}%")

    df_per = pd.DataFrame(per_ticker_rows)
    out_per = LOG_DIR / "dividend_drift_full_universe.csv"
    df_per.to_csv(out_per, index=False)
    print(f"\nSaved per-ETF -> {out_per}")

    df_all = pd.DataFrame(all_recs)
    out_evt = LOG_DIR / "dividend_drift_full_universe_events.csv"
    df_all.to_csv(out_evt, index=False)
    print(f"Saved raw events -> {out_evt}  (n={len(df_all)})")

    # =====================================================================
    # STEP 4: aggregate splits
    # =====================================================================
    print("\n=== Aggregate splits ===")
    splits = {
        "FULL_UNIVERSE": df_all,
        "ORIGINAL_5_ETF": df_all[df_all["ticker"].isin(ORIGINAL_5)],
        "QUARTERLY_ONLY": df_all[df_all["ticker"].isin(QUARTERLY)],
        "PRE_2023": df_all[df_all["ex_date"] < "2023-01-01"],
        "POST_2023": df_all[df_all["ex_date"] >= "2023-01-01"],
        "JUL_AUG": df_all[df_all["month"].isin([7, 8])],
    }
    summary_rows = []
    for label, sub in splits.items():
        if len(sub) < 5:
            print(f"[{label:18s}] n={len(sub)} (skipped)")
            continue
        agg = aggregate(sub.to_dict("records"))
        agg["split"] = label
        summary_rows.append(agg)
        print(f"[{label:18s}] n={agg['n']:4d}  "
              f"gross={agg['h2_gross_mean']*100:+.3f}%  "
              f"t={agg['h2_gross_t']:+.2f}  "
              f"win={agg['h2_gross_winrate']*100:.1f}%  "
              f"net={agg['h2_net_mean']*100:+.3f}%  "
              f"net_CI95_low={agg['h2_net_ci_lower_95']*100:+.3f}%")

    summary_df = pd.DataFrame(summary_rows)
    out_sum = LOG_DIR / "dividend_drift_full_universe_summary.csv"
    summary_df.to_csv(out_sum, index=False)
    print(f"Saved splits -> {out_sum}")

    # =====================================================================
    # MCPT on FULL_UNIVERSE
    # =====================================================================
    print("\n=== MCPT permutation tests ===")
    full_mean = df_all["h2_gross"].mean()
    pool_events = [(r["ticker"], r["ex_date"]) for r in all_recs]
    p_full = mcpt_pvalue(full_mean, all_ohlcv, pool_events, n_perm=1000)
    print(f"FULL_UNIVERSE: observed gross={full_mean*100:+.3f}%  MCPT p={p_full:.3f}")

    # MCPT on QUARTERLY
    qsub = df_all[df_all["ticker"].isin(QUARTERLY)]
    if len(qsub) >= 5:
        q_ohlcv = {t: all_ohlcv[t] for t in QUARTERLY if t in all_ohlcv}
        q_mean = qsub["h2_gross"].mean()
        p_q = mcpt_pvalue(q_mean, q_ohlcv, [], n_perm=1000)
        # n for quarterly subset
        # rebuild pool with proper n
        pool_q = [(r["ticker"], r["ex_date"]) for r in qsub.to_dict("records")]
        p_q = mcpt_pvalue(q_mean, q_ohlcv, pool_q, n_perm=1000)
        print(f"QUARTERLY_ONLY: observed gross={q_mean*100:+.3f}%  MCPT p={p_q:.3f}")

    # =====================================================================
    # STEP 5: feasibility check
    # =====================================================================
    print("\n=== STEP 5: announce-date feasibility (sample 10) ===")
    fdf = feasibility_check(sample_size=10)
    if fdf.empty:
        print("No ETFs have AnnouncementDate populated in cache; only stocks do.")
        print("ETF announce dates govern by 投信投顧公會 規範 (typ. >= 8 cal days before ex).")
    else:
        print(fdf.to_string(index=False))
        ok = (fdf["gap_days"] >= 10).sum()
        print(f"Feasibility: {ok}/{len(fdf)} events have announce >= 10 days before ex.")

    # =====================================================================
    # SURVIVOR BIAS DECISION
    # =====================================================================
    print("\n=== SURVIVOR BIAS QUANTIFICATION ===")
    full = next((r for r in summary_rows if r["split"] == "FULL_UNIVERSE"), None)
    orig = next((r for r in summary_rows if r["split"] == "ORIGINAL_5_ETF"), None)
    qrt = next((r for r in summary_rows if r["split"] == "QUARTERLY_ONLY"), None)
    if full and orig:
        bias_pp = (orig["h2_gross_mean"] - full["h2_gross_mean"]) * 100
        bias_pct = bias_pp / (full["h2_gross_mean"] * 100) if full["h2_gross_mean"] != 0 else float("nan")
        print(f"Original 5-ETF gross : {orig['h2_gross_mean']*100:+.3f}%  (n={orig['n']})")
        print(f"Full universe gross  : {full['h2_gross_mean']*100:+.3f}%  (n={full['n']})")
        print(f"Survivor bias        : +{bias_pp:.3f} pp (~{bias_pct:.1%} overstatement)")
        print(f"Full universe NET    : {full['h2_net_mean']*100:+.3f}%")
        print(f"Full universe NET CI95 lower : {full['h2_net_ci_lower_95']*100:+.3f}%")

        # Gate decision
        net = full["h2_net_mean"]
        gate = "KILL" if net < 0.007 else ("EDGE" if net < 0.010 else "MAYBE_DEPLOY")
        if gate == "MAYBE_DEPLOY":
            if qrt and qrt["h2_gross_t"] > 2.5:
                gate = "DEPLOY (pending feasibility)"
            else:
                gate = f"EDGE (quarterly t={qrt['h2_gross_t']:+.2f} < 2.5)" if qrt else "EDGE"
        print(f"\n>>> GATE DECISION: {gate}  (net={net*100:+.3f}%)")


if __name__ == "__main__":
    main()
