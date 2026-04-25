"""
AI Research Helper（Phase 17b）— 個股深度體檢輔助器。

把「30 小時讀年報」濃縮到「3 分鐘看 AI 摘要」的工具。

流程：
  1. 對給定 ticker 收集所有可用資料（財報 / 月營收 / PER / 新聞）
  2. 組裝成結構化 prompt，餵給 Claude Sonnet
  3. 強制 JSON 輸出（business_summary / moat / red_flags / valuation /
     growth_drivers / risks / ai_confidence / verdict）
  4. 解析後回傳 AIResearchReport，可序列化成 Markdown 報告

設計重點：
  - **不是自動下單系統**，是研究助手 — 把人腦從 90% 機械工作解放
  - 「最後 1% 責任」永遠在你手上：是否相信 AI 的判斷
  - 使用 Sonnet（不是 Haiku）— 這類複雜推理 Sonnet 品質明顯較高
  - JSON 強制格式 → 解析穩定（vs sentiment_factor 的 SCORE/REASON 純文字）
  - Prompt caching：system 部分 cache，動態資料只進 user message
  - 容錯：API 失敗 / JSON 解析失敗 → 回傳 partial report 而非 crash

成本估算：
  - 單檔輸入 ~5000 tokens，輸出 ~1500 tokens
  - Sonnet 4.6: ~$0.03/檔
  - 30 檔/月：~$0.90/月，極划算
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是專業的台股價值投資分析師（Buffett / Munger 風格）。
基於使用者提供的財務數據、月營收、本益比歷史、新聞，產出一份「個股體檢報告」。

報告原則：
1. 用「事業擁有者」視角：這家公司在做什麼？護城河在哪？
2. 識別會計紅旗：應收帳款異常增加、存貨堆積、現金流與獲利不對稱
3. 估值合理性：當前 PE vs 5 年平均 vs 同業、是不是貴
4. 風險清單：從財報看出的具體風險（不是泛泛而論）
5. 機會清單：成長動能、新題材、競爭優勢擴張

關鍵約束：
- 只用使用者提供的資料推論。不要憑訓練資料記憶補洞（資料可能過時）
- 所有判斷需有具體佐證（從輸入資料引用數字 / 事實）
- 不確定時直接寫「資料不足」，禁止猜測
- AI 信心度（ai_confidence）對應：
  - high = 有 8 季+完整財報 + 30 天新聞 + 5 年 PER
  - medium = 部分資料缺失但仍可判斷大方向
  - low = 資料嚴重不足、僅能給概略印象

**輸出格式必須是純 JSON（不包 markdown code block）**：
{
  "business_summary": "1-2 句說明公司主要業務與獲利模式",
  "moat": {
    "exists": "yes" | "no" | "uncertain",
    "type": "品牌/網路效應/規模/專利/客戶轉換成本/無 等",
    "evidence": "從輸入資料找到的證據"
  },
  "red_flags": ["紅旗 1（含具體數字）", "紅旗 2", ...],
  "valuation": {
    "is_reasonable": true | false,
    "current_pe": 數字 or null,
    "historical_avg_pe": 數字 or null,
    "comment": "估值合不合理的判斷"
  },
  "growth_drivers": ["成長動能 1", "成長動能 2", ...],
  "key_risks": ["風險 1", "風險 2", "風險 3"],
  "ai_confidence": "high" | "medium" | "low",
  "verdict": "整體 1 句話結論：值不值得進一步研究",
  "one_year_outlook": "未來 1 年該公司可能的發展方向（樂觀/中性/悲觀劇本各 1 句）"
}"""


