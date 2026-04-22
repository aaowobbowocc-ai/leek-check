"""
Walk-Forward 盲測 — 計畫 §8.5。

演算法：
    simulated_today = start
    while simulated_today < end:
        train = [simulated_today − train_months, simulated_today)
        test  = [simulated_today, simulated_today + test_months)
        best_weights = _grid_search(train)                     # 只讀 train 區間
        run BacktestEngine(test, weights=best_weights)         # 嚴格盲測
        simulated_today += test_months

關鍵防線：
    - 訓練與盲測都透過 HistoricalDataView.at(cutoff) 取切片，保證 date < cutoff
    - 訓練階段用「小網格」，避免過擬合（只 4 組預設組合）
    - 權重切換透過 ScoringPipeline.set_base_weights() 覆寫

輸出：WalkForwardReport — 多個 1 個月子區間的績效彙總 + 整體權益曲線。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from src.backtest.cost_model import CostConfig
from src.backtest.data_view import HistoricalDataView
from src.backtest.engine import BacktestEngine, BacktestReport
from src.strategy.scoring_pipeline import ScoringPipeline


# 小網格 — 4 組語意明確的權重組合，避免暴力搜尋產生過擬合
_WEIGHT_GRID: dict[str, dict[str, float]] = {
    "default": {
        "chip_concentration": 0.25,
        "sector_momentum": 0.10,
        "supply_chain": 0.20,
        "news_sentiment": 0.20,
        "technical": 0.15,
        "market_regime": 0.10,
    },
    "chip_heavy": {
        "chip_concentration": 0.40,
        "sector_momentum": 0.10,
        "supply_chain": 0.15,
        "news_sentiment": 0.10,
        "technical": 0.15,
        "market_regime": 0.10,
    },
}
# 註：原本含 technical_heavy / supply_chain_heavy 共 4 組，
# 但歷史 WF 幾乎只在 default 與 chip_heavy 間跳（見 replay_wf_20260420_170356.md），
# 其他兩組從未被選中，移除後 grid search 加速 2×。


@dataclass(frozen=True)
class WindowResult:
    train_start: date
    train_end: date               # exclusive（= simulated_today）
    test_start: date
    test_end: date
    chosen_preset: str
    train_metrics: dict[str, float]
    test_metrics: dict[str, float]


@dataclass
class WalkForwardReport:
    windows: list[WindowResult]
    equity_curve: pd.DataFrame    # 整個 walk-forward 期間拼接起來的盲測權益曲線
    metrics: dict[str, float]     # 整體彙總（不含訓練）

    def summary(self) -> str:
        m = self.metrics
        return (
            f"Windows: {len(self.windows)}  "
            f"Total trades: {int(m.get('trades', 0))}  "
            f"Win%: {m.get('win_rate', 0):.1%}  "
            f"Expectancy: {m.get('expectancy_pct', 0):.2f}%  "
            f"MaxDD: {m.get('max_drawdown_pct', 0):.1%}  "
            f"Sharpe: {m.get('sharpe', 0):.2f}"
        )


def _score_metrics(m: dict[str, float]) -> float:
    """挑最佳權重的目標函數 — expectancy 為主，MaxDD 當負向懲罰。"""
    exp = m.get("expectancy_pct", 0.0)
    dd = m.get("max_drawdown_pct", 0.0)       # 本來就是負值
    # 期望值 + 0.5 × (MaxDD)；MaxDD 越負懲罰越重
    return exp + 0.5 * dd * 100.0


def run_walk_forward(
    view: HistoricalDataView,
    pipeline_factory: Callable[[], ScoringPipeline],
    cost: CostConfig,
    trading_calendar: list[date],
    watchlist: list[str],
    ticker_meta: dict[str, dict],
    start: date,
    end: date,
    train_months: int = 24,
    test_months: int = 1,
    initial_equity: float = 100_000,
    fixed_preset: str | None = None,
) -> WalkForwardReport:
    """
    pipeline_factory: 每個視窗重建 pipeline，避免訓練狀態污染盲測。
    trading_calendar: 全區間可交易日（升冪），walk-forward 會切子序列。
    fixed_preset: 若指定（如 "default"），跳過 grid search，所有視窗都用同一組權重。
                  用於排除訓練階段過擬合雜訊，純驗證策略跨期穩定性。
    """
    windows: list[WindowResult] = []
    test_reports: list[BacktestReport] = []

    simulated_today = _align_forward(trading_calendar, start)

    while simulated_today is not None and simulated_today < end:
        train_start = _shift_months(simulated_today, -train_months)
        test_end = min(end, _shift_months(simulated_today, test_months))

        train_days = [d for d in trading_calendar if train_start <= d < simulated_today]
        test_days = [d for d in trading_calendar if simulated_today <= d < test_end]

        if not train_days or not test_days:
            simulated_today = _next_after(trading_calendar, test_end)
            continue

        # 1. 訓練：在小網格上找最佳權重（只用 train 區間）
        if fixed_preset is not None:
            # 跳過 grid search — 用指定 preset 做純盲測
            best_preset = fixed_preset
            best_train_metrics = {}
        else:
            #    平行跑所有 preset — pandas C-level op 會釋放 GIL，用 threads 即可
            def _train_one(name_weights: tuple[str, dict[str, float]]) -> tuple[str, dict[str, float], float]:
                name, w = name_weights
                pipe = pipeline_factory()
                pipe.set_base_weights(w)
                engine = BacktestEngine(
                    pipeline=pipe, view=view, cost=cost,
                    initial_equity=initial_equity,
                )
                rep = engine.run(train_days, watchlist, ticker_meta)
                return name, rep.metrics, _score_metrics(rep.metrics)

            with ThreadPoolExecutor(max_workers=len(_WEIGHT_GRID)) as ex:
                results = list(ex.map(_train_one, list(_WEIGHT_GRID.items())))
            best_preset, best_train_metrics, _ = max(results, key=lambda r: r[2])

        # 2. 盲測：用選中的權重跑 test 區間
        test_pipe = pipeline_factory()
        test_pipe.set_base_weights(_WEIGHT_GRID[best_preset])
        test_engine = BacktestEngine(
            pipeline=test_pipe, view=view, cost=cost,
            initial_equity=initial_equity,
        )
        test_rep = test_engine.run(test_days, watchlist, ticker_meta)
        test_reports.append(test_rep)

        windows.append(
            WindowResult(
                train_start=train_start,
                train_end=simulated_today,
                test_start=test_days[0],
                test_end=test_days[-1],
                chosen_preset=best_preset,
                train_metrics=best_train_metrics,
                test_metrics=test_rep.metrics,
            )
        )

        # 3. 推進到下一個測試窗
        simulated_today = _next_after(trading_calendar, test_end)

    equity = _stitch_equity(test_reports, initial_equity)
    metrics = _aggregate_metrics(test_reports, equity, initial_equity)
    return WalkForwardReport(windows=windows, equity_curve=equity, metrics=metrics)


# ─────────────────────────────────────────
# 工具
# ─────────────────────────────────────────
def _shift_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    y, m = divmod(total, 12)
    day = min(d.day, 28)  # 避免 2/30 類錯誤
    return date(y, m + 1, day)


def _align_forward(calendar: list[date], target: date) -> date | None:
    for d in calendar:
        if d >= target:
            return d
    return None


def _next_after(calendar: list[date], target: date) -> date | None:
    for d in calendar:
        if d >= target:
            return d
    return None


def _stitch_equity(reports: list[BacktestReport], initial_equity: float) -> pd.DataFrame:
    """把每個盲測窗的權益曲線首尾相接，轉為整段 walk-forward 的 equity curve。"""
    rows: list[dict] = []
    running = initial_equity
    for rep in reports:
        if rep.equity_curve.empty:
            continue
        start_eq = float(rep.equity_curve["equity"].iloc[0])
        for _, r in rep.equity_curve.iterrows():
            scaled = running * float(r["equity"]) / start_eq
            rows.append({"date": r["date"], "equity": scaled})
        if rows:
            running = rows[-1]["equity"]
    return pd.DataFrame(rows, columns=["date", "equity"])


def _aggregate_metrics(
    reports: list[BacktestReport],
    equity: pd.DataFrame,
    initial_equity: float,
) -> dict[str, float]:
    all_trades = [t for rep in reports for t in rep.trades]
    if not all_trades:
        return {"trades": 0, "max_drawdown_pct": 0.0, "sharpe": 0.0, "expectancy_pct": 0.0}

    wins = [t for t in all_trades if t.net_return_pct > 0]
    losses = [t for t in all_trades if t.net_return_pct <= 0]
    win_rate = len(wins) / len(all_trades)
    avg_win = float(np.mean([t.net_return_pct for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([abs(t.net_return_pct) for t in losses])) if losses else 0.0
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    if equity.empty:
        max_dd = 0.0
        sharpe = 0.0
        total_ret = 0.0
    else:
        eq = equity["equity"].astype(float)
        run_max = eq.cummax()
        max_dd = float(((eq - run_max) / run_max).min())
        pct = eq.pct_change().dropna()
        sharpe = float(pct.mean() / pct.std() * np.sqrt(252)) if len(pct) > 20 and pct.std() > 0 else 0.0
        total_ret = float(eq.iloc[-1] / initial_equity - 1.0) * 100.0

    return {
        "trades": float(len(all_trades)),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "pl_ratio": round(pl_ratio, 3),
        "expectancy_pct": round(expectancy, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe": round(sharpe, 3),
        "total_return_pct": round(total_ret, 2),
    }
