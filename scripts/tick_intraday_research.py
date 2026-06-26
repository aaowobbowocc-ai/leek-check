"""
Intraday Tick Pattern 研究 — 5 分鐘 rolling 內盤比 z-score 突發。

Hypothesis：
  日內某時刻 5 分內盤比突然飆升（z-score > 2）=「主力倒貨」訊號 → 隔日跌

Quick test 設計：
  1. 對每日 tick 聚合成 5-min bars (volume, inner_vol, outer_vol)
  2. 每個 5-min bar 計算 inner_ratio
  3. 對該 bar 算 30 分 rolling z-score
  4. 看「日內最大 z-score」是否預測次日報酬

驗收：
  - 6 ticker 中至少 4 個 lift > +10pp → 進 step B（整合進策略）
  - 否則承認 tick daily/intraday signals 都不夠 robust → 走路 C 收手
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

TICK_CACHE = ROOT / "data" / "cache" / "finmind" / "tick"

TICKERS = ["2330", "3231", "2382", "8046", "3037", "3017"]
START = date(2025, 4, 1)
END = date(2026, 4, 25)


def load_tick_day(ticker: str, d: date) -> pd.DataFrame:
    cp = TICK_CACHE / f"{ticker}_{d.strftime('%Y%m%d')}.parquet"
    if not cp.exists():
        return pd.DataFrame()
    df = pd.read_parquet(cp)
    if "_empty" in df.columns:
        return pd.DataFrame()
    df["TickType"] = pd.to_numeric(df["TickType"], errors="coerce").fillna(0).astype(int)
    df["deal_price"] = pd.to_numeric(df["deal_price"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
    # parse Time to datetime
    df["dt"] = pd.to_datetime(d.isoformat() + " " + df["Time"].astype(str).str[:8])
    return df


def compute_intraday_features(ticker: str, d: date) -> dict:
    """單日 intraday 特徵：5-min bars + rolling z-score 突發。"""
    df = load_tick_day(ticker, d)
    if df.empty or len(df) < 30:
        return {}

    df = df.sort_values("dt").reset_index(drop=True)
    df.set_index("dt", inplace=True)

    # 5-min bars
    bar = df.resample("5min").agg(
        total_vol=("volume", "sum"),
        outer_vol=("volume", lambda v: df.loc[v.index, "volume"][df.loc[v.index, "TickType"] == 1].sum()),
        inner_vol=("volume", lambda v: df.loc[v.index, "volume"][df.loc[v.index, "TickType"] == 2].sum()),
        close=("deal_price", "last"),
        high=("deal_price", "max"),
        low=("deal_price", "min"),
    )
    # 限定 09:00-13:30 trading hours
    bar = bar.between_time("09:00", "13:30").copy()
    bar = bar[bar["total_vol"] > 0]
    if len(bar) < 10:
        return {}

    bar["inner_ratio"] = bar["inner_vol"] / (bar["inner_vol"] + bar["outer_vol"])
    bar["inner_ratio"] = bar["inner_ratio"].fillna(0.5)

    # 30-min rolling (6 bars) z-score
    bar["roll_mean"] = bar["inner_ratio"].rolling(6, min_periods=3).mean()
    bar["roll_std"] = bar["inner_ratio"].rolling(6, min_periods=3).std()
    bar["z_score"] = (bar["inner_ratio"] - bar["roll_mean"]) / bar["roll_std"].replace(0, 1)

    # 重要 features
    max_z = float(bar["z_score"].max()) if not bar["z_score"].isna().all() else 0.0
    min_z = float(bar["z_score"].min()) if not bar["z_score"].isna().all() else 0.0
    n_z_above_2 = int((bar["z_score"] > 2).sum())
    n_z_above_3 = int((bar["z_score"] > 3).sum())
    max_inner_ratio = float(bar["inner_ratio"].max())

    # 大量 5min bar 的內盤比（5min vol > 過去 6 bar 均量 × 2）
    bar["roll_vol_mean"] = bar["total_vol"].rolling(6, min_periods=3).mean()
    bar["vol_burst"] = bar["total_vol"] / bar["roll_vol_mean"].replace(0, 1)
    bar["is_burst"] = bar["vol_burst"] > 2
    burst_inner_avg = float(bar[bar["is_burst"]]["inner_ratio"].mean()) if bar["is_burst"].any() else 0.5
    n_bursts = int(bar["is_burst"].sum())

    # 收盤價
    close_price = float(bar["close"].iloc[-1])

    return {
        "ticker": ticker,
        "date": d,
        "close": close_price,
        "max_z": max_z,             # 日內最大 inner_ratio z-score
        "min_z": min_z,
        "n_z_above_2": n_z_above_2,  # z > 2 的 5min bar 數
        "n_z_above_3": n_z_above_3,
        "max_inner_ratio": max_inner_ratio,
        "burst_inner_avg": burst_inner_avg,  # 量爆時的內盤比平均
        "n_bursts": n_bursts,
    }


def analyze_ticker(ticker: str) -> pd.DataFrame:
    rows = []
    cur = START
    while cur <= END:
        if cur.weekday() < 5:
            f = compute_intraday_features(ticker, cur)
            if f:
                rows.append(f)
        cur += timedelta(days=1)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    df["next_close"] = df["close"].shift(-1)
    df["next_ret"] = (df["next_close"] / df["close"] - 1) * 100
    return df


def main() -> None:
    print("=" * 100)
    print("Intraday Tick Pattern Robustness（5-min inner_ratio z-score）")
    print("=" * 100)

    results = {}
    for tk in TICKERS:
        df = analyze_ticker(tk)
        if df.empty:
            print(f"  ❌ {tk}")
            continue
        results[tk] = df
        print(f"  ✅ {tk}: {len(df)} days")

    # 各 metric 跨 ticker 一致性
    metrics = ["max_z", "n_z_above_2", "n_z_above_3", "max_inner_ratio",
               "burst_inner_avg", "n_bursts"]

    print("\n" + "=" * 100)
    print("各 metric × ticker 的 lift（top quartile - bot quartile next-day win%）")
    print("=" * 100)
    print(f"  {'metric':<22}", end="")
    for tk in TICKERS:
        print(f" {tk:>7}", end="")
    print(f" {'mean':>7} {'pos/neg':>10}")

    for m in metrics:
        line = f"  {m:<22}"
        values = []
        for tk in TICKERS:
            df = results.get(tk, pd.DataFrame())
            if df.empty:
                line += f" {'-':>7}"
                continue
            valid = df[[m, "next_ret"]].dropna()
            if len(valid) < 30:
                line += f" {'-':>7}"
                continue
            q1, q3 = valid[m].quantile([0.25, 0.75]).values
            bot = valid[valid[m] <= q1]
            top = valid[valid[m] >= q3]
            if len(bot) == 0 or len(top) == 0:
                line += f" {'-':>7}"
                continue
            lift = ((top["next_ret"] > 0).mean() - (bot["next_ret"] > 0).mean()) * 100
            values.append(lift)
            line += f" {lift:>+6.1f}"
        if values:
            mean = sum(values) / len(values)
            pos = sum(1 for v in values if v > 5)
            neg = sum(1 for v in values if v < -5)
            line += f" {mean:>+6.1f}"
            sig = ""
            if pos >= 4:
                sig = " 🟢 robust+"
            elif neg >= 4:
                sig = " 🔴 robust-"
            line += f"   {pos}+/{neg}-{sig}"
        print(line)

    # Spearman
    print("\n  Spearman 相關性（vs next_ret）:")
    print(f"  {'metric':<22}", end="")
    for tk in TICKERS:
        print(f" {tk:>7}", end="")
    print()
    for m in metrics:
        line = f"  {m:<22}"
        for tk in TICKERS:
            df = results.get(tk, pd.DataFrame())
            if df.empty:
                line += f" {'-':>7}"
                continue
            valid = df[[m, "next_ret"]].dropna()
            if len(valid) < 30:
                line += f" {'-':>7}"
                continue
            spear = valid[m].rank().corr(valid["next_ret"].rank())
            line += f" {spear:>+7.3f}"
        print(line)

    # 寫出
    out = ROOT / "logs" / "tick_intraday_robustness.csv"
    summary = []
    for tk, df in results.items():
        for _, r in df.iterrows():
            summary.append({"ticker": tk, **r.to_dict()})
    pd.DataFrame(summary).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
