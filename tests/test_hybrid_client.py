"""
HybridClient 單元測試 — 驗證資料源分派 + 介面對齊 FinMindClient。

不打網路：mock 底層 client 行為。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.data.hybrid_client import HybridClient


def _mk_twse_per_pbr_response() -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": date(2026, 4, 24), "per": 23.5, "pbr": 8.1, "dividend_yield": 1.74}]
    )


def _mk_twse_institutional_response() -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "date": date(2026, 4, 24), "ticker": "2330", "name": "台積電",
            "foreign_net": 15_000_000, "inv_trust_net": 2_000_000,
            "dealer_net": 500_000, "total_net": 17_500_000,
        }]
    )


# ─────────────────────────────────────────
# 路由分派
# ─────────────────────────────────────────
def test_get_per_pbr_routes_to_twse() -> None:
    twse = MagicMock()
    twse.build_ticker_per_history.return_value = _mk_twse_per_pbr_response()
    client = HybridClient(twse=twse, mops=MagicMock(), finmind=None)
    df = client.get_per_pbr("2330", date(2026, 4, 1), date(2026, 4, 24))
    twse.build_ticker_per_history.assert_called_once()
    assert not df.empty
    assert df.iloc[0]["per"] == 23.5


def test_get_monthly_revenue_routes_to_mops() -> None:
    mops = MagicMock()
    mops.build_ticker_revenue_history.return_value = pd.DataFrame(
        [{"date": date(2026, 3, 10), "revenue": 1_500_000, "revenue_yoy": 25.0}]
    )
    client = HybridClient(twse=MagicMock(), mops=mops, finmind=None)
    df = client.get_monthly_revenue("2330", date(2026, 1, 1), date(2026, 4, 1))
    mops.build_ticker_revenue_history.assert_called_once()
    assert not df.empty


def test_get_broker_returns_empty_without_finmind() -> None:
    """No FinMind → broker 回空（user 取消 Sponsor 後的行為）。"""
    client = HybridClient(twse=MagicMock(), mops=MagicMock(), finmind=None)
    df = client.get_broker_distribution("2330", date(2026, 1, 1), date(2026, 4, 1))
    assert df.empty


def test_get_broker_uses_finmind_when_available() -> None:
    finmind = MagicMock()
    finmind.get_broker_distribution.return_value = pd.DataFrame(
        [{"date": date(2026, 4, 24), "broker_id": "1", "buy": 100_000, "sell": 50_000}]
    )
    client = HybridClient(twse=MagicMock(), mops=MagicMock(), finmind=finmind)
    df = client.get_broker_distribution("2330", date(2026, 1, 1), date(2026, 4, 1))
    finmind.get_broker_distribution.assert_called_once()
    assert not df.empty


# ─────────────────────────────────────────
# Institutional 展平：TWSE per-day → FinMind long format
# ─────────────────────────────────────────
def test_institutional_flattens_to_long_format(monkeypatch) -> None:
    """TWSE 每日 1 列 (含 3 法人)，HybridClient 應展開成 3 列。"""
    twse = MagicMock()
    twse.get_institutional_day.return_value = _mk_twse_institutional_response()

    client = HybridClient(twse=twse, mops=MagicMock(), finmind=None)
    df = client.get_institutional("2330", date(2026, 4, 24), date(2026, 4, 24))

    assert len(df) == 3   # 3 法人各一筆
    names = set(df["name"])
    assert names == {"外陸資", "投信", "自營商"}
    foreign = df[df["name"] == "外陸資"].iloc[0]
    assert foreign["net_buy"] == 15_000_000


def test_institutional_skips_weekends(monkeypatch) -> None:
    """週末不該打 TWSE。"""
    twse = MagicMock()
    twse.get_institutional_day.return_value = pd.DataFrame()

    client = HybridClient(twse=twse, mops=MagicMock(), finmind=None)
    # 2025-04-19 週六、04-20 週日 → 都跳過
    client.get_institutional("2330", date(2025, 4, 19), date(2025, 4, 20))
    assert twse.get_institutional_day.call_count == 0


def test_institutional_empty_when_no_match() -> None:
    twse = MagicMock()
    twse.get_institutional_day.return_value = pd.DataFrame(
        [{
            "date": date(2026, 4, 24), "ticker": "2330", "name": "台積電",
            "foreign_net": 100, "inv_trust_net": 100, "dealer_net": 100, "total_net": 300,
        }]
    )
    client = HybridClient(twse=twse, mops=MagicMock(), finmind=None)
    # 找不存在的 ticker
    df = client.get_institutional("9999", date(2026, 4, 21), date(2026, 4, 25))
    assert df.empty


# ─────────────────────────────────────────
# Margin / FinancialStatements stub
# ─────────────────────────────────────────
def test_margin_returns_empty_without_finmind() -> None:
    client = HybridClient(twse=MagicMock(), mops=MagicMock(), finmind=None)
    df = client.get_margin("2330", date(2026, 1, 1), date(2026, 4, 1))
    assert df.empty


def test_financial_statements_returns_empty_without_finmind() -> None:
    client = HybridClient(twse=MagicMock(), mops=MagicMock(), finmind=None)
    df = client.get_financial_statements("2330", date(2026, 1, 1), date(2026, 4, 1))
    assert df.empty
