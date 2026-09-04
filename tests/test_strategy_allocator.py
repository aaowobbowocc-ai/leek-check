"""
Strategy Allocator 單元測試。

覆蓋範圍：
  - _detect_direction: bull / flat / bear 判斷
  - _combine: 8 種 regime 組合
  - evaluate: AllocationPlan 欄位正確性
  - 邊界條件：資料不足 / 空 DataFrame
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.risk.strategy_allocator import AllocationPlan, StrategyAllocator

# ──────────────────────────────────────────────
# 共用工具
# ──────────────────────────────────────────────
YAML_PATH = Path(__file__).resolve().parents[1] / "config" / "strategy.yaml"


def _mk_taiex(n_days: int, base: float = 20000.0, trend: float = 0.0) -> pd.DataFrame:
    """生成 n_days 天的加權指數假資料。
    trend: 每日漲跌 points（正 = 上漲趨勢）。
    """
    rows = []
    for i in range(n_days):
        c = base + trend * i
        rows.append({
            "date": date(2024, 1, 1) + timedelta(days=i),
            "close": c,
            "high": c + 50,
            "low": c - 50,
        })
    return pd.DataFrame(rows)


def _mk_bear_taiex(n_days: int = 120) -> pd.DataFrame:
    """明顯下跌走勢：每日 -30 點，觸發 bear。"""
    return _mk_taiex(n_days, base=22000.0, trend=-30.0)


def _mk_bull_taiex(n_days: int = 120) -> pd.DataFrame:
    """明顯上漲走勢：每日 +20 點，觸發 bull。"""
    return _mk_taiex(n_days, base=18000.0, trend=20.0)


def _mk_flat_taiex(n_days: int = 120) -> pd.DataFrame:
    """橫盤：無趨勢，收在 MA20 / MA60 附近。"""
    return _mk_taiex(n_days, base=20000.0, trend=0.0)


# ──────────────────────────────────────────────
# StrategyAllocator 基礎設定
# ──────────────────────────────────────────────
@pytest.fixture
def allocator() -> StrategyAllocator:
    return StrategyAllocator(YAML_PATH)


# ──────────────────────────────────────────────
# _detect_direction 測試
# ──────────────────────────────────────────────
class TestDetectDirection:
    def test_bull_uptrend(self, allocator: StrategyAllocator) -> None:
        df = _mk_bull_taiex(120)
        direction, ma20_dev, ma20_vs_ma60 = allocator._detect_direction(df)
        assert direction == "bull"
        assert ma20_dev > 0
        assert ma20_vs_ma60 > 0

    def test_bear_downtrend(self, allocator: StrategyAllocator) -> None:
        df = _mk_bear_taiex(120)
        direction, ma20_dev, ma20_vs_ma60 = allocator._detect_direction(df)
        assert direction == "bear"
        assert ma20_dev < 0
        assert ma20_vs_ma60 < 0

    def test_flat_sideways(self, allocator: StrategyAllocator) -> None:
        df = _mk_flat_taiex(120)
        direction, ma20_dev, ma20_vs_ma60 = allocator._detect_direction(df)
        assert direction == "flat"

    def test_insufficient_data_returns_flat(self, allocator: StrategyAllocator) -> None:
        df = _mk_taiex(30)  # 少於 MA60+1
        direction, ma20_dev, ma20_vs_ma60 = allocator._detect_direction(df)
        assert direction == "flat"
        assert ma20_dev == 0.0
        assert ma20_vs_ma60 == 0.0

    def test_empty_dataframe_returns_flat(self, allocator: StrategyAllocator) -> None:
        direction, ma20_dev, ma20_vs_ma60 = allocator._detect_direction(pd.DataFrame())
        assert direction == "flat"


# ──────────────────────────────────────────────
# _combine 測試
# ──────────────────────────────────────────────
class TestCombine:
    @pytest.mark.parametrize("vol,direction,expected", [
        ("low",    "bull", "bull_low"),
        ("normal", "bull", "bull_normal"),
        ("high",   "bull", "bull_high"),
        ("low",    "flat", "flat_low"),
        ("normal", "flat", "flat_normal"),
        ("high",   "flat", "flat_high"),
        ("low",    "bear", "bear"),
        ("normal", "bear", "bear"),
        ("crazy",  "bull", "crash"),
        ("crazy",  "bear", "crash"),
        ("crazy",  "flat", "crash"),
    ])
    def test_combine_all_regimes(
        self, allocator: StrategyAllocator, vol: str, direction: str, expected: str
    ) -> None:
        assert allocator._combine(vol, direction) == expected


# ──────────────────────────────────────────────
# AllocationPlan 欄位驗證
# ──────────────────────────────────────────────
class TestAllocationPlan:
    def _evaluate_with_regime(
        self, allocator: StrategyAllocator, vol_regime: str, direction: str
    ) -> AllocationPlan:
        """mock RegimeDetector 強制指定 vol_regime，mock _detect_direction 強制指定 direction。"""
        from src.risk.regime_detector import RegimeVerdict
        verdict = RegimeVerdict(
            regime=vol_regime, vol_ratio=1.0,
            weight_overrides={}, atr_stop_multiplier=2.0,
            position_scale=1.0, force_cash=(vol_regime == "crazy"),
        )
        with patch.object(allocator._regime_detector, "detect", return_value=verdict):
            with patch.object(allocator, "_detect_direction", return_value=(direction, 2.0, 1.5)):
                return allocator.evaluate(_mk_flat_taiex())

    def test_bull_normal_plan(self, allocator: StrategyAllocator) -> None:
        plan = self._evaluate_with_regime(allocator, "normal", "bull")
        assert plan.combined_regime == "bull_normal"
        assert plan.vol_anomaly_active is True
        assert plan.early_hunter_active is True
        assert plan.core_etf_pct + plan.satellite_max_pct + plan.cash_pct == pytest.approx(1.0)

    def test_bull_high_early_hunter_off(self, allocator: StrategyAllocator) -> None:
        plan = self._evaluate_with_regime(allocator, "high", "bull")
        assert plan.combined_regime == "bull_high"
        assert plan.early_hunter_active is False

    def test_flat_high_both_strategies_off(self, allocator: StrategyAllocator) -> None:
        plan = self._evaluate_with_regime(allocator, "high", "flat")
        assert plan.combined_regime == "flat_high"
        assert plan.vol_anomaly_active is False
        assert plan.early_hunter_active is False

    def test_bear_satellite_zero(self, allocator: StrategyAllocator) -> None:
        plan = self._evaluate_with_regime(allocator, "normal", "bear")
        assert plan.combined_regime == "bear"
        assert plan.satellite_max_pct == 0.0
        assert plan.max_concurrent_satellite == 0
        assert plan.per_trade_pct == 0.0

    def test_crash_max_cash(self, allocator: StrategyAllocator) -> None:
        plan = self._evaluate_with_regime(allocator, "crazy", "bull")
        assert plan.combined_regime == "crash"
        assert plan.cash_pct >= 0.50  # crash 必須有大量現金緩衝
        assert plan.vol_anomaly_active is False
        assert plan.early_hunter_active is False

    def test_allocation_sums_to_one_for_all_regimes(self, allocator: StrategyAllocator) -> None:
        regimes = [
            ("low",    "bull"), ("normal", "bull"), ("high",   "bull"),
            ("low",    "flat"), ("normal", "flat"), ("high",   "flat"),
            ("normal", "bear"),
            ("crazy",  "flat"),
        ]
        for vol, direction in regimes:
            plan = self._evaluate_with_regime(allocator, vol, direction)
            total = plan.core_etf_pct + plan.satellite_max_pct + plan.cash_pct
            assert total == pytest.approx(1.0), f"{plan.combined_regime}: sum={total}"

    def test_plan_has_briefing_note(self, allocator: StrategyAllocator) -> None:
        plan = self._evaluate_with_regime(allocator, "normal", "bull")
        assert len(plan.briefing_note) > 0


# ──────────────────────────────────────────────
# evaluate() end-to-end（不 mock）
# ──────────────────────────────────────────────
class TestEvaluateEndToEnd:
    def test_bull_market_detected(self, allocator: StrategyAllocator) -> None:
        df = _mk_bull_taiex(200)
        plan = allocator.evaluate(df)
        assert plan.direction == "bull"
        assert plan.combined_regime.startswith("bull_")

    def test_bear_market_detected(self, allocator: StrategyAllocator) -> None:
        df = _mk_bear_taiex(200)
        plan = allocator.evaluate(df)
        assert plan.direction == "bear"
        assert plan.combined_regime == "bear"

    def test_returns_allocation_plan_type(self, allocator: StrategyAllocator) -> None:
        plan = allocator.evaluate(_mk_flat_taiex(200))
        assert isinstance(plan, AllocationPlan)

    def test_vol_ratio_positive(self, allocator: StrategyAllocator) -> None:
        plan = allocator.evaluate(_mk_flat_taiex(200))
        assert plan.vol_ratio > 0

    def test_atr_multiplier_in_range(self, allocator: StrategyAllocator) -> None:
        plan = allocator.evaluate(_mk_flat_taiex(200))
        assert 1.5 <= plan.atr_stop_multiplier <= 3.5
