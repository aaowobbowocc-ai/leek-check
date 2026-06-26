"""AI 智能解讀 endpoint — Gemini Flash 寫白話健檢報告."""
from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path

router = APIRouter(tags=["ai"])

# 讀 streamlit secrets.toml 拿 GEMINI key
SECRETS = Path(__file__).resolve().parents[2] / ".streamlit" / "secrets.toml"
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
if not GEMINI_KEY and SECRETS.exists():
    try:
        for line in SECRETS.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GEMINI_API_KEY"):
                GEMINI_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    except Exception:
        pass


class ExplainIn(BaseModel):
    ticker: str
    name: str
    industry: str
    price: float
    change_pct: float
    composite: int
    verdict: str
    tech: dict | None = None
    chip: dict | None = None
    funda: dict | None = None
    style: str = "neutral"  # neutral / pro / casual
    timeframe: str = "mid"  # short / mid / long


class ExplainOut(BaseModel):
    text: str
    model: str


PROMPT_STYLES = {
    "pro":     "用嚴肅專業的金融分析師語氣",
    "neutral": "用中立易懂的白話文",
    "casual":  "用輕鬆口語、有點朋友聊天的感覺",
}
TIMEFRAMES = {
    "short": "重點放在短期(1-4 週):技術指標、單日量價、近 5 日資金動向。長期基本面只提一句。",
    "mid":   "重點放在中期(1-3 個月):月營收 YoY 趨勢、20 日法人佈局、距 60 日均線。技術面與長期基本面各提一句。",
    "long":  "重點放在長期(6-12 個月):基本面 + 體質 + 200 日均線。技術面只提一句。",
}


def build_prompt(p: ExplainIn) -> str:
    def fmt(d):
        if not d:
            return "(無資料)"
        return "\n".join(f"  • {k}: {v}" for k, v in d.items())
    style = PROMPT_STYLES.get(p.style, PROMPT_STYLES["neutral"])
    tf = TIMEFRAMES.get(p.timeframe, TIMEFRAMES["mid"])
    return f"""你是「韭菜健檢」的客觀分析助理 — 純資料展示,不報明牌不喊飆股。

【標的】{p.ticker} {p.name} ({p.industry or '—'})
【目前報價】NT$ {p.price:.2f} ({p.change_pct:+.2f}%)
【健檢分數】{p.composite}/100 ({p.verdict})

【技術面】
{fmt(p.tech)}

【籌碼面 20 日】
{fmt(p.chip)}

【基本面】
{fmt(p.funda)}

請{style},{tf}

格式:
1. 🩺 技術面健檢 (2-3 句)
2. 🩺 籌碼面健檢 (2-3 句)
3. 🩺 基本面健檢 (2-3 句)
4. 🚨 綜合判斷 + 韭菜病風險警示 (3-4 句)

規則:
- 不報明牌、不給買賣建議、純客觀判讀
- 直接從第 1 點開始,不要開場白、不要結尾贅述
- 不要說「以上純客觀」「不構成投資建議」
- 用 markdown 強調重點(粗體 / icon)
"""


@router.post("/ai/explain", response_model=ExplainOut)
def ai_explain(payload: ExplainIn):
    return _gemini_run(build_prompt(payload), max_tokens=1200)


# ────── 智能國際情勢 ──────
class MarketInsightIn(BaseModel):
    taiex_price: float
    taiex_change_pct: float
    taiex_ma200_dist: float | None = None
    taiex_temperature: str = ""
    vix: float | None = None
    intl: dict = {}              # {sp500: {price, change_pct}, nasdaq: ..., ...}
    institutional: dict = {}     # {foreign_20d, invtrust_20d, dealer_20d}
    style: str = "neutral"
    timeframe: str = "mid"


