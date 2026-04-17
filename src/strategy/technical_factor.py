"""
技術面因子（權重 15%）— 站上 5MA、量能擴張、ATR 擴張。

資料來源：adr_fetcher.get_tw_ohlcv_adjusted()（還原股價 OHLCV）

評分組成（加權合成 0–1）：
  - 收盤 vs 5MA（0.4）：收盤高於 5MA 距離 0–3% 線性給分
  - 量能異常（0.3）：當日量 / 20MA 量，> 1.5 給滿分
  - ATR 擴張（0.3）：近 5 日 ATR / 近 20 日 ATR，> 1.3 給滿分

ATR 使用 Wilder 平滑（避免短期噪音），window=14。
量能比例使用簡單移動平均即可（交易員習慣的參照）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategy.factor_base import FactorScore, clamp01


class TechnicalFactor:
    def __init__(
        self,
        ma_window: int = 5,
        volume_ma_window: int = 20,
        atr_short_window: int = 5,
        atr_long_window: int = 20,
        atr_period: int = 14,
        price_above_ma_full_pct: float = 3.0,
        volume_ratio_full: float = 1.5,
        atr_expand_full: float = 1.3,
        weights: tuple[float, float, float] = (0.4, 0.3, 0.3),
    ) -> None:
        self._ma = ma_window
        self._vol_ma = volume_ma_window
        self._atr_short = atr_short_window
        self._atr_long = atr_long_window
        self._atr_period = atr_period
        self._px_full = price_above_ma_full_pct
        self._vol_full = volume_ratio_full
        self._atr_full = atr_expand_full
        self._w_px, self._w_vol, self._w_atr = weights

    def score(self, ohlcv: pd.DataFrame) -> FactorScore:
        """
        ohlcv: 欄位 date | open | high | low | close | volume（按 date 升冪）
        至少需要 max(ma, vol_ma, atr_long, atr_period) + 1 筆資料。
        """
        if ohlcv.empty:
            return FactorScore(value=0.0, reason="無價量資料")

        min_required = max(self._ma, self._vol_ma, self._atr_long, self._atr_period) + 1
        if len(ohlcv) < min_required:
            return FactorScore(value=0.0, reason="資料不足")

        df = ohlcv.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)
        volume = df["volume"].astype(float)

        ma = close.rolling(self._ma).mean()
        price_above_pct = (close.iloc[-1] - ma.iloc[-1]) / ma.iloc[-1] * 100.0
        s_px = clamp01(price_above_pct / self._px_full) if price_above_pct > 0 else 0.0

        vol_ma = volume.rolling(self._vol_ma).mean()
        vol_ratio = (
            float(volume.iloc[-1] / vol_ma.iloc[-1])
            if vol_ma.iloc[-1] > 0
            else 0.0
        )
        s_vol = clamp01((vol_ratio - 1.0) / (self._vol_full - 1.0)) if vol_ratio > 0 else 0.0

        atr = self._wilder_atr(df, self._atr_period)
        short_atr = atr.tail(self._atr_short).mean()
        long_atr = atr.tail(self._atr_long).mean()
        atr_ratio = float(short_atr / long_atr) if long_atr > 0 else 0.0
        s_atr = clamp01((atr_ratio - 1.0) / (self._atr_full - 1.0)) if atr_ratio > 0 else 0.0

        value = self._w_px * s_px + self._w_vol * s_vol + self._w_atr * s_atr

        return FactorScore(
            value=clamp01(value),
            breakdown={
                "price_above_ma_pct": round(price_above_pct, 2),
                "volume_ratio": round(vol_ratio, 2),
                "atr_ratio": round(atr_ratio, 2),
                "atr_14": round(float(atr.iloc[-1]), 3),
            },
            flags={},
            reason=self._build_reason(price_above_pct, vol_ratio, atr_ratio),
        )

    @staticmethod
    def _wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        # Wilder smoothing: alpha = 1/period
        atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        return atr.fillna(0.0)

    @staticmethod
    def _build_reason(px_pct: float, vol_ratio: float, atr_ratio: float) -> str:
        parts = []
        if px_pct > 0.5:
            parts.append(f"站 5MA +{px_pct:.1f}%")
        if vol_ratio >= 1.3:
            parts.append(f"量 {vol_ratio:.1f}x")
        if atr_ratio >= 1.2:
            parts.append("ATR 擴張")
        return "、".join(parts) or "技術面平淡"


def atr_from_ohlcv(ohlcv: pd.DataFrame, period: int = 14) -> float:
    """
    供 atr_stops / position_sizing 共用：回傳最新 ATR（Wilder）。
    """
    if ohlcv.empty or len(ohlcv) < period + 1:
        return 0.0
    tf = TechnicalFactor(atr_period=period)
    atr = tf._wilder_atr(ohlcv.sort_values("date"), period)
    return float(atr.iloc[-1])
