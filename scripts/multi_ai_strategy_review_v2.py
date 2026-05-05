"""
Multi-AI Strategic Review V2 — 含實證相關性數據

V1 後新增:
- OpenAI GPT-4o (第三 AI 視角)
- Anthropic Claude Sonnet 4.6 (第二 Claude 視角，反向自我批評)
- Gemini 2.5 Pro via Vertex AI

加入實證相關性數據 (用戶 challenge 觸發):
- 0050 ↔ 00646 = 0.62 (週) / 0.74 (近 60d) — AI boom 同步飆升
- 0050 ↔ GLD = 0.21 / 近 60d 0.09 — 真分散
- 0050 ↔ EWY = 0.60 / 0.61 — 假分散，同 AI 半導體
- 0050 ↔ DXJ = 0.36 / 0.28 — 部分分散，最近反而下降
- 0050 ↔ EWZ = 0.35 / 0.40 — 部分分散
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
load_dotenv(ROOT / "config" / ".env", override=True)

import requests


PROMPT = """你是頂尖 macro 投資 reviewer。一個 INVEST 量化系統正在做策略決策，
我需要最 SKEPTICAL 反向意見找投資邏輯 / 統計方法 / 資料偏差盲點。

⚠️ 重要規則:
- 只批評投資 / 量化 / 系統 / 程式碼 / 資料層面
- 不要給 career advice / 時間管理建議 / 「stay in lane」/ 「人力資本 vs portfolio」這類 life coaching
- 用戶是成年人，自己決定時間分配。你只負責投資 critique

## 用戶背景
- NT$594K portfolio
- 跨 8 個月跑了 60+ backtest，TW long-only stock-picking 全部輸 0050
- 已驗證 alpha 來源 (post bias-fix):
  * Revenue YoY 60d portfolio: 1H 2020-22 +9.8pp vs 0050 / 2H 2023-25 -20pp 輸 (TSMC AI boom)
  * EWY 韓: 11y +13.37% CAGR, 1Y +187.5% (memory: 漏網最大 alpha)
  * DXJ 日 hedged: 16y +12.33% CAGR, alpha vs 0050 +6.40pp/yr
  * Foreign TX OI z<-2: 10d TAIEX alpha +1.43%
  * Pair trading DRAM: +3.16%/筆 (需信用)
- 已封閉路線:
  * 期貨 prop firm swing (overnight rule kill)
  * Day trade ORB (-0.76%/筆)
  * Value investing (TSMC 結構壟斷)

## 現況 2026-05-04
- TAIEX 距 MA200 +40.5% (V2 = STRONG_BULL)
- 30d vol 31.1% (post 2025-04-07 Trump crash V 反彈)
- 60d ret +26.9%
- VIX 17, Foreign TX OI z = -1.10 (hedge 未觸發)
- 9y 實證 STRONG_BULL fwd 20d 跨期不穩 (+0.31% vs -2.13%)

## 用戶持倉
- 0050 (200): 核心
- 00646 (250): S&P 500 台版
- 00947 (100): TW 半導體 (跟 0050 重)
- 009819 (1000): 不確定
- 2345 (25), 2408 (100): 個股
- Cash: NT$466K (79%)

## 實證週相關性 2020-2025 (重要！)
0050 ↔ 各資產 (週相關 / 最近 60d):
- 00646 (S&P 台版): 0.62 / **0.74** ⬆️ AI boom 同步飆
- EWY (韓): 0.60 / 0.61
- DXJ (日 hedged): 0.36 / 0.28 ⬇️ 變更分散
- EWZ (巴西): 0.35 / 0.40
- **GLD (黃金): 0.21 / 0.09 ⬇️** 真對沖

## 我給的當前建議

50-55% 0050 + 5% 00646 + 10-15% GLD/IAU + 5% EWZ + 5% DXJ + 5% 個股 + 10% 現金 + 0-2% BTC

## 任務 — 找 5-7 個盲點

特別挑戰:
1. **持有 79% 現金 → 50% 0050 該不該分批 8-12 週？** STRONG_BULL fwd 20d 期望 -0.62%，等於 12 週累積期望 -3.7%。但年輕累積期不該抱現金。怎麼平衡？

