"""
Regime Detector（計畫 §3.4）— 波動率機制偵測，自適應因子權重與風控。

核心：vol_ratio = 加權指數最近 N 日 ATR ÷ 過去 M 年中位數 ATR

| vol_ratio | 機制     | 行為                                                         |
|-----------|---------|--------------------------------------------------------------|
| < 0.8     | 低波     | 技術面 +5%、新聞 −5%                                         |
| 0.8–1.3   | 中波     | 預設權重                                                      |
| 1.3–2.0   | 高波     | 籌碼 +10%、供應鏈 +5%、技術面 −10%、大盤 −5%；止損放寬 2.5×ATR |
| > 2.0     | 狂波     | 強制空手，晨報顯示 🔴                                         |

設計理由：2026 年盤中 > 80% 成交量由演算法觸發，高波時技術指標會鈍化，
只有「跟大錢走（籌碼）」與「跟邏輯走（供應鏈）」是真的。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml


@dataclass(frozen=True)
class RegimeVerdict:
    regime: str               # "low" | "normal" | "high" | "crazy"
    vol_ratio: float
    weight_overrides: dict[str, float]
    atr_stop_multiplier: float
    position_scale: float     # 部位縮放倍率（例如高波 = 0.5）
    force_cash: bool


_WEIGHT_OVERRIDES = {
    "low":    {"technical": +0.05, "news_sentiment": -0.05},
    "normal": {},
    "high":   {
        "chip_concentration": +0.10,
        "supply_chain": +0.05,
        "technical": -0.10,
        "market_regime": -0.05,
    },
    "crazy":  {},
}

_ATR_MULT = {"low": 2.0, "normal": 2.0, "high": 2.5, "crazy": 2.5}
_POS_SCALE = {"low": 1.0, "normal": 1.0, "high": 0.5, "crazy": 0.0}


class RegimeDetector:
    def __init__(self, strategy_yaml_path: Path | str) -> None:
        with Path(strategy_yaml_path).open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        r = cfg.get("regime", {}) or {}
        self._lookback_days = int(r.get("lookback_days", 20))
        self._reference_years = int(r.get("reference_years", 5))
        th = r.get("thresholds", {}) or {}
        self._th_low = float(th.get("low", 0.8))
        self._th_normal_high = float(th.get("normal_high", 1.3))
        self._th_high_crazy = float(th.get("high_crazy", 2.0))
        self._crazy_force_cash = bool(r.get("crazy_force_cash", True))

    def detect(self, taiex_daily: pd.DataFrame) -> RegimeVerdict:
        """taiex_daily: 欄位 date | high | low | close（升冪排序，需至少 5 年歷史）"""
        if taiex_daily.empty or len(taiex_daily) < self._lookback_days + 1:
            return self._verdict("normal", 1.0)

        df = taiex_daily.sort_values("date").reset_index(drop=True)
        atr_series = _true_range_atr(df, period=self._lookback_days)

        if atr_series.empty:
            return self._verdict("normal", 1.0)

        recent_atr = float(atr_series.iloc[-1])
        reference = self._reference_median(atr_series)
        if reference <= 0:
            return self._verdict("normal", 1.0)

        vol_ratio = recent_atr / reference
        regime = self._classify(vol_ratio)
        return self._verdict(regime, vol_ratio)

    def _reference_median(self, atr_series: pd.Series) -> float:
        # 約 252 交易日 × N 年
        years_days = 252 * self._reference_years
        tail = atr_series.tail(years_days)
        if tail.empty:
            return 0.0
        return float(tail.median())

    def _classify(self, vol_ratio: float) -> str:
        if vol_ratio < self._th_low:
            return "low"
        if vol_ratio < self._th_normal_high:
            return "normal"
        if vol_ratio < self._th_high_crazy:
            return "high"
        return "crazy"

    def _verdict(self, regime: str, vol_ratio: float) -> RegimeVerdict:
        return RegimeVerdict(
            regime=regime,
            vol_ratio=round(vol_ratio, 2),
            weight_overrides=dict(_WEIGHT_OVERRIDES.get(regime, {})),
            atr_stop_multiplier=_ATR_MULT.get(regime, 2.0),
            position_scale=_POS_SCALE.get(regime, 1.0),
            force_cash=regime == "crazy" and self._crazy_force_cash,
        )


def _true_range_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().dropna()


def apply_overrides(
    base_weights: dict[str, float], overrides: dict[str, float]
) -> dict[str, float]:
    """
    把 overrides 累加到 base_weights，保留總和為 1（其餘權重按比例縮放）。
    若 overrides 空 → 回傳 base_weights 拷貝。
    """
    if not overrides:
        return dict(base_weights)
    adjusted = {k: v + overrides.get(k, 0.0) for k, v in base_weights.items()}
    adjusted = {k: max(0.0, v) for k, v in adjusted.items()}
    total = sum(adjusted.values())
    if total <= 0:
        return dict(base_weights)
    return {k: v / total for k, v in adjusted.items()}
