"""
Trend Regime Detector — 用 MA20 / MA200 判斷加權指數的多空方向。

補強原本只看波動（vol）的 regime_detector — 熊市不一定高波，因此需要 trend 軸。

三段分類：
  - **bull**    ：price > MA200 且 MA200 斜率 > 0 且 price > MA20（避免多頭回檔誤判為強勢）
  - **bear**    ：price < MA200 且 MA200 斜率 < 0
  - **sideways**：其他（multi-month consolidation、bull pullback、bear rebound）

每段的策略調整：

| 趨勢     | min_score delta | target ATR 乘數 | 部位縮放 |
|----------|-----------------|------------------|----------|
| bull     | −5（更多訊號）   | 不變              | 1.0     |
| sideways | 0                | 2.0（見好就收）  | 1.0     |
| bear     | +15（嚴格防守）  | 2.0               | 0.3     |

bull 乘數 4.0 維持 — 牛市抱住；sideways/bear 收斂 2.0 — 盤整見好就收。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TrendVerdict:
    trend: str                          # "bull" | "sideways" | "bear"
    price: float
    ma20: float
    ma200: float
    ma200_slope_pct: float              # MA200 過去 20 日變化率 %
    min_score_delta: float
    target_atr_mult_override: float | None
    position_scale: float
    reason: str


class TrendRegimeDetector:
    def __init__(
        self,
        short_window: int = 20,
        long_window: int = 200,
        slope_lookback: int = 20,
        slope_threshold_pct: float = 0.5,
    ) -> None:
        self._short = short_window
        self._long = long_window
        self._slope_lookback = slope_lookback
        self._slope_threshold = slope_threshold_pct

    def detect(self, taiex_daily: pd.DataFrame) -> TrendVerdict:
        """taiex_daily: 欄位 date | close（升冪排序，至少 long_window + slope_lookback 筆）"""
        if taiex_daily.empty or len(taiex_daily) < self._long + self._slope_lookback:
            return self._fallback("歷史資料不足，視為 sideways")

        df = taiex_daily.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)
        ma20 = close.rolling(self._short).mean()
        ma200 = close.rolling(self._long).mean()

        price = float(close.iloc[-1])
        m20 = float(ma20.iloc[-1])
        m200 = float(ma200.iloc[-1])
        m200_prev = float(ma200.iloc[-1 - self._slope_lookback])
        slope_pct = (m200 - m200_prev) / m200_prev * 100.0 if m200_prev > 0 else 0.0

        if (
            price > m200
            and slope_pct > self._slope_threshold
            and price > m20
        ):
            return TrendVerdict(
                trend="bull",
                price=price, ma20=m20, ma200=m200, ma200_slope_pct=round(slope_pct, 2),
                min_score_delta=-5.0,
                target_atr_mult_override=None,
                position_scale=1.0,
                reason=f"價 {price:.0f} > MA200 {m200:.0f} 且斜率 +{slope_pct:.1f}%、價 > MA20",
            )

        if price < m200 and slope_pct < -self._slope_threshold:
            return TrendVerdict(
                trend="bear",
                price=price, ma20=m20, ma200=m200, ma200_slope_pct=round(slope_pct, 2),
                min_score_delta=+15.0,
                target_atr_mult_override=2.0,
                position_scale=0.3,
                reason=f"價 {price:.0f} < MA200 {m200:.0f} 且斜率 {slope_pct:.1f}%",
            )

        return TrendVerdict(
            trend="sideways",
            price=price, ma20=m20, ma200=m200, ma200_slope_pct=round(slope_pct, 2),
            min_score_delta=0.0,
            target_atr_mult_override=2.0,
            position_scale=1.0,
            reason=f"價 {price:.0f}、MA200 斜率 {slope_pct:+.1f}% — 盤整見好就收",
        )

    def _fallback(self, reason: str) -> TrendVerdict:
        return TrendVerdict(
            trend="sideways", price=0.0, ma20=0.0, ma200=0.0, ma200_slope_pct=0.0,
            min_score_delta=0.0, target_atr_mult_override=None, position_scale=1.0,
            reason=reason,
        )

    def get_breadth_score(self, ohlcv_map: dict[str, pd.DataFrame]) -> float:
        """
        市場寬度 — 計算 watchlist 中「最新收盤 > MA20」的比例，回傳 0.0 ~ 1.0。

        語意：trend 指數平均線只看 TAIEX 一條線會漏掉指數靠權值股拉抬、其餘個股已轉弱
        的「假牛市」。當 bull regime 下 breadth < 40%，代表漲幅集中在少數權值股、
        其餘觀察清單多已跌破 MA20 → 應啟動防守（laggard penalty 不可關）。

        資料不足或無有效 ticker → 回 1.0（中性，不觸發防守；避免因資料瑕疵誤判）。
        """
        if not ohlcv_map:
            return 1.0
        above = 0
        total = 0
        for df in ohlcv_map.values():
            if df is None or df.empty or "close" not in df:
                continue
            sorted_df = df.sort_values("date")
            if len(sorted_df) < self._short:
                continue
            closes = sorted_df["close"].astype(float)
            ma20 = closes.tail(self._short).mean()
            last_close = float(closes.iloc[-1])
            total += 1
            if last_close > ma20:
                above += 1
        if total == 0:
            return 1.0
        return above / total
