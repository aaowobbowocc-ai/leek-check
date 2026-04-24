"""
Paper Trading 對帳 — 讀 data/paper_trades/YYYY-MM-DD.json，用後續真實日線
重算每筆推薦的模擬結果，輸出 ledger CSV + 統計。

用法：
    python scripts/paper_check.py                 # 對帳到今天
    python scripts/paper_check.py --as-of 2026-05-15

設計：
  - 每次執行都重新 reconcile（冪等），避免手動維護 open/closed 狀態的錯誤
  - 輸出 data/paper_trades/ledger.csv（給 Excel 打開）+ 終端統計
  - 不寫入、不修改任何「真相來源」（YYYY-MM-DD.json）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

from src.data.adr_fetcher import get_tw_ohlcv_adjusted
from src.portfolio.paper_tracker import reconcile, summarize

STATE_DIR = ROOT / "data" / "paper_trades"
CACHE_DIR = ROOT / "data" / "cache" / "yfinance"


def _ohlcv_fetcher(ticker: str, start: date, end: date) -> pd.DataFrame:
    return get_tw_ohlcv_adjusted(ticker, start, end, cache_dir=CACHE_DIR)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=str, help="對帳日期 YYYY-MM-DD，預設 today")
    ap.add_argument("--max-hold-days", type=int, default=20)
    args = ap.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    if not STATE_DIR.exists() or not any(STATE_DIR.glob("????-??-??.json")):
        print(f"尚無任何晨報快照於 {STATE_DIR}，先跑幾天 morning_briefing.py 再來對帳。")
        return

    print(f"對帳日期：{as_of}")
    trades = reconcile(
        state_dir=STATE_DIR,
        ohlcv_fetcher=_ohlcv_fetcher,
        as_of=as_of,
        max_hold_days=args.max_hold_days,
    )

    if not trades:
        print("快照中尚無可驗證的推薦（可能全部都在未來）。")
        return

    # 寫 CSV
    df = pd.DataFrame([t.__dict__ for t in trades])
    csv_path = STATE_DIR / "ledger.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    s = summarize(trades)
    print(f"\n總筆數：{s.total}（已平倉 {s.closed} / 持倉 {s.open} / 等待進場 {s.pending} / 已失效 {s.expired}）")
    if s.closed:
        print(f"勝率：{s.win_rate:.2%}  |  盈虧比：{s.pl_ratio:.2f}  |  期望值：{s.expectancy_pct:+.2f}%")
        print(f"平均獲利：{s.avg_win_pct:+.2f}%  |  平均虧損：{s.avg_loss_pct:-.2f}%")
    if s.closed < 20:
        print(f"\n💡 距離 Phase 10 驗收（≥20 筆已平倉）還需 {20 - s.closed} 筆。")
    else:
        print(f"\n✅ 已累積 ≥20 筆模擬交易，Phase 10 驗收條件達標。")
    print(f"\nLedger CSV：{csv_path}")


if __name__ == "__main__":
    main()
