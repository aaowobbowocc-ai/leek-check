"""
FinMind REST API 客戶端 — 三大法人、分點籌碼、融資融券。

注意：價格資料（日 K / 還原股價）請用 adr_fetcher.get_tw_ohlcv_adjusted()。
FinMind 此處僅提供 台股特有的籌碼面資料。

每個 (dataset, ticker) 結果以 parquet 快取（data/cache/finmind/）。
首次請求抓 2017-01-01 至今；後續只增量補抓最新幾天。

─────────────────────────────────────────────────────────────
FinMind 資費方案 × 本模組可用功能（2026-04 現況）
─────────────────────────────────────────────────────────────
| 方案            | 月費        | 解鎖本模組函式                                    |
|-----------------|-------------|-------------------------------------------------|
| Free (目前)     | $0          | get_institutional / get_margin / get_per_pbr /  |
|                 |             |   get_foreign_ownership                         |
| Backer          | NT$699      | + get_adjusted_price（還原股價）                 |
|                 |             |   → 可補 yfinance 對中型台股的 2018 前資料缺洞    |
| Sponsor (推薦)  | NT$999      | + get_broker_distribution（分點籌碼）            |
|                 |             |   → 激活 chip_factor 的 day_trader_risk flag     |
|                 |             |   → 補 2018/2022 年投信空窗期的訊號              |
| Sponsor Pro     | NT$3330     | + Tick Data（本專案不用）                        |

決策理由：
- 本模組的 chip_factor 有兩條訊號：(a) 投信連買  (b) 分點集中度
- Free 只能驅動 (a)；2018/2022 年投信保守時 chip 因子整個歸零
- Sponsor 補齊 (b) → debug 實測能讓這兩年從 0 trades 變回正常
- Backer 的還原股價 yfinance 已免費提供 80%+，優先度低
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
    # 分點籌碼（主力券商買賣） — 需 Sponsor 方案（NT$999/月）
    # ─────────────────────────────────────
    def get_broker_distribution(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """
        回傳欄位: date | broker_id | broker_name | buy | sell | net_buy

        ⚠️ 需 FinMind Sponsor 方案；Free / Backer 會回傳 400。
        FinMind TaiwanStockTradingDailyReport 單次僅回一天 → 需逐日抓並累積快取。
        同一 (ticker, date) 抓過就不再打 API。
        """
        key = f"TaiwanStockTradingDailyReport_{ticker}"
        path = cache_path(self.cache_dir, "finmind", key)
        cached = load_cache(path)
        cached_dates: set[date] = set()
        if cached is not None and "date" in cached:
            cached_dates = set(pd.to_datetime(cached["date"]).dt.date.unique())

        # 只抓快取沒有的工作日（週末 FinMind 會回空，我們也快取起來避免重抓）
        need_days: list[date] = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5 and cursor not in cached_dates:
                need_days.append(cursor)
            cursor += timedelta(days=1)

        new_frames: list[pd.DataFrame] = []
        for i, d in enumerate(need_days, 1):
            if i == 1 or i % 50 == 0 or i == len(need_days):
                print(f"      broker {ticker} {d} ({i}/{len(need_days)})", flush=True)
            try:
                day_df = self._fetch_single_day(
                    "TaiwanStockTradingDailyReport", ticker, d
                )
            except Exception as e:
                print(f"      broker {ticker} {d} 失敗：{e}", flush=True)
                day_df = pd.DataFrame()
            # 無論有沒有資料都記一筆 sentinel，避免下次重抓
            if day_df.empty:
                day_df = pd.DataFrame([{"date": d, "broker_id": "", "broker_name": "", "buy": 0, "sell": 0}])
            else:
                day_df = self._aggregate_broker_day(day_df)
            new_frames.append(day_df)

        if new_frames:
            combined = pd.concat(([cached] if cached is not None else []) + new_frames, ignore_index=True)
            combined["date"] = pd.to_datetime(combined["date"]).dt.date
            combined = combined.drop_duplicates(subset=["date", "broker_id"], keep="last")
            save_cache(path, combined)
            df = combined
        else:
            df = cached if cached is not None else pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()
        normalized = self._norm_broker(df)
        return slice_by_date(normalized, start, end)

    def _fetch_single_day(self, dataset: str, ticker: str, d: date) -> pd.DataFrame:
        """FinMind 單日取得 — 不帶 end_date（某些 dataset 限制）。"""
        params = {
            "dataset": dataset,
            "data_id": ticker,
            "start_date": d.isoformat(),
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

    @staticmethod
    def _aggregate_broker_day(raw: pd.DataFrame) -> pd.DataFrame:
        """
        單日分點原始資料每筆為 (broker × 價位) 的買賣張數，需按 broker 聚合。
        欄位: securities_trader, securities_trader_id, buy, sell, price, date, stock_id
        """
        if raw.empty:
            return raw
        df = raw.copy()
        df["broker_id"] = df.get("securities_trader_id", "").astype(str)
        df["broker_name"] = df.get("securities_trader", "").astype(str)
        df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0).astype(int)
        df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0).astype(int)
        agg = (
            df.groupby(["date", "broker_id", "broker_name"], as_index=False)[["buy", "sell"]].sum()
        )
        return agg

    # ─────────────────────────────────────
    # 還原股價（Backer+ 方案補 yfinance 2018 前缺洞用）— 暫不啟用
    # ─────────────────────────────────────
    def get_adjusted_price(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """
        ⚠️ 需 FinMind Backer 方案（NT$699/月）；Free 會回 400。
        實際運作下優先使用 adr_fetcher.get_tw_ohlcv_adjusted()（yfinance auto_adjust），
        只有當 yfinance 抓不到（如部分中型股 2018 前）才 fallback 到此函式。

        回傳欄位: date | open | high | low | close | volume（已處理除權息）
        """
        return self._get(
            dataset="TaiwanStockPriceAdj",
            ticker=ticker,
            start=start,
            end=end,
            normalize=self._norm_adjusted_price,
        )

    @staticmethod
    def _norm_adjusted_price(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        out = df.rename(columns={
            "Trading_Volume": "volume",
            "max": "high", "min": "low",
            "open": "open", "close": "close",
        })
        keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in out.columns]
        return out[keep]

    # ─────────────────────────────────────
    # 外資持股率（每日）— Free 方案可用
    # 用途：補足 Free 方案無分點資料時的主力訊號
    # 原規劃用集保大戶持股（TaiwanStockHoldingSharesPer）但該 dataset 屬 Sponsor 限定；
    # TaiwanStockShareholding 提供外資持股率，對 AI 設備股（家登/奇鋐等外資推動標的）更直接。
    # ─────────────────────────────────────
    def get_foreign_ownership(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """
        回傳欄位: date | foreign_pct | foreign_shares | shares_issued
        foreign_pct = ForeignInvestmentSharesRatio（外資實際持股佔已發行股份 %）
        """
        return self._get(
            dataset="TaiwanStockShareholding",
            ticker=ticker,
            start=start,
            end=end,
            normalize=self._norm_foreign_ownership,
        )

    @staticmethod
    def _norm_foreign_ownership(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        out = df.rename(
            columns={
                "ForeignInvestmentSharesRatio": "foreign_pct",
                "ForeignInvestmentShares": "foreign_shares",
                "NumberOfSharesIssued": "shares_issued",
            }
        ).copy()
        for col in ("foreign_pct", "foreign_shares", "shares_issued"):
            if col in out:
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
        keep = [c for c in ("date", "foreign_pct", "foreign_shares", "shares_issued") if c in out]
        return out[keep]

    # ─────────────────────────────────────
    # PER / PBR / 現金殖利率（每日）— Free 方案可用
    # 用途：Phase 12 Valuation Guard — 計算 PBR 5 年歷史百分位，過高則 composite 扣分
    # ─────────────────────────────────────
    def get_per_pbr(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """
        回傳欄位: date | per | pbr | dividend_yield
        FinMind TaiwanStockPER 欄位: PER | PBR | dividend_yield
        """
        return self._get(
            dataset="TaiwanStockPER",
            ticker=ticker,
            start=start,
            end=end,
            normalize=self._norm_per_pbr,
        )

    @staticmethod
    def _norm_per_pbr(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        out = df.rename(
            columns={
                "PER": "per",
                "PBR": "pbr",
                "dividend_yield": "dividend_yield",
            }
        ).copy()
        for col in ("per", "pbr", "dividend_yield"):
            if col in out:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        keep = [c for c in ("date", "per", "pbr", "dividend_yield") if c in out]
        return out[keep]

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
        """
        FinMind TaiwanStockMarginPurchaseShortSale 欄位語意：
          - MarginPurchaseTodayBalance / YesterdayBalance：融資餘額（每日變動的真實散戶部位）
          - MarginPurchaseLimit：融資限額（券商給的上限，幾乎常數）
          - MarginPurchaseBuy / Sell：當日融資買/賣張數
        Bug fix：原本誤把 Limit 當餘額（近乎常數），導致 chip_factor 的 margin 背離訊號從不觸發。
        """
        out = df.copy()
        col_map = {
            "MarginPurchaseBuy": "margin_purchase",
            "ShortSaleSell": "short_sale",
            "MarginPurchaseTodayBalance": "margin_balance",
            "ShortSaleTodayBalance": "short_balance",
        }
        out = out.rename(columns={k: v for k, v in col_map.items() if k in out})
        for col in col_map.values():
            if col in out:
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
        keep = [c for c in ("date", "margin_purchase", "short_sale", "margin_balance", "short_balance") if c in out]
        return out[keep]
