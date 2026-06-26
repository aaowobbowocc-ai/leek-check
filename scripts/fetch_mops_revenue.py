"""MOPS 月營收爬蟲 — FinMind TaiwanStockMonthRevenue 替代

MOPS = 公開資訊觀測站 (Market Observation Post System).
Monthly revenue announced every month around 10th by 21:30.

URL: POST https://mopsov.twse.com.tw/server-java/FileDownLoad
  form: step=9, functionName=show_file2, filePath=/t21/sii/,
        fileName=t21sc03_{民國年}_{月}.csv

Encoding: utf-8-sig (BOM-prefixed UTF-8)

Output: data/cache/mops/revenue_YYYY_MM.parquet (per-month)
        + data/cache/mops/revenue_combined.parquet

Schema (matches FinMind TaiwanStockMonthRevenue):
  date, stock_id, country, revenue, revenue_month, revenue_year
  (date = announcement date in YYYY-MM-DD ISO format)

Run:
  python -m scripts.fetch_mops_revenue                          # latest month
  python -m scripts.fetch_mops_revenue --year 2026 --month 4   # specific
  python -m scripts.fetch_mops_revenue --backfill 12           # last 12 months
"""
from __future__ import annotations
import argparse, io, time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "cache" / "mops"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MOPS_DOWNLOAD = "https://mopsov.twse.com.tw/server-java/FileDownLoad"
# 上市 (sii), 上櫃 (otc), 興櫃 (rotc), 公發 (pub)
MOPS_SOURCES = [
    ("/t21/sii/", "上市"),
    ("/t21/otc/", "上櫃"),
]


def fetch_mops_csv(year_minguo: int, month: int, file_path: str) -> pd.DataFrame:
    """Fetch one month's CSV from MOPS. year_minguo = 西元 - 1911."""
    filename = f"t21sc03_{year_minguo}_{month}.csv"
    data = {
        "step":         "9",
        "functionName": "show_file2",
        "filePath":     file_path,
        "fileName":     filename,
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer":    f"https://mopsov.twse.com.tw/nas/t21{file_path[2:]}t21sc03_{year_minguo}_{month}_0.html",
    }
    try:
        r = requests.post(MOPS_DOWNLOAD, data=data, headers=headers, timeout=30)
    except Exception as e:
        print(f"    fetch fail {year_minguo}/{month} {file_path}: {e!r}")
        return pd.DataFrame()
    if r.status_code != 200:
        return pd.DataFrame()
    text = r.content.decode("utf-8-sig", errors="ignore")
    if "公司代號" not in text:
        return pd.DataFrame()
    try:
        df = pd.read_csv(io.StringIO(text), thousands=",")
    except Exception as e:
        print(f"    parse fail {year_minguo}/{month}: {e!r}")
        return pd.DataFrame()
    df.columns = [c.strip() for c in df.columns]
    return df


def normalize(raw: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    """Normalize MOPS columns to FinMind schema."""
    if raw.empty:
        return raw
    # Find columns
    col_id   = next((c for c in raw.columns if "公司代號" in c), None)
    col_name = next((c for c in raw.columns if "公司名稱" in c), None)
    col_rev  = next((c for c in raw.columns if "當月營收" in c and "去年" not in c
                     and "上月" not in c and "累計" not in c), None)
    col_date = next((c for c in raw.columns if "出表日期" in c), None)
    if not col_id or not col_rev:
        return pd.DataFrame()

    df = raw.copy()
    df["stock_id"] = df[col_id].astype(str).str.strip()
    df = df[df["stock_id"].str.match(r"^\d{4}\w?$")]

    # MOPS revenue is in 千元; FinMind stores in 元
    df["revenue"] = pd.to_numeric(df[col_rev], errors="coerce") * 1000
    df = df.dropna(subset=["revenue"])

    # Date convention: match FinMind = "1st of month following revenue month".
    # MOPS's 出表日期 is the query render time (always today), not actual
    # filing date, so it's not useful. Use synthesized 1st-of-next-month.
    next_m = month + 1
    next_y = year
    if next_m > 12:
        next_m = 1
        next_y += 1
    df["date"] = f"{next_y:04d}-{next_m:02d}-01"
    df["country"] = "Taiwan"
    df["revenue_month"] = month
    df["revenue_year"] = year

    if col_name:
        df["company_name"] = df[col_name].astype(str).str.strip()

    return df[["date", "stock_id", "country", "revenue", "revenue_month", "revenue_year"]]


def fetch_month(year: int, month: int) -> pd.DataFrame:
    """Fetch 上市 + 上櫃 monthly revenue and combine."""
    year_minguo = year - 1911
    print(f"  fetching {year}/{month:02d} (民國 {year_minguo}/{month})...")
    parts = []
    for path, label in MOPS_SOURCES:
        raw = fetch_mops_csv(year_minguo, month, path)
        if raw.empty:
            print(f"    {label}: no data")
            continue
        norm = normalize(raw, year, month)
        if not norm.empty:
            parts.append(norm)
            print(f"    {label}: {len(norm)} stocks")
        time.sleep(0.5)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def merge_into_combined() -> Path:
    files = sorted(OUT_DIR.glob("revenue_*.parquet"))
    files = [f for f in files if "combined" not in f.name]
    if not files:
        return None
    dfs = [pd.read_parquet(f) for f in files]
    combined = pd.concat(dfs, ignore_index=True).drop_duplicates(
        subset=["stock_id", "revenue_year", "revenue_month"], keep="last"
    ).sort_values(["stock_id", "revenue_year", "revenue_month"])
    out = OUT_DIR / "revenue_combined.parquet"
    combined.to_parquet(out, index=False)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--backfill", type=int, default=0)
    args = parser.parse_args()

    targets = []
    if args.year and args.month:
        targets.append((args.year, args.month))
    elif args.backfill > 0:
        # Walk back N months from current
        today = date.today()
        y, m = today.year, today.month - 1   # last completed month
        if m < 1:
            m = 12
            y -= 1
        for _ in range(args.backfill):
            targets.append((y, m))
            m -= 1
            if m < 1:
                m = 12
                y -= 1
    else:
        # Default: latest completed month (current month - 1)
        today = date.today()
        m = today.month - 1
        y = today.year
        if m < 1:
            m = 12
            y -= 1
        targets.append((y, m))

    print(f"Fetching {len(targets)} month(s) from MOPS")
    saved = 0
    for (y, m) in targets:
        out_path = OUT_DIR / f"revenue_{y}_{m:02d}.parquet"
        if out_path.exists():
            print(f"  {y}/{m:02d}: cache hit, skip")
            saved += 1
            continue
        df = fetch_month(y, m)
        if not df.empty:
            df.to_parquet(out_path, index=False)
            saved += 1
            print(f"    saved {len(df)} rows -> {out_path.name}")
        else:
            print(f"  {y}/{m:02d}: no data (maybe not yet announced)")
        time.sleep(1.0)

    if saved > 0:
        merged = merge_into_combined()
        if merged:
            cdf = pd.read_parquet(merged)
            print(f"\nCombined: {len(cdf):,} rows, "
                  f"{cdf['stock_id'].nunique()} stocks, "
                  f"covering {cdf['revenue_year'].min()}-"
                  f"{cdf['revenue_year'].max()} -> {merged.name}")


if __name__ == "__main__":
    main()
