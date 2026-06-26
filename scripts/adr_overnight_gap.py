"""
Backtest #5 — TW ADR overnight gap (TSM/UMC ADR -> 2330/2303 next-TW-day)

Question: TSM ADR overnight % move -> does 2330 next TW-day open->close
          (a) FADE (mean revert) or (b) FOLLOW (momentum)?

Signal:
  TSM_overnight_pct = (TSM_close[t] - TSM_close[t-1]) / TSM_close[t-1] * 100
  Pair to first TW trading day strictly AFTER TSM_close[t] timestamp.
  Target = (2330_close[t+1] - 2330_open[t+1]) / 2330_open[t+1] * 100

Strategies:
  Fade:   |TSM_overnight| > 1.5% -> reverse trade
  Follow: |TSM_overnight| > 1.5% -> same direction

Cost: 0.585% round-trip (single-stock TW)
Gate: mean_net > 0.3%, t > 2.0, OOS robust both halves, MCPT p<0.05
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path("C:/Users/USER/Desktop/INVEST")
GLOBAL = ROOT / "data" / "cache" / "yfinance" / "global"
TEMP = ROOT / "data" / "cache" / "yfinance"
OUT = ROOT / "logs" / "adr_overnight_gap.csv"

COST_RT = 0.585  # % round-trip single-stock TW
SPLIT_DATE = pd.Timestamp("2018-01-01")  # OOS split (in1=2010-2017, in2=2018-2025)
EXTREME_THR = 1.5  # |TSM overnight %|
N_MCPT = 2000
RNG = np.random.default_rng(42)


def load_adr(path: Path, label: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "date" in df.columns:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
    else:
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df.index.name = "date"
    return df


def overnight_pct(adr: pd.DataFrame) -> pd.Series:
    return (adr["close"] / adr["close"].shift(1) - 1.0) * 100


def pair_signal_target(adr: pd.DataFrame, tw: pd.DataFrame) -> pd.DataFrame:
    """
    For each TSM trading day t, find first TW trading day strictly > t (calendar-wise).
    The TW open is the next morning after ADR close (ET evening -> TW morning ~13h later).
    """
    sig = overnight_pct(adr).rename("tsm_overnight_pct").to_frame()
    sig["adr_date"] = sig.index

    tw_dates = tw.index.values  # numpy datetime64
    # for each adr_date find first tw_date > adr_date
    pos = np.searchsorted(tw_dates, sig.index.values, side="right")
    valid = pos < len(tw_dates)
    sig = sig[valid].copy()
    pos = pos[valid]
    sig["tw_date"] = pd.to_datetime(tw_dates[pos])

    tw_o = tw["open"].reindex(sig["tw_date"].values).values
    tw_c = tw["close"].reindex(sig["tw_date"].values).values
    sig["tw_open"] = tw_o
    sig["tw_close"] = tw_c
    sig["tw_o2c_pct"] = (tw_c / tw_o - 1.0) * 100
    # Drop NaN target / signal
    sig = sig.dropna(subset=["tsm_overnight_pct", "tw_o2c_pct"])
    return sig.reset_index(drop=True)


def quintile_table(df: pd.DataFrame, label: str) -> pd.DataFrame:
    bins = [-np.inf, -2, -1, 1, 2, np.inf]
    df = df.copy()
    df["bucket"] = pd.cut(df["tsm_overnight_pct"], bins=bins,
                          labels=["<-2%", "-2~-1%", "-1~+1%", "+1~+2%", ">+2%"])
    rows = []
    for b, g in df.groupby("bucket", observed=True):
        n = len(g)
        if n == 0:
            continue
        mean = g["tw_o2c_pct"].mean()
        median = g["tw_o2c_pct"].median()
        std = g["tw_o2c_pct"].std(ddof=1)
        t = mean / (std / np.sqrt(n)) if std > 0 else np.nan
        rows.append({"pair": label, "bucket": str(b), "n": n,
                     "mean_o2c_pct": round(mean, 4),
                     "median_pct": round(median, 4),
                     "t_stat": round(t, 3)})
    return pd.DataFrame(rows)


def backtest_strategy(df: pd.DataFrame, mode: str, threshold: float = EXTREME_THR) -> dict:
    """mode = 'fade' or 'follow'."""
    sub = df[df["tsm_overnight_pct"].abs() > threshold].copy()
    if len(sub) == 0:
        return {"mode": mode, "n": 0}
    direction = np.sign(sub["tsm_overnight_pct"].values)
    if mode == "fade":
        # bet against ADR direction
        pnl = -direction * sub["tw_o2c_pct"].values
    else:
        pnl = direction * sub["tw_o2c_pct"].values
    pnl_net = pnl - COST_RT
    n = len(pnl_net)
    mean = pnl_net.mean()
    std = pnl_net.std(ddof=1)
    t = mean / (std / np.sqrt(n)) if std > 0 else np.nan
    win_rate = (pnl_net > 0).mean() * 100
    return {
        "mode": mode,
        "threshold": threshold,
        "n": n,
        "mean_gross_pct": round(pnl.mean(), 4),
        "mean_net_pct": round(mean, 4),
        "t_stat": round(t, 3),
        "win_rate_pct": round(win_rate, 2),
        "std_pct": round(std, 4),
        "pnl_array": pnl_net,
    }


def oos_split(df: pd.DataFrame, mode: str, thr: float = EXTREME_THR) -> dict:
    in1 = df[df["tw_date"] < SPLIT_DATE]
    in2 = df[df["tw_date"] >= SPLIT_DATE]
    r1 = backtest_strategy(in1, mode, thr)
    r2 = backtest_strategy(in2, mode, thr)
    return {
        "p1_2010_2017_n": r1.get("n", 0),
        "p1_mean_net": r1.get("mean_net_pct", np.nan),
        "p1_t": r1.get("t_stat", np.nan),
        "p2_2018_2025_n": r2.get("n", 0),
        "p2_mean_net": r2.get("mean_net_pct", np.nan),
        "p2_t": r2.get("t_stat", np.nan),
        "robust": (r1.get("mean_net_pct", -1) > 0
                   and r2.get("mean_net_pct", -1) > 0
                   and r1.get("t_stat", 0) > 1.0
                   and r2.get("t_stat", 0) > 1.0),
    }


def mcpt(df: pd.DataFrame, mode: str, thr: float = EXTREME_THR, n_iter: int = N_MCPT) -> float:
    """Permute target, count how often shuffled mean_net >= observed."""
    obs = backtest_strategy(df, mode, thr)
    if obs["n"] == 0:
        return np.nan
    obs_mean = obs["mean_net_pct"]
    target = df["tw_o2c_pct"].values.copy()
    sig = df["tsm_overnight_pct"].values
    direction = np.sign(sig)
    mask = np.abs(sig) > thr
    if mask.sum() == 0:
        return np.nan
    cnt = 0
    for _ in range(n_iter):
        shuffled = RNG.permutation(target)
        if mode == "fade":
            pnl = -direction[mask] * shuffled[mask]
        else:
            pnl = direction[mask] * shuffled[mask]
        m = pnl.mean() - COST_RT
        if m >= obs_mean:
            cnt += 1
    return (cnt + 1) / (n_iter + 1)


def run_pair(adr_path: Path, tw_path: Path, label: str) -> tuple[pd.DataFrame, list[dict]]:
    adr = load_adr(adr_path, label)
    tw = load_adr(tw_path, label)
    df = pair_signal_target(adr, tw)
    print(f"\n=== {label} ===  pairs={len(df)}  ADR={adr.index.min().date()}~{adr.index.max().date()}  TW={tw.index.min().date()}~{tw.index.max().date()}")

    qt = quintile_table(df, label)
    print(qt.to_string(index=False))

    rows: list[dict] = []
    for mode in ["fade", "follow"]:
        for thr in [1.0, 1.5, 2.0]:
            res = backtest_strategy(df, mode, thr)
            if res["n"] == 0:
                continue
            oos = oos_split(df, mode, thr)
            p_mcpt = mcpt(df, mode, thr) if (res["mean_net_pct"] > 0 and res["t_stat"] > 1.5) else np.nan
            row = {
                "pair": label,
                "strategy": mode,
                "threshold_pct": thr,
                "n_total": res["n"],
                "mean_gross_pct": res["mean_gross_pct"],
                "mean_net_pct": res["mean_net_pct"],
                "t_stat": res["t_stat"],
                "win_rate_pct": res["win_rate_pct"],
                "p1_n": oos["p1_2010_2017_n"],
                "p1_mean_net": oos["p1_mean_net"],
                "p1_t": oos["p1_t"],
                "p2_n": oos["p2_2018_2025_n"],
                "p2_mean_net": oos["p2_mean_net"],
                "p2_t": oos["p2_t"],
                "oos_robust": oos["robust"],
                "mcpt_p": round(p_mcpt, 4) if not np.isnan(p_mcpt) else None,
                "gate_pass": (res["mean_net_pct"] > 0.3
                              and res["t_stat"] > 2.0
                              and oos["robust"]
                              and (p_mcpt is not None and not np.isnan(p_mcpt) and p_mcpt < 0.05)),
            }
            rows.append(row)
    return qt, rows


def main():
    pairs = []
    # TSM -> 2330
    pairs.append((GLOBAL / "TSM.parquet",
                  GLOBAL / "2330_TW.parquet", "TSM->2330"))
    # UMC -> 2303 (use temp downloaded)
    umc_p = TEMP / "_temp_UMC.parquet"
    twe_p = TEMP / "_temp_2303_TW.parquet"
    if umc_p.exists() and twe_p.exists():
        pairs.append((umc_p, twe_p, "UMC->2303"))
    else:
        print("UMC/2303 missing -- skipping that pair")

    all_rows: list[dict] = []
    qt_frames: list[pd.DataFrame] = []
    for adr, tw, label in pairs:
        qt, rows = run_pair(adr, tw, label)
        qt_frames.append(qt)
        all_rows.extend(rows)

    out_df = pd.DataFrame(all_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(OUT, index=False)
    print(f"\nWrote {OUT}  rows={len(out_df)}")

    # Print bucket tables and main results
    print("\n=== QUINTILE TABLES ===")
    print(pd.concat(qt_frames, ignore_index=True).to_string(index=False))
    print("\n=== STRATEGY RESULTS ===")
    show_cols = ["pair", "strategy", "threshold_pct", "n_total",
                 "mean_net_pct", "t_stat", "win_rate_pct",
                 "p1_mean_net", "p1_t", "p2_mean_net", "p2_t",
                 "oos_robust", "mcpt_p", "gate_pass"]
    print(out_df[show_cols].to_string(index=False))


if __name__ == "__main__":
    main()
