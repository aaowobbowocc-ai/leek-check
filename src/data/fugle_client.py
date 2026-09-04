"""
Fugle MarketData 客戶端 — 即時報價。

若 FUGLE_API_KEY 未設定，自動退回 yfinance（.TW 結尾）讀取最新收盤價。
此模組設計為 AssetManager.price_fetcher 的後端：

    from src.data.fugle_client import FugleClient
    fugle = FugleClient(api_key=os.environ.get("FUGLE_API_KEY"))
    am = AssetManager(assets_path, price_fetcher=fugle.get_realtime_quote)
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


class FugleClient:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or ""
        self._mode = "fugle" if self._api_key else "yfinance"
        if self._mode == "yfinance":
            logger.info("FUGLE_API_KEY 未設定，使用 yfinance 取得最新收盤價（僅適合非盤中）")

    def get_realtime_quote(self, ticker: str) -> float:
        """
        取得最新價格（即時或收盤）。
        Fugle 模式：盤中即時 last price。
        yfinance 降級模式：前一交易日收盤價（盤前適用）。
        """
        if self._mode == "fugle":
            return self._fugle_quote(ticker)
        return self._yfinance_quote(ticker)

    def as_price_fetcher(self) -> Callable[[str], float]:
        """回傳可直接傳給 AssetManager(price_fetcher=...) 的 callable。"""
        return self.get_realtime_quote

    def _fugle_quote(self, ticker: str) -> float:
        try:
            from fugle_marketdata import RestClient  # type: ignore
            client = RestClient(api_key=self._api_key)
            quote = client.stock.intraday.quote(symbol=ticker)
            return float(quote.get("lastPrice") or quote.get("closePrice") or 0)
        except Exception as exc:
            logger.warning("Fugle API 失敗 (%s)，退回 yfinance: %s", ticker, exc)
            return self._yfinance_quote(ticker)

    # 上櫃 (TPEx) 個股 — yfinance 需要 .TWO 而非 .TW
    _TPEX_TICKERS = {"6233", "4543", "5483", "6488", "6515", "3324"}

    @classmethod
    def _yfinance_quote(cls, ticker: str) -> float:
        import pandas as pd
        import yfinance as yf  # type: ignore

        if "." in ticker:
            candidates = [ticker]
        elif ticker in cls._TPEX_TICKERS:
            candidates = [f"{ticker}.TWO", f"{ticker}.TW"]
        else:
            candidates = [f"{ticker}.TW", f"{ticker}.TWO"]

        last_err: Exception | None = None
        for tw_ticker in candidates:
            try:
                hist = yf.download(tw_ticker, period="5d", auto_adjust=True, progress=False)
                if hist.empty:
                    last_err = ValueError(f"yfinance {tw_ticker} 空資料")
                    continue
                if isinstance(hist.columns, pd.MultiIndex):
                    hist.columns = hist.columns.get_level_values(0)
                close = hist["Close"].dropna()
                if close.empty:
                    last_err = ValueError(f"yfinance {tw_ticker} 近 5 日無收盤價")
                    continue
                last = close.iloc[-1]
                if hasattr(last, "item"):
                    return float(last.item())
                return float(last)
            except Exception as exc:
                last_err = exc
                continue
        raise ValueError(f"yfinance 無法取得 {ticker} 的報價 (tried {candidates}): {last_err}")
