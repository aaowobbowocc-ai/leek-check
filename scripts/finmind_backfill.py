"""
FinMind Sponsor 資料一次性歷史全量下載。

在取消 Sponsor 訂閱前跑一次，把以下兩個 dataset 的歷史資料存進 cache：

  1. TaiwanStockHoldingSharesPer（大戶持股）
     - 對象：全 universe 2494 檔
     - 一次 API call 可拿全段歷史 → 速度快
     - 預估時間：40-80 分鐘

  2. TaiwanStockTradingDailyReport（分點籌碼）
     - 對象：Vol Anomaly 歷史觸發標的 + 目前已有 cache 的標的
     - 只抓最近 120 天（chip_concentration 用 4 週窗口，120 天已綽綽有餘）
     - 逐日逐 ticker 抓，比較慢
     - 預估時間：25-40 分鐘

資料存入 data/cache/finmind/finmind/ 後，之後可改成自爬 TDCC + TWSE BSR。
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

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

CACHE = ROOT / "data" / "cache" / "finmind"
BACKFILL_START = date(2017, 1, 1)
BROKER_LOOKBACK_DAYS = 120   # 分點籌碼只抓近 N 天


def load_universe() -> list[str]:
    path = ROOT / "config" / "universe_all.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return sorted(str(t) for t in raw.get("tickers", []))


def load_vol_anomaly_tickers() -> list[str]:
    csv = ROOT / "logs" / "vol_anomaly_backtest_2020-01-01_2024-12-31.csv"
    if not csv.exists():
        return []
    df = pd.read_csv(csv)
    return sorted(df["ticker"].astype(str).unique().tolist())


def already_cached_broker_tickers() -> list[str]:
    return [
        p.stem.replace("TaiwanStockTradingDailyReport_", "")
        for p in CACHE.glob("finmind/TaiwanStockTradingDailyReport_*.parquet")
    ]


def backfill_holding_shares_per(client: FinMindClient, tickers: list[str]) -> None:
    """大戶持股全量下載。"""
    done_tickers = {
        p.stem.replace("TaiwanStockHoldingSharesPer_", "")
        for p in CACHE.glob("finmind/TaiwanStockHoldingSharesPer_*.parquet")
    }
    todo = [t for t in tickers if t not in done_tickers]
    total = len(tickers)
    print(f"\n[HoldingSharesPer] 全量下載")
    print(f"  總計 {total} 檔，已有 {len(done_tickers)} 檔 cache，待抓 {len(todo)} 檔")

    if not todo:
        print("  全部已有 cache，跳過。")
        return

    t0 = time.time()
    ok, fail = 0, 0
    for i, tk in enumerate(todo, 1):
        try:
            df = client.get_holding_shares_per(tk, BACKFILL_START, date.today())
            if df is not None and not df.empty:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            if i <= 5 or i % 200 == 0:
                print(f"    [{i}] {tk} 失敗: {e}")

        if i % 100 == 0 or i == len(todo):
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (len(todo) - i) / rate if rate > 0 else 0
            print(
                f"  [{i:>4}/{len(todo)}] ok={ok} fail={fail} "
                f"elapsed={elapsed/60:.1f}m ETA={eta/60:.1f}m"
            )

    print(f"  完成：ok={ok} fail={fail}，耗時 {(time.time()-t0)/60:.1f} 分鐘")


def backfill_broker_distribution(client: FinMindClient, tickers: list[str]) -> None:
    """分點籌碼近 N 天下載（逐日，速度較慢）。"""
    end = date.today()
    start = end - timedelta(days=BROKER_LOOKBACK_DAYS)
    print(f"\n[TradingDailyReport] 近 {BROKER_LOOKBACK_DAYS} 天下載（{start} ~ {end}）")
    print(f"  對象：{len(tickers)} 個 ticker")

    t0 = time.time()
    ok, fail = 0, 0
    for i, tk in enumerate(tickers, 1):
        try:
            df = client.get_broker_distribution(tk, start, end)
            if df is not None and not df.empty:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"    [{i}] {tk} 失敗: {e}")

        elapsed = time.time() - t0
        if i % 10 == 0 or i == len(tickers):
            rate = i / elapsed if elapsed > 0 else 1
            eta = (len(tickers) - i) / rate
            print(
                f"  [{i:>3}/{len(tickers)}] ok={ok} fail={fail} "
                f"elapsed={elapsed/60:.1f}m ETA={eta/60:.1f}m"
            )

    print(f"  完成：ok={ok} fail={fail}，耗時 {(time.time()-t0)/60:.1f} 分鐘")


def main() -> None:
    token = os.getenv("FINMIND_TOKEN") or os.getenv("FINMIND_API_KEY") or ""
    if not token:
        print("❌ 找不到 FINMIND_TOKEN，請確認 config/.env")
        return

    client = FinMindClient(token=token, cache_dir=CACHE)

    # ── 準備 ticker 清單 ──
    universe = load_universe()
    va_tickers = load_vol_anomaly_tickers()
    cached_broker = already_cached_broker_tickers()

    # 分點籌碼：Vol Anomaly 觸發 + 已有 cache 的（更新即可）
    broker_priority = sorted(set(va_tickers) | set(cached_broker))

    print("=" * 60)
    print("FinMind Sponsor 歷史資料全量下載")
    print("=" * 60)
    print(f"Universe:              {len(universe)} 檔")
    print(f"分點籌碼目標:          {len(broker_priority)} 檔")
    print(f"  (Vol Anomaly: {len(va_tickers)} + 已有 cache: {len(cached_broker)})")

    # ── Part 1：大戶持股（全量） ──
    backfill_holding_shares_per(client, universe)

    # ── Part 2：分點籌碼（優先清單） ──
    backfill_broker_distribution(client, broker_priority)

    print("\n✅ 全部完成。可以取消 Sponsor 訂閱。")
    print("   之後大戶持股改從 TDCC 自爬，分點籌碼改從 TWSE BSR 自爬。")


if __name__ == "__main__":
    main()
