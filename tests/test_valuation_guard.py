"""Phase 12 測試 — valuation_guard：PBR 百分位計算與 look-ahead 防線。"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.strategy.valuation_guard import compute_pbr_percentile, is_overvalued


def _build_pbr_history(as_of: date, values: list[float], days_back: int) -> pd.DataFrame:
    """以 as_of 回推 days_back 個交易日，逐日填 values（末尾對齊 as_of）。"""
    dates = [as_of - timedelta(days=i) for i in range(days_back - 1, -1, -1)]
    assert len(values) == days_back, "values 數量需與 days_back 一致"
    return pd.DataFrame({"date": dates, "pbr": values})


def test_percentile_mid_range() -> None:
    """中位值應落在 ~0.5（嚴格用 < as_of 的歷史分佈）。"""
    as_of = date(2024, 6, 30)
    # 200 日歷史線性從 1.0 → 3.0，當前 PBR = 2.0（中位）
    vals = [1.0 + (2.0 * i / 199) for i in range(200)]
    vals.append(2.0)  # as_of 當天
    df = _build_pbr_history(as_of, vals, 201)
    pct = compute_pbr_percentile(df, as_of)
    assert pct is not None
    assert 0.45 <= pct <= 0.55


def test_percentile_top_10() -> None:
    """當前 PBR 比過去 99% 都貴 → 百分位 > 0.9。"""
    as_of = date(2024, 6, 30)
    # 200 日都 1.0–2.0，當前 PBR = 5.0（超越所有歷史）
    vals = [1.0 + (i / 199) for i in range(200)]
    vals.append(5.0)
    df = _build_pbr_history(as_of, vals, 201)
    pct = compute_pbr_percentile(df, as_of)
    assert pct is not None
    assert pct > 0.9


def test_insufficient_samples_returns_none() -> None:
    """樣本 < 60 個 → None。"""
    as_of = date(2024, 6, 30)
    vals = [1.5] * 30 + [2.0]
    df = _build_pbr_history(as_of, vals, 31)
    assert compute_pbr_percentile(df, as_of) is None


def test_empty_history_returns_none() -> None:
    assert compute_pbr_percentile(pd.DataFrame(), date(2024, 6, 30)) is None
    assert compute_pbr_percentile(None, date(2024, 6, 30)) is None


def test_look_ahead_excludes_as_of() -> None:
    """as_of 當天不可計入歷史分佈（防止把「當前值」包進分母拉高百分位）。"""
    as_of = date(2024, 6, 30)
    # 前 100 日全部 1.0，as_of 當天 3.0
    # 若正確排除 as_of，rank=100（全勝） → pct = 1.0
    # 若誤把 as_of 納入歷史，rank=101（101/101） → pct = 1.0 仍對，需另造反例
    # 反例：前 100 日 1.0，as_of 當天 1.0 → 正確 pct = 100/100 = 1.0
    # 但若誤把 as_of 納入，hist 變 101 筆，rank=101 → 1.0（仍一樣）
    # 更嚴格反例：前 99 日 1.0，第 100 日 = as_of 當天 = 5.0
    # 正確：歷史 99 筆全是 1.0，current=5.0 → rank=99/99=1.0
    # 若誤包 as_of：歷史 100 筆含 5.0，current=5.0 → rank=100/100=1.0（錯在 over-count）
    # 採用另一策略：確認 < 60 的 insufficient 門檻在「as_of 當天填資料、前面只有 59 天」時觸發
    vals = [1.0] * 59 + [2.0]  # 60 筆含 as_of
    df = _build_pbr_history(as_of, vals, 60)
    # history 嚴格 < as_of → 只有 59 筆 < 60 門檻 → None
    assert compute_pbr_percentile(df, as_of) is None


def test_invalid_pbr_filtered() -> None:
    """PBR ≤ 0（淨值為負或資料異常）應被過濾。"""
    as_of = date(2024, 6, 30)
    vals = [1.5] * 200 + [2.0]
    df = _build_pbr_history(as_of, vals, 201)
    # 塞幾筆異常值
    df.loc[5:10, "pbr"] = -1.0
    df.loc[50, "pbr"] = 0.0
    pct = compute_pbr_percentile(df, as_of)
    assert pct is not None  # 剩下的樣本應仍 ≥ 60


def test_is_overvalued_triggers_above_threshold() -> None:
    as_of = date(2024, 6, 30)
    vals = [1.0 + (i / 199) for i in range(200)]
    vals.append(5.0)
    df = _build_pbr_history(as_of, vals, 201)
    over, pct = is_overvalued(df, as_of, threshold=0.90)
    assert over is True
    assert pct is not None and pct > 0.90


def test_is_overvalued_passes_below_threshold() -> None:
    as_of = date(2024, 6, 30)
    vals = [1.0 + (2.0 * i / 199) for i in range(200)]
    vals.append(2.0)  # 中位
    df = _build_pbr_history(as_of, vals, 201)
    over, pct = is_overvalued(df, as_of, threshold=0.90)
    assert over is False
    assert pct is not None and pct < 0.90


def test_is_overvalued_none_when_insufficient() -> None:
    """樣本不足 → 回 (False, None)，保守不擋。"""
    as_of = date(2024, 6, 30)
    vals = [1.5] * 10 + [2.0]
    df = _build_pbr_history(as_of, vals, 11)
    over, pct = is_overvalued(df, as_of)
    assert over is False
    assert pct is None
