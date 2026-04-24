"""
妖股雷達單元測試：四個訊號各自的觸發 / 不觸發邏輯。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.strategy.yaogu_radar import scan_ticker


def _mk_ohlcv(
    closes: list[float],
    volumes: list[int] | None = None,
    highs: list[float] | None = None,
) -> pd.DataFrame:
    """用 closes 序列造 OHLCV，high 預設 = close × 1.01。"""
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    if highs is None:
        highs = [c * 1.01 for c in closes]
    rows = [
        {
            "date": date(2025, 1, 1) + timedelta(days=i),
            "open": closes[i],
            "high": highs[i],
            "low": closes[i] * 0.99,
            "close": closes[i],
            "volume": volumes[i],
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


# ─────────────────────────────────────────
# 資料量不足
# ─────────────────────────────────────────
def test_insufficient_data_returns_none() -> None:
    df = _mk_ohlcv([100.0] * 30)
    assert scan_ticker(df, as_of=date(2025, 1, 30)) is None


def test_empty_data_returns_none() -> None:
    assert scan_ticker(pd.DataFrame(), as_of=date(2025, 1, 1)) is None


# ─────────────────────────────────────────
# 訊號 1：量能突破
# ─────────────────────────────────────────
def test_volume_breakout_triggers() -> None:
    """前 60 日區間在 100，最後一日收 108 突破 + 量 4x → vol_breakout 觸發。"""
    closes = [100.0] * 64 + [108.0]
    highs = [100.5] * 64 + [108.5]
    vols = [1_000_000] * 64 + [4_000_000]
    sig = scan_ticker(_mk_ohlcv(closes, vols, highs), as_of=_last_date(closes))
    assert sig is not None
    assert sig.breakdown["volume_breakout"] > 10.0
    assert "vol_breakout" in sig.flags


def test_volume_breakout_fails_without_volume() -> None:
    """價突破但量沒爆 → 不觸發。"""
    closes = [100.0] * 64 + [108.0]
    highs = [100.5] * 64 + [108.5]
    sig = scan_ticker(_mk_ohlcv(closes, None, highs), as_of=_last_date(closes))
    assert sig.breakdown["volume_breakout"] == 0.0
    assert "vol_breakout" not in sig.flags


def test_volume_breakout_fails_without_price_new_high() -> None:
    """量爆但價沒突破 60 日新高 → 不觸發。"""
    # 前 60 日最高 = 100.5；今日收 100.4（仍未破 100.5）
    closes = [100.0] * 64 + [100.4]
    highs = [100.5] * 64 + [100.4]
    vols = [1_000_000] * 64 + [4_000_000]
    sig = scan_ticker(_mk_ohlcv(closes, vols, highs), as_of=_last_date(closes))
    assert sig.breakdown["volume_breakout"] == 0.0


# ─────────────────────────────────────────
# 訊號 2：動能爆發
# ─────────────────────────────────────────
def test_momentum_triggers_on_big_5d_rally() -> None:
    closes = [100.0] * 60 + [100, 102, 105, 108, 112]     # 5D 報酬 12%
    sig = scan_ticker(_mk_ohlcv(closes), as_of=_last_date(closes))
    assert sig.breakdown["momentum"] > 0.0
    assert "momentum" in sig.flags


def test_momentum_fails_on_small_move() -> None:
    closes = [100.0] * 60 + [100, 101, 102, 103, 104]     # 5D 報酬 4%
    sig = scan_ticker(_mk_ohlcv(closes), as_of=_last_date(closes))
    assert sig.breakdown["momentum"] == 0.0


# ─────────────────────────────────────────
# 訊號 3：漲停動能
# ─────────────────────────────────────────
def test_limit_up_triggers_on_single() -> None:
    closes = [100.0] * 62 + [100, 105, 115.5]   # 最後一日漲停 115.5/105 = 1.1
    sig = scan_ticker(_mk_ohlcv(closes), as_of=_last_date(closes))
    assert sig.breakdown["limit_up"] == 15.0


def test_limit_up_triggers_on_double() -> None:
    closes = [100.0] * 62 + [100, 110, 121]     # 連兩日漲停
    sig = scan_ticker(_mk_ohlcv(closes), as_of=_last_date(closes))
    assert sig.breakdown["limit_up"] == 25.0


def test_limit_up_fails_when_none() -> None:
    closes = [100.0] * 65                        # 全平盤
    sig = scan_ticker(_mk_ohlcv(closes), as_of=_last_date(closes))
    assert sig.breakdown["limit_up"] == 0.0


# ─────────────────────────────────────────
# 訊號 4：爆量近期
# ─────────────────────────────────────────
def test_volume_expansion_triggers() -> None:
    """前 15 日量 100 萬、近 5 日量 250 萬 → 2.5x → 觸發。"""
    closes = [100.0] * 65
    vols = [1_000_000] * 60 + [2_500_000] * 5
    sig = scan_ticker(_mk_ohlcv(closes, vols), as_of=_last_date(closes))
    assert sig.breakdown["vol_expansion"] > 10.0
    assert "vol_expansion" in sig.flags


def test_volume_expansion_fails_when_flat() -> None:
    closes = [100.0] * 65
    vols = [1_000_000] * 65
    sig = scan_ticker(_mk_ohlcv(closes, vols), as_of=_last_date(closes))
    assert sig.breakdown["vol_expansion"] == 0.0


# ─────────────────────────────────────────
# 整合：多訊號同時觸發 → triggered=True
# ─────────────────────────────────────────
def test_combined_signals_trigger_score_above_threshold() -> None:
    """組合：四訊號同時觸發 → score 應遠 >= 60。"""
    # 平盤 62 日 + 漲停兩日（100 → 110 → 121），最後一日量 5x 爆發
    closes = [100.0] * 62 + [100.0, 110.0, 121.0]
    highs = [100.5] * 62 + [100.5, 110.5, 121.5]
    vols = [1_000_000] * 64 + [5_000_000]
    sig = scan_ticker(_mk_ohlcv(closes, vols, highs), as_of=_last_date(closes))
    assert sig.score >= 60.0
    assert sig.triggered is True
    # 確認四訊號都有貢獻
    assert sig.breakdown["volume_breakout"] > 0
    assert sig.breakdown["momentum"] > 0
    assert sig.breakdown["limit_up"] > 0
    assert sig.breakdown["vol_expansion"] > 0


def test_look_ahead_guard_excludes_post_cutoff() -> None:
    """as_of 日之後的 bar 不應進入計算（look-ahead 防線）。"""
    # 建 80 天資料，as_of 設在第 65 天 → 應只看前 65 天
    closes = [100.0] * 60 + [100, 102, 105, 108, 112] + [200.0] * 15  # 第 66-80 天暴漲
    highs = [h * 1.01 for h in closes]
    vols = [1_000_000] * 80
    df = _mk_ohlcv(closes, vols, highs)
    cutoff = df.iloc[64]["date"]
    sig = scan_ticker(df, as_of=cutoff)
    # 若偷看未來，close 會是 200；正確時 close 應是 112
    assert sig.close == pytest.approx(112.0)


# ─────────────────────────────────────────
# Helper
# ─────────────────────────────────────────
def _last_date(closes: list[float]) -> date:
    return date(2025, 1, 1) + timedelta(days=len(closes) - 1)
