"""
FinMind Sponsor Pro 端點探測 — 訂閱完成後第一個跑的。

驗證以下端點哪些可用：
  - TaiwanStockTick: 真實 tick (含買賣方向 type)
  - TaiwanStockOrderBook: 五檔深度
  - 其他 Sponsor Pro exclusive datasets

對每個端點：
  - 試抓 2330 一天的資料
  - 量化回傳 size + 欄位 + 耗時
  - 估算全市場 backfill 規模

跑完判斷：
  - 哪些端點真的有
  - 該選哪 N 檔做 1-2 年 backfill
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date
from pathlib import Path

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
TEST_DATE = date(2025, 4, 15)
TEST_TICKER = "2330"

# 候選端點（依 FinMind 常見命名 + 經驗）
CANDIDATES = [
    "TaiwanStockTick",
    "TaiwanStockTickTrade",
    "TaiwanStockMatch",
    "TaiwanStockOrderBook",
    "TaiwanStockBidAsk",
    "TaiwanStockOrder",
    "TaiwanStockTickInfo",
    "TaiwanStockEvery5Sec",
    "TaiwanStockMinute",
    # 可能 sponsor 級的衍生
    "TaiwanStockDayTrading",
    "TaiwanStockDealer",
]


def try_endpoint(token: str, dataset: str) -> dict:
    params = {
        "dataset": dataset, "data_id": TEST_TICKER,
        "start_date": TEST_DATE.isoformat(),
        "end_date": TEST_DATE.isoformat(),
        "token": token,
    }
    t0 = time.time()
    try:
        resp = requests.get(API_URL, params=params, timeout=30)
        elapsed = (time.time() - t0) * 1000
        try:
            payload = resp.json()
        except Exception as e:
            return {"ok": False, "error": f"JSON: {e}", "elapsed_ms": elapsed}
        status = payload.get("status")
        msg = payload.get("msg", "")
        rows = payload.get("data") or []
        if status != 200:
            return {
                "ok": False, "error": f"status={status} msg={msg!r}",
                "elapsed_ms": elapsed,
            }
        return {
            "ok": True, "elapsed_ms": elapsed, "n_rows": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
            "sample": rows[0] if rows else None,
            "last_sample": rows[-1] if rows else None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed_ms": (time.time() - t0) * 1000}


def main() -> None:
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_KEY") or ""
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    print(f"FinMind Sponsor Pro 端點探測（token: {token[:10]}...）")
    print(f"測試標的: {TEST_TICKER}, 日期: {TEST_DATE}")
    print("=" * 80)

    available = []
    for ds in CANDIDATES:
        result = try_endpoint(token, ds)
        if result["ok"]:
            n = result["n_rows"]
            cols = result.get("columns", [])
            ms = result["elapsed_ms"]
            print(f"  ✅ {ds:<30} n={n:>6} elapsed={ms:>5.0f}ms  cols={cols}")
            if result.get("sample"):
                print(f"     first: {result['sample']}")
            if result.get("last_sample") and n > 1:
                print(f"     last : {result['last_sample']}")
            available.append((ds, result))
        else:
            err = result.get("error", "unknown")[:60]
            print(f"  ❌ {ds:<30} {err}")
        time.sleep(0.2)

    print("\n" + "=" * 80)
    print(f"可用端點: {len(available)}")
    print("=" * 80)

    if available:
        # 估算全市場 backfill 規模
        n_tickers_full = 1853   # weekly EH universe
        days_2y = 500
        for ds, res in available:
            avg_rows = res.get("n_rows", 0)
            avg_ms = res.get("elapsed_ms", 0)
            est_rows_full = avg_rows * n_tickers_full * days_2y
            est_seconds = (avg_ms / 1000) * n_tickers_full * days_2y
            print(f"\n  {ds}:")
            print(f"    {TEST_TICKER} 1 日 ~ {avg_rows:,} rows")
            print(f"    全市場 2 年估算: {est_rows_full/1e9:.2f}B rows, "
                  f"~{est_seconds/3600:.0f} 小時")

    print("\n" + "=" * 80)
    print("下一步建議：")
    print("=" * 80)
    print("  1. 上面 ✅ 的端點看「rows 數 + 欄位」決定要抓哪些")
    print("  2. 若有 TaiwanStockTick（含 type 標買賣方向）→ 內外盤比訊號可做")
    print("  3. 若只有 minute / 5-sec → 跟現有 KBar 重疊，CP 值降低")
    print("  4. Backfill 規模：建議只抓「我們已用的 25 ticker × 1-2 年」(可控)")


if __name__ == "__main__":
    main()
