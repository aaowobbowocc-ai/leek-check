"""
壓力年份生存檢查 — 計畫 §8.6。

單獨抽出三個「重創散戶」的壓力年份，驗證 black_swan_filter + regime_detector
能讓權益曲線不崩：

    | 年份 | 事件            | 大盤最大跌幅 | 系統目標 MaxDD |
    | 2018 | 美中貿易戰       | −14%        | < 5%          |
    | 2020 | COVID 股災       | −28%        | < 8%          |
    | 2022 | 聯準會暴力升息   | −27%        | < 5%          |

使用方式：
    check = SurvivalCheck(view=view, pipeline_factory=..., cost=..., ...)
    results = check.run()
    for yr, res in results.items():
        print(yr, res.passed, res.metrics)

此模組**不**跑參數優化，純粹用使用者設定的 strategy.yaml 預設權重驗證風控層。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from src.backtest.cost_model import CostConfig
from src.backtest.data_view import HistoricalDataView
from src.backtest.engine import BacktestEngine


@dataclass(frozen=True)
class StressPeriod:
    label: str
    start: date
    end: date                   # exclusive
    max_drawdown_target: float  # 例：2018 → -0.05


# 範圍略往前延伸以涵蓋事件爆發前的交易日
STRESS_PERIODS: list[StressPeriod] = [
    StressPeriod("2018_trade_war", date(2018, 1, 1), date(2019, 1, 1), -0.05),
    StressPeriod("2020_covid", date(2020, 1, 1), date(2021, 1, 1), -0.08),
    StressPeriod("2022_fed_hike", date(2022, 1, 1), date(2023, 1, 1), -0.05),
]


@dataclass(frozen=True)
class SurvivalResult:
    period: StressPeriod
    metrics: dict[str, float]
    passed: bool
    reason: str


def run_survival_check(
    view: HistoricalDataView,
    pipeline_factory: Callable,
    cost: CostConfig,
    trading_calendar: list[date],
    watchlist: list[str],
    ticker_meta: dict[str, dict],
    initial_equity: float = 100_000,
) -> list[SurvivalResult]:
    results: list[SurvivalResult] = []
    for period in STRESS_PERIODS:
        days = [d for d in trading_calendar if period.start <= d < period.end]
        if not days:
            results.append(
                SurvivalResult(
                    period=period,
                    metrics={},
                    passed=False,
                    reason=f"無交易日資料 ({period.start}~{period.end})",
                )
            )
            continue

        engine = BacktestEngine(
            pipeline=pipeline_factory(),
            view=view,
            cost=cost,
            initial_equity=initial_equity,
        )
        rep = engine.run(days, watchlist, ticker_meta)
        dd = rep.metrics.get("max_drawdown_pct", 0.0)
        passed = dd >= period.max_drawdown_target  # dd 為負；越接近 0 越好
        reason = (
            f"MaxDD {dd:.2%} {'≤' if passed else '>'} 目標 {period.max_drawdown_target:.0%}"
        )
        results.append(
            SurvivalResult(period=period, metrics=rep.metrics, passed=passed, reason=reason)
        )
    return results


def format_survival_report(results: list[SurvivalResult]) -> str:
    lines = ["壓力年份生存檢查", "=" * 60]
    for res in results:
        mark = "✅ PASS" if res.passed else "❌ FAIL"
        m = res.metrics
        lines.append(
            f"{mark}  {res.period.label}  "
            f"trades={int(m.get('trades', 0))}  "
            f"MaxDD={m.get('max_drawdown_pct', 0):.2%}  "
            f"return={m.get('total_return_pct', 0):+.2f}%  "
            f"→ {res.reason}"
        )
    return "\n".join(lines)
