"""
Gemini cross-review of INVEST strategy audit

Use Google Gemini via Vertex AI (Google Cloud, billed on GCP free trial credit).
Different model = different blind spots = catch issues Claude missed.

Auth setup (one-time):
  1. https://console.cloud.google.com/apis/library/aiplatform.googleapis.com → 啟用
  2. `gcloud auth application-default login`
  3. `pip install google-auth`

Env override:
  GEMINI_BACKEND=studio  # 強制走 AI Studio API key（GEMINI_API_KEY）
  GEMINI_BACKEND=vertex  # 強制走 Vertex AI（GCP credit）
  default: vertex
"""
from __future__ import annotations
import os
import sys
import io
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env")

import requests
import json

BACKEND = os.environ.get("GEMINI_BACKEND", "vertex").lower()
# Vertex AI model id (與 AI Studio 不同):
#   gemini-2.5-pro / gemini-2.5-flash / gemini-2.0-flash-001
# AI Studio: gemini-3-pro-preview
DEFAULT_MODEL = "gemini-2.5-pro" if BACKEND == "vertex" else "gemini-3-pro-preview"
MODEL = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

# Vertex AI 設定（吃 GCP free trial credit）
GCP_PROJECT = os.environ.get("GCP_PROJECT", "gen-lang-client-0502672630")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")

if BACKEND == "studio":
    API_KEY = os.environ.get("GEMINI_API_KEY", "")
    if not API_KEY:
        print("ERROR: GEMINI_API_KEY not set in config/.env")
        sys.exit(1)
    ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
else:
    ENDPOINT = (f"https://{GCP_REGION}-aiplatform.googleapis.com/v1/projects/"
                f"{GCP_PROJECT}/locations/{GCP_REGION}/publishers/google/models/"
                f"{MODEL}:generateContent")


def read_file(path: Path) -> str:
    if not path.exists():
        return f"(file not found: {path})"
    return path.read_text(encoding="utf-8")


def build_review_prompt():
    """組裝完整 audit context 給 Gemini"""
    audit_doc = read_file(ROOT / "docs" / "strategy_audit_20260504.md")
    followup = read_file(ROOT / "docs" / "strategy_audit_followup_20260504.md")

    # 簡化版 — 只附主要 audit doc + followup status
    prompt = f"""你是頂尖的台股量化策略 SKEPTICAL REVIEWER。User 的 INVEST 系統已經被 1 個 Claude reviewer 找到 14 個 bias，並修了大部分。

請從 GEMINI 角度做獨立 review，找 Claude reviewer 可能漏掉的問題。

## 系統 audit 文件 (原 14 個 bug)

{audit_doc}

---

## 修正後 status (前 reviewer + user fix)

{followup}

---

## 你的任務

1. **Validate fixes** — 修正邏輯有沒有 subtle bug?
2. **Find NEW issues** — Claude 沒抓到的 (你是不同模型，不同訓練)
3. **Quantitative challenge** — 對「修正後」的 alpha 數字 (Revenue YoY +2.03%, Quiet Limitdown +8.55%, VIX≥35 +9.05%)，這些真的是真 alpha 嗎? 或仍 over-fit?

特別關注:
- **Statistical pitfall**: clustering, multiple comparison, baseline coherency
- **Code-level bugs**: off-by-one, edge case, timezone, data alignment
- **Methodology**: 是否完全 IID 假設破壞? Block bootstrap 該不該用?
- **Real-world execution**: 摩擦成本、滑點、流動性、tax — backtest 假設 frictionless?

不要客氣。給 5-8 個你最 confident 的 finding，rate severity (CRITICAL / HIGH / LOW)。

特別評估:
- 在台股低 ATR 環境，"+2% / 60d" alpha 扣 0.74% round-trip 後實際 portfolio impact
- VIX≥35 robust signal 在過去 9 年只 ~2 個 cluster (2020 Q1 + 2022 Q1)，是否本質上 only 2 independent observations?
- 量縮跌停反彈 "+8.55%" 是否在 Taiwan 有 daily limit (10%) 結構性 effect?

請給結構化 output (markdown), 有具體 actionable suggestions."""
    return prompt


def _vertex_token() -> str:
    """從 gcloud Application Default Credentials 取 OAuth token (Vertex AI 需要)"""
    try:
        from google.auth import default
        from google.auth.transport.requests import Request as AuthRequest
    except ImportError:
        print("ERROR: google-auth 未安裝。執行 `pip install google-auth`")
        sys.exit(1)
    creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(AuthRequest())
    return creds.token


def call_gemini(prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,  # focused critique
            "maxOutputTokens": 8192,
        },
    }
    if BACKEND == "studio":
        params = {"key": API_KEY}
        r = requests.post(ENDPOINT, params=params, headers=headers, json=body, timeout=120)
    else:
        token = _vertex_token()
        headers["Authorization"] = f"Bearer {token}"
        r = requests.post(ENDPOINT, headers=headers, json=body, timeout=120)
    if r.status_code != 200:
        body = r.text[:1500] if r.text else "(empty body)"
        return f"ERROR {r.status_code}\nURL: {ENDPOINT}\nBody: {body}"
    j = r.json()
    try:
        return j["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        return f"PARSE ERROR: {e}\nRaw: {json.dumps(j, indent=2)[:1000]}"


def main():
    print("=" * 80)
    print(f"  Gemini Strategy Review ({MODEL})")
    print(f"  Backend: {BACKEND}", end="")
    if BACKEND == "vertex":
        print(f" (project={GCP_PROJECT}, region={GCP_REGION}, 走 GCP credit)")
    else:
        print(" (Google AI Studio API key)")
    print("=" * 80)
    prompt = build_review_prompt()
    print(f"\n  Prompt length: {len(prompt):,} chars")
    print("  Calling Gemini API...\n")

    response = call_gemini(prompt)
    print(response)

    # Save to file
    out = ROOT / "docs" / "gemini_review_20260504.md"
    out.write_text(f"# Gemini Strategy Review ({MODEL})\n\n{response}", encoding="utf-8")
    print(f"\n  💾 Saved to {out}")


if __name__ == "__main__":
    main()
