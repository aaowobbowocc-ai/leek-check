"""
EH v3 Walk-Forward 驗證。

問題：v3 的 +3.58pp CAGR 改善是否 in-sample overfit？

切分：
  TRAIN: 2019-01-01 ~ 2022-12-31（4 年）
  TEST:  2023-01-01 ~ 2026-04-24（3.3 年 OOS）

對每個 period 獨立計算：
  Baseline Trailing
  + v3 (#1+#2+#4) 同一套規則（不重新調 hyperparameter）

驗收：
  - 若 TEST 期 v3 vs baseline Δ ≥ +1.5pp → ✅ 確認真實 alpha
  - 若 TEST 期 Δ < +0.5pp → ❌ 高機率 overfit
  - 中間（0.5 ~ 1.5pp）→ 真實 alpha 但弱化
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

from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

# 重用 sprint script 的 filter 函式
sys.path.insert(0, str(ROOT / "scripts"))
from eh_v3_sprint import (  # noqa: E402
    PER_TRADE_PCT,
    apply_2_early_cut,
    apply_4_conviction_weight,
    filter_1_big_holder_slope,
    nearest_price,
    run_v2_portfolio,
)

import argparse  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
DEFAULT_CSV = ROOT / "logs" / "early_hunter_trailing_v2.csv"

TRAIN_START = date(2019, 1, 1)
TRAIN_END = date(2022, 12, 31)
TEST_START = date(2023, 1, 1)
TEST_END = date(2026, 4, 24)


def evaluate_period(
    label: str,
    df_period: pd.DataFrame,
    prices_0050: dict[date, float],
    period_start: date,
    period_end: date,
) -> dict:
    print(f"\n{'─' * 70}")
    print(f"[{label}] {period_start} ~ {period_end}, n={len(df_period)} trades")

    if len(df_period) == 0:
        print("  no trades, skip")
        return {}

    base = run_v2_portfolio(df_period, prices_0050, period_start, period_end)
    print(f"  baseline       : CAGR {base['cagr']:+7.2f}%   alpha {base['alpha']:+6.2f}pp   n={base['n_trades']}")

    df_v3 = filter_1_big_holder_slope(df_period, min_slope=-0.5)
    df_v3 = apply_2_early_cut(df_v3, cut_days=60)
    df_v3 = apply_4_conviction_weight(df_v3)
    res = run_v2_portfolio(
        df_v3, prices_0050, period_start, period_end, use_size_col=True,
    )
    print(f"  v3 (#1+#2+#4)  : CAGR {res['cagr']:+7.2f}%   alpha {res['alpha']:+6.2f}pp   n={res['n_trades']}")
    print(f"  Δ (v3 - base)  :       {res['cagr'] - base['cagr']:+7.2f}pp        {res['alpha'] - base['alpha']:+6.2f}pp")

    return {
        "label": label,
        "n": len(df_period),
        "base_cagr": base["cagr"],
        "base_alpha": base["alpha"],
        "v3_cagr": res["cagr"],
        "v3_alpha": res["alpha"],
        "delta_cagr": res["cagr"] - base["cagr"],
        "delta_alpha": res["alpha"] - base["alpha"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=str, default=str(DEFAULT_CSV))
    args = parser.parse_args()
    df = pd.read_csv(args.csv)
    print(f"Input: {args.csv}")
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.date
    df["ticker"] = df["ticker"].astype(str)

    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    train = df[df["entry_date"] <= TRAIN_END].reset_index(drop=True)
    test = df[df["entry_date"] >= TEST_START].reset_index(drop=True)

    print("=" * 70)
    print("EH v3 Walk-Forward Validation")
    print("=" * 70)
    print(f"全部 trades: {len(df)}  (train={len(train)}, test={len(test)})")

    train_result = evaluate_period(
        "TRAIN", train, prices_0050, TRAIN_START, TRAIN_END
    )
    test_result = evaluate_period(
        "TEST (OOS)", test, prices_0050, TEST_START, TEST_END
    )

    # 判決
    print("\n" + "=" * 70)
    print("驗收判決")
    print("=" * 70)
    if not train_result or not test_result:
        print("  資料不足，無法判決")
        return

    train_d = train_result["delta_cagr"]
    test_d = test_result["delta_cagr"]
    print(f"  TRAIN Δ : {train_d:+.2f}pp")
    print(f"  TEST  Δ : {test_d:+.2f}pp")
    print(f"  退化幅度: {train_d - test_d:+.2f}pp")

    if test_d >= 1.5:
        verdict = "✅ 真實 alpha — v3 通過 OOS 驗證"
    elif test_d >= 0.5:
        verdict = "⚠️  alpha 真實但弱化 — 上線可，但期望值降低"
    elif test_d >= -0.5:
        verdict = "❌ alpha 消失 — v3 大機率 overfit"
    else:
        verdict = "❌❌ OOS 退化嚴重 — v3 是 in-sample 假象"

    print(f"\n  {verdict}")

    # 子 filter 個別 OOS 表現
    print("\n" + "=" * 70)
    print("各 filter 在 TEST 期單獨表現（OOS sanity check）")
    print("=" * 70)
    for fname, fn, kwargs in [
        ("#1 big_holder", filter_1_big_holder_slope, {"min_slope": -0.5}),
        ("#2 early_cut", apply_2_early_cut, {"cut_days": 60}),
        ("#4 conviction", apply_4_conviction_weight, {}),
    ]:
        df_f = fn(test, **kwargs)
        use_size = "size_pct" in df_f.columns
        res_f = run_v2_portfolio(
            df_f, prices_0050, TEST_START, TEST_END, use_size_col=use_size,
        )
        base_test = test_result["base_cagr"]
        delta = res_f["cagr"] - base_test
        print(f"  {fname:<14s}  n={len(df_f):>3}  CAGR {res_f['cagr']:+7.2f}%  Δ vs test_base {delta:+7.2f}pp")


if __name__ == "__main__":
    main()
