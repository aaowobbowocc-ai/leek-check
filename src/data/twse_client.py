"""
TWSE / TPEX 官方每日 CSV 自抓器 — FinMind 的免費替代方案。

覆蓋功能（與 FinMindClient 介面對齊，方便未來切換）：
  - get_per_pbr_day(d): 某日全市場 P/E、P/B、殖利率
  - get_institutional_day(d): 某日三大法人買賣超（全市場）

優勢：
  - 官方原始資料，無 API 額度限制
  - 永久免費
  - TWSE / TPEX 格式幾乎 10 年不變（我們用的欄位）

限制：
  - 每個日期一次 HTTP 請求（歷史回填慢）
  - 假日沒有資料
  - TWSE 與 TPEX 格式略不同（需分開處理）

Cache：每日一個 parquet `data/cache/twse/{dataset}_{YYYYMMDD}.parquet`
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

_BASE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "twse"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


class TWSEClient:
    """TWSE / TPEX 公開資料下載 + 快取。"""

    def __init__(self, cache_dir: Path = _BASE_DIR, polite_delay: float = 1.0) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.polite_delay = polite_delay   # TWSE 太快會擋

    # ─────────────────────────────────────
    # 每日本益比 / 殖利率 / 股價淨值比
    # ─────────────────────────────────────
    def get_per_pbr_day(self, d: date) -> pd.DataFrame:
        """
        回傳某日全市場 TWSE + TPEX 的 PER / PBR / 殖利率。
        欄位: date | ticker | name | dividend_yield | per | pbr
        """
        cache = self.cache_dir / f"per_pbr_{d.strftime('%Y%m%d')}.parquet"
        if cache.exists():
            try:
                return pd.read_parquet(cache)
            except Exception:
                cache.unlink(missing_ok=True)

        twse_df = self._fetch_twse_per_pbr(d)
        tpex_df = self._fetch_tpex_per_pbr(d)
        out = pd.concat([twse_df, tpex_df], ignore_index=True)
        if not out.empty:
            out.to_parquet(cache, index=False)
        return out

    def _fetch_twse_per_pbr(self, d: date) -> pd.DataFrame:
        """
        https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=YYYYMMDD&response=csv
        CSV 有中文欄位 + 標題行干擾，需要清理。
        """
        url = (
            "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
            f"?date={d.strftime('%Y%m%d')}&response=csv"
        )
        text = self._get_text(url)
        if not text:
            return pd.DataFrame()
        # TWSE CSV 前幾行是 metadata，data 從包含 "證券代號" 的那行開始
        lines = text.splitlines()
        header_idx = None
        # header 行必含多個欄位名；標題行只含敘述 → 要求 "證券名稱" 與 "殖利率" 同時在
        for i, line in enumerate(lines):
            if "證券代號" in line and "證券名稱" in line and "殖利率" in line:
                header_idx = i
                break
        if header_idx is None:
            return pd.DataFrame()
        csv_chunk = "\n".join(lines[header_idx:])
        try:
            df = pd.read_csv(StringIO(csv_chunk))
        except Exception:
            return pd.DataFrame()
        return self._normalize_per_pbr(df, d, source="twse")

    def _fetch_tpex_per_pbr(self, d: date) -> pd.DataFrame:
        """
        https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php
        TPEX 用 POST 形式較麻煩，這裡用 GET 版 CSV（2025 年起官方提供）。
        """
        roc_year = d.year - 1911
        url = (
            "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_print.php"
            f"?l=zh-tw&d={roc_year}/{d.month:02d}/{d.day:02d}&s=0,asc,0"
        )
        text = self._get_text(url)
        if not text:
            return pd.DataFrame()
        # TPEX 格式較不穩定，MVP 版先回空、保留擴充點
        return pd.DataFrame()

    @staticmethod
    def _normalize_per_pbr(df: pd.DataFrame, d: date, source: str) -> pd.DataFrame:
        if df.empty:
            return df
        # 清除欄位名殘留引號與空白（TWSE CSV 的欄位常被包引號）
        df.columns = [str(c).strip().strip('"') for c in df.columns]
        col_map = {
            "證券代號": "ticker",
            "證券名稱": "name",
            "殖利率(%)": "dividend_yield",
            "本益比": "per",
            "股價淨值比": "pbr",
        }
        out = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}).copy()
        # 清理 ticker：移除引號、空白
        if "ticker" in out:
            out["ticker"] = out["ticker"].astype(str).str.strip().str.replace('"', "", regex=False)
        # 數字欄位：處理「-」「--」等缺值
        for col in ("dividend_yield", "per", "pbr"):
            if col in out:
                out[col] = pd.to_numeric(
                    out[col].astype(str).str.replace(",", "").replace(["-", "--", ""], pd.NA),
                    errors="coerce",
                )
        out["date"] = d
        out["source"] = source
        keep = [c for c in ("date", "ticker", "name", "dividend_yield", "per", "pbr", "source") if c in out]
        # 過濾無效代號（header 殘留）
        return out[keep].dropna(subset=["ticker"]).query("ticker.str.len() == 4", engine="python")

    # ─────────────────────────────────────
    # 三大法人買賣超
    # ─────────────────────────────────────
    def get_institutional_day(self, d: date) -> pd.DataFrame:
        """
        https://www.twse.com.tw/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALL&response=csv
        回傳欄位: date | ticker | name | foreign_net | inv_trust_net | dealer_net | total_net
        """
        cache = self.cache_dir / f"institutional_{d.strftime('%Y%m%d')}.parquet"
        if cache.exists():
            try:
                return pd.read_parquet(cache)
            except Exception:
                cache.unlink(missing_ok=True)

        url = (
            "https://www.twse.com.tw/rwd/zh/fund/T86"
            f"?date={d.strftime('%Y%m%d')}&selectType=ALL&response=csv"
        )
        text = self._get_text(url)
        if not text:
            return pd.DataFrame()
        lines = text.splitlines()
        header_idx = None
        for i, line in enumerate(lines):
            if "證券代號" in line and "證券名稱" in line and "外陸資" in line:
                header_idx = i
                break
        if header_idx is None:
            return pd.DataFrame()
        try:
            df = pd.read_csv(StringIO("\n".join(lines[header_idx:])))
        except Exception:
            return pd.DataFrame()
        df.columns = [str(c).strip().strip('"') for c in df.columns]

        # TWSE 欄位較多，挑出常用
        col_map = {
            "證券代號": "ticker",
            "證券名稱": "name",
            "外陸資買賣超股數(不含外資自營商)": "foreign_net",
            "投信買賣超股數": "inv_trust_net",
            "自營商買賣超股數": "dealer_net",
            "三大法人買賣超股數": "total_net",
        }
        out = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}).copy()
        if "ticker" in out:
            out["ticker"] = out["ticker"].astype(str).str.strip().str.replace('"', "", regex=False)
        for col in ("foreign_net", "inv_trust_net", "dealer_net", "total_net"):
            if col in out:
                out[col] = pd.to_numeric(
                    out[col].astype(str).str.replace(",", ""), errors="coerce"
                )
        out["date"] = d
        keep = [c for c in (
            "date", "ticker", "name", "foreign_net", "inv_trust_net", "dealer_net", "total_net"
        ) if c in out]
        out = out[keep].dropna(subset=["ticker"])
        out = out.query("ticker.str.len() == 4", engine="python")
        if not out.empty:
            out.to_parquet(cache, index=False)
        return out

    # ─────────────────────────────────────
    # 內部工具
    # ─────────────────────────────────────
    def _get_text(self, url: str) -> str:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            resp.encoding = resp.apparent_encoding or "utf-8"
            time.sleep(self.polite_delay)
            if resp.status_code != 200:
                return ""
            return resp.text
        except Exception:
            return ""

    # ─────────────────────────────────────
    # 批次：某 ticker 的歷史 PER 序列
    # ─────────────────────────────────────
    def build_ticker_per_history(
        self,
        ticker: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """
        遍歷 [start, end] 每日 CSV，抽出 ticker 的 PER/PBR 時序。
        有 per-day cache，重跑時只抓新日。
        回傳：date | per | pbr | dividend_yield
        """
        rows = []
        cur = start
        while cur <= end:
            # 跳過週末（TWSE 無資料）
            if cur.weekday() < 5:
                df = self.get_per_pbr_day(cur)
                if not df.empty:
                    hit = df[df["ticker"] == str(ticker)]
                    if not hit.empty:
                        rows.append(hit.iloc[0].to_dict())
            cur += timedelta(days=1)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)[["date", "per", "pbr", "dividend_yield"]]
