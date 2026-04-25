"""
MOPS client 單元測試（mocked HTTP）。

因 MOPS HTML table 格式複雜、read_html 依賴 lxml，本測試聚焦於
normalize 邏輯的正確性。抓取端 (_get_bytes) 的網路部分留整合測試。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.data.mops_client import MOPSClient


# ─────────────────────────────────────────
# normalize 測試（直接餵 pandas DataFrame，不經 HTML parse）
# ─────────────────────────────────────────
def test_normalize_revenue_basic() -> None:
    """模擬 read_html 回來的 table。"""
    raw = pd.DataFrame(
        {
            "公司代號": ["2330", "2317", "9999_header"],
            "公司名稱": ["台積電", "鴻海", ""],
            "當月營收": ["1,500,000", "600,000", "--"],
            "上月營收": ["1,400,000", "580,000", "--"],
            "去年當月營收": ["1,200,000", "550,000", "--"],
            "上月比較增減(%)": ["7.14", "3.45", "--"],
            "去年同月增減(%)": ["25.00", "9.09", "--"],
        }
    )
    out = MOPSClient._normalize_revenue_table(raw, year=2026, month=4)

    assert len(out) == 2       # 9999_header 被過濾
    tsmc = out[out["ticker"] == "2330"].iloc[0]
    assert tsmc["revenue"] == 1_500_000
    assert tsmc["revenue_yoy_pct"] == pytest.approx(25.0)
    assert tsmc["revenue_mom_pct"] == pytest.approx(7.14)
    assert tsmc["announce_date"] == date(2026, 4, 10)


def test_normalize_filters_non_ticker_rows() -> None:
    """MOPS 原表包含產業分隔行、合計行；要濾掉。"""
    raw = pd.DataFrame(
        {
            "公司代號": ["2330", "水泥工業", "", "合計", "1101"],
            "公司名稱": ["台積電", "", "", "", "台泥"],
            "當月營收": ["1500", "--", "--", "99999", "800"],
        }
    )
    out = MOPSClient._normalize_revenue_table(raw, year=2026, month=4)
    assert set(out["ticker"]) == {"2330", "1101"}


def test_normalize_handles_missing_columns() -> None:
    """MOPS 偶爾欄位名變動，缺某欄位時不應 crash。"""
    raw = pd.DataFrame(
        {
            "公司代號": ["2330"],
            "公司名稱": ["台積電"],
            "當月營收": ["1500"],
            # 缺其他欄位
        }
    )
    out = MOPSClient._normalize_revenue_table(raw, year=2026, month=4)
    assert len(out) == 1
    assert out.iloc[0]["revenue"] == 1500


def test_normalize_empty_returns_empty() -> None:
    out = MOPSClient._normalize_revenue_table(pd.DataFrame(), year=2026, month=4)
    assert out.empty


def test_normalize_rejects_non_digit_tickers() -> None:
    """代號非數字或非 4 碼 → 過濾。"""
    raw = pd.DataFrame(
        {
            "公司代號": ["ABCD", "12345", "2330", "123"],
            "公司名稱": ["X", "Y", "台積電", "Z"],
            "當月營收": ["1", "2", "3", "4"],
        }
    )
    out = MOPSClient._normalize_revenue_table(raw, year=2026, month=4)
    assert list(out["ticker"]) == ["2330"]


# ─────────────────────────────────────────
# Cache behavior
# ─────────────────────────────────────────
def test_cache_hit_avoids_network(tmp_path: Path) -> None:
    """第二次呼叫應走快取，不打網路。"""
    client = MOPSClient(cache_dir=tmp_path, polite_delay=0.0)

    # 準備假 parquet cache
    fake_data = pd.DataFrame([{"ticker": "2330", "revenue": 1500, "announce_date": date(2026, 4, 10)}])
    cache_file = tmp_path / "revenue_115_04_sii.parquet"
    fake_data.to_parquet(cache_file, index=False)

    # 就算網路壞掉也能讀
    with patch.object(client, "_get_bytes", return_value=None):
        out = client.get_monthly_revenue_batch(year=2026, month=4, market="sii")

    assert not out.empty
    assert out.iloc[0]["ticker"] == "2330"


def test_empty_response_returns_empty(tmp_path: Path) -> None:
    client = MOPSClient(cache_dir=tmp_path, polite_delay=0.0)
    with patch.object(client, "_get_bytes", return_value=None):
        out = client.get_monthly_revenue_batch(year=2026, month=4, market="sii")
    assert out.empty
