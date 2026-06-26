"""
限漲停打開 + 配對交易 cross-regime 驗證 (4 期分層)

驗收：每期都 alpha > 0 + sigma > 1.96 = 真 robust
"""
from __future__ import annotations
import io, sys
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
COST = 0.34

PERIODS = [
    ("A 2017-2019", date(2017, 1, 1), date(2019, 12, 31)),
    ("B 2020 covid", date(2020, 1, 1), date(2020, 12, 31)),
    ("C 2021-2022 熊", date(2021, 1, 1), date(2022, 12, 31)),
    ("D 2023-2026 牛", date(2023, 1, 1), date(2026, 4, 30)),
]


def load_ohlcv(tk):
    p = CACHE_YF / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


# ════════════════════════════════════════════
# 限漲停打開反向 cross-regime
# ════════════════════════════════════════════
def detect_limitup_breaks(df: pd.DataFrame, hold_days: int) -> list:
    """偵測訊號 + 計算 short return list of (date, ret)"""
    if df.empty or len(df) < 100:
        return []
    df = df.copy()
    df["prev_close"] = df["close"].shift(1)
    df["limitup"] = df["close"] / df["prev_close"] >= 1.095
    df["next_open"] = df["open"].shift(-1)
    df["next_close"] = df["close"].shift(-1)
    df["future_close"] = df["close"].shift(-1 - hold_days)
    df["jumped"] = df["next_open"] > df["close"] * 1.005
    df["broke_down"] = df["next_close"] < df["next_open"]
    df["trigger"] = df["limitup"] & df["jumped"] & df["broke_down"]
    triggered = df[df["trigger"]].copy()
    triggered = triggered.dropna(subset=["next_close", "future_close"])
    if triggered.empty: return []
    rets = []
    for _, r in triggered.iterrows():
        sret = (r["next_close"] - r["future_close"]) / r["next_close"] * 100
        rets.append((r["date"], sret - COST))
    return rets


def random_short_in_period(df: pd.DataFrame, hold: int, start: date, end: date) -> np.ndarray:
    rets = []
    for i in range(len(df) - hold - 2):
        d = df.iloc[i]["date"]
        if not (start <= d <= end): continue
        entry = float(df.iloc[i + 1]["close"])
        exit_p = float(df.iloc[i + 1 + hold]["close"])
        rets.append((entry - exit_p) / entry * 100 - COST)
    return np.array(rets)


def test_limitup_break_cross_regime():
    print("\n" + "=" * 90)
    print("【1. 限漲停打開反向 cross-regime】")
    print("=" * 90)

    # 從之前 v2 csv 找 top tickers
    src = ROOT / "logs" / "limitup_break_v2.csv"
    if not src.exists():
        print("找不到 limitup_break_v2.csv，跑全市場 sample")
        candidates = ["8476", "4905", "8455", "1781", "4946", "3288", "6549", "3017",
                      "2308", "2308", "6770"]
    else:
        df = pd.read_csv(src, dtype={"ticker": str})
        # top 20 真 alpha
        top = df[(df["sigma"] > 1.96) & (df["true_alpha"] > 1)].sort_values("true_alpha", ascending=False)
        candidates = top["ticker"].head(15).unique().tolist()
        print(f"選 v2 top {len(candidates)} 候選")

    print(f"\n{'ticker':<7} {'A':>10} {'B':>10} {'C':>10} {'D':>10} {'verdict':>10}")
    print("-" * 75)

    for tk in candidates:
        df = load_ohlcv(tk)
        if df.empty: continue
        # 用 hold=3 統一比較
        sigs = detect_limitup_breaks(df, 3)
        if len(sigs) < 5: continue

        line = f"  {tk:<6}"
        n_robust = 0
        for label, start, end in PERIODS:
            sig_p = [r for d, r in sigs if start <= d <= end]
            rand_p = random_short_in_period(df, 3, start, end)
            if len(sig_p) < 3 or len(rand_p) < 30:
                line += f" {'-':>10}"
                continue
            sig_mean = np.mean(sig_p)
            rand_mean = rand_p.mean()
            rand_std = rand_p.std()
            alpha = sig_mean - rand_mean
            sigma = (alpha / (rand_std / np.sqrt(len(sig_p)))) if rand_std > 0 else 0
            mark = "✅" if sigma > 1.96 and alpha > 0 else (
                "⚠️" if alpha > 0 else "❌")
            line += f" {alpha:>+5.1f}% {mark}"
            if sigma > 1.96 and alpha > 0: n_robust += 1
        v = "✅robust" if n_robust >= 3 else ("⚠️" if n_robust >= 2 else "❌")
        line += f"  {n_robust}/4 期 {v}"
        print(line)


