"""
MOPS（公開資訊觀測站）月營收自抓器 — FinMind get_monthly_revenue 的替代方案。

優勢：
  - 官方直接來源，永久免費
  - 每月只需 2 次 HTTP（上市 + 上櫃各一頁），比個股 API 省流量
  - 格式在過去 10 年幾乎沒變

限制：
  - 每月公告在下月 10 日前（T+10 延遲）
  - HTML table 解析，偶爾格式微調
  - Big5 encoding 需特別處理

Cache：
  data/cache/mops/revenue_{YYY}_{MM}_{market}.parquet
  (民國年 / 月份 / market: sii 上市 or otc 上櫃)
"""
from __future__ import annotations

import time
from datetime import date
from io import BytesIO, StringIO
from pathlib import Path

import pandas as pd
import requests

_BASE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "mops"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


class MOPSClient:
    def __init__(self, cache_dir: Path = _BASE_DIR, polite_delay: float = 2.0) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.polite_delay = polite_delay      # MOPS 較嚴格，建議 2 秒

    def get_monthly_revenue_batch(
        self, year: int, month: int, market: str = "sii"
    ) -> pd.DataFrame:
        """
        抓某 (西元年, 月) 的全市場月營收（每家公司一筆）。
        market: "sii" 上市 / "otc" 上櫃
        回傳欄位: ticker | name | revenue | revenue_last_month | revenue_yoy_pct | revenue_mom_pct
        """
        roc_year = year - 1911
        cache = self.cache_dir / f"revenue_{roc_year}_{month:02d}_{market}.parquet"
        if cache.exists():
            try:
                return pd.read_parquet(cache)
            except Exception:
                cache.unlink(missing_ok=True)

        url = (
            f"https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_"
            f"{roc_year}_{month}.html"
        )
        html_bytes = self._get_bytes(url)
        if not html_bytes:
            return pd.DataFrame()

        try:
            # MOPS 用 Big5
            tables = pd.read_html(
                BytesIO(html_bytes), encoding="big5",
                flavor="lxml",    # 需要 lxml 否則用 html5lib
                match="公司代號",
            )
        except ImportError:
            # 沒 lxml 也試試用 html5lib/bs4
            try:
                tables = pd.read_html(
                    BytesIO(html_bytes), encoding="big5", match="公司代號",
                )
            except Exception:
                return pd.DataFrame()
        except Exception:
            return pd.DataFrame()

        if not tables:
            return pd.DataFrame()

        # MOPS 有多個產業分類 table，合併全部
        merged = pd.concat(tables, ignore_index=True)
        out = self._normalize_revenue_table(merged, year, month)
        if not out.empty:
            out.to_parquet(cache, index=False)
        return out

    @staticmethod
    def _normalize_revenue_table(df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
        if df.empty:
            return df
        # MOPS 欄位名（2026 版本）：
        #   公司代號 | 公司名稱 | 當月營收 | 上月營收 | 去年當月營收
        #   | 上月比較增減(%) | 去年同月增減(%) | 當月累計營收 | 去年累計營收 | 前期比較增減(%)
        col_map = {}
        for c in df.columns:
            cs = str(c).strip()
            if "公司代號" in cs:
                col_map[c] = "ticker"
            elif "公司名稱" in cs:
                col_map[c] = "name"
            elif "當月營收" == cs or cs == "當月營收":
                col_map[c] = "revenue"
            elif "上月營收" in cs:
                col_map[c] = "revenue_last_month"
            elif "去年當月營收" in cs:
                col_map[c] = "revenue_year_ago"
            elif "上月比較" in cs and "增減" in cs:
                col_map[c] = "revenue_mom_pct"
            elif "去年同月" in cs and "增減" in cs:
                col_map[c] = "revenue_yoy_pct"

        out = df.rename(columns=col_map).copy()
        # 過濾：只留有 ticker 的列（MOPS 表頭與產業分隔列會有空 ticker）
        if "ticker" not in out.columns:
            return pd.DataFrame()
        out["ticker"] = out["ticker"].astype(str).str.strip()
        out = out[out["ticker"].str.len() == 4]
        out = out[out["ticker"].str[0].str.isdigit()]

        for col in ("revenue", "revenue_last_month", "revenue_year_ago",
                     "revenue_mom_pct", "revenue_yoy_pct"):
            if col in out.columns:
                out[col] = pd.to_numeric(
                    out[col].astype(str).str.replace(",", "").replace(["-", "", "--"], pd.NA),
                    errors="coerce",
                )

        # 公告日：假設該月 10 日（保守估計）
        out["announce_date"] = date(year, month, 10)
        out["revenue_year"] = year
        out["revenue_month"] = month

        keep = [
            "announce_date", "ticker", "name", "revenue",
            "revenue_last_month", "revenue_year_ago",
            "revenue_mom_pct", "revenue_yoy_pct",
            "revenue_year", "revenue_month",
        ]
        return out[[c for c in keep if c in out.columns]]

    def build_ticker_revenue_history(
        self,
        ticker: str,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
    ) -> pd.DataFrame:
        """
        對某 ticker 取跨月的營收時序（會遍歷 [start..end] 每月 batch）。
        回傳: date (announce_date) | revenue | revenue_yoy | revenue_mom
        """
        rows = []
        y, m = start_year, start_month
        while (y, m) <= (end_year, end_month):
            for market in ("sii", "otc"):
                df = self.get_monthly_revenue_batch(y, m, market=market)
                if df.empty:
                    continue
                hit = df[df["ticker"] == str(ticker)]
                if hit.empty:
                    continue
                r = hit.iloc[0]
                rows.append(
                    {
                        "date": r.get("announce_date"),
                        "revenue": r.get("revenue"),
                        "revenue_yoy": r.get("revenue_yoy_pct"),
                        "revenue_mom": r.get("revenue_mom_pct"),
                    }
                )
                break   # 找到就不查另一個 market
            m += 1
            if m > 12:
                m = 1
                y += 1
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def _get_bytes(self, url: str) -> bytes | None:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=60)
            time.sleep(self.polite_delay)
            if resp.status_code != 200:
                return None
            return resp.content
        except Exception:
            return None
