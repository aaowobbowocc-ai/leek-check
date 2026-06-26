"""
TWSE T86 即時法人買賣超抓取（不用 FinMind，直接 TWSE 公開 API）

優勢：
  - 收盤後 1-2h 即可抓（vs FinMind T+1 早上）
  - 完全免費
  - 含上市 + 上櫃所有股票

用法：
  python scripts/twse_t86_realtime.py [--date 20260504] [--ticker 2408]
"""
from __future__ import annotations
import io, sys, time
import argparse
import pandas as pd
import requests
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = ROOT / "data" / "cache" / "finmind" / "extras"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_twse_t86(d: date) -> pd.DataFrame:
    """上市公司 T86 法人買賣超"""
    url = "https://www.twse.com.tw/rwd/zh/fund/T86"
    params = {"date": d.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"}
    r = requests.get(url, params=params, timeout=15, headers=HEADERS)
    j = r.json()
    if j.get("stat") != "OK":
        return pd.DataFrame()
    fields = j["fields"]
    data = j["data"]
    df = pd.DataFrame(data, columns=fields)
    # 整理欄位
    df["date"] = d.isoformat()
    df["stock_id"] = df["證券代號"].str.strip()
    df["stock_name"] = df["證券名稱"].str.strip()
    # 三大法人 cleanup
    for col in df.columns:
        if "股數" in col or "買賣超" in col:
            df[col] = df[col].str.replace(",", "", regex=False).astype(float)
    df["foreign_net"] = df["外陸資買賣超股數(不含外資自營商)"]
    df["trust_net"] = df["投信買賣超股數"]
    df["dealer_net"] = df["自營商買賣超股數"]
    df["total_3_inst_net"] = df["三大法人買賣超股數"]
    df["market"] = "TWSE"
    return df[["date", "stock_id", "stock_name", "market",
               "foreign_net", "trust_net", "dealer_net", "total_3_inst_net"]]


def fetch_otc_3insti(d: date) -> pd.DataFrame:
    """上櫃法人買賣超（OTC）"""
    url = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
    params = {"l": "zh-tw", "se": "EW", "t": "D", "d": d.strftime("%Y/%m/%d"), "o": "json"}
    try:
        r = requests.get(url, params=params, timeout=15, headers=HEADERS)
        j = r.json()
        if not j.get("aaData"): return pd.DataFrame()
        # OTC 欄位較複雜，簡化提取核心
        rows = []
        for row in j["aaData"]:
            try:
                rows.append({
                    "date": d.isoformat(),
                    "stock_id": row[0].strip(),
                    "stock_name": row[1].strip(),
                    "market": "OTC",
                    "foreign_net": float(row[10].replace(",", "")) if row[10] else 0,
                    "trust_net": float(row[13].replace(",", "")) if row[13] else 0,
                    "dealer_net": float(row[16].replace(",", "")) if row[16] else 0,
                    "total_3_inst_net": float(row[22].replace(",", "")) if row[22] else 0,
                })
            except Exception: continue
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  OTC fetch failed: {e}")
        return pd.DataFrame()


CACHE_DIR = ROOT / "data" / "cache" / "twse_t86"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_and_save(d: date) -> int:
    """抓 + 存 cache，回傳 rows 數"""
    cache_file = CACHE_DIR / f"{d.strftime('%Y%m%d')}.parquet"
    if cache_file.exists():
        existing = pd.read_parquet(cache_file)
        if len(existing) > 0:
            return len(existing)  # 已有

    twse = fetch_twse_t86(d)
    time.sleep(1)
    otc = fetch_otc_3insti(d)

    if twse.empty and otc.empty:
        return 0

    df = pd.concat([twse, otc], ignore_index=True)
    df.to_parquet(cache_file, index=False)
    return len(df)


def get_latest_inst(tk: str, days_back: int = 7) -> pd.DataFrame:
    """從 cache 取最近 N 日該 ticker 法人資料"""
    today_d = date.today()
    rows = []
    for i in range(days_back):
        d = today_d - timedelta(days=i)
        f = CACHE_DIR / f"{d.strftime('%Y%m%d')}.parquet"
        if f.exists():
            df = pd.read_parquet(f)
            sub = df[df["stock_id"] == tk]
            if not sub.empty:
                rows.append(sub)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True).sort_values("date")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD")
    ap.add_argument("--ticker", help="特定股票（查 + 顯示）")
    ap.add_argument("--save", action="store_true", help="儲存到 cache")
    ap.add_argument("--backfill", type=int, help="從今天往回抓 N 天（自動 save）")
    args = ap.parse_args()

    # Backfill 模式：抓多天
    if args.backfill:
        today_d = date.today()
        print(f"=== Backfill TWSE T86 最近 {args.backfill} 天 ===\n")
        for i in range(args.backfill):
            d = today_d - timedelta(days=i)
            if d.weekday() >= 5: continue  # 跳週末
            try:
                n = fetch_and_save(d)
                print(f"  {d}: {n} rows {'✓' if n else '⚠️ no data'}")
                time.sleep(1.5)
            except Exception as e:
                print(f"  {d}: 失敗 {str(e)[:60]}")
        return

    # 單日模式
    if args.date:
        target_d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
    else:
        target_d = date.today()

    print(f"=== TWSE/OTC T86 法人買賣超 — {target_d} ===\n")

    print("Fetching TWSE...")
    twse = fetch_twse_t86(target_d)
    print(f"  TWSE: {len(twse)} rows")

    print("Fetching OTC...")
    time.sleep(1)
    otc = fetch_otc_3insti(target_d)
    print(f"  OTC: {len(otc)} rows")

    if twse.empty and otc.empty:
        print(f"\n⚠️ {target_d} 沒有資料（盤後資料通常 18-21 點公告）")
        return

    df = pd.concat([twse, otc], ignore_index=True)
    print(f"\nTotal: {len(df)} rows")

    if args.ticker:
        sub = df[df["stock_id"] == args.ticker]
        if sub.empty:
            print(f"  {args.ticker} 沒有資料")
        else:
            print(f"\n=== {args.ticker} ===")
            row = sub.iloc[0]
            for c in df.columns:
                v = row[c]
                if isinstance(v, float):
                    v = f"{v:+,.0f}" if v != 0 else "0"
                print(f"  {c}: {v}")

    if args.save:
        out = CACHE_DIR / f"{target_d.strftime('%Y%m%d')}.parquet"
        df.to_parquet(out, index=False)
        print(f"\n✅ Saved to {out}")


if __name__ == "__main__":
    main()
