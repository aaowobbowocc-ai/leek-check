"""
EH v3 + 連續漲停雙策略並行回測。

Hypothesis：兩個策略 alpha 來源不同（主升段早期 vs 短期動能爆發），
組合後 Sharpe 應改善（更平滑），CAGR 可能 > 單一最佳策略。

倉位配置：
  - Core 0050 = 60%
  - Sat A: EH v3 = 20% (max 4 concurrent, 5%/筆，conviction 加權)
  - Sat B: 連續漲停 = 15% (max 5 concurrent, 3%/筆)
  - Cash = 5%

實作上由於 V2 framework 是 trade-driven（閒置停核心），
直接合併兩支策略的 trades 入同一 V2 portfolio，per_trade=5%。
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
    apply_4_conviction_weight,
    filter_1_big_holder_slope,
    nearest_price,
    run_v2_portfolio,
)
from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
EH_TRAILING = ROOT / "logs" / "early_hunter_trailing_v2.csv"
LIMITUP = ROOT / "logs" / "consecutive_limitup_trades.csv"


def load_eh_v3() -> pd.DataFrame:
    df = pd.read_csv(EH_TRAILING)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.date
    df["ticker"] = df["ticker"].astype(str)
    df = filter_1_big_holder_slope(df, min_slope=-0.5)
    df = apply_2_early_cut(df, cut_days=60)
    df = apply_4_conviction_weight(df)
    df["source"] = "eh_v3"
    return df


def load_limitup(max_concurrent_proxy: int = 5) -> pd.DataFrame:
    df = pd.read_csv(LIMITUP)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.date
    df["ticker"] = df["ticker"].astype(str)
    # 模擬 max_concurrent FIFO 過濾：依 entry_date 排序，遇到 concurrent 滿就跳過
    df = df.sort_values(
        ["entry_date", "n_limit_up_5d"], ascending=[True, False]
    ).reset_index(drop=True)
    open_pos: list[date] = []
    keep = []
    for _, t in df.iterrows():
        # 移除已 exit 的
        open_pos = [d for d in open_pos if d > t["entry_date"]]
        if len(open_pos) >= max_concurrent_proxy:
            keep.append(False)
            continue
        keep.append(True)
        open_pos.append(t["exit_date"])
    df = df[keep].copy()
    df["source"] = "limitup"
    df["size_pct"] = 0.03
    return df[["ticker", "entry_date", "exit_date", "gross_return_pct", "source", "size_pct"]]


def main() -> None:
    eh_v3 = load_eh_v3()
    print(f"EH v3 trades: {len(eh_v3)}")

    limitup = load_limitup(max_concurrent_proxy=5)
    print(f"Limit-up trades (max=5): {len(limitup)}")

    # 0050 prices
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    cols = ["ticker", "entry_date", "exit_date", "gross_return_pct", "source", "size_pct"]
    eh_v3_slim = eh_v3[cols].copy()
    combined = pd.concat([eh_v3_slim, limitup], ignore_index=True)
    combined = combined.sort_values("entry_date").reset_index(drop=True)
    print(f"Combined: {len(combined)} trades")

    start_date = combined["entry_date"].min()
    end_date = combined["exit_date"].max()

    # 跑各別 + 組合
    print("\n" + "=" * 70)
    print(f"V2 Portfolio 比較 ({start_date} ~ {end_date})")
    print("=" * 70)

    results = []
    for label, df_in in [
        ("0050 only", None),
        ("EH v3", eh_v3_slim),
        ("Limitup", limitup),
        ("Dual (EH v3 + Limitup)", combined),
    ]:
        if df_in is None:
            # 0050 buy-and-hold
            p_start = nearest_price(prices_0050, start_date)
            p_end = nearest_price(prices_0050, end_date)
            years = (end_date - start_date).days / 365.25
            cagr = ((p_end / p_start) ** (1 / years) - 1) * 100
            print(f"  {label:<22} CAGR {cagr:+7.2f}%   alpha   0.00pp")
            results.append((label, cagr, 0.0))
            continue
        res = run_v2_portfolio(
            df_in, prices_0050, start_date, end_date, use_size_col=True,
        )
        print(
            f"  {label:<22} CAGR {res['cagr']:+7.2f}%   "
            f"alpha {res['alpha']:+7.2f}pp   n={res['n_trades']}"
        )
        results.append((label, res["cagr"], res["alpha"]))

    # 結論
    print("\n" + "=" * 70)
    eh = next(r for r in results if r[0] == "EH v3")
    lu = next(r for r in results if r[0] == "Limitup")
    dual = next(r for r in results if r[0] == "Dual (EH v3 + Limitup)")
    print(f"  EH v3 alone   alpha: {eh[2]:+.2f}pp")
    print(f"  Limitup alone alpha: {lu[2]:+.2f}pp")
    print(f"  Dual combined alpha: {dual[2]:+.2f}pp")
    print(f"  Dual − max(EH,LU)  : {dual[2] - max(eh[2], lu[2]):+.2f}pp")
    if dual[2] > max(eh[2], lu[2]) + 0.3:
        print("  ✅ 組合產生 diversification benefit（贏單一最佳策略）")
    else:
        print("  ❌ 組合無顯著加值（單一策略已最佳）")


if __name__ == "__main__":
    main()