@dataclass(frozen=True)
class AIResearchReport:
    """AI 生成的個股體檢報告。"""
    ticker: str
    company_name: str
    as_of: date
    business_summary: str = ""
    moat: dict[str, Any] = field(default_factory=dict)
    red_flags: list[str] = field(default_factory=list)
    valuation: dict[str, Any] = field(default_factory=dict)
    growth_drivers: list[str] = field(default_factory=list)
    key_risks: list[str] = field(default_factory=list)
    ai_confidence: str = "low"
    verdict: str = ""
    one_year_outlook: str = ""
    raw_response: str = ""
    error: str = ""

    @property
    def has_error(self) -> bool:
        return bool(self.error)

    def to_markdown(self) -> str:
        """渲染成晨報可附加的 Markdown 段落。"""
        if self.has_error:
            return f"## 🤖 {self.ticker} {self.company_name} — 分析失敗\n{self.error}\n"
        confidence_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(
            self.ai_confidence, "⚪"
        )
        moat_status = self.moat.get("exists", "uncertain")
        moat_icon = {"yes": "✅", "no": "❌", "uncertain": "❔"}.get(moat_status, "❔")

        lines = [
            f"## 🤖 {self.ticker} {self.company_name} — AI 體檢報告",
            f"**信心度**：{confidence_icon} {self.ai_confidence.upper()}",
            f"**整體判斷**：{self.verdict}",
            "",
            f"### 業務摘要\n{self.business_summary}",
            "",
            f"### 護城河 {moat_icon}",
            f"- 類型：{self.moat.get('type', '—')}",
            f"- 證據：{self.moat.get('evidence', '—')}",
        ]

        if self.red_flags:
            lines.append("\n### 🚩 紅旗")
            for f in self.red_flags:
                lines.append(f"- {f}")

        v = self.valuation
        if v:
            ok_icon = "✅ 合理" if v.get("is_reasonable") else "⚠️ 偏貴/偏便宜"
            lines.append("\n### 估值")
            lines.append(f"- 判斷：{ok_icon}")
            cur_pe = v.get("current_pe")
            hist_pe = v.get("historical_avg_pe")
            if cur_pe is not None:
                lines.append(f"- 目前 PE：{cur_pe}")
            if hist_pe is not None:
                lines.append(f"- 5 年平均 PE：{hist_pe}")
            if v.get("comment"):
                lines.append(f"- 說明：{v['comment']}")

        if self.growth_drivers:
            lines.append("\n### 📈 成長動能")
            for g in self.growth_drivers:
                lines.append(f"- {g}")

        if self.key_risks:
            lines.append("\n### ⚠️ 主要風險")
            for r in self.key_risks:
                lines.append(f"- {r}")

        if self.one_year_outlook:
            lines.append(f"\n### 1 年展望\n{self.one_year_outlook}")

        return "\n".join(lines) + "\n"


def gather_ticker_data(
    ticker: str,
    company_name: str,
    finmind,                                 # FinMindClient or HybridClient
    news_collector,                           # NewsCollector or None
    as_of: date,
    history_years: int = 2,
) -> dict[str, pd.DataFrame | str]:
    """
    收集 ticker 的全部研究素材。容錯：個別資料源失敗回空。
    """
    from datetime import timedelta

    start = date(as_of.year - history_years, as_of.month, as_of.day)

    bundle = {"ticker": ticker, "company_name": company_name, "as_of": as_of}

    try:
        bundle["financials"] = finmind.get_financial_statements(ticker, start, as_of)
    except Exception as e:
        logger.warning("financials 失敗 %s: %s", ticker, e)
        bundle["financials"] = pd.DataFrame()

    try:
        bundle["revenue"] = finmind.get_monthly_revenue(ticker, start, as_of)
    except Exception as e:
        logger.warning("revenue 失敗 %s: %s", ticker, e)
        bundle["revenue"] = pd.DataFrame()

    try:
        bundle["per_pbr"] = finmind.get_per_pbr(
            ticker, date(as_of.year - 5, as_of.month, as_of.day), as_of
        )
    except Exception as e:
        logger.warning("per_pbr 失敗 %s: %s", ticker, e)
        bundle["per_pbr"] = pd.DataFrame()

    if news_collector is not None:
        try:
            bundle["news"] = news_collector.collect(ticker, company_name, lookback_hours=720)  # 30 日
        except Exception as e:
            logger.warning("news 失敗 %s: %s", ticker, e)
            bundle["news"] = []
    else:
        bundle["news"] = []

    return bundle


