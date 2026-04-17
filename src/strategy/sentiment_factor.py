"""
LLM 新聞情緒評分 — 使用 Claude Haiku 4.5 + prompt caching。

Haiku 4.5 適合此類短 classification 任務：
- 輸出固定格式（SCORE + REASON）
- 成本極低（每日約 30 檔 × ~500 tokens）
- 延遲低，不拖累晨報產出

Prompt caching 策略：
- SYSTEM_PROMPT 設為 ephemeral cache
- 目前約 500 tokens（低於 Haiku 快取門檻 1024）
- 設計上預留擴充空間（例如加入 few-shot 範例時自動啟用 cache）

容錯設計：
- API 失敗 → score=0, reason="API 失敗"（晨報不中斷）
- 解析失敗 → score=0, reason="解析失敗"
- 無新聞 → score=0, reason="無相關新聞"
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.news_collector import NewsItem

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是專業的台股短期分析師，負責評估過去 24 小時的新聞對個股未來 3–5 個交易日股價的影響。

評分規則（輸出 -1.0 至 +1.0 的浮點數）：

+0.8 ~ +1.0：強利多，顯著推升預期報酬
  - 重大訂單確認（如 NVIDIA 下大量訂單給供應鏈）
  - 營收/獲利大幅超過預期（月增 > 30%）
  - 法人大幅調升目標價（+20% 以上）
  - 獨家受惠的重大政策或題材（CoWoS 擴產、HBM 放量）

+0.3 ~ +0.7：溫和利多
  - 法人評等上調但未大幅調價
  - 單月營收優於同業
  - 產能稼動率回升、訂單能見度拉長

-0.2 ~ +0.2：中性
  - 例行法說內容、技術合作公告、非關鍵題材
  - 資訊不足以判斷

-0.3 ~ -0.7：溫和利空
  - 營收月減、產品降價壓力、庫存升高
  - 法人調降目標價

-0.8 ~ -1.0：強利空
  - 重大客戶砍單、主要產品線終止
  - 財報重大缺失、司法/監管調查
  - 長期技術領先地位失守

特別注意（台股特性）：
- 除權息前後的新聞要區分「事件驅動」與「常態除息」
- 投信法說轉多通常落後股價一週，屬於「追認利多」而非「預示」，略微折扣
- 「調升目標價」若僅是個位數 %，算中性
- 單一分析師報告 ≠ 整個研究圈共識，語氣要保守

輸出格式（嚴格遵守，否則解析失敗）：
SCORE: <-1.0 至 1.0 的浮點數，保留一位小數>
REASON: <一句話說明最關鍵的因素，20 字以內>"""


@dataclass(frozen=True)
class SentimentResult:
    ticker: str
    score: float          # -1.0 ~ +1.0，clamped
    reason: str
    n_news: int


class SentimentAnalyzer:
    def __init__(
        self,
        client,
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 150,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def score(
        self,
        ticker: str,
        company_name: str,
        news: list["NewsItem"],
    ) -> SentimentResult:
        if not news:
            return SentimentResult(ticker=ticker, score=0.0, reason="無相關新聞", n_news=0)

        user_content = self._format_news(ticker, company_name, news)

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
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as exc:
            logger.warning("Claude API 失敗 (%s): %s", ticker, exc)
            return SentimentResult(ticker=ticker, score=0.0, reason="API 失敗", n_news=len(news))

        text = self._extract_text(resp)
        score, reason = self._parse(text)
        return SentimentResult(ticker=ticker, score=score, reason=reason, n_news=len(news))

    # ─────────────────────────────────────
    # 格式化 user message
    # ─────────────────────────────────────
    @staticmethod
    def _format_news(ticker: str, company_name: str, news: list["NewsItem"]) -> str:
        lines = [f"標的：{ticker} {company_name}", f"新聞數：{len(news)}", "", "新聞列表："]
        for i, item in enumerate(news, 1):
            lines.append(f"{i}. {item.title}")
            if item.summary:
                snippet = item.summary.replace("\n", " ").strip()[:100]
                lines.append(f"   摘要：{snippet}")
        return "\n".join(lines)

    # ─────────────────────────────────────
    # 解析 Claude 回應
    # ─────────────────────────────────────
    @staticmethod
    def _extract_text(response) -> str:
        # Anthropic SDK: response.content is list of ContentBlock, each has .text
        blocks = getattr(response, "content", None) or []
        for block in blocks:
            text = getattr(block, "text", None)
            if text:
                return text
        return ""

    @staticmethod
    def _parse(text: str) -> tuple[float, str]:
        if not text:
            return 0.0, "解析失敗"

        score_match = re.search(r"SCORE:\s*([-+]?\d*\.?\d+)", text)
        reason_match = re.search(r"REASON:\s*(.+?)(?:\n|$)", text, re.DOTALL)

        if not score_match:
            return 0.0, "解析失敗"

        try:
            score = float(score_match.group(1))
        except ValueError:
            return 0.0, "解析失敗"

        score = max(-1.0, min(1.0, score))
        reason = reason_match.group(1).strip() if reason_match else "無解釋"
        return score, reason
