"""
Intraday vs Overnight Momentum Decomposition (TW market)
=========================================================

Academic basis:
  Lou-Polk-Skouras style decomposition (ScienceDirect 2023, S0927538X23002226):
    "Past intraday returns generate significantly positive momentum,
     past overnight returns generate significantly negative momentum"
  TW retail-driven market => intraday underreaction (momentum) +
  overnight overreaction (mean-reversion next month).

Hypotheses (long-only, retail-deployable):
  H1: Top decile by 60d cumulative INTRADAY returns -> outperform next month
  H2: Top decile by 60d cumulative OVERNIGHT returns -> underperform next month (avoid signal)
  H3 (control): Bottom decile by 60d cumulative OVERNIGHT returns -> outperform (mean-reversion)

Backtest:
  - Period: 2017-01 to 2026-05 (data available)
  - Universe: liquid TW stocks (median 60d turnover >= NT$100M)
  - Monthly rebalance (last trading day -> hold next month)
  - Equal-weight long, top/bottom 10%
  - Cost: 0.585%/round-trip, monthly turnover assumed full -> 0.585% drag/month
    (We'll compute actual turnover for accurate costing)

Validation gates:
  - vs 0050 benchmark
  - vs same-ticker random-entry baseline (1000 monthly random pulls from universe)
  - MCPT p < 0.05 (block bootstrap on monthly excess returns)
  - OOS split: 2017-2020 vs 2021-2026

Output:
  logs/intraday_overnight_momentum.csv
  Console summary -> verdict (DEPLOY / EDGE / FAIL)
"""

from __future__ import annotations

import os
import sys
import glob
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path("c:/Users/USER/Desktop/INVEST")
TW_DIR = ROOT / "data/cache/yfinance/tw_ohlcv"
BENCH_PATH = ROOT / "data/cache/yfinance/tw_ohlcv/0050.parquet"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ----- Params -----
LOOKBACK = 60          # 60 trading days
DECILE_PCT = 0.10      # top/bottom 10%
LIQ_THRESH_TWD = 1e8   # NT$100M median 60d turnover
COST_RT = 0.00585      # 0.585% round-trip
START = "2017-01-01"
END = "2026-12-31"
OOS_SPLIT = "2021-01-01"
RAND_SEED = 42
N_MCPT = 1000

# Skip ETF / leveraged / inverse / bond / overseas
SKIP_PREFIXES = ("00",)  # ETFs in TW are 4-digit '00xxx'; we want individual stocks (4-digit not starting with 00)


def load_ticker_files():
    files = sorted(glob.glob(str(TW_DIR / "*.parquet")))
    out = []
    for f in files:
        sym = Path(f).stem
        # individual stocks: 4-digit not starting with '00'
        if not sym.isdigit():
            continue
        if sym.startswith("00"):
            continue
        if len(sym) != 4:
            continue
        out.append((sym, f))
    return out


def load_panel():
    """Load all individual TW stocks into a long panel."""
    files = load_ticker_files()
    print(f"[load] {len(files)} individual TW stock files (4-digit, non-ETF)")
    frames = []
    for sym, f in files:
        try:
            df = pd.read_parquet(f, columns=["date", "open", "high", "low", "close", "volume"])
        except Exception:
            continue
        if df.empty or len(df) < 200:
            continue
        df["ticker"] = sym
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel[(panel["date"] >= START) & (panel["date"] <= END)]
    panel = panel.sort_values(["ticker", "date"]).reset_index(drop=True)
    print(f"[load] panel rows: {len(panel):,}, tickers: {panel['ticker'].nunique()}")
    return panel