2. **GLD 0.09 vs 0050 是 AI boom 期暫時現象嗎？** 過去十年 gold-equity correlation 在不同 macro regime 變動劇烈。會不會我們剛好抓到 anomaly？

3. **00646 corr 0.74** — 真的該降到 5%？S&P 500 內含的不只 TSMC 客戶，還有金融、醫療、消費。降太多會不會 over-react？

4. **EWY +187% 1Y 我已警告不追** — 但 momentum factor 實證有效，可能還能再漲？

5. **DXJ 是 hedged 美元計價** — 對台幣 user 是 USD 多頭。當 USD 已在歷史高位，加 DXJ 是雙重風險嗎？

6. **STRONG_BULL 跨期 +0.31% vs -2.13%** — 樣本太小（n=110, 拆兩期各 ~50）。conclusion 是不是 over-fit？

7. **「不碰加密」** — Gemini 已批評過時。BTC ETF 已機構化。1-2% asymmetric barbell 該重新考慮嗎？

8. **個股 2345 / 2408** — 智邦 (網通) / 南亞科 (DRAM) 是否該保留？網通跟半導體週期不同，DRAM 是獨立週期，可能是 alpha 不是 risk。

請給最 BRUTAL contrarian view，每個 finding 標 severity (CRITICAL / HIGH / MEDIUM)。

直接 markdown 5-7 個 findings。不要客套。"""


def call_gemini_vertex(prompt: str) -> str:
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
        return f"ERROR: GCP auth — {e}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8192},
    }
    r = requests.post(endpoint, headers=headers, json=body, timeout=180)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:1000]}"
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}\nRaw: {json.dumps(r.json(), indent=2)[:800]}"


def call_claude(prompt: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "ERROR: ANTHROPIC_API_KEY 未設"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-5-20250929",
        "max_tokens": 8192,
        "system": "You are a skeptical senior investment strategist. Find blind spots and contrarian views. Be brutal and specific. Reply in 繁體中文.",
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=180)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:1000]}"
    try:
        return r.json()["content"][0]["text"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}"


def call_openai(prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "ERROR: OPENAI_API_KEY 未設"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are a skeptical senior investment strategist. Find blind spots and contrarian views. Be brutal and specific. Reply in 繁體中文."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 4096,
    }
    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, timeout=180)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:1000]}"
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}"


def main():
    print("=" * 80)
    print("  Multi-AI Strategic Review V2 — 三方 AI 找盲點")
    print("=" * 80)

    docs_dir = ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)

    print(f"\n  Prompt size: {len(PROMPT):,} chars")
    print()

    print("[1/3] Gemini 2.5 Pro via Vertex AI...")
    gemini = call_gemini_vertex(PROMPT)
    print(f"  返回 {len(gemini):,} chars")
    (docs_dir / "review_v2_gemini.md").write_text(
        f"# Gemini 2.5 Pro V2 Review\n\n{gemini}", encoding="utf-8"
    )

    print("\n[2/3] Claude Sonnet 4.5...")
    claude = call_claude(PROMPT)
    print(f"  返回 {len(claude):,} chars")
    (docs_dir / "review_v2_claude.md").write_text(
        f"# Claude Sonnet 4.5 V2 Review\n\n{claude}", encoding="utf-8"
    )

    print("\n[3/3] OpenAI GPT-4o...")
    gpt = call_openai(PROMPT)
    print(f"  返回 {len(gpt):,} chars")
    (docs_dir / "review_v2_gpt.md").write_text(
        f"# GPT-4o V2 Review\n\n{gpt}", encoding="utf-8"
    )

    for label, resp in [("Gemini 2.5 Pro", gemini), ("Claude Sonnet 4.5", claude), ("GPT-4o", gpt)]:
        print("\n" + "=" * 80)
        print(f"  {label}")
        print("=" * 80)
        print(resp[:5000])
        if len(resp) > 5000:
            print(f"\n... ({len(resp) - 5000} chars 截斷，完整見 docs/review_v2_*.md)")


if __name__ == "__main__":
    main()
