"""
回測引擎骨架 — 計畫 §8.1（每日模擬流程）+ §8.2（輸出指標）。

主迴圈（對 [start, end] 內的每個交易日 D）：
  1. snapshot = view.at(D)   # 嚴格 < D 的資料
  2. pipeline.run(snapshot) → 推薦名單
  3. 對持倉中的 open positions 用 D 日 high/low 判斷止損止盈（保守：同日同觸發先觸發止損）
  4. 對推薦名單模擬進場（若 D 日盤中 [low, high] ∩ [entry_low, entry_high] 非空，以區間中價成交）
  5. 扣 cost → 寫入交易簿

禁止事項：
  - 任何地方直接讀 df.loc[df["date"] == D] 供「打分」使用
  - 所有打分資料一律走 snapshot.*()
  - 只有「模擬成交 / 判斷止損」才能用 view.bar(ticker, D) 取當日 OHLCV

輸出：BacktestReport — 含交易簿、權益曲線、關鍵指標。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest.cost_model import CostConfig, TradeResult, simulate_fill
from src.backtest.data_view import HistoricalDataView
from src.data.adr_fetcher import OvernightReport
from src.risk.atr_stops import StopState, exit_signal, trail
from src.risk.position_sizing import SizingInput, size_position
from src.strategy.composite_scorer import Recommendation
from src.strategy.scoring_pipeline import (
    PipelineInput,
    ScoringPipeline,
    TickerInputs,
)


@dataclass(frozen=True)
class ExecutedTrade:
    ticker: str
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    shares: int
    exit_reason: str             # "stop" | "target" | "timeout"
    gross_return_pct: float
    net_return_pct: float
    pnl: float


@dataclass
class OpenPosition:
    ticker: str
    entry_date: date
    entry_price: float
    shares: int
    stop_state: StopState
    max_hold_days: int = 20      # 超時出場（避免死抱）


@dataclass
class BacktestReport:
    trades: list[ExecutedTrade]
    equity_curve: pd.DataFrame              # date | equity
    metrics: dict[str, float]

    def summary(self) -> str:
        m = self.metrics
        return (
            f"Trades: {len(self.trades)}  "
            f"Win%: {m.get('win_rate', 0):.1%}  "
            f"PL Ratio: {m.get('pl_ratio', 0):.2f}  "
            f"Expectancy: {m.get('expectancy_pct', 0):.2f}%  "
            f"MaxDD: {m.get('max_drawdown_pct', 0):.1%}  "
            f"Sharpe: {m.get('sharpe', 0):.2f}"
        )


class BacktestEngine:
    def __init__(
        self,
        pipeline: ScoringPipeline,
        view: HistoricalDataView,
        cost: CostConfig,
        initial_equity: float = 100_000,
        win_rate_prior: float = 0.55,
        avg_win_prior: float = 0.08,
        avg_loss_prior: float = 0.04,
        max_hold_days: int = 20,
        k_stop: float = 1.5,
        k_target: float = 4.0,
        chandelier_k: float = 2.0,
    ) -> None:
        self._pipeline = pipeline
        self._view = view
        self._cost = cost
        self._equity = initial_equity
        self._initial_equity = initial_equity
        self._open_positions: list[OpenPosition] = []
        self._trades: list[ExecutedTrade] = []
        self._equity_series: list[tuple[date, float]] = []
        self._win_rate = win_rate_prior
        self._avg_win = avg_win_prior
        self._avg_loss = avg_loss_prior
        self._max_hold_days = max_hold_days
        self._k_stop = k_stop
        self._k_target = k_target
        self._chandelier_k = chandelier_k

    def run(
        self,
        trading_days: list[date],
        watchlist: list[str],
        ticker_meta: dict[str, dict],
    ) -> BacktestReport:
        """
        trading_days: 回測期間的交易日列表（升冪）
        ticker_meta:  {ticker: {"company_name": str, "shares_outstanding": int}}
        """
        for d in trading_days:
            self._process_day(d, watchlist, ticker_meta)
            self._equity_series.append((d, self._mark_to_market(d)))

        # 結算尚未平倉的部位（以最後一日收盤出場）
        if trading_days:
            self._force_close_all(trading_days[-1])

        equity_df = pd.DataFrame(self._equity_series, columns=["date", "equity"])
        metrics = self._compute_metrics(equity_df)
        return BacktestReport(trades=list(self._trades), equity_curve=equity_df, metrics=metrics)

    # ─────────────────────────────────────
    # 每日流程
    # ─────────────────────────────────────
    def _process_day(
        self,
        d: date,
        watchlist: list[str],
        ticker_meta: dict[str, dict],
    ) -> None:
        # 1. 先處理出場（用當日 bar）
        self._process_exits(d)

        # 2. 跑 pipeline（所有資料嚴格 < d）
        snapshot = self._view.at(d)
        ticker_inputs: list[TickerInputs] = []
        for tk in watchlist:
            if self._has_position(tk):
                continue  # 已持倉就不再進場
            meta = ticker_meta.get(tk, {})
            ohlcv_hist = snapshot.ohlcv(tk)
            if ohlcv_hist.empty:
                continue
            last_date = pd.to_datetime(ohlcv_hist["date"]).dt.date.max()
            recent_volume = int(
                ohlcv_hist.loc[pd.to_datetime(ohlcv_hist["date"]).dt.date == last_date, "volume"].sum()
            )
            ticker_inputs.append(
                TickerInputs(
                    ticker=tk,
                    company_name=meta.get("company_name", tk),
                    ohlcv=ohlcv_hist,
                    institutional=snapshot.institutional(tk),
                    broker=snapshot.broker_on(tk, last_date),
                    shares_outstanding=int(meta.get("shares_outstanding", 1_000_000_000)),
                    recent_volume=recent_volume,
                    news=[],
                    sentiment=None,  # 回測中情緒因子在 Phase 8d 加入
                    concentration=snapshot.concentration(tk),
                    margin=snapshot.margin(tk),
                    pbr=snapshot.pbr(tk),
                )
            )

        if not ticker_inputs:
            return

        pipe_out = self._pipeline.run(
            PipelineInput(
                as_of_date=d,
                tickers=ticker_inputs,
                taiex_daily=snapshot.taiex_window(),
                overnight=_cast_overnight(snapshot.overnight),
            )
        )

        if pipe_out.defensive:
            return

        # 3. 嘗試進場（bear/sideways 用 trend.position_scale 縮部位）
        for reco in pipe_out.recommendations:
            self._try_enter(d, reco, position_scale=pipe_out.position_scale)

    def _process_exits(self, d: date) -> None:
        still_open: list[OpenPosition] = []
        for pos in self._open_positions:
            bar = self._view.bar(pos.ticker, d)
            if bar is None:
                still_open.append(pos)
                continue

            reason = exit_signal(pos.stop_state, bar["low"], bar["high"])
            hold_days = (d - pos.entry_date).days

            if reason:
                exit_px = pos.stop_state.stop if reason == "stop" else pos.stop_state.target
                self._close_position(pos, d, exit_px, reason)
            elif hold_days >= self._max_hold_days:
                self._close_position(pos, d, bar["close"], "timeout")
            else:
                pos.stop_state = trail(pos.stop_state, bar["high"])
                still_open.append(pos)

        self._open_positions = still_open

    def _try_enter(self, d: date, reco: Recommendation, position_scale: float = 1.0) -> None:
        bar = self._view.bar(reco.ticker, d)
        if bar is None:
            return
        # 當日 [low, high] 需與入手區間重疊
        fill_low = max(bar["low"], reco.entry_low)
        fill_high = min(bar["high"], reco.entry_high)
        if fill_low > fill_high:
            return

        entry_px = (fill_low + fill_high) / 2.0

        # 部位規模（bear 模式下 position_scale < 1.0 → 上限縮小）
        ohlcv_hist = self._view.at(d).ohlcv(reco.ticker)
        if ohlcv_hist.empty:
            return
        recent_vol = int(
            ohlcv_hist.sort_values("date").iloc[-1]["volume"]
        )
        spec = SizingInput(
            total_equity=self._mark_to_market(d),
            available_cash=self._available_cash(),
            entry_price=entry_px,
            stop_price=reco.stop,
            recent_volume=recent_vol,
            win_rate=self._win_rate,
            avg_win=self._avg_win,
            avg_loss=self._avg_loss,
            max_single_position_pct=reco.max_position_pct * position_scale,
        )
        sized = size_position(spec)
        if sized.shares <= 0:
            return

        # 用 composite_scorer 算出的真實 ATR，不再從 target/stop 反推
        stop_state = StopState(
            entry=entry_px,
            atr=reco.atr,
            stop=reco.stop,
            target=reco.target,
            k_stop=self._k_stop,
            k_target=self._k_target,
            locked_profit_steps=0,
            running_high=entry_px,
            chandelier_k=self._chandelier_k,
        )
        self._open_positions.append(
            OpenPosition(
                ticker=reco.ticker,
                entry_date=d,
                entry_price=entry_px,
                shares=sized.shares,
                stop_state=stop_state,
                max_hold_days=self._max_hold_days,
            )
        )

    def _close_position(
        self, pos: OpenPosition, d: date, exit_price: float, reason: str
    ) -> None:
        fill: TradeResult = simulate_fill(
            self._cost, pos.entry_price, exit_price, pos.shares
        )
        self._equity += fill.pnl

        self._trades.append(
            ExecutedTrade(
                ticker=pos.ticker,
                entry_date=pos.entry_date,
                entry_price=pos.entry_price,
                exit_date=d,
                exit_price=exit_price,
                shares=pos.shares,
                exit_reason=reason,
                gross_return_pct=fill.gross_return_pct,
                net_return_pct=fill.net_return_pct,
                pnl=fill.pnl,
            )
        )
        self._refresh_priors()

    def _force_close_all(self, d: date) -> None:
        for pos in list(self._open_positions):
            bar = self._view.bar(pos.ticker, d)
            if bar is None:
                continue
            self._close_position(pos, d, bar["close"], "end_of_backtest")
        self._open_positions.clear()

    # ─────────────────────────────────────
    # 輔助
    # ─────────────────────────────────────
    def _has_position(self, ticker: str) -> bool:
        return any(p.ticker == ticker for p in self._open_positions)

    def _available_cash(self) -> float:
        used = sum(p.shares * p.entry_price for p in self._open_positions)
        return max(0.0, self._equity - used)

    def _mark_to_market(self, d: date) -> float:
        unrealized = 0.0
        for pos in self._open_positions:
            bar = self._view.bar(pos.ticker, d)
            if bar is None:
                continue
            unrealized += (bar["close"] - pos.entry_price) * pos.shares
        return self._equity + unrealized

    def _refresh_priors(self) -> None:
        """用已實現交易滾動更新 win_rate / avg_win / avg_loss（供 Kelly 動態調整）。"""
        if len(self._trades) < 5:
            return
        recent = self._trades[-50:]
        wins = [t for t in recent if t.net_return_pct > 0]
        losses = [t for t in recent if t.net_return_pct <= 0]
        if not recent:
            return
        self._win_rate = len(wins) / len(recent)
        if wins:
            self._avg_win = float(np.mean([t.net_return_pct / 100.0 for t in wins]))
        if losses:
            self._avg_loss = float(np.mean([abs(t.net_return_pct) / 100.0 for t in losses]))

    # ─────────────────────────────────────
    # 指標
    # ─────────────────────────────────────
    def _compute_metrics(self, equity_df: pd.DataFrame) -> dict[str, float]:
        if not self._trades:
            return {"trades": 0}

        wins = [t for t in self._trades if t.net_return_pct > 0]
        losses = [t for t in self._trades if t.net_return_pct <= 0]
        win_rate = len(wins) / len(self._trades)
        avg_win = float(np.mean([t.net_return_pct for t in wins])) if wins else 0.0
        avg_loss = float(np.mean([abs(t.net_return_pct) for t in losses])) if losses else 0.0
        pl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

        # MaxDD
        equity = equity_df["equity"].astype(float)
        running_max = equity.cummax()
        dd = (equity - running_max) / running_max
        max_dd = float(dd.min()) if len(dd) else 0.0

        # Sharpe（粗估，日報酬）
        equity_pct = equity.pct_change().dropna()
        if len(equity_pct) > 20 and equity_pct.std() > 0:
            sharpe = float(equity_pct.mean() / equity_pct.std() * np.sqrt(252))
        else:
            sharpe = 0.0

        total_return_pct = (float(equity.iloc[-1]) / self._initial_equity - 1.0) * 100.0 if len(equity) else 0.0

        return {
            "trades": float(len(self._trades)),
            "win_rate": round(win_rate, 4),
            "avg_win_pct": round(avg_win, 4),
            "avg_loss_pct": round(avg_loss, 4),
            "pl_ratio": round(pl_ratio, 3),
            "expectancy_pct": round(expectancy, 4),
            "max_drawdown_pct": round(max_dd, 4),
            "sharpe": round(sharpe, 3),
            "total_return_pct": round(total_return_pct, 2),
            "final_equity": round(float(equity.iloc[-1]), 2) if len(equity) else self._initial_equity,
        }


def _cast_overnight(d: dict) -> OvernightReport:
    return OvernightReport(
        as_of_date=str(d.get("as_of_date", "")),
        tsmc_adr_close=float(d.get("tsmc_adr_close", float("nan"))),
        tsmc_adr_change_pct=float(d.get("tsmc_adr_change_pct", 0.0)),
        nvda_close=float(d.get("nvda_close", float("nan"))),
        nvda_change_pct=float(d.get("nvda_change_pct", 0.0)),
        sox_close=float(d.get("sox_close", float("nan"))),
        sox_change_pct=float(d.get("sox_change_pct", 0.0)),
        vix=float(d.get("vix", 15.0)),
        market_mode=str(d.get("market_mode", "normal")),
    )
