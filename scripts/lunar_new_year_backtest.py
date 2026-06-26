"""農曆年效應 backtest — 封關前 5 日 vs 開紅盤後 10 日。

使用 0050 (TAIEX 0.99 相關) 16 年歷史。

進場規則 candidates:
  A. 封關前 5 個交易日進場 → 封關當天平倉(跨年防禦?)
  B. 封關前 5 個交易日進場 → 開紅盤當天平倉(年前佈局)
  C. 開紅盤後 10 個交易日進場 → 第 11 天平倉(紅盤反彈?)
  D. 封關前 1 天進場 → 開紅盤當天平倉(年假跨年 gap)

成本:0050 ETF 0.34% per round-trip (含 0.5x tax 折扣)
"""
from __future__ import annotations
import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[1]

# 農曆春節「年假封關第一天」(approximate based on TW history)
# Source: TWSE 歷年休市公告
CNY_CLOSE_FIRST_DAY = {
    2010: date(2010, 2, 11),  # 封關前最後交易日 = 2/10
    2011: date(2011, 2, 1),   # 1/31
    2012: date(2012, 1, 20),  # 1/19 last
    2013: date(2013, 2, 7),
    2014: date(2014, 1, 28),
    2015: date(2015, 2, 17),
    2016: date(2016, 2, 5),
    2017: date(2017, 1, 25),
    2018: date(2018, 2, 13),
    2019: date(2019, 2, 1),
    2020: date(2020, 1, 21),
    2021: date(2021, 2, 8),
    2022: date(2022, 1, 27),
    2023: date(2023, 1, 18),
    2024: date(2024, 2, 6),
    2025: date(2025, 1, 24),
}


def load_data() -> pd.DataFrame:
    df = pd.read_parquet(ROOT / "data/cache/yfinance/global/0050_TW.parquet")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def find_close_idx(df: pd.DataFrame, target: date) -> int | None:
    """找封關前最後一個交易日(target 是封關第一天)."""
    mask = df["date"] < target
    if not mask.any():
        return None
    return int(df.index[mask].max())


def find_reopen_idx(df: pd.DataFrame, close_idx: int) -> int | None:
    """封關後第一個交易日(下一個 trading day in df after close_idx)."""
    if close_idx + 1 < len(df):
        return close_idx + 1
    return None


def compute_strategy_returns(df: pd.DataFrame) -> pd.DataFrame:
    """每年計算 4 種策略的 raw return."""
    rows = []
    for year, cny_first in CNY_CLOSE_FIRST_DAY.items():
        close_idx = find_close_idx(df, cny_first)
        if close_idx is None or close_idx < 5:
            continue
        reopen_idx = find_reopen_idx(df, close_idx)
        if reopen_idx is None or reopen_idx + 10 >= len(df):
            continue

        # closes
        c_minus5 = df["close"].iloc[close_idx - 4]   # 5 個交易日前(包含當天)
        c_close = df["close"].iloc[close_idx]         # 封關當天
        c_reopen = df["close"].iloc[reopen_idx]       # 開紅盤
        c_plus10 = df["close"].iloc[reopen_idx + 9]   # 開紅盤後第 10 天

        # A: 封關前 5d 進場 → 封關平倉
        ret_A = (c_close / c_minus5 - 1) * 100
        # B: 封關前 5d 進場 → 開紅盤平倉
        ret_B = (c_reopen / c_minus5 - 1) * 100
        # C: 開紅盤後 10d 持有
        ret_C = (c_plus10 / c_reopen - 1) * 100
        # D: 封關當天進場 → 開紅盤平倉(過年假 gap)
        ret_D = (c_reopen / c_close - 1) * 100
        # E: 封關前 5d 進場 → 開紅盤後 10d 平倉(完整 trade)
        ret_E = (c_plus10 / c_minus5 - 1) * 100

        rows.append({
            "year": year,
            "close_date": df["date"].iloc[close_idx],
            "reopen_date": df["date"].iloc[reopen_idx],
            "A_pre5_to_close": ret_A,
            "B_pre5_to_reopen": ret_B,
            "C_post10": ret_C,
            "D_gap": ret_D,
            "E_full": ret_E,
        })
    return pd.DataFrame(rows)


def stats(arr: pd.Series, cost_pct: float = 0.34) -> dict:
    n = len(arr)
    mean = arr.mean()
    net = mean - cost_pct
    std = arr.std(ddof=1)
    t = (mean / (std / np.sqrt(n))) if std > 0 and n > 1 else 0.0
    win_rate = (arr > 0).mean() * 100
    return {
        "n": n,
        "mean_gross": round(mean, 3),
        "mean_net": round(net, 3),
        "std": round(std, 3),
        "t_stat": round(t, 2),
        "win_rate": round(win_rate, 1),
        "median": round(arr.median(), 3),
        "min": round(arr.min(), 3),
        "max": round(arr.max(), 3),
    }


def main():
    df = load_data()
    print(f"0050 載入 {len(df)} rows ({df['date'].min()} ~ {df['date'].max()})")

    results = compute_strategy_returns(df)
    print(f"\n年度 raw returns (n={len(results)} 年):")
    print(results.to_string(index=False))

    print("\n" + "=" * 80)
    print("Strategy summary (cost: 0050 ETF round-trip 0.34%)")
    print("=" * 80)
    strategies = {
        "A. 封關前 5d → 封關平倉":     "A_pre5_to_close",
        "B. 封關前 5d → 開紅盤平倉":   "B_pre5_to_reopen",
        "C. 開紅盤後 10d 持有":         "C_post10",
        "D. 封關當天 → 開紅盤(gap)":   "D_gap",
        "E. 封關前 5d → 紅盤+10d":     "E_full",
    }
    for label, col in strategies.items():
        s = stats(results[col])
        verdict = "🟢 PASS" if s["t_stat"] > 2.0 and s["mean_net"] > 0.3 else (
                  "🟡 EDGE" if s["t_stat"] > 1.5 and s["mean_net"] > 0 else "🔴 FAIL")
        print(f"\n{label}")
        print(f"  n={s['n']} mean_gross={s['mean_gross']:+.2f}% "
              f"net={s['mean_net']:+.2f}% std={s['std']:.2f}% "
              f"t={s['t_stat']:+.2f} win={s['win_rate']:.0f}% {verdict}")
        print(f"  median={s['median']:+.2f}% range=[{s['min']:+.2f}, {s['max']:+.2f}]")

    # Save raw
    out = ROOT / "logs" / "lunar_new_year_backtest.csv"
    results.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
