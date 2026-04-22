"""
Phase 2 測試 — 數據客戶端（不需實際 API Key）。
所有外部 HTTP / yfinance 呼叫皆以 mock 取代。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data._cache import (
    cache_last_date,
    is_cache_fresh,
    save_cache,
    slice_by_date,
)
from src.data.adr_fetcher import _yf_download, get_overnight_report, get_tw_ohlcv_adjusted
from src.data.finmind_client import FinMindClient
from src.data.fugle_client import FugleClient


# ─────────────────────────────────────────
# _cache utilities
# ─────────────────────────────────────────
def _make_price_df(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": [date.fromisoformat(d) for d in dates],
        "close": [100.0 + i for i in range(len(dates))],
    })


def test_cache_fresh_with_yesterday(tmp_path: Path) -> None:
    yesterday = (date.today() - __import__("datetime").timedelta(days=1)).isoformat()
    df = _make_price_df(["2024-01-02", yesterday])
    path = tmp_path / "test.parquet"
    save_cache(path, df)
    loaded = pd.read_parquet(path)
    assert is_cache_fresh(loaded, "date")


def test_cache_stale_with_old_data(tmp_path: Path) -> None:
    df = _make_price_df(["2024-01-01", "2024-01-02"])
    path = tmp_path / "test.parquet"
    save_cache(path, df)
    loaded = pd.read_parquet(path)
    assert not is_cache_fresh(loaded, "date")


def test_cache_fresh_returns_none_for_empty() -> None:
    assert is_cache_fresh(None) is False
    assert is_cache_fresh(pd.DataFrame()) is False


def test_slice_by_date() -> None:
    df = _make_price_df(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"])
    result = slice_by_date(df, date(2024, 1, 2), date(2024, 1, 3))
    assert len(result) == 2
    assert result.iloc[0]["date"] == date(2024, 1, 2)
    assert result.iloc[-1]["date"] == date(2024, 1, 3)


# ─────────────────────────────────────────
# FinMindClient — cache hit skips HTTP call
# ─────────────────────────────────────────
def _inst_df() -> pd.DataFrame:
    yesterday = (date.today() - __import__("datetime").timedelta(days=1)).isoformat()
    return pd.DataFrame({
        "date": [date.fromisoformat("2024-01-02"), date.fromisoformat(yesterday)],
        "name": ["外資", "投信"],
        "buy": [1_000_000, 500_000],
        "sell": [200_000, 100_000],
        "net_buy": [800_000, 400_000],
    })


def test_finmind_uses_cache_when_fresh(tmp_path: Path) -> None:
    """快取新鮮時不應發出 HTTP 請求。"""
    df = _inst_df()
    path = tmp_path / "finmind" / "TaiwanStockInstitutionalInvestorsBuySell_3413.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)

    client = FinMindClient(token="fake", cache_dir=tmp_path)
    with patch("requests.get") as mock_get:
        result = client.get_institutional("3413", date(2024, 1, 1), date.today())
    mock_get.assert_not_called()
    assert not result.empty


def test_finmind_fetches_when_no_cache(tmp_path: Path) -> None:
    """無快取時應呼叫 FinMind API。"""
    api_response = {
        "status": 200,
        "data": [
            {
                "date": "2024-01-02",
                "stock_id": "3413",
                "name": "外資",
                "buy": "1000000",
                "sell": "200000",
            }
        ],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = api_response
    mock_resp.raise_for_status = MagicMock()

    client = FinMindClient(token="fake", cache_dir=tmp_path)
    with patch("requests.get", return_value=mock_resp):
        result = client.get_institutional("3413", date(2024, 1, 1), date(2024, 1, 31))

    assert not result.empty
    assert "net_buy" in result.columns
    assert result.iloc[0]["net_buy"] == 800_000


def test_finmind_get_per_pbr_normalizes(tmp_path: Path) -> None:
    """get_per_pbr 應把 FinMind 的 PER/PBR 欄位轉為小寫，並保留 dividend_yield。"""
    api_response = {
        "status": 200,
        "data": [
            {"date": "2024-01-02", "stock_id": "3413", "PER": "18.5", "PBR": "2.3", "dividend_yield": "1.5"},
            {"date": "2024-01-03", "stock_id": "3413", "PER": "18.7", "PBR": "2.4", "dividend_yield": "1.5"},
        ],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = api_response
    mock_resp.raise_for_status = MagicMock()

    client = FinMindClient(token="fake", cache_dir=tmp_path)
    with patch("requests.get", return_value=mock_resp):
        result = client.get_per_pbr("3413", date(2024, 1, 1), date(2024, 1, 31))

    assert set(result.columns) == {"date", "per", "pbr", "dividend_yield"}
    assert result.iloc[0]["pbr"] == pytest.approx(2.3)
    assert result.iloc[1]["per"] == pytest.approx(18.7)


def test_finmind_get_per_pbr_empty_response(tmp_path: Path) -> None:
    """空資料應優雅降級（不 raise）。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": 200, "data": []}
    mock_resp.raise_for_status = MagicMock()

    client = FinMindClient(token="fake", cache_dir=tmp_path)
    with patch("requests.get", return_value=mock_resp):
        result = client.get_per_pbr("9999", date(2024, 1, 1), date(2024, 1, 31))
    assert result.empty


def test_finmind_raises_on_api_error(tmp_path: Path) -> None:
    api_response = {"status": 403, "msg": "Forbidden"}
    mock_resp = MagicMock()
    mock_resp.json.return_value = api_response
    mock_resp.raise_for_status = MagicMock()

    client = FinMindClient(token="bad_token", cache_dir=tmp_path)
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="403"):
            client.get_institutional("3413", date(2024, 1, 1), date(2024, 1, 31))


