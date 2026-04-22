"""
Phase 11.3 — 雙龍取珠（Double Dragon Filter）籌碼訊號測試。

必須同時滿足 AND 條件：
  前提：5 日融資總額減少 + ≥ 3 天下跌 + 收盤未跌（散戶退場）
  板機：投信 OR 外資連續淨買 ≥ 2 日（聰明錢進場）

bonus：預設 +0.10；breadth < 40% → 減半 +0.05。
空 DataFrame 需優雅降級（盤後 21:00 前 FinMind 可能回空）。

演化脈絡：
  - Phase 11 原設計是「散戶退場」或「投信連買+融資降」擇一觸發各 +0.08
  - Phase 11.2 修好資料 bug 後發現單邊「散戶退場」在 2023 年是負 alpha
  - Phase 11.3 改為 AND 門檻 — 必須有機構確認進場才給分
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.strategy.chip_factor import ChipFactor


def _write_dt_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "day_trader.yaml"
    path.write_text(
        """known_day_trader_branches: []
thresholds:
  top_n_brokers: 5
  ratio_threshold: 0.40
""",
        encoding="utf-8",
    )
    return path


def _inst_df(trust_days: int = 0, daily_net: int = 500_000) -> pd.DataFrame:
    """投信連買 trust_days 日的 mock。"""
    rows = []
    base = date(2024, 4, 15)
    for i in range(10):
        rows.append({
            "date": base - timedelta(days=i), "name": "外資",
            "buy": 0, "sell": 0, "net_buy": 0,
        })
    for i in range(trust_days):
        rows.append({
            "date": base - timedelta(days=i), "name": "投信",
            "buy": daily_net, "sell": 0, "net_buy": daily_net,
        })
    return pd.DataFrame(rows)


def _ownership_df(foreign_pcts: list[float]) -> pd.DataFrame:
    """外資持股率序列（由舊到新）。"""
    base = date(2024, 4, 15)
    n = len(foreign_pcts)
    rows = []
    for i, pct in enumerate(foreign_pcts):
        rows.append({
            "date": base - timedelta(days=n - 1 - i),
            "foreign_pct": pct,
            "foreign_shares": 0,
            "shares_issued": 0,
        })
    return pd.DataFrame(rows)


def _margin_df(balances: list[int]) -> pd.DataFrame:
    """balances[-1] 是最新一日；日期由舊到新。"""
    base = date(2024, 4, 15)
    n = len(balances)
    rows = []
    for i, bal in enumerate(balances):
        rows.append({
            "date": base - timedelta(days=n - 1 - i),
            "margin_purchase": 0,
            "short_sale": 0,
            "margin_balance": bal,
            "short_balance": 0,
        })
    return pd.DataFrame(rows)


def _ohlcv_df(closes: list[float]) -> pd.DataFrame:
    base = date(2024, 4, 15)
    n = len(closes)
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "date": base - timedelta(days=n - 1 - i),
            "open": c, "high": c, "low": c, "close": c, "volume": 1_000_000,
        })
    return pd.DataFrame(rows)


def _empty_broker() -> pd.DataFrame:
    return pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# 核心行為：AND 門檻
# ─────────────────────────────────────────────────────────────

def test_double_dragon_trust_confirmation_triggers(tmp_path: Path) -> None:
    """散戶退場 + 投信連買 2 日 → 雙龍取珠觸發 +0.10。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),   # 投信連買 2 日
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,
    )
    assert result.flags["double_dragon"] is True
    assert result.breakdown["double_dragon_source"] == 1   # 1=投信
    assert result.breakdown["double_dragon_bonus_applied"] == 0.10


def test_double_dragon_foreign_confirmation_triggers(tmp_path: Path) -> None:
    """散戶退場 + 外資連 2 日加碼 → 雙龍取珠觸發（source=2 外資）。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    ownership = _ownership_df([20.0, 20.5, 21.0, 21.5])    # 連 3 日上升，streak=3
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=0),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        concentration=ownership,
        margin=margin,
        ohlcv=ohlcv,
    )
    assert result.flags["double_dragon"] is True
    assert result.breakdown["double_dragon_source"] == 2


def test_double_dragon_both_parties_triggers(tmp_path: Path) -> None:
    """投信 + 外資同時確認 → source=3。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    ownership = _ownership_df([20.0, 20.5, 21.0, 21.5])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=3),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        concentration=ownership,
        margin=margin,
        ohlcv=ohlcv,
    )
    assert result.flags["double_dragon"] is True
    assert result.breakdown["double_dragon_source"] == 3


def test_retail_exit_alone_does_not_trigger(tmp_path: Path) -> None:
    """Phase 11.3 核心：單純散戶退場（無機構確認）→ 不觸發、不加分。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=0),   # 投信無動作
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,                             # 外資也沒 concentration 資料
    )
    assert result.flags["double_dragon"] is False
    assert result.breakdown["double_dragon_source"] == 0
    assert result.breakdown["double_dragon_bonus_applied"] == 0.0


def test_institutional_buy_without_retail_exit_does_not_trigger(tmp_path: Path) -> None:
    """投信連買但散戶沒退場 → 雙龍取珠不觸發（前提不滿足）。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    # 融資總額上升 → net_decrease = False
    margin = _margin_df([10_000, 10_100, 10_200, 10_300, 10_400, 10_500])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=3),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,
    )
    assert result.flags["double_dragon"] is False


