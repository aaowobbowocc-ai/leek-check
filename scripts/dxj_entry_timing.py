"""
DXJ Entry Timing Backtest — 找出買 DXJ 的「真 alpha 時機」。

DXJ = WisdomTree Japan Hedged Equity（已 hedge 日圓）。
理論上 DXJ 應該排除日圓貶值帶來的「假 alpha」。

但 memory 提到 DXJ 過去 16 年 +12.33% CAGR vs 0050 +6.4pp/yr，一半來自匯率。
若 DXJ 真有 hedge，仍有 +6pp/yr 真 alpha 來自日股本身。

驗證：
  - 純日股 1306.T: -3.77% CAGR（governance reform 警訊）
  - DXJ 即使 hedge 後仍有？

進場 timing 假設：
  H1. JPY 大幅貶值後 → DXJ 表現好（即使 hedged）
  H2. JPY 大幅升值後 → DXJ 表現好（reversal）
  H3. SPY 大跌後 → DXJ 領先反彈（Japan as risk-on play）
  H4. 純 buy-and-hold 已經贏，timing 沒必要

對每個假設：
  事件後 30/60/90 日 DXJ 報酬 vs DXJ 隨機 30/60/90 日報酬（同 ticker baseline）。
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
GLOBAL = ROOT / "data" / "cache" / "yfinance" / "global"


def load(name):
    df = pd.read_parquet(GLOBAL / f"{name}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def merge_on_date(*dfs, names):
    out = dfs[0][["date", "close"]].rename(columns={"close": names[0]})
    for d, n in zip(dfs[1:], names[1:]):
        out = pd.merge(out, d[["date", "close"]].rename(columns={"close": n}), on="date")
    return out


def fwd_ret(s, days):
    return (s.shift(-days) / s - 1) * 100


def random_baseline(s, hold_days, n_samples=2000, seed=42):
    rng = np.random.default_rng(seed)
    n = len(s) - hold_days
    if n <= 0: return np.array([])
    idx = rng.integers(0, n, n_samples)
    rets = (s.values[idx + hold_days] / s.values[idx] - 1) * 100
    return rets


def test_hypothesis(label, m, signal_idx, hold_days):
    if len(signal_idx) < 5:
        return None
    rets = []
    for i in signal_idx:
        if i + hold_days >= len(m): continue
        ent = m["DXJ"].iloc[i]
        ext = m["DXJ"].iloc[i + hold_days]
        rets.append((ext / ent - 1) * 100)
    if len(rets) < 5: return None
    rets = np.array(rets)
    base = random_baseline(m["DXJ"], hold_days)
    mean = rets.mean()
    base_mean = base.mean()
    base_std = base.std()
    z = (mean - base_mean) / (base_std / np.sqrt(len(rets))) if base_std > 0 else 0
    win = (rets > 0).mean()
    base_win = (base > 0).mean()
    return {
        "label": label,
        "hold": hold_days,
        "n": len(rets),
        "mean": mean,
        "win": win,
        "base_mean": base_mean,
        "base_win": base_win,
        "alpha": mean - base_mean,
        "z": z,
    }


def main():
    print("=" * 90)
    print("🇯🇵 DXJ Entry Timing Backtest（同 ticker random window baseline）")
    print("=" * 90)

    dxj = load("DXJ")
    jpy = load("USDJPY_X")
    spy = load("SPY")

    m = merge_on_date(dxj, jpy, spy, names=["DXJ", "USDJPY", "SPY"])
    print(f"\n樣本: {m['date'].min().date()} → {m['date'].max().date()} ({len(m)} 日)")

    # 計算特徵
    m["jpy_30d"] = m["USDJPY"].pct_change(30) * 100  # 正=貶值
    m["jpy_90d"] = m["USDJPY"].pct_change(90) * 100
    m["spy_30d"] = m["SPY"].pct_change(30) * 100
    m["spy_90d"] = m["SPY"].pct_change(90) * 100
    m["dxj_60ma"] = m["DXJ"].rolling(60).mean()
    m["dxj_above_ma"] = m["DXJ"] > m["dxj_60ma"]

    # Buy-and-hold baseline (DXJ 任意時點)
    print(f"\n📊 Buy-and-hold baseline:")
    for h in [30, 60, 90, 180, 252]:
        base = random_baseline(m["DXJ"], h)
        print(f"  {h:>3}日: mean {base.mean():+5.2f}%  win {(base>0).mean():.0%}  "
              f"std {base.std():4.1f}%")

    print(f"\n{'='*90}")
    print(f"假設測試（DXJ 同 ticker random 為 baseline）")
    print(f"{'='*90}")

    print(f"\n  {'假設':<46} {'hold':>4} {'n':>4} "
          f"{'實際%':>8} {'baseline%':>9} {'alpha':>7} {'z':>6} {'勝率':>5}")
    print(f"  {'-'*46} {'-'*4} {'-'*4} "
          f"{'-'*8} {'-'*9} {'-'*7} {'-'*6} {'-'*5}")

    hypotheses = [
        # (label, mask)
        ("H1a JPY 30日貶 >5%（DXJ 應該贏）",  (m["jpy_30d"] > 5)),
        ("H1b JPY 30日貶 >3%",                (m["jpy_30d"] > 3)),
        ("H2a JPY 30日升 >5%（reversal）",    (m["jpy_30d"] < -5)),
        ("H2b JPY 30日升 >3%",                (m["jpy_30d"] < -3)),
        ("H3a SPY 30日跌 >5%（risk-on）",     (m["spy_30d"] < -5)),
        ("H3b SPY 30日跌 >10%",               (m["spy_30d"] < -10)),
        ("H4 DXJ 突破 60MA",                  (m["DXJ"] > m["dxj_60ma"]) & (m["DXJ"].shift(1) <= m["dxj_60ma"].shift(1))),
        ("H5 JPY 90日累貶 >10%",              (m["jpy_90d"] > 10)),
        ("H6 SPY 90日跌 >10%",                (m["spy_90d"] < -10)),
    ]

    for label, mask in hypotheses:
        idx = np.where(mask & mask.notna())[0]
        for hold in [30, 60, 90]:
            r = test_hypothesis(label, m, idx, hold)
            if r is None: continue
            sig = "⭐⭐⭐" if abs(r["z"]) > 3 else ("⭐⭐" if abs(r["z"]) > 2 else ("⭐" if abs(r["z"]) > 1.5 else "  "))
            print(f"  {label:<46} {r['hold']:>3}d {r['n']:>4} "
                  f"{r['mean']:>+7.2f} {r['base_mean']:>+8.2f} "
                  f"{r['alpha']:>+7.2f} {r['z']:>+6.2f} {r['win']:>4.0%} {sig}")

    print(f"\n{'='*90}")
    print(f"結論判定:")
    print(f"  z > 2.0 = 該 timing 有顯著 alpha vs 隨機進場")
    print(f"  alpha < 0 = 該 timing 反而比隨機差")
    print(f"  若全部 z < 2.0 → DXJ 沒有可靠 timing alpha，純 DCA 即可")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()
