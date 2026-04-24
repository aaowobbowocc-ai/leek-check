"""
批次抓取全市場 universe 的 Phase 16 所需資料（OHLCV + PER + 財報 + 營收）。

使用 FinMind Sponsor token 的高額度跑一次性歷史資料回填。
之後日常增量補抓由各 client 的 parquet 快取處理。

用法：
  python scripts/bulk_fetch_universe.py --start 2019-01-01 --end 2026-04-25
  python scripts/bulk_fetch_universe.py --limit 100   # 先測試前 100 檔

設計：
  - 讀 config/universe_all.yaml 拿 2494 檔 ticker 清單
  - 對每檔依序抓 4 種資料，每種資料的 FinMindClient 自動走 parquet 快取
  - Resumable：重跑會自動跳過已快取且新鮮的
  - Progress log：每 50 檔印一次進度 + 失敗統計
  - 若 FinMind 失敗（403 / 429）→ 指數退避重試 3 次
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

from src.data.adr_fetcher import get_tw_ohlcv_adjusted
from src.data.finmind_client import FinMindClient

UNIVERSE_PATH = ROOT / "config" / "universe_all.yaml"
CACHE_YF = ROOT / "data" / "cache" / "yfinance"
LOGS_DIR = ROOT / "logs"


def load_universe(limit: int | None = None) -> list[str]:
    raw = yaml.safe_load(UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers = sorted(raw.get("tickers", []))
    if limit is not None:
        tickers = tickers[:limit]
    return tickers


def with_retry(fn, label: str, max_retries: int = 3) -> pd.DataFrame:
    """FinMind 偶爾 429/403，指數退避重試。"""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"      [FAIL] {label}: {e}", flush=True)
                return pd.DataFrame()
            backoff = 2 ** attempt
            print(f"      [retry {attempt + 1}] {label}: {e} — sleep {backoff}s", flush=True)
            time.sleep(backoff)
    return pd.DataFrame()


def fetch_ticker_all(
    finmind: FinMindClient,
    ticker: str,
    start: date,
    end: date,
    skip_ohlcv: bool = False,
) -> dict[str, int]:
    """對單一 ticker 抓 4 種資料。回傳各資料集的筆數。"""
    stats = {"ohlcv": 0, "per_pbr": 0, "financials": 0, "revenue": 0}

    if not skip_ohlcv:
        df = with_retry(
            lambda: get_tw_ohlcv_adjusted(ticker, start, end, cache_dir=CACHE_YF),
            f"{ticker} OHLCV",
        )
        stats["ohlcv"] = len(df)

    df = with_retry(lambda: finmind.get_per_pbr(ticker, start, end), f"{ticker} PER")
    stats["per_pbr"] = len(df)

    # 月營收 YoY 需要多抓 1 年 buffer 才能計算
    rev_start = date(start.year - 1, start.month, 1)
    df = with_retry(
        lambda: finmind.get_monthly_revenue(ticker, rev_start, end),
        f"{ticker} Revenue",
    )
    stats["revenue"] = len(df)

    # 財報：多抓 2 年 buffer（TTM ROE 要 4 季）
    fin_start = date(start.year - 2, start.month, 1)
    df = with_retry(
        lambda: finmind.get_financial_statements(ticker, fin_start, end),
        f"{ticker} Financials",
    )
    stats["financials"] = len(df)

    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=str, default="2019-01-01")
    ap.add_argument("--end", type=str, default=date.today().isoformat())
    ap.add_argument("--limit", type=int, default=None,
                    help="只抓前 N 檔（測試用，省時間）")
    ap.add_argument("--skip-ohlcv", action="store_true",
                    help="略過 yfinance OHLCV（若已另外抓好）")
    ap.add_argument("--resume-from", type=str, default=None,
                    help="從這個 ticker 開始（resume 用）")
    args = ap.parse_args()

    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("[ERROR] FINMIND_TOKEN 未設定")
        sys.exit(1)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    tickers = load_universe(limit=args.limit)

    if args.resume_from:
        if args.resume_from in tickers:
            idx = tickers.index(args.resume_from)
            tickers = tickers[idx:]
            print(f"[RESUME] 從 {args.resume_from} 開始（第 {idx + 1} 檔）")

    print(f"[BULK FETCH] {len(tickers)} 檔 × 4 資料集 × {args.start} ~ {args.end}")
    print(f"             FinMind Sponsor 約 6-10k req/hr，估 {len(tickers) * 3 / 60:.1f} 分鐘")

    finmind = FinMindClient(token=token)
    LOGS_DIR.mkdir(exist_ok=True)

    t0 = time.time()
    totals = {"ohlcv": 0, "per_pbr": 0, "financials": 0, "revenue": 0}
    failures: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        try:
            stats = fetch_ticker_all(finmind, ticker, start, end, skip_ohlcv=args.skip_ohlcv)
            for k, v in stats.items():
                totals[k] += v
            # 若四項都是 0 → 記錄為失敗
            if sum(stats.values()) == 0:
                failures.append(ticker)
        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {ticker} UNEXPECTED: {e}", flush=True)
            failures.append(ticker)
            continue

        if i % 50 == 0 or i == len(tickers):
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(tickers) - i) / rate if rate > 0 else 0
            print(
                f"  [{i}/{len(tickers)}] {ticker} "
                f"(ohlcv={totals['ohlcv']}, per={totals['per_pbr']}, "
                f"fin={totals['financials']}, rev={totals['revenue']}) "
                f"rate={rate:.1f}/s ETA={eta / 60:.1f}min",
                flush=True,
            )

    elapsed = time.time() - t0
    print(f"\n=== 抓取完成（{elapsed / 60:.1f} 分鐘） ===")
    for k, v in totals.items():
        print(f"  {k}: {v:,} 筆")
    print(f"  失敗: {len(failures)} 檔")
    if failures:
        fail_log = LOGS_DIR / "bulk_fetch_failures.txt"
        fail_log.write_text("\n".join(failures), encoding="utf-8")
        print(f"  失敗清單: {fail_log}")


if __name__ == "__main__":
    main()
