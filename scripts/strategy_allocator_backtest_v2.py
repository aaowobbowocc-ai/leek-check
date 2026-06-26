"""
Strategy Allocator backtest v2 — option 3：A + B 合併驗收。

加入「真實 satellite alpha」：
  - 用 logs/early_hunter_trailing_v2.csv 的 96 筆 trades
  - 每筆 trade 線性攤銷 daily return（保守，把波動平均化）
  - 當天 active trades 等權重 → satellite daily return
  - 沒 active trade 時 satellite = 0%（cash）

公式：
  ret = core_pct × ret_0050 + sat_pct × ret_satellite + cash_pct × 0

驗收標準：
  - CAGR ≥ 0050（贏或打平）
  - Sharpe > 0050 + 0.05
  - MDD 改善 ≥ 5pp
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

from src.risk.strategy_allocator import StrategyAllocator  # noqa: E402

YAML = ROOT / "config" / "strategy.yaml"
TAIEX_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "^TWII.parquet"
TW0050_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "0050.parquet"
EH_TRADES = ROOT / "logs" / "early_hunter_trailing_v2.csv"
OUT_CSV = ROOT / "logs" / "strategy_allocator_backtest_v2.csv"

START = date(2019, 1, 1)
END = date(2026, 4, 24)


def build_satellite_daily_returns(trading_days: list[date]) -> pd.Series:
    """從 96 筆 EH Trailing trades 構造 satellite daily return series。"""
    trades = pd.read_csv(EH_TRADES)
    trades["entry_date"] = pd.to_datetime(trades["entry_date"]).dt.date
    trades["exit_date"] = pd.to_datetime(trades["exit_date"]).dt.date

    # 對每筆 trade，把 gross_return_pct 線性攤到 hold 期間
    # daily_ret = (1 + ret_pct/100)^(1/hold_days) - 1
    daily_returns_by_date: dict[date, list[float]] = {d: [] for d in trading_days}

    for _, t in trades.iterrows():
        if t["hold_days"] <= 0:
            continue
        per_day = (1 + t["gross_return_pct"] / 100) ** (1 / t["hold_days"]) - 1
        d = t["entry_date"]
        while d <= t["exit_date"]:
            if d in daily_returns_by_date:
                daily_returns_by_date[d].append(per_day)
            d += timedelta(days=1)

    sat_ret = []
    for d in trading_days:
        rs = daily_returns_by_date[d]
        if rs:
            sat_ret.append(sum(rs) / len(rs))   # 等權重
        else:
            sat_ret.append(0.0)
    return pd.Series(sat_ret, index=trading_days)


def run_backtest() -> pd.DataFrame:
    taiex = pd.read_parquet(TAIEX_PATH)
    taiex["date"] = pd.to_datetime(taiex["date"]).dt.date
    taiex = taiex.sort_values("date").reset_index(drop=True)

    tw0050 = pd.read_parquet(TW0050_PATH)
    tw0050["date"] = pd.to_datetime(tw0050["date"]).dt.date
    tw0050 = tw0050[(tw0050["date"] >= START) & (tw0050["date"] <= END)].reset_index(drop=True)

    trading_days = list(tw0050["date"])
    sat_series = build_satellite_daily_returns(trading_days)

    allocator = StrategyAllocator(YAML)
    rows = []
    cur_core = cur_sat = cur_cash = 0.0
    cur_eh_active = cur_va_active = False
    cur_regime = "init"
    cur_briefing = ""
    last_eval_month = None

    for i in range(len(tw0050) - 1):
        d = tw0050.loc[i, "date"]
        d_next = tw0050.loc[i + 1, "date"]

        if last_eval_month != d.month:
            taiex_slice = taiex[taiex["date"] <= d].tail(250)
            if len(taiex_slice) >= 100:
                plan = allocator.evaluate(taiex_slice)
                cur_core = plan.core_etf_pct
                cur_sat = plan.satellite_max_pct
                cur_cash = plan.cash_pct
                cur_eh_active = plan.early_hunter_active
                cur_va_active = plan.vol_anomaly_active
                cur_regime = plan.combined_regime
                cur_briefing = plan.briefing_note
            last_eval_month = d.month

        ret_0050 = tw0050.loc[i + 1, "close"] / tw0050.loc[i, "close"] - 1
        # satellite 報酬：只有 EH 開啟時才吃 satellite series
        ret_sat = sat_series.loc[d_next] if cur_eh_active else 0.0
        # EH 關閉時 satellite 那部分閒置（cash）→ 加進 cash_pct
        if not cur_eh_active:
            sat_to_cash = cur_sat
            invested_core = cur_core
            invested_sat = 0.0
            cash_total = cur_cash + sat_to_cash
        else:
            invested_core = cur_core
            invested_sat = cur_sat
            cash_total = cur_cash

        portfolio_ret = invested_core * ret_0050 + invested_sat * ret_sat

        rows.append({
            "date": d_next,
            "regime": cur_regime,
            "core_pct": invested_core,
            "sat_pct": invested_sat,
            "cash_pct": cash_total,
            "eh_active": cur_eh_active,
            "ret_0050": ret_0050,
            "ret_sat": ret_sat,
            "ret_portfolio": portfolio_ret,
        })

    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> None:
    df["cum_0050"] = (1 + df["ret_0050"]).cumprod()
    df["cum_portfolio"] = (1 + df["ret_portfolio"]).cumprod()
    df["year"] = pd.to_datetime(df["date"]).dt.year

    print("\n" + "=" * 60)
    print("年度報酬比較")
    print("=" * 60)
    print(f"  {'年':<6} {'0050':>10} {'Allocator+EH':>13} {'差距':>10}")
    yearly = df.groupby("year").agg(
        ret_0050=("ret_0050", lambda x: (1 + x).prod() - 1),
        ret_portfolio=("ret_portfolio", lambda x: (1 + x).prod() - 1),
    )
    for y, row in yearly.iterrows():
        delta = (row["ret_portfolio"] - row["ret_0050"]) * 100
        print(
            f"  {y:<6} {row['ret_0050']*100:>9.2f}% "
            f"{row['ret_portfolio']*100:>12.2f}% {delta:>+9.2f}pp"
        )

    total_0050 = df["cum_0050"].iloc[-1] - 1
    total_port = df["cum_portfolio"].iloc[-1] - 1
    n_years = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    cagr_0050 = (1 + total_0050) ** (1 / n_years) - 1
    cagr_port = (1 + total_port) ** (1 / n_years) - 1
    sharpe_0050 = df["ret_0050"].mean() / df["ret_0050"].std() * (252 ** 0.5)
    sharpe_port = df["ret_portfolio"].mean() / df["ret_portfolio"].std() * (252 ** 0.5)

    def mdd(c: pd.Series) -> float:
        peak = c.cummax()
        return ((c - peak) / peak).min() * 100

    print("\n" + "=" * 60)
    print("全期績效（驗收標準：CAGR≥0050 & Sharpe+0.05 & MDD−5pp）")
    print("=" * 60)
    print(f"  {'指標':<12s} {'0050':>10} {'Allocator+EH':>13} {'差距':>10}")
    print(f"  {'累積報酬':<12s} {total_0050*100:>9.2f}% {total_port*100:>12.2f}% "
          f"{(total_port-total_0050)*100:>+9.2f}pp")
    print(f"  {'CAGR':<12s} {cagr_0050*100:>9.2f}% {cagr_port*100:>12.2f}% "
          f"{(cagr_port-cagr_0050)*100:>+9.2f}pp")
    print(f"  {'Sharpe':<12s} {sharpe_0050:>10.2f} {sharpe_port:>13.2f} "
          f"{sharpe_port-sharpe_0050:>+9.2f}")
    print(f"  {'MDD':<12s} {mdd(df['cum_0050']):>9.2f}% {mdd(df['cum_portfolio']):>12.2f}% "
          f"{mdd(df['cum_portfolio'])-mdd(df['cum_0050']):>+9.2f}pp")
    print(f"  期間: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]} ({n_years:.1f} 年)")

    # 驗收
    print("\n" + "=" * 60)
    print("驗收結果")
    print("=" * 60)
    pass_cagr = cagr_port >= cagr_0050
    pass_sharpe = sharpe_port >= sharpe_0050 + 0.05
    pass_mdd = mdd(df["cum_portfolio"]) - mdd(df["cum_0050"]) >= 5.0
    print(f"  CAGR ≥ 0050        : {'✅' if pass_cagr else '❌'}")
    print(f"  Sharpe + 0.05      : {'✅' if pass_sharpe else '❌'}")
    print(f"  MDD 改善 ≥ 5pp     : {'✅' if pass_mdd else '❌'}")
    print(f"  總結: {'✅ 通過' if all([pass_cagr, pass_sharpe, pass_mdd]) else '❌ 沒過'}")


def main() -> None:
    print(f"Strategy Allocator + EH Trailing backtest: {START} ~ {END}")
    df = run_backtest()
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"寫入：{OUT_CSV.relative_to(ROOT)}（{len(df)} rows）")
    report(df)


if __name__ == "__main__":
    main()
