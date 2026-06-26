"""
v3.6 — 加 V2_entry_z（法人進場日 z-score）當 entry filter。

Sweep z 門檻 = 0 / 0.5 / 1 / 1.5 / 2，看哪個提升最多。
基底：v3.3-C（cut=30, trail=50, size=10%, #1 大戶持股 slope > -0.5）。
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
    run_v2_portfolio,
)
from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_FM = ROOT / "data" / "cache" / "finmind" / "finmind"
WEEKLY_T50 = ROOT / "logs" / "weekly_trailing50.csv"


def compute_inst_z(ticker: str, entry_date: date) -> float | None:
    inst_path = CACHE_FM / f"TaiwanStockInstitutionalInvestorsBuySell_{ticker}.parquet"
    if not inst_path.exists():
        return None
    try:
        inst = pd.read_parquet(inst_path)
    except Exception:
        return None
    if inst.empty:
        return None
    inst["date"] = pd.to_datetime(inst["date"]).dt.date
    inst = inst[inst["date"] <= entry_date].copy()
    if len(inst) < 30:
        return None
    inst["net_buy"] = inst["buy"] - inst["sell"]
    daily = inst.groupby("date")["net_buy"].sum().sort_index()
    last_30 = daily.tail(30)
    if len(last_30) < 30:
        return None
    last_day = float(last_30.iloc[-1])
    mean_30 = last_30.mean()
    std_30 = last_30.std()
    if std_30 <= 0:
        return None
    return (last_day - mean_30) / std_30


def filter_by_inst_z(trades: pd.DataFrame, min_z: float) -> pd.DataFrame:
    keep = []
    for _, t in trades.iterrows():
        z = compute_inst_z(t["ticker"], t["entry_date"])
        if z is None:
            keep.append(False)
            continue
        keep.append(z >= min_z)
    return trades[keep].reset_index(drop=True)


def main() -> None:
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    df_w = pd.read_csv(WEEKLY_T50)
    df_w["entry_date"] = pd.to_datetime(df_w["entry_date"]).dt.date
    df_w["exit_date"] = pd.to_datetime(df_w["exit_date"]).dt.date
    df_w["ticker"] = df_w["ticker"].astype(str)
    print(f"Weekly trailing-50 sample: {len(df_w)} trades")

    # 套 #1 大戶 + cut=30 (apply_2_early_cut)
    df_base = filter_1_big_holder_slope(df_w, min_slope=-0.5)
    df_base = apply_2_early_cut(df_base, cut_days=30)
    print(f"After #1 + cut=30: {len(df_base)} trades")

    print("\n" + "=" * 70)
    print("v3.6 — V2 entry z filter sweep")
    print("=" * 70)
    print(f"  {'config':<28} {'CAGR':>8} {'alpha':>8} {'n':>5}")

    # baseline (no z filter)
    df_t = df_base.copy()
    df_t["size_pct"] = 0.10
    res = run_v2_portfolio(
        df_t, prices_0050, df_t["entry_date"].min(), df_t["exit_date"].max(),
        use_size_col=True,
    )
    print(f"  no z filter (v3.3-C ref)     {res['cagr']:>+7.2f}% {res['alpha']:>+7.2f}pp {len(df_t):>5}")

    for thr in [0.0, 0.5, 1.0, 1.5, 2.0]:
        df_t = filter_by_inst_z(df_base, min_z=thr)
        if len(df_t) == 0:
            continue
        df_t["size_pct"] = 0.10
        res = run_v2_portfolio(
            df_t, prices_0050, df_t["entry_date"].min(), df_t["exit_date"].max(),
            use_size_col=True,
        )
        print(f"  V2_entry_z >= {thr:.1f}            {res['cagr']:>+7.2f}% {res['alpha']:>+7.2f}pp {len(df_t):>5}")


if __name__ == "__main__":
    main()
