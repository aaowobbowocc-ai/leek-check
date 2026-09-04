"""
Volume Anomaly Detector 單元測試 — Phase 18b。

測試覆蓋：
  1. strict_rolling_modified_z 嚴格不偷看未來
  2. detect_direction 內盤比 + 開收盤組合
  3. is_in_ex_dividend_window 邊界
  4. compute_anomaly_score 各狀態
  5. scan_volume_anomaly 觸發 / 否決路徑
  6. append_to_history + lookup_recent_anomalies 雙重確認流程
"""
from __future__ import annotations

import math
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.strategy.volume_anomaly import (
    BoardThresholds,
    VolumeAnomalySignal,
    append_to_history,
    compute_anomaly_score,
    detect_direction,
    is_in_ex_dividend_window,
    lookup_recent_anomalies,
    scan_volume_anomaly,
    strict_rolling_modified_z,
)


# ─────────────────────────────────────────
# 工具：構造 OHLCV
# ─────────────────────────────────────────
def _mk_ohlcv(
    n_days: int = 250,
    base_volume: int = 1_000_000,
    base_close: float = 50.0,
    volume_spikes: dict[int, float] | None = None,
    close_path: list[float] | None = None,
    start: date = date(2025, 1, 1),
) -> pd.DataFrame:
    """
    產生 n_days 天的 OHLCV。
    volume_spikes: {day_index: spike_multiplier}，特定天的量乘以 multiplier。
    close_path: 自訂收盤價序列（長度需 = n_days）。
    """
    rng = np.random.default_rng(42)
    rows = []
    for i in range(n_days):
        vol = int(base_volume * rng.lognormal(mean=0, sigma=0.2))
        if volume_spikes and i in volume_spikes:
            vol = int(vol * volume_spikes[i])
        close = base_close if close_path is None else close_path[i]
        rows.append({
            "date": start + timedelta(days=i),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": vol,
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────
# strict_rolling_modified_z
# ─────────────────────────────────────────
def test_modified_z_no_lookahead() -> None:
    """t 時點的 z 完全不能用到 t 之後的資料。"""
    series_a = pd.Series(np.log(np.array([1_000_000] * 100, dtype=float)))
    z_a = strict_rolling_modified_z(series_a, window=60, min_periods=30)

    # 現在後 50 天加入暴量，前 60 天的 z 不該變
    arr = np.array([1_000_000] * 60 + [10_000_000] * 40, dtype=float)
    series_b = pd.Series(np.log(arr))
    z_b = strict_rolling_modified_z(series_b, window=60, min_periods=30)

    # 第 30-59 天的 z（baseline 都是過去的常態量）應該與 z_a 相同
    pd.testing.assert_series_equal(z_a.iloc[30:60], z_b.iloc[30:60])


def test_modified_z_detects_volume_spike() -> None:
    """常態量中突然 10x → z 應顯著為正。
    需要歷史有些微波動讓 MAD > 0，否則 z 無法定義。"""
    rng = np.random.default_rng(0)
    history = 1_000_000 * rng.lognormal(mean=0, sigma=0.1, size=80)
    arr = np.concatenate([history, [10_000_000]])
    series = pd.Series(np.log(arr))
    z = strict_rolling_modified_z(series, window=60, min_periods=30)
    assert z.iloc[-1] > 5.0   # log(10) ≈ 2.3，MAD 小 → z 大


def test_modified_z_returns_nan_when_mad_zero() -> None:
    """若歷史量完全相同（MAD = 0）→ z 應為 NaN，不可除以 0。"""
    arr = np.array([1_000_000] * 100, dtype=float)
    series = pd.Series(np.log(arr))
    z = strict_rolling_modified_z(series, window=60, min_periods=30)
    # 後段應全部為 NaN（MAD = 0）
    assert z.iloc[60:].isna().all()


# ─────────────────────────────────────────
# detect_direction
# ─────────────────────────────────────────
def test_direction_buying() -> None:
    assert detect_direction(0.40, close=51.0, open_price=50.0) == "buying"


def test_direction_selling() -> None:
    assert detect_direction(0.60, close=49.0, open_price=50.0) == "selling"


def test_direction_unknown_when_no_data() -> None:
    assert detect_direction(None, close=51.0, open_price=50.0) == "unknown"
    assert detect_direction(float("nan"), close=51.0, open_price=50.0) == "unknown"


def test_direction_unknown_when_inner_ratio_in_grey_zone() -> None:
    """內盤比 0.45-0.55 之間 → 不確定。"""
    assert detect_direction(0.50, close=51.0, open_price=50.0) == "unknown"


def test_direction_buying_rejected_when_price_drops_too_much() -> None:
    """內盤比低但價格大跌 → 仍判 unknown（買盤但被打下來）。"""
    assert detect_direction(0.40, close=48.0, open_price=50.0) == "unknown"


# ─────────────────────────────────────────
# is_in_ex_dividend_window
# ─────────────────────────────────────────
def test_ex_dividend_window_within_5_days() -> None:
    df = pd.DataFrame({"ticker": ["3595"], "ex_date": [date(2026, 4, 22)]})
    assert is_in_ex_dividend_window("3595", date(2026, 4, 25), df) is True
    assert is_in_ex_dividend_window("3595", date(2026, 4, 17), df) is True
    assert is_in_ex_dividend_window("3595", date(2026, 4, 27), df) is True


def test_ex_dividend_window_outside() -> None:
    df = pd.DataFrame({"ticker": ["3595"], "ex_date": [date(2026, 4, 22)]})
    assert is_in_ex_dividend_window("3595", date(2026, 5, 10), df) is False


def test_ex_dividend_window_other_ticker_not_polluted() -> None:
    df = pd.DataFrame({"ticker": ["3595"], "ex_date": [date(2026, 4, 22)]})
    assert is_in_ex_dividend_window("2330", date(2026, 4, 22), df) is False


def test_ex_dividend_window_handles_none() -> None:
    assert is_in_ex_dividend_window("3595", date(2026, 4, 22), None) is False


# ─────────────────────────────────────────
# BoardThresholds
# ─────────────────────────────────────────
def test_otc_thresholds_stricter_than_twse() -> None:
    twse = BoardThresholds.for_board("TWSE")
    otc = BoardThresholds.for_board("OTC")
    assert otc.z_min > twse.z_min
    assert otc.market_cap_floor_btw > twse.market_cap_floor_btw


# ─────────────────────────────────────────
# compute_anomaly_score
# ─────────────────────────────────────────
def test_score_perfect_signal() -> None:
    """z=3.0 + 滑動窗口 5 天 + buying + 站上 200MA → 滿分附近。"""
    score, breakdown = compute_anomaly_score(z=3.0, days_z_above_2=5,
                                              direction="buying", above_200ma=True)
    assert score == 100.0
    assert breakdown["direction"] == 25.0


def test_score_selling_zeros_direction() -> None:
    """selling 直接 direction = 0 — 確認方向過濾的「致命否決」效果。"""
    score, breakdown = compute_anomaly_score(z=3.0, days_z_above_2=5,
                                              direction="selling", above_200ma=True)
    assert breakdown["direction"] == 0.0
    assert score == 75.0   # 100 - 25


def test_score_unknown_direction_partial_credit() -> None:
    """資料缺失 → 給少分，不一票否決。"""
    score, breakdown = compute_anomaly_score(z=3.0, days_z_above_2=5,
                                              direction="unknown", above_200ma=True)
    assert 0 < breakdown["direction"] < 25.0


def test_score_extreme_z_penalized() -> None:
    """z >= 4.0 反而扣分（通常已暴漲，太晚）。"""
    s4, _ = compute_anomaly_score(z=4.5, days_z_above_2=5, direction="buying", above_200ma=True)
    s3, _ = compute_anomaly_score(z=3.0, days_z_above_2=5, direction="buying", above_200ma=True)
    assert s4 < s3


# ─────────────────────────────────────────
# scan_volume_anomaly — 整合
# ─────────────────────────────────────────
def test_scan_returns_none_when_data_too_short() -> None:
    df = _mk_ohlcv(n_days=50)
    result = scan_volume_anomaly("3595", df, as_of=df.iloc[-1]["date"])
    assert result is None


def test_scan_triggers_on_clear_anomaly() -> None:
    """連續 5 天暴量 + 內盤比低 → 應觸發（用較低 threshold 驗證流程）。
    注意：新規則下 z >= 4.0 z_score=0，所以 8x spike 反而會降分。
    這個測試驗證的是「能識別異常 + 流程跑通」，不是甜蜜區判定。"""
    n = 250
    spikes = {n - i: 8.0 for i in range(1, 8)}
    df = _mk_ohlcv(n_days=n, volume_spikes=spikes)
    inner = pd.DataFrame({
        "date": [df.iloc[-1]["date"]],
        "inner_ratio": [0.40],
    })
    result = scan_volume_anomaly(
        "3595", df, as_of=df.iloc[-1]["date"],
        inner_outer=inner, market_cap_btw=80.0, board="OTC",
        score_threshold=40.0,   # 降低門檻僅為驗證流程
    )
    assert result is not None
    assert result.modified_z > 2.5
    assert result.days_z_above_2 >= 4
    assert result.direction == "buying"
    assert result.triggered, f"應觸發但被否決：{result.rejection_reason}"


def test_scan_rejects_selling_pressure() -> None:
    """量爆但內盤比 > 0.55（出貨）→ 必須否決。"""
    n = 250
    spikes = {n - i: 8.0 for i in range(1, 8)}
    df = _mk_ohlcv(n_days=n, volume_spikes=spikes)
    inner = pd.DataFrame({
        "date": [df.iloc[-1]["date"]],
        "inner_ratio": [0.65],
    })
    result = scan_volume_anomaly(
        "3595", df, as_of=df.iloc[-1]["date"],
        inner_outer=inner, market_cap_btw=80.0, board="OTC",
    )
    assert result is not None
    assert not result.triggered
    assert "出貨" in (result.rejection_reason or "")


def test_scan_rejects_within_ex_dividend_window() -> None:
    """除權息前後 5 日內必須否決，避免污染。"""
    n = 250
    spikes = {n - i: 8.0 for i in range(1, 8)}
    df = _mk_ohlcv(n_days=n, volume_spikes=spikes)
    today = df.iloc[-1]["date"]
    inner = pd.DataFrame({"date": [today], "inner_ratio": [0.40]})
    ex_div = pd.DataFrame({"ticker": ["3595"], "ex_date": [today - timedelta(days=2)]})

    result = scan_volume_anomaly(
        "3595", df, as_of=today,
        inner_outer=inner, market_cap_btw=80.0, board="OTC",
        ex_dividend_dates=ex_div,
    )
    assert result is not None
    assert not result.triggered
    assert "除權息" in (result.rejection_reason or "")


def test_scan_rejects_low_liquidity_otc() -> None:
    """OTC 日均量 < 500 張必須否決。"""
    n = 250
    spikes = {n - i: 8.0 for i in range(1, 8)}
    df = _mk_ohlcv(n_days=n, base_volume=100, volume_spikes=spikes)
    today = df.iloc[-1]["date"]
    inner = pd.DataFrame({"date": [today], "inner_ratio": [0.40]})

    result = scan_volume_anomaly(
        "3595", df, as_of=today,
        inner_outer=inner, market_cap_btw=80.0, board="OTC",
    )
    assert result is not None
    assert not result.triggered
    assert "流動性" in (result.rejection_reason or "")


def test_scan_rejects_already_rallied() -> None:
    """5 日漲幅 > 15% → 已進末段，否決。"""
    n = 250
    closes = [50.0] * (n - 6) + [50.0, 51.0, 53.0, 55.0, 58.0, 62.0]   # 5 日 +24%
    spikes = {n - i: 8.0 for i in range(1, 8)}
    df = _mk_ohlcv(n_days=n, volume_spikes=spikes, close_path=closes)
    today = df.iloc[-1]["date"]
    inner = pd.DataFrame({"date": [today], "inner_ratio": [0.40]})

    result = scan_volume_anomaly(
        "3595", df, as_of=today,
        inner_outer=inner, market_cap_btw=80.0, board="OTC",
    )
    assert result is not None
    assert not result.triggered
    assert "末段" in (result.rejection_reason or "")


def test_scan_handles_missing_inner_ratio() -> None:
    """沒有內盤比資料時，direction = unknown，不一票否決但分數降低。"""
    n = 250
    spikes = {n - i: 8.0 for i in range(1, 8)}
    df = _mk_ohlcv(n_days=n, volume_spikes=spikes)

    result = scan_volume_anomaly(
        "3595", df, as_of=df.iloc[-1]["date"],
        inner_outer=None, market_cap_btw=80.0, board="OTC",
    )
    assert result is not None
    assert result.direction == "unknown"
    # 分數應該被「unknown」拉低，可能不觸發
    assert result.breakdown["direction"] < 25.0


# ─────────────────────────────────────────
# 歷史紀錄 + 雙重確認查詢
# ─────────────────────────────────────────
def test_append_history_and_lookup_for_double_confirmation() -> None:
    """模擬 Early Hunter 雙重確認流程：
    1. T0 (4 個月前) 偵測到吃貨期 → append_to_history
    2. T1 (現在) Early Hunter 觸發 → lookup_recent_anomalies 查到 T0 紀錄
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "anomaly_history.parquet"

        # T0 訊號（4 個月前）
        t0 = date(2025, 12, 25)
        signals_t0 = [
            VolumeAnomalySignal(
                ticker="3595", as_of=t0, board="OTC",
                modified_z=3.2, median_log_vol_60d=14.5, mad_log_vol_60d=0.2,
                days_z_above_2=5, days_z_above_3=2, max_z_recent_10d=3.5,
                inner_ratio=0.40, direction="buying",
                close=120.0, price_change_5d_pct=2.0, above_200ma=True,
                avg_volume_60d=2000, market_cap_btw=80.0,
                is_ex_dividend_window=False,
                score=85.0, triggered=True,
            )
        ]
        append_to_history(history_path, signals_t0)

        # 現在查回去
        today = date(2026, 4, 25)
        matches = lookup_recent_anomalies(
            history_path, "3595", today,
            lookback_min_days=90, lookback_max_days=180,
        )
        assert t0 in matches


def test_append_history_skips_untriggered_signals() -> None:
    """未觸發的訊號不應寫入歷史（避免雜訊）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "anomaly_history.parquet"

        signals = [
            VolumeAnomalySignal(
                ticker="3595", as_of=date(2026, 4, 25), board="OTC",
                modified_z=1.5, median_log_vol_60d=14.5, mad_log_vol_60d=0.2,
                days_z_above_2=2, days_z_above_3=0, max_z_recent_10d=1.8,
                inner_ratio=0.50, direction="unknown",
                close=120.0, price_change_5d_pct=2.0, above_200ma=True,
                avg_volume_60d=2000, market_cap_btw=80.0,
                is_ex_dividend_window=False,
                score=30.0, triggered=False,
                rejection_reason="z 不足",
            )
        ]
        append_to_history(history_path, signals)

        assert not history_path.exists()


def test_lookup_returns_empty_when_no_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "anomaly_history.parquet"
        result = lookup_recent_anomalies(history_path, "3595", date(2026, 4, 25))
        assert result == []


def test_lookup_excludes_too_recent_anomalies() -> None:
    """只在 T-90 ~ T-180 區間，太近（< 90 天）的不算雙重確認。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "anomaly_history.parquet"

        recent = date(2026, 4, 1)   # 只距今 24 天，太近
        signals = [
            VolumeAnomalySignal(
                ticker="3595", as_of=recent, board="OTC",
                modified_z=3.0, median_log_vol_60d=14.0, mad_log_vol_60d=0.2,
                days_z_above_2=5, days_z_above_3=2, max_z_recent_10d=3.0,
                inner_ratio=0.40, direction="buying",
                close=120.0, price_change_5d_pct=2.0, above_200ma=True,
                avg_volume_60d=2000, market_cap_btw=80.0,
                is_ex_dividend_window=False,
                score=85.0, triggered=True,
            )
        ]
        append_to_history(history_path, signals)

        matches = lookup_recent_anomalies(
            history_path, "3595", date(2026, 4, 25),
            lookback_min_days=90, lookback_max_days=180,
        )
        assert matches == []
