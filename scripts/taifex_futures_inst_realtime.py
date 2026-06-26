"""
TAIFEX 期貨法人未平倉即時抓取（完全免費，不用 FinMind sponsor）

來源：期交所 https://www.taifex.com.tw/cht/3/futContractsDate
更新時間：每日 15:00 後
資料：三大法人 × 4 期貨（TX/MTX/TE/TF）每日交易 + 未平倉

用法：
  python scripts/taifex_futures_inst_realtime.py [--date 20260430] [--save]
  python scripts/taifex_futures_inst_realtime.py --backfill 7
"""
from __future__ import annotations
import io, sys, time
import argparse
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import requests
from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "taifex_inst"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_taifex_inst(d: date) -> pd.DataFrame:
    """抓某日期貨三大法人未平倉

    Returns: date, futures_id, institutional, long_oi, short_oi, net_oi

    注意：期交所對非交易日 / 未公告日，會 fallback 回最近交易日資料。
    本 function 透過比對 response 中顯示的「資料日期」確認，避免存錯日期。
    """
    url = "https://www.taifex.com.tw/cht/3/futContractsDate"
    params = {
        "queryStartDate": d.strftime("%Y/%m/%d"),
        "queryEndDate": d.strftime("%Y/%m/%d"),
    }
    r = requests.post(url, data=params, timeout=20, headers=HEADERS)
    if r.status_code != 200: return pd.DataFrame()

    # 檢查 response 中的「資料日期」是否符合 query date
    import re
    # 期交所 page header 通常會顯示 "日期 yyyy/mm/dd 至 yyyy/mm/dd"
    actual_dates = re.findall(r'\b(\d{4})/(\d{2})/(\d{2})\b', r.text)
    found_match = False
    for y, m, day in actual_dates:
        try:
            page_d = date(int(y), int(m), int(day))
            if page_d == d:
                found_match = True
                break
        except: pass
    if not found_match:
        # response 中沒有 query date → 期交所沒當日資料，回 fallback
        return pd.DataFrame()

    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.find_all("table")
    rows = []
    futures_map = {
        "臺股期貨": "TX", "小型臺指期貨": "MTX",
        "電子期貨": "TE", "金融期貨": "TF",
    }
    inst_map = {"自營商": "Dealer", "投信": "Investment_Trust", "外資": "Foreign_Investor"}

    for t in tables:
        text = t.get_text()
        if "臺股期貨" not in text:
            continue
        trs = t.find_all("tr")
        cur_futures = None
        for tr in trs:
            tds = tr.find_all("td")
            if len(tds) < 12: continue
            cells = [td.get_text(strip=True).replace(",", "") for td in tds]

            # 偵測 futures name
            for fname, fid in futures_map.items():
                if any(fname in c for c in cells[:3]):
                    cur_futures = fid
                    break

            # 偵測身份別
            inst = None
            for c in cells[:3]:
                for iname, iid in inst_map.items():
                    if iname in c:
                        inst = iid; break
                if inst: break
            if not cur_futures or not inst: continue

            # cells 結構（13 vs 15 看是否包含序號+商品名）：
            # 15: [序號, 商品名, 身份別, 多口, 多額, 空口, 空額, 淨口, 淨額,
            #      多OI口, 多OI額, 空OI口, 空OI額, 淨OI口, 淨OI額]
            # 13: [身份別, 多口, 多額, 空口, 空額, 淨口, 淨額,
            #      多OI口, 多OI額, 空OI口, 空OI額, 淨OI口, 淨OI額]
            try:
                if len(cells) >= 15:
                    long_oi = int(cells[9]); short_oi = int(cells[11]); net_oi = int(cells[13])
                else:
                    long_oi = int(cells[7]); short_oi = int(cells[9]); net_oi = int(cells[11])
                rows.append({
                    "date": d.isoformat(),
                    "futures_id": cur_futures,
                    "institutional": inst,
                    "long_oi": long_oi,
                    "short_oi": short_oi,
                    "net_oi": net_oi,
                })
            except (ValueError, IndexError): continue
        if rows: break  # 只取第一個含臺股期貨的 table
    return pd.DataFrame(rows).drop_duplicates(subset=["date","futures_id","institutional"])


def fetch_and_save(d: date) -> int:
    cache_file = CACHE_DIR / f"{d.strftime('%Y%m%d')}.parquet"
    if cache_file.exists():
        existing = pd.read_parquet(cache_file)
        if not existing.empty: return len(existing)
    df = fetch_taifex_inst(d)
    if df.empty: return 0
    df.to_parquet(cache_file, index=False)
    return len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD")
    ap.add_argument("--backfill", type=int, help="N 天 backfill")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    if args.backfill:
        today_d = date.today()
        print(f"=== TAIFEX 期貨法人 backfill {args.backfill} 天 ===")
        for i in range(args.backfill):
            d = today_d - timedelta(days=i)
            if d.weekday() >= 5: continue
            try:
                n = fetch_and_save(d)
                print(f"  {d}: {n} rows {'✓' if n else '⚠️'}")
                time.sleep(1.5)
            except Exception as e:
                print(f"  {d}: 失敗 {str(e)[:80]}")
        return

    if args.date:
        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
    else:
        d = date.today()
    print(f"=== TAIFEX 期貨法人 — {d} ===")
    df = fetch_taifex_inst(d)
    if df.empty:
        print(f"⚠️ {d} 沒有資料")
        return
    print(df.to_string(index=False))
    if args.save:
        out = CACHE_DIR / f"{d.strftime('%Y%m%d')}.parquet"
        df.to_parquet(out, index=False)
        print(f"\n✅ Saved to {out}")


if __name__ == "__main__":
    main()
