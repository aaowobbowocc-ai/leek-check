"""
Quality Momentum 單元測試：
  1. 每個因子各自的計算正確
  2. look-ahead 防線（as_of 之後的資料不能進入）
  3. 橫斷面 z-score 合成
  4. 資料不足時回 None 而非 crash
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.strategy.quality_momentum import (
    FactorWeights,
    compute_ticker_factors,
    cross_sectional_score,
    factor_low_vol_60d,
    factor_momentum_12m,
    factor_quality_roe,
    factor_revenue_growth_yoy,
    factor_value_earnings_yield,
    zscore,
)


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────
def _mk_ohlcv(start: date, closes: list[float]) -> pd.DataFrame:
    rows = [
        {
            "date": start + timedelta(days=i),
            "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
            "volume": 1_000_000,
        }
        for i, c in enumerate(closes)
    ]
    return pd.DataFrame(rows)


def _mk_per(start: date, pe_vals: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": start + timedelta(days=i), "per": pe, "pbr": 2.0, "dividend_yield": 3.0}
            for i, pe in enumerate(pe_vals)
        ]
    )


def _mk_financials(start: date, roe_vals: list[float]) -> pd.DataFrame:
    # 用不同季度日期
    rows = []
    for i, r in enumerate(roe_vals):
        # 模擬四季 quarterly 財報
        rows.append(
            {
                "date": start + timedelta(days=i * 90),
                "type": "ROE",
                "value": r,
            }
        )
    return pd.DataFrame(rows)


def _mk_revenue(start: date, yoy_vals: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": start + timedelta(days=i * 30),
                "revenue": 1_000_000 * (1 + y / 100),
                "revenue_yoy": y,
                "revenue_mom": 0.0,
            }
            for i, y in enumerate(yoy_vals)
        ]
    )


# ─────────────────────────────────────────
# Momentum
# ─────────────────────────────────────────
def test_momentum_positive_trend() -> None:
    closes = [100.0 + i * 0.5 for i in range(260)]
    df = _mk_ohlcv(date(2024, 1, 1), closes)
    r = factor_momentum_12m(df, as_of=df.iloc[-1]["date"])
    assert r is not None and r > 0.5      # 漲幅大


def test_momentum_insufficient_data() -> None:
    df = _mk_ohlcv(date(2024, 1, 1), [100.0] * 200)
    assert factor_momentum_12m(df, as_of=date(2024, 7, 20)) is None


def test_momentum_look_ahead_guard() -> None:
    """as_of 之後的 bar 不應被算入。"""
    closes = [100.0] * 260 + [500.0] * 30
    df = _mk_ohlcv(date(2024, 1, 1), closes)
    cutoff = df.iloc[259]["date"]
    r = factor_momentum_12m(df, as_of=cutoff)
    # 前 260 天全平，動能應接近 0
    assert r is not None and abs(r) < 0.01


# ─────────────────────────────────────────
# Low Vol
# ─────────────────────────────────────────
def test_low_vol_stable_series() -> None:
    # 純線性增長 → 日報酬率近乎固定 → 波動極低
    closes = [100.0 + i * 0.1 for i in range(100)]
    df = _mk_ohlcv(date(2024, 1, 1), closes)
    v = factor_low_vol_60d(df, as_of=df.iloc[-1]["date"])
    assert v is not None
    # -annualized_vol 趨近 0（但會是小負數）
    assert v > -0.5


def test_low_vol_insufficient() -> None:
    df = _mk_ohlcv(date(2024, 1, 1), [100.0] * 30)
    assert factor_low_vol_60d(df, as_of=date(2024, 1, 30)) is None


# ─────────────────────────────────────────
# Value (Earnings Yield)
# ─────────────────────────────────────────
def test_value_earnings_yield_basic() -> None:
    df = _mk_per(date(2024, 1, 1), [10.0] * 30)
    ey = factor_value_earnings_yield(df, as_of=date(2024, 2, 1))
    assert ey == pytest.approx(0.1)


def test_value_skips_negative_pe() -> None:
    """賠錢公司 PE 通常負或 0 → None。"""
    df = _mk_per(date(2024, 1, 1), [-5.0] * 30)
    assert factor_value_earnings_yield(df, as_of=date(2024, 2, 1)) is None


def test_value_empty_returns_none() -> None:
    assert factor_value_earnings_yield(pd.DataFrame(), as_of=date(2024, 1, 1)) is None


# ─────────────────────────────────────────
# Quality (ROE)
# ─────────────────────────────────────────
def test_roe_basic_average() -> None:
    """4 季 ROE 平均。"""
    df = _mk_financials(date(2023, 1, 1), [10, 12, 14, 16])
    roe = factor_quality_roe(df, as_of=date(2024, 6, 1))
    assert roe == pytest.approx(13.0)   # 平均


def test_roe_respects_announcement_lag() -> None:
    """3 個月內的財報不應被使用（避免 look-ahead）。"""
    # 今天剛公告 ROE=20 → 不能偷用
    df = pd.DataFrame(
        [
            {"date": date(2024, 6, 15), "type": "ROE", "value": 20.0},
            {"date": date(2024, 1, 1), "type": "ROE", "value": 10.0},
        ]
    )
    roe = factor_quality_roe(df, as_of=date(2024, 6, 20))
    # 只能拿到 1/1 那筆（90 天外）
    assert roe == pytest.approx(10.0)


# ─────────────────────────────────────────
# Revenue growth
# ─────────────────────────────────────────
def test_revenue_growth_basic() -> None:
    df = _mk_revenue(date(2023, 1, 1), [10.0, 15.0, 20.0, 25.0])
    g = factor_revenue_growth_yoy(df, as_of=date(2024, 6, 1))
    # 最新的那筆（120 天後 = 2023-05-01 左右）yoy 應在序列中
    assert g is not None


def test_revenue_respects_announcement_lag() -> None:
    """10 天內公告的營收不能用。"""
    df = pd.DataFrame(
        [
            {"date": date(2024, 6, 15), "revenue_yoy": 30.0, "revenue": 1},
            {"date": date(2024, 5, 10), "revenue_yoy": 10.0, "revenue": 1},
        ]
    )
    g = factor_revenue_growth_yoy(df, as_of=date(2024, 6, 20))
    # 只能用 5/10 那筆（10 天外）
    assert g == pytest.approx(10.0)


# ─────────────────────────────────────────
# z-score + 橫斷面合成
# ─────────────────────────────────────────
def test_zscore_normal_case() -> None:
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    z = zscore(s)
    assert z.mean() == pytest.approx(0.0, abs=1e-6)
    assert z.std() == pytest.approx(1.0, abs=0.3)


def test_zscore_handles_constant_series() -> None:
    s = pd.Series([5.0, 5.0, 5.0])
    z = zscore(s)
    assert (z == 0.0).all()


def test_zscore_handles_nan() -> None:
    s = pd.Series([1.0, 2.0, None, 4.0, 5.0])
    z = zscore(s)
    assert z.iloc[2] == 0.0    # NaN 被填 0（中性）


def test_cross_sectional_scoring_picks_right_leaders() -> None:
    """A 在每個因子都最強 → 總分最高。"""
    df = pd.DataFrame(
        {
            "momentum": [0.5, 0.1, -0.1],
            "quality_roe": [30.0, 15.0, 5.0],
            "value_pe": [0.15, 0.08, 0.03],
            "low_vol": [-0.1, -0.2, -0.3],
            "revenue_growth": [50.0, 10.0, -5.0],
        },
        index=["A", "B", "C"],
    )
    scored = cross_sectional_score(df)
    assert scored["score"].idxmax() == "A"
    assert scored["score"].idxmin() == "C"


# ─────────────────────────────────────────
# 整合：compute_ticker_factors
# ─────────────────────────────────────────
def test_compute_ticker_factors_returns_all_keys() -> None:
    as_of = date(2024, 6, 1)
    factors = compute_ticker_factors(
        ticker="TEST",
        as_of=as_of,
        ohlcv=_mk_ohlcv(date(2023, 1, 1), [100.0 + i * 0.3 for i in range(300)]),
        per_pbr=_mk_per(date(2024, 5, 1), [15.0] * 20),
        financials=_mk_financials(date(2023, 1, 1), [10, 12, 14]),
        revenue=_mk_revenue(date(2023, 1, 1), [8.0, 10.0, 12.0, 14.0]),
    )
    assert set(factors.keys()) == {
        "momentum", "quality_roe", "value_pe", "low_vol", "revenue_growth"
    }


def test_compute_ticker_factors_tolerates_missing_data() -> None:
    """缺某些欄位時不應 crash，只是該欄 None。"""
    factors = compute_ticker_factors(
        ticker="X",
        as_of=date(2024, 1, 1),
        ohlcv=pd.DataFrame(),
        per_pbr=pd.DataFrame(),
        financials=pd.DataFrame(),
        revenue=pd.DataFrame(),
    )
    assert factors == {
        "momentum": None, "quality_roe": None, "value_pe": None,
        "low_vol": None, "revenue_growth": None,
    }
