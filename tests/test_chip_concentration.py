"""
Chip Concentration 單元測試（Sponsor 升級版 — 4 訊號）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.strategy.chip_concentration import (
    LEVEL_BONUS,
    compute_big_holder_slope,
    compute_broker_concentration,
    compute_foreign_pct_slope,
    compute_institutional_accumulation,
    evaluate_concentration,
)


# ─────────────────────────────────────────
# 工具：構造資料
# ─────────────────────────────────────────
def _mk_foreign(start: date, n_days: int, pct_path: list[float]) -> pd.DataFrame:
    assert len(pct_path) == n_days
    return pd.DataFrame({
        "date": [start + timedelta(days=i) for i in range(n_days)],
        "foreign_pct": pct_path,
    })


def _mk_institutional(
    start: date, n_days: int, daily_net_buy_lots: list[int],
) -> pd.DataFrame:
    rows = []
    for i, lots in enumerate(daily_net_buy_lots):
        d = start + timedelta(days=i)
        rows.append({"date": d, "name": "外資", "buy": max(lots, 0),
                     "sell": max(-lots, 0), "net_buy": lots})
    return pd.DataFrame(rows)


def _mk_ohlcv(start: date, n_days: int, daily_volume_lots: int = 5000) -> pd.DataFrame:
    return pd.DataFrame({
        "date": [start + timedelta(days=i) for i in range(n_days)],
        "open": [50.0] * n_days, "high": [51.0] * n_days,
        "low": [49.0] * n_days, "close": [50.0] * n_days,
        "volume": [daily_volume_lots] * n_days,
    })


def _mk_holding_shares(start: date, n_weeks: int, pct_path: list[float]) -> pd.DataFrame:
    """大戶持股週度資料。"""
    assert len(pct_path) == n_weeks
    return pd.DataFrame({
        "date": [start + timedelta(weeks=i) for i in range(n_weeks)],
        "big_holder_pct": pct_path,
    })


def _mk_broker(start: date, n_days: int, net_buy_by_broker: dict[str, int]) -> pd.DataFrame:
    """每天各券商相同淨買量。"""
    rows = []
    for i in range(n_days):
        d = start + timedelta(days=i)
        for broker_id, net in net_buy_by_broker.items():
            rows.append({
                "date": d,
                "broker_id": broker_id,
                "buy": max(net, 0),
                "sell": max(-net, 0),
            })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────
# L2 外資斜率
# ─────────────────────────────────────────
def test_foreign_pct_slope_increasing() -> None:
    df = _mk_foreign(date(2026, 3, 28), 30, pct_path=[35.0 + 0.04 * i for i in range(30)])
    now, past, slope = compute_foreign_pct_slope(df, as_of=date(2026, 4, 25), weeks=4)
    assert now is not None and past is not None
    assert slope == pytest.approx(0.28, abs=0.05)


def test_foreign_pct_slope_no_data() -> None:
    now, past, slope = compute_foreign_pct_slope(pd.DataFrame(), date(2026, 4, 25))
    assert now is None and past is None and slope is None


def test_foreign_pct_slope_no_lookahead() -> None:
    df = _mk_foreign(date(2026, 3, 28), 60, pct_path=[35.0] * 30 + [50.0] * 30)
    now, past, slope = compute_foreign_pct_slope(df, as_of=date(2026, 4, 25), weeks=4)
    assert now == pytest.approx(35.0)


# ─────────────────────────────────────────
# L1 法人累積買超
# ─────────────────────────────────────────
def test_institutional_accumulation_positive() -> None:
    inst = _mk_institutional(date(2026, 3, 28), 28, [100] * 28)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28, daily_volume_lots=5000)
    net, pct = compute_institutional_accumulation(inst, ohlcv, date(2026, 4, 25), weeks=4)
    assert net == 2800 * 1000
    assert pct == pytest.approx(2.0, abs=0.1)


def test_institutional_distribution() -> None:
    inst = _mk_institutional(date(2026, 3, 28), 28, [-100] * 28)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28)
    _, pct = compute_institutional_accumulation(inst, ohlcv, date(2026, 4, 25), weeks=4)
    assert pct is not None and pct < 0


# ─────────────────────────────────────────
# L3 大戶持股斜率（Sponsor）
# ─────────────────────────────────────────
def test_big_holder_slope_increasing() -> None:
    """大戶持股 4 週從 86% → 87% → slope +0.25 pp/wk。"""
    df = _mk_holding_shares(date(2026, 3, 28), 5, [86.0, 86.25, 86.5, 86.75, 87.0])
    now, past, slope = compute_big_holder_slope(df, as_of=date(2026, 4, 25), weeks=4)
    assert now is not None and past is not None
    assert slope == pytest.approx(0.25, abs=0.05)


def test_big_holder_slope_decreasing() -> None:
    df = _mk_holding_shares(date(2026, 3, 28), 5, [87.0, 86.75, 86.5, 86.25, 86.0])
    now, past, slope = compute_big_holder_slope(df, as_of=date(2026, 4, 25), weeks=4)
    assert slope is not None and slope < 0


def test_big_holder_slope_no_data() -> None:
    now, past, slope = compute_big_holder_slope(pd.DataFrame(), date(2026, 4, 25))
    assert now is None and slope is None


# ─────────────────────────────────────────
# L4 分點集中度（Sponsor）
# ─────────────────────────────────────────
def test_broker_concentration_high() -> None:
    """Top-5 券商合計淨買 5% 以上 → 高集中度。"""
    brokers = {str(i): 100 for i in range(1, 6)}   # 5 券商各買 100 張/天
    brokers["99"] = -10                              # 一個出貨券商（負值）
    broker_df = _mk_broker(date(2026, 3, 28), 28, brokers)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28, daily_volume_lots=5000)

    pct = compute_broker_concentration(broker_df, ohlcv, as_of=date(2026, 4, 25))
    # top5 各 100×28 = 2800 張；總成交 5000×28 = 14 萬張 → 2800*5/140000 ≈ 10%
    assert pct is not None and pct > 3.0


def test_broker_concentration_no_data() -> None:
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28)
    pct = compute_broker_concentration(pd.DataFrame(), ohlcv, date(2026, 4, 25))
    assert pct is None


# ─────────────────────────────────────────
# 整合：evaluate_concentration
# ─────────────────────────────────────────
def test_strong_accumulation_three_signals() -> None:
    """3 訊號同時成立 → strong_accumulation。"""
    today = date(2026, 4, 25)
    foreign = _mk_foreign(date(2026, 3, 28), 30, [35.0 + 0.05 * i for i in range(30)])
    inst = _mk_institutional(date(2026, 3, 28), 28, [200] * 28)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28, daily_volume_lots=5000)
    holding = _mk_holding_shares(date(2026, 3, 28), 5, [86.0, 86.25, 86.5, 86.75, 87.0])

    sig = evaluate_concentration("3595", foreign, inst, ohlcv, today, holding_shares=holding)
    assert sig.level == "strong_accumulation"
    assert sig.score_bonus == LEVEL_BONUS["strong_accumulation"]
    assert sig.signal_count == 3


def test_moderate_accumulation_two_signals() -> None:
    """L1 + L2 成立但無 L3/L4 → moderate_accumulation（新邏輯 2 訊號）。"""
    today = date(2026, 4, 25)
    foreign = _mk_foreign(date(2026, 3, 28), 30, [35.0 + 0.05 * i for i in range(30)])
    inst = _mk_institutional(date(2026, 3, 28), 28, [200] * 28)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28, daily_volume_lots=5000)

    sig = evaluate_concentration("3595", foreign, inst, ohlcv, today)
    assert sig.level == "moderate_accumulation"
    assert sig.signal_count == 2


def test_distribution_when_institutions_selling() -> None:
    today = date(2026, 4, 25)
    foreign = _mk_foreign(date(2026, 3, 28), 30, [35.0] * 30)
    inst = _mk_institutional(date(2026, 3, 28), 28, [-200] * 28)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28, daily_volume_lots=5000)

    sig = evaluate_concentration("3595", foreign, inst, ohlcv, today)
    assert sig.level == "distribution"
    assert sig.score_bonus < 0


def test_distribution_big_holder_selling() -> None:
    """大戶持股快速下滑 → distribution。"""
    today = date(2026, 4, 25)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28)
    holding = _mk_holding_shares(date(2026, 3, 28), 5, [87.0, 86.5, 86.0, 85.5, 85.0])

    sig = evaluate_concentration("3595", None, None, ohlcv, today, holding_shares=holding)
    assert sig.level == "distribution"


def test_no_accumulation_when_quiet() -> None:
    today = date(2026, 4, 25)
    foreign = _mk_foreign(date(2026, 3, 28), 30, [35.0] * 30)
    inst = _mk_institutional(date(2026, 3, 28), 28, [0] * 28)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28)

    sig = evaluate_concentration("3595", foreign, inst, ohlcv, today)
    assert sig.level == "no_accumulation"
    assert sig.score_bonus == 0.0


def test_handles_missing_foreign_data() -> None:
    today = date(2026, 4, 25)
    inst = _mk_institutional(date(2026, 3, 28), 28, [200] * 28)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28, daily_volume_lots=5000)

    sig = evaluate_concentration("3595", None, inst, ohlcv, today)
    assert sig.level in ("moderate_accumulation", "weak_accumulation")


def test_handles_missing_institutional_data() -> None:
    today = date(2026, 4, 25)
    foreign = _mk_foreign(date(2026, 3, 28), 30, [35.0 + 0.05 * i for i in range(30)])
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28)

    sig = evaluate_concentration("3595", foreign, None, ohlcv, today)
    assert sig.level == "weak_accumulation"


def test_handles_all_data_missing() -> None:
    today = date(2026, 4, 25)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28)
    sig = evaluate_concentration("3595", None, None, ohlcv, today)
    assert sig.level == "no_accumulation"
    assert sig.foreign_pct_now is None
    assert sig.inst_net_buy_4w_shares is None
    assert sig.big_holder_pct_now is None
    assert sig.broker_top5_net_buy_pct_volume is None


def test_new_fields_present_with_sponsor_data() -> None:
    """有 Sponsor 資料時，新欄位都不為 None。"""
    today = date(2026, 4, 25)
    ohlcv = _mk_ohlcv(date(2026, 3, 28), 28, daily_volume_lots=5000)
    holding = _mk_holding_shares(date(2026, 3, 28), 5, [86.0, 86.25, 86.5, 86.75, 87.0])
    broker_df = _mk_broker(date(2026, 3, 28), 28, {"A": 200, "B": 150, "C": 100, "D": 80, "E": 60})

    sig = evaluate_concentration(
        "3595", None, None, ohlcv, today,
        holding_shares=holding, broker_df=broker_df,
    )
    assert sig.big_holder_pct_now is not None
    assert sig.big_holder_slope_4w_pp_per_week is not None
    assert sig.broker_top5_net_buy_pct_volume is not None
