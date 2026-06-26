"""
Multi-AI Dashboard / Discord UX Review

問 Gemini + Claude + GPT 改善建議，每個 AI 從不同 UX/視覺/資訊架構角度。

避免 life advice (memory: feedback_no_medical_career_advice)。
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


PROMPT = """你是 UX / 資訊架構 reviewer。我有一個量化投資儀表板 + Discord 推播系統，
需要你審查現況並給「資訊呈現 / 易讀性」建議。

⚠️ **規則**: 只給 UX / 資訊架構 / 視覺設計 critique。不要 career advice / life coaching。

## 系統架構

### 1. GUI Tkinter Dashboard (scripts/dashboard_gui.py, 2300+ lines)

左側 (滾動式):
1. 💰 累計成績 (4 cells: 總資產 / 現金 / 持股市值 / 檔數)
2. 🎯 市場 Regime V2 (CRASH/BEAR/SIDEWAYS/BULL_TREND/STRONG_BULL + 距 MA200 + vol30 + 60d ret + 推薦動作)
3. 🛡️ Hedge Signals (5 訊號 grid: TX OI z / VIX / VIX/VIX3M / TX basis / SPY 隔夜)
4. 💼 Barbell 配置 (target/current/delta 8 buckets table + top 3 actions)
5. 🌙 夜盤訊號 (預測明日開盤跳空)
6. 📅 今日 DCA Timing 評分
7. 💼 持股 (table: ticker / 股數 / 成本 / 現價 / P&L)
8. 🎯 ORB 訊號 (paper trade)
9. 📡 法人訊號 (真 alpha 驗證後)
10. 📈 短線 watchlist
11. 📈 DCA 進度 (9 週分批)

右側:
- 動作清單 (priority: 必做/建議/觀察)
- 系統訊號 (部署排程 + Alpha Decay)
- 最近 trades
- 事件 log

刷新: 每 60 秒 update 一次

### 2. Discord 推播 (scripts/daily_signal_scanner.py 14:00 cron)

每天 push 內容包含:
- 🐉 妖股 #1 (連漲+法人買)
- 📊 多因子 S1+S3 (中小妖股)
- 💰 月營收 Relative YoY (最有 alpha)
  - Deploy-Ready (L4 流動性 > 10億/日，可實單)
  - Informational only (流動性不足)
- 📈 量縮漲停 (informational)
- 📉 量縮跌停反彈 (informational)
- 多訊號共識 combo
- 訊號數量警示 (cluster)

## 已知問題 (我自己看到的)

GUI:
1. **資訊過多** - 11+ sections，user 早上 30 秒看不完
2. **沒有「今天該做什麼」一行 hero** - 重點被埋
3. **數據沒解釋** - "Foreign TX OI z=-1.10" user 可能不知道 z-score 意思
4. **CRASH 警報不夠顯眼** - 真正觸發時應該頂到最上面
5. **deltas % 沒換算 NT$** - 「+24pp」要換算成「該買 NT$143K」更直接

Discord:
1. **訊息太長** - 全部訊號顯示太擠，手機讀不完
2. **沒「重要級別」標題排序** - Deploy-Ready 應和 Informational 強烈視覺分開
3. **沒「今日總結」一行** - 開頭該有「✅ 0 個 Deploy / 5 informational / 0 hedge alert」
4. **不顯示持倉部位** - 看 Discord 不知道自己有什麼，與信號相關性不明
5. **重複資訊** - 多訊號共識和個別訊號重複
6. **沒指出「今天該動的金額」** - 應該說「現金 NT$371K，今天可用 NT$30K 加碼 0050」
7. **時區/即時性沒標** - SPY 隔夜訊號是台灣時間幾點?

## 用戶背景 (重要!)

- NT$594K portfolio
- 早上 08:30-09:00 看晨報 + dashboard (30 分鐘)
- 技術水平高 (能讀 z-score, t-stat)
- 但忙碌，要「立即抓重點」

## 你的任務

給 5-7 個具體 UX / 資訊架構改善建議，每個包含:
1. 問題描述 (user 會怎麼搞錯/錯失資訊)
2. 具體解法 (UI mockup 或 component 描述)
3. 優先序 (P0 critical / P1 important / P2 nice-to-have)
4. 工程量估算 (S/M/L)

特別關注:
- Information hierarchy (重要的在前面，次要的後面)
- Progressive disclosure (展開細節而非一次顯示全部)
- Action-oriented (告訴 user 該做什麼，不是給 raw data)
- Mobile-friendly (Discord 在手機讀)
- 警報層級 (CRASH 級警報必須跳出來)

請繁體中文回答。"""


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
        return f"ERROR: {e}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": 8192},
    }
    r = requests.post(endpoint, headers=headers, json=body, timeout=240)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:500]}"
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}"


def call_claude(prompt: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "ERROR: no key"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    body = {
        "model": "claude-sonnet-4-5-20250929", "max_tokens": 8192,
        "system": "You are a senior UX / information architecture reviewer for fintech apps. Focus on usability, information hierarchy, mobile-friendliness, alert systems. Reply 繁體中文.",
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=240)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:500]}"
    try:
        return r.json()["content"][0]["text"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}"


def call_openai(prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "ERROR: no key"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "gpt-4o", "temperature": 0.5, "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": "You are a senior UX / information architecture reviewer for fintech dashboards. Focus on usability, information hierarchy, mobile-friendliness, alert systems. Reply 繁體中文."},
            {"role": "user", "content": prompt},
        ],
    }
    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, timeout=240)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:500]}"
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}"


def main():
    print("=" * 80)
    print("  Multi-AI Dashboard / Discord UX Review")
    print("=" * 80)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)

    print("\n[1/3] Gemini 2.5 Pro...")
    gemini = call_gemini_vertex(PROMPT)
    print(f"  {len(gemini):,} chars")
    (docs / "ux_review_gemini.md").write_text(f"# Gemini UX Review\n\n{gemini}", encoding="utf-8")

    print("\n[2/3] Claude Sonnet 4.5...")
    claude = call_claude(PROMPT)
    print(f"  {len(claude):,} chars")
    (docs / "ux_review_claude.md").write_text(f"# Claude UX Review\n\n{claude}", encoding="utf-8")

    print("\n[3/3] GPT-4o...")
    gpt = call_openai(PROMPT)
    print(f"  {len(gpt):,} chars")
    (docs / "ux_review_gpt.md").write_text(f"# GPT UX Review\n\n{gpt}", encoding="utf-8")

    for label, resp in [("Gemini 2.5 Pro", gemini), ("Claude Sonnet 4.5", claude), ("GPT-4o", gpt)]:
        print("\n" + "=" * 80)
        print(f"  {label}")
        print("=" * 80)
        print(resp[:5500])
        if len(resp) > 5500:
            print(f"\n... ({len(resp) - 5500} chars 截斷, 完整見 docs/ux_review_*.md)")


if __name__ == "__main__":
    main()
