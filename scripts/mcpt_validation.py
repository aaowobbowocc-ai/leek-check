"""
Monte Carlo Permutation Test (MCPT) 嚴格驗證 top 3 真 alpha 策略。

對每個策略：
  1. 取得實際訊號日 → 計算實際 mean return
  2. shuffle 1000 次（隨機重抽相同數量訊號日）
  3. 計算每次 shuffle 的 mean
  4. p-value = (shuffle mean >= 實際 mean) / 1000
  5. p < 0.01 = 真極度顯著

對 top 3:
  A. 配對交易 2408-2344
  B. 0050 自營商連買 3d/20d
  C. DCA timing (春節前 5 日)
"""
from __future__ import annotations

import io
import sys
from datetime import date
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_INST = ROOT / "data" / "cache" / "finmind" / "institutional"
COST = 0.34
N_PERMUTATIONS = 1000
SEED = 42


def load_ohlcv(tk):
    p = CACHE_YF / f"{tk}.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


# ════════════════════════════════════════════
# A. 配對交易 2408-2344 MCPT
# ════════════════════════════════════════════
def mcpt_pair_trading():
    print("\n" + "=" * 90)
    print("【A. 配對交易 2408-2344 MCPT (1000 次 shuffle)】")
    print("=" * 90)

    a_df = load_ohlcv("2408")[["date", "close"]]
    b_df = load_ohlcv("2344")[["date", "close"]]
    merged = pd.merge(a_df.rename(columns={"close": "a"}),
                      b_df.rename(columns={"close": "b"}),
                      on="date").sort_values("date").reset_index(drop=True)
    merged["log_a"] = np.log(merged["a"])
    merged["log_b"] = np.log(merged["b"])
    merged["spread"] = merged["log_a"] - merged["log_b"]
    merged["spread_mean"] = merged["spread"].rolling(60).mean()
    merged["spread_std"] = merged["spread"].rolling(60).std()
    merged["z"] = (merged["spread"] - merged["spread_mean"]) / merged["spread_std"]

    # 實際訊號 — z 觸 ±2.5 進場
    actual_trades = []
    in_pos = False; pos_dir = 0; pos_entry = None
    for i in range(60, len(merged) - 1):
        z = merged.iloc[i]["z"]
        if pd.isna(z): continue
        if not in_pos:
            if z > 2.5:
                in_pos = True; pos_dir = -1; pos_entry = i
            elif z < -2.5:
                in_pos = True; pos_dir = +1; pos_entry = i
        else:
            elapsed = i - pos_entry
            if abs(z) < 0.5 or elapsed >= 20:
                a0 = merged.iloc[pos_entry]["a"]; b0 = merged.iloc[pos_entry]["b"]
                a1 = merged.iloc[i]["a"]; b1 = merged.iloc[i]["b"]
                a_ret = (a1/a0 - 1) * 100; b_ret = (b1/b0 - 1) * 100
                gross = (a_ret - b_ret) if pos_dir == 1 else (b_ret - a_ret)
                actual_trades.append(gross - COST * 2)
                in_pos = False
    actual_mean = np.mean(actual_trades)
    n = len(actual_trades)
    print(f"\n  實際訊號數: {n}")
    print(f"  實際 mean: {actual_mean:+.2f}%")

    # MCPT — shuffle z 軌跡，重新算交易
    rng = np.random.default_rng(SEED)
    shuffle_means = []
    for trial in range(N_PERMUTATIONS):
        # 隨機選 N 個進場日 + 隨機 hold (1-20 天) + 隨機方向
        valid_idx = list(range(60, len(merged) - 21))
        random_entries = rng.choice(valid_idx, size=n, replace=False)
        random_holds = rng.integers(1, 20, size=n)
        random_dirs = rng.choice([-1, 1], size=n)
        trades = []
        for entry, hold, d in zip(random_entries, random_holds, random_dirs):
            a0 = merged.iloc[entry]["a"]; b0 = merged.iloc[entry]["b"]
            a1 = merged.iloc[entry + hold]["a"]; b1 = merged.iloc[entry + hold]["b"]
            a_ret = (a1/a0 - 1) * 100; b_ret = (b1/b0 - 1) * 100
            gross = (a_ret - b_ret) if d == 1 else (b_ret - a_ret)
            trades.append(gross - COST * 2)
        shuffle_means.append(np.mean(trades))

    shuffle_means = np.array(shuffle_means)
    p_value = (shuffle_means >= actual_mean).sum() / N_PERMUTATIONS
    print(f"  Shuffle 1000 次 mean 分佈:")
    print(f"    最低 5%: {np.percentile(shuffle_means, 5):+.2f}%")
    print(f"    中位:   {np.median(shuffle_means):+.2f}%")
    print(f"    最高 95%: {np.percentile(shuffle_means, 95):+.2f}%")
    print(f"  P-value: {p_value:.4f}")
    if p_value < 0.001:
        print(f"  → ⭐⭐⭐ 極度顯著 (p < 0.001) — 真 alpha 確認")
    elif p_value < 0.01:
        print(f"  → ⭐⭐ 顯著 (p < 0.01)")
    elif p_value < 0.05:
        print(f"  → ⭐ 邊緣 (p < 0.05)")
    else:
        print(f"  → ❌ 未過 (p >= 0.05)")


