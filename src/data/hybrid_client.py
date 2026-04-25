"""
HybridClient — 漸進式取代 FinMind 的混合資料源 wrapper（Phase 17a 收尾）。

設計目標：
  - 介面對齊 FinMindClient（morning_briefing.py 不用大改）
  - 內部依資料類型分派到最佳免費來源
  - 分點籌碼仍保留 FinMind（無替代品）
  - 用戶下個月可以停 FinMind Sponsor，省 NT$999/月

來源分派表：
  | 資料            | 對外介面                    | 實際後端                |
  |-----------------|-----------------------------|--------------------------|
  | OHLCV           | (沒在這層；morning_briefing 直接用 yfinance) |
  | get_per_pbr     | TWSEClient.build_ticker_per_history | TWSE 每日 CSV (免費) |
  | get_institutional | TWSE per-day → ticker filter | TWSE 每日 CSV (免費) |
  | get_monthly_revenue | MOPSClient.build_ticker_revenue_history | MOPS HTML (免費) |
  | get_margin      | FinMind (TODO: 自抓 TWSE 融資融券) | FinMind |
  | get_broker_distribution | FinMind (無替代) | FinMind |
  | get_foreign_ownership | FinMind | FinMind |

切換時機：
  - 立即可切：per_pbr / institutional / monthly_revenue
  - 暫留 FinMind：broker / margin / foreign_ownership
  - 取消 Sponsor 後 chip_factor 部分訊號（雙龍取珠 / 隔日沖偵測）會失效
    → 對「ETF 配置 + 價值投資」策略無影響
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.data.finmind_client import FinMindClient
from src.data.mops_client import MOPSClient
from src.data.twse_client import TWSEClient


class HybridClient:
    """
    對外介面盡量對齊 FinMindClient — morning_briefing.py 從原本 `finmind` 換成
    `HybridClient(...)` 即可。FinMind backend 為 None 時，依賴 FinMind 的
    method 會回空 DataFrame（讓下游 chip_factor 等優雅降級）。
    """

    def __init__(
        self,
        twse: TWSEClient | None = None,
        mops: MOPSClient | None = None,
        finmind: FinMindClient | None = None,
    ) -> None:
        self.twse = twse or TWSEClient()
        self.mops = mops or MOPSClient()
        self.finmind = finmind   # None 表示要省 Sponsor 訂閱

    # ─────────────────────────────────────
    # PER / PBR / 殖利率（TWSE 替代 FinMind）
    # ─────────────────────────────────────
    def get_per_pbr(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """
        回傳 columns=['date', 'per', 'pbr', 'dividend_yield']，與 FinMindClient 對齊。
        遍歷 [start, end] 每日 CSV、抽 ticker。首次跑慢，之後走每日 parquet 快取。
        """
        return self.twse.build_ticker_per_history(ticker, start, end)

    # ─────────────────────────────────────
    # 三大法人（TWSE per-day → 過濾 ticker → 累積成時序）
    # ─────────────────────────────────────
    def get_institutional(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """
        回傳 columns=['date', 'name', 'buy', 'sell', 'net_buy']
        對齊 FinMindClient.get_institutional 的格式（chip_factor 用）。

        TWSE 每日法人 CSV 結構：每檔每日一筆，含外資/投信/自營商分項與合計。
        我們把它展平成 long format（每日每法人類型一筆）以對齊 FinMind 行為。
        """
        rows: list[dict] = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                df = self.twse.get_institutional_day(cur)
                if not df.empty:
                    hit = df[df["ticker"] == str(ticker)]
                    if not hit.empty:
                        r = hit.iloc[0]
                        # FinMind 是 long format（每日每 name 一筆），這裡展開
                        for name, col in [
                            ("外陸資", "foreign_net"),
                            ("投信", "inv_trust_net"),
                            ("自營商", "dealer_net"),
                        ]:
                            net = r.get(col, 0)
                            if pd.notna(net):
                                rows.append(
                                    {
                                        "date": cur,
                                        "name": name,
                                        "buy": max(int(net), 0),
                                        "sell": max(-int(net), 0),
                                        "net_buy": int(net),
                                    }
                                )
            cur += timedelta(days=1)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    # ─────────────────────────────────────
    # 月營收（MOPS 替代）
    # ─────────────────────────────────────
    def get_monthly_revenue(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """回傳 columns=['date', 'revenue', 'revenue_yoy', 'revenue_mom']."""
        return self.mops.build_ticker_revenue_history(
            ticker,
            start_year=start.year, start_month=start.month,
            end_year=end.year, end_month=end.month,
        )

    # ─────────────────────────────────────
    # 仍依賴 FinMind（無替代品）
    # ─────────────────────────────────────
    def get_broker_distribution(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """分點籌碼 — 唯一沒法自抓的，要保留 FinMind Sponsor 才能用。"""
        if self.finmind is None:
            return pd.DataFrame()
        return self.finmind.get_broker_distribution(ticker, start, end)

    def get_margin(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """融資融券 — TWSE 也有但目前還沒寫自抓器，先保留 FinMind。"""
        if self.finmind is None:
            return pd.DataFrame()
        return self.finmind.get_margin(ticker, start, end)

    def get_foreign_ownership(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """週集保股權分散表 — FinMind 獨家整理，TWSE 原始格式較難解析。"""
        if self.finmind is None:
            return pd.DataFrame()
        return self.finmind.get_foreign_ownership(ticker, start, end)

    # ─────────────────────────────────────
    # 不再需要的 FinMind 函式（保留 stub 兼容性）
    # ─────────────────────────────────────
    def get_financial_statements(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """目前 quality_momentum 用，後續可加 MOPS 財報自抓。"""
        if self.finmind is None:
            return pd.DataFrame()
        return self.finmind.get_financial_statements(ticker, start, end)