# ─────────────────────────────────────────
# FugleClient — fallback to yfinance
# ─────────────────────────────────────────
def test_fugle_client_falls_back_without_key() -> None:
    """無 API key 時應退到 yfinance 模式（不 raise）。"""
    client = FugleClient(api_key=None)
    assert client._mode == "yfinance"


def test_fugle_yfinance_quote(tmp_path: Path) -> None:
    """yfinance 降級模式應正確回傳收盤價。"""
    mock_close = pd.Series([185.5, 187.0], index=pd.date_range("2024-01-02", periods=2))
    mock_hist = pd.DataFrame({"Close": mock_close})

    client = FugleClient(api_key=None)
    with patch("yfinance.download", return_value=mock_hist):
        price = client.get_realtime_quote("3413")
    assert price == pytest.approx(187.0)


def test_fugle_price_fetcher_callable() -> None:
    client = FugleClient(api_key=None)
    fetcher = client.as_price_fetcher()
    assert callable(fetcher)
    # bound method 每次取用建新物件，驗證行為等同即可
    mock_close = pd.Series([190.0], index=pd.date_range("2024-01-02", periods=1))
    mock_hist = pd.DataFrame({"Close": mock_close})
    with patch("yfinance.download", return_value=mock_hist):
        assert fetcher("3413") == pytest.approx(190.0)


# ─────────────────────────────────────────
# adr_fetcher — get_tw_ohlcv_adjusted
# ─────────────────────────────────────────
def _fake_yf_hist() -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": [180.0] * 5,
            "High": [185.0] * 5,
            "Low": [178.0] * 5,
            "Close": [182.0, 183.0, 181.0, 184.0, 185.0],
            "Volume": [10000] * 5,
        }
    ).set_index("Date")


def test_tw_ohlcv_adjusted_columns(tmp_path: Path) -> None:
    """回傳 DataFrame 應含正確欄位，close 為還原股價。"""
    with patch("yfinance.download", return_value=_fake_yf_hist()):
        df = get_tw_ohlcv_adjusted("3413", date(2024, 1, 2), date(2024, 1, 8), cache_dir=tmp_path)

    assert set(["date", "open", "high", "low", "close", "volume"]).issubset(df.columns)
    assert df["close"].iloc[0] == pytest.approx(182.0)


def test_tw_ohlcv_caches_to_parquet(tmp_path: Path) -> None:
    """首次呼叫後應在 cache_dir 產出 parquet 檔。"""
    with patch("yfinance.download", return_value=_fake_yf_hist()):
        get_tw_ohlcv_adjusted("3413", date(2024, 1, 2), date(2024, 1, 8), cache_dir=tmp_path)

    parquet_files = list(tmp_path.rglob("*.parquet"))
    assert len(parquet_files) == 1


def test_tw_ohlcv_cache_hit_skips_api(tmp_path: Path) -> None:
    """快取新鮮時第二次不應呼叫 yfinance。"""
    with patch("yfinance.download", return_value=_fake_yf_hist()) as mock_dl:
        get_tw_ohlcv_adjusted("3413", date(2024, 1, 2), date(2024, 1, 8), cache_dir=tmp_path)
        call_count_first = mock_dl.call_count

    # Inject fresh cache (last date = yesterday)
    parquet = list(tmp_path.rglob("*.parquet"))[0]
    df = pd.read_parquet(parquet)
    yesterday = date.today() - __import__("datetime").timedelta(days=1)
    df.loc[len(df)] = {
        "date": yesterday, "open": 186, "high": 188, "low": 185, "close": 187, "volume": 9999,
    }
    df.to_parquet(parquet, index=False)

    with patch("yfinance.download", return_value=_fake_yf_hist()) as mock_dl2:
        get_tw_ohlcv_adjusted("3413", date(2024, 1, 2), date(2024, 1, 8), cache_dir=tmp_path)
    mock_dl2.assert_not_called()


# ─────────────────────────────────────────
# adr_fetcher — get_overnight_report
# ─────────────────────────────────────────
def _fake_overnight_download(symbol: str, **kwargs) -> pd.DataFrame:
    prices = {
        "TSM": [175.0, 178.0],
        "NVDA": [850.0, 870.0],
        "SOXX": [210.0, 212.0],
        "^VIX": [16.0, 16.5],
    }
    vals = prices.get(symbol, [100.0, 101.0])
    idx = pd.date_range("2024-01-02", periods=len(vals))
    return pd.DataFrame({"Close": vals}, index=idx)


def test_overnight_report_structure() -> None:
    with patch("yfinance.download", side_effect=_fake_overnight_download):
        report = get_overnight_report(date(2024, 1, 3))

    assert report["market_mode"] in ("normal", "caution", "defensive")
    assert "tsmc_adr_change_pct" in report
    assert isinstance(report["vix"], float)


def test_overnight_report_defensive_on_tsmc_drop() -> None:
    """TSMC ADR 暴跌超過 3% 應觸發 defensive 模式。"""
    def drop_download(symbol: str, **kwargs) -> pd.DataFrame:
        prices = {
            "TSM": [180.0, 170.0],   # −5.6%
            "NVDA": [850.0, 848.0],
            "SOXX": [210.0, 209.0],
            "^VIX": [16.0, 17.0],
        }
        vals = prices.get(symbol, [100.0, 100.0])
        idx = pd.date_range("2024-01-02", periods=len(vals))
        return pd.DataFrame({"Close": vals}, index=idx)

    with patch("yfinance.download", side_effect=drop_download):
        report = get_overnight_report()
    assert report["market_mode"] == "defensive"
