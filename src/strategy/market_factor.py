"""
大盤背景因子（權重 10%）— 加權指數站上月線才計分。

資料來源：加權指數（^TWII）日線（yfinance 可取）

評分邏輯：
  - 加權指數收盤 > 20 日移動平均 → value = 1.0
  - 收盤 = MA → value = 0.5
  - 收盤 < MA 5% 以上 → value = 0.0

注意：大盤背景是「必要條件」之一，不是主要訊號。若 vol_ratio 進入狂波，
regime_detector 會直接強制空手，此因子分數就不重要了。
"""
from __future__ import annotations

import pandas as pd

from src.strategy.factor_base import FactorScore, clamp01


class MarketFactor:
    def __init__(self, ma_window: int = 20, downside_full_pct: float = 5.0) -> None:
        self._ma_window = ma_window
        self._downside_full_pct = downside_full_pct

    def score(self, taiex_daily: pd.DataFrame) -> FactorScore:
        """
        taiex_daily: 欄位 date | close（至少需 ma_window+1 筆）
        """
        if taiex_daily.empty or len(taiex_daily) < self._ma_window + 1:
            return FactorScore(value=0.5, reason="資料不足，保守中性")

        df = taiex_daily.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)
        ma = close.rolling(self._ma_window).mean()
        latest_close = float(close.iloc[-1])
        latest_ma = float(ma.iloc[-1])
        if latest_ma <= 0:
            return FactorScore(value=0.5, reason="MA 計算異常")

        diff_pct = (latest_close - latest_ma) / latest_ma * 100.0

        if diff_pct >= 0:
            value = 0.5 + 0.5 * clamp01(diff_pct / 2.0)  # 站上且拉開 2% → 1.0
        else:
            value = 0.5 * (1.0 - clamp01(abs(diff_pct) / self._downside_full_pct))

        return FactorScore(
            value=clamp01(value),
            breakdown={
                "taiex_close": round(latest_close, 1),
                "taiex_ma20": round(latest_ma, 1),
                "diff_pct": round(diff_pct, 2),
            },
            flags={"below_monthly_ma": diff_pct < 0},
            reason=(
                f"加權 +{diff_pct:.1f}% 月線"
                if diff_pct >= 0
                else f"加權 {diff_pct:.1f}% 月線"
            ),
        )
