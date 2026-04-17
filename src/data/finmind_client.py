"""
FinMind REST API 客戶端 — 三大法人、分點籌碼、融資融券。

注意：價格資料（日 K / 還原股價）請用 adr_fetcher.get_tw_ohlcv_adjusted()。
FinMind 此處僅提供 台股特有的籌碼面資料。

每個 (dataset, ticker) 結果以 parquet 快取（data/cache/finmind/）。
首次請求抓 2017-01-01 至今；後續只增量補抓最新幾天。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from src.data._cache import (
    cache_path,
    is_cache_fresh,
    load_cache,
    save_cache,
    slice_by_date,
)

_API_URL = "https://api.finmindtrade.com/api/v4/data"
_HISTORY_START = date(2017, 1, 1)
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "finmind"


class FinMindClient:
    def __init__(self, token: str, cache_dir: Path = _DEFAULT_CACHE) -> None:
        self.token = token
        self.cache_dir = cache_dir

    # ─────────────────────────────────────
    # 三大法人買賣超
    # ─────────────────────────────────────
    def get_institutional(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """
        回傳欄位: date | name(外資/投信/自營) | buy | sell | net_buy
        """
        return self._get(
            dataset="TaiwanStockInstitutionalInvestorsBuySell",
            ticker=ticker,
            start=start,
            end=end,
            normalize=self._norm_institutional,
        )

    # ─────────────────────────────────────
    # 分點籌碼（主力券商買賣）
    # ─────────────────────────────────────
    def get_broker_distribution(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """
        回傳欄位: date | broker_id | broker_name | buy | sell | net_buy
        """
        return self._get(
            dataset="TaiwanStockTradingDailyReport",
            ticker=ticker,
            start=start,
            end=end,
            normalize=self._norm_broker,
        )

    # ─────────────────────────────────────
    # 融資融券
    # ─────────────────────────────────────
    def get_margin(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """
        回傳欄位: date | margin_purchase | short_sale | margin_balance | short_balance
        """
        return self._get(
            dataset="TaiwanStockMarginPurchaseShortSale",
            ticker=ticker,
            start=start,
            end=end,
            normalize=self._norm_margin,
        )

    # ─────────────────────────────────────
    # 內部工具
    # ─────────────────────────────────────
    def _get(
        self,
        dataset: str,
        ticker: str,
        start: date,
        end: date,
        normalize,
    ) -> pd.DataFrame:
        key = f"{dataset}_{ticker}"
        path = cache_path(self.cache_dir, "finmind", key)
        cached = load_cache(path)

        if is_cache_fresh(cached):
            df = cached
        elif cached is not None:
            last = pd.to_datetime(cached["date"]).dt.date.max()
            fetch_start = last + timedelta(days=1)
            new = self._fetch(dataset, ticker, fetch_start, date.today())
            df = pd.concat([cached, new], ignore_index=True).drop_duplicates(
                subset="date", keep="last"
            )
            save_cache(path, df)
        else:
            df = self._fetch(dataset, ticker, _HISTORY_START, date.today())
            save_cache(path, df)

        if df is None or df.empty:
            return pd.DataFrame()

        normalized = normalize(df)
        return slice_by_date(normalized, start, end)

    def _fetch(
        self, dataset: str, ticker: str, start: date, end: date
    ) -> pd.DataFrame:
        params: dict[str, Any] = {
            "dataset": dataset,
            "data_id": ticker,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "token": self.token,
        }
        resp = requests.get(_API_URL, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != 200:
            raise RuntimeError(
                f"FinMind API error [{payload.get('status')}]: {payload.get('msg')}"
            )
        df = pd.DataFrame(payload.get("data", []))
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df

    # ─────────────────────────────────────
    # 欄位正規化
    # ─────────────────────────────────────
    @staticmethod
    def _norm_institutional(df: pd.DataFrame) -> pd.DataFrame:
        rename = {
            "stock_id": "ticker",
            "name": "name",
            "buy": "buy",
            "sell": "sell",
        }
        out = df.rename(columns=rename).copy()
        for col in ("buy", "sell"):
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
        out["net_buy"] = out["buy"] - out["sell"]
        return out[["date", "name", "buy", "sell", "net_buy"]]

    @staticmethod
    def _norm_broker(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for col in ("buy", "sell"):
            out[col] = pd.to_numeric(out.get(col, 0), errors="coerce").fillna(0).astype(int)
        out["net_buy"] = out["buy"] - out["sell"]
        renames = {"broker_id": "broker_id", "broker_name": "broker_name"}
        out = out.rename(columns={k: v for k, v in renames.items() if k in out})
        cols = [c for c in ("date", "broker_id", "broker_name", "buy", "sell", "net_buy") if c in out]
        return out[cols]

    @staticmethod
    def _norm_margin(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        col_map = {
            "MarginPurchaseBuy": "margin_purchase",
            "ShortSaleSell": "short_sale",
            "MarginPurchaseLimit": "margin_balance",
            "ShortSaleLimit": "short_balance",
        }
        out = out.rename(columns={k: v for k, v in col_map.items() if k in out})
        for col in col_map.values():
            if col in out:
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
        keep = [c for c in ("date", "margin_purchase", "short_sale", "margin_balance", "short_balance") if c in out]
        return out[keep]
