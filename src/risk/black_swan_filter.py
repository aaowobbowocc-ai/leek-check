"""
黑天鵝過濾器（計畫 §3.1）— 若命中則當日強制防守模式，晨報不發推薦。

觸發條件（OR 邏輯，任一命中即防守）：
  1. TSMC ADR 昨夜跌幅 ≥ |adr_drop_pct|（預設 −3%）
  2. VIX 昨日 ≥ vix_threshold（預設 25）
  3. 加權指數日線跌破月線（由 MarketFactor 提供 below_monthly_ma flag）
  4. （選用）Claude 判斷隔夜有重大系統性新聞 — 由上層把布林 flag 傳進來

輸出 `BlackSwanVerdict`，晨報模板會據此切換顯示。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class BlackSwanVerdict:
    defensive: bool
    reasons: list[str]


class BlackSwanFilter:
    def __init__(self, strategy_yaml_path: Path | str) -> None:
        with Path(strategy_yaml_path).open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        bs = cfg.get("black_swan", {}) or {}
        self._adr_drop_pct = float(bs.get("adr_drop_pct", -3.0))
        self._vix_threshold = float(bs.get("vix_threshold", 25.0))
        self._check_taiex = bool(bs.get("taiex_below_monthly_ma", True))

    def check(
        self,
        tsmc_adr_change_pct: float,
        vix: float,
        taiex_below_ma: bool,
        systemic_event: bool = False,
    ) -> BlackSwanVerdict:
        reasons: list[str] = []

        if tsmc_adr_change_pct <= self._adr_drop_pct:
            reasons.append(
                f"TSMC ADR 夜盤 {tsmc_adr_change_pct:.2f}% ≤ {self._adr_drop_pct:.1f}%"
            )
        if vix >= self._vix_threshold:
            reasons.append(f"VIX {vix:.1f} ≥ {self._vix_threshold:.1f}")
        if self._check_taiex and taiex_below_ma:
            reasons.append("加權指數跌破月線")
        if systemic_event:
            reasons.append("隔夜重大系統性事件")

        return BlackSwanVerdict(defensive=len(reasons) > 0, reasons=reasons)
