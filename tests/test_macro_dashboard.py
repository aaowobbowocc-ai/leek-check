"""
Macro Dashboard 單元測試 — 相關性 / VIX 狀態 / ETF 溢價。
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.report.macro_dashboard import (
    CorrelationStatus,
    ETFPremiumCheck,
    VIXStatus,
    compute_taiex_sp500_correlation,
    estimate_etf_premium,
    render_macro_section,
    vix_status,
)


def _mk_ohlcv(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date(2025, 1, 1) + timedelta(days=i) for i in range(len(closes))],
            "open": closes, "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes], "close": closes,
            "volume": [100_000] * len(closes),
        }
    )


# ─────────────────────────────────────────
# Correlation
# ─────────────────────────────────────────
def test_correlation_high_when_perfectly_synced() -> None:
    base = list(range(80))
    taiex = _mk_ohlcv([100 + i for i in base])
    sp = _mk_ohlcv([200 + i * 2 for i in base])
    r = compute_taiex_sp500_correlation(taiex, sp, window_days=60)
    assert r is not None
    assert r.correlation > 0.95
    assert r.level == "concentrated"


def test_correlation_low_when_independent() -> None:
    rng = np.random.default_rng(42)
    n = 80
    taiex = _mk_ohlcv([100 + rng.normal(0, 1) for _ in range(n)])
    sp = _mk_ohlcv([200 + rng.normal(0, 1) for _ in range(n)])
    r = compute_taiex_sp500_correlation(taiex, sp, window_days=60)
    assert r is not None
    assert -0.5 < r.correlation < 0.5


def test_correlation_insufficient_data_returns_none() -> None:
    taiex = _mk_ohlcv([100] * 20)
    sp = _mk_ohlcv([200] * 20)
    assert compute_taiex_sp500_correlation(taiex, sp, window_days=60) is None


def test_correlation_empty_df_returns_none() -> None:
    assert compute_taiex_sp500_correlation(pd.DataFrame(), pd.DataFrame()) is None


# ─────────────────────────────────────────
# VIX
# ─────────────────────────────────────────
def test_vix_panic_above_30() -> None:
    s = vix_status(35.0)
    assert s.level == "panic"
    assert "⚫" in s.description


def test_vix_alert_20_30() -> None:
    s = vix_status(25.0)
    assert s.level == "alert"


def test_vix_calm_12_20() -> None:
    s = vix_status(15.0)
    assert s.level == "calm"


def test_vix_complacent_below_12() -> None:
    s = vix_status(10.0)
    assert s.level == "complacent"


# ─────────────────────────────────────────
# ETF Premium
# ─────────────────────────────────────────
def test_etf_premium_normal_range() -> None:
    """TW ETF 與美股 ETF × 匯率比例與歷史中位數一致 → ok。"""
    # 美股價 100, TW 對應約 100 × 32 = 3200 (假設匯率 32)
    n = 80
    tw_closes = [100.0 * 32 + i * 0.1 for i in range(n)]   # 緩慢上漲
    ref_closes = [100.0 + i * 0.003 for i in range(n)]     # 同步緩漲
    p = estimate_etf_premium("00646", _mk_ohlcv(tw_closes), _mk_ohlcv(ref_closes),
                              usd_twd_rate=32.0)
    assert p is not None
    assert p.level == "ok"
    assert abs(p.estimated_premium_pct) < 1.5


def test_etf_premium_warning_when_above_baseline() -> None:
    """TW ETF 突然偏離歷史比例 +5% → danger。"""
    n = 80
    tw_closes = [100.0 * 32] * (n - 1) + [100.0 * 32 * 1.05]   # 最後一日突然 +5%
    ref_closes = [100.0] * n
    p = estimate_etf_premium("00646", _mk_ohlcv(tw_closes), _mk_ohlcv(ref_closes),
                              usd_twd_rate=32.0)
    assert p is not None
    assert p.level == "danger"
    assert p.estimated_premium_pct > 3


def test_etf_premium_returns_none_for_unknown_ticker() -> None:
    p = estimate_etf_premium("9999", _mk_ohlcv([100] * 30), _mk_ohlcv([100] * 30),
                              usd_twd_rate=32.0)
    assert p is None


def test_etf_premium_returns_none_when_data_insufficient() -> None:
    p = estimate_etf_premium("00646", _mk_ohlcv([100] * 5), _mk_ohlcv([100] * 5),
                              usd_twd_rate=32.0)
    assert p is None


def test_etf_premium_handles_zero_fx_rate() -> None:
    p = estimate_etf_premium("00646", _mk_ohlcv([100] * 60), _mk_ohlcv([100] * 60),
                              usd_twd_rate=0.0)
    assert p is None


# ─────────────────────────────────────────
# Render
# ─────────────────────────────────────────
def test_render_section_includes_all_blocks() -> None:
    corr = CorrelationStatus(0.92, "concentrated", "🔴 相關性 0.92 過高")
    vix = VIXStatus(28.0, "alert", "🟠 VIX 28")
    premiums = [
        ETFPremiumCheck(
            tw_ticker="00646", tw_name="元大標普", tw_price=64,
            ref_ticker="SPY", ref_name="美股", ref_price=600,
            usd_twd=32.0, estimated_premium_pct=4.2,
            level="danger", suggestion="⚠️ 00646 偏離 +4.2%",
        ),
    ]
    md = render_macro_section(corr, vix, premiums)
    assert "🌍 全球宏觀" in md
    assert "0.92" in md
    assert "VIX 28" in md
    assert "00646" in md


def test_render_section_handles_missing_data() -> None:
    md = render_macro_section(None, None, [])
    assert "🌍 全球宏觀" in md
    assert "資料不足" in md or "無資料" in md
