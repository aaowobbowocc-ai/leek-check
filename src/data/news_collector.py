"""
新聞收集器 — Google News RSS + 鉅亨網 RSS。

流程：
1. 依 ticker + 公司名查詢各來源
2. 用 config/news_keywords.yaml 白名單過濾（命中才留下）
3. 未命中的新聞 → 情緒分數 0（不送 LLM，節省 API 成本）

設計假設：
- 晨報只看過去 24h 的新聞（lookback_hours=24）
- 單一 ticker 典型新聞量 < 20 條，全部送 LLM 也便宜；過濾是避免雜訊
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsItem:
    ticker: str
    title: str
    summary: str
    published_at: datetime
    source: str
    url: str


class NewsCollector:
    def __init__(self, keywords_path: Path | str) -> None:
        self._keywords = self._load_keywords(Path(keywords_path))

    def collect(
        self,
        ticker: str,
        company_name: str,
        lookback_hours: int = 24,
    ) -> list[NewsItem]:
        """抓取所有來源，用關鍵字白名單過濾後回傳。"""
        raw: list[NewsItem] = []
        raw.extend(self._fetch_google_news(ticker, company_name, lookback_hours))
        # 未來可加入：鉅亨網、工商時報 RSS
        return [item for item in raw if self._matches_keywords(item)]

    def collect_no_filter(
        self,
        ticker: str,
        company_name: str,
        lookback_hours: int = 24,
    ) -> list[NewsItem]:
        """不套用關鍵字過濾 — 主要供測試或探索用。"""
        return self._fetch_google_news(ticker, company_name, lookback_hours)

    def _matches_keywords(self, item: NewsItem) -> bool:
        text = f"{item.title} {item.summary}"
        return any(kw in text for kw in self._keywords)

    def _fetch_google_news(
        self,
        ticker: str,
        company_name: str,
        lookback_hours: int,
    ) -> list[NewsItem]:
        import feedparser  # type: ignore

        query = f'"{ticker}" OR "{company_name}"'
        url = (
            f"https://news.google.com/rss/search?q={quote_plus(query)}"
            "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        )
        feed = feedparser.parse(url)
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=lookback_hours)

        items: list[NewsItem] = []
        for entry in feed.entries:
            pub = self._parse_date(entry.get("published"))
            if pub is None:
                continue
            if pub < cutoff:
                continue
            items.append(
                NewsItem(
                    ticker=ticker,
                    title=entry.get("title", ""),
                    summary=entry.get("summary", ""),
                    published_at=pub,
                    source="google_news",
                    url=entry.get("link", ""),
                )
            )
        return items

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
        if not date_str:
            return None
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %Z",
            "%a, %d %b %Y %H:%M:%S %z",
            "%Y-%m-%dT%H:%M:%S%z",
        ):
            try:
                dt = datetime.strptime(date_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None

    @staticmethod
    def _load_keywords(path: Path) -> set[str]:
        with path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        keywords: set[str] = set()
        for value in cfg.values():
            if isinstance(value, list):
                keywords.update(str(v) for v in value)
        return keywords
