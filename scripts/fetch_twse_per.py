"""TWSE BWIBBU_d PER/PBR/殖利率 爬蟲 — FinMind TaiwanStockPER 替代

URL: https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=YYYYMMDD&selectType=ALL&response=csv

Output: data/cache/twse/per_twse_YYYYMMDD.parquet (per-day)
        + data/cache/twse/per_twse_combined.parquet

Schema (matches FinMind TaiwanStockPER):
  date, stock_id, dividend_yield, PER, PBR

OTC: TPEx 對應端點為 /www/zh-tw/afterTrading/peQryDate (HTML, 解析麻煩 — 暫不做)

Run:
  python -m scripts.fetch_twse_per                  # today
  python -m scripts.fetch_twse_per --date 20260507
  python -m scripts.fetch_twse_per --backfill 30
"""
from __future__ import annotations
import argparse, io, time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "cache" / "twse"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TWSE_BWIBBU = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"


def _to_float(x) -> float:
    if pd.isna(x):
        return float("nan")
    s = str(x).replace(",", "").strip()
    if s == "" or s == "--":
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def fetch_twse_per(target_date: date) -> pd.DataFrame:
    date_str = target_date.strftime("%Y%m%d")
    params = {"date": date_str, "selectType": "ALL", "response": "csv"}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.twse.com.tw/zh/page/trading/exchange/BWIBBU_d.html",
    }
    r = requests.get(TWSE_BWIBBU, params=params, headers=headers, timeout=30)
    if r.status_code != 200:
        return pd.DataFrame()

    try:
        text = r.content.decode("ms950", errors="ignore")
    except Exception:
        text = r.content.decode("utf-8-sig", errors="ignore")

    lines = text.splitlines()
    # Find header row
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith('"證券代號"') or line.startswith("證券代號"):
            header_idx = i
            break
    if header_idx is None:
        return pd.DataFrame()

    csv_text = "\n".join(lines[header_idx:])
    try:
        df = pd.read_csv(io.StringIO(csv_text), thousands=",")
    except Exception as e:
        print(f"    parse fail {target_date}: {e!r}")
        return pd.DataFrame()
    df.columns = [c.strip().strip('"') for c in df.columns]

    if "證券代號" not in df.columns:
        return pd.DataFrame()

    df["stock_id"] = df["證券代號"].astype(str).str.replace("=", "").str.replace('"', "").str.strip()
    df = df[df["stock_id"].str.match(r"^\d{4}\w?$")].copy()
    df = df[~df["stock_id"].str.contains("合計", na=False)]

    # Map columns (TWSE column names may vary slightly)
    col_yield = next((c for c in df.columns if "殖利率" in c), None)
    col_per   = next((c for c in df.columns if "本益比" in c), None)
    col_pbr   = next((c for c in df.columns if "股價淨值比" in c), None)

    if not col_per:
        return pd.DataFrame()

    out = pd.DataFrame({
        "date":           target_date.isoformat(),
        "stock_id":       df["stock_id"].values,
        "dividend_yield": df[col_yield].apply(_to_float) if col_yield else float("nan"),
        "PER":            df[col_per].apply(_to_float),
        "PBR":            df[col_pbr].apply(_to_float) if col_pbr else float("nan"),
    })
    return out.dropna(subset=["PER"])


def merge_into_combined() -> Path:
    files = sorted(OUT_DIR.glob("per_twse_*.parquet"))
    files = [f for f in files if "combined" not in f.name]
    if not files:
        return None
    dfs = [pd.read_parquet(f) for f in files]
    combined = pd.concat(dfs, ignore_index=True).drop_duplicates(
        subset=["date", "stock_id"], keep="last"
    ).sort_values(["date", "stock_id"])
    out = OUT_DIR / "per_twse_combined.parquet"
    combined.to_parquet(out, index=False)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--backfill", type=int, default=0)
    args = parser.parse_args()

    if args.date:
        dates = [datetime.strptime(args.date, "%Y%m%d").date()]
    elif args.backfill > 0:
        today = date.today()
        dates = [today - timedelta(days=i) for i in range(args.backfill)]
    else:
        dates = [date.today()]

    print(f"Fetching {len(dates)} day(s) from TWSE BWIBBU_d (PER/PBR/yield)")
    saved = 0
    for d in dates:
        if d.weekday() >= 5:
            continue
        out_path = OUT_DIR / f"per_twse_{d.strftime('%Y%m%d')}.parquet"
        if out_path.exists():
            print(f"  {d}: cache hit, skip")
            saved += 1
            continue
        print(f"  {d}: fetching...")
        df = fetch_twse_per(d)
        time.sleep(1.0)
        if len(df) > 0:
            df.to_parquet(out_path, index=False)
            saved += 1
            print(f"    saved {len(df)} stocks -> {out_path.name}")
        else:
            print(f"    no data (probably holiday)")

    if saved > 0:
        merged = merge_into_combined()
        if merged:
            cdf = pd.read_parquet(merged)
            print(f"\nCombined: {len(cdf):,} rows, "
                  f"{cdf['date'].nunique()} dates, "
                  f"{cdf['stock_id'].nunique()} stocks -> {merged.name}")


if __name__ == "__main__":
    main()
