"""
FinMind Master Backfill — 訂閱結束前的 cache 補強

實況盤點（finmind/finmind/ 路徑下，client 自動 cache）：
  HoldingSharesPer:        2437 ✓ 全市場（已完整）
  FinancialStatements:     2396 ✓ 全市場
  PER:                     2042 ✓ 全市場
  MonthRevenue:            2019 ✓ 全市場
  InstitutionalBuySell:    1988 ✓ 全市場
  ────────────────────────────────────────
  MarginPurchaseShortSale:   16 ✗ 待補（融資融券，散戶恐慌訊號）
  Shareholding:              27 ✗ 待補（外資 daily 持股，跟 institutional 互補）
  TradingDailyReport:        80 ✗ 跳過（分點籌碼，資料巨大且 alpha 未驗證）

策略：
  1. 用 client method（自動 cache 到 finmind/finmind/dataset_ticker.parquet）
  2. Universe = institutional cache 已有的 1988 檔
  3. 只 backfill 缺失的 ticker

預估時間：
  - 2 endpoints × ~1960 tickers = ~3920 reqs
  - FinMind Sponsor 600/h → ~6.5 小時，可背景跑

執行：
  python scripts/backfill_finmind_master.py [--only margin|foreign_own]
  python scripts/backfill_finmind_master.py --max-tickers 50  # 測試
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

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

from src.data.finmind_client import FinMindClient

START = date(2018, 1, 1)
CACHE_BASE = ROOT / "data" / "cache" / "finmind" / "finmind"

ENDPOINTS = {
    "holding": {
        "method": "get_holding_shares_per",
        "dataset_prefix": "TaiwanStockHoldingSharesPer",
        "description": "大戶持股分級（散戶比例 alpha 訊號 +11.3pp）",
    },
    "margin": {
        "method": "get_margin",
        "dataset_prefix": "TaiwanStockMarginPurchaseShortSale",
        "description": "融資融券餘額（散戶恐慌訊號）",
    },
    "foreign_own": {
        "method": "get_foreign_ownership",
        "dataset_prefix": "TaiwanStockShareholding",
        "description": "外資持股 daily（跟 institutional 互補）",
    },
    "institutional": {
        "method": "get_institutional",
        "dataset_prefix": "TaiwanStockInstitutionalInvestorsBuySell",
        "description": "三大法人買賣超（核心 alpha 來源）",
    },
    "revenue": {
        "method": "get_monthly_revenue",
        "dataset_prefix": "TaiwanStockMonthRevenue",
        "description": "月營收（PEAD alpha +3.95%/60d）",
    },
}


def get_universe() -> list[str]:
    """從 institutional cache 拿 ticker universe"""
    inst_pattern = "TaiwanStockInstitutionalInvestorsBuySell_*.parquet"
    tickers = sorted({
        p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
        for p in CACHE_BASE.glob(inst_pattern)
    })
    print(f"  Universe: {len(tickers)} tickers (from institutional cache)")
    return tickers


def get_done(dataset_prefix: str) -> set[str]:
    """檢查每個 cache 的最後一筆日期，距今 < 14 天才算 done

    避免 cache 卡在舊日期但 file 存在被誤判為 done
    """
    from datetime import timedelta
    threshold = date.today() - timedelta(days=14)
    done = set()
    for p in CACHE_BASE.glob(f"{dataset_prefix}_*.parquet"):
        try:
            df = pd.read_parquet(p, columns=["date"])
            if df.empty: continue
            last = pd.to_datetime(df["date"]).dt.date.max()
            if last >= threshold:
                done.add(p.stem.replace(f"{dataset_prefix}_", ""))
        except Exception:
            pass
    return done


def backfill_one(
    fc: FinMindClient,
    endpoint_key: str,
    universe: list[str],
    today: date,
    max_tickers: int = 0,
) -> None:
    cfg = ENDPOINTS[endpoint_key]
    method = getattr(fc, cfg["method"])
    done = get_done(cfg["dataset_prefix"])
    todo = [t for t in universe if t not in done]
    if max_tickers > 0:
        todo = todo[:max_tickers]

    print(f"\n{'=' * 70}")
    print(f"  ▶ {endpoint_key.upper()} — {cfg['description']}")
    print(f"  Universe {len(universe)} | Done {len(done)} | TODO {len(todo)}")
    print(f"{'=' * 70}")

    if not todo:
        print("  ✓ 全部已 cache，跳過")
        return

    t0 = time.time()
    ok = fail = empty = 0
    for i, tk in enumerate(todo, 1):
        try:
            df = method(tk, START, today)
            if df is not None and not df.empty:
                ok += 1
            else:
                empty += 1
        except Exception as e:
            fail += 1
            if i <= 3 or i % 200 == 0:
                msg = str(e)[:80]
                print(f"    [{i}] {tk} 失敗: {type(e).__name__}: {msg}")

        if i % 100 == 0 or i == len(todo):
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(todo) - i) / rate if rate > 0 else 0
            print(
                f"  [{i:>4}/{len(todo)}] ok={ok} empty={empty} fail={fail}  "
                f"elapsed={elapsed/60:.1f}m  ETA={eta/60:.1f}m  "
                f"rate={rate*60:.1f}/min"
            )

    print(f"\n  ✅ {endpoint_key}: ok={ok}, empty={empty}, fail={fail}, "
          f"耗時 {(time.time()-t0)/60:.1f} 分")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(ENDPOINTS.keys()),
                    help="只跑特定 endpoint（不指定則全跑）")
    ap.add_argument("--max-tickers", type=int, default=0,
                    help="0 = 全跑")
    args = ap.parse_args()

    print("=" * 70)
    print("  FinMind Master Backfill — 訂閱期內 cache 補強")
    print(f"  範圍: {START} → {date.today()}")
    print("=" * 70)

    fc = FinMindClient(
        token=os.environ.get("FINMIND_TOKEN", ""),
        cache_dir=ROOT / "data" / "cache" / "finmind",
    )
    universe = get_universe()
    if not universe:
        print("  ❌ Universe 為空（institutional cache 沒資料）")
        sys.exit(1)

    eps = [args.only] if args.only else list(ENDPOINTS.keys())
    print(f"\n  將跑 endpoints: {eps}")
    print(f"  Max tickers per endpoint: {args.max_tickers if args.max_tickers else 'ALL'}")

    overall_t0 = time.time()
    for ep in eps:
        backfill_one(fc, ep, universe, date.today(), args.max_tickers)

    print(f"\n{'=' * 70}")
    print(f"  🎯 總耗時: {(time.time()-overall_t0)/60:.1f} 分")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
