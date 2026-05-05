"""
Multi-AI Validation V3 — 三個新策略 backtest 結果交叉驗證

驗證對象（剛跑完）:
1. AB 雙重共識 (TW market) — n=578, mean +8.60%, OOS 不穩 (2017-19 失效, 2020-22 強, 2023-25 邊緣)
2. VIX/VIX3M ratio → SPY timing — moderate backwardation 黃金, deep 反而失效（反教科書）
3. SPY-QQQ pair daily — 15 年 CAGR -0.33%, 完全不符 memory ES-NQ +15.7% claim

對每個策略，問:
- 結論可靠嗎? 統計方法 / 樣本 / regime / look-ahead 有問題嗎?
- 我的解讀正確嗎? 有沒有 alternative explanation?
- 該不該 deploy?
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


PROMPT = """你是頂尖量化策略 SKEPTICAL REVIEWER。我剛跑完 3 個策略 backtest，
要你獨立 challenge 結果，找出我可能漏掉的盲點 / 統計錯誤 / 解讀偏差。

---

## 策略 1: AB 雙重共識 (TW market multifactor)

**Setup**:
- A signal: 連漲 + 法人買 (3 日內 ≥2 次 ≥9% 漲幅 AND 當日法人 ≥200 張)
- B signal: 散戶低 + 量爆 (散戶比例 < 252日 p20 AND 量能 z >= 2.5)
- Trigger: 同檔同日 A∩B
- Hold: 60 day, next-day open entry, COST 0.78%
- Universe: 1954 TW tickers (排除 10 大型權值)
- Period: 2017-2025 (9 年)

**結果**:
| 指標 | 值 |
|---|---|
| n events | **578** (vs 之前 sample 126，4.6 倍) |
| Mean | +8.60% |
| Median | -2.25% |
| Std | 43.77% |
| t-stat | +4.73 |
| p-value | < 0.0001 |
| Win rate | 47.1% |
| Random baseline (same ticker random window) | +4.80% |
| **Incremental alpha** | **+3.81pp** |

**OOS Walk-Forward**:
| Period | n | mean | t | p |
|---|---|---|---|---|
| 2017-2019 | 138 | +0.98% | +0.37 | **0.35 ❌** |
| 2020-2022 | 267 | **+13.87%** | +4.36 | <0.001 ✅ |
| 2023-2025 | 169 | +4.08% | +1.57 | **0.06 邊緣** |

**Year-by-year**:
- 2018: -5.73% (n=56, win 28.6%) ← 系統性失敗
- 2020: +33.02% (n=74, win 62%) ⚠️ outlier
- 2024: -2.99% (n=12, win 67%)
- 2026: +111% (n=4, 樣本太少)

**Top tickers** (concentration):
- 2337 (旺宏) 7 events mean +84% ← single ticker huge driver
- 1760 (碩天) 6 events mean -19%

**我的解讀**:
1. 真有 alpha 但靠 2020 COVID era outlier (剝離 2020 平均 ~+5%, 接近 baseline)
2. OOS 1H 失效 + 3H 邊緣 → regime-dependent
3. 47% win 配 mean +8.6% = 肥尾分佈
4. 建議保留 scanner detection，不做主力，單筆 ≤3% portfolio

**請挑戰**:
- 用 random baseline 對 same-ticker 隨機進場，但這對「肥尾型策略」是否公平？baseline 自己也吃肥尾，可能 underestimate true alpha
- 「Incremental alpha +3.81pp」是否被 2020 outlier 也污染了 baseline?
- 47% win rate 在 long-only 60d hold 是否實際過低（個股 60d 自然 win rate 約 50-55%）
- Top ticker 2337 mean +84% 等於是「靠單一 outlier 撐」，是否該另外 risk-adj?
- 我說的「2020 outlier」剝離後 +5% 算法，是否簡化過度（2020 的 ticker selection 可能對其他年份 representative）

---

## 策略 2: VIX/VIX3M ratio → SPY timing

**Setup**:
- ratio = VIX / VIX3M (proxy for term structure)
- contango: ratio < 1.0 (90% 時間)
- backwardation: ratio > 1.0 (panic)
- Period: 2006-07 ~ 2026-02, n=4921 days

**結果 (SPY fwd 20d return by ratio bucket)**:
| Bucket | n | mean | win |
|---|---|---|---|
| Deep contango (<0.85) | 1197 | +0.67% | 65.7% |
| Normal contango (0.85-0.95) | 2437 | +0.90% | 69.1% |
| Slight contango (0.95-1.0) | 743 | +1.10% | 65.9% |
| **Mild backwardation (1.0-1.05)** | 321 | **+1.97%** | 66.7% |
| **Moderate backwardation (1.05-1.10)** | 90 | **+4.32%** | **82.2%** |
| **Deep backwardation (≥1.10)** | 133 | **-1.36%** | 58.6% |

**反教科書發現**:
- 教科書說「買 deepest panic」→ 但 deep backwardation (≥1.10) **fwd 20d -1.36%**
- 黃金區是 moderate backwardation (1.05-1.10), +4.32%, 82% win

**我的解讀**:
- Deep backwardation 通常出現在 crash 中段 (2008/2020/2025-04-07)，市場還沒到底
- Moderate backwardation 是 panic 結束、reversion 啟動的 sweet spot
- 建議 deploy 為 0050/00646 加碼訊號 (ratio 1.05-1.10 trigger)

