"""
多因子合成器 — 把六大因子融合成 0–100 綜合分，產出晨報所需的推薦資訊。

流程：
  1. 讀 config/strategy.yaml 的 factor_weights
  2. 收集每檔候選股的 6 個 FactorScore（chip, sector, supply_chain, news, technical, market）
  3. 加權合成 value ∈ [0, 1] → score ∈ [0, 100]
  4. 套用 flags 調整：
     - day_trader_risk → 入手區間 = 前收 − 0.5 × ATR（避免市價衝進去）
     - leader_divergence → max_single_position_pct 減半
     - sector_weak → 扣 5 分
  5. 計算 ATR 止損 / 目標：entry − 2 × ATR、entry + 3 × ATR
  6. 依 recommendation.min_score 過濾，取 top max_picks

輸出 `Recommendation`：晨報模板會直接讀它。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.strategy.factor_base import FactorScore


@dataclass(frozen=True)
class FactorBundle:
    """一檔股票當日的所有因子分數 + ATR + 前收 — 由 pipeline 餵進來。"""
    ticker: str
    chip: FactorScore
    sector: FactorScore
    supply_chain: FactorScore
    news: FactorScore
    technical: FactorScore
    market: FactorScore
    atr: float
    prev_close: float


@dataclass(frozen=True)
class Recommendation:
    ticker: str
    score: float                                    # 0–100
    entry_low: float
    entry_high: float
    target: float
    stop: float
    max_position_pct: float
    reasons: list[str] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)
    breakdown: dict[str, float] = field(default_factory=dict)


class CompositeScorer:
    def __init__(self, strategy_yaml_path: Path | str) -> None:
        with Path(strategy_yaml_path).open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        self._weights: dict[str, float] = cfg.get("factor_weights", {}) or {}
        reco = cfg.get("recommendation", {}) or {}
        self._min_score = float(reco.get("min_score", 75))
        self._max_picks = int(reco.get("max_picks", 3))
        risk = cfg.get("risk", {}) or {}
        self._max_single_pct = float(risk.get("max_single_position_pct", 20.0))
        self._atr_stop_mult = float(risk.get("atr_stop_multiplier", 2.0))
        self._atr_target_mult = float(risk.get("atr_target_multiplier", 3.0))
        # 隔日沖入手下修係數（從 day_trader_brokers.yaml.thresholds 讀比較完整，
        # 但為避免跨檔耦合，這裡寫死預設值並允許 strategy.yaml 覆寫）
        self._day_trader_entry_discount_atr = float(
            risk.get("day_trader_entry_discount_atr", 0.5)
        )
        self._sector_weak_penalty = float(
            cfg.get("sector", {}).get("weak_penalty_points", 5.0)
        )

    def score(
        self,
        bundle: FactorBundle,
        weights: dict[str, float] | None = None,
        atr_stop_multiplier: float | None = None,
    ) -> Recommendation:
        """
        回傳單檔推薦（不論分數是否達標，過濾在 rank() 做）。

        weights: 若提供則覆寫 yaml 權重（由 regime_detector 產生）
        atr_stop_multiplier: 若提供則覆寫（高波機制放寬至 2.5）
        """
        weighted = self._weighted_sum(bundle, weights or self._weights)

        # 族群偏弱扣分（以百分制）
        if bundle.sector.flags.get("sector_weak"):
            weighted -= self._sector_weak_penalty / 100.0

        score_100 = max(0.0, min(100.0, weighted * 100.0))

        entry_low, entry_high = self._entry_zone(bundle)
        stop_mult = atr_stop_multiplier if atr_stop_multiplier is not None else self._atr_stop_mult
        target = bundle.prev_close + self._atr_target_mult * bundle.atr
        stop = bundle.prev_close - stop_mult * bundle.atr

        # 龍頭背離 → 部位上限減半
        max_pos = self._max_single_pct
        if bundle.supply_chain.flags.get("leader_divergence"):
            max_pos = max_pos / 2.0

        reasons = self._collect_reasons(bundle)
        flags = self._merge_flags(bundle)
        breakdown = self._collect_breakdown(bundle)

        return Recommendation(
            ticker=bundle.ticker,
            score=round(score_100, 1),
            entry_low=round(entry_low, 2),
            entry_high=round(entry_high, 2),
            target=round(target, 2),
            stop=round(stop, 2),
            max_position_pct=max_pos,
            reasons=reasons,
            flags=flags,
            breakdown=breakdown,
        )

    def rank(
        self,
        bundles: list[FactorBundle],
        weights: dict[str, float] | None = None,
        atr_stop_multiplier: float | None = None,
    ) -> list[Recommendation]:
        """對候選清單打分 → 依 score 排序 → 過濾未達標 → 截 top N。"""
        recos = [self.score(b, weights=weights, atr_stop_multiplier=atr_stop_multiplier) for b in bundles]
        qualified = [r for r in recos if r.score >= self._min_score]
        qualified.sort(key=lambda r: r.score, reverse=True)
        return qualified[: self._max_picks]

    @property
    def min_score(self) -> float:
        return self._min_score

    @property
    def max_picks(self) -> int:
        return self._max_picks

    @property
    def default_weights(self) -> dict[str, float]:
        return dict(self._weights)

    # ─────────────────────────────────────
    # 內部工具
    # ─────────────────────────────────────
    def _weighted_sum(self, bundle: FactorBundle, weights: dict[str, float]) -> float:
        mapping = {
            "chip_concentration": bundle.chip.value,
            "sector_momentum": bundle.sector.value,
            "supply_chain": bundle.supply_chain.value,
            "news_sentiment": self._normalize_news(bundle.news.value),
            "technical": bundle.technical.value,
            "market_regime": bundle.market.value,
        }
        total = 0.0
        for key, weight in weights.items():
            total += float(weight) * float(mapping.get(key, 0.0))
        return total

    @staticmethod
    def _normalize_news(sentiment_value: float) -> float:
        """情緒分數原始範圍 −1 ~ +1，映射為 0 ~ 1。"""
        return max(0.0, min(1.0, (sentiment_value + 1.0) / 2.0))

    def _entry_zone(self, bundle: FactorBundle) -> tuple[float, float]:
        """
        預設入手區間：前收 ± 0.3 × ATR
        若 day_trader_risk → 整段下修 0.5 × ATR，避免市價衝進去
        """
        base = bundle.prev_close
        spread = 0.3 * bundle.atr
        low = base - spread
        high = base + spread
        if bundle.chip.flags.get("day_trader_risk"):
            discount = self._day_trader_entry_discount_atr * bundle.atr
            low -= discount
            high -= discount
        return low, high

    @staticmethod
    def _collect_reasons(bundle: FactorBundle) -> list[str]:
        reasons = []
        for fs in (
            bundle.chip,
            bundle.sector,
            bundle.supply_chain,
            bundle.news,
            bundle.technical,
        ):
            if fs.reason:
                reasons.append(fs.reason)
        return reasons

    @staticmethod
    def _merge_flags(bundle: FactorBundle) -> dict[str, bool]:
        merged: dict[str, bool] = {}
        for fs in (
            bundle.chip,
            bundle.sector,
            bundle.supply_chain,
            bundle.news,
            bundle.technical,
            bundle.market,
        ):
            for k, v in fs.flags.items():
                merged[k] = merged.get(k, False) or bool(v)
        return merged

    @staticmethod
    def _collect_breakdown(bundle: FactorBundle) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, fs in (
            ("chip", bundle.chip),
            ("sector", bundle.sector),
            ("supply_chain", bundle.supply_chain),
            ("news", bundle.news),
            ("technical", bundle.technical),
            ("market", bundle.market),
        ):
            out[f"{name}_value"] = round(fs.value, 3)
            for k, v in fs.breakdown.items():
                if isinstance(v, (int, float)):
                    out[f"{name}.{k}"] = float(v)
        return out
