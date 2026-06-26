"""
EH v3.3 — 把 sweep 找到的三個最佳值綜合：
  - cut_days = 30
  - per_trade_pct = 0.30
  - trailing_pp = 50

驗證：是否真的綜合 alpha 比個別更強？還是 over-fit？
"""
from __future__ import annotations

import io
import sys
from datetime import date
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
from eh_v32_param_sweep import re_simulate_with_trailing  # noqa: E402
from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def main() -> None:
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    print("=" * 70)
    print("v3.3 試 9 種組合（驗證 sweep 最佳值組合是否疊加）")
    print("=" * 70)

    configs = [
        ("v3.2 baseline", 60, 0.10, 25),
        ("cut=30",        30, 0.10, 25),
        ("size=30%",      60, 0.30, 25),
        ("trail=50pp",    60, 0.10, 50),
        ("cut=30+size=30", 30, 0.30, 25),
        ("cut=30+trail=50", 30, 0.10, 50),
        ("size=30+trail=50", 60, 0.30, 50),
        ("v3.3 (cut=30,size=30,trail=50)", 30, 0.30, 50),
        ("v3.3+ (cut=30,size=20,trail=50)", 30, 0.20, 50),
    ]

    print(f"  {'config':<45} {'CAGR':>8} {'alpha':>8} {'n':>4}")
    for label, cut_days, size_pct, trail in configs:
        df_t = re_simulate_with_trailing(
            pd.DataFrame({"_": []}), trail
        )
        df_t["entry_date"] = pd.to_datetime(df_t["entry_date"]).dt.date
        df_t["exit_date"] = pd.to_datetime(df_t["exit_date"]).dt.date
        df_t["ticker"] = df_t["ticker"].astype(str)
        df_t = filter_1_big_holder_slope(df_t, min_slope=-0.5)
        df_t = apply_2_early_cut(df_t, cut_days=cut_days)
        df_t["size_pct"] = size_pct
        start_d = df_t["entry_date"].min()
        end_d = df_t["exit_date"].max()
        res = run_v2_portfolio(
            df_t, prices_0050, start_d, end_d, use_size_col=True,
        )
        print(f"  {label:<45} {res['cagr']:>+7.2f}% {res['alpha']:>+7.2f}pp {len(df_t):>4}")


if __name__ == "__main__":
    main()