def compute_signals(panel: pd.DataFrame) -> pd.DataFrame:
    """Compute intraday%, overnight%, cum sums, and returns."""
    panel = panel.sort_values(["ticker", "date"]).copy()
    g = panel.groupby("ticker", group_keys=False)

    panel["prev_close"] = g["close"].shift(1)
    # Intraday %: open->close
    panel["intra_pct"] = (panel["close"] - panel["open"]) / panel["open"] * 100
    # Overnight %: prev_close -> open
    panel["over_pct"] = (panel["open"] - panel["prev_close"]) / panel["prev_close"] * 100
    # Daily total return for performance attribution
    panel["ret"] = panel["close"] / panel["prev_close"] - 1.0
    # Turnover (NT$)
    panel["turnover"] = panel["close"] * panel["volume"]

    # Drop infs (e.g. price reset, splits)
    for c in ["intra_pct", "over_pct", "ret", "turnover"]:
        panel[c] = panel[c].replace([np.inf, -np.inf], np.nan)

    # Rolling cumulative sums (60-day window)
    g = panel.groupby("ticker", group_keys=False)
    panel["cum_intra_60d"] = g["intra_pct"].rolling(LOOKBACK, min_periods=LOOKBACK).sum().reset_index(level=0, drop=True)
    panel["cum_over_60d"] = g["over_pct"].rolling(LOOKBACK, min_periods=LOOKBACK).sum().reset_index(level=0, drop=True)
    panel["med_turnover_60d"] = g["turnover"].rolling(LOOKBACK, min_periods=LOOKBACK).median().reset_index(level=0, drop=True)

    return panel


def get_month_ends(panel: pd.DataFrame) -> list:
    """List of month-end trading dates available in panel."""
    dates = pd.Series(panel["date"].unique()).sort_values()
    df = pd.DataFrame({"date": dates})
    df["ym"] = df["date"].dt.to_period("M")
    me = df.groupby("ym")["date"].max().tolist()
    return me


def compute_monthly_returns(panel: pd.DataFrame, month_ends: list) -> pd.DataFrame:
    """For each ticker, return between consecutive month-end closes."""
    me = sorted(month_ends)
    rows = []
    pivot = panel.pivot(index="date", columns="ticker", values="close")
    pivot = pivot.reindex(sorted(pivot.index))
    for i in range(len(me) - 1):
        d0, d1 = me[i], me[i + 1]
        if d0 not in pivot.index or d1 not in pivot.index:
            continue
        s0 = pivot.loc[d0]
        s1 = pivot.loc[d1]
        ret = (s1 / s0 - 1.0)
        for tkr, r in ret.items():
            if pd.notna(r):
                rows.append({"rebalance_date": d0, "hold_until": d1, "ticker": tkr, "fwd_ret": r})
    out = pd.DataFrame(rows)
    return out


def build_portfolios(panel: pd.DataFrame, monthly_ret: pd.DataFrame) -> pd.DataFrame:
    """For each rebalance date, rank by signals & compute decile portfolio returns."""
    panel_idx = panel.set_index(["ticker", "date"])
    rows = []
    rebalance_dates = sorted(monthly_ret["rebalance_date"].unique())

    for rd in rebalance_dates:
        # Eligible universe: liquidity gate
        snap = panel[panel["date"] == rd].copy()
        snap = snap.dropna(subset=["cum_intra_60d", "cum_over_60d", "med_turnover_60d"])
        snap = snap[snap["med_turnover_60d"] >= LIQ_THRESH_TWD]
        if len(snap) < 30:
            continue

        # Get forward returns for these tickers
        rets_this = monthly_ret[monthly_ret["rebalance_date"] == rd].set_index("ticker")["fwd_ret"]
        snap = snap[snap["ticker"].isin(rets_this.index)].copy()
        snap["fwd_ret"] = snap["ticker"].map(rets_this)
        snap = snap.dropna(subset=["fwd_ret"])

        n = len(snap)
        if n < 30:
            continue
        k = max(int(n * DECILE_PCT), 5)

        # H1: top decile by cum_intra (long)
        h1 = snap.nlargest(k, "cum_intra_60d")
        # H2: top decile by cum_over (long, expect underperform)
        h2 = snap.nlargest(k, "cum_over_60d")
        # H3: bottom decile by cum_over (long, expect outperform - mean reversion)
        h3 = snap.nsmallest(k, "cum_over_60d")
        # Universe avg
        univ_mean = snap["fwd_ret"].mean()

        rows.append({
            "rebalance_date": rd,
            "n_universe": n,
            "k_decile": k,
            "H1_intra_top_ret": h1["fwd_ret"].mean(),
            "H2_over_top_ret": h2["fwd_ret"].mean(),
            "H3_over_bot_ret": h3["fwd_ret"].mean(),
            "universe_mean_ret": univ_mean,
            "H1_tickers": ",".join(h1["ticker"].tolist()),
            "H2_tickers": ",".join(h2["ticker"].tolist()),
            "H3_tickers": ",".join(h3["ticker"].tolist()),
        })
    return pd.DataFrame(rows)


