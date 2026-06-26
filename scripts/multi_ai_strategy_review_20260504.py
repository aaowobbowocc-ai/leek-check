"""
Multi-AI Strategic Review — TW STRONG_BULL diversification dilemma

當前情境（2026-05-04）:
  - TAIEX dist MA200 +40.5% (V2 classifier = STRONG_BULL)
  - User portfolio NT$594K: 79% cash, 3% 0050, 18% other ETFs/stocks
  - 9-year backtest 顯示 STRONG_BULL fwd 20d 跨期不穩定 (+0.31% vs -2.13%)
  - Foreign TX OI z = -1.10, VIX = 17 (hedge signals 未觸發)

User 質疑:
  1. 為什麼把所有非 0050 ETF 都歸為「satellite 該減持」？
  2. TW 水位這麼高，該往國際分散嗎？
  3. 是否考慮 stock futures / forex / crypto?

請以最 SKEPTICAL 角度找盲點，給 contrarian view。
"""
from __future__ import annotations

import json
import os
import sys
import io
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env")

import requests


PROMPT = """你是頂尖的 macro 投資 reviewer。一個 INVEST 系統正在做策略決策，
我需要你從最 SKEPTICAL 角度找盲點。

## 用戶背景
- 醫學生（時間有限，08:30-09:00 看晨報）
- 台股投資者，有 NT$594K portfolio
- 已經跨 8 個月跑了 60+ 個 backtest，TW long-only stock-picking 全部輸 0050
- 已驗證的 alpha 來源（all post-bias-fix）:
  * Revenue YoY 60d portfolio (max=20 yoy_asc): 1H +9.8pp vs 0050, 2H -20pp 輸 (TSMC AI boom)
  * EWY 韓國: 11 年 +13.37% CAGR, 1Y +187.5% (memory 標為「漏網最大 alpha」)
  * DXJ 日本 hedged: 16 年 +12.33% CAGR, alpha vs 0050 +6.40pp/yr
  * Foreign TX OI z<-2: 10d TAIEX alpha +1.43% (hedge signal)
  * Pair trading DRAM: +3.16%/筆 但需信用帳戶
- 已封閉路線（驗證失敗）:
  * 期貨 prop firm swing (overnight rule kills strategy)
  * Day trade ORB (-0.76%/筆 net)
  * Value investing (TSMC 結構壟斷)
  * 廣度因子 portfolio (capacity constraint)

## 當前情境（2026-05-04）
- TAIEX 距 MA200: **+40.5%** (V2 5-regime 分類為 STRONG_BULL)
- 30d 年化波動: 31.1%（post-2025-04-07 Trump crash recovery 高 vol）
- 60d return: +26.9%（V 型反彈中）
- 0050 9 年歷史: STRONG_BULL fwd 20d **跨期不穩定**
  - Period B 2020-2022: +0.31% (COVID liquidity boom 延續)
  - Period C 2023-2025: -2.13% (AI peak mean reversion)
- VIX: 17（正常）
- Foreign TX OI z: -1.10（接近警戒但未到 -2.0）

## 用戶實際持倉
- 0050 (200 股): 核心 TW
- 00646 (250 股): S&P 500 ETF (美股 exposure)
- 00947 (100 股): TW 半導體 ETF (與 0050 TSMC 重疊)
- 009819 (1000 股): 不確定（疑似抓錯代號）
- 2345 (25 股): 智邦 (TW 個股)
- 2408 (100 股): 南亞科 (DRAM)
- Cash: NT$466K (79%)

## 我給用戶的初步建議（你要挑戰）

我建議的 STRONG_BULL barbell:
  - 30% 0050 (從 3% 加碼，但慢慢分批)
  - 10-15% EWY 韓國 (新加碼，國際分散 alpha)
  - 5-10% DXJ 日本 (用 SPY 跌訊號 timing 加)
  - 5% EWZ 巴西 (多元化)
  - 25-30% 現金 (CRASH 子彈)
  - 不碰 00631L 正2 (STRONG_BULL fwd 期望值負)
  - 不碰 期貨/外匯/加密貨幣

## 你的任務 — 找 5-8 個我可能的盲點

特別挑戰:
1. **「STRONG_BULL = 不買」是否過度保守？** 2H 2023-25 數據是 backtest，現在已經是 1.6 年後。AI boom 還在繼續嗎？大多人還是 underweight TW，FOMO 可能會延續多年。我把用戶卡在 79% 現金等 CRASH，會不會 opportunity cost 太大？

2. **EWY 韓國 +187.5% 1Y 是否該追？** 這數字可能是基期低（2024 戒嚴/政治危機）。追歷史新高的台/美/韓電子股，會不會買在山頂？

3. **DXJ 是 currency hedge 美元計價** — 對台幣 user 來說等於 USD 部位。當前美元高位時加碼 DXJ 是不是雙重風險？

4. **00946（半導體）vs 0050 重疊**  — 但 00947 集中度更高，AI boom 期可能 outperform。建議減持是否錯？

5. **「不碰外匯」是否教條？** 比如 EUR/USD carry trade，或低槓桿 USD/TWD 對沖（user 大量持有 USD 資產 via 00646/EWY/DXJ）。

6. **「不碰加密」** — BTC 已成為機構資產類別，11 年實證有 risk premium。完全不碰是否類似 2015 年完全不碰美股？

7. **Sequence-of-returns risk** — user 是醫學生，現金 79%。在 STRONG_BULL 等 CRASH，如果 CRASH 兩年沒來（如 2017-19 持續 bull），是極大 opportunity cost。

8. **量化 backtest 能 generalize 嗎？** 我給的所有「實證」都是過去 9-16 年。AI boom 是 2023+ 的新 regime，過去資料的有效性？

請給最 BRUTAL 且 contrarian 的反向意見。每個 finding 標 severity (CRITICAL / HIGH / MEDIUM)。

請直接寫 markdown 5-8 個 findings。"""


