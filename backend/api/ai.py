"""AI 智能解讀 endpoint — Gemini Flash 寫白話健檢報告.

【Daily Cache 策略】
- 國際情勢 / 新聞情緒 每天 7:30 AM 自動生成(或第一個使用者觸發)
- 24h 內後續呼叫都讀 cache,不花 API 額度
- 個股健檢仍即時生成(每檔不同,難 cache)
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["ai"])

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "ai_cache" / "daily"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TPE = ZoneInfo("Asia/Taipei")


def _slot_str() -> str:
    """3 時段切點(台灣時間):
    07:30 morning  — 美股收盤後 + 台股開盤前(看夜盤動向)
    14:00 noon     — 台股收盤後(消化當日盤)
    20:30 evening  — 美股開盤後(夜盤實況)
    格式:YYYY-MM-DD_slot
    """
    now = datetime.now(TPE)
    h, m = now.hour, now.minute
    if h < 7 or (h == 7 and m < 30):
        # 07:30 前,讀昨晚 evening
        import datetime as _dt
        yest = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        return f"{yest}_evening"
    if h < 14:
        return f"{now.strftime('%Y-%m-%d')}_morning"
    if h < 20 or (h == 20 and m < 30):
        return f"{now.strftime('%Y-%m-%d')}_noon"
    return f"{now.strftime('%Y-%m-%d')}_evening"


def _load_daily_cache(kind: str) -> dict | None:
    fp = CACHE_DIR / f"{kind}_{_slot_str()}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_daily_cache(kind: str, data: dict):
    fp = CACHE_DIR / f"{kind}_{_slot_str()}.json"
    fp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

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

格式(每段請給足夠深度,不要太簡略):

1. 🩺 **技術面健檢**(4-6 句)
   - 解讀 RSI / MA / 量價關係的具體狀態
   - 點出短期支撐 / 壓力區或關鍵均線
   - 動能是否健康(上升 / 盤整 / 走弱)
   - 若有警訊請明說(如「跌破月線」「量縮反彈」)

2. 🩺 **籌碼面健檢**(4-6 句)
   - 20 日法人累計:外資 / 投信 / 自營佈局
   - 三方分歧 vs 一致解讀
   - 散戶比例 / 大戶持股(若有)解讀
   - 籌碼面短期是否穩定

3. 🩺 **基本面健檢**(4-6 句)
   - PER / PBR 估值高 / 低估
   - 月營收 YoY 趨勢
   - 產業景氣位階(若已知)
   - 長期體質判讀

4. 🚨 **綜合判斷 + 韭菜病風險警示**(5-7 句)
   - 整體 verdict 解釋(健 / 中 / 弱)
   - 列 2-3 個具體韭菜病風險:
     * 例:「追在均線高位」「籌碼鬆動但散戶仍積極」「估值偏高 + 動能轉弱」
   - 中期可能走勢方向(不報明牌,只說結構性傾向)
   - 建議觀察的關鍵指標(如「月營收破 0% 即訊號」)

規則:
- 不報明牌、不給買賣建議、純客觀判讀
- 直接從第 1 點開始,不要開場白、不要結尾贅述
- 不要說「以上純客觀」「不構成投資建議」
- 用 markdown 強調重點(粗體 / icon)
- 數字要具體(例:「RSI 67 屬偏熱」「PER 18.5 在歷史中位」)
- 寧可長一點也別簡略
"""


@router.post("/ai/explain", response_model=ExplainOut)
def ai_explain(payload: ExplainIn):
    return _gemini_run(build_prompt(payload), max_tokens=2000)


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


class CachedExplainOut(ExplainOut):
    cached: bool = False
    cached_at: str | None = None  # ISO timestamp


@router.post("/ai/market-insight", response_model=CachedExplainOut)
def market_insight(p: MarketInsightIn):
    # 默認語氣才走 cache(用戶選 pro/casual/short/long 才即時生成)
    if p.style == "neutral" and p.timeframe == "mid":
        cached = _load_daily_cache("market_insight")
        if cached:
            return CachedExplainOut(**cached, cached=True)
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

格式(每段請給足夠深度,不要太簡略):

1. 🌍 **國際情勢判讀**
   - 美股三大指數(SP500 / Nasdaq / SOX)的整體方向解讀,4-5 句
   - DXY 美元指數變動 → 對熱錢流向意涵
   - VIX 恐慌水位判斷(<15 樂觀 / 15-20 中性 / 20-25 緊張 / >25 恐慌)
   - 商品 / 加密(黃金 / 原油 / BTC)動向若有明顯訊號請點出

2. 📊 **法人佈局解讀**
   - 三大法人 20 日累計分歧 / 一致 解讀,3-4 句
   - 外資 連買 / 連賣 的力道大小判讀
   - 投信跟自營的「同向 vs 反向」隱含意義

3. 🇹🇼 **對台股影響**(中性,不報明牌)
   - 加權溫度(過熱 / 偏熱 / 正常 / 偏冷)+ MA200 距 → 中期位階解讀,4-5 句
   - 國際連動度(若美股強,台股早盤大機率跟漲反之亦然)
   - 法人態度 × 大盤位階 → 走勢可能傾向(震盪 / 整理 / 突破 / 修正)

