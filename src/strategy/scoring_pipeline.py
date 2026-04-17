"""
ScoringPipeline — 把資料層 → 因子 → 權重調整 → composite → 風控串成一條鏈。

呼叫者（morning_briefing / backtest engine）只需準備好 PipelineInput，
pipeline 內部自己決定：
  1. 先跑 black_swan_filter：防守模式 → 空推薦，直接回報 reasons
  2. 跑 regime_detector：拿到 weight_overrides、atr_stop_multiplier、force_cash
  3. 對每檔候選 ticker 跑五大因子（news 已經由呼叫者預跑，直接作為輸入）
  4. 用 composite_scorer.rank（帶 regime 覆寫後的權重）
  5. 回傳 PipelineOutput

設計理由：
  - 資料抓取與情緒分析放在上游（morning_briefing 控制快取與 API 金鑰）
  - pipeline 只負責策略邏輯，才能在 backtest 引擎裡離線重放歷史 bundle
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from src.data.adr_fetcher import OvernightReport
from src.data.news_collector import NewsItem
from src.risk.black_swan_filter import BlackSwanFilter, BlackSwanVerdict
from src.risk.regime_detector import RegimeDetector, RegimeVerdict, apply_overrides
from src.strategy.chip_factor import ChipFactor
from src.strategy.composite_scorer import (
    CompositeScorer,
    FactorBundle,
    Recommendation,
)
from src.strategy.factor_base import FactorScore
from src.strategy.market_factor import MarketFactor
from src.strategy.sector_factor import SectorFactor
from src.strategy.sentiment_factor import SentimentResult
from src.strategy.supply_chain_factor import SupplyChainFactor
from src.strategy.technical_factor import TechnicalFactor, atr_from_ohlcv


@dataclass(frozen=True)
class TickerInputs:
    ticker: str
    company_name: str
    ohlcv: pd.DataFrame                       # T−1 及更早（升冪）
    institutional: pd.DataFrame               # T−1 及更早
    broker: pd.DataFrame                      # T−1 單日
    shares_outstanding: int
    recent_volume: int                        # T−1 成交量
    news: list[NewsItem] = field(default_factory=list)
    sentiment: SentimentResult | None = None  # 已由上游預跑好
    today_open_close: tuple[float, float] | None = None  # 僅給 sector 紅 K 比例用（回測模擬日的開收；實盤晨報前留空）


@dataclass(frozen=True)
class PipelineInput:
    as_of_date: date
    tickers: list[TickerInputs]
    taiex_daily: pd.DataFrame                 # 含 date | high | low | close
    overnight: OvernightReport


@dataclass(frozen=True)
class PipelineOutput:
    as_of_date: date
    recommendations: list[Recommendation]
    defensive: bool
    defensive_reasons: list[str]
    regime: str
    vol_ratio: float
    weights_used: dict[str, float]
    atr_stop_multiplier: float
    overnight: OvernightReport | None = None


class ScoringPipeline:
    def __init__(
        self,
        strategy_yaml: Path | str,
        sector_map_yaml: Path | str,
        day_trader_yaml: Path | str,
    ) -> None:
        self._chip = ChipFactor(day_trader_yaml)
        self._sector = SectorFactor(sector_map_yaml)
        self._supply = SupplyChainFactor()
        self._tech = TechnicalFactor()
        self._market = MarketFactor()
        self._composite = CompositeScorer(strategy_yaml)
        self._black_swan = BlackSwanFilter(strategy_yaml)
        self._regime = RegimeDetector(strategy_yaml)

    def run(self, inp: PipelineInput) -> PipelineOutput:
        market_score = self._market.score(inp.taiex_daily)
        regime = self._regime.detect(inp.taiex_daily)
        bs = self._black_swan.check(
            tsmc_adr_change_pct=inp.overnight["tsmc_adr_change_pct"],
            vix=inp.overnight["vix"],
            taiex_below_ma=market_score.flags.get("below_monthly_ma", False),
        )

        weights = apply_overrides(self._composite.default_weights, regime.weight_overrides)

        if bs.defensive or regime.force_cash:
            return PipelineOutput(
                as_of_date=inp.as_of_date,
                recommendations=[],
                defensive=True,
                defensive_reasons=bs.reasons + (["vol_ratio > 2.0 狂波強制空手"] if regime.force_cash else []),
                regime=regime.regime,
                vol_ratio=regime.vol_ratio,
                weights_used=weights,
                atr_stop_multiplier=regime.atr_stop_multiplier,
                overnight=inp.overnight,
            )

        # 先跑每檔 chip 分數與今日 K 線，供 sector 因子參照
        chip_scores: dict[str, FactorScore] = {}
        peer_chip_vals: dict[str, float] = {}
        peer_candles: dict[str, tuple[float, float]] = {}
        atrs: dict[str, float] = {}
        prev_closes: dict[str, float] = {}

        for ti in inp.tickers:
            chip_scores[ti.ticker] = self._chip.score(
                ti.ticker,
                ti.institutional,
                ti.broker,
                ti.shares_outstanding,
                ti.recent_volume,
            )
            peer_chip_vals[ti.ticker] = chip_scores[ti.ticker].value
            if ti.today_open_close is not None:
                peer_candles[ti.ticker] = ti.today_open_close
            atrs[ti.ticker] = atr_from_ohlcv(ti.ohlcv, period=14)
            if not ti.ohlcv.empty:
                prev_closes[ti.ticker] = float(ti.ohlcv.sort_values("date").iloc[-1]["close"])
            else:
                prev_closes[ti.ticker] = 0.0

        bundles: list[FactorBundle] = []
        for ti in inp.tickers:
            tech_score = self._tech.score(ti.ohlcv)
            sector_score = self._sector.score(ti.ticker, peer_chip_vals, peer_candles)
            supply_score = self._supply.score(
                ti.ticker,
                nvda_change_pct=inp.overnight["nvda_change_pct"],
                sox_change_pct=inp.overnight["sox_change_pct"],
                tsm_change_pct=inp.overnight["tsmc_adr_change_pct"],
                leader_below_monthly_ma=market_score.flags.get("below_monthly_ma", False),
                ticker_price_above_5ma_pct=tech_score.breakdown.get("price_above_ma_pct", 0.0),
            )
            news_score = _sentiment_to_factor(ti.sentiment)

            bundles.append(
                FactorBundle(
                    ticker=ti.ticker,
                    chip=chip_scores[ti.ticker],
                    sector=sector_score,
                    supply_chain=supply_score,
                    news=news_score,
                    technical=tech_score,
                    market=market_score,
                    atr=atrs[ti.ticker],
                    prev_close=prev_closes[ti.ticker],
                )
            )

        recos = self._composite.rank(
            bundles,
            weights=weights,
            atr_stop_multiplier=regime.atr_stop_multiplier,
        )

        return PipelineOutput(
            as_of_date=inp.as_of_date,
            recommendations=recos,
            defensive=False,
            defensive_reasons=[],
            regime=regime.regime,
            vol_ratio=regime.vol_ratio,
            weights_used=weights,
            atr_stop_multiplier=regime.atr_stop_multiplier,
            overnight=inp.overnight,
        )


def _sentiment_to_factor(sent: SentimentResult | None) -> FactorScore:
    if sent is None:
        return FactorScore(value=0.0, reason="無新聞")
    return FactorScore(
        value=sent.score,                # composite 會把 [-1, 1] 映射到 [0, 1]
        breakdown={"n_news": float(sent.n_news), "raw_sentiment": sent.score},
        reason=sent.reason,
    )
