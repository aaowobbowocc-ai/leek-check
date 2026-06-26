"""
EH 部分減碼規則：
  漲到 +50% 時，賣半部位（鎖利）；剩半繼續 trailing -25pp。

不是 hyperparameter sweep，是新規則。Overfit 風險低。

實作方式：
  對每筆 trade 重新模擬：
    1. 從 entry 起逐日跟蹤
    2. 若曾達 +50%，於該日收盤賣 50%（鎖利 +50% × 0.5 = +25%）
    3. 剩 50% 繼續 trailing -25pp / hard stop 200MA × 0.85
    4. 最終 return = 0.5 × +50% + 0.5 × (剩半 trailing 結果)

對比 baseline trailing only。
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
RAW_CSV = ROOT / "logs" / "early_hunter_20260425_160432.csv"


def simulate_partial_profit(
    ohlcv: pd.DataFrame,
    entry_date: date,
    entry_price: float,
    profit_take_pct: float = 50.0,
    profit_take_fraction: float = 0.5,
    trailing_pp: float = 25.0,
    hard_stop_ma_pct: float = 0.85,
) -> tuple[float, date, str]:
    """模擬：到 +profit_take_pct% 賣 fraction，剩繼續 trailing。"""
    df = ohlcv.sort_values("date").reset_index(drop=True).copy()
    df["ma200"] = df["close"].rolling(200).mean()
    after = df[df["date"] >= entry_date].reset_index(drop=True)
    if len(after) < 2:
        return 0.0, entry_date, "no_data"

    locked_profit = 0.0
    fraction_remaining = 1.0
    profit_taken = False
    peak = 0.0

    for i in range(1, min(1500, len(after))):
        c = float(after.iloc[i]["close"])
        ma = after.iloc[i]["ma200"]
        ret = (c / entry_price - 1) * 100

        # 部分減碼觸發
        if not profit_taken and ret >= profit_take_pct:
            locked_profit = profit_take_pct * profit_take_fraction
            fraction_remaining = 1.0 - profit_take_fraction
            profit_taken = True

        if ret > peak:
            peak = ret

        # Hard stop
        if pd.notna(ma) and c < float(ma) * hard_stop_ma_pct:
            final_ret = locked_profit + ret * fraction_remaining
            return final_ret, after.iloc[i]["date"], (
                "hard_stop_after_partial" if profit_taken else "hard_stop"
            )

        # Trailing
        if peak >= 5.0 and (peak - ret) >= trailing_pp:
            final_ret = locked_profit + ret * fraction_remaining
            return final_ret, after.iloc[i]["date"], (
                "trailing_after_partial" if profit_taken else "trailing"
            )

    last = after.iloc[-1]
    final_ret = locked_profit + ((float(last["close"]) / entry_price - 1) * 100) * fraction_remaining
    return final_ret, last["date"], "end_of_data"


def main() -> None:
    raw = pd.read_csv(RAW_CSV)
    raw["entry_date"] = pd.to_datetime(raw["entry_date"]).dt.date
    raw["entry_price"] = raw["entry_price"].astype(float)
    raw["ticker"] = raw["ticker"].astype(str)

    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    print("=" * 70)
    print("EH partial profit 規則測試")
    print("=" * 70)

    configs = [
        ("baseline trailing -25pp", None, None),
        ("+30% 賣半",  30, 0.5),
        ("+50% 賣半",  50, 0.5),
        ("+50% 賣 1/3", 50, 0.33),
        ("+75% 賣半",  75, 0.5),
        ("+100% 賣半", 100, 0.5),
        ("+50% 全賣",   50, 1.0),  # 強制 +50% 出場（無 trailing）
    ]

    print(f"  {'config':<28} {'CAGR':>8} {'alpha':>8} {'mean trade':>10}")
    for label, take_pct, take_frac in configs:
        rows = []
        for _, r in raw.iterrows():
            ohlcv = load_ohlcv_cache(r["ticker"], CACHE_YF)
            if ohlcv.empty:
                continue
            ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
            if take_pct is None:
                # baseline trailing
                from early_hunter_trailing_resim import simulate_trailing_exit
                ret, exit_d, reason = simulate_trailing_exit(
                    ohlcv, r["entry_date"], r["entry_price"]
                )
            else:
                ret, exit_d, reason = simulate_partial_profit(
                    ohlcv, r["entry_date"], r["entry_price"],
                    profit_take_pct=take_pct,
                    profit_take_fraction=take_frac,
                )
            rows.append({
                "ticker": r["ticker"],
                "entry_date": r["entry_date"],
                "exit_date": exit_d,
                "gross_return_pct": round(ret, 2),
                "exit_reason": reason,
                "hold_days": (exit_d - r["entry_date"]).days,
            })
        df_t = pd.DataFrame(rows)
        # Apply v3.2 filters (#1+#2)
        df_t = filter_1_big_holder_slope(df_t, min_slope=-0.5)
        df_t = apply_2_early_cut(df_t, cut_days=60)
        start_d = df_t["entry_date"].min()
        end_d = df_t["exit_date"].max()
        res = run_v2_portfolio(df_t, prices_0050, start_d, end_d)
        mean_t = df_t["gross_return_pct"].mean()
        print(f"  {label:<28} {res['cagr']:>+7.2f}% {res['alpha']:>+7.2f}pp {mean_t:>+9.2f}%")


if __name__ == "__main__":
    main()
