"""
FinMind Extras Backfill — 期貨/政府基金（CLAUDE.md 認可的 Sponsor 限定資料）

直接 raw fetch（不需要 client method 包裝，因為都是低頻、單檔/多檔）

抓取項目：
  1. TaiwanFuturesInstitutionalInvestors — TX/MTX/TE/TF 期貨法人未平倉
     ⭐ 外資期指多空 = 台股最強先行指標
  2. TaiwanFuturesDaily — 4 大期貨 daily OHLC
  3. TaiwanStockGovernmentBankBuySell — 八大行庫買賣超（底部訊號）

存放位置：data/cache/finmind/extras/{dataset}.parquet
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

OUT_DIR = ROOT / "data" / "cache" / "finmind" / "extras"
OUT_DIR.mkdir(parents=True, exist_ok=True)
API_URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
START = date(2018, 1, 1)
TODAY = date.today()


def fetch(dataset: str, data_id: str = "", start: date = START, end: date = TODAY) -> pd.DataFrame:
    params = {
        "dataset": dataset,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "token": TOKEN,
    }
    if data_id:
        params["data_id"] = data_id
    r = requests.get(API_URL, params=params, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != 200:
        raise RuntimeError(f"FinMind error [{j.get('status')}]: {j.get('msg')}")
    return pd.DataFrame(j.get("data", []))


# ─── 1. 期貨法人未平倉 ───
def backfill_futures_institutional():
    print("\n" + "=" * 70)
    print("  ▶ TaiwanFuturesInstitutionalInvestors (期貨法人未平倉)")
    print("=" * 70)

    futures_ids = ["TX", "MTX", "TE", "TF"]  # 台指/小台/電子/金融
    all_dfs = []
    t0 = time.time()
    for fid in futures_ids:
        try:
            df = fetch("TaiwanFuturesInstitutionalInvestors", fid)
            print(f"  {fid}: {len(df)} rows")
            if not df.empty:
                all_dfs.append(df)
        except Exception as e:
            print(f"  {fid}: 失敗 {e}")

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        out = OUT_DIR / "futures_institutional.parquet"
        combined.to_parquet(out, index=False)
        print(f"  ✅ 寫入 {out} ({len(combined)} rows, {(time.time()-t0):.1f}s)")


# ─── 2. 期貨日 OHLC ───
def backfill_futures_daily():
    print("\n" + "=" * 70)
    print("  ▶ TaiwanFuturesDaily (期貨日 OHLC)")
    print("=" * 70)

    futures_ids = ["TX", "MTX", "TE", "TF"]
    all_dfs = []
    t0 = time.time()
    for fid in futures_ids:
        try:
            df = fetch("TaiwanFuturesDaily", fid)
            print(f"  {fid}: {len(df)} rows")
            if not df.empty:
                all_dfs.append(df)
        except Exception as e:
            print(f"  {fid}: 失敗 {e}")

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        out = OUT_DIR / "futures_daily.parquet"
        combined.to_parquet(out, index=False)
        print(f"  ✅ 寫入 {out} ({len(combined)} rows, {(time.time()-t0):.1f}s)")


# ─── 3. 政府基金（八大行庫）─── 必須單日查詢
def backfill_government_bank():
    print("\n" + "=" * 70)
    print("  ▶ TaiwanStockGovernmentBankBuySell (政府基金/八大行庫)")
    print("  ⚠️ 單日查詢，5 年約 1260 reqs，預估 30-60 分鐘")
    print("=" * 70)

    out = OUT_DIR / "government_bank_buysell.parquet"
    # Resume：如果已有 cache，從最後日期 +1 開始
    existing = None
    cursor = START
    if out.exists():
        existing = pd.read_parquet(out)
        if not existing.empty and "date" in existing.columns:
            last_date = pd.to_datetime(existing["date"]).dt.date.max()
            cursor = last_date + timedelta(days=1)
            print(f"  Resume: 已有 cache 至 {last_date}，從 {cursor} 繼續")

    all_dfs = [existing] if existing is not None else []
    t0 = time.time()
    n_days = 0
    n_ok = 0
    n_empty = 0
    while cursor <= TODAY:
        # 跳過週末
        if cursor.weekday() >= 5:
            cursor += timedelta(days=1)
            continue
        try:
            df = fetch("TaiwanStockGovernmentBankBuySell", "", cursor, cursor)
            if not df.empty:
                all_dfs.append(df)
                n_ok += 1
            else:
                n_empty += 1
            n_days += 1
        except Exception as e:
            n_empty += 1
            if n_days < 3:
                print(f"  [{cursor}] 失敗: {str(e)[:80]}")
        cursor += timedelta(days=1)

        # 定期 flush（避免長跑遺失）
        if n_days % 100 == 0 and n_days > 0:
            if all_dfs:
                combined = pd.concat(all_dfs, ignore_index=True).drop_duplicates(subset=["date", "stock_id", "bank_name"])
                combined.to_parquet(out, index=False)
                elapsed = time.time() - t0
                rate = n_days / elapsed if elapsed > 0 else 0
                print(f"  [{cursor}] {n_days} 天 (ok={n_ok}, empty={n_empty}), {len(combined):,} rows, {rate*60:.0f}/min")

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True).drop_duplicates(subset=["date", "stock_id", "bank_name"])
        combined.to_parquet(out, index=False)
        print(f"  ✅ 完成: {n_days} 天, {len(combined):,} rows, {(time.time()-t0)/60:.1f} 分")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["futures_inst", "futures_daily", "gov_bank"],
                    help="只跑特定項目")
    args = ap.parse_args()

    print("=" * 70)
    print(f"  FinMind Extras Backfill — {START} ~ {TODAY}")
    print(f"  輸出: {OUT_DIR}")
    print("=" * 70)

    if args.only is None or args.only == "futures_inst":
        backfill_futures_institutional()
    if args.only is None or args.only == "futures_daily":
        backfill_futures_daily()
    if args.only is None or args.only == "gov_bank":
        backfill_government_bank()

    print("\n" + "=" * 70)
    print("  🎯 完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
