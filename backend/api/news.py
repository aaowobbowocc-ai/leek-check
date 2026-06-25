"""新聞 — Google News RSS(合法 + 免費)."""
from __future__ import annotations
from urllib.parse import quote
from fastapi import APIRouter, Query
from pydantic import BaseModel
from functools import lru_cache
from time import time

router = APIRouter(tags=["news"])


class NewsItem(BaseModel):
    title: str
    source: str
    link: str
    published: str


class NewsCategory(BaseModel):
    key: str
    label: str
    items: list[NewsItem]


# 30 min 簡易 cache(key=query)
_CACHE: dict[str, tuple[float, list[NewsItem]]] = {}
TTL = 1800


def _fetch_rss(query: str, max_n: int = 8) -> list[NewsItem]:
    cache_key = f"{query}_{max_n}"
    if cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if time() - ts < TTL:
            return data

    try:
        import feedparser
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        feed = feedparser.parse(url)
        items: list[NewsItem] = []
        for e in feed.entries[:max_n]:
            title = e.get("title", "")
            source = ""
            if " - " in title:
                source = title.split(" - ")[-1]
                title = title.rsplit(" - ", 1)[0]
            items.append(NewsItem(
                title=title,
                source=source,
                link=e.get("link", ""),
                published=e.get("published", ""),
            ))
        _CACHE[cache_key] = (time(), items)
        return items
    except Exception as e:
        print(f"[news] {query} failed: {e}")
        return []


WORLD_CATEGORIES = [
    {"key": "us_fed", "label": "🇺🇸 美股 / 聯準會", "query": "聯準會 Fed 利率"},
    {"key": "us_china", "label": "🇨🇳 中美關係", "query": "中美關係 川普 關稅"},
    {"key": "geo", "label": "⚡ 地緣 / 大事", "query": "伊朗 戰爭 油價"},
]


@router.get("/news/world", response_model=list[NewsCategory])
def world_news():
    return [
        NewsCategory(
            key=c["key"],
            label=c["label"],
            items=_fetch_rss(c["query"], max_n=3),
        )
        for c in WORLD_CATEGORIES
    ]


@router.get("/news/market", response_model=list[NewsItem])
def market_news(limit: int = Query(10, ge=1, le=20)):
    return _fetch_rss("台股 大盤 加權", max_n=limit)


@router.get("/news/ticker/{ticker}", response_model=list[NewsItem])
def ticker_news(ticker: str, name: str = Query("")):
    query = f"{ticker} {name}".strip()
    return _fetch_rss(query, max_n=10)
