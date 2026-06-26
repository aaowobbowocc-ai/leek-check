"""
v3.3 候選組合在 weekly 10182 sample 的 OOS 驗證。

驗證最強的 3 個 in-sample 候選：
  A. size=30% + trail=50pp + cut=60 (in-sample +15.63pp)
  B. size=30% + trail=50pp + cut=30 (in-sample +9.91pp)
  C. v3.2 baseline                  (in-sample +2.71pp)

對 weekly trade-level 跑 trailing 重模擬 + V2 portfolio。
比較 in-sample 倍率 vs OOS 倍率 → 判斷 overfit 程度。
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
from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
WEEKLY_CSV = ROOT / "logs" / "early_hunter_weekly_v2.csv"


def re_simulate_with_trailing_weekly(trailing_pp: float) -> pd.DataFrame:
    """以 weekly entries 為基底重新算 trailing exit。"""
    from early_hunter_trailing_resim import simulate_trailing_exit  # noqa
    weekly = pd.read_csv(WEEKLY_CSV)
    weekly["entry_date"] = pd.to_datetime(weekly["entry_date"]).dt.date
    weekly["ticker"] = weekly["ticker"].astype(str)

    rows = []
    for i, r in enumerate(weekly.itertuples(), 1):
        ohlcv = load_ohlcv_cache(r.ticker, CACHE_YF)
        if ohlcv.empty:
            continue
        ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
        # 需要原 entry_price → 從 ohlcv 找 entry_date 收盤
        prior = ohlcv[ohlcv["date"] <= r.entry_date]
        if prior.empty:
            continue
        entry_price = float(prior.iloc[-1]["close"])
        ret, exit_d, reason = simulate_trailing_exit(
            ohlcv, r.entry_date, entry_price, trailing_pp=trailing_pp,
        )
        rows.append({
            "ticker": r.ticker,
            "entry_date": r.entry_date,
            "exit_date": exit_d,
            "gross_return_pct": round(ret, 2),
            "exit_reason": reason,
            "hold_days": (exit_d - r.entry_date).days,
        })
        if i % 2000 == 0:
            print(f"    [{i}/{len(weekly)}]")
    return pd.DataFrame(rows)


def main() -> None:
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    print("=" * 70)
    print("v3.3 候選組合 weekly OOS 驗證")
    print("=" * 70)

    configs = [
        # (label, trailing_pp, cut_days, size_pct, in_sample_alpha)
        ("v3.2 baseline (cut=60,size=10%,trail=25)", 25, 60, 0.10, 2.71),
        ("v3.3-A (cut=60,size=30%,trail=50)",       50, 60, 0.30, 15.63),
        ("v3.3-B (cut=30,size=30%,trail=50)",       50, 30, 0.30, 9.91),
        ("v3.3-C (cut=30,size=10%,trail=50)",       50, 30, 0.10, 6.42),
    ]

    # 因 trailing 50pp 跟 25pp 各自要算一遍 entries，cache 一次
    print("\n[1/2] 計算 trailing -50pp weekly entries（重模擬 10182 trades）...")
    weekly_t50 = re_simulate_with_trailing_weekly(50.0)
    print(f"    完成 {len(weekly_t50)} trades")

    print("\n[2/2] 計算 trailing -25pp weekly entries（讀現成 csv）...")
    weekly_t25 = pd.read_csv(WEEKLY_CSV)
    weekly_t25["entry_date"] = pd.to_datetime(weekly_t25["entry_date"]).dt.date
    weekly_t25["exit_date"] = pd.to_datetime(weekly_t25["exit_date"]).dt.date
    weekly_t25["ticker"] = weekly_t25["ticker"].astype(str)

    print(f"\n{'config':<48} {'in_alpha':>8} {'OOS_alpha':>9} {'倍率':>7}")
    for label, trail, cut, size, in_alpha in configs:
        df_w = weekly_t25 if trail == 25 else weekly_t50.copy()
        df_w["entry_date"] = pd.to_datetime(df_w["entry_date"]).dt.date
        df_w["exit_date"] = pd.to_datetime(df_w["exit_date"]).dt.date
        df_w = filter_1_big_holder_slope(df_w, min_slope=-0.5)
        df_w = apply_2_early_cut(df_w, cut_days=cut)
        df_w["size_pct"] = size
        start_d = df_w["entry_date"].min()
        end_d = df_w["exit_date"].max()
        res = run_v2_portfolio(df_w, prices_0050, start_d, end_d, use_size_col=True)
        ratio = res["alpha"] / in_alpha if in_alpha != 0 else 0
        print(f"  {label:<48} {in_alpha:>+7.2f}pp {res['alpha']:>+8.2f}pp {ratio*100:>+6.0f}%")


if __name__ == "__main__":
    main()
