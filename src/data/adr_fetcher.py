"""
ADR 夜盤 + 台股還原股價（yfinance）。

主要功能：
1. get_overnight_report(as_of_date) — 每日晨報用的美股夜盤數據
   回傳 TSMC ADR / NVDA / SOX ETF / VIX / 台積電昨收
2. get_tw_ohlcv_adjusted(ticker, start, end) — 回測用的還原股價
   使用 yfinance ".TW" 自動處理除權息（auto_adjust=True）
   結果以 parquet 快取（增量更新）

還原股價說明：
yfinance auto_adjust=True 使用 Yahoo Finance 的「還原收盤價」演算法，
已自動回調所有歷史除息跳空，止損計算不會被除息日誤觸發。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict

import pandas as pd

from src.data._cache import (
    cache_path,
    is_cache_fresh,
    load_cache,
    save_cache,
    slice_by_date,
)

_HISTORY_START = date(2017, 1, 1)
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "yfinance"

_OVERNIGHT_TICKERS = {
    "tsmc_adr": "TSM",
    "nvda": "NVDA",
    "sox_etf": "SOXX",
    "vix": "^VIX",
}


class OvernightReport(TypedDict):
    as_of_date: str
    tsmc_adr_close: float
    tsmc_adr_change_pct: float
    nvda_close: float
    nvda_change_pct: float
    sox_close: float
    sox_change_pct: float
    vix: float
    market_mode: str  # "normal" | "caution" | "defensive"


def get_overnight_report(as_of_date: date | None = None) -> OvernightReport:
    """
    取得 as_of_date 前一個美股交易日的收盤數據，供晨報的大盤背景欄位使用。
    as_of_date 預設為今天（台灣時間）。
    """
    import yfinance as yf  # type: ignore

    target = as_of_date or date.today()
    # 抓最近 5 個交易日確保取到有效收盤
    data: dict[str, pd.DataFrame] = {}
    for key, sym in _OVERNIGHT_TICKERS.items():
        hist = yf.download(sym, period="5d", auto_adjust=True, progress=False)
        if hist.empty:
            data[key] = pd.Series(dtype=float)
        else:
            # yfinance ≥ 0.2.x 回傳 MultiIndex columns，壓平後取 Close
            if isinstance(hist.columns, pd.MultiIndex):
                hist.columns = hist.columns.get_level_values(0)
            close_col = hist.get("Close", hist.get("close", pd.Series(dtype=float)))
            data[key] = close_col.squeeze().dropna() if close_col is not None else pd.Series(dtype=float)

    def last_close(series: pd.Series) -> float:
        return float(series.iloc[-1]) if not series.empty else float("nan")

    def prev_close(series: pd.Series) -> float:
        return float(series.iloc[-2]) if len(series) >= 2 else float("nan")

    def pct(cur: float, prev: float) -> float:
        if prev == 0 or prev != prev:  # nan check
            return float("nan")
        return (cur - prev) / prev * 100

    tsm_c, tsm_p = last_close(data["tsmc_adr"]), prev_close(data["tsmc_adr"])
    nvda_c, nvda_p = last_close(data["nvda"]), prev_close(data["nvda"])
    sox_c, sox_p = last_close(data["sox_etf"]), prev_close(data["sox_etf"])
    vix_v = last_close(data["vix"])

    tsm_chg = pct(tsm_c, tsm_p)
    nvda_chg = pct(nvda_c, nvda_p)
    sox_chg = pct(sox_c, sox_p)

    # 市場模式判斷（供 black_swan_filter 參考）
    if tsm_chg <= -3.0 or vix_v >= 25:
        mode = "defensive"
    elif tsm_chg <= -1.5 or vix_v >= 20:
        mode = "caution"
    else:
        mode = "normal"

    return OvernightReport(
        as_of_date=target.isoformat(),
        tsmc_adr_close=tsm_c,
        tsmc_adr_change_pct=round(tsm_chg, 2),
        nvda_close=nvda_c,
        nvda_change_pct=round(nvda_chg, 2),
        sox_close=sox_c,
        sox_change_pct=round(sox_chg, 2),
        vix=round(vix_v, 2),
        market_mode=mode,
    )


def get_tw_ohlcv_adjusted(
    ticker: str,
    start: date,
    end: date,
    cache_dir: Path = _DEFAULT_CACHE,
) -> pd.DataFrame:
    """
    取得台股還原股價 OHLCV（已處理除權息）。
    欄位: date(date) | open | high | low | close | volume

    yfinance auto_adjust=True 避免除息日誤觸 ATR 止損。
    """
    key = ticker
    path = cache_path(cache_dir, "tw_ohlcv", key)
    cached = load_cache(path)

    if is_cache_fresh(cached, "date"):
        return slice_by_date(cached, start, end)

    if cached is not None:
        last = pd.to_datetime(cached["date"]).dt.date.max()
        fetch_start = last - timedelta(days=5)  # 多取幾天確保重疊
        new = _yf_download(ticker, fetch_start, date.today())
        df = (
            pd.concat([cached, new], ignore_index=True)
            .drop_duplicates(subset="date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )
    else:
        df = _yf_download(ticker, _HISTORY_START, date.today())

    save_cache(path, df)
    return slice_by_date(df, start, end)


def _yf_download(ticker: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf  # type: ignore

    tw_sym = ticker if ("." in ticker or ticker.startswith("^")) else f"{ticker}.TW"
    # 多取一天確保 end 日有資料（yfinance end 是 exclusive）
    raw = yf.download(
        tw_sym,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=True,
        progress=False,
    )
    # yfinance ≥ 0.2.x 會回傳 MultiIndex columns，壓平成單層
    if isinstance(raw.columns, __import__("pandas").MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    if raw.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    raw = raw.reset_index()
    raw.columns = [c.lower() for c in raw.columns]
    raw = raw.rename(columns={"index": "date"} if "index" in raw.columns else {})
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    return raw[["date", "open", "high", "low", "close", "volume"]].dropna(
        subset=["close"]
    )
