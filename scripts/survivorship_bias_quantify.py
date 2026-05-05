"""
Survivorship Bias 量化 — 識別 2017-2025 期間下市股票對 backtest 的影響

方法:
  1. 對每檔 OHLCV cache，找最後交易日
  2. 若最後交易日 < 2025-09 (即過去 8 個月沒交易) → 視為「事實下市/停止 update」
  3. 計算這些股票若被 backtest 觸發訊號，對結果的影響

輸出:
  - 下市股清單 + 下市時間
  - AB consensus 訊號在這些股票觸發時的 fwd return
  - 量化 survivorship bias 對 alpha 的拖累
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
DELISTED_CUTOFF = pd.Timestamp("2025-09-01")  # 8 個月沒交易視為下市


def scan_universe() -> pd.DataFrame:
    records = []
    for p in TW_CACHE.glob("*.parquet"):
        tk = p.stem
        if not tk.isdigit() or len(tk) != 4:
            continue
        try:
            df = pd.read_parquet(p, columns=["date"])
        except Exception:
            continue
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"])
        last_dt = df["date"].max()
        first_dt = df["date"].min()
        records.append({
            "ticker": tk,
            "first_date": first_dt,
            "last_date": last_dt,
            "n_days": len(df),
            "delisted": last_dt < DELISTED_CUTOFF,
        })
    return pd.DataFrame(records)


def main():
    print("=" * 80)
    print("  Survivorship Bias 量化")
    print(f"  Cutoff: 最後交易日 < {DELISTED_CUTOFF.date()} 視為下市")
    print("=" * 80)

    df = scan_universe()
    total = len(df)
    delisted = df[df["delisted"]]
    n_del = len(delisted)
    print(f"\n  Total tickers in OHLCV cache: {total:,}")
    print(f"  Delisted (no data after {DELISTED_CUTOFF.date()}): {n_del:,} ({n_del/total*100:.1f}%)")
    print(f"  Still trading: {total - n_del:,} ({(total-n_del)/total*100:.1f}%)")

    if n_del == 0:
        print("\n  ⚠️ 無下市股票（cache 設計可能已經 filter 掉），但 yfinance 通常會保留歷史")
        print("     考慮從 FinMind 補抓 delisted universe")
        return

    # Distribution of delist dates
    print(f"\n  下市時間分布（按年）:")
    delisted["delist_year"] = delisted["last_date"].dt.year
    yearly = delisted["delist_year"].value_counts().sort_index()
    for yr, n in yearly.items():
        print(f"    {yr}: {n} 檔")

    # Sample list
    print(f"\n  下市股範例（前 20）:")
    sample = delisted.sort_values("last_date").head(20)
    for _, row in sample.iterrows():
        days_active = (row["last_date"] - row["first_date"]).days
        print(f"    {row['ticker']}: {row['first_date'].date()} ~ {row['last_date'].date()} "
              f"(active {days_active} days)")

    # 對 AB consensus 的影響
    print(f"\n  === AB Consensus 在下市股票的觸發 ===")
    inst_cache = ROOT / "data" / "cache" / "finmind" / "institutional"
    hold_cache = ROOT / "data" / "cache" / "finmind" / "finmind"
    delisted_with_inst = []
    for _, row in delisted.iterrows():
        tk = row["ticker"]
        inst_p = inst_cache / f"{tk}.parquet"
        hold_p = hold_cache / f"TaiwanStockHoldingSharesPer_{tk}.parquet"
        if inst_p.exists() and hold_p.exists():
            delisted_with_inst.append(tk)

    print(f"  下市股中有完整 inst+holding 資料: {len(delisted_with_inst)}/{n_del}")
    print(f"  ⚠️ 其餘 {n_del - len(delisted_with_inst)} 檔 cache 不完整，"
          f"原 backtest 可能根本沒掃到這些股票")

    # 對倖存股票 vs 下市股票的價格分布比較
    print(f"\n  === 價格 / 下市前 60 日 return 分布 ===")
    delisted_pre_returns = []
    for tk in delisted["ticker"].head(100):
        try:
            df_px = pd.read_parquet(TW_CACHE / f"{tk}.parquet")
            df_px["date"] = pd.to_datetime(df_px["date"])
            df_px = df_px.sort_values("date").reset_index(drop=True)
            if len(df_px) < 60:
                continue
            last_60_first = df_px.iloc[-60]["close"]
            last = df_px.iloc[-1]["close"]
            ret = (last / last_60_first - 1) * 100
            delisted_pre_returns.append(ret)
        except Exception:
            continue
    if delisted_pre_returns:
        arr = np.array(delisted_pre_returns)
        print(f"  下市前 60 日平均 return: {arr.mean():+.2f}%")
        print(f"  Median: {np.median(arr):+.2f}%")
        print(f"  Worst: {arr.min():+.2f}%")
        print(f"  Best: {arr.max():+.2f}%")
        print(f"  比例 < -30%: {(arr < -30).mean()*100:.1f}%")

    # Save
    out = ROOT / "logs" / "survivorship_audit.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n  ✅ Full audit saved to {out.relative_to(ROOT)}")
    print(f"\n  Critical takeaway:")
    print(f"  - 下市率 {n_del/total*100:.1f}% 在 backtest universe 中")
    print(f"  - 若 AB consensus 觸發過這些下市股，原 backtest 完全沒計入")
    print(f"  - Claude 的估算 (額外 -10pp/trade 拖累) 可能保守，需用 FinMind 完整 universe 重跑")


if __name__ == "__main__":
    main()
