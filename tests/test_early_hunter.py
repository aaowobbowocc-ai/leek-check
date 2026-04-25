"""
Early Hunter 單元測試 — 5 個因子各自正確性 + 整合 scan。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.strategy.early_hunter import (
    EarlyHunterSignal,
    factor_above_200ma,
    factor_market_cap_band,
    factor_momentum_window,
    factor_revenue_acceleration,
    factor_volume_expansion,
    scan_ticker,
)


def _mk_ohlcv(closes: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    rows = [
        {
            "date": date(2024, 1, 1) + timedelta(days=i),
            "open": closes[i], "high": closes[i] * 1.01,
            "low": closes[i] * 0.99, "close": closes[i],
            "volume": volumes[i],
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


# ─────────────────────────────────────────
# factor_momentum_window — 甜蜜區 [30, 80]
# ─────────────────────────────────────────
def test_momentum_in_sweet_spot_scores_high() -> None:
    """12M 漲 55% 落在甜蜜區中央（midpoint = (30+80)/2 = 55）→ 滿分。"""
    closes = [100.0] * 252 + [155.0]
    df = _mk_ohlcv(closes)
    ret, score = factor_momentum_window(df, df.iloc[-1]["date"])
    assert ret == pytest.approx(55.0)
    assert score == pytest.approx(25.0, abs=0.5)


def test_momentum_too_low_scores_zero() -> None:
    """12M 只漲 10% → 未啟動 → 0 分。"""
    closes = [100.0] * 252 + [110.0]
    df = _mk_ohlcv(closes)
    ret, score = factor_momentum_window(df, df.iloc[-1]["date"])
    assert score == 0


def test_momentum_too_high_scores_zero() -> None:
    """12M 漲 200% → 末段，已過甜蜜區 → 0 分。"""
    closes = [100.0] * 252 + [300.0]
    df = _mk_ohlcv(closes)
    ret, score = factor_momentum_window(df, df.iloc[-1]["date"])
    assert score == 0


def test_momentum_insufficient_data() -> None:
    df = _mk_ohlcv([100.0] * 100)
    ret, score = factor_momentum_window(df, df.iloc[-1]["date"])
    assert ret is None and score == 0


# ─────────────────────────────────────────
# factor_volume_expansion
# ─────────────────────────────────────────
def test_volume_expansion_triggers() -> None:
    """近 60D 量是 252D 平均的 2x → 滿分。"""
    n = 252
    vols = [1_000_000] * (n - 60) + [2_500_000] * 60
    df = _mk_ohlcv([100.0] * n, vols)
    ratio, score = factor_volume_expansion(df, df.iloc[-1]["date"])
    assert ratio > 1.5
    assert score > 15


def test_volume_no_expansion_scores_zero() -> None:
    df = _mk_ohlcv([100.0] * 252, [1_000_000] * 252)
    ratio, score = factor_volume_expansion(df, df.iloc[-1]["date"])
    assert score == 0


# ─────────────────────────────────────────
# factor_revenue_acceleration
# ─────────────────────────────────────────
def test_revenue_acceleration_triggers() -> None:
    """近 3 月 YoY 平均 +50%、過去 6 月 YoY 平均 +20% → 加速 +30pp 滿分。"""
    rows = []
    for i in range(9):
        yoy = 20.0 if i < 6 else 50.0
        rows.append({
            "date": date(2024, 1, 1) + timedelta(days=i * 30),
            "revenue_yoy": yoy,
        })
    rev = pd.DataFrame(rows)
    accel, score = factor_revenue_acceleration(rev, date(2025, 12, 1))
    assert accel == pytest.approx(30.0)
    assert score == pytest.approx(25.0)


def test_revenue_deceleration_scores_zero() -> None:
    """近 3 月 YoY 反而比過去差 → 0 分。"""
    rows = []
    for i in range(9):
        yoy = 50.0 if i < 6 else 10.0
        rows.append({
            "date": date(2024, 1, 1) + timedelta(days=i * 30),
            "revenue_yoy": yoy,
        })
    rev = pd.DataFrame(rows)
    _, score = factor_revenue_acceleration(rev, date(2025, 12, 1))
    assert score == 0


def test_revenue_empty_returns_none() -> None:
    accel, score = factor_revenue_acceleration(pd.DataFrame(), date(2025, 1, 1))
    assert accel is None and score == 0


# ─────────────────────────────────────────
# factor_above_200ma
# ─────────────────────────────────────────
def test_above_200ma_scores() -> None:
    closes = [100.0] * 200 + [120.0]   # +20% above 200MA
    df = _mk_ohlcv(closes)
    above, score = factor_above_200ma(df, df.iloc[-1]["date"])
    assert above is True
    assert score == 15


def test_below_200ma_zero() -> None:
    closes = [100.0] * 200 + [95.0]
    df = _mk_ohlcv(closes)
    above, score = factor_above_200ma(df, df.iloc[-1]["date"])
    assert above is False
    assert score == 0


# ─────────────────────────────────────────
# factor_market_cap_band
# ─────────────────────────────────────────
def test_market_cap_in_band() -> None:
    in_band, score = factor_market_cap_band(150)
    assert in_band is True
    assert score == 10


def test_market_cap_out_of_band() -> None:
    _, score = factor_market_cap_band(2000)   # too big
    assert score == 0
    _, score2 = factor_market_cap_band(10)    # too small
    assert score2 == 0


def test_market_cap_unknown_gives_neutral() -> None:
    """無市值資料 → 中間 5 分（不一票否決）。"""
    _, score = factor_market_cap_band(None)
    assert score == 5


# ─────────────────────────────────────────
# Integration: scan_ticker
# ─────────────────────────────────────────
def test_scan_ticker_sweet_spot_triggers() -> None:
    """
    完美組合：
      - 12M 漲 +50%（甜蜜區）
      - 量擴 2x
      - 在 200MA 上 +20%
      - 市值 150 億
    """
    # 252 日前 100，緩漲 60 日到 159（12M = +59%，落在甜蜜區）
    closes = [100.0] * 192 + [100.0 + i * 1.0 for i in range(60)]
    vols = [1_000_000] * 192 + [2_500_000] * 60
    df = _mk_ohlcv(closes, vols)
    sig = scan_ticker(
        ticker="TEST", ohlcv=df, revenue=None,
        as_of=df.iloc[-1]["date"],
        market_cap_btw=150,
    )
    assert sig is not None
    assert sig.score > 50
    # 信心：mom + vol + ma + cap = 約 22 + 18 + 15 + 10 = 65 → triggered
    assert sig.triggered


def test_scan_ticker_misses_when_too_late() -> None:
    """已漲 +200% 末段 → momentum 不給分，整體可能不觸發。"""
    closes = [100.0] * 252 + [300.0]   # 12M +200%
    df = _mk_ohlcv(closes)
    sig = scan_ticker(
        ticker="TEST", ohlcv=df, revenue=None,
        as_of=df.iloc[-1]["date"],
        market_cap_btw=150,
    )
    # mom=0, vol=0(平), accel=0, ma=15, cap=10 = 25
    assert sig.score < 60
    assert not sig.triggered


def test_scan_ticker_handles_short_history() -> None:
    """資料不足 200 日 → 仍回 signal 但分數低。"""
    closes = [100.0] * 100
    df = _mk_ohlcv(closes)
    sig = scan_ticker(
        ticker="TEST", ohlcv=df, revenue=None,
        as_of=df.iloc[-1]["date"], market_cap_btw=None,
    )
    assert sig is not None
    assert not sig.triggered