def benchmark_returns(month_ends: list) -> pd.Series:
    """0050 monthly returns aligned to rebalance dates."""
    bench = pd.read_parquet(BENCH_PATH)
    bench["date"] = pd.to_datetime(bench["date"])
    bench = bench.set_index("date").sort_index()
    me = sorted(month_ends)
    out = {}
    for i in range(len(me) - 1):
        d0, d1 = me[i], me[i + 1]
        if d0 in bench.index and d1 in bench.index:
            r = bench.loc[d1, "close"] / bench.loc[d0, "close"] - 1.0
            out[d0] = r
    return pd.Series(out, name="bench_ret")


def random_baseline(monthly_ret: pd.DataFrame, panel: pd.DataFrame, n_iter=1000, k_pct=0.10) -> pd.Series:
    """Same-ticker random entry baseline — for each rebalance, pick random k tickers
    from liquid universe, repeat 1000x, mean monthly return."""
    rng = np.random.default_rng(RAND_SEED)
    rebalance_dates = sorted(monthly_ret["rebalance_date"].unique())
    panel_by_date = {rd: panel[panel["date"] == rd] for rd in rebalance_dates}

    rand_means = []
    for rd in rebalance_dates:
        snap = panel_by_date.get(rd)
        if snap is None:
            continue
        snap = snap.dropna(subset=["med_turnover_60d"])
        snap = snap[snap["med_turnover_60d"] >= LIQ_THRESH_TWD]
        rets_this = monthly_ret[monthly_ret["rebalance_date"] == rd].set_index("ticker")["fwd_ret"]
        elig = snap[snap["ticker"].isin(rets_this.index)]["ticker"].tolist()
        if len(elig) < 30:
            continue
        k = max(int(len(elig) * k_pct), 5)
        # Vectorized random sampling
        sims = []
        for _ in range(n_iter):
            picks = rng.choice(elig, size=k, replace=False)
            sims.append(rets_this.loc[picks].mean())
        rand_means.append({"rebalance_date": rd, "rand_mean": np.mean(sims), "rand_std": np.std(sims)})
    out = pd.DataFrame(rand_means)
    return out


def mcpt_test(strategy_excess: pd.Series, n_perm=1000, seed=42) -> float:
    """One-sample sign-flip permutation test on monthly excess returns.
    H0: mean excess return = 0. Two-sided p-value."""
    arr = strategy_excess.dropna().to_numpy()
    if len(arr) < 12:
        return 1.0
    obs = arr.mean()
    rng = np.random.default_rng(seed)
    perm_means = np.zeros(n_perm)
    for i in range(n_perm):
        signs = rng.choice([-1, 1], size=len(arr))
        perm_means[i] = (arr * signs).mean()
    p = float((np.abs(perm_means) >= abs(obs)).mean())
    return p


def turnover_rate(picks_series: pd.Series) -> float:
    """Average month-over-month change in holdings."""
    rates = []
    prev = None
    for s in picks_series:
        cur = set(s.split(",")) if isinstance(s, str) and s else set()
        if prev is not None and len(prev) > 0:
            changed = len(cur.symmetric_difference(prev)) / 2  # one-sided turnover
            rates.append(changed / max(len(prev), 1))
        prev = cur
    return float(np.mean(rates)) if rates else 0.0


def annualize_ret(monthly_ret_series: pd.Series) -> float:
    """Compound monthly returns to annualized."""
    r = monthly_ret_series.dropna()
    if len(r) == 0:
        return 0.0
    cum = (1 + r).prod()
    yrs = len(r) / 12.0
    return cum ** (1 / yrs) - 1.0


