"""
AI Research Helper 單元測試（mock Claude API，不實打）。
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.strategy.ai_research_helper import (
    AIResearchAnalyzer,
    AIResearchReport,
    build_user_prompt,
    gather_ticker_data,
    parse_response,
)


# ─────────────────────────────────────────
# parse_response — JSON 抽取容錯
# ─────────────────────────────────────────
def test_parse_clean_json() -> None:
    text = '{"verdict": "good", "ai_confidence": "high"}'
    parsed = parse_response(text)
    assert parsed["verdict"] == "good"


def test_parse_markdown_wrapped_json() -> None:
    """Claude 偶爾會包 ```json ... ```，要能剝掉。"""
    text = '```json\n{"verdict": "ok"}\n```'
    parsed = parse_response(text)
    assert parsed["verdict"] == "ok"


def test_parse_text_with_preamble() -> None:
    """Claude 偶爾在 JSON 前加敘述文字，要能定位 { 起點。"""
    text = '以下是分析結果：\n{"verdict": "ok"}\n感謝。'
    parsed = parse_response(text)
    assert parsed["verdict"] == "ok"


def test_parse_invalid_returns_empty() -> None:
    assert parse_response("沒有 JSON 結構") == {}
    assert parse_response("") == {}


# ─────────────────────────────────────────
# AIResearchReport markdown 渲染
# ─────────────────────────────────────────
def test_report_markdown_contains_key_sections() -> None:
    r = AIResearchReport(
        ticker="2330", company_name="台積電", as_of=date(2026, 4, 25),
        business_summary="全球最大晶圓代工廠",
        moat={"exists": "yes", "type": "規模 + 製程", "evidence": "市佔 60%+"},
        red_flags=["AI 集中度高"],
        valuation={"is_reasonable": False, "current_pe": 33.0, "historical_avg_pe": 22.0,
                    "comment": "PE 在 5 年高位"},
        growth_drivers=["AI capex 持續"],
        key_risks=["地緣政治", "半導體景氣循環"],
        ai_confidence="high",
        verdict="基本面強，但估值偏高，建議等回檔",
        one_year_outlook="樂觀：突破 2200；中性：盤整；悲觀：跌破 1800",
    )
    md = r.to_markdown()
    assert "AI 體檢報告" in md
    assert "🟢" in md   # high confidence
    assert "✅" in md or "護城河" in md
    assert "🚩" in md
    assert "估值" in md
    assert "1 年展望" in md


def test_report_markdown_handles_error_state() -> None:
    r = AIResearchReport(
        ticker="9999", company_name="未知", as_of=date(2026, 4, 25),
        error="無資料",
    )
    md = r.to_markdown()
    assert "分析失敗" in md
    assert "無資料" in md


# ─────────────────────────────────────────
# AIResearchAnalyzer.analyze（mock client）
# ─────────────────────────────────────────
def _mk_bundle(ticker="2330", company="台積電") -> dict:
    rev = pd.DataFrame(
        [{"date": date(2026, 3, 10), "revenue": 1500_000_000, "revenue_yoy": 25.0}]
    )
    fin = pd.DataFrame(
        [
            {"date": date(2025, 9, 30), "type": "EPS", "value": 12.5},
            {"date": date(2025, 12, 31), "type": "EPS", "value": 14.2},
        ]
    )
    return {
        "ticker": ticker, "company_name": company, "as_of": date(2026, 4, 25),
        "revenue": rev, "financials": fin, "per_pbr": pd.DataFrame(), "news": [],
    }


def _mock_response(json_payload: dict):
    """模擬 anthropic SDK response object。"""
    block = MagicMock()
    block.text = json.dumps(json_payload, ensure_ascii=False)
    resp = MagicMock()
    resp.content = [block]
    return resp


def test_analyze_full_flow() -> None:
    client = MagicMock()
    payload = {
        "business_summary": "晶圓代工龍頭",
        "moat": {"exists": "yes", "type": "規模", "evidence": "市佔高"},
        "red_flags": [],
        "valuation": {"is_reasonable": True, "current_pe": 25, "historical_avg_pe": 22,
                       "comment": "略偏高但 OK"},
        "growth_drivers": ["AI"],
        "key_risks": ["景氣", "地緣"],
        "ai_confidence": "high",
        "verdict": "可長期持有",
        "one_year_outlook": "繼續成長",
    }
    client.messages.create.return_value = _mock_response(payload)

    analyzer = AIResearchAnalyzer(client)
    report = analyzer.analyze(_mk_bundle())

    assert not report.has_error
    assert report.business_summary == "晶圓代工龍頭"
    assert report.moat["exists"] == "yes"
    assert report.ai_confidence == "high"
    assert "可長期持有" in report.verdict


def test_analyze_skips_when_no_data() -> None:
    """財報與營收都空 → 不打 API、直接回 error。"""
    bundle = {
        "ticker": "X", "company_name": "X", "as_of": date(2026, 4, 25),
        "revenue": pd.DataFrame(), "financials": pd.DataFrame(),
        "per_pbr": pd.DataFrame(), "news": [],
    }
    client = MagicMock()
    analyzer = AIResearchAnalyzer(client)
    r = analyzer.analyze(bundle)
    assert r.has_error
    client.messages.create.assert_not_called()


def test_analyze_handles_api_failure() -> None:
    client = MagicMock()
    client.messages.create.side_effect = Exception("API down")
    analyzer = AIResearchAnalyzer(client)
    r = analyzer.analyze(_mk_bundle())
    assert r.has_error
    assert "API down" in r.error


def test_analyze_handles_invalid_json_response() -> None:
    client = MagicMock()
    block = MagicMock()
    block.text = "這不是 JSON"
    resp = MagicMock(); resp.content = [block]
    client.messages.create.return_value = resp

    analyzer = AIResearchAnalyzer(client)
    r = analyzer.analyze(_mk_bundle())
    assert r.has_error
    assert "JSON 解析失敗" in r.error


# ─────────────────────────────────────────
# build_user_prompt — 結構化資料注入
# ─────────────────────────────────────────
def test_build_prompt_includes_ticker_and_data() -> None:
    msg = build_user_prompt(_mk_bundle())
    assert "2330" in msg
    assert "台積電" in msg
    assert "月營收" in msg or "revenue" in msg.lower()
    assert "EPS" in msg


def test_build_prompt_handles_empty_data() -> None:
    bundle = {
        "ticker": "X", "company_name": "X", "as_of": date(2026, 4, 25),
        "revenue": pd.DataFrame(), "financials": pd.DataFrame(),
        "per_pbr": pd.DataFrame(), "news": [],
    }
    msg = build_user_prompt(bundle)
    assert "無資料" in msg or "（無" in msg


# ─────────────────────────────────────────
# gather_ticker_data — 容錯
# ─────────────────────────────────────────
def test_gather_handles_failing_finmind() -> None:
    """個別資料源失敗不應 crash。"""
    finmind = MagicMock()
    finmind.get_financial_statements.side_effect = Exception("API down")
    finmind.get_monthly_revenue.return_value = pd.DataFrame()
    finmind.get_per_pbr.return_value = pd.DataFrame()

    bundle = gather_ticker_data(
        ticker="2330", company_name="台積電",
        finmind=finmind, news_collector=None,
        as_of=date(2026, 4, 25),
    )
    assert bundle["financials"].empty
    assert bundle["news"] == []