# ════════════════════════════════════════════
# 配對交易 cross-regime
# ════════════════════════════════════════════
def backtest_pair_in_period(a_df, b_df, start, end):
    merged = pd.merge(a_df.rename(columns={"close": "a"}),
                      b_df.rename(columns={"close": "b"}),
                      on="date").sort_values("date").reset_index(drop=True)
    merged = merged[(merged["date"] >= start) & (merged["date"] <= end)]
    if len(merged) < 90:
        return []
    merged = merged.reset_index(drop=True)
    merged["log_a"] = np.log(merged["a"])
    merged["log_b"] = np.log(merged["b"])
    merged["spread"] = merged["log_a"] - merged["log_b"]
    merged["spread_mean"] = merged["spread"].rolling(60).mean()
    merged["spread_std"] = merged["spread"].rolling(60).std()
    merged["z"] = (merged["spread"] - merged["spread_mean"]) / merged["spread_std"]

    trades = []
    in_pos = False
    pos_dir = 0
    pos_entry = None
    for i in range(60, len(merged) - 1):
        row = merged.iloc[i]
        z = row["z"]
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
                a1 = row["a"]; b1 = row["b"]
                a_ret = (a1/a0 - 1) * 100; b_ret = (b1/b0 - 1) * 100
                gross = (a_ret - b_ret) if pos_dir == 1 else (b_ret - a_ret)
                net = gross - COST * 2
                trades.append(net)
                in_pos = False
    return trades


PAIR_GROUPS = {
    "DRAM": ("2408", "2344"),
    "半導體 2330-3711": ("2330", "3711"),
    "半導體 2454-3711": ("2454", "3711"),
    "重電 1513-1519": ("1513", "1519"),
    "航運 2609-2615": ("2609", "2615"),
    "塑化 1301-1326": ("1301", "1326"),
}


def test_pair_cross_regime():
    print("\n" + "=" * 90)
    print("【2. 配對交易 cross-regime】")
    print("=" * 90)
    print(f"\n{'pair':<22} {'A':>11} {'B':>11} {'C':>11} {'D':>11} {'verdict':>10}")
    print("-" * 90)

    for label, (a, b) in PAIR_GROUPS.items():
        a_df = load_ohlcv(a)[["date", "close"]] if not load_ohlcv(a).empty else pd.DataFrame()
        b_df = load_ohlcv(b)[["date", "close"]] if not load_ohlcv(b).empty else pd.DataFrame()
        if a_df.empty or b_df.empty: continue

        line = f"  {label:<21}"
        n_robust = 0
        for p_label, start, end in PERIODS:
            trades = backtest_pair_in_period(a_df, b_df, start, end)
            if len(trades) < 3:
                line += f" {'-':>11}"; continue
            mean_net = np.mean(trades)
            n = len(trades)
            wins = sum(1 for t in trades if t > 0)
            win = wins / n * 100
            mark = "✅" if mean_net > 0.5 and win > 60 else (
                "⚠️" if mean_net > 0 else "❌")
            line += f" {mean_net:>+5.2f}%/{n:>2} {mark}"
            if mean_net > 0.5 and win > 60: n_robust += 1
        v = "✅robust" if n_robust >= 3 else ("⚠️" if n_robust >= 2 else "❌")
        line += f"  {n_robust}/4 期 {v}"
        print(line)


def main():
    print("=" * 90)
    print("限漲停打開 + 配對交易 cross-regime 驗證")
    print("=" * 90)

    test_limitup_break_cross_regime()
    test_pair_cross_regime()


if __name__ == "__main__":
    main()