def summarize(name: str, ret: pd.Series, bench: pd.Series, rand: pd.Series,
              cost_drag: float = 0.0) -> dict:
    """Compute summary stats for a portfolio."""
    common = ret.index.intersection(bench.index).intersection(rand.index)
    r = ret.loc[common] - cost_drag
    b = bench.loc[common]
    rb = rand.loc[common]
    excess_vs_bench = r - b
    excess_vs_rand = r - rb
    tstat_vs_bench = excess_vs_bench.mean() / (excess_vs_bench.std(ddof=1) / np.sqrt(len(excess_vs_bench))) if len(excess_vs_bench) > 1 else 0.0
    tstat_vs_rand = excess_vs_rand.mean() / (excess_vs_rand.std(ddof=1) / np.sqrt(len(excess_vs_rand))) if len(excess_vs_rand) > 1 else 0.0
    p_mcpt_bench = mcpt_test(excess_vs_bench)
    p_mcpt_rand = mcpt_test(excess_vs_rand)
    return {
        "name": name,
        "n_months": len(common),
        "ann_ret_net": annualize_ret(r),
        "ann_ret_gross": annualize_ret(ret.loc[common]),
        "ann_bench": annualize_ret(b),
        "ann_rand": annualize_ret(rb),
        "alpha_vs_bench_pp": (annualize_ret(r) - annualize_ret(b)) * 100,
        "alpha_vs_rand_pp": (annualize_ret(r) - annualize_ret(rb)) * 100,
        "monthly_excess_vs_bench_mean_pp": excess_vs_bench.mean() * 100,
        "monthly_excess_vs_rand_mean_pp": excess_vs_rand.mean() * 100,
        "tstat_vs_bench": tstat_vs_bench,
        "tstat_vs_rand": tstat_vs_rand,
        "p_mcpt_vs_bench": p_mcpt_bench,
        "p_mcpt_vs_rand": p_mcpt_rand,
        "win_rate_vs_bench": float((excess_vs_bench > 0).mean()),
        "cost_drag_monthly_pp": cost_drag * 100,
    }


