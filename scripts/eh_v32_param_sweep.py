"""
EH v3.2 參數 sweep — 三個維度同時掃：
  A. cut_days: 30 / 45 / 60 / 75 / 90 / 120
  B. per_trade_pct: 0.05 / 0.10 / 0.15 / 0.20 / 0.30
  C. trailing_pp: 15 / 20 / 25 / 30 / 35

策略基礎: monthly trades + #1 大戶持股 slope > -0.5 + #2 N 天早砍 + Trailing X pp
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eh_v3_sprint import (  # noqa: E402
    apply_2_early_cut,
    filter_1_big_holder_slope,
    nearest_price,
    run_v2_portfolio,
)
from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
INPUT = ROOT / "logs" / "early_hunter_trailing_v2.csv"


def re_simulate_with_trailing(trades: pd.DataFrame, trailing_pp: float) -> pd.DataFrame:
    """重新計算 trailing exit。"""
    from early_hunter_trailing_resim import simulate_trailing_exit  # noqa
    raw = pd.read_csv(ROOT / "logs" / "early_hunter_20260425_160432.csv")
    raw["entry_date"] = pd.to_datetime(raw["entry_date"]).dt.date
    raw["entry_price"] = raw["entry_price"].astype(float)
    raw["ticker"] = raw["ticker"].astype(str)

    new_rows = []
    for _, r in raw.iterrows():
        ohlcv = load_ohlcv_cache(r["ticker"], CACHE_YF)
        if ohlcv.empty:
            continue
        ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
        ret, exit_d, reason = simulate_trailing_exit(
            ohlcv, r["entry_date"], r["entry_price"], trailing_pp=trailing_pp,
        )
        new_rows.append({
            "ticker": r["ticker"],
            "entry_date": r["entry_date"],
            "exit_date": exit_d,
            "gross_return_pct": round(ret, 2),
            "exit_reason": reason,
            "hold_days": (exit_d - r["entry_date"]).days,
        })
    return pd.DataFrame(new_rows)


def main() -> None:
    df_base = pd.read_csv(INPUT)
    df_base["entry_date"] = pd.to_datetime(df_base["entry_date"]).dt.date
    df_base["exit_date"] = pd.to_datetime(df_base["exit_date"]).dt.date
    df_base["ticker"] = df_base["ticker"].astype(str)

    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    start_d = df_base["entry_date"].min()
    end_d = df_base["exit_date"].max()

    # ── A. cut_days sweep（固定 PER_TRADE_PCT=0.10, trailing=25 baseline）──
    print("=" * 70)
    print("A. cut_days sweep (#1+#2 only, per_trade=10%, trailing=-25pp baseline)")
    print("=" * 70)
    print(f"  {'cut_days':<10} {'CAGR':>8} {'alpha':>8} {'n_cut':>6}")
    df_pre = filter_1_big_holder_slope(df_base, min_slope=-0.5)
    for cut_days in [30, 45, 60, 75, 90, 120, 9999]:
        df_v = apply_2_early_cut(df_pre, cut_days=cut_days)
        n_cut = (df_v.get("exit_reason", pd.Series([])) == "early_cut_60d").sum() if cut_days < 9999 else 0
        res = run_v2_portfolio(df_v, prices_0050, start_d, end_d)
        label = f"{cut_days}d" if cut_days < 9999 else "no cut"
        print(f"  {label:<10} {res['cagr']:>+7.2f}% {res['alpha']:>+7.2f}pp {n_cut:>6}")

    # ── B. per_trade_pct sweep（固定 cut=60, trailing=25 baseline）──
    print("\n" + "=" * 70)
    print("B. per_trade_pct sweep (#1+#2, cut=60, trailing=-25pp)")
    print("=" * 70)
    df_v = apply_2_early_cut(df_pre, cut_days=60)
    print(f"  {'size':<8} {'CAGR':>8} {'alpha':>8}")
    for size_pct in [0.05, 0.10, 0.15, 0.20, 0.30, 0.50]:
        df_t = df_v.copy()
        df_t["size_pct"] = size_pct
        res = run_v2_portfolio(
            df_t, prices_0050, start_d, end_d, use_size_col=True,
        )
        print(f"  {size_pct*100:>5.0f}%   {res['cagr']:>+7.2f}% {res['alpha']:>+7.2f}pp")

    # ── C. trailing_pp sweep（固定 cut=60, per_trade=10%）──
    print("\n" + "=" * 70)
    print("C. trailing_pp sweep (#1+#2, cut=60, per_trade=10%)")
    print("=" * 70)
    print(f"  {'trail':<8} {'CAGR':>8} {'alpha':>8} {'n':>4}")
    for trail in [15, 20, 25, 30, 35, 40, 50]:
        df_t = re_simulate_with_trailing(df_base, trail)
        df_t["entry_date"] = pd.to_datetime(df_t["entry_date"]).dt.date
        df_t["exit_date"] = pd.to_datetime(df_t["exit_date"]).dt.date
        df_t["ticker"] = df_t["ticker"].astype(str)
        df_t = filter_1_big_holder_slope(df_t, min_slope=-0.5)
        df_t = apply_2_early_cut(df_t, cut_days=60)
        res = run_v2_portfolio(df_t, prices_0050, start_d, end_d)
        print(f"  {trail:>4}pp   {res['cagr']:>+7.2f}% {res['alpha']:>+7.2f}pp {len(df_t):>4}")


if __name__ == "__main__":
    main()
