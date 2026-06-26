"""
背景補抓全市場法人買賣超資料。

目標：weekly EH trades 1853 unique tickers，每檔抓 2018-01 到今天。
預估時間：30-60 分鐘（取決於 FinMind 速率）。
"""
from __future__ import annotations

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

CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
START = date(2018, 1, 1)


def main() -> None:
    weekly = pd.read_csv(ROOT / "logs" / "early_hunter_weekly_v2.csv")
    tickers = sorted(weekly["ticker"].astype(str).unique().tolist())

    done = {
        p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
        for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet")
    }
    todo = [t for t in tickers if t not in done]
    print(f"目標 {len(tickers)} tickers, 已有 {len(done)}, 待抓 {len(todo)}")

    if not todo:
        print("全部已 cache，跳過")
        return

    fc = FinMindClient(
        token=os.environ.get("FINMIND_TOKEN", ""),
        cache_dir=CACHE.parent,
    )
    today = date.today()

    t0 = time.time()
    ok, fail = 0, 0
    for i, tk in enumerate(todo, 1):
        try:
            df = fc.get_institutional(tk, START, today)
            if df is not None and not df.empty:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            if i <= 3 or i % 100 == 0:
                print(f"    [{i}] {tk} 失敗: {e}")

        if i % 100 == 0 or i == len(todo):
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (len(todo) - i) / rate if rate > 0 else 0
            print(
                f"  [{i:>4}/{len(todo)}] ok={ok} fail={fail}  "
                f"elapsed={elapsed/60:.1f}m  ETA={eta/60:.1f}m"
            )

    print(f"\n完成：ok={ok}, fail={fail}, 總耗時 {(time.time()-t0)/60:.1f} 分")


if __name__ == "__main__":
    main()
