"""TWSE 除權除息預告表 scraper — FinMind 替代品 (5/20 後用).

Endpoint: https://www.twse.com.tw/exchangeReport/TWT48U
- 公開資料,無需 token
- 返回 JSON,date_str 為民國年 (115年05月19日 = 2026-05-19)
- 涵蓋全 TWSE 股票 + ETF 未來除權除息預告

寫入 data/cache/twse/exdiv_schedule.parquet
- 每日 17:30 跑(已加進 daily_data_update.bat)
- 抓未來 60 天

cols: ex_date | stock_id | name | type | cash_div | str_year | str_date_raw

執行:
  python scripts/fetch_twse_exdiv.py
  python scripts/fetch_twse_exdiv.py --days 90  # 抓未來 90 天
"""
from __future__ import annotations
import sys, io, argparse, time, json
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from pathlib import Path
from datetime import date, timedelta
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "twse"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT = CACHE_DIR / "exdiv_schedule.parquet"


def roc_to_iso(roc_str: str) -> str:
    """Convert '115年05月19日' → '2026-05-19'."""
    s = roc_str.replace("年", "-").replace("月", "-").replace("日", "")
    parts = s.split("-")
    if len(parts) != 3:
        return ""
    yr = int(parts[0]) + 1911
    return f"{yr:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def parse_cash(s: str) -> float:
    """'7.00' → 7.0; '待公告實際收益分配金額' → 0.0."""
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def fetch_window(start: date, end: date) -> pd.DataFrame:
    url = "https://www.twse.com.tw/exchangeReport/TWT48U"
    params = {
        "response": "json",
        "strDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; INVEST-bot/1.0)",
    }
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    payload = resp.json()
    if payload.get("stat") != "OK":
        raise RuntimeError(f"TWSE TWT48U status: {payload.get('stat')}")
    rows = payload.get("data", []) or []
    out = []
    for r in rows:
        if len(r) < 9:
            continue
        ex_date_iso = roc_to_iso(r[0])
        if not ex_date_iso:
            continue
        out.append({
            "ex_date": ex_date_iso,
            "stock_id": str(r[1]).strip(),
            "name": str(r[2]).strip(),
            "type": str(r[3]).strip(),     # 息 / 權 / 權息
            "cash_div": parse_cash(r[7]),
            "str_year": int(ex_date_iso[:4]),
            "str_date_raw": r[0],
        })
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="抓未來 N 天 (default 60)")
    args = ap.parse_args()

    today = date.today()
    end = today + timedelta(days=args.days)
    print(f"Fetching TWSE 除權息預告 {today} ~ {end} ...")

    try:
        df = fetch_window(today, end)
    except Exception as e:
        print(f"❌ TWSE fetch failed: {e}")
        return 1

    if df.empty:
        print("⚠️ 0 rows")
        return 0

    df = df.sort_values(["ex_date", "stock_id"]).reset_index(drop=True)
    df.to_parquet(OUT, index=False)

    n_etf = df[df["stock_id"].str.startswith("00")].shape[0]
    n_stk = df.shape[0] - n_etf
    print(f"✅ {len(df)} rows ({n_etf} ETF + {n_stk} 個股) → {OUT.relative_to(ROOT)}")
    print(f"\n未來 30 天 ETF events:")
    horizon = (today + timedelta(days=30)).isoformat()
    show = df[(df["stock_id"].str.startswith("00")) & (df["ex_date"] <= horizon)].head(20)
    if not show.empty:
        print(show[["ex_date", "stock_id", "name", "cash_div", "type"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
