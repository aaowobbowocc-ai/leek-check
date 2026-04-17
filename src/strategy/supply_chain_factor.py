"""
供應鏈傳導因子（權重 20%）— TSMC ADR / NVDA / SOX 夜盤溢出效應 + 龍頭背離偵測。

核心假設：
  半導體設備/材料股與 NVDA/SOX 的相關性遠高於與加權指數，晨報用美股夜盤就能先行預判
  當日台股表現。相關係數預先用 walk-forward 歷史資料算好，放 config/strategy.yaml
  或直接傳入（此處保留預設值做 fallback）。

評分公式：
  base = (corr_nvda × nvda_chg% + corr_sox × sox_chg% + corr_tsm × tsm_chg%) / 3
  s_value = clamp01((base + full_range) / (2 × full_range))   # base ∈ [−full, +full] → [0, 1]
  full_range 預設 ±2%（夜盤漲跌幅超過 2% 的事件已算明顯）

龍頭背離指標（flags.leader_divergence）：
  - 若 2330 跌破月線（TSMC 疲弱）
  - 但本標的技術面仍多頭（站上 5MA 且距離 > 1%）
  - → 末升段風險，composite_scorer 會把 max_single_position_pct 減半
"""
from __future__ import annotations

import pandas as pd

from src.strategy.factor_base import FactorScore, clamp01


_DEFAULT_CORRELATIONS = {
    # ticker → (corr_nvda, corr_sox, corr_tsm)
    # 這些數值只是 fallback；walk_forward 實際跑出的矩陣會覆寫
    "3413": (0.55, 0.60, 0.65),  # 京鼎（半導體設備）
    "3680": (0.50, 0.55, 0.60),  # 家登（CoWoS 設備）
    "3131": (0.50, 0.55, 0.58),  # 弘塑
    "8996": (0.45, 0.50, 0.55),  # 高力
    "2330": (0.70, 0.75, 1.00),  # 台積電
}


class SupplyChainFactor:
    def __init__(
        self,
        correlations: dict[str, tuple[float, float, float]] | None = None,
        full_range_pct: float = 2.0,
        leader_ticker: str = "2330",
    ) -> None:
        self._corr = correlations or dict(_DEFAULT_CORRELATIONS)
        self._full_range = full_range_pct
        self._leader = leader_ticker

    def score(
        self,
        ticker: str,
        nvda_change_pct: float,
        sox_change_pct: float,
        tsm_change_pct: float,
        leader_below_monthly_ma: bool,
        ticker_price_above_5ma_pct: float,
    ) -> FactorScore:
        """
        nvda/sox/tsm_change_pct：昨夜美股收盤漲跌幅（百分比數字，例如 1.5 代表 +1.5%）
        leader_below_monthly_ma：2330 是否跌破月線（True 為疲弱）
        ticker_price_above_5ma_pct：本標的收盤 vs 5MA 的百分比（>0 代表偏多）
        """
        corr_nvda, corr_sox, corr_tsm = self._corr.get(ticker, (0.3, 0.3, 0.3))
        base = (
            corr_nvda * nvda_change_pct
            + corr_sox * sox_change_pct
            + corr_tsm * tsm_change_pct
        ) / 3.0

        # 線性映射 [-full_range, +full_range] → [0, 1]
        full = self._full_range
        raw = (base + full) / (2 * full)
        value = clamp01(raw)

        leader_divergence = (
            ticker != self._leader
            and leader_below_monthly_ma
            and ticker_price_above_5ma_pct > 1.0
        )

        return FactorScore(
            value=value,
            breakdown={
                "weighted_overnight_pct": round(base, 3),
                "corr_nvda": round(corr_nvda, 2),
                "corr_sox": round(corr_sox, 2),
                "corr_tsm": round(corr_tsm, 2),
                "nvda_change_pct": round(nvda_change_pct, 2),
                "sox_change_pct": round(sox_change_pct, 2),
                "tsm_change_pct": round(tsm_change_pct, 2),
            },
            flags={"leader_divergence": leader_divergence},
            reason=self._build_reason(base, leader_divergence),
        )

    @staticmethod
    def _build_reason(weighted_pct: float, divergence: bool) -> str:
        parts = []
        if weighted_pct >= 0.5:
            parts.append(f"夜盤 +{weighted_pct:.2f}%")
        elif weighted_pct <= -0.5:
            parts.append(f"夜盤 {weighted_pct:.2f}%")
        else:
            parts.append("夜盤中性")
        if divergence:
            parts.append("⚠️ 龍頭背離")
        return "、".join(parts)


def compute_correlations(
    ticker_returns: pd.Series,
    nvda_returns: pd.Series,
    sox_returns: pd.Series,
    tsm_returns: pd.Series,
) -> tuple[float, float, float]:
    """
    工具函式：從日報酬序列算相關係數。
    所有輸入需為 pd.Series，index 為 date 對齊（同一時區的 pd.Timestamp）。
    """
    aligned = pd.DataFrame(
        {"t": ticker_returns, "nvda": nvda_returns, "sox": sox_returns, "tsm": tsm_returns}
    ).dropna()
    if len(aligned) < 20:
        return 0.0, 0.0, 0.0
    return (
        float(aligned["t"].corr(aligned["nvda"])),
        float(aligned["t"].corr(aligned["sox"])),
        float(aligned["t"].corr(aligned["tsm"])),
    )