def call_gemini_vertex(prompt: str) -> str:
    """Call Gemini via Vertex AI (uses GCP free trial credit)."""
    GCP_PROJECT = os.environ.get("GCP_PROJECT", "gen-lang-client-0502672630")
    GCP_REGION = os.environ.get("GCP_REGION", "us-central1")
    MODEL = "gemini-2.5-pro"
    endpoint = (f"https://{GCP_REGION}-aiplatform.googleapis.com/v1/projects/"
                f"{GCP_PROJECT}/locations/{GCP_REGION}/publishers/google/models/"
                f"{MODEL}:generateContent")
    try:
        from google.auth import default
        from google.auth.transport.requests import Request as AuthRequest
        creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(AuthRequest())
        token = creds.token
    except Exception as e:
        return f"ERROR: GCP auth 失敗 — {e}"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,  # higher for contrarian creativity
            "maxOutputTokens": 8192,
        },
    }
    r = requests.post(endpoint, headers=headers, json=body, timeout=180)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:1000]}"
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}\nRaw: {json.dumps(r.json(), indent=2)[:800]}"


def call_claude_secondary(prompt: str) -> str:
    """Call Claude as second perspective (different from main agent)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "ERROR: ANTHROPIC_API_KEY not set"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 8192,
        "system": "You are a skeptical senior investment strategist. Your job is to find blind spots and contrarian views. Be brutal and specific.",
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=180)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:1000]}"
    try:
        return r.json()["content"][0]["text"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}\nRaw: {r.text[:800]}"


def main():
    print("=" * 80)
    print("  Multi-AI Strategic Review — STRONG_BULL Diversification Dilemma")
    print("=" * 80)
    print(f"\n  Prompt size: {len(PROMPT):,} chars")

    docs_dir = ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)

    print("\n[1/2] 呼叫 Gemini 2.5 Pro via Vertex AI（contrarian view）...")
    gemini_resp = call_gemini_vertex(PROMPT)
    print(f"  返回 {len(gemini_resp):,} chars")
    (docs_dir / "multi_ai_review_gemini_20260504.md").write_text(
        f"# Gemini 2.5 Pro Strategic Review\n\n{gemini_resp}",
        encoding="utf-8",
    )

    print("\n[2/2] 呼叫 Claude Sonnet 4.6（second-perspective Claude）...")
    claude_resp = call_claude_secondary(PROMPT)
    print(f"  返回 {len(claude_resp):,} chars")
    (docs_dir / "multi_ai_review_claude_20260504.md").write_text(
        f"# Claude Sonnet 4.6 Strategic Review\n\n{claude_resp}",
        encoding="utf-8",
    )

    print("\n" + "=" * 80)
    print("  📋 Gemini 回覆（節錄）")
    print("=" * 80)
    print(gemini_resp[:4000])
    if len(gemini_resp) > 4000:
        print(f"\n... ({len(gemini_resp) - 4000} chars 截斷，完整見 docs/multi_ai_review_gemini_*.md)")

    print("\n" + "=" * 80)
    print("  📋 Claude Sonnet 4.6 回覆（節錄）")
    print("=" * 80)
    print(claude_resp[:4000])
    if len(claude_resp) > 4000:
        print(f"\n... ({len(claude_resp) - 4000} chars 截斷，完整見 docs/multi_ai_review_claude_*.md)")


if __name__ == "__main__":
    main()