def build_user_prompt(bundle: dict) -> str:
    """把資料 bundle 組成餵給 Claude 的 user message。"""
    ticker = bundle["ticker"]
    name = bundle["company_name"]
    as_of = bundle["as_of"]

    lines = [
        f"# 標的：{ticker} {name}",
        f"# 分析日期：{as_of}",
        "",
        "---",
        "## 月營收（最近 12 個月）",
    ]
    rev = bundle.get("revenue")
    if rev is not None and not rev.empty:
        rev_sorted = rev.sort_values("date").tail(12)
        for _, row in rev_sorted.iterrows():
            r = row.get("revenue", "—")
            yoy = row.get("revenue_yoy", "—")
            try:
                r_str = f"{float(r) / 1_000_000:.0f}M" if pd.notna(r) else "—"
            except Exception:
                r_str = str(r)
            lines.append(f"- {row.get('date', '?')}: 營收 {r_str}, YoY {yoy:+.1f}%" if isinstance(yoy, (int, float)) else f"- {row.get('date', '?')}: 營收 {r_str}, YoY {yoy}")
    else:
        lines.append("（無資料）")

    lines.extend(["", "---", "## 季度財報重點"])
    fin = bundle.get("financials")
    if fin is not None and not fin.empty and "type" in fin.columns:
        # 取最近 8 季的關鍵欄位
        wanted = ["EPS", "ROE", "ROA", "OperatingGrossProfitRate", "DebtAssetRatio"]
        for w in wanted:
            sub = fin[fin["type"] == w].sort_values("date").tail(8)
            if sub.empty:
                continue
            vals = ", ".join(f"{r['date']}: {r['value']:.2f}" for _, r in sub.iterrows())
            lines.append(f"- {w}: {vals}")
    else:
        lines.append("（無季度財報資料）")

    lines.extend(["", "---", "## 本益比 / 殖利率（最近 5 年參考）"])
    per = bundle.get("per_pbr")
    if per is not None and not per.empty:
        per_sorted = per.sort_values("date")
        latest = per_sorted.iloc[-1]
        avg5y = per_sorted.tail(1260).get("per", per_sorted.get("PER", pd.Series()))
        if hasattr(avg5y, "mean"):
            avg = float(avg5y.mean())
        else:
            avg = None
        cur_per = latest.get("per") or latest.get("PER")
        cur_div = latest.get("dividend_yield")
        lines.append(f"- 目前 PER: {cur_per}")
        if avg is not None:
            lines.append(f"- 5 年平均 PER: {avg:.2f}")
        if cur_div is not None:
            lines.append(f"- 殖利率: {cur_div}%")
    else:
        lines.append("（無 PER 資料）")

    lines.extend(["", "---", "## 近 30 日新聞"])
    news = bundle.get("news") or []
    if news:
        for i, n in enumerate(news[:20], 1):
            title = getattr(n, "title", str(n))
            lines.append(f"{i}. {title}")
    else:
        lines.append("（無近期新聞）")

    lines.extend([
        "",
        "---",
        "請依 SYSTEM 指示產出純 JSON 報告。",
    ])
    return "\n".join(lines)


def parse_response(text: str) -> dict:
    """從 Claude 回應抽出 JSON。容忍 markdown code block 包裝。"""
    if not text:
        return {}
    # 移除 markdown code block 包裝
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    # 找第一個 { 到最後一個 }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return {}


class AIResearchAnalyzer:
    """組裝 Claude API 呼叫。"""

    def __init__(
        self,
        client,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 2048,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def analyze(
        self,
        bundle: dict,
    ) -> AIResearchReport:
        ticker = bundle["ticker"]
        name = bundle["company_name"]
        as_of = bundle["as_of"]

        # 至少要有財報或營收，否則直接 low confidence 不打 API
        fin_ok = isinstance(bundle.get("financials"), pd.DataFrame) and not bundle["financials"].empty
        rev_ok = isinstance(bundle.get("revenue"), pd.DataFrame) and not bundle["revenue"].empty
        if not fin_ok and not rev_ok:
            return AIResearchReport(
                ticker=ticker, company_name=name, as_of=as_of,
                error="財報與月營收皆無資料，無法分析",
                ai_confidence="low",
            )

        user_msg = build_user_prompt(bundle)

        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as e:
            logger.warning("AI Research API 失敗 %s: %s", ticker, e)
            return AIResearchReport(
                ticker=ticker, company_name=name, as_of=as_of,
                error=f"API 失敗：{e}",
            )

        # 抽 text
        text = ""
        for block in getattr(resp, "content", []) or []:
            t = getattr(block, "text", None)
            if t:
                text = t
                break

        parsed = parse_response(text)
        if not parsed:
            return AIResearchReport(
                ticker=ticker, company_name=name, as_of=as_of,
                raw_response=text, error="JSON 解析失敗",
            )

        return AIResearchReport(
            ticker=ticker, company_name=name, as_of=as_of,
            business_summary=parsed.get("business_summary", ""),
            moat=parsed.get("moat", {}) or {},
            red_flags=parsed.get("red_flags", []) or [],
            valuation=parsed.get("valuation", {}) or {},
            growth_drivers=parsed.get("growth_drivers", []) or [],
            key_risks=parsed.get("key_risks", []) or [],
            ai_confidence=parsed.get("ai_confidence", "low"),
            verdict=parsed.get("verdict", ""),
            one_year_outlook=parsed.get("one_year_outlook", ""),
            raw_response=text,
        )
