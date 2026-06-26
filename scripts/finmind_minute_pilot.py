"""
ORB Step 1：FinMind 分鐘線資料可行性檢查（半天工程）。

目標：
  1. 確認 FinMind Sponsor 是否真的提供 TaiwanStockPriceMinute 端點
  2. 量化單筆 API 耗時 / 回傳資料 size / 欄位
  3. 試抓 10 檔（5 大型 + 5 小型）2024-04 一週看資料完整度

Go/No-go：
  - API 成功且資料完整 → 進 Step 2 (orb_signal_diagnostic.py)
  - 端點不存在 / 權限不足 / 資料殘缺 → stop，告知用戶決策
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

API_URL = "https://api.finmindtrade.com/api/v4/data"
CACHE = ROOT / "data" / "cache" / "finmind" / "minute"
CACHE.mkdir(parents=True, exist_ok=True)

# 測試標的：5 大型權值 + 5 小型妖股
LARGE_CAP = ["2330", "2317", "2454", "2891", "0050"]
SMALL_CAP = ["3595", "5213", "6186", "8038", "4117"]
SAMPLE_TICKERS = LARGE_CAP + SMALL_CAP

# 試抓 2024-04-15 ~ 2024-04-19（一週）
TEST_START = date(2024, 4, 15)
TEST_END = date(2024, 4, 19)

# 候選端點名（不確定實際名稱，依序試）
CANDIDATE_DATASETS = [
    "TaiwanStockPriceMinute",
    "TaiwanStockKBar",
    "TaiwanStockPrice5MinK",
]


def try_fetch(token: str, dataset: str, ticker: str, d: date) -> dict:
    """單次請求 → 回傳 metadata + first row。"""
    params = {
        "dataset": dataset,
        "data_id": ticker,
        "start_date": d.isoformat(),
        "end_date": d.isoformat(),
        "token": token,
    }
    t0 = time.time()
    try:
        resp = requests.get(API_URL, params=params, timeout=30)
        elapsed_ms = (time.time() - t0) * 1000
        try:
            payload = resp.json()
        except Exception as e:
            return {"ok": False, "error": f"JSON parse: {e}", "elapsed_ms": elapsed_ms}
        status = payload.get("status")
        msg = payload.get("msg", "")
        rows = payload.get("data") or []
        if status != 200:
            return {
                "ok": False, "error": f"status={status} msg={msg}",
                "elapsed_ms": elapsed_ms, "n_rows": 0,
            }
        return {
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "n_rows": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
            "first_row": rows[0] if rows else None,
            "last_row": rows[-1] if rows else None,
        }
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Request: {e}", "elapsed_ms": (time.time() - t0) * 1000}


def main() -> None:
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_KEY") or ""
    if not token:
        print("❌ FINMIND_TOKEN not set — abort")
        return
    print(f"FinMind token loaded ({token[:10]}...)")

    # ─── Phase 1: 端點可行性 ───
    print("\n" + "=" * 70)
    print("Phase 1：端點探測（試 3 個候選 dataset name）")
    print("=" * 70)

    valid_dataset = None
    for ds in CANDIDATE_DATASETS:
        result = try_fetch(token, ds, "2330", TEST_START)
        status = "✅" if result["ok"] else "❌"
        print(f"  {status} {ds:<32} elapsed={result.get('elapsed_ms', 0):.0f}ms")
        if not result["ok"]:
            print(f"     error: {result.get('error', 'unknown')}")
        else:
            print(f"     n_rows={result['n_rows']}, columns={result['columns']}")
            if result["first_row"]:
                print(f"     first_row sample: {result['first_row']}")
            valid_dataset = ds
            break  # 找到第一個成功的就夠

    if valid_dataset is None:
        print("\n💀 所有候選端點都失敗 — FinMind Sponsor 可能不含分鐘線")
        print("   建議下一步：")
        print("     1. 升級 Sponsor Pro (NT$3330/月) 或")
        print("     2. 改用 Fugle 即時 API（需付費）或")
        print("     3. 放棄當沖模組，回 v3.7 paper trading")
        return

    print(f"\n✅ 找到可用端點: {valid_dataset}")

    # ─── Phase 2: 多檔多日資料完整度檢測 ───
    print("\n" + "=" * 70)
    print(f"Phase 2：10 檔 × 5 日資料完整度（{TEST_START} ~ {TEST_END}）")
    print("=" * 70)

    all_results = []
    biz_days = []
    cur = TEST_START
    while cur <= TEST_END:
        if cur.weekday() < 5:  # 跳過週末
            biz_days.append(cur)
        cur += timedelta(days=1)

    total_calls = len(SAMPLE_TICKERS) * len(biz_days)
    call_idx = 0
    print(f"  總請求數: {total_calls}")
    print(f"  {'ticker':>6} {'date':<12} {'rows':>5} {'elapsed':>8}  {'note':<30}")
    print("  " + "─" * 70)
    for tk in SAMPLE_TICKERS:
        for d in biz_days:
            call_idx += 1
            r = try_fetch(token, valid_dataset, tk, d)
            note = "OK" if r["ok"] else r.get("error", "unknown")[:30]
            n = r.get("n_rows", 0)
            elapsed = r.get("elapsed_ms", 0)
            cap = "L" if tk in LARGE_CAP else "S"
            print(f"  {cap}{tk:>5} {d!s:<12} {n:>5} {elapsed:>6.0f}ms  {note:<30}")
            all_results.append({
                "ticker": tk, "date": d, "cap": cap,
                "ok": r["ok"], "n_rows": n, "elapsed_ms": elapsed,
                "error": r.get("error") if not r["ok"] else "",
            })
            time.sleep(0.05)   # polite delay

    df = pd.DataFrame(all_results)
    out_csv = ROOT / "logs" / "minute_pilot_metadata.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    # ─── Phase 3: 統計報告 ───
    print("\n" + "=" * 70)
    print("Phase 3：總結")
    print("=" * 70)
    ok_rate = df["ok"].mean() * 100
    avg_rows = df[df["ok"]]["n_rows"].mean() if df["ok"].any() else 0
    avg_ms = df[df["ok"]]["elapsed_ms"].mean() if df["ok"].any() else 0
    total_rows = df["n_rows"].sum()

    print(f"  成功率           : {ok_rate:.0f}% ({df['ok'].sum()}/{len(df)})")
    print(f"  每筆平均回傳     : {avg_rows:.0f} rows")
    print(f"  每筆平均耗時     : {avg_ms:.0f}ms")
    print(f"  總資料量(5日10檔): {total_rows:,} rows")

    # 拆 large vs small
    if df["ok"].any():
        for cap in ["L", "S"]:
            sub = df[(df["cap"] == cap) & df["ok"]]
            if len(sub):
                print(f"  {cap} 平均 rows/day  : {sub['n_rows'].mean():.0f}")

    # 推估全市場 backfill 規模
    n_tickers_full = 1853
    days_2y = 500
    est_calls = n_tickers_full * days_2y
    est_seconds = est_calls * (avg_ms / 1000) if avg_ms > 0 else 0
    est_rows_full = avg_rows * est_calls
    print(f"\n  全市場 backfill 估算（1853 檔 × 500 日）：")
    print(f"    API 呼叫數     : {est_calls:,}")
    print(f"    預估耗時       : {est_seconds/3600:.1f} 小時")
    print(f"    預估 row 數    : {est_rows_full/1e6:.1f}M")
    print(f"    預估磁碟 size  : {est_rows_full * 80 / 1e9:.1f} GB（粗估每 row 80 bytes）")

    # ─── Go/No-go ───
    print("\n" + "=" * 70)
    print("Go/No-go 判決")
    print("=" * 70)
    if ok_rate >= 90 and avg_rows >= 100:
        print("  ✅ 資料可行 — 可進 Step 2 (orb_signal_diagnostic.py)")
    elif ok_rate < 50:
        print("  ❌ API 大量失敗 — 端點權限或 quota 問題，stop")
    else:
        print(f"  ⚠️  資料部分可行（成功率 {ok_rate:.0f}%）— 需用戶決定是否繼續")

    print(f"\n寫入 metadata: {out_csv.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
