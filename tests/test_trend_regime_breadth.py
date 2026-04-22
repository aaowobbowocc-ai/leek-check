"""
Phase 11.2 — 市場寬度（breadth）測試。

get_breadth_score 應回傳「watchlist 中最新收盤 > MA20 的比例」，
供 composite_scorer 在 bull regime 下判斷是否為「權值股假拉抬」並啟動防守。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.risk.trend_regime import TrendRegimeDetector


def _ohlcv(closes: list[float]) -> pd.DataFrame:
    base = date(2024, 1, 1)
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "date": base + timedelta(days=i),
            "open": c, "high": c, "low": c, "close": c, "volume": 1_000_000,
        })
    return pd.DataFrame(rows)


def test_breadth_all_above_ma20() -> None:
    detector = TrendRegimeDetector()
    # 三檔皆尾盤 > MA20（最後一筆遠高於前 20 日平均）
    ohlcv_map = {
        "A": _ohlcv([100.0] * 20 + [110.0]),
        "B": _ohlcv([50.0] * 20 + [55.0]),
        "C": _ohlcv([200.0] * 20 + [220.0]),
    }
    assert detector.get_breadth_score(ohlcv_map) == 1.0


def test_breadth_all_below_ma20() -> None:
    detector = TrendRegimeDetector()
    ohlcv_map = {
        "A": _ohlcv([100.0] * 20 + [90.0]),
        "B": _ohlcv([50.0] * 20 + [45.0]),
    }
    assert detector.get_breadth_score(ohlcv_map) == 0.0


def test_breadth_mixed() -> None:
    detector = TrendRegimeDetector()
    ohlcv_map = {
        "A": _ohlcv([100.0] * 20 + [110.0]),  # 上
        "B": _ohlcv([100.0] * 20 + [90.0]),   # 下
        "C": _ohlcv([100.0] * 20 + [105.0]),  # 上
        "D": _ohlcv([100.0] * 20 + [95.0]),   # 下
    }
    assert detector.get_breadth_score(ohlcv_map) == 0.5


def test_breadth_below_defense_threshold() -> None:
    """4 檔只有 1 檔站上 MA20 → 25%，低於 40% 門檻。"""
    detector = TrendRegimeDetector()
    ohlcv_map = {
        "A": _ohlcv([100.0] * 20 + [110.0]),
        "B": _ohlcv([100.0] * 20 + [95.0]),
        "C": _ohlcv([100.0] * 20 + [95.0]),
        "D": _ohlcv([100.0] * 20 + [95.0]),
    }
    breadth = detector.get_breadth_score(ohlcv_map)
    assert breadth == 0.25
    assert breadth < 0.40


def test_breadth_insufficient_history_skipped() -> None:
    """個股歷史 < 20 日 → 略過該檔；全部略過 → 回中性 1.0。"""
    detector = TrendRegimeDetector()
    thin = _ohlcv([100.0] * 5)
    assert detector.get_breadth_score({"A": thin}) == 1.0


def test_breadth_empty_map_returns_neutral() -> None:
    """空輸入 → 中性 1.0（不誤觸發防守）。"""
    detector = TrendRegimeDetector()
    assert detector.get_breadth_score({}) == 1.0


def test_breadth_mixed_history_partial_count() -> None:
    """一檔資料不足 + 一檔夠 → 只計夠的那一檔。"""
    detector = TrendRegimeDetector()
    ohlcv_map = {
        "thin": _ohlcv([100.0] * 5),                    # 不計
        "ok_above": _ohlcv([100.0] * 20 + [110.0]),     # 上
    }
    assert detector.get_breadth_score(ohlcv_map) == 1.0
