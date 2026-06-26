"""
AB Consensus Alpha Monthly Decompose
=====================================
Memory baseline: n=126, alpha +8.78%, t=+3.83
Hypothesis: alpha 靠 2020 COVID outlier; 剝離後 attenuated or 消失

Decompose:
  1. by year
  2. by month (1-12)
  3. by regime (TAIEX > MA200 vs <)
  4. 剝離 2020-03 ~ 2020-06
  5. within-year robust 比例 (year x month mean > 0 比例)
  6. OOS: 2017-2020 train / 2021-2025 test
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "logs" / "ab_consensus_events.csv"
TAIEX_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "^TWII.parquet"
OUT = ROOT / "logs" / "ab_consensus_decompose.csv"


def stats_block(arr: np.ndarray) -> dict:
    arr = np.asarray(arr, dtype=float)
    arr = arr[~np.isnan(arr)]
    n = len(arr)
    if n < 2:
        return {"n": n, "mean": np.nan, "median": np.nan,
                "std": np.nan, "sem": np.nan,
                "t": np.nan, "p_one": np.nan, "win": np.nan}
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    sem = std / np.sqrt(n)
    t, p = stats.ttest_1samp(arr, 0, alternative="greater")
    win = float((arr > 0).mean() * 100)
    return {"n": n, "mean": mean, "median": float(np.median(arr)),
            "std": std, "sem": sem, "t": float(t), "p_one": float(p), "win": win}


def fmt(d: dict) -> str:
    if np.isnan(d["mean"]):
        return f"n={d['n']:>4}  (insufficient)"
    return (f"n={d['n']:>4}  mean={d['mean']:+6.2f}%  "
            f"sem={d['sem']:5.2f}  t={d['t']:+5.2f}  "
            f"p={d['p_one']:.4f}  win={d['win']:5.1f}%")


def main():
    print("=" * 80)
    print("  AB CONSENSUS — MONTHLY / YEAR / REGIME DECOMPOSE")
    print("=" * 80)

    df = pd.read_csv(EVENTS, encoding="utf-8-sig")
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["ym"] = df["date"].dt.strftime("%Y-%m")
    df = df.dropna(subset=["fwd_60d"]).reset_index(drop=True)

    print(f"\n  Loaded events: n={len(df)}")
    print(f"  Date range: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"  Tickers: {df['ticker'].nunique()}")

    # --- Full sample baseline ---
    full = stats_block(df["fwd_60d"].values)
    print(f"\n  === [1] FULL SAMPLE ===")
    print(f"  {fmt(full)}")

    # --- By year ---
    print(f"\n  === [2] BY YEAR ===")
    print(f"  {'Year':<6} {'n':>4}  {'mean':>8}  {'sem':>5}  {'t':>5}  {'win%':>6}")
    year_rows = []
    for yr in sorted(df["year"].unique()):
        sub = df[df["year"] == yr]["fwd_60d"].values
        st = stats_block(sub)
        year_rows.append({"year": yr, **st})
        print(f"  {yr:<6} {st['n']:>4}  {st['mean']:>+7.2f}%  "
              f"{st['sem']:>5.2f}  {st['t']:>+5.2f}  {st['win']:>5.1f}%")

    # --- By month (1-12) ---
    print(f"\n  === [3] BY CALENDAR MONTH ===")
    print(f"  {'Mon':<4} {'n':>4}  {'mean':>8}  {'sem':>5}  {'t':>5}  {'win%':>6}")
    month_rows = []
    for m in range(1, 13):
        sub = df[df["month"] == m]["fwd_60d"].values
        st = stats_block(sub)
        month_rows.append({"month": m, **st})
        if st["n"] == 0:
            continue
        flag = " *" if st["p_one"] < 0.05 else ""
        print(f"  {m:<4} {st['n']:>4}  {st['mean']:>+7.2f}%  "
              f"{st['sem']:>5.2f}  {st['t']:>+5.2f}  {st['win']:>5.1f}%{flag}")

    # --- By Year-Month (for outlier hunt) ---
    print(f"\n  === [4] TOP/BOTTOM YEAR-MONTH CLUSTERS (n>=5) ===")
    ym_grp = df.groupby("ym")["fwd_60d"].agg(["count", "mean", "median"]).reset_index()
    ym_grp = ym_grp[ym_grp["count"] >= 5].sort_values("mean", ascending=False)
    print(f"  Top 8 ym (mean):")
    for _, r in ym_grp.head(8).iterrows():
        print(f"    {r['ym']}  n={int(r['count']):>3}  "
              f"mean={r['mean']:+6.2f}%  med={r['median']:+6.2f}%")
    print(f"  Bottom 5 ym (mean):")
    for _, r in ym_grp.tail(5).iterrows():
        print(f"    {r['ym']}  n={int(r['count']):>3}  "
              f"mean={r['mean']:+6.2f}%  med={r['median']:+6.2f}%")

    # --- 2020 stripped ---
    print(f"\n  === [5] 2020 OUTLIER STRIP ===")
    cov_mask = (df["date"] >= "2020-03-01") & (df["date"] <= "2020-06-30")
    cov_df = df[cov_mask]
    rest = df[~cov_mask]

    cov_st = stats_block(cov_df["fwd_60d"].values)
    rest_st = stats_block(rest["fwd_60d"].values)
    print(f"  COVID window 2020-03~06 : {fmt(cov_st)}")
    print(f"  Rest of sample          : {fmt(rest_st)}")
    print(f"  Δ mean (full - rest)    : {full['mean'] - rest_st['mean']:+.2f}pp")

    # All-2020 strip variant
    not2020 = df[df["year"] != 2020]
    n20_st = stats_block(not2020["fwd_60d"].values)
    print(f"  Strip ALL 2020          : {fmt(n20_st)}")

    # --- Within-year-month robust ratio ---
    print(f"\n  === [6] WITHIN YEAR-MONTH ROBUST CHECK ===")
    ym_min5 = ym_grp[ym_grp["count"] >= 5]
    pos_ym = (ym_min5["mean"] > 0).sum()
    tot_ym = len(ym_min5)
    print(f"  Year-Month buckets w/ n>=5: {tot_ym}")
    print(f"  Buckets with mean > 0    : {pos_ym} ({pos_ym/tot_ym*100:.1f}%)")
    print(f"  Threshold for robust     : >= 70%  → "
          f"{'✅ PASS' if pos_ym/tot_ym >= 0.70 else '❌ FAIL'}")

    # --- Year-only robust ---
    yr_arr = pd.DataFrame(year_rows)
    yr_min5 = yr_arr[yr_arr["n"] >= 5]
    pos_yr = (yr_min5["mean"] > 0).sum()
    tot_yr = len(yr_min5)
    print(f"\n  Year buckets w/ n>=5     : {tot_yr}")
    print(f"  Years with mean > 0      : {pos_yr} ({pos_yr/tot_yr*100:.1f}%)")

    # --- Regime via TAIEX MA200 ---
    print(f"\n  === [7] REGIME (TAIEX vs MA200) ===")
    if TAIEX_PATH.exists():
        tw = pd.read_parquet(TAIEX_PATH)
        tw["date"] = pd.to_datetime(tw["date"])
        tw = tw.sort_values("date").reset_index(drop=True)
        tw["ma200"] = tw["close"].rolling(200).mean()
        tw["above_ma200"] = tw["close"] > tw["ma200"]
        tw_lookup = tw.set_index("date")[["above_ma200", "close", "ma200"]]

        df["above_ma200"] = df["date"].map(
            lambda d: tw_lookup["above_ma200"].asof(d) if d >= tw_lookup.index.min() else np.nan
        )
        df["above_ma200"] = df["above_ma200"].astype("boolean")

        bull = df[df["above_ma200"] == True]["fwd_60d"].values
        bear = df[df["above_ma200"] == False]["fwd_60d"].values
        print(f"  Bull (close > MA200) : {fmt(stats_block(bull))}")
        print(f"  Bear (close <= MA200): {fmt(stats_block(bear))}")
    else:
        print("  TAIEX cache missing")

    # --- OOS split ---
    print(f"\n  === [8] OOS WALK-FORWARD ===")
    train = df[df["year"] <= 2020]["fwd_60d"].values
    test = df[df["year"] >= 2021]["fwd_60d"].values
    print(f"  Train 2017-2020 : {fmt(stats_block(train))}")
    print(f"  Test  2021-2025 : {fmt(stats_block(test))}")

    train_no20 = df[(df["year"] <= 2020) & (df["year"] != 2020)]["fwd_60d"].values
    print(f"  Train (no 2020) : {fmt(stats_block(train_no20))}")

    # --- Save decompose CSV ---
    rows = []
    rows.append({"bucket": "FULL", "key": "all", **full})
    for r in year_rows:
        rows.append({"bucket": "YEAR", "key": str(r["year"]),
                     **{k: r[k] for k in ["n", "mean", "median",
                                           "std", "sem", "t", "p_one", "win"]}})
    for r in month_rows:
        if r["n"] == 0:
            continue
        rows.append({"bucket": "MONTH", "key": f"M{r['month']:02d}",
                     **{k: r[k] for k in ["n", "mean", "median",
                                           "std", "sem", "t", "p_one", "win"]}})
    rows.append({"bucket": "STRIP", "key": "covid_mar_jun_2020", **cov_st})
    rows.append({"bucket": "STRIP", "key": "rest_minus_covid", **rest_st})
    rows.append({"bucket": "STRIP", "key": "rest_minus_all_2020", **n20_st})
    rows.append({"bucket": "OOS", "key": "train_2017_2020",
                 **stats_block(train)})
    rows.append({"bucket": "OOS", "key": "test_2021_2025",
                 **stats_block(test)})
    rows.append({"bucket": "OOS", "key": "train_no2020",
                 **stats_block(train_no20)})
    if TAIEX_PATH.exists():
        rows.append({"bucket": "REGIME", "key": "bull_above_ma200",
                     **stats_block(bull)})
        rows.append({"bucket": "REGIME", "key": "bear_below_ma200",
                     **stats_block(bear)})

    out_df = pd.DataFrame(rows)
    out_df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ Saved: {OUT.relative_to(ROOT)}")

    # --- Verdict ---
    print(f"\n  === [9] VERDICT GATE ===")
    new_mean = rest_st["mean"]
    new_t = rest_st["t"]
    if new_mean > 0 and new_t > 1.5:
        verdict = "ALPHA REAL but ATTENUATED"
    elif new_mean > 0 and new_t > 0:
        verdict = "ALPHA WEAK (t too low — cannot reject null)"
    else:
        verdict = "ALPHA 100% from 2020 OUTLIER → DEPRECATE"
    print(f"  Strip COVID 2020-03~06: mean={new_mean:+.2f}%  t={new_t:+.2f}")
    print(f"  → {verdict}")


if __name__ == "__main__":
    main()
