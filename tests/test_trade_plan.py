"""
Trade Plan 單元測試（Phase 18b v2 — 軌跡校準版）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.strategy.trade_plan import (
    CONFIDENCE_POSITION_PCT,
    compute_ma200,
    determine_confidence,
    generate_trade_plan,
    render_trade_plan_block,
)


def _mk_ohlcv(n_days: int, base: float = 100.0) -> pd.DataFrame:
    rows = []
    for i in range(n_days):
        rows.append({
            "date": date(2025, 1, 1) + timedelta(days=i),
            "open": base, "high": base * 1.01,
            "low": base * 0.99, "close": base, "volume": 1_000_000,
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────
# MA200
# ─────────────────────────────────────────
def test_ma200_requires_200_days() -> None:
    assert compute_ma200(_mk_ohlcv(150)) is None
    assert compute_ma200(_mk_ohlcv(250)) == pytest.approx(100.0)


# ─────────────────────────────────────────
# determine_confidence
# ─────────────────────────────────────────
def test_confidence_high_when_sweet_spot_with_chip() -> None:
    assert determine_confidence(z=3.2, chip_level="strong_accumulation",
                                 direction="buying") == "high"


def test_confidence_high_with_only_chip() -> None:
    assert determine_confidence(z=3.2, chip_level="strong_accumulation",
                                 direction="unknown") == "high"


def test_confidence_medium_when_no_confirmation() -> None:
    assert determine_confidence(z=3.2, chip_level="no_accumulation",
                                 direction="unknown") == "medium"


def test_confidence_reject_distribution() -> None:
    assert determine_confidence(z=3.2, chip_level="distribution",
                                 direction="buying") == "reject"


def test_confidence_reject_selling() -> None:
    assert determine_confidence(z=3.2, chip_level="strong_accumulation",
                                 direction="selling") == "reject"


def test_confidence_low_outside_sweet_spot() -> None:
    assert determine_confidence(z=2.7, chip_level="moderate_accumulation",
                                 direction="buying") == "low"
    assert determine_confidence(z=3.8, chip_level="strong_accumulation",
                                 direction="buying") == "low"


# ─────────────────────────────────────────
# generate_trade_plan
# ─────────────────────────────────────────
def test_plan_basic_geometry() -> None:
    df = _mk_ohlcv(250, base=100.0)
    plan = generate_trade_plan(
        ticker="3595", as_of=date(2025, 9, 7),
        close=100.0, z=3.2, direction="buying", chip_level="strong_accumulation",
        ohlcv=df, total_assets_twd=600_000,
    )
    assert plan is not None
    assert plan.entry_low < plan.entry_high <= plan.reference_close
    assert plan.hard_stop < plan.entry_low
    assert plan.confidence == "high"
    assert plan.trailing_drawdown_pp == 25.0


def test_plan_position_matches_confidence() -> None:
    df = _mk_ohlcv(250, base=100.0)
    high = generate_trade_plan(
        ticker="A", as_of=date(2025, 9, 7), close=100.0, z=3.2,
        direction="buying", chip_level="strong_accumulation",
        ohlcv=df, total_assets_twd=600_000,
    )
    low = generate_trade_plan(
        ticker="B", as_of=date(2025, 9, 7), close=100.0, z=2.7,
        direction="unknown", chip_level=None,
        ohlcv=df, total_assets_twd=600_000,
    )
    assert high.suggested_position_pct == CONFIDENCE_POSITION_PCT["high"]
    assert low.suggested_position_pct == CONFIDENCE_POSITION_PCT["low"]


def test_plan_hard_stop_uses_ma200() -> None:
    """200MA 存在 → hard_stop = ma200 × 0.85。"""
    df = _mk_ohlcv(250, base=100.0)   # ma200 = 100
    plan = generate_trade_plan(
        ticker="A", as_of=date(2025, 9, 7), close=100.0, z=3.2,
        direction="buying", chip_level="strong_accumulation", ohlcv=df,
    )
    # ma200 = 100, hard_stop_target = 85，但 entry_low = 97
    # 85 < 97 × 0.95 = 92.15 → 不觸發 adjust → hard_stop ≈ 85
    assert plan.hard_stop == pytest.approx(85.0, abs=0.5)
    assert "200MA" in plan.hard_stop_logic


def test_plan_hard_stop_fallback_when_no_ma200() -> None:
    """200 日資料不足 → fallback close × 0.80。"""
    df = _mk_ohlcv(150, base=100.0)
    plan = generate_trade_plan(
        ticker="A", as_of=date(2025, 9, 7), close=100.0, z=3.2,
        direction="buying", chip_level=None, ohlcv=df,
    )
    assert plan is not None
    assert plan.hard_stop == pytest.approx(80.0)
    assert "fallback" in plan.hard_stop_logic


def test_plan_reference_days_match_backtest_medians() -> None:
    """預估時程應對應軌跡回測中位數：+15%=32d、+30%=50d、peak=281d。"""
    df = _mk_ohlcv(250, base=100.0)
    plan = generate_trade_plan(
        ticker="A", as_of=date(2025, 9, 7), close=100.0, z=3.2,
        direction="buying", chip_level=None, ohlcv=df,
    )
    assert plan.expected_15pct_days == 32
    assert plan.expected_30pct_days == 50
    assert plan.expected_peak_days == 281


def test_plan_no_take_profit_fields() -> None:
    """v2 已移除 take_profit_1/2 — 確認 dataclass 無此欄位。"""
    df = _mk_ohlcv(250)
    plan = generate_trade_plan(
        ticker="A", as_of=date(2025, 9, 7), close=100.0, z=3.2,
        direction="buying", chip_level=None, ohlcv=df,
    )
    assert not hasattr(plan, "take_profit_1")
    assert not hasattr(plan, "take_profit_2")
    assert hasattr(plan, "trailing_drawdown_pp")


# ─────────────────────────────────────────
# render
# ─────────────────────────────────────────
def test_render_reject_warning() -> None:
    df = _mk_ohlcv(250, base=100.0)
    plan = generate_trade_plan(
        ticker="A", as_of=date(2025, 9, 7), close=100.0, z=3.2,
        direction="selling", chip_level="distribution", ohlcv=df,
    )
    md = render_trade_plan_block(plan)
    assert "拒絕" in md


def test_render_high_confidence_includes_trailing() -> None:
    df = _mk_ohlcv(250, base=100.0)
    plan = generate_trade_plan(
        ticker="A", as_of=date(2025, 9, 7), close=100.0, z=3.2,
        direction="buying", chip_level="strong_accumulation",
        ohlcv=df, total_assets_twd=600_000,
    )
    md = render_trade_plan_block(plan)
    assert "進場" in md
    assert "trailing" in md.lower() or "回撤" in md
    assert "硬停損" in md
    assert "高信心" in md
    # 不該出現舊版的 +15%/+30% 雙層停利
    assert "停利①" not in md
    assert "停利②" not in md