**請挑戰**:
- n=90 (moderate) 是否太小，spread 跨 regime 不穩? (2008 vs 2020 vs 2025 三次 crash 樣本可能差異大)
- Deep backwardation 的 -1.36% 在 n=133 是否 path-dependent (集中在 2008 雷曼後幾週)?
- VIX/VIX3M ratio 跟「真正 VIX 期貨曲線」差別 — VIX 是現貨 30d implied, VIX3M 是 93d implied，但都不是真正期貨。在 panic 時 VIX 跳得比 VIX3M 快是 mechanical not signal?
- 「buy moderate but not deep」的 rule 在 forward-looking 環境下能 ex ante 知道哪個是 moderate vs deep 嗎? 還是 hindsight bias?
- 樣本 2006-2025 包含 2008/2011/2020 三個大 crash，是否 over-fit 在這幾個事件?

---

## 策略 3: SPY-QQQ Pair Daily (ES-NQ ETF proxy)

**Setup**:
- spread = log(QQQ) - log(SPY)
- 60 day rolling z-score
- |z| > 2 entry, |z| < 0.5 exit, 20d timeout
- Cost 0.05% × 4 legs = 0.2%/round trip
- Period: 2010-2025 (15 年)

**結果**:
| 指標 | 值 |
|---|---|
| Total trades | 94 |
| CAGR | **-0.33%/yr** |
| Win rate | 52.1% |
| Cumulative | -5.1% |
| t-stat | -0.21 |
| **vs SPY BTH same period** | SPY +14% CAGR vs pair -0.3% (落後 14pp/yr) |

**By direction**:
- long QQQ (n=36): mean +0.30%, win 64%
- short QQQ (n=58): mean -0.25%, win **44.8%** ← failed direction

**OOS**:
| Period | mean | cum |
|---|---|---|
| 2010-2014 | -0.31% | -9.0% |
| 2015-2019 | -0.47% | -10.4% |
| 2020-2025 | +0.38% | +15.3% |

**memory 聲稱 "ES-NQ pair daily +15.7%/5y" — 重跑 ETF 版本 -0.33%/yr，矛盾巨大**

**我的解讀**:
- NQ vs SPY 不是 mean reversion，是 secular trend (NQ 結構性領先)
- short_QQQ 方向 win 44.8% = 賭「QQQ 過熱會收斂」失敗
- ETF 版本不能 reproduce ES-NQ 期貨 alpha
- 可能差異: hedge ratio (我用 1:1)、roll/carry (期貨換月)、parameters

**請挑戰**:
- z-score window 60 日是否太短? 美股 sector rotation 週期可能 3-6 個月
- 1:1 log spread 跟 notional ratio (NQ/ES contract size $20K vs $50K) 是否導致 hedge 不平?
- ES-NQ 期貨「每日結算」vs ETF 「日內無結算」的微結構差異
- memory 的 +15.7% 可能是 short-only / long-only / breakout 策略，不是我假設的 mean reversion?
- 也可能 memory 的 5 年 sample 剛好抓到 2018 sector rotation cycle?

---

## 共同問題（all 3 strategies）:

1. **Look-ahead bias**: 我用 next-day open, 60d 後 close。理論上沒 look-ahead，但 random baseline 用 same ticker 隨機進場 — 這個 baseline 是否也用 next-day open?

2. **Sample selection**: AB 用 1954 ticker，但很多小型股流動性差。是否該按市值分層?

3. **Survivorship bias**: 9 年都還在的 ticker 才能 backtest，下市的不能。對小型股策略致命嗎?

4. **Cost model**: AB 用 0.78% (round-trip 60d hold 是 OK)。VIX/SPY 沒扣 cost (因為是 timing signal 不是 trade)。SPY-QQQ pair 用 0.2% (4 legs × 0.05%)。一致嗎?

請給每個策略一個 verdict (DEPLOY / DEPLOY WITH CAVEAT / REJECT) 加 5-7 個 critical findings。直接回繁體中文。"""


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
        "max_tokens": 8192,
        "system": "You are a senior quantitative strategy reviewer at a hedge fund. Be brutally skeptical. Find statistical / methodological / regime-dependent flaws. Reply in 繁體中文.",
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
            {"role": "system", "content": "You are a senior quantitative strategy reviewer. Find statistical and methodological flaws. Be brutally skeptical. Reply in 繁體中文."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, timeout=240)
    if r.status_code != 200:
        return f"ERROR {r.status_code}: {r.text[:1000]}"
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}"


def main():
    print("=" * 80)
    print("  Multi-AI Strategy Validation V3 — 3 策略交叉驗證")
    print("=" * 80)
    print(f"\n  Prompt size: {len(PROMPT):,} chars")

    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)

    print("\n[1/3] Gemini 2.5 Pro...")
    gemini = call_gemini_vertex(PROMPT)
    print(f"  {len(gemini):,} chars")
    (docs / "validate_v3_gemini.md").write_text(f"# Gemini V3\n\n{gemini}", encoding="utf-8")

    print("\n[2/3] Claude Sonnet 4.5...")
    claude = call_claude(PROMPT)
    print(f"  {len(claude):,} chars")
    (docs / "validate_v3_claude.md").write_text(f"# Claude V3\n\n{claude}", encoding="utf-8")

    print("\n[3/3] GPT-4o...")
    gpt = call_openai(PROMPT)
    print(f"  {len(gpt):,} chars")
    (docs / "validate_v3_gpt.md").write_text(f"# GPT V3\n\n{gpt}", encoding="utf-8")

    for label, resp in [("Gemini 2.5 Pro", gemini), ("Claude Sonnet 4.5", claude), ("GPT-4o", gpt)]:
        print("\n" + "=" * 80)
        print(f"  {label}")
        print("=" * 80)
        print(resp[:6000])
        if len(resp) > 6000:
            print(f"\n... ({len(resp) - 6000} chars 截斷, 完整 docs/validate_v3_*.md)")


if __name__ == "__main__":
    main()