def test_price_down_disqualifies_premise(tmp_path: Path) -> None:
    """融資下降 + 投信買 + 但價跌 → 前提不滿足（不是籌碼換手，是一起跑）。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    ohlcv = _ohlcv_df([110, 108, 106, 104, 102, 100])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=3),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,
    )
    assert result.flags["double_dragon"] is False


def test_premise_net_decrease_with_3_down_days(tmp_path: Path) -> None:
    """Phase 11.2 放寬保留：5 日非連降但總額減+ ≥ 3 天下跌 → 前提滿足。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_500, 9_800, 9_300, 9_100, 9_000])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,
    )
    assert result.flags["double_dragon"] is True


def test_premise_net_increase_no_trigger(tmp_path: Path) -> None:
    """雖 ≥ 3 天下跌但整體總額上升 + 投信買 → 前提不滿足。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_800, 9_600, 9_400, 10_800, 11_000])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,
    )
    assert result.flags["double_dragon"] is False


def test_empty_margin_graceful_degrade(tmp_path: Path) -> None:
    """盤後 21:00 前 FinMind 回空 → 不爆、不觸發、不加分。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=3),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=pd.DataFrame(),
        ohlcv=_ohlcv_df([100, 100, 100, 100, 100, 100]),
    )
    assert result.flags["double_dragon"] is False
    assert result.breakdown["double_dragon_source"] == 0


# ─────────────────────────────────────────────────────────────
# 環境敏感度（breadth scaling）
# ─────────────────────────────────────────────────────────────

def test_bonus_halved_when_breadth_low(tmp_path: Path) -> None:
    """breadth < 40% → bonus 減半 = +0.05。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,
        breadth=0.25,                              # 寬度不佳
    )
    assert result.flags["double_dragon"] is True
    assert result.breakdown["double_dragon_bonus_applied"] == 0.05


def test_bonus_full_when_breadth_healthy(tmp_path: Path) -> None:
    """breadth >= 40% → bonus 滿額 +0.10。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,
        breadth=0.8,
    )
    assert result.breakdown["double_dragon_bonus_applied"] == 0.10


def test_breadth_none_uses_full_bonus(tmp_path: Path) -> None:
    """breadth 未提供（None）視同健康 → 滿額。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    result = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,
    )
    assert result.breakdown["double_dragon_bonus_applied"] == 0.10


def test_value_caps_at_one(tmp_path: Path) -> None:
    """chip 接近滿分 + bonus 觸發 → value 不可超過 1.0。"""
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    inst = _inst_df(trust_days=5, daily_net=10_000_000)
    margin = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    ohlcv = _ohlcv_df([100, 100, 100, 100, 100, 100])
    result = factor.score(
        "3413",
        institutional=inst,
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin,
        ohlcv=ohlcv,
    )
    assert result.value <= 1.0


# ─────────────────────────────────────────────────────────────
# Phase 11.4：foreign + double_dragon 加總後封頂（共振疊加）
# ─────────────────────────────────────────────────────────────

def test_mutually_exclusive_bonus_caps_at_foreign(tmp_path: Path) -> None:
    """外資連買（+0.15）與雙龍取珠（+0.10）同時觸發 → 疊加後封頂 0.20。

    Phase 11.3 採 max(foreign, dd) 讓 dd 淪為 foreign 的備胎（兩者共振時無增量）；
    Phase 11.4 改為疊加 clamp，讓「散戶退+外資連買」比「外資連買」多 0.05 分差，
    把真正的籌碼換手訊號拉開距離。
    """
    factor = ChipFactor(_write_dt_yaml(tmp_path))
    margin_drop = _margin_df([10_000, 9_500, 9_000, 8_500, 8_000, 7_500])
    margin_flat = _margin_df([10_000, 10_000, 10_000, 10_000, 10_000, 10_000])
    ohlcv = _ohlcv_df([100, 100, 101, 102, 103, 104])
    ownership_up = _ownership_df([20.0, 20.5, 21.0, 21.5])   # 外資連 3 日加碼 → streak=3

    # 基準：只有投信連買 2 日（dd 板機觸發要件之一），無 margin、無外資
    baseline = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=pd.DataFrame(),
        ohlcv=ohlcv,
    )

    # 僅 foreign 觸發：外資連 3 日加碼 + margin 平、無 dd
    foreign_only = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        concentration=ownership_up,
        margin=margin_flat,
        ohlcv=ohlcv,
    )

    # 僅 dd 觸發：投信連買 2 + 散戶退場、無外資 concentration
    dd_only = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        margin=margin_drop,
        ohlcv=ohlcv,
    )

    # 共振：外資連 3 日加碼 + 散戶退場
    both = factor.score(
        "3413",
        institutional=_inst_df(trust_days=2),
        broker=_empty_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=5_000_000,
        concentration=ownership_up,
        margin=margin_drop,
        ohlcv=ohlcv,
    )

    foreign_delta = foreign_only.value - baseline.value
    dd_delta = dd_only.value - baseline.value
    both_delta = both.value - baseline.value

    assert foreign_delta == pytest.approx(0.15, abs=1e-6)
    assert dd_delta == pytest.approx(0.10, abs=1e-6)
    # 共振：max(0.15, 0.10) = 0.15（互斥取大，不疊加）
    assert both_delta == pytest.approx(0.15, abs=1e-6)
    # 共振 == foreign 單邊，且嚴格大於 dd 單邊
    assert both_delta == pytest.approx(foreign_delta, abs=1e-6)
    assert both_delta > dd_delta
