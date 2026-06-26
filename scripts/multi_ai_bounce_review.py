"""Multi-AI review for the bounce strategy after OOS + MCPT.

Sends actual backtest results to GPT/Gemini/Claude to find blind spots
specific to this strategy (lookahead, regime bias, methodology issues).
"""
from __future__ import annotations
import os, sys, io
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env", override=True)

import requests


# Inject backtest results dynamically
RESULTS_PATH = ROOT / "docs" / "bounce_oos_mcpt.md"


def build_prompt() -> str:
    if RESULTS_PATH.exists():
        results = RESULTS_PATH.read_text(encoding="utf-8")
    else:
        results = "(尚未跑出結果)"

    return f"""你是 senior quant strategy reviewer。我設計了一個短線「抓反彈」策略,跑了 90 天 backtest + OOS + MCPT 已附,請**極度懷疑**地審視盲點。

---

## 策略定義

**Trigger** (per-ticker, daily scan):
1. 5d 累跌 -8 ~ -25%  OR  10d 累跌 -12 ~ -25%
2. RSI 14 < 35 (oversold)
3. close < MA20 (短期跌破)
4. close > MA60 (中期均線守住,排除崩跌型)
5. 60d max drawdown > -35% (排除完全崩壞)
6. avg dollar volume > 50 萬 NT$/day (排除殭屍股)

**Score**: RSI 越低 + 跌幅越大 + 站 60MA 越遠 → score 越高

**Entry/Exit**:
- T+1 09:00 限價 +0.5% buy
- 5 day hold,T+6 close 平倉
- 中途觸 +5% TP → 鎖一半;-7% stop
- Cost: 0.78% RT (TW long-only)

**Universe**: 2329 個 4-digit TW listed stocks (ex-ETFs)

---

## 90 天 backtest 結果

{results}

---

## 你的任務

請挑出 **5 個最致命的盲點**,並按嚴重度排序。每個說明:
1. 為什麼是盲點
2. 對 +6.79% mean 的可能 inflation
3. 怎麼測 (具體 audit 步驟)

特別 challenge:
- **Sample period bias**: 90 天剛好是 Q1 correction 反彈期,任何 oversold 都會反彈
- **Survivorship**: 只有「現在還在交易」的 ticker 在 universe,死掉的 not 包括
- **Look-ahead**: scan(d) 用 close.iloc[i] 的 RSI,確認 RSI 計算只用 ≤d 的資料
- **Selection bias**: 只挑分數最高的會 inflate alpha (top quintile +10.99% 但實際我們挑 1-3 檔不是全 110)
- **Cost**: 0.78% 是否含 slippage?
- **Position overlap**: 同一 ticker 5 天內可能多次觸發 → autocorrelation,inflated t-stat

最後給 verdict:
- (A) **Deploy 全力** — 真有 alpha,可大量試
- (B) **Deploy 小量試** — 有 alpha 但 sample 不足,先 5 萬 NT$ 試水
- (C) **More audits before deploy** — 列出必須的 3 個 audit
- (D) **Reject** — alpha 是 backtest illusion

回繁體中文,具體有數字。"""


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
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 6144},
    }
    r = requests.post(endpoint, headers=headers, json=body, timeout=300)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:1000]}"
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}"


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
        "max_tokens": 6144,
        "system": ("You are a senior quant strategy reviewer at a top hedge fund. "
                   "Be brutally skeptical. Find statistical, methodological, regime-dependent "
                   "and microstructure flaws. Reply in 繁體中文 with concrete numbers."),
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers=headers, json=body, timeout=300)
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
            {"role": "system",
             "content": ("You are a senior quant strategy reviewer at a top hedge fund. "
                         "Be brutally skeptical. Find flaws. Reply in 繁體中文 with concrete numbers.")},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers=headers, json=body, timeout=300)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:1000]}"
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}"


def main():
    prompt = build_prompt()
    print(f"Prompt size: {len(prompt):,} chars")
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)

    print("\n[1/3] Gemini 2.5 Pro...")
    g = call_gemini_vertex(prompt)
    print(f"  {len(g):,} chars")
    (docs / "bounce_review_gemini.md").write_text(
        f"# Gemini — Bounce Review\n\n{g}", encoding="utf-8")

    print("\n[2/3] Claude Sonnet 4.5...")
    c = call_claude(prompt)
    print(f"  {len(c):,} chars")
    (docs / "bounce_review_claude.md").write_text(
        f"# Claude — Bounce Review\n\n{c}", encoding="utf-8")

    print("\n[3/3] GPT-4o...")
    p = call_openai(prompt)
    print(f"  {len(p):,} chars")
    (docs / "bounce_review_gpt.md").write_text(
        f"# GPT — Bounce Review\n\n{p}", encoding="utf-8")

    print("\nDone.")


if __name__ == "__main__":
    main()
