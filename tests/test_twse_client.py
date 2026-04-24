"""
TWSE self-fetcher 單元測試（mocked HTTP，不打官方 API）。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from src.data.twse_client import TWSEClient


# ─────────────────────────────────────────
# Mocked TWSE CSV response samples
# ─────────────────────────────────────────
_TWSE_PER_PBR_CSV = """\
114年04月24日 個股日本益比、殖利率及股價淨值比(依證券代號)
"證券代號","證券名稱","殖利率(%)","股利年度","本益比","股價淨值比","財報年/季"
"1101","台泥","5.63","113","8.47","0.91","113/4"
"2330","台積電","1.74","113","23.50","8.12","113/4"
"3413","京鼎","3.21","113","12.34","2.05","113/4"
"2345","智邦","1.20","113","45.00","5.50","113/4"
"""

_TWSE_INSTITUTIONAL_CSV = """\
114年04月24日 三大法人買賣超日報
"證券代號","證券名稱","外陸資買賣超股數(不含外資自營商)","外資自營商買賣超股數","投信買賣超股數","自營商買賣超股數","三大法人買賣超股數"
"1101","台泥","1,234,000","0","-500,000","200,000","934,000"
"2330","台積電","15,000,000","100,000","2,000,000","500,000","17,600,000"
"3413","京鼎","500,000","0","300,000","-100,000","700,000"
"""


# ─────────────────────────────────────────
# PER/PBR parsing
# ─────────────────────────────────────────
def test_per_pbr_parsing_from_twse_csv(tmp_path) -> None:
    client = TWSEClient(cache_dir=tmp_path, polite_delay=0.0)
    with patch.object(client, "_get_text", return_value=_TWSE_PER_PBR_CSV):
        df = client.get_per_pbr_day(date(2025, 4, 24))

    assert not df.empty
    assert len(df) == 4      # 4 檔
    assert set(df["ticker"]) == {"1101", "2330", "3413", "2345"}
    # 台積電本益比應該被 parse 成 23.5
    tsmc = df[df["ticker"] == "2330"].iloc[0]
    assert tsmc["per"] == pytest.approx(23.5)
    assert tsmc["pbr"] == pytest.approx(8.12)
    assert tsmc["dividend_yield"] == pytest.approx(1.74)


def test_per_pbr_cache_hit(tmp_path) -> None:
    """第二次呼叫應從 parquet 讀，不再打網路。"""
    client = TWSEClient(cache_dir=tmp_path, polite_delay=0.0)
    with patch.object(client, "_get_text", return_value=_TWSE_PER_PBR_CSV):
        client.get_per_pbr_day(date(2025, 4, 24))

    # 第二次：就算網路壞掉也能從 cache 取
    with patch.object(client, "_get_text", return_value=""):
        df2 = client.get_per_pbr_day(date(2025, 4, 24))
    assert not df2.empty
    assert len(df2) == 4


def test_per_pbr_empty_response(tmp_path) -> None:
    client = TWSEClient(cache_dir=tmp_path, polite_delay=0.0)
    with patch.object(client, "_get_text", return_value=""):
        df = client.get_per_pbr_day(date(2025, 4, 24))
    assert df.empty


def test_per_pbr_filters_non_4digit_tickers(tmp_path) -> None:
    """不小心把 header 殘留當 data 時要能過濾掉。"""
    bad_csv = _TWSE_PER_PBR_CSV + '"xxx","雜訊","1.0","113","1.0","1.0","113/4"\n'
    client = TWSEClient(cache_dir=tmp_path, polite_delay=0.0)
    with patch.object(client, "_get_text", return_value=bad_csv):
        df = client.get_per_pbr_day(date(2025, 4, 24))
    # 3 碼 xxx 應被過濾
    assert (df["ticker"].str.len() == 4).all()


# ─────────────────────────────────────────
# Institutional parsing
# ─────────────────────────────────────────
def test_institutional_parsing(tmp_path) -> None:
    client = TWSEClient(cache_dir=tmp_path, polite_delay=0.0)
    with patch.object(client, "_get_text", return_value=_TWSE_INSTITUTIONAL_CSV):
        df = client.get_institutional_day(date(2025, 4, 24))
    assert not df.empty
    assert len(df) == 3
    tsmc = df[df["ticker"] == "2330"].iloc[0]
    assert tsmc["foreign_net"] == 15_000_000
    assert tsmc["inv_trust_net"] == 2_000_000
    assert tsmc["total_net"] == 17_600_000


def test_institutional_handles_negative_numbers(tmp_path) -> None:
    """法人賣超會是負數（含千分位逗號）。"""
    client = TWSEClient(cache_dir=tmp_path, polite_delay=0.0)
    with patch.object(client, "_get_text", return_value=_TWSE_INSTITUTIONAL_CSV):
        df = client.get_institutional_day(date(2025, 4, 24))
    cement = df[df["ticker"] == "1101"].iloc[0]
    assert cement["inv_trust_net"] == -500_000


# ─────────────────────────────────────────
# Batch history
# ─────────────────────────────────────────
def test_build_ticker_per_history_skips_weekends(tmp_path) -> None:
    """週末不該發請求。"""
    client = TWSEClient(cache_dir=tmp_path, polite_delay=0.0)
    call_count = [0]

    def mock_get(url: str) -> str:
        call_count[0] += 1
        return _TWSE_PER_PBR_CSV

    with patch.object(client, "_get_text", side_effect=mock_get):
        # 2025-04-19 週六、04-20 週日 → 應跳過
        df = client.build_ticker_per_history("2330", date(2025, 4, 18), date(2025, 4, 22))

    # 04-18 五 + 04-21 一 + 04-22 二 = 3 工作日 × 2 sources (TWSE + TPEX) = 6 次 HTTP
    assert call_count[0] == 6
