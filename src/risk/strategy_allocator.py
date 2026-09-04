"""
Strategy Allocator — 2D 機制偵測 → 策略分配。

CTA 核心邏輯：不同市場狀態讓不同策略上場，而不是調整同一套的因子權重。

兩個維度：
  Vol 維度（繼承 RegimeDetector）:
    low | normal | high | crazy

  方向維度（新增）:
    bull  — Close > MA20 且 MA20 > MA60（明顯上升趨勢）
    flat  — 介於中間
    bear  — Close < MA20 且 MA20 < MA60（明顯下降趨勢）

組合成 6 個運作狀態：
  bull_low / bull_normal / bull_high / flat / bear / crash

每個狀態輸出 AllocationPlan：
  - 核心 ETF 倉位 %
  - 衛星策略上限 %
  - 現金緩衝 %
  - Vol Anomaly 開關
  - Early Hunter 開關
  - 最大同時持倉數
  - 每筆下單佔總資產 %

設計原則：
  Early Hunter 需要明確上升趨勢（持 6-12 個月），flat/bear 一律關閉。
  Vol Anomaly 能在 flat 盤中抓個股異動，但 high_vol + flat（噪音高）時關閉。
  crash（crazy vol）無條件全現金。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from src.risk.regime_detector import RegimeDetector, RegimeVerdict


# ─────────────────────────────────────────
# 輸出資料結構
# ─────────────────────────────────────────
@dataclass(frozen=True)
class AllocationPlan:
    # 機制標籤
    combined_regime: str        # "bull_normal" | "flat" | "bear" | "crash" etc.
    vol_regime: str             # "low" | "normal" | "high" | "crazy"
    direction: str              # "bull" | "flat" | "bear"
    vol_ratio: float
    taiex_vs_ma20_pct: float    # 指數距 MA20 偏離 %
    ma20_vs_ma60_pct: float     # MA20 距 MA60 偏離 %（斜率代理）

    # 倉位分配（三者合計 = 100%）
    core_etf_pct: float         # 核心 ETF（0050/006208）建議倉位
    satellite_max_pct: float    # 衛星策略可用上限
    cash_pct: float

    # 策略開關
    vol_anomaly_active: bool
    early_hunter_active: bool

    # 每筆進場參數
    max_concurrent_satellite: int
    per_trade_pct: float        # 每筆佔總資產 %

    # 風控
    atr_stop_multiplier: float

    # 晨報文字
    briefing_note: str


# ─────────────────────────────────────────
# 分配表（6 狀態）
# ─────────────────────────────────────────
#   core / sat_max / cash / va_on / eh_on / max_pos / per_trade / atr_mult
#
# v2 設計（2026-04-26 backtest 驗收後修正）：
#   memory 已記載「37% buffer 在牛市拖累 11pp」。回測證實 cash_pct=20-30% 的舊表
#   讓 allocator CAGR 從 22.8% → 15.3%，bull 年每年輸 5-15pp。
#   修正方針：bull/flat 大幅降 cash（≤10%），bear/crash 才保留高現金緩衝。
#   省下的 cash 改投 core，因 satellite 上限受策略開關 + max_pos 限制無法吸收。
#
_ALLOCATION_TABLE: dict[str, tuple] = {
    #              core   sat   cash  VA     EH     pos  trade  atr
    "bull_low":   (0.75, 0.20, 0.05, True,  True,  4,   0.05,  2.0),
    "bull_normal":(0.70, 0.25, 0.05, True,  True,  4,   0.05,  2.0),
    "bull_high":  (0.65, 0.20, 0.15, True,  False, 3,   0.04,  2.5),
    "flat_low":   (0.80, 0.10, 0.10, True,  False, 2,   0.04,  2.0),
    "flat_normal":(0.80, 0.10, 0.10, True,  False, 2,   0.04,  2.0),
    "flat_high":  (0.70, 0.05, 0.25, False, False, 1,   0.03,  2.5),
    "bear":       (0.60, 0.00, 0.40, False, False, 0,   0.00,  2.5),
    "crash":      (0.35, 0.00, 0.65, False, False, 0,   0.00,  3.0),
}

_BRIEFING_NOTES: dict[str, str] = {
    "bull_low":    "牛市低波 — 衛星全速，Vol Anomaly + Early Hunter 同時啟用",
    "bull_normal": "牛市正常 — 衛星空間最大，兩個策略皆開",
    "bull_high":   "牛市高波 — 衛星縮量，Early Hunter 暫停（高波趨勢不適合長持）",
    "flat_low":    "橫盤低波 — 衛星保守，只留 Vol Anomaly 抓個股異動",
    "flat_normal": "橫盤正常 — 衛星保守，只留 Vol Anomaly",
    "flat_high":   "橫盤高波（噪音盤）— 衛星縮至最小，Vol Anomaly 暫停",
    "bear":        "熊市 — 衛星全關，現金緩衝增至 50%，等待機會",
    "crash":       "崩盤/狂波 — 全面防守，現金 70%，任何新部位暫停",
}


# ─────────────────────────────────────────
# 主類
# ─────────────────────────────────────────
class StrategyAllocator:
    """
    每日晨報前呼叫 evaluate()，取得今日策略分配方案。
    """

    def __init__(self, strategy_yaml_path: Path | str) -> None:
        self._regime_detector = RegimeDetector(strategy_yaml_path)
        with Path(strategy_yaml_path).open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        alloc_cfg = cfg.get("strategy_allocator", {}) or {}
        self._ma_short = int(alloc_cfg.get("ma_short", 20))
        self._ma_long = int(alloc_cfg.get("ma_long", 60))
        self._bull_threshold_pct = float(alloc_cfg.get("bull_threshold_pct", 0.5))
        self._bear_threshold_pct = float(alloc_cfg.get("bear_threshold_pct", -0.5))

    def evaluate(self, taiex_daily: pd.DataFrame) -> AllocationPlan:
        """
        taiex_daily: 欄位 date | close | high | low（升冪）
        傳入 ≥ 200 日以確保 MA60 計算穩定。
        """
        vol_verdict = self._regime_detector.detect(taiex_daily)
        direction, ma20_dev, ma20_vs_ma60 = self._detect_direction(taiex_daily)
        combined = self._combine(vol_verdict.regime, direction)
        row = _ALLOCATION_TABLE[combined]

        return AllocationPlan(
            combined_regime=combined,
            vol_regime=vol_verdict.regime,
            direction=direction,
            vol_ratio=vol_verdict.vol_ratio,
            taiex_vs_ma20_pct=round(ma20_dev, 2),
            ma20_vs_ma60_pct=round(ma20_vs_ma60, 2),
            core_etf_pct=row[0],
            satellite_max_pct=row[1],
            cash_pct=row[2],
            vol_anomaly_active=row[3],
            early_hunter_active=row[4],
            max_concurrent_satellite=row[5],
            per_trade_pct=row[6],
            atr_stop_multiplier=row[7],
            briefing_note=_BRIEFING_NOTES[combined],
        )

    def _detect_direction(
        self, taiex_daily: pd.DataFrame
    ) -> tuple[str, float, float]:
        """回傳 (direction, close_vs_ma20_pct, ma20_vs_ma60_pct)"""
        if taiex_daily.empty or len(taiex_daily) < self._ma_long + 1:
            return "flat", 0.0, 0.0

        df = taiex_daily.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)

        ma20 = close.rolling(self._ma_short).mean().iloc[-1]
        ma60 = close.rolling(self._ma_long).mean().iloc[-1]
        cur = float(close.iloc[-1])

        if ma20 <= 0 or ma60 <= 0:
            return "flat", 0.0, 0.0

        ma20_dev = (cur / ma20 - 1) * 100
        ma20_vs_ma60 = (ma20 / ma60 - 1) * 100

        # bull: 站上 MA20 且 MA20 > MA60（兩個條件同時）
        if ma20_dev > self._bull_threshold_pct and ma20_vs_ma60 > self._bull_threshold_pct:
            return "bull", ma20_dev, ma20_vs_ma60

        # bear: 跌破 MA20 且 MA20 < MA60
        if ma20_dev < self._bear_threshold_pct and ma20_vs_ma60 < self._bear_threshold_pct:
            return "bear", ma20_dev, ma20_vs_ma60

        return "flat", ma20_dev, ma20_vs_ma60

    def _combine(self, vol: str, direction: str) -> str:
        if vol == "crazy":
            return "crash"
        if direction == "bear":
            return "bear"
        if direction == "bull":
            return f"bull_{vol}"      # bull_low | bull_normal | bull_high
        # flat
        return f"flat_{vol}"          # flat_low | flat_normal | flat_high
