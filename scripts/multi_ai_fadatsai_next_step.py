"""Multi-AI consultation — what's the next step for fadatsai?

Context: 11 audits done + 6 patches deployed. Real paper trade sample is small
(13 closed). Recent post-patch 6 trades: mean -0.86%/trade vs research +1.26%
(1.3σ deviation, p=0.18, not statistically significant).

User has been heavily focused on auditing. Asking the 3 AIs:
"Given everything we've validated, what should the user actually DO this week?"

Output: docs/fadatsai_next_step_{gemini,claude,gpt}.md
"""
from __future__ import annotations
import os, sys, io
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


PROMPT = """你是頂尖 quant hedge fund 的 senior strategy reviewer。User 跑了一個 crypto perp
量化策略 (fadatsai),做完非常完整的 audit + patches,現在卡在「該觀察 vs 該動作」的決策點。
請給**直接、可執行的建議**(不要再列盲點)。

---

## 已完成 (前 11 層 audit + 6 patches)

### Audit 結論 (Sharpe 期望)

| 情境 | Sharpe | 樣本 |
|---|---|---|
| Bull market 22 memecoin universe (2024-2025) | **18.35** | 60K trades |
| Chained forward OOS 2026 (frozen prior-year threshold) | **16.58** | 16K |
| Non-meme universe 11 coins (BTC/SOL/DOGE/LINK/etc) | 13.07 | 20K |
| BTC alone 2022 bear (含 LUNA collapse) | 7.87 | 632 |
| Slow-grind 2024 bear (BTC -9% over 6mo) | **15.22** | 12K |
| Q1 2025 -30% drawdown | 19.23 | 16K |
| **2022 alive-coin portfolio (BTC/SOL/DOGE/SHIB), Apr-Nov, LUNA + FTX events** | **7.03** | 6K |
| Delisted coins (LUNA/ANC/FTT/WAVES/BTS/COCOS/TRB/BNX/DGB/TLM) | 5.38 | 5.5K |
| Mark vs Last price spread | <0.3% (negligible) | 43K |
| BTC/SOL/DOGE correlation | -0.22 (neg, hedge) | 349 days |

**Long-run blended Sharpe estimate: 13-14** (regime-weighted)

### 6 個 Patches deployed

1. 50%TP5+BB exit (50% lock at +5% TP, rest exit when BB squeeze ends, 48h cap)
2. Bear regime guard: BTC 30d < -10% → disable fund_low_sq / fund_mom_low_sq
3. Concurrent position cap 15 → 100
4. CSV schema migration (added tp_hit / exit_reason cols)
5. Cooldown 8h → 2h (per-coin RISK_MAX_POS_PER_COIN=1 already prevents stacking)
6. Per-coin Sharpe monitor: if coin's 30d t-stat < -1 (n≥10), auto-disable

---

## 現況: 13 closed paper trades 累積

### Pre-patch (7 trades, 4/26 to 5/5)
- WR 50%
- Mean PnL/trade: **+1.42%**
- 跟研究期望 +1.26% 一致 ✓

### Post-patch (6 trades, 5/5 16:33 onwards)
- WR 33% (2/6)
- Mean PnL/trade: **-0.86%**
- 跟研究期望落差: -2.12% (z = -1.32σ, p = 0.18 → **不統計顯著**)
- **全部 6 筆都是 SHORT** (近期 crypto 反彈,SHORT 自然逆風)
- 平均 hold time 1.5h (跟研究 AvgH=6 bars × 15min 一致)

### 細節
- HYPE -4.40% (13.5h, bb_exit) — 進場後價格走錯方向 +4.4%
- ACT +2.62%, FARTCOIN -2.08%, MOODENG -1.06%, HYPE +0.95%, ENA -1.21%
- 全 bb_exit 觸發 (no timeouts) → exit logic 健康
- 每 hold ≈ 0.5-1.5h,跟 research 預期一致

---

## User 的決策點

User 處於三選一:

**A. 觀察一週 (passive)** — 累積 30-50 trades 再 judge,中途不動策略
**B. 立即動作 (active)** — 改參數 (例如 cooldown 4h、TP 改 3%、暫停 SHORT) 救短期表現
**C. 部分倒退 (conservative)** — 把 50%TP+BB 改回 48h fixed hold,放棄跟 backtest 對齊但拿穩定性

User 心理面: 看到 6 筆 4 虧有點動搖,但理性上知道 sample 太小。

---

## 你的任務

請以**senior PM 給 trader 建議的口吻**,直接回答:

### Q1. 你選 A / B / C 哪個? 為什麼?

### Q2. 如果 7 天後 Sharpe 仍 < 5,你會怎麼診斷? 列**3 個最可能原因**+對應修法。

### Q3. **目前最危險的 cognitive bias** 是什麼?
(例: anchoring on backtest Sharpe 18 / loss aversion / sample-size denial / etc)
直接 call out user 容易掉進去的 trap。

### Q4. 你會不會建議 deploy real money? 多少? 在什麼條件下?

### Q5. 一句話 — 「下週你只該做 1 件事是什麼?」

直接、具體、可執行。**不要再列盲點 / audit 建議**(已經做夠了)。
回繁體中文。"""


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
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096},
    }
    r = requests.post(endpoint, headers=headers, json=body, timeout=240)
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
        "max_tokens": 4096,
        "system": ("You are a senior portfolio manager at a top crypto quant fund. "
                   "You're advising a trader on a tactical decision after extensive "
                   "audit work is already done. Be direct and actionable. Don't "
                   "list more audits to do — give operational guidance. Reply in 繁體中文."),
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=240)
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
             "content": ("You are a senior portfolio manager at a top crypto quant fund. "
                         "You're advising a trader on a tactical decision after extensive "
                         "audit work is done. Be direct and actionable. Don't list more "
                         "audits — give operational guidance. Reply in 繁體中文.")},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 3000,
    }
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers=headers, json=body, timeout=240)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:1000]}"
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}"


def main():
    print("=" * 80)
    print("  Multi-AI: fadatsai 下一步建議")
    print("=" * 80)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)

    print("\n[1/3] Gemini 2.5 Pro...")
    g = call_gemini_vertex(PROMPT)
    print(f"  {len(g):,} chars")
    (docs / "fadatsai_next_step_gemini.md").write_text(
        f"# Gemini — fadatsai 下一步\n\n{g}", encoding="utf-8")

    print("\n[2/3] Claude Sonnet 4.5...")
    c = call_claude(PROMPT)
    print(f"  {len(c):,} chars")
    (docs / "fadatsai_next_step_claude.md").write_text(
        f"# Claude — fadatsai 下一步\n\n{c}", encoding="utf-8")

    print("\n[3/3] GPT-4o...")
    p = call_openai(PROMPT)
    print(f"  {len(p):,} chars")
    (docs / "fadatsai_next_step_gpt.md").write_text(
        f"# GPT — fadatsai 下一步\n\n{p}", encoding="utf-8")

    print("\nDone. Reports in docs/fadatsai_next_step_*.md")


if __name__ == "__main__":
    main()
