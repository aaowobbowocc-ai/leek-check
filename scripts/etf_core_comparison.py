"""
ETF Core Comparison: 0050 vs 00947 vs 00631L vs 00646
Same-period comparison from 00947 IPO (2022) to today.
Output: logs/etf_core_comparison_audit.csv
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from pathlib import Path

CACHE = Path("c:/Users/USER/Desktop/INVEST/data/cache/yfinance/tw_ohlcv")
LOG_PATH = Path("c:/Users/USER/Desktop/INVEST/logs/etf_core_comparison_audit.csv")

TICKERS = ["0050", "00947", "00631L", "00646"]
RF = 0.015  # 1.5% TW 10y yield approx


def load(tk: str) -> pd.DataFrame:
    """Load from yfinance live (cache too stale/limited for 00947)."""
    import yfinance as yf
    df = yf.download(f"{tk}.TW", start="2022-01-01", end="2026-05-13",
                     progress=False, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"no data for {tk}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={"Adj Close": "close"})
    df.index = pd.to_datetime(df.index)
    return df[["close"]].sort_index()


def metrics(prices: pd.Series, name: str, bench: pd.Series | None = None) -> dict:
    rets = prices.pct_change().dropna()
    n = len(rets)
    if n < 30:
        return {"ticker": name, "n_days": n, "note": "insufficient"}

    total_ret = prices.iloc[-1] / prices.iloc[0] - 1
    years = n / 252
    cagr = (1 + total_ret) ** (1 / years) - 1
    vol = rets.std() * np.sqrt(252)
    downside = rets[rets < 0].std() * np.sqrt(252)
    sharpe = (cagr - RF) / vol if vol > 0 else np.nan
    sortino = (cagr - RF) / downside if downside > 0 else np.nan

    cum = (1 + rets).cumprod()
    peak = cum.cummax()
    dd = (cum / peak - 1).min()

    corr = rets.corr(bench) if bench is not None else 1.0

    return {
        "ticker": name,
        "start": str(prices.index[0].date()),
        "end": str(prices.index[-1].date()),
        "n_days": n,
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "annual_vol_pct": round(vol * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "mdd_pct": round(dd * 100, 2),
        "corr_0050": round(corr, 3),
    }


def quarterly_outperf(p1: pd.Series, p_bench: pd.Series, label: str) -> pd.DataFrame:
    df = pd.concat([p1.rename(label), p_bench.rename("0050")], axis=1).dropna()
    q_rets = df.resample("QE").apply(lambda x: x.iloc[-1] / x.iloc[0] - 1 if len(x) > 1 else np.nan)
    q_rets["outperf"] = q_rets[label] - q_rets["0050"]
    return q_rets


def regime_analysis(prices: dict[str, pd.Series]) -> pd.DataFrame:
    """MDD per ETF in defined regimes."""
    regimes = {
        "2024_H2_ipo_to_eoy": ("2024-06-12", "2024-12-31"),
        "2025_full_year": ("2025-01-01", "2025-12-31"),
        "2026_ytd": ("2026-01-01", "2026-05-13"),
    }
    rows = []
    for rname, (s, e) in regimes.items():
        for tk, p in prices.items():
            sub = p.loc[s:e]
            if len(sub) < 20:
                rows.append({"regime": rname, "ticker": tk, "ret_pct": None, "mdd_pct": None})
                continue
            ret = sub.iloc[-1] / sub.iloc[0] - 1
            cum = (1 + sub.pct_change().dropna()).cumprod()
            dd = (cum / cum.cummax() - 1).min()
            rows.append({
                "regime": rname,
                "ticker": tk,
                "ret_pct": round(ret * 100, 2),
                "mdd_pct": round(dd * 100, 2),
            })
    return pd.DataFrame(rows)


def main():
    print("Loading ETF data...")
    raw = {tk: load(tk) for tk in TICKERS}
    for tk, df in raw.items():
        print(f"  {tk}: {df.index.min().date()} -> {df.index.max().date()}, n={len(df)}")

    # Align to 00947 IPO (latest start)
    start = max(df.index.min() for df in raw.values())
    print(f"\nAligned start: {start.date()}")

    aligned = {tk: df.loc[start:]["close"] for tk, df in raw.items()}

    # Metrics
    print("\n=== Metrics (aligned period) ===")
    bench_rets = aligned["0050"].pct_change().dropna()
    results = []
    for tk in TICKERS:
        m = metrics(aligned[tk], tk, bench_rets if tk != "0050" else None)
        results.append(m)
        print(m)

    df_metrics = pd.DataFrame(results)
    df_metrics.to_csv(LOG_PATH, index=False)
    print(f"\nSaved core metrics: {LOG_PATH}")

    # Quarterly 00947 vs 0050
    print("\n=== Quarterly 00947 vs 0050 ===")
    q_947 = quarterly_outperf(aligned["00947"], aligned["0050"], "00947")
    print(q_947.round(4))
    q_947_path = LOG_PATH.parent / "etf_core_quarterly_00947_vs_0050.csv"
    q_947.to_csv(q_947_path)

    # 00631L quarterly
    q_631 = quarterly_outperf(aligned["00631L"], aligned["0050"], "00631L")
    print("\n=== Quarterly 00631L vs 0050 ===")
    print(q_631.round(4))
    q_631.to_csv(LOG_PATH.parent / "etf_core_quarterly_00631L_vs_0050.csv")

    # Regime
    print("\n=== Regime analysis ===")
    df_reg = regime_analysis(aligned)
    print(df_reg)
    df_reg.to_csv(LOG_PATH.parent / "etf_core_regime.csv", index=False)

    # Summary stats for 00947 outperf consistency
    print("\n=== 00947 outperf consistency ===")
    quarters = q_947["outperf"].dropna()
    print(f"Total quarters: {len(quarters)}")
    print(f"Quarters outperform 0050: {(quarters > 0).sum()} ({(quarters > 0).mean()*100:.1f}%)")
    print(f"Avg outperf per quarter: {quarters.mean()*100:.2f}%")
    print(f"Std outperf: {quarters.std()*100:.2f}%")
    print(f"Best Q: {quarters.max()*100:.2f}% | Worst Q: {quarters.min()*100:.2f}%")

    # Year-by-year 00947 vs 0050 outperf decomposition
    print("\n=== Year-by-year 4-ETF compare ===")
    for label, s, e in [
        ("2024_H2_post_IPO", "2024-06-12", "2024-12-31"),
        ("2025", "2025-01-01", "2025-12-31"),
        ("2026_YTD", "2026-01-01", "2026-05-13"),
    ]:
        p947 = aligned["00947"].loc[s:e]
        p0050 = aligned["0050"].loc[s:e]
        p631 = aligned["00631L"].loc[s:e]
        p646 = aligned["00646"].loc[s:e]
        if len(p947) < 5:
            continue
        r947 = p947.iloc[-1] / p947.iloc[0] - 1
        r0050 = p0050.iloc[-1] / p0050.iloc[0] - 1
        r631 = p631.iloc[-1] / p631.iloc[0] - 1
        r646 = p646.iloc[-1] / p646.iloc[0] - 1
        print(f"\n{label}:")
        print(f"  0050   : {r0050*100:+.2f}%")
        print(f"  00947  : {r947*100:+.2f}%  (vs 0050: {(r947-r0050)*100:+.2f}pp)")
        print(f"  00631L : {r631*100:+.2f}%  (vs 0050: {(r631-r0050)*100:+.2f}pp)")
        print(f"  00646  : {r646*100:+.2f}%  (vs 0050: {(r646-r0050)*100:+.2f}pp)")


if __name__ == "__main__":
    main()