# ════════════════════════════════════════════
# B. 0050 自營商連買 3d/20d MCPT
# ════════════════════════════════════════════
def mcpt_dealer_signal():
    print("\n" + "=" * 90)
    print("【B. 0050 自營商連買 3d/20d MCPT (1000 次 shuffle)】")
    print("=" * 90)

    ohlcv = load_ohlcv("0050")
    inst_p = CACHE_INST / "0050.parquet"
    inst = pd.read_parquet(inst_p)
    inst["date"] = pd.to_datetime(inst["date"]).dt.date
    pivot = inst.pivot_table(index="date", columns="name", values="net_buy",
                              aggfunc="sum").reset_index()
    pivot.columns.name = None
    pivot = pivot.sort_values("date").reset_index(drop=True)
    pivot["is_buy"] = pivot["Dealer_self"] > 0
    pivot["consec"] = pivot["is_buy"].astype(int).rolling(3).sum()
    pivot["trigger"] = pivot["consec"] == 3

    # 實際訊號
    sig_dates = pivot[pivot["trigger"]]["date"].tolist()
    o_dates = list(ohlcv["date"])
    actual_rets = []
    for d in sig_dates:
        if d not in o_dates: continue
        idx = o_dates.index(d)
        if idx + 1 + 20 >= len(o_dates): continue
        entry = float(ohlcv.iloc[idx + 1]["open"])
        exit_p = float(ohlcv.iloc[idx + 1 + 20]["close"])
        actual_rets.append((exit_p / entry - 1) * 100)
    actual_mean = np.mean(actual_rets)
    n = len(actual_rets)
    print(f"\n  實際訊號數: {n}")
    print(f"  實際 mean (20 日報酬): {actual_mean:+.2f}%")

    # MCPT — shuffle 隨機選 N 個日子
    rng = np.random.default_rng(SEED)
    shuffle_means = []
    valid_idx = list(range(0, len(ohlcv) - 22))
    for trial in range(N_PERMUTATIONS):
        random_idx = rng.choice(valid_idx, size=n, replace=False)
        rets = []
        for idx in random_idx:
            entry = float(ohlcv.iloc[idx + 1]["open"])
            exit_p = float(ohlcv.iloc[idx + 1 + 20]["close"])
            rets.append((exit_p / entry - 1) * 100)
        shuffle_means.append(np.mean(rets))

    shuffle_means = np.array(shuffle_means)
    p_value = (shuffle_means >= actual_mean).sum() / N_PERMUTATIONS
    print(f"  Shuffle 1000 次 mean 分佈:")
    print(f"    最低 5%: {np.percentile(shuffle_means, 5):+.2f}%")
    print(f"    中位:   {np.median(shuffle_means):+.2f}%")
    print(f"    最高 95%: {np.percentile(shuffle_means, 95):+.2f}%")
    print(f"  P-value: {p_value:.4f}")
    if p_value < 0.001:
        print(f"  → ⭐⭐⭐ 極度顯著 (p < 0.001)")
    elif p_value < 0.01:
        print(f"  → ⭐⭐ 顯著 (p < 0.01)")
    elif p_value < 0.05:
        print(f"  → ⭐ 邊緣 (p < 0.05)")
    else:
        print(f"  → ❌ 未過")


