"""
EH v3 trade-level 評估（解決 weekly walk-forward 的 capital 排隊雜訊）。

不跑 V2 portfolio，直接看：
  - Win rate
  - Mean / median return
  - Sharpe of trade returns（trade 為單位）
  - 樣本 n

對 baseline vs 各 filter 各別作用後 vs 全 v3，做大 sample 對比。
這樣排除 V2 capital 限制造成的 sample 排隊問題。
"""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import numpy as np
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
)

WEEKLY_CSV = ROOT / "logs" / "early_hunter_weekly_v2.csv"
TRAIN_END = date(2022, 12, 31)
TEST_START = date(2023, 1, 1)


def stats(label: str, df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"label": label, "n": 0}
    r = df["gross_return_pct"]
    win = (r > 0).mean() * 100
    mean = r.mean()
    median = r.median()
    std = r.std()
    sharpe = mean / std if std > 0 else 0
    # 年化期望（用平均 hold 推）
    avg_hold = df["hold_days"].mean() if "hold_days" in df else 200
    annualized = ((1 + mean / 100) ** (365 / max(avg_hold, 1)) - 1) * 100
    return {
        "label": label,
        "n": n,
        "win_pct": win,
        "mean_pct": mean,
        "median_pct": median,
        "sharpe": sharpe,
        "annualized": annualized,
    }


def report(rows: list[dict], header: str) -> None:
    print(f"\n{header}")
    print("─" * 90)
    print(f"  {'方案':<20} {'n':>6} {'win%':>7} {'mean':>8} {'median':>8} {'Sharpe':>8} {'年化':>8}")
    for r in rows:
        if r["n"] == 0:
            print(f"  {r['label']:<20} {'0':>6} {'-':>7} {'-':>8} {'-':>8} {'-':>8} {'-':>8}")
            continue
        print(
            f"  {r['label']:<20} {r['n']:>6} "
            f"{r['win_pct']:>6.1f}% {r['mean_pct']:>+7.2f}% "
            f"{r['median_pct']:>+7.2f}% {r['sharpe']:>8.3f} {r['annualized']:>+7.1f}%"
        )


def main() -> None:
    df = pd.read_csv(WEEKLY_CSV)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.date
    df["ticker"] = df["ticker"].astype(str)
    print(f"Weekly sample: {len(df)} trades")

    train = df[df["entry_date"] <= TRAIN_END].reset_index(drop=True)
    test = df[df["entry_date"] >= TEST_START].reset_index(drop=True)

    # 對每個 sample 跑全部變體
    variants = [
        ("baseline", lambda d: d),
        ("#1 big_holder", lambda d: filter_1_big_holder_slope(d, min_slope=-0.5)),
        ("#2 early_cut", lambda d: apply_2_early_cut(d, cut_days=60)),
        ("#1+#2", lambda d: apply_2_early_cut(filter_1_big_holder_slope(d, min_slope=-0.5), cut_days=60)),
    ]
    # 對 #4 conviction 我們不直接 filter trades，只是改 size — 此處跳過
    # （weekly 沒有 entry_score 對應 raw csv，且 score 與報酬相關性 0.002）

    for label, sample in [("ALL (10182)", df), ("TRAIN (5593)", train), ("TEST OOS (4589)", test)]:
        rows = []
        for vname, vf in variants:
            try:
                vd = vf(sample)
                rows.append(stats(vname, vd))
            except Exception as e:
                rows.append({"label": vname, "n": 0})
                print(f"  {vname} 失敗: {e}")
        report(rows, f"=== {label} ===")


if __name__ == "__main__":
    main()
