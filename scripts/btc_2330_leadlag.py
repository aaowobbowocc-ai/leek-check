"""
Backtest #2 — BTC → 2330 lead-lag

Hypothesis: BTC 24h pump leads TSMC (2330) by 1-3 days via global risk-on flow.

Tests:
 1) BTC[t] 24h % > +5%  -> 2330[t+1] open->close
 2) BTC[t] 24h % < -5%  -> 2330[t+1] open->close
 3) BTC[t] 5d cum % > +15% -> 2330[t+1..t+5] forward 5d return
 4) BTC[t-1..t] 2d signed momentum -> 2330[t+1] direction (binary acc)

Quintile bucket + monotonicity check.
MCPT 1000 perms + OOS 2014-2020 vs 2021-2026.

Cost: 0.585% round-trip (individual stock)
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

INVEST_ROOT = Path(r"c:/Users/USER/Desktop/INVEST")
TSMC_PARQUET = INVEST_ROOT / "data" / "cache" / "yfinance" / "global" / "2330_TW.parquet"
BTC_CACHE = INVEST_ROOT / "data" / "cache" / "yfinance" / "global" / "BTC_USD.parquet"
OUT_CSV = INVEST_ROOT / "logs" / "btc_2330_leadlag.csv"

COST_RT = 0.00585  # 0.585% round-trip per-trade


def fetch_btc() -> pd.DataFrame:
    """Fetch BTC-USD daily OHLCV via yfinance, cached."""
    if BTC_CACHE.exists():
        df = pd.read_parquet(BTC_CACHE)
        # auto-refresh if cache is older than 7 days
        last = pd.to_datetime(df["date"]).max()
        if (pd.Timestamp.utcnow().tz_localize(None) - last).days < 7:
            return df
    import yfinance as yf
    print("[fetch] downloading BTC-USD via yfinance...", flush=True)
    raw = yf.download("BTC-USD", start="2014-01-01", end=None, progress=False, auto_adjust=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.reset_index().rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Adj Close": "adj_close", "Volume": "volume"
    })
    raw["date"] = pd.to_datetime(raw["date"]).dt.tz_localize(None).dt.normalize()
    BTC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    raw.to_parquet(BTC_CACHE, index=False)
    return raw


def load_tsmc() -> pd.DataFrame:
    df = pd.read_parquet(TSMC_PARQUET)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    df["open_to_close"] = df["close"] / df["open"] - 1.0
    df["close_to_close"] = df["close"].pct_change()
    return df


def build_signal_table(btc: pd.DataFrame, tsmc: pd.DataFrame) -> pd.DataFrame:
    """
    Align BTC daily signal at UTC date t with 2330 next available trading day t+1.

    Logic: BTC close at UTC 23:59 of day t is fully observable before 2330 opens
    on day t+1 (TW market opens 09:00 UTC+8 = 01:00 UTC of day t+1, so BTC's
    UTC close on day t is ~1h before TW open the *same calendar* day t+1).
    For weekends, BTC sat+sun cumulative effect lands on Monday open via
    "carry-forward last available BTC date < tsmc_date".
    """
    btc = btc.sort_values("date").reset_index(drop=True).copy()
    btc["btc_24h"] = btc["close"].pct_change()
    btc["btc_5d"] = btc["close"].pct_change(5)
    btc["btc_2d"] = btc["close"].pct_change(2)

    tsmc = tsmc.sort_values("date").reset_index(drop=True).copy()

    # For each tsmc trading day d, the predictive BTC observation is the
    # most recent BTC close at date < d (so BTC of d-1 typically; for Monday
    # it picks Sunday which captures Sat+Sun cumulative via 24h pct).
    # Use "asof merge" with direction='backward' on (d - 1 microsecond).
    btc_for_merge = btc[["date", "btc_24h", "btc_5d", "btc_2d", "close"]].rename(
        columns={"close": "btc_close", "date": "btc_date"}
    )
    # We want the latest BTC date strictly less than tsmc date.
    tsmc_shifted = tsmc[["date"]].copy()
    tsmc_shifted["lookup"] = (tsmc_shifted["date"] - pd.Timedelta(seconds=1)).astype("datetime64[ns]")
    btc_for_merge["btc_date"] = btc_for_merge["btc_date"].astype("datetime64[ns]")
    merged = pd.merge_asof(
        tsmc_shifted.sort_values("lookup"),
        btc_for_merge.sort_values("btc_date"),
        left_on="lookup",
        right_on="btc_date",
        direction="backward",
    )
    # restore tsmc date alignment
    merged["date"] = (merged["lookup"] + pd.Timedelta(seconds=1)).astype("datetime64[ns]")
    merged = merged[["date", "btc_date", "btc_24h", "btc_5d", "btc_2d", "btc_close"]]

    tsmc["date"] = tsmc["date"].astype("datetime64[ns]")
    out = tsmc.merge(merged, on="date", how="left")

    # Forward returns for tsmc[t]: same day open->close (we'll use date as t+1
    # of the BTC signal). Also fwd 5d close-to-close.
    out["fwd_oc"] = out["open_to_close"]  # next-day after BTC = today open->close
    out["fwd_5d_cc"] = out["close"].pct_change(5).shift(-5)  # close[t+5]/close[t]-1
    out["next_close"] = out["close"].shift(-1)
    out["dir_up"] = (out["close_to_close"] > 0).astype(int)

    return out.dropna(subset=["btc_24h"]).reset_index(drop=True)


def mcpt_pvalue(observed: float, sample_returns: np.ndarray, all_returns: np.ndarray,
                 n_signals: int, n_perm: int = 1000, seed: int = 42) -> float:
    """Permutation: pick n_signals random fwd returns from full universe; count >= observed."""
    rng = np.random.default_rng(seed)
    full = all_returns[~np.isnan(all_returns)]
    if len(full) < n_signals or n_signals == 0:
        return float("nan")
    perm_means = np.empty(n_perm)
    for i in range(n_perm):
        idx = rng.choice(len(full), size=n_signals, replace=False)
        perm_means[i] = full[idx].mean()
    if observed >= 0:
        return float((perm_means >= observed).mean())
    return float((perm_means <= observed).mean())


def summarize(label: str, sub: pd.DataFrame, fwd_col: str, full_pop: np.ndarray,
              cost_rt: float = COST_RT) -> dict:
    sub = sub.dropna(subset=[fwd_col])
    n = len(sub)
    if n == 0:
        return {"label": label, "n": 0}
    rets = sub[fwd_col].to_numpy()
    mean_gross = rets.mean()
    win = (rets > 0).mean()
    std = rets.std(ddof=1) if n > 1 else float("nan")
    t_stat = mean_gross / (std / np.sqrt(n)) if std and std > 0 else float("nan")
    net = mean_gross - cost_rt
    pval = mcpt_pvalue(mean_gross, rets, full_pop, n)
    return {
        "label": label, "n": n,
        "mean_gross_pct": round(mean_gross * 100, 4),
        "mean_net_pct": round(net * 100, 4),
        "win_rate": round(win, 4),
        "t_stat": round(t_stat, 3) if not np.isnan(t_stat) else None,
        "mcpt_p": round(pval, 4) if not np.isnan(pval) else None,
    }


def quintile_table(df: pd.DataFrame, signal_col: str, fwd_col: str) -> pd.DataFrame:
    sub = df.dropna(subset=[signal_col, fwd_col]).copy()
    sub["q"] = pd.qcut(sub[signal_col], 5, labels=["Q1_low", "Q2", "Q3", "Q4", "Q5_high"])
    g = sub.groupby("q", observed=True)[fwd_col].agg(["count", "mean", "std"])
    g["mean_pct"] = (g["mean"] * 100).round(4)
    g["mean_net_pct"] = ((g["mean"] - COST_RT) * 100).round(4)
    return g


def run() -> None:
    print("[load] BTC + 2330", flush=True)
    btc = fetch_btc()
    tsmc = load_tsmc()
    df = build_signal_table(btc, tsmc)
    print(f"[align] joined rows: {len(df)}; date range {df['date'].min().date()} -> {df['date'].max().date()}",
          flush=True)

    # Crypto era only: post-2017-01-01 (avoid 2014-16 manipulation)
    df_full = df[df["date"] >= "2017-01-01"].copy()
    df_oos1 = df_full[df_full["date"] < "2021-01-01"].copy()  # 2017-2020
    df_oos2 = df_full[df_full["date"] >= "2021-01-01"].copy()  # 2021-2026

    full_oc = df_full["fwd_oc"].to_numpy()
    full_5d = df_full["fwd_5d_cc"].to_numpy()

    rows: list[dict] = []

    # ---- Test 1: BTC 24h > +5% -> 2330 next open->close ----
    for tag, sub in [("FULL_2017_2026", df_full), ("OOS1_2017_2020", df_oos1),
                     ("OOS2_2021_2026", df_oos2)]:
        sig = sub[sub["btc_24h"] > 0.05]
        rows.append({"test": "T1_btc24h_gt5", "period": tag,
                     **summarize(f"T1_{tag}", sig, "fwd_oc", full_oc)})

    # ---- Test 2: BTC 24h < -5% -> 2330 next open->close ----
    for tag, sub in [("FULL_2017_2026", df_full), ("OOS1_2017_2020", df_oos1),
                     ("OOS2_2021_2026", df_oos2)]:
        sig = sub[sub["btc_24h"] < -0.05]
        rows.append({"test": "T2_btc24h_lt-5", "period": tag,
                     **summarize(f"T2_{tag}", sig, "fwd_oc", full_oc)})

    # ---- Test 3: BTC 5d cum > +15% -> 2330 fwd 5d c2c ----
    for tag, sub in [("FULL_2017_2026", df_full), ("OOS1_2017_2020", df_oos1),
                     ("OOS2_2021_2026", df_oos2)]:
        sig = sub[sub["btc_5d"] > 0.15]
        rows.append({"test": "T3_btc5d_gt15", "period": tag,
                     **summarize(f"T3_{tag}", sig, "fwd_5d_cc", full_5d)})

    # ---- Test 4: BTC 2d signed momentum -> 2330 direction (binary acc) ----
    for tag, sub in [("FULL_2017_2026", df_full), ("OOS1_2017_2020", df_oos1),
                     ("OOS2_2021_2026", df_oos2)]:
        s = sub.dropna(subset=["btc_2d", "dir_up", "close_to_close"]).copy()
        s["pred"] = (s["btc_2d"] > 0).astype(int)
        s = s[s["btc_2d"].abs() > 0.01]  # ignore tiny moves
        if len(s) > 0:
            acc = (s["pred"] == s["dir_up"]).mean()
            n = len(s)
            # binomial 95% CI vs 0.5 baseline
            se = np.sqrt(0.5 * 0.5 / n)
            z = (acc - 0.5) / se if se > 0 else float("nan")
            rows.append({"test": "T4_btc2d_dir", "period": tag, "label": f"T4_{tag}",
                         "n": n, "accuracy": round(acc, 4),
                         "z_vs_0.5": round(z, 3),
                         "deployable": acc > 0.55})
        else:
            rows.append({"test": "T4_btc2d_dir", "period": tag, "label": f"T4_{tag}", "n": 0})

    # ---- Quintile / monotonicity (full sample) ----
    print("\n[quintile] BTC 24h pct -> 2330 next open->close (full 2017-2026):", flush=True)
    qt = quintile_table(df_full, "btc_24h", "fwd_oc")
    print(qt, flush=True)
    for q_label, r in qt.iterrows():
        rows.append({
            "test": "Q_btc24h_oc",
            "period": "FULL_2017_2026",
            "label": f"quintile_{q_label}",
            "n": int(r["count"]),
            "mean_gross_pct": float(r["mean_pct"]),
            "mean_net_pct": float(r["mean_net_pct"]),
        })

    # ---- Pearson correlation BTC 24h vs 2330 next-day o2c ----
    s = df_full.dropna(subset=["btc_24h", "fwd_oc"])
    pear = float(np.corrcoef(s["btc_24h"], s["fwd_oc"])[0, 1]) if len(s) > 2 else float("nan")
    s_oos1 = df_oos1.dropna(subset=["btc_24h", "fwd_oc"])
    pear_o1 = float(np.corrcoef(s_oos1["btc_24h"], s_oos1["fwd_oc"])[0, 1]) if len(s_oos1) > 2 else float("nan")
    s_oos2 = df_oos2.dropna(subset=["btc_24h", "fwd_oc"])
    pear_o2 = float(np.corrcoef(s_oos2["btc_24h"], s_oos2["fwd_oc"])[0, 1]) if len(s_oos2) > 2 else float("nan")
    rows.append({"test": "CORR_btc24h_oc", "period": "FULL_2017_2026", "label": "pearson",
                 "n": len(s), "pearson": round(pear, 4)})
    rows.append({"test": "CORR_btc24h_oc", "period": "OOS1_2017_2020", "label": "pearson",
                 "n": len(s_oos1), "pearson": round(pear_o1, 4)})
    rows.append({"test": "CORR_btc24h_oc", "period": "OOS2_2021_2026", "label": "pearson",
                 "n": len(s_oos2), "pearson": round(pear_o2, 4)})

    # ---- Save ----
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out_df = pd.DataFrame(rows)
    out_df.to_csv(OUT_CSV, index=False)
    print(f"\n[save] {OUT_CSV}", flush=True)

    # console summary
    print("\n=== RESULTS ===")
    print(out_df.to_string(index=False))


if __name__ == "__main__":
    run()
