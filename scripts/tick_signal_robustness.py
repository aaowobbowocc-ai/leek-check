"""
Multi-ticker Tick 訊號 Robustness 驗證。

對 6 個 ticker（2330 + 5 補抓）跑相同 metric → next-day return 分析。

驗收：
  - close_vs_vwap_pct lift > -15pp 在至少 4/6 ticker → robust
  - inner_ratio quartile lift > +5pp 在至少 4/6 ticker → robust
  - 任一 metric 在 5+ ticker 都顯著 → 高度 robust
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

from src.strategy.tick_microstructure import rolling_daily_metrics  # noqa: E402

TICKERS = ["2330", "3231", "2382", "8046", "3037", "3017"]
START = date(2025, 4, 1)
END = date(2026, 4, 25)

METRICS = [
    "inner_ratio",
    "io_ratio",
    "morning_inner_ratio",
    "closing_inner_ratio",
    "close_vs_vwap_pct",
    "big_ratio",
]


def analyze_ticker(ticker: str) -> dict | None:
    df = rolling_daily_metrics(ticker, START, END)
    if df.empty:
        return None
    df = df.sort_values("date").reset_index(drop=True)
    df["next_close"] = df["close"].shift(-1)
    df["next_ret"] = (df["next_close"] / df["close"] - 1) * 100

    result = {"ticker": ticker, "n_days": len(df)}
    for m in METRICS:
        valid = df[[m, "next_ret"]].dropna()
        if len(valid) < 30:
            continue
        spear = valid[m].rank().corr(valid["next_ret"].rank())
        q1, q3 = valid[m].quantile([0.25, 0.75]).values
        bot = valid[valid[m] <= q1]
        top = valid[valid[m] >= q3]
        bot_win = (bot["next_ret"] > 0).mean() * 100 if len(bot) else 0
        top_win = (top["next_ret"] > 0).mean() * 100 if len(top) else 0
        result[f"{m}_spear"] = round(spear, 3)
        result[f"{m}_lift"] = round(top_win - bot_win, 1)
    return result


def main() -> None:
    print("=" * 100)
    print(f"Multi-ticker Tick Signal Robustness（{len(TICKERS)} tickers, {START} ~ {END}）")
    print("=" * 100)

    rows = []
    for tk in TICKERS:
        r = analyze_ticker(tk)
        if r is not None:
            rows.append(r)
            print(f"  ✅ {tk}: {r['n_days']} days analyzed")
        else:
            print(f"  ❌ {tk}: 無資料")

    if not rows:
        return

    df = pd.DataFrame(rows)

    # 對每個 metric 看 6 ticker 的 lift 一致性
    print("\n" + "=" * 100)
    print("各 metric 在不同 ticker 的 lift（top quartile - bot quartile win rate, pp）")
    print("=" * 100)
    print(f"  {'metric':<28}", end="")
    for tk in TICKERS:
        print(f" {tk:>7}", end="")
    print(f" {'mean':>7} {'positive_count':>15}")

    for m in METRICS:
        col = f"{m}_lift"
        if col not in df.columns:
            continue
        values = []
        line = f"  {m:<28}"
        for tk in TICKERS:
            row = df[df["ticker"] == tk]
            if row.empty or pd.isna(row.iloc[0].get(col)):
                line += f" {'-':>7}"
            else:
                v = row.iloc[0][col]
                values.append(v)
                line += f" {v:>+6.1f}"
        if values:
            mean = sum(values) / len(values)
            pos = sum(1 for v in values if v > 5)  # lift > +5pp 算 positive
            neg = sum(1 for v in values if v < -5)
            line += f" {mean:>+6.1f}"
            sig = ""
            if pos >= 4:
                sig = " 🟢 robust+"
            elif neg >= 4:
                sig = " 🔴 robust-"
            elif pos + neg <= 1:
                sig = " ⚪ noise"
            line += f"   {pos}+ / {neg}-{sig}"
        print(line)

    # Spearman correlation 同樣比對
    print("\n  Spearman 相關性:")
    print(f"  {'metric':<28}", end="")
    for tk in TICKERS:
        print(f" {tk:>7}", end="")
    print()
    for m in METRICS:
        col = f"{m}_spear"
        if col not in df.columns:
            continue
        line = f"  {m:<28}"
        for tk in TICKERS:
            row = df[df["ticker"] == tk]
            if row.empty or pd.isna(row.iloc[0].get(col)):
                line += f" {'-':>7}"
            else:
                v = row.iloc[0][col]
                line += f" {v:>+7.3f}"
        print(line)

    out = ROOT / "logs" / "tick_signal_robustness.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
