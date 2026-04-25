"""
重新抓取 universe 中沒 OHLCV 快取的個股（多為上櫃 .TWO 股票）。

背景：
  原本 _yf_download 只試 .TW，導致所有上櫃股漏抓
  Fix 後（adr_fetcher.py）會 fallback .TWO
  這個腳本批次補上所有缺漏

只跑 yfinance（OHLCV），不碰 FinMind。
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.adr_fetcher import get_tw_ohlcv_adjusted

UNIVERSE = ROOT / "config" / "universe_all.yaml"
CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def main() -> None:
    raw = yaml.safe_load(UNIVERSE.read_text(encoding="utf-8"))
    tickers = sorted(raw.get("tickers", []))

    missing = [t for t in tickers if not (CACHE / f"{t}.parquet").exists()]
    print(f"Universe {len(tickers)} 檔，缺 OHLCV {len(missing)} 檔")

    success = 0
    fail = 0
    t0 = time.time()
    for i, tk in enumerate(missing, 1):
        try:
            df = get_tw_ohlcv_adjusted(tk, date(2018, 1, 1), date.today())
            if not df.empty:
                success += 1
            else:
                fail += 1
        except Exception:
            fail += 1
        if i % 50 == 0 or i == len(missing):
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(missing) - i) / rate if rate > 0 else 0
            print(
                f"  [{i}/{len(missing)}] {tk} ok={success} fail={fail} "
                f"rate={rate:.1f}/s ETA={eta/60:.1f}min",
                flush=True,
            )

    elapsed = time.time() - t0
    print(f"\n=== 完成（{elapsed/60:.1f} 分鐘）===")
    print(f"  成功補抓: {success} 檔")
    print(f"  仍失敗: {fail} 檔（真下市或 yfinance 沒收錄）")


if __name__ == "__main__":
    main()
