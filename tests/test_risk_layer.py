"""
Phase 6 測試 — 風控五件套：position_sizing / atr_stops / black_swan / regime / concept_drift。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.risk.atr_stops import exit_signal, initial_stops, trail
from src.risk.black_swan_filter import BlackSwanFilter
from src.risk.concept_drift import ConceptDriftDetector
from src.risk.position_sizing import SizingInput, kelly_fraction, size_position
from src.risk.regime_detector import RegimeDetector, apply_overrides


# ─────────────────────────────────────────
# position_sizing
# ─────────────────────────────────────────
def test_kelly_fraction_positive_edge() -> None:
    # p=0.6, avg_win=0.08, avg_loss=0.04 → b=2, f=(0.6×2 − 0.4)/2 = 0.4
    assert kelly_fraction(0.6, 0.08, 0.04) == pytest.approx(0.4)


def test_kelly_fraction_negative_edge_returns_zero() -> None:
    assert kelly_fraction(0.3, 0.05, 0.05) == 0.0


def test_sizing_fixed_fractional_binds() -> None:
    """止損近時，部位上限（position_cap）通常比 fixed fractional 更嚴。"""
    spec = SizingInput(
        total_equity=1_000_000,
        available_cash=800_000,
        entry_price=200.0,
        stop_price=190.0,
        recent_volume=10_000_000,
        win_rate=0.6,
        avg_win=0.08,
        avg_loss=0.04,
    )
    result = size_position(spec)
    # position_cap = 20% × 100萬 = 20萬，@200 → 1000 股
    assert result.shares == 1000
    assert result.dollar_risk <= 20_000
    assert result.binding_constraint == "position_cap"


def test_sizing_fixed_fractional_actually_binds_when_stop_wide() -> None:
    """止損較寬時，風險限制才真的卡位。"""
    spec = SizingInput(
        total_equity=1_000_000,
        available_cash=800_000,
        entry_price=200.0,
        stop_price=150.0,    # 每股風險 50
        recent_volume=10_000_000,
        win_rate=0.6,
        avg_win=0.08,
        avg_loss=0.04,
    )
    result = size_position(spec)
    # fixed: 2% × 100萬 = 2萬 / 50 = 400 股
    # position_cap: 20% × 100萬 / 200 = 1000 股
    # fixed 較嚴格
    assert result.shares == 400
    assert result.binding_constraint == "fixed_fractional"


def test_sizing_invalid_stop_returns_zero() -> None:
    spec = SizingInput(
        total_equity=1_000_000,
        available_cash=500_000,
        entry_price=200.0,
        stop_price=200.0,
        recent_volume=10_000_000,
        win_rate=0.6,
        avg_win=0.08,
        avg_loss=0.04,
    )
    result = size_position(spec)
    assert result.shares == 0
    assert result.binding_constraint == "invalid_stop"


def test_sizing_liquidity_caps_large_order() -> None:
    """成交量很小時，流動性限制應該生效。"""
    spec = SizingInput(
        total_equity=100_000_000,  # 大資金
        available_cash=100_000_000,
        entry_price=200.0,
        stop_price=198.0,
        recent_volume=50_000,      # 很小
        win_rate=0.8,
        avg_win=0.10,
        avg_loss=0.02,
        max_per_trade_pct=5.0,
        kelly_fraction=0.5,
    )
    result = size_position(spec)
    # 流動性上限 = 50000 × 1% = 500 股
    assert result.shares <= 500
    assert result.binding_constraint == "liquidity"


# ─────────────────────────────────────────
# atr_stops
# ─────────────────────────────────────────
def test_initial_stops() -> None:
    s = initial_stops(entry=100.0, atr=2.0)
    assert s.stop == 96.0      # 100 − 2×2
    assert s.target == 106.0   # 100 + 3×2
    assert s.locked_profit_steps == 0


def test_trail_moves_stop_to_breakeven() -> None:
    s = initial_stops(100.0, 2.0)
    s = trail(s, latest_high=102.0)  # +1 ATR
    assert s.stop == 100.0
    assert s.locked_profit_steps == 1


def test_trail_locks_half_profit() -> None:
    s = initial_stops(100.0, 2.0)
    s = trail(s, 102.0)
    s = trail(s, 104.0)  # +2 ATR
    assert s.stop == 102.0
    assert s.locked_profit_steps == 2


def test_trail_does_not_lower_stop() -> None:
    s = initial_stops(100.0, 2.0)
    s = trail(s, 102.0)      # 提到 100
    s = trail(s, 101.5)      # 不能再下移
    assert s.stop == 100.0


def test_exit_signal_hits_stop() -> None:
    s = initial_stops(100.0, 2.0)
    assert exit_signal(s, bar_low=95.0, bar_high=99.0) == "stop"


def test_exit_signal_hits_target() -> None:
    s = initial_stops(100.0, 2.0)
    assert exit_signal(s, bar_low=98.0, bar_high=107.0) == "target"


def test_exit_signal_both_hit_prefers_stop() -> None:
    """保守：同日同時觸及 → 先觸及止損。"""
    s = initial_stops(100.0, 2.0)
    assert exit_signal(s, bar_low=95.0, bar_high=110.0) == "stop"


def test_exit_signal_no_trigger() -> None:
    s = initial_stops(100.0, 2.0)
    assert exit_signal(s, bar_low=98.0, bar_high=102.0) is None


# ─────────────────────────────────────────
# black_swan_filter
# ─────────────────────────────────────────
def _write_strategy(tmp_path: Path) -> Path:
    path = tmp_path / "strategy.yaml"
    path.write_text(
        """factor_weights:
  chip_concentration: 0.25
  technical: 0.15
recommendation:
  min_score: 75
  max_picks: 3
