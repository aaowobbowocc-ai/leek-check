"""
Phase 3 測試 — 新聞收集 + LLM 情緒評分。
所有外部 API（Google News RSS、Anthropic）皆 mock。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.data.news_collector import NewsCollector, NewsItem
from src.strategy.sentiment_factor import (
    SYSTEM_PROMPT,
    SentimentAnalyzer,
    SentimentResult,
)


# ─────────────────────────────────────────
# NewsCollector
# ─────────────────────────────────────────
def _write_keywords(tmp_path: Path) -> Path:
    path = tmp_path / "keywords.yaml"
    path.write_text(
        "financial:\n  - 營收\n  - 獲利\n"
        "capacity:\n  - 產能\n  - 急單\n"
        "themes:\n  - CoWoS\n  - HBM\n",
        encoding="utf-8",
    )
    return path


def _feed_entry(title: str, summary: str = "", hours_ago: int = 1) -> dict:
    pub = (datetime.now(tz=timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%a, %d %b %Y %H:%M:%S %z"
    )
    return {
        "title": title,
        "summary": summary,
        "published": pub,
        "link": "https://example.com/news",
    }


def _mock_feed(entries: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(entries=entries)


def test_collector_loads_keywords(tmp_path: Path) -> None:
    kw_path = _write_keywords(tmp_path)
    collector = NewsCollector(kw_path)
    assert "營收" in collector._keywords
    assert "CoWoS" in collector._keywords
    assert len(collector._keywords) == 6


def test_collector_filters_by_keyword(tmp_path: Path) -> None:
    kw_path = _write_keywords(tmp_path)
    collector = NewsCollector(kw_path)

    entries = [
        _feed_entry("3413 京鼎營收創新高"),            # 命中：營收
        _feed_entry("3413 董事會改選"),               # 未命中
        _feed_entry("家登 CoWoS 設備出貨滿載"),         # 命中：CoWoS + 出貨
        _feed_entry("半導體股價下跌"),                 # 未命中
    ]
    with patch("feedparser.parse", return_value=_mock_feed(entries)):
        result = collector.collect("3413", "京鼎")

    titles = [it.title for it in result]
    assert "3413 京鼎營收創新高" in titles
    assert "家登 CoWoS 設備出貨滿載" in titles
    assert "3413 董事會改選" not in titles
    assert "半導體股價下跌" not in titles


def test_collector_respects_lookback(tmp_path: Path) -> None:
    """超出 lookback 的新聞應被過濾。"""
    kw_path = _write_keywords(tmp_path)
    collector = NewsCollector(kw_path)

    entries = [
        _feed_entry("3413 營收公告", hours_ago=2),     # 在範圍內
        _feed_entry("3413 急單湧入", hours_ago=48),    # 超出 24h
    ]
    with patch("feedparser.parse", return_value=_mock_feed(entries)):
        result = collector.collect("3413", "京鼎", lookback_hours=24)

    assert len(result) == 1
    assert result[0].title == "3413 營收公告"


def test_collector_all_items_have_ticker(tmp_path: Path) -> None:
    kw_path = _write_keywords(tmp_path)
    collector = NewsCollector(kw_path)

    entries = [_feed_entry("3413 獲利創高"), _feed_entry("3413 CoWoS 產能滿載")]
    with patch("feedparser.parse", return_value=_mock_feed(entries)):
        result = collector.collect("3413", "京鼎")

    assert all(item.ticker == "3413" for item in result)
    assert all(item.source == "google_news" for item in result)


# ─────────────────────────────────────────
# SentimentAnalyzer
# ─────────────────────────────────────────
def _mock_response(text: str) -> MagicMock:
    block = SimpleNamespace(text=text)
    return SimpleNamespace(content=[block])


def _sample_news(n: int = 2) -> list[NewsItem]:
    now = datetime.now(tz=timezone.utc)
    return [
        NewsItem(
            ticker="3413",
            title=f"新聞 {i}",
            summary=f"摘要 {i}",
            published_at=now,
            source="google_news",
            url=f"https://x.com/{i}",
        )
        for i in range(1, n + 1)
    ]


def test_sentiment_no_news_returns_zero() -> None:
    client = MagicMock()
    analyzer = SentimentAnalyzer(client)
    result = analyzer.score("3413", "京鼎", [])

    assert result.score == 0.0
    assert result.reason == "無相關新聞"
    assert result.n_news == 0
    client.messages.create.assert_not_called()


def test_sentiment_parses_strong_bull() -> None:
    client = MagicMock()
    client.messages.create.return_value = _mock_response(
        "SCORE: 0.8\nREASON: NVIDIA 確認擴大 CoWoS 訂單"
    )
    analyzer = SentimentAnalyzer(client)
    result = analyzer.score("3413", "京鼎", _sample_news())

    assert result.score == 0.8
    assert "NVIDIA" in result.reason
    assert result.n_news == 2


def test_sentiment_clamps_out_of_range() -> None:
    client = MagicMock()
    client.messages.create.return_value = _mock_response("SCORE: 1.5\nREASON: 模型失準")
    analyzer = SentimentAnalyzer(client)
    result = analyzer.score("3413", "京鼎", _sample_news())
    assert result.score == 1.0

    client.messages.create.return_value = _mock_response("SCORE: -2.0\nREASON: 模型失準")
    result = analyzer.score("3413", "京鼎", _sample_news())
    assert result.score == -1.0


def test_sentiment_api_failure_returns_zero() -> None:
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("API down")
    analyzer = SentimentAnalyzer(client)
    result = analyzer.score("3413", "京鼎", _sample_news())

    assert result.score == 0.0
    assert result.reason == "API 失敗"
    assert result.n_news == 2


def test_sentiment_malformed_response_returns_zero() -> None:
    client = MagicMock()
    client.messages.create.return_value = _mock_response("我覺得應該很棒喔")
    analyzer = SentimentAnalyzer(client)
    result = analyzer.score("3413", "京鼎", _sample_news())
    assert result.score == 0.0
    assert result.reason == "解析失敗"


def test_sentiment_uses_prompt_caching() -> None:
    """SYSTEM_PROMPT 必須帶 cache_control ephemeral。"""
    client = MagicMock()
    client.messages.create.return_value = _mock_response("SCORE: 0.0\nREASON: 中性")

    analyzer = SentimentAnalyzer(client)
    analyzer.score("3413", "京鼎", _sample_news())

    call_args = client.messages.create.call_args
    system_blocks = call_args.kwargs["system"]
    assert len(system_blocks) == 1
    assert system_blocks[0]["type"] == "text"
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert system_blocks[0]["text"] == SYSTEM_PROMPT


def test_sentiment_uses_haiku_model() -> None:
    """預設模型必須是 Haiku 4.5（最新且最便宜）。"""
    client = MagicMock()
    client.messages.create.return_value = _mock_response("SCORE: 0.0\nREASON: 中性")

    analyzer = SentimentAnalyzer(client)
    analyzer.score("3413", "京鼎", _sample_news())

    model_used = client.messages.create.call_args.kwargs["model"]
    assert model_used == "claude-haiku-4-5-20251001"


def test_sentiment_result_is_frozen() -> None:
    result = SentimentResult(ticker="3413", score=0.5, reason="利多", n_news=3)
    with pytest.raises(Exception):
        result.score = 0.8  # type: ignore[misc]