4. ⚠️ **風險警示**(2-3 個具體警示)
   - 列具體訊號(如「VIX 短期內若衝破 25 需提高警覺」「外資連續賣超超過 X 億需擔心」)
   - 每個警示說明可能後續影響

規則:
- 純客觀數據判讀,不給買賣建議
- 直接從第 1 點開始,不要開場白
- markdown 粗體強調關鍵字
- 數字要具體(例:「距 MA200 +41.9% 屬偏熱」「VIX 18.2 屬中性」)
- 寧可長一點也別簡略
"""
    result = _gemini_run(prompt, max_tokens=1800)
    # 默認 neutral/mid 才存 cache
    if p.style == "neutral" and p.timeframe == "mid":
        now_iso = datetime.now(TPE).isoformat()
        _save_daily_cache("market_insight", {
            "text": result.text, "model": result.model, "cached_at": now_iso,
        })
    return CachedExplainOut(text=result.text, model=result.model, cached=False)


# ────── 智能新聞情緒 ──────
class NewsSentimentIn(BaseModel):
    news_titles: list[str]      # 10 條左右
    style: str = "neutral"
    timeframe: str = "mid"


@router.post("/ai/news-sentiment", response_model=CachedExplainOut)
def news_sentiment(p: NewsSentimentIn):
    if p.style == "neutral" and p.timeframe == "mid":
        cached = _load_daily_cache("news_sentiment")
        if cached:
            return CachedExplainOut(**cached, cached=True)
    style = PROMPT_STYLES.get(p.style, PROMPT_STYLES["neutral"])
    tf = TIMEFRAMES.get(p.timeframe, TIMEFRAMES["mid"])
    titles = "\n".join(f"  • {t}" for t in p.news_titles[:15])
    prompt = f"""你是「韭菜健檢」的新聞分析助理 — 解讀今日台股新聞情緒。

【今日台股 / 大盤新聞】
{titles}

請{style},{tf}

格式(每段請給足夠深度,不要太簡略):

1. 📊 **整體新聞情緒**
   - 判讀:正面 / 中性 / 負面
   - 3-5 句具體理由(指出哪幾條新聞的方向)
   - 提到外資 / 投信 / 散戶 / 籌碼動向的新聞請點名

2. 🔥 **熱點主題**(列 4-6 個)
   每個主題:
   - 主題名稱 + 涉及的族群 / 個股
   - 1-2 句解釋為何成為熱點
   - 簡短預估這波熱度可能持續多久

3. 🚨 **風險訊號**
   - 列具體警示(2-4 個),包含:
     * 量縮 / 量爆 / 跌破均線 / 法人狂賣 等技術或籌碼警訊
     * 國際 / 政策 / 黑天鵝 等系統性風險
   - 每個警示說明可能後續影響

4. 🎯 **對盤勢影響**
   - 短期(1 週):3-5 句具體判讀
   - 中期(1-3 月):2-3 句方向預測
   - 建議觀察的關鍵指標 / 數據(2-3 個)

規則:
- 純客觀,不給「該不該買 / 賣」明牌
- 直接從第 1 點開始,不要開場白
- markdown 粗體強調關鍵字
- 數字 / 比例請具體(例:「跌破月線 3%」)
- 寧可長一點也別簡略
"""
    result = _gemini_run(prompt, max_tokens=1800)
    if p.style == "neutral" and p.timeframe == "mid":
        now_iso = datetime.now(TPE).isoformat()
        _save_daily_cache("news_sentiment", {
            "text": result.text, "model": result.model, "cached_at": now_iso,
        })
    return CachedExplainOut(text=result.text, model=result.model, cached=False)


# ────── 手動強制重生(admin)──────
@router.delete("/ai/cache/{kind}")
def clear_cache(kind: str):
    """admin: 清掉本 slot cache 強制下次重生."""
    fp = CACHE_DIR / f"{kind}_{_slot_str()}.json"
    if fp.exists():
        fp.unlink()
        return {"cleared": kind, "slot": _slot_str()}
    return {"already_empty": kind, "slot": _slot_str()}


@router.post("/ai/cache/check-now")
def check_now():
    """手動立刻跑 news watcher(等同每 30 分鐘的自動檢查)."""
    from backend.jobs.news_watcher import check_and_maybe_regen
    triggered = check_and_maybe_regen()
    return {"triggered_regen": triggered, "slot": _slot_str()}


@router.get("/ai/cache/status")
def cache_status():
    """查目前 cache 狀態."""
    out = {"slot": _slot_str(), "items": {}}
    for kind in ["market_insight", "news_sentiment"]:
        fp = CACHE_DIR / f"{kind}_{_slot_str()}.json"
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                out["items"][kind] = {
                    "cached": True,
                    "cached_at": data.get("cached_at"),
                    "text_len": len(data.get("text", "")),
                }
            except Exception:
                out["items"][kind] = {"cached": False}
        else:
            out["items"][kind] = {"cached": False}
    return out


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
