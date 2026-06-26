"""
Tick 微結構訊號研究 — 看哪些 daily metrics 預測未來報酬。

對 2330 一年 daily metrics（從 tick 抽取），測：
  - 各 metric 跟 next-day return 的相關性
  - Top quartile vs Bottom quartile 的 win rate
  - 哪個訊號值得加進策略
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategy.tick_microstructure import rolling_daily_metrics  # noqa: E402


def main() -> None:
    ticker = "2330"
    start = date(2025, 4, 1)
    end = date(2026, 4, 25)
    print(f"研究 {ticker} {start} ~ {end} 微結構訊號")
    print("=" * 80)

    df = rolling_daily_metrics(ticker, start, end)
    if df.empty:
        print("❌ 無 daily metrics（tick cache 還沒抓完？）")
        return

    print(f"\n[1/3] Daily metrics: {len(df)} days")
    print(df.head(3).to_string())

    # 計算 next-day return
    df = df.sort_values("date").reset_index(drop=True)
    df["next_close"] = df["close"].shift(-1)
    df["next_ret"] = (df["next_close"] / df["close"] - 1) * 100
    df["t+5_close"] = df["close"].shift(-5)
    df["t+5_ret"] = (df["t+5_close"] / df["close"] - 1) * 100
    df["t+20_close"] = df["close"].shift(-20)
    df["t+20_ret"] = (df["t+20_close"] / df["close"] - 1) * 100

    metrics_to_test = [
        "inner_ratio",
        "io_ratio",
        "big_ratio",
        "morning_inner_ratio",
        "closing_inner_ratio",
        "close_vs_vwap_pct",
        "big_count",
    ]

    print("\n" + "=" * 80)
    print("[2/3] 各 metric 與未來報酬 Spearman 相關性")
    print("=" * 80)
    for horizon in ["next_ret", "t+5_ret", "t+20_ret"]:
        print(f"\n  {horizon}:")
        print(f"    {'metric':<28} {'spear':>7} {'bot_q win%':>11} {'top_q win%':>11} {'lift':>7}")
        for m in metrics_to_test:
            valid = df[[m, horizon]].dropna()
            if len(valid) < 30:
                continue
            spear = valid[m].rank().corr(valid[horizon].rank())
            q1, q3 = valid[m].quantile([0.25, 0.75]).values
            bot = valid[valid[m] <= q1]
            top = valid[valid[m] >= q3]
            bot_win = (bot[horizon] > 0).mean() * 100 if len(bot) else 0
            top_win = (top[horizon] > 0).mean() * 100 if len(top) else 0
            print(f"    {m:<28s} {spear:>+6.3f} {bot_win:>10.1f}% {top_win:>10.1f}% "
                  f"{top_win - bot_win:>+6.1f}pp")

    # 簡單策略：「inner_ratio 高 = 賣壓強 → 隔天空（或反向 long）」測試
    print("\n" + "=" * 80)
    print("[3/3] 簡單策略測試: inner_ratio > 0.55 → 隔日表現")
    print("=" * 80)
    high_inner = df[df["inner_ratio"] > 0.55]
    low_inner = df[df["inner_ratio"] < 0.45]
    print(f"  inner_ratio > 0.55 (賣壓強): n={len(high_inner)}, "
          f"next-day mean {high_inner['next_ret'].mean():+.3f}%, "
          f"win {(high_inner['next_ret'] > 0).mean() * 100:.1f}%")
    print(f"  inner_ratio < 0.45 (買壓強): n={len(low_inner)}, "
          f"next-day mean {low_inner['next_ret'].mean():+.3f}%, "
          f"win {(low_inner['next_ret'] > 0).mean() * 100:.1f}%")

    # 大單訊號：big_ratio 高的隔日表現
    print(f"\n  big_ratio > 0.5 (大單主導): n={(df['big_ratio'] > 0.5).sum()}")
    big_q = df[df["big_ratio"] > df["big_ratio"].quantile(0.8)]
    print(f"    next-day mean: {big_q['next_ret'].mean():+.3f}%, "
          f"win {(big_q['next_ret'] > 0).mean() * 100:.1f}%")

    # 寫出
    out = ROOT / "logs" / f"tick_metrics_{ticker}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
