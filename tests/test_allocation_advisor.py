"""
allocation_advisor 單元測試 — 市場狀態 / 個股追蹤 / 配置偏離 / 季度再平衡。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.report.allocation_advisor import (
    AllocationCheck,
    StockTracker,
    check_allocation_drift,
    detect_regime,
    is_quarterly_rebalance_day,
    render_allocation_section,
)


def _mk_taiex(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))],
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [100_000] * len(closes),
        }
    )


# ─────────────────────────────────────────
# detect_regime
# ─────────────────────────────────────────
def test_regime_overheated_when_far_above_ma200() -> None:
    closes = [20_000.0] * 200 + [38_500.0]
    r = detect_regime(_mk_taiex(closes))
    assert r is not None
    assert r.level == "overheated"
    assert r.deviation_pct > 30


def test_regime_expensive() -> None:
    closes = [30_000.0] * 200 + [35_000.0]   # +16.67%
    r = detect_regime(_mk_taiex(closes))
    assert r.level == "expensive"


def test_regime_fair_within_15pct() -> None:
    closes = [30_000.0] * 200 + [31_500.0]   # +5%
    r = detect_regime(_mk_taiex(closes))
    assert r.level == "fair"


def test_regime_cheap_below_ma200() -> None:
    """跌破 200MA 但月跌 < 10%（緩跌）→ cheap，不是 crashing。"""
    # 200 日平盤 30,000 + 55 日緩跌（每日 −100）到 24,500
    closes = [30_000.0] * 200 + [30_000.0 - i * 100 for i in range(1, 56)]
    r = detect_regime(_mk_taiex(closes))
    assert r.level == "cheap"


def test_regime_crashing_when_below_ma_and_month_drop() -> None:
    """跌破 200MA 且月跌 > 10% → 崩盤。"""
    base = 30_000.0
    closes = [base] * 200 + [base] * 5 + [base * (1 - i * 0.025) for i in range(1, 17)]
    r = detect_regime(_mk_taiex(closes))
    assert r.level == "crashing"


def test_regime_insufficient_data_returns_none() -> None:
    df = _mk_taiex([30_000.0] * 100)
    assert detect_regime(df) is None


def test_regime_empty_returns_none() -> None:
    assert detect_regime(pd.DataFrame()) is None


# ─────────────────────────────────────────
# StockTracker
# ─────────────────────────────────────────
def test_stock_tracker_safe_zone() -> None:
    t = StockTracker("2345", "智邦", 2139, 2140, 1925, 2460)
    assert t.diff_to_stop_pct > 5
    assert "🟢" in t.status


def test_stock_tracker_close_to_stop_yellow() -> None:
    t = StockTracker("2345", "智邦", 2139, 2000, 1925, 2460)
    # diff = (2000-1925)/2000 = 3.75% < 5%
    assert "🟠" in t.status


def test_stock_tracker_red_alert_under_2pct() -> None:
    t = StockTracker("2345", "智邦", 2139, 1960, 1925, 2460)
    # diff = (1960-1925)/1960 = 1.79% < 2%
    assert "🔴" in t.status


def test_stock_tracker_triggered_stop() -> None:
    t = StockTracker("2345", "智邦", 2139, 1900, 1925, 2460)
    # current < stop
    assert "🛑" in t.status


def test_stock_tracker_triggered_target() -> None:
    t = StockTracker("2345", "智邦", 2139, 2500, 1925, 2460)
    assert "🎯" in t.status


# ─────────────────────────────────────────
# Allocation drift
# ─────────────────────────────────────────
def test_drift_check_within_tolerance() -> None:
    # 總資產 1000，按目標 28/18/37/17 完美匹配
    actual = {"TW": 280, "US": 180, "Cash": 370, "Other": 170}
    target = {"TW": 28.0, "US": 18.0, "Cash": 37.0, "Other": 17.0}
    total = sum(actual.values())   # = 1000
    checks = check_allocation_drift(actual, target, total)
    tw_check = next(c for c in checks if c.bucket == "TW")
    assert abs(tw_check.deviation) < 1
    assert not tw_check.needs_action


def test_drift_check_triggers_action_when_above_5pct() -> None:
    actual = {"TW": 700_000, "US": 100_000}    # TW 87.5%
    target = {"TW": 30.0, "US": 25.0}
    total = sum(actual.values())
    checks = check_allocation_drift(actual, target, total)
    tw = next(c for c in checks if c.bucket == "TW")
    assert tw.needs_action
    assert tw.deviation > 50


# ─────────────────────────────────────────
# Quarterly check
# ─────────────────────────────────────────
def test_quarterly_first_week_triggers() -> None:
    assert is_quarterly_rebalance_day(date(2026, 4, 1))
    assert is_quarterly_rebalance_day(date(2026, 4, 5))
    assert is_quarterly_rebalance_day(date(2026, 7, 3))
    assert is_quarterly_rebalance_day(date(2026, 1, 7))
    assert is_quarterly_rebalance_day(date(2026, 10, 2))


def test_non_quarterly_months_skipped() -> None:
    assert not is_quarterly_rebalance_day(date(2026, 5, 1))
    assert not is_quarterly_rebalance_day(date(2026, 8, 5))


def test_quarterly_day_after_first_week_skipped() -> None:
    assert not is_quarterly_rebalance_day(date(2026, 4, 8))
    assert not is_quarterly_rebalance_day(date(2026, 4, 15))


# ─────────────────────────────────────────
# Render
# ─────────────────────────────────────────
def test_render_full_section() -> None:
    regime = detect_regime(_mk_taiex([20_000.0] * 200 + [38_500.0]))
    trackers = [StockTracker("2345", "智邦", 2139, 2140, 1925, 2460)]
    md = render_allocation_section(regime, trackers, drift_checks=None, is_rebalance_day=False)
    assert "💰 全球配置 + 部位建議" in md
    assert "過熱" in md or "overheated" in md.lower()
    assert "2345" in md
    assert "🟢" in md or "🛡" in md


def test_render_includes_rebalance_when_quarterly() -> None:
    regime = detect_regime(_mk_taiex([20_000.0] * 200 + [38_500.0]))
    drifts = [
        AllocationCheck("US", 18.0, 30.0, 12.0),     # 偏離 +12% → 觸發
        AllocationCheck("Cash", 37.0, 35.0, -2.0),   # 在 ±5% → 不顯示
    ]
    md = render_allocation_section(regime, [], drift_checks=drifts, is_rebalance_day=True)
    assert "🔄 季度再平衡" in md
    assert "US" in md
    # Cash 偏離 < 5%，不應出現在「需動作」清單
    rebalance_section = md.split("🔄 季度再平衡")[1]
    assert "賣出" in rebalance_section   # US 偏多要賣
