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

    @staticmethod
    def _yfinance_quote(ticker: str) -> float:
        import pandas as pd
        import yfinance as yf  # type: ignore

        tw_ticker = ticker if "." in ticker else f"{ticker}.TW"
        hist = yf.download(tw_ticker, period="5d", auto_adjust=True, progress=False)
        if hist.empty:
            raise ValueError(f"yfinance 無法取得 {tw_ticker} 的報價")
        # 新版 yfinance 單 ticker 也回 MultiIndex columns → 展平
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        close = hist["Close"].dropna()
        if close.empty:
            raise ValueError(f"yfinance {tw_ticker} 近 5 日無收盤價")
        last = close.iloc[-1]
        # squeeze() 處理單一元素殘留 Series 的情境
        if hasattr(last, "item"):
            return float(last.item() if hasattr(last, "item") else last)
        return float(last)