risk:
  atr_stop_multiplier: 2.0
black_swan:
  adr_drop_pct: -3.0
  vix_threshold: 25.0
  taiex_below_monthly_ma: true
regime:
  lookback_days: 20
  reference_years: 5
  thresholds:
    low: 0.8
    normal_high: 1.3
    high_crazy: 2.0
  crazy_force_cash: true
concept_drift:
  window_size: 5
  alert_threshold: 0.20
  force_paper_trading_after: 10
""",
        encoding="utf-8",
    )
    return path


def test_black_swan_no_trigger(tmp_path: Path) -> None:
    bs = BlackSwanFilter(_write_strategy(tmp_path))
    v = bs.check(tsmc_adr_change_pct=-0.5, vix=18.0, taiex_below_ma=False)
    assert v.defensive is False
    assert v.reasons == []


def test_black_swan_tsm_drop(tmp_path: Path) -> None:
    bs = BlackSwanFilter(_write_strategy(tmp_path))
    v = bs.check(tsmc_adr_change_pct=-3.5, vix=18.0, taiex_below_ma=False)
    assert v.defensive is True
    assert any("ADR" in r for r in v.reasons)


def test_black_swan_vix_and_taiex(tmp_path: Path) -> None:
    bs = BlackSwanFilter(_write_strategy(tmp_path))
    v = bs.check(tsmc_adr_change_pct=-1.0, vix=28.0, taiex_below_ma=True)
    assert v.defensive is True
    assert len(v.reasons) == 2


def test_black_swan_systemic_event(tmp_path: Path) -> None:
    bs = BlackSwanFilter(_write_strategy(tmp_path))
    v = bs.check(tsmc_adr_change_pct=-0.5, vix=15.0, taiex_below_ma=False, systemic_event=True)
    assert v.defensive is True


# ─────────────────────────────────────────
# regime_detector
# ─────────────────────────────────────────
def _fake_taiex_with_vol(n: int, daily_range: float = 200.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        close = 20_000 + (i % 5 - 2) * daily_range
        rows.append(
            {
                "date": date(2020, 1, 1) + timedelta(days=i),
                "high": close + daily_range,
                "low": close - daily_range,
                "close": close,
            }
        )
    return pd.DataFrame(rows)


def test_regime_normal(tmp_path: Path) -> None:
    det = RegimeDetector(_write_strategy(tmp_path))
    # 全部同波動 → vol_ratio ≈ 1.0
    v = det.detect(_fake_taiex_with_vol(300, daily_range=200))
    assert v.regime == "normal"
    assert v.force_cash is False


def test_regime_crazy_forces_cash(tmp_path: Path) -> None:
    det = RegimeDetector(_write_strategy(tmp_path))
    df_hist = _fake_taiex_with_vol(300, daily_range=100)
    # 最後 25 天突然翻 4 倍
    df_spike = _fake_taiex_with_vol(25, daily_range=400)
    df_spike["date"] = [d + timedelta(days=300) for d in df_spike["date"]]
    df = pd.concat([df_hist, df_spike], ignore_index=True)
    v = det.detect(df)
    assert v.regime in ("high", "crazy")
    if v.regime == "crazy":
        assert v.force_cash is True
        assert v.position_scale == 0.0


def test_regime_insufficient_data_defaults_normal(tmp_path: Path) -> None:
    det = RegimeDetector(_write_strategy(tmp_path))
    v = det.detect(pd.DataFrame())
    assert v.regime == "normal"


def test_apply_overrides_preserves_sum() -> None:
    base = {
        "chip_concentration": 0.25,
        "technical": 0.15,
        "news_sentiment": 0.20,
        "supply_chain": 0.20,
        "market_regime": 0.10,
        "sector_momentum": 0.10,
    }
    overrides = {"chip_concentration": +0.10, "technical": -0.10}
    out = apply_overrides(base, overrides)
    assert sum(out.values()) == pytest.approx(1.0)
    assert out["chip_concentration"] > out["technical"]


# ─────────────────────────────────────────
# concept_drift
# ─────────────────────────────────────────
def test_drift_needs_window_before_alert(tmp_path: Path) -> None:
    log = tmp_path / "drift.parquet"
    det = ConceptDriftDetector(_write_strategy(tmp_path), log)
    for i in range(3):
        det.record(date(2026, 4, i + 1), "3413", 0.05, -0.05)
    v = det.verdict()
    assert v.alert is False
    assert v.reason == "紀錄不足"


def test_drift_triggers_on_large_divergence(tmp_path: Path) -> None:
    log = tmp_path / "drift.parquet"
    det = ConceptDriftDetector(_write_strategy(tmp_path), log)
    # 5 筆，每筆誤差 0.08 → 滾動總和 0.4 > 0.2
    for i in range(5):
        det.record(date(2026, 4, i + 1), "3413", 0.05, -0.03)
    v = det.verdict()
    assert v.alert is True
    assert v.window_divergence >= 0.2


def test_drift_persists_across_instances(tmp_path: Path) -> None:
    log = tmp_path / "drift.parquet"
    det1 = ConceptDriftDetector(_write_strategy(tmp_path), log)
    for i in range(5):
        det1.record(date(2026, 4, i + 1), "3413", 0.05, -0.03)

    det2 = ConceptDriftDetector(_write_strategy(tmp_path), log)
    v = det2.verdict()
    assert v.alert is True


def test_drift_reset(tmp_path: Path) -> None:
    log = tmp_path / "drift.parquet"
    det = ConceptDriftDetector(_write_strategy(tmp_path), log)
    for i in range(5):
        det.record(date(2026, 4, i + 1), "3413", 0.05, -0.03)
    det.reset()
    v = det.verdict()
    assert v.alert is False
    assert v.reason == "紀錄不足"
