"""
因子共用型別。

所有因子（chip / sector / supply_chain / news / technical / market）都回傳
`FactorScore`，由 `composite_scorer` 依 `strategy.yaml` 的權重加總成 0–100。

設計理由：
- value 統一為 0.0–1.0，composite 層才換算成百分制，避免各因子自己寫死倍率
- breakdown 攤開給晨報顯示「為什麼這檔被推薦」
- flags 承載風控訊號（day_trader_risk、leader_divergence …），
  由 `composite_scorer` 轉譯成晨報警示與部位調整
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FactorScore:
    value: float                               # 0.0 – 1.0，超出會被 composite clamp
    breakdown: dict[str, float] = field(default_factory=dict)
    flags: dict[str, bool] = field(default_factory=dict)
    reason: str = ""


def clamp01(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return max(0.0, min(1.0, x))