# ════════════════════════════════════════════
# C. DCA timing 春節前 5 日 MCPT
# ════════════════════════════════════════════
def mcpt_dca_timing():
    print("\n" + "=" * 90)
    print("【C. DCA timing 春節前 5 日 MCPT (1000 次 shuffle)】")
    print("=" * 90)

    ohlcv = load_ohlcv("0050")
    cny_dates = ["2017-01-28", "2018-02-16", "2019-02-05", "2020-01-25",
                 "2021-02-12", "2022-02-01", "2023-01-22", "2024-02-10",
                 "2025-01-29", "2026-02-17"]
    cny_dates = [pd.Timestamp(d).date() for d in cny_dates]

    # 春節前 5 日 daily return
    actual_rets = []
    o_dates = list(ohlcv["date"])
    for cny in cny_dates:
        for d in o_dates:
            delta = (d - cny).days
            if -7 <= delta < 0:  # 前 7 日內
                idx = o_dates.index(d)
                if idx > 0:
                    ret = (float(ohlcv.iloc[idx]["close"]) /
                           float(ohlcv.iloc[idx - 1]["close"]) - 1) * 100
                    actual_rets.append(ret)
    actual_mean = np.mean(actual_rets)
    n = len(actual_rets)
    print(f"\n  實際春節前 5 日數: {n}")
    print(f"  實際 mean (1 日報酬): {actual_mean:+.3f}%")

    # MCPT — random 選 N 個交易日
    rng = np.random.default_rng(SEED)
    shuffle_means = []
    for trial in range(N_PERMUTATIONS):
        random_idx = rng.choice(range(1, len(ohlcv)), size=n, replace=False)
        rets = []
        for idx in random_idx:
            ret = (float(ohlcv.iloc[idx]["close"]) /
                   float(ohlcv.iloc[idx - 1]["close"]) - 1) * 100
            rets.append(ret)
        shuffle_means.append(np.mean(rets))

    shuffle_means = np.array(shuffle_means)
    p_value = (shuffle_means >= actual_mean).sum() / N_PERMUTATIONS
    print(f"  Shuffle 1000 次 mean 分佈:")
    print(f"    最低 5%: {np.percentile(shuffle_means, 5):+.3f}%")
    print(f"    中位:   {np.median(shuffle_means):+.3f}%")
    print(f"    最高 95%: {np.percentile(shuffle_means, 95):+.3f}%")
    print(f"  P-value: {p_value:.4f}")
    if p_value < 0.001:
        print(f"  → ⭐⭐⭐ 極度顯著 (p < 0.001)")
    elif p_value < 0.01:
        print(f"  → ⭐⭐ 顯著 (p < 0.01)")
    elif p_value < 0.05:
        print(f"  → ⭐ 邊緣 (p < 0.05)")
    else:
        print(f"  → ❌ 未過")


def main():
    print("=" * 90)
    print("Monte Carlo Permutation Test (1000 次 shuffle)")
    print("=" * 90)
    mcpt_pair_trading()
    mcpt_dealer_signal()
    mcpt_dca_timing()


if __name__ == "__main__":
    main()