@router.post("/ai/market-insight", response_model=ExplainOut)
def market_insight(p: MarketInsightIn):
    style = PROMPT_STYLES.get(p.style, PROMPT_STYLES["neutral"])
    tf = TIMEFRAMES.get(p.timeframe, TIMEFRAMES["mid"])
    intl_lines = []
    for k, v in p.intl.items():
        if not v:
            continue
        intl_lines.append(f"  • {k}: {v.get('price','?')} ({v.get('change_pct',0):+.2f}%)")
    inst = p.institutional or {}
    ma200_str = f"{p.taiex_ma200_dist:+.1f}%" if p.taiex_ma200_dist is not None else "?"
    vix_str = f"{p.vix:.1f}" if p.vix is not None else "?"
    prompt = f"""你是「韭菜健檢」的客觀分析助理 — 解讀國際市場對台股的影響。

【加權指數】{p.taiex_price:.0f} ({p.taiex_change_pct:+.2f}%) · {p.taiex_temperature} · 距 MA200 {ma200_str}

【VIX 恐慌】{vix_str}

【國際市場】
{chr(10).join(intl_lines) if intl_lines else '(無資料)'}

【三大法人 20 日】
  • 外資: {inst.get('foreign_20d', 0):,} 張
  • 投信: {inst.get('invtrust_20d', 0):,} 張
  • 自營: {inst.get('dealer_20d', 0):,} 張

請{style},{tf}

格式:
1. 🌍 國際情勢判讀(2-3 句)
2. 📊 法人佈局解讀(2 句)
3. 🇹🇼 對台股影響(2-3 句,中性,不報明牌)
4. ⚠️ 風險警示(1-2 句)

規則:
- 純客觀數據判讀,不給買賣建議
- 直接從第 1 點開始
- markdown 粗體強調重點
"""
    return _gemini_run(prompt, max_tokens=1000)


# ────── 智能新聞情緒 ──────
class NewsSentimentIn(BaseModel):
    news_titles: list[str]      # 10 條左右
    style: str = "neutral"
    timeframe: str = "mid"


@router.post("/ai/news-sentiment", response_model=ExplainOut)
def news_sentiment(p: NewsSentimentIn):
    style = PROMPT_STYLES.get(p.style, PROMPT_STYLES["neutral"])
    tf = TIMEFRAMES.get(p.timeframe, TIMEFRAMES["mid"])
    titles = "\n".join(f"  • {t}" for t in p.news_titles[:15])
    prompt = f"""你是「韭菜健檢」的新聞分析助理 — 解讀今日台股新聞情緒。

【今日台股 / 大盤新聞】
{titles}

請{style},{tf}

格式:
1. 📊 整體新聞情緒(正面 / 中性 / 負面 + 1-2 句理由)
2. 🔥 熱點主題(列 2-3 個族群或事件)
3. 🚨 風險訊號(如有警訊提一句,沒有就跳過)
4. 🎯 對盤勢影響(2 句中性判讀)

規則:
- 純客觀,不給買賣建議
- 直接從第 1 點開始
- markdown 粗體強調重點
"""
    return _gemini_run(prompt, max_tokens=900)


def _gemini_run(prompt: str, max_tokens: int = 600):
    if not GEMINI_KEY:
        raise HTTPException(status_code=503, detail="AI service not configured")
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        # 拉高 token 上限避免 thinking 吃光輸出
        resp = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.3,
                # 2.5-flash 預設 thinking 模式,需要給 4x token 才能完整輸出
                "max_output_tokens": max_tokens * 4,
            },
        )
        return ExplainOut(text=resp.text or "(AI 沒回應)", model="gemini-2.5-flash")
    except Exception as e:
        err_str = str(e)
        # 把 429 rate limit 抽出來給更友善訊息
        if "429" in err_str or "quota" in err_str.lower() or "rate" in err_str.lower():
            raise HTTPException(
                status_code=429,
                detail="智能整理今日 free tier 額度已滿,明天再試(或站長升 Gemini Pro 付費版)",
            )
        raise HTTPException(status_code=500, detail=f"智能整理失敗: {err_str[:200]}")