def main():
    print("=" * 70)
    print("Intraday vs Overnight Momentum Decomposition (TW)")
    print("=" * 70)

    panel = load_panel()
    print("[signals] computing intraday/overnight/cum sums…")
    panel = compute_signals(panel)

    month_ends = get_month_ends(panel)
    print(f"[rebalance] month-end dates: {len(month_ends)} ({month_ends[0].date()} -> {month_ends[-1].date()})")

    monthly_ret = compute_monthly_returns(panel, month_ends)
    print(f"[forward] monthly fwd-ret rows: {len(monthly_ret):,}")

    print("[portfolio] building H1/H2/H3 monthly portfolios…")
    port = build_portfolios(panel, monthly_ret)
    print(f"[portfolio] rebalance points: {len(port)}")

    bench = benchmark_returns(month_ends)
    print(f"[bench] 0050 months: {len(bench)}")

    print("[random] computing same-ticker random baseline (1000 sims/month)…")
    rand_df = random_baseline(monthly_ret, panel, n_iter=N_MCPT, k_pct=DECILE_PCT)
    rand = rand_df.set_index("rebalance_date")["rand_mean"]

    # Build series
    port_idx = port.set_index("rebalance_date")
    h1_ret = port_idx["H1_intra_top_ret"]
    h2_ret = port_idx["H2_over_top_ret"]
    h3_ret = port_idx["H3_over_bot_ret"]

    # Turnover-based cost drag
    h1_turn = turnover_rate(port_idx["H1_tickers"])
    h2_turn = turnover_rate(port_idx["H2_tickers"])
    h3_turn = turnover_rate(port_idx["H3_tickers"])
    print(f"[turnover] H1={h1_turn:.2%}/mo, H2={h2_turn:.2%}/mo, H3={h3_turn:.2%}/mo")

    h1_cost = h1_turn * COST_RT
    h2_cost = h2_turn * COST_RT
    h3_cost = h3_turn * COST_RT

    print()
    print("=" * 70)
    print("Full sample (2017-2026)")
    print("=" * 70)
    full_summaries = []
    for name, ret, c in [("H1_intraday_top", h1_ret, h1_cost),
                         ("H2_overnight_top", h2_ret, h2_cost),
                         ("H3_overnight_bot", h3_ret, h3_cost)]:
        s = summarize(name, ret, bench, rand, cost_drag=c)
        full_summaries.append(s)
        print(f"\n[{name}] cost_drag/mo={c*100:.3f}pp  n={s['n_months']}")
        print(f"  ann_ret_net={s['ann_ret_net']*100:.2f}%  bench={s['ann_bench']*100:.2f}%  rand={s['ann_rand']*100:.2f}%")
        print(f"  alpha vs bench={s['alpha_vs_bench_pp']:+.2f}pp/yr   vs rand={s['alpha_vs_rand_pp']:+.2f}pp/yr")
        print(f"  monthly excess vs bench: mean={s['monthly_excess_vs_bench_mean_pp']:+.3f}pp  t={s['tstat_vs_bench']:+.2f}  p_mcpt={s['p_mcpt_vs_bench']:.4f}")
        print(f"  monthly excess vs rand : mean={s['monthly_excess_vs_rand_mean_pp']:+.3f}pp  t={s['tstat_vs_rand']:+.2f}  p_mcpt={s['p_mcpt_vs_rand']:.4f}")
        print(f"  win_rate vs bench: {s['win_rate_vs_bench']:.2%}")

    # OOS split
    print()
    print("=" * 70)
    print(f"OOS Split: IS=2017-{OOS_SPLIT[:4]} | OOS={OOS_SPLIT[:4]}-2026")
    print("=" * 70)
    split_dt = pd.Timestamp(OOS_SPLIT)
    oos_summaries = []
    for name, ret, c in [("H1_intraday_top", h1_ret, h1_cost),
                         ("H2_overnight_top", h2_ret, h2_cost),
                         ("H3_overnight_bot", h3_ret, h3_cost)]:
        is_ret = ret[ret.index < split_dt]
        oos_ret = ret[ret.index >= split_dt]
        is_bench = bench[bench.index < split_dt]
        oos_bench = bench[bench.index >= split_dt]
        is_rand = rand[rand.index < split_dt]
        oos_rand = rand[rand.index >= split_dt]
        s_is = summarize(name + "_IS", is_ret, is_bench, is_rand, cost_drag=c)
        s_oos = summarize(name + "_OOS", oos_ret, oos_bench, oos_rand, cost_drag=c)
        oos_summaries.extend([s_is, s_oos])
        print(f"\n[{name}]")
        print(f"  IS  ({s_is['n_months']}m): ann_net={s_is['ann_ret_net']*100:.2f}%  alpha_bench={s_is['alpha_vs_bench_pp']:+.2f}pp  alpha_rand={s_is['alpha_vs_rand_pp']:+.2f}pp  p_rand={s_is['p_mcpt_vs_rand']:.3f}")
        print(f"  OOS ({s_oos['n_months']}m): ann_net={s_oos['ann_ret_net']*100:.2f}%  alpha_bench={s_oos['alpha_vs_bench_pp']:+.2f}pp  alpha_rand={s_oos['alpha_vs_rand_pp']:+.2f}pp  p_rand={s_oos['p_mcpt_vs_rand']:.3f}")

    # Save CSVs
    summary_df = pd.DataFrame(full_summaries + oos_summaries)
    summary_path = LOG_DIR / "intraday_overnight_momentum.csv"
    summary_df.to_csv(summary_path, index=False)
    print(f"\n[save] summary -> {summary_path}")

    # Detailed monthly portfolio file
    detail = port_idx.copy()
    detail["bench_ret"] = bench
    detail["rand_mean_ret"] = rand
    detail_path = LOG_DIR / "intraday_overnight_momentum_monthly.csv"
    detail.to_csv(detail_path)
    print(f"[save] monthly detail -> {detail_path}")

    # Verdict
    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    h1_full = full_summaries[0]
    h2_full = full_summaries[1]
    h3_full = full_summaries[2]
    h1_is, h1_oos = oos_summaries[0], oos_summaries[1]
    h2_is, h2_oos = oos_summaries[2], oos_summaries[3]
    h3_is, h3_oos = oos_summaries[4], oos_summaries[5]

    def _v(name, full, is_, oos_, expect_positive=True):
        passed_full_rand = full["p_mcpt_vs_rand"] < 0.05 and (
            (full["alpha_vs_rand_pp"] > 0) if expect_positive else (full["alpha_vs_rand_pp"] < 0)
        )
        oos_robust = (oos_["alpha_vs_rand_pp"] > 0) == expect_positive and (oos_["alpha_vs_rand_pp"] != 0)
        if passed_full_rand and oos_robust:
            return "DEPLOY"
        elif passed_full_rand or (oos_["alpha_vs_rand_pp"] > 0) == expect_positive:
            return "EDGE"
        else:
            return "FAIL"

    h1_verdict = _v("H1", h1_full, h1_is, h1_oos, expect_positive=True)
    h2_verdict = _v("H2", h2_full, h2_is, h2_oos, expect_positive=False)  # expect underperform
    h3_verdict = _v("H3", h3_full, h3_is, h3_oos, expect_positive=True)

    print(f"H1 (intraday top, expect outperform): {h1_verdict}")
    print(f"H2 (overnight top, expect underperform): {h2_verdict}")
    print(f"H3 (overnight bot, expect outperform): {h3_verdict}")

    return summary_df


if __name__ == "__main__":
    main()
