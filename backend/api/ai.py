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
    if not GEMINI_KEY:
        raise HTTPException(status_code=503, detail="AI service not configured")
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel("gemini-flash-latest")
        prompt = build_prompt(payload)
        resp = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 600,
            },
        )
        return ExplainOut(text=resp.text or "(AI 沒回應)", model="gemini-2.0-flash")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 失敗: {e}")
