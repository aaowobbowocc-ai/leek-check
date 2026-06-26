"""
Strategy Allocator 歷史回測。

驗證問題：
  1. Regime 分布：8 種狀態各占 % 多少天？是否合理（bear/crash 不能太少）？
  2. 2022 bear 覆蓋：TAIEX 2022 從 18000 → 12700（-29%），allocator 該年是否
     大量觸發 bear/flat_high？
  3. 資金加權報酬：用 plan.core_etf_pct 倉位投入 0050，剩餘現金 0% 利率。
     比較 vs 100% 0050 baseline，驗證「2022 縮倉減損」是否成立。

每日決策邏輯（無 look-ahead）：
  - day t 的 TAIEX 收盤後跑 allocator → 取得 day t+1 的目標倉位 plan
  - day t+1 開盤起套用 plan.core_etf_pct（0050）
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import io  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

from src.risk.strategy_allocator import StrategyAllocator  # noqa: E402

YAML = ROOT / "config" / "strategy.yaml"
TAIEX_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "^TWII.parquet"
TW0050_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "0050.parquet"
OUT_CSV = ROOT / "logs" / "strategy_allocator_backtest.csv"

START = date(2018, 1, 1)
END = date(2026, 4, 24)


def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    taiex = pd.read_parquet(TAIEX_PATH)
    taiex["date"] = pd.to_datetime(taiex["date"]).dt.date
    taiex = taiex.sort_values("date").reset_index(drop=True)

    tw0050 = pd.read_parquet(TW0050_PATH)
    tw0050["date"] = pd.to_datetime(tw0050["date"]).dt.date
    tw0050 = tw0050.sort_values("date").reset_index(drop=True)
    return taiex, tw0050


def run_backtest() -> pd.DataFrame:
    """
    每月第一個交易日重新評估 regime（降低換倉成本，貼近真實使用）。
    """
    taiex, tw0050 = load_data()
    allocator = StrategyAllocator(YAML)

    # 對齊 0050 / TAIEX 交易日
    tw0050 = tw0050[(tw0050["date"] >= START) & (tw0050["date"] <= END)].copy()
    tw0050 = tw0050.reset_index(drop=True)

    rows = []
    # 起始：100% 投資（core+sat），剩餘現金 0
    current_invested_pct = 1.0
    current_regime = "bull_normal"
    current_briefing = "init"
    current_core_pct = 1.0
    current_sat_pct = 0.0
    current_cash_pct = 0.0

    last_eval_month = None

    for i in range(len(tw0050) - 1):
        d = tw0050.loc[i, "date"]
        d_next = tw0050.loc[i + 1, "date"]
        # 月初換倉
        if last_eval_month != d.month:
            taiex_slice = taiex[taiex["date"] <= d].tail(250)
            if len(taiex_slice) >= 100:
                plan = allocator.evaluate(taiex_slice)
                current_core_pct = plan.core_etf_pct
                current_sat_pct = plan.satellite_max_pct
                current_cash_pct = plan.cash_pct
                # 假設 satellite 報酬 ≈ 0050（保守；真實 Early Hunter Trailing 期望值可能略高）
                current_invested_pct = current_core_pct + current_sat_pct
                current_regime = plan.combined_regime
                current_briefing = plan.briefing_note
            last_eval_month = d.month

        # 計算 day t+1 的報酬：(core+sat) 跟 0050、cash 0%
        ret_0050 = tw0050.loc[i + 1, "close"] / tw0050.loc[i, "close"] - 1
        portfolio_ret = current_invested_pct * ret_0050

        rows.append({
            "date": d_next,
            "regime": current_regime,
            "core_pct": current_core_pct,
            "sat_pct": current_sat_pct,
            "cash_pct": current_cash_pct,
            "invested_pct": current_invested_pct,
            "ret_0050": ret_0050,
            "ret_portfolio": portfolio_ret,
            "briefing": current_briefing,
        })

    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> None:
    df["cum_0050"] = (1 + df["ret_0050"]).cumprod()
    df["cum_portfolio"] = (1 + df["ret_portfolio"]).cumprod()
    df["year"] = pd.to_datetime(df["date"]).dt.year

    # ── 1. Regime 分布 ──
    print("\n" + "=" * 60)
    print("1. Regime 分布（總天數）")
    print("=" * 60)
    dist = df["regime"].value_counts(normalize=True).sort_index() * 100
    for k, v in dist.items():
        cnt = (df["regime"] == k).sum()
        print(f"  {k:<14s} {v:6.2f}%   ({cnt} days)")

    # ── 2. 各年份報酬比較 ──
    print("\n" + "=" * 60)
    print("2. 年度報酬比較（0050 vs Allocator）")
    print("=" * 60)
    print(f"  {'年份':<6} {'0050':>10} {'Allocator':>10} {'差距':>10}")
    yearly = df.groupby("year").agg(
        ret_0050=("ret_0050", lambda x: (1 + x).prod() - 1),
        ret_portfolio=("ret_portfolio", lambda x: (1 + x).prod() - 1),
    )
    for year, row in yearly.iterrows():
        delta = (row["ret_portfolio"] - row["ret_0050"]) * 100
        print(
            f"  {year:<6} {row['ret_0050']*100:>9.2f}% "
            f"{row['ret_portfolio']*100:>9.2f}% {delta:>+9.2f}pp"
        )

    # ── 3. 2022 bear 細節 ──
    print("\n" + "=" * 60)
    print("3. 2022 bear 覆蓋率（TAIEX 該年 -22%）")
    print("=" * 60)
    df_2022 = df[df["year"] == 2022]
    if len(df_2022) > 0:
        bear_dist = df_2022["regime"].value_counts(normalize=True) * 100
        for k, v in bear_dist.items():
            cnt = (df_2022["regime"] == k).sum()
            print(f"  {k:<14s} {v:6.2f}%   ({cnt} days)")

    # ── 4. 全期累積 + Sharpe ──
    print("\n" + "=" * 60)
    print("4. 全期績效")
    print("=" * 60)
    total_0050 = df["cum_0050"].iloc[-1] - 1
    total_port = df["cum_portfolio"].iloc[-1] - 1
    n_years = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25

    cagr_0050 = (1 + total_0050) ** (1 / n_years) - 1
    cagr_port = (1 + total_port) ** (1 / n_years) - 1

    sharpe_0050 = df["ret_0050"].mean() / df["ret_0050"].std() * (252 ** 0.5)
    sharpe_port = df["ret_portfolio"].mean() / df["ret_portfolio"].std() * (252 ** 0.5)

    # Max drawdown
    def mdd(cum: pd.Series) -> float:
        peak = cum.cummax()
        dd = (cum - peak) / peak
        return dd.min() * 100

    print(f"  {'指標':<12s} {'0050':>10} {'Allocator':>10}")
    print(f"  {'累積報酬':<12s} {total_0050*100:>9.2f}% {total_port*100:>9.2f}%")
    print(f"  {'CAGR':<12s} {cagr_0050*100:>9.2f}% {cagr_port*100:>9.2f}%")
    print(f"  {'Sharpe':<12s} {sharpe_0050:>10.2f} {sharpe_port:>10.2f}")
    print(f"  {'MDD':<12s} {mdd(df['cum_0050']):>9.2f}% {mdd(df['cum_portfolio']):>9.2f}%")
    print(f"  期間: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]} ({n_years:.1f} 年)")


def main() -> None:
    print(f"Strategy Allocator backtest: {START} ~ {END}")
    print(f"換倉頻率: 每月第一個交易日")
    df = run_backtest()
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n寫入：{OUT_CSV.relative_to(ROOT)}（{len(df)} rows）")
    report(df)


if __name__ == "__main__":
    main()
