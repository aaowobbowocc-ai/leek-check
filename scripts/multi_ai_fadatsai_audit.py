"""
Multi-AI Validation — fadatsai 8-layer audit cross-check.

請 GPT-4o / Gemini 2.5 Pro / Claude Sonnet 4.5 各自獨立 challenge 8 個已完成的
audit,找出 user (我) 還沒驗證的盲點。每個 AI 對相同 prompt 獨立回答,
然後對比它們的找到的 gaps 是否一致。

Output: docs/fadatsai_audit_{gemini,claude,gpt}.md
"""
from __future__ import annotations

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


PROMPT = """你是頂尖 quant hedge fund 的 senior strategy reviewer。我要你**極度懷疑**地審視一個
crypto perpetual futures 量化策略的 audit 結果,找出我**還沒驗證**的盲點。

---

## 策略 fadatsai 摘要

**Universe**: 22 個 Binance USDT-M memecoin perp (HYPE, POPCAT, FARTCOIN, WIF, BONK, 1000PEPE, 1000SHIB, MOODENG, ACT, SUI, ENA, ONDO 等)

**核心訊號** (三個,搭配 Bollinger Band 擠壓 + funding rate quantile):
1. `tight_comp_short` — BB squeeze 剛開始 + funding rate ∈ [p50, p75] → SHORT
2. `fund_high_sq` — BB squeeze 持續 + funding rate > p95 → SHORT
3. `fund_mom_low_sq` — BB squeeze 持續 + 連續 2 個 8h-settlement funding rate < p10 → LONG

**Quantile** 用 rolling 200 settlements (~67 days) 計算
**Exit**: 進場後若 high/low 觸 ±5%,50% 部位鎖 +5%;BB squeeze 結束或 48h timeout 時平剩餘
**Cost**: 0.08% round-trip (Binance taker 0.04% × 2)
**槓桿**: 最高 3x; 單倉位 ≤ 10% 帳戶
**Daily DD -5% 強制止損**, peak DD -5% 強制止損
**Max concurrent positions = 100**

---

## 8 層 audit 已執行 + 結果

| # | Audit 名稱 | Sample | 結果 (Sharpe) | 結論 |
|---|---|---|---|---|
| 1 | Rolling 60d quantile (修 look-ahead bias,原始用 full-period quantile) | 22 coins × 2024-2026 | LA 18.35 → WF 18.36 | -1% 影響,bug 真實但微小 |
| 2 | Chained forward OOS (2026 trades 用 2024+2025 frozen quantile) | 2025/2026 | LA 18.53 → CH 17.76 (2025), 15.33 → 16.58 (2026) | 跨年 robust,2026 反而 +8% |
| 3 | Non-meme universe blind test (BTC/SOL/DOGE/LINK/LTC/OP/AVAX/ARB/APT/TIA/XRP) | 11 coins × 2025 | Sharpe 13.07 (71% retention vs meme 18.35) | Alpha 不是 meme cherry-pick;BTC 自身全 fail (efficient market) |
| 4 | Bear regime test (BTC 2022-04 → 2022-07, BTC -61% incl LUNA collapse) | BTC × 92d | LA 6.87, WF 7.87, **CH 3.58 (bull-trained)** | bull thresholds 在 bear 大幅退化;rolling quantile 必須 |
| 5 | Funding payment during hold (原 PnL 沒含 funding 收付) | 22 × 2025 | 18.35 → 18.36 | 微小 |
| 6 | Drop first N days post-listing (測 post-listing hype bias) | 22 × 2025 | drop 30d: -0.06, drop 60d: -0.25, drop 90d: -0.59 | 最壞 -3%,沒崩 |
| 7 | Daily return correlation vs BTC/SOL/DOGE | 2025 | **-0.22 / -0.22 / -0.28** (負相關!) Crash-day BTC -2%+ 時 fadatsai +4.4% | 天然 hedge,真分散 |
| 8 | Concurrent position cap (50 / 100 / 150 / 200 / 250) | 22 × 2025 | cap=50: Sharpe 20.37, cap=100: 19.05, 200: 18.46, 250: 18.38 | 加 cap 反而 +Sharpe (drop 邊際信號) |

**綜合結論**: 修正後實戰期望 **Sharpe 10-13** (混合 80% bull + 20% bear),MDD -15-20%,vs INVEST/0050 真分散

---

## 我自己列的「可能還沒驗證」盲點 (但深度有限,你看是否同意 + 漏了什麼)

A. **倖存者偏差** — 22 檔全是 2026-05 還活著的妖幣,死掉的妖幣 (LUNA, FTT type) 不在樣本
B. **Strategy crowding** — 訊號被其他 bot 發現後 alpha decay
C. **Funding rate 操控** — 妖幣 OI 小,大戶可以推動 funding 觸發我的訊號然後反向獲利
D. **Token unlock cliff 事件** — 6/12 月解鎖傾倒 -50% 不在 backtest
E. **Portfolio-level MCPT** — individual signal MCPT 通過 (p<0.001),但 52-strategy 組合的 portfolio 級別 sign-permutation MCPT 沒做
F. **Funding distribution structural shift** — 過去 2 年 funding 分布穩定不代表未來 2 年也穩定
G. **API 故障 mode** — 5 分鐘 Binance API outage 期間怎麼處理 stop?  flash crash 期間 latency 暴增?
H. **Single-coin failure** — 1 檔 alpha decay 是否拖垮 portfolio?
I. **Position sizing** — 目前 equal-weight,Kelly fraction 是否更好?
J. **新上架 coin (post-2026-04)** — production 一定吃到,但樣本 0
K. **LUNA collapse 在 bear sample 內** — bear Sharpe 7.87 可能被那一週 short PnL 撐起
L. **Cluster correlation** — 平均 corr 0.05 但 BTC -10% 那種 flash crash 日,corr 是否變 0.7+

---

## 你的任務

請以一個**極度懷疑、不相信 audit 結果、想找出 alpha 是 illusion 的證據**的角度,回答:

### 1. 我的 8 層 audit 哪些**有方法論問題**?
具體指出哪個 audit 結果不該被相信。範例:audit #4 bear sample 只有 1 個 coin (BTC) × 92 天,sample 太小推論 portfolio bear regime 太勉強。

### 2. 我列的 12 個盲點 (A-L) 哪些**最致命**?  排優先序。

### 3. **我列表外**的盲點是什麼? 至少給 5 個我沒想到的。
範例: cross-exchange funding 套利者可能讓 funding 訊號失效;Binance funding 公式 2025 後改過嗎?;特定 coin 上市時的 listing burst 在 sample 內如何處理?

### 4. 你覺得真正的 expected Sharpe 是多少? 給範圍 + 為什麼。

### 5. Verdict:
- **DEPLOY (full confidence)** — audit 充分,Sharpe 10+ 可信,可上 25%
- **DEPLOY WITH CAVEAT** — 有未解風險,先上 5-10%
- **MORE AUDITS NEEDED** — 列出必須先驗證的 3 件事
- **REJECT** — alpha 是 backtest illusion

直接用繁體中文回答。給具體數字 + 統計理由,不要含糊。"""


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
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192},
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
        "max_tokens": 8192,
        "system": (
            "You are a senior quantitative strategy reviewer at a top crypto hedge fund. "
            "Be brutally skeptical. Find statistical, methodological, regime-dependent, "
            "and microstructure flaws. Reply in 繁體中文 with concrete numbers."
        ),
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=300)
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
             "content": ("You are a senior quantitative strategy reviewer at a top crypto hedge fund. "
                         "Be brutally skeptical. Find statistical, methodological, regime-dependent, "
                         "and microstructure flaws. Reply in 繁體中文 with concrete numbers.")},
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
    print("=" * 80)
    print("  Multi-AI fadatsai Audit Validation")
    print("=" * 80)
    print(f"\n  Prompt size: {len(PROMPT):,} chars")

    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)

    print("\n[1/3] Gemini 2.5 Pro (Vertex)...")
    gemini = call_gemini_vertex(PROMPT)
    print(f"  {len(gemini):,} chars")
    (docs / "fadatsai_audit_gemini.md").write_text(
        f"# Gemini 2.5 Pro — fadatsai Audit Review\n\n{gemini}", encoding="utf-8"
    )

    print("\n[2/3] Claude Sonnet 4.5...")
    claude = call_claude(PROMPT)
    print(f"  {len(claude):,} chars")
    (docs / "fadatsai_audit_claude.md").write_text(
        f"# Claude Sonnet 4.5 — fadatsai Audit Review\n\n{claude}", encoding="utf-8"
    )

    print("\n[3/3] GPT-4o...")
    gpt = call_openai(PROMPT)
    print(f"  {len(gpt):,} chars")
    (docs / "fadatsai_audit_gpt.md").write_text(
        f"# GPT-4o — fadatsai Audit Review\n\n{gpt}", encoding="utf-8"
    )

    print("\n" + "=" * 80)
    print("  Done. Reports written to docs/fadatsai_audit_*.md")
    print("=" * 80)


if __name__ == "__main__":
    main()
