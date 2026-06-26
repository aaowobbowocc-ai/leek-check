"""Low Volatility Anomaly Backtest — TW Stocks 2017-2026.

Signal: 90d realized volatility = std(daily log return) * sqrt(252)
Universe: yfinance tw_ohlcv cache (2351 tickers); filter 60d avg vol > 100K
Rebalance: monthly, equal-weight Q1 (lowest 10%)
Costs: 0.585% per turnover (estimate ~40%/month single-leg => ~0.234%/month)
Compare: Q1 vs 0050 BTH; Q1 - Q5 spread (informational only)

Validation gates:
  - vs 0050 alpha annualized > 1% net
  - MCPT vs random N-sample from same Q1-eligible pool
  - OOS split 2017-2019 vs 2020-2026
  - TSMC weight check (concentration artifact)
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data/cache/yfinance/tw_ohlcv"
OUT_CSV = ROOT / "logs/low_vol_anomaly.csv"

START = "2017-01-01"
END = "2026-05-06"

VOL_LOOKBACK = 90
LIQ_LOOKBACK = 60
LIQ_MIN_VOL = 100_000  # avg daily shares
COST_BPS_TURNOVER = 0.00585  # 0.585% per single-leg turnover (round-trip ~1.17%)
QUINTILE = 5  # Q1 = top 20% lowest vol; we'll also report top 10%
TOP_PCT = 0.10  # primary: top 10%

BENCH = "0050"


def load_ticker(path: str) -> pd.DataFrame | None:
    """Load and normalize a single parquet to ['date','close','volume']."""
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance multi-index format
        df.columns = [c[0] for c in df.columns]
        df = df.reset_index()
        # date col may be 'Date' or 'index'
        for c in ("Date", "index", "date"):
            if c in df.columns:
                df = df.rename(columns={c: "date"})
                break
        # close: prefer Adj Close
        if "Adj Close" in df.columns:
            df["close"] = df["Adj Close"]
        elif "Close" in df.columns:
            df["close"] = df["Close"]
        else:
            return None
        if "Volume" in df.columns:
            df["volume"] = df["Volume"]
    if "date" not in df.columns or "close" not in df.columns:
        return None
    df = df[["date", "close", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return df


def build_panel() -> pd.DataFrame:
    """Build wide panel: index=date, columns=ticker, values=close. Plus volume panel."""
    files = sorted(glob.glob(str(CACHE_DIR / "*.parquet")))
    print(f"Loading {len(files)} ticker files...")
    closes = {}
    vols = {}
    for i, f in enumerate(files):
        ticker = os.path.basename(f).replace(".parquet", "")
        # skip ETFs that contain known leveraged/inverse/bond tags for stock-only universe
        # but keep 0050 for benchmark
        df = load_ticker(f)
        if df is None or len(df) < 200:
            continue
        df = df[(df["date"] >= START) & (df["date"] <= END)]
        if len(df) < 200:
            continue
        df = df.set_index("date")
        closes[ticker] = df["close"]
        vols[ticker] = df["volume"]
        if (i + 1) % 500 == 0:
            print(f"  loaded {i+1}/{len(files)}")
    close_df = pd.DataFrame(closes)
    vol_df = pd.DataFrame(vols)
    print(f"Panel: {close_df.shape[0]} dates x {close_df.shape[1]} tickers")
    return close_df, vol_df


def is_stock(ticker: str) -> bool:
    """Filter ETFs (4-digit numeric is TWSE stock; ETFs are 5-6 digit starting 00)."""
    # Keep only 4-digit pure numeric tickers (individual stocks)
    return ticker.isdigit() and len(ticker) == 4


def compute_signals(close_df: pd.DataFrame, vol_df: pd.DataFrame):
    """Compute rolling 90d realized vol and 60d liquidity, both indexed by date."""
    log_ret = np.log(close_df / close_df.shift(1))
    realized_vol = log_ret.rolling(VOL_LOOKBACK, min_periods=60).std() * np.sqrt(252)
    avg_vol = vol_df.rolling(LIQ_LOOKBACK, min_periods=30).mean()
    return realized_vol, avg_vol, log_ret


def get_rebalance_dates(close_df: pd.DataFrame) -> list:
    """Last trading day of each month."""
    df = close_df.reset_index().rename(columns={"index": "date"})
    if "date" not in df.columns:
        df = df.rename(columns={df.columns[0]: "date"})
    df["ym"] = df["date"].dt.to_period("M")
    rebal = df.groupby("ym")["date"].max().tolist()
    return rebal


def backtest_lowvol(
    close_df: pd.DataFrame,
    realized_vol: pd.DataFrame,
    avg_vol: pd.DataFrame,
    top_pct: float = TOP_PCT,
    quintile: bool = False,
    quintile_idx: int = 0,  # 0 = Q1 (lowest), 4 = Q5 (highest)
):
    """Run monthly rebalanced equal-weight portfolio of low-vol stocks.

    Returns: (returns_series, holdings_history, turnover_series, tsmc_weights)
    """
    rebal_dates = get_rebalance_dates(close_df)

    # Universe: 4-digit stocks only
    stock_cols = [c for c in close_df.columns if is_stock(c)]
    print(f"Stock universe: {len(stock_cols)} tickers")

    portfolio_ret = pd.Series(0.0, index=close_df.index)
    holdings_history = []
    turnover_history = []
    tsmc_weights = []
    prev_holdings = set()

    for i, rebal_date in enumerate(rebal_dates[:-1]):
        next_rebal = rebal_dates[i + 1]

        # Get vol & liquidity at rebal_date
        if rebal_date not in realized_vol.index:
            continue
        vol_snap = realized_vol.loc[rebal_date, stock_cols]
        liq_snap = avg_vol.loc[rebal_date, stock_cols] if rebal_date in avg_vol.index else None

        # Eligible: non-NaN vol AND liquid
        eligible = vol_snap.dropna()
        if liq_snap is not None:
            liq_eligible = liq_snap[liq_snap >= LIQ_MIN_VOL].index
            eligible = eligible[eligible.index.isin(liq_eligible)]
        if len(eligible) < 20:
            continue

        # Rank
        if quintile:
            n = len(eligible)
            q_size = n // 5
            sorted_tickers = eligible.sort_values()  # ascending = lowest vol first
            start = quintile_idx * q_size
            end_i = (quintile_idx + 1) * q_size if quintile_idx < 4 else n
            holdings = sorted_tickers.iloc[start:end_i].index.tolist()
        else:
            n_select = max(int(len(eligible) * top_pct), 10)
            holdings = eligible.nsmallest(n_select).index.tolist()

        # TSMC weight
        tsmc_in = "2330" in holdings
        tsmc_w = (1.0 / len(holdings)) if tsmc_in else 0.0
        tsmc_weights.append({"date": rebal_date, "tsmc_weight": tsmc_w, "n_holdings": len(holdings)})

        # Turnover
        new_set = set(holdings)
        added = new_set - prev_holdings
        removed = prev_holdings - new_set
        turnover = (len(added) + len(removed)) / (2 * max(len(new_set), 1))  # one-way
        turnover_history.append({"date": rebal_date, "turnover": turnover})

        # Build daily returns from rebal_date+1 to next_rebal (inclusive)
        period_close = close_df.loc[rebal_date:next_rebal, holdings]
        period_close = period_close.dropna(axis=1, thresh=2)
        if period_close.empty:
            continue
        period_ret = period_close.pct_change().fillna(0.0)
        # Equal-weight: mean across holdings
        ew_ret = period_ret.iloc[1:].mean(axis=1)
        # Apply turnover cost on rebal day
        ew_ret_with_cost = ew_ret.copy()
        if not ew_ret_with_cost.empty:
            ew_ret_with_cost.iloc[0] -= turnover * COST_BPS_TURNOVER
        portfolio_ret.loc[ew_ret_with_cost.index] = ew_ret_with_cost.values

        holdings_history.append({"date": rebal_date, "holdings": holdings})
        prev_holdings = new_set

    return (
        portfolio_ret,
        pd.DataFrame(holdings_history),
        pd.DataFrame(turnover_history),
        pd.DataFrame(tsmc_weights),
    )


def annualize_metrics(ret: pd.Series) -> dict:
    ret = ret.dropna()
    if len(ret) == 0:
        return {"cagr": 0, "vol": 0, "sharpe": 0, "mdd": 0, "total_return": 0}
    cum = (1 + ret).cumprod()
    n_years = len(ret) / 252
    total = cum.iloc[-1] - 1
    cagr = (1 + total) ** (1 / n_years) - 1 if n_years > 0 else 0
    vol = ret.std() * np.sqrt(252)
    sharpe = (ret.mean() * 252) / vol if vol > 0 else 0
    drawdown = (cum / cum.cummax() - 1).min()
    return {
        "cagr": cagr,
        "vol": vol,
        "sharpe": sharpe,
        "mdd": drawdown,
        "total_return": total,
        "n_years": n_years,
    }


def benchmark_returns(close_df: pd.DataFrame, ticker: str = BENCH) -> pd.Series:
    if ticker not in close_df.columns:
        print(f"WARN: {ticker} missing from cache")
        return pd.Series(dtype=float)
    return close_df[ticker].pct_change().fillna(0.0)


def mcpt_random_baseline(
    close_df: pd.DataFrame,
    realized_vol: pd.DataFrame,
    avg_vol: pd.DataFrame,
    actual_cagr: float,
    actual_n: int,
    n_iter: int = 200,
):
    """Random N-ticker monthly rebalance from same eligible pool — baseline."""
    rebal_dates = get_rebalance_dates(close_df)
    stock_cols = [c for c in close_df.columns if is_stock(c)]
    rng = np.random.default_rng(42)
    sim_cagrs = []
    for trial in range(n_iter):
        portfolio_ret = pd.Series(0.0, index=close_df.index)
        prev_holdings = set()
        for i, rebal_date in enumerate(rebal_dates[:-1]):
            next_rebal = rebal_dates[i + 1]
            if rebal_date not in realized_vol.index:
                continue
            vol_snap = realized_vol.loc[rebal_date, stock_cols].dropna()
            liq_snap = avg_vol.loc[rebal_date, stock_cols] if rebal_date in avg_vol.index else None
            eligible = vol_snap
            if liq_snap is not None:
                liq_idx = liq_snap[liq_snap >= LIQ_MIN_VOL].index
                eligible = eligible[eligible.index.isin(liq_idx)]
            if len(eligible) < actual_n:
                continue
            holdings = rng.choice(eligible.index.tolist(), size=actual_n, replace=False).tolist()
            new_set = set(holdings)
            turnover = (len(new_set - prev_holdings) + len(prev_holdings - new_set)) / (2 * actual_n)
            period_close = close_df.loc[rebal_date:next_rebal, holdings].dropna(axis=1, thresh=2)
            if period_close.empty:
                continue
            period_ret = period_close.pct_change().fillna(0.0)
            ew = period_ret.iloc[1:].mean(axis=1)
            if not ew.empty:
                ew.iloc[0] -= turnover * COST_BPS_TURNOVER
            portfolio_ret.loc[ew.index] = ew.values
            prev_holdings = new_set
        m = annualize_metrics(portfolio_ret)
        sim_cagrs.append(m["cagr"])
        if (trial + 1) % 50 == 0:
            print(f"  MCPT {trial+1}/{n_iter} sim_cagr={m['cagr']:.4f}")
    sim_cagrs = np.array(sim_cagrs)
    p_value = (sim_cagrs >= actual_cagr).mean()
    return {
        "n_iter": n_iter,
        "mean_random_cagr": sim_cagrs.mean(),
        "std_random_cagr": sim_cagrs.std(),
        "actual_cagr": actual_cagr,
        "p_value": p_value,
        "actual_pct_rank": (sim_cagrs < actual_cagr).mean(),
    }


def split_oos(ret: pd.Series, split_date: str = "2020-01-01"):
    is_pre = ret.loc[:split_date]
    is_post = ret.loc[split_date:]
    return annualize_metrics(is_pre), annualize_metrics(is_post)


def main():
    print("=" * 60)
    print("Low Volatility Anomaly Backtest — TW Stocks 2017-2026")
    print("=" * 60)

    close_df, vol_df = build_panel()
    if close_df.empty:
        print("ERROR: empty panel")
        return

    realized_vol, avg_vol, _ = compute_signals(close_df, vol_df)

    # 0050 benchmark
    bench_ret = benchmark_returns(close_df, BENCH)
    bench_metrics = annualize_metrics(bench_ret)

    print("\n--- Q1 (top 10% lowest vol) Portfolio ---")
    q1_ret, q1_hold, q1_turn, q1_tsmc = backtest_lowvol(close_df, realized_vol, avg_vol, top_pct=0.10)
    q1_metrics = annualize_metrics(q1_ret)
    avg_n = q1_hold["holdings"].apply(len).mean() if not q1_hold.empty else 0
    avg_turn = q1_turn["turnover"].mean() if not q1_turn.empty else 0
    tsmc_freq = (q1_tsmc["tsmc_weight"] > 0).mean() if not q1_tsmc.empty else 0
    avg_tsmc_w = q1_tsmc["tsmc_weight"].mean() if not q1_tsmc.empty else 0
    print(f"  N holdings (avg): {avg_n:.1f}")
    print(f"  Avg monthly turnover: {avg_turn:.1%}")
    print(f"  TSMC inclusion freq: {tsmc_freq:.1%}, avg weight: {avg_tsmc_w:.2%}")
    print(f"  CAGR: {q1_metrics['cagr']:.2%}, Sharpe: {q1_metrics['sharpe']:.2f}, MDD: {q1_metrics['mdd']:.2%}")

    print("\n--- 0050 Benchmark ---")
    print(f"  CAGR: {bench_metrics['cagr']:.2%}, Sharpe: {bench_metrics['sharpe']:.2f}, MDD: {bench_metrics['mdd']:.2%}")

    alpha = q1_metrics["cagr"] - bench_metrics["cagr"]
    print(f"\n  Q1 alpha vs 0050 (annualized, net): {alpha:+.2%}")

    # Q5 (highest vol) for spread
    print("\n--- Q5 (highest vol Q5/5) Portfolio ---")
    q5_ret, _, _, _ = backtest_lowvol(close_df, realized_vol, avg_vol, quintile=True, quintile_idx=4)
    q5_metrics = annualize_metrics(q5_ret)
    print(f"  CAGR: {q5_metrics['cagr']:.2%}, Sharpe: {q5_metrics['sharpe']:.2f}")
    spread = q1_metrics["cagr"] - q5_metrics["cagr"]
    print(f"  Q1-Q5 spread: {spread:+.2%}/yr")

    # Q1 quintile (top 20% lowest vol) — academic standard
    print("\n--- Q1 quintile (lowest 20%) Portfolio ---")
    q1q_ret, _, _, _ = backtest_lowvol(close_df, realized_vol, avg_vol, quintile=True, quintile_idx=0)
    q1q_metrics = annualize_metrics(q1q_ret)
    print(f"  CAGR: {q1q_metrics['cagr']:.2%}, Sharpe: {q1q_metrics['sharpe']:.2f}")

    # OOS split
    print("\n--- OOS split (Q1 top 10%) ---")
    pre, post = split_oos(q1_ret)
    bpre, bpost = split_oos(bench_ret)
    print(f"  IS 2017-2019:  Q1 CAGR {pre['cagr']:.2%}, 0050 {bpre['cagr']:.2%}, alpha {pre['cagr']-bpre['cagr']:+.2%}")
    print(f"  OOS 2020-2026: Q1 CAGR {post['cagr']:.2%}, 0050 {bpost['cagr']:.2%}, alpha {post['cagr']-bpost['cagr']:+.2%}")

    # MCPT
    print("\n--- MCPT random N-ticker baseline (n_iter=200) ---")
    n_avg = int(round(avg_n)) if avg_n > 0 else 30
    mcpt = mcpt_random_baseline(close_df, realized_vol, avg_vol, q1_metrics["cagr"], n_avg, n_iter=200)
    print(f"  Random N={n_avg} mean CAGR: {mcpt['mean_random_cagr']:.2%} +/- {mcpt['std_random_cagr']:.2%}")
    print(f"  Q1 actual CAGR: {mcpt['actual_cagr']:.2%}")
    print(f"  P-value (Q1 >= random): {mcpt['p_value']:.3f}")
    print(f"  Q1 percentile rank: {mcpt['actual_pct_rank']:.1%}")

    # Verdict
    print("\n--- Verdict ---")
    pass_alpha = alpha > 0.01
    pass_oos = (pre["cagr"] - bpre["cagr"] > 0.005) and (post["cagr"] - bpost["cagr"] > 0.005)
    pass_mcpt = mcpt["p_value"] < 0.05
    if pass_alpha and pass_oos and pass_mcpt:
        verdict = "DEPLOY"
    elif pass_alpha and (pass_oos or pass_mcpt):
        verdict = "EDGE"
    else:
        verdict = "FAIL"
    print(f"  alpha>1%: {pass_alpha} ({alpha:+.2%})")
    print(f"  OOS robust: {pass_oos}")
    print(f"  MCPT p<0.05: {pass_mcpt} (p={mcpt['p_value']:.3f})")
    print(f"  VERDICT: {verdict}")

    # Save CSV
    rows = []
    rows.append({"metric": "Q1_top10pct_CAGR", "value": q1_metrics["cagr"]})
    rows.append({"metric": "Q1_top10pct_Sharpe", "value": q1_metrics["sharpe"]})
    rows.append({"metric": "Q1_top10pct_MDD", "value": q1_metrics["mdd"]})
    rows.append({"metric": "Q1_top10pct_n_avg", "value": avg_n})
    rows.append({"metric": "Q1_top10pct_turnover_avg", "value": avg_turn})
    rows.append({"metric": "Q1_top10pct_tsmc_freq", "value": tsmc_freq})
    rows.append({"metric": "Q1_top10pct_tsmc_avg_weight", "value": avg_tsmc_w})
    rows.append({"metric": "Q1_quintile_CAGR", "value": q1q_metrics["cagr"]})
    rows.append({"metric": "Q5_quintile_CAGR", "value": q5_metrics["cagr"]})
    rows.append({"metric": "Q1_minus_Q5_spread", "value": spread})
    rows.append({"metric": "0050_CAGR", "value": bench_metrics["cagr"]})
    rows.append({"metric": "0050_Sharpe", "value": bench_metrics["sharpe"]})
    rows.append({"metric": "0050_MDD", "value": bench_metrics["mdd"]})
    rows.append({"metric": "alpha_vs_0050_annualized", "value": alpha})
    rows.append({"metric": "IS_2017_2019_alpha", "value": pre["cagr"] - bpre["cagr"]})
    rows.append({"metric": "OOS_2020_2026_alpha", "value": post["cagr"] - bpost["cagr"]})
    rows.append({"metric": "MCPT_p_value", "value": mcpt["p_value"]})
    rows.append({"metric": "MCPT_random_mean_CAGR", "value": mcpt["mean_random_cagr"]})
    rows.append({"metric": "MCPT_random_std_CAGR", "value": mcpt["std_random_cagr"]})
    rows.append({"metric": "Q1_pct_rank_vs_random", "value": mcpt["actual_pct_rank"]})
    rows.append({"metric": "verdict", "value": verdict})
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
    print(f"\n  CSV saved: {OUT_CSV}")


if __name__ == "__main__":
    main()
