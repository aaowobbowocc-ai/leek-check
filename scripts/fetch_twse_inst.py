"""TWSE 三大法人買賣超 (T86) 爬蟲 — FinMind 替代來源

URL: https://www.twse.com.tw/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALL&response=csv

Output: data/cache/twse/inst_twse_YYYYMMDD.parquet (per-day)
        + data/cache/twse/inst_twse_combined.parquet (all dates merged)

Schema (matches FinMind TaiwanStockInstitutionalInvestorsBuySell):
  date, stock_id, name, buy, sell  (per investor type per stock)

TWSE 投資者類別 → FinMind name 對應:
  外陸資 (excl 自營)    → Foreign_Investor
  外資自營商            → Foreign_Dealer_Self
  投信                  → Investment_Trust
  自營商 (自行買賣)     → Dealer_self
  自營商 (避險)         → Dealer_Hedging

OTC (上櫃) 用 TPEx 端點:
  https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade.php

Run modes:
  python -m scripts.fetch_twse_inst                  # today only
  python -m scripts.fetch_twse_inst --date 20260507  # specific date
  python -m scripts.fetch_twse_inst --backfill 30    # last 30 trading days
"""
from __future__ import annotations
import argparse, io, sys, time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "cache" / "twse"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_3INSTI = ("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?"
               "code=&date={date}&id=&type=Daily&response=csv")

# TWSE T86 column → FinMind investor name mapping (handles renamed cols)
INVESTOR_MAP = {
    "外陸資買進股數(不含外資自營商)":  ("Foreign_Investor", "buy"),
    "外陸資賣出股數(不含外資自營商)":  ("Foreign_Investor", "sell"),
    "外資自營商買進股數":              ("Foreign_Dealer_Self", "buy"),
    "外資自營商賣出股數":              ("Foreign_Dealer_Self", "sell"),
    "投信買進股數":                    ("Investment_Trust", "buy"),
    "投信賣出股數":                    ("Investment_Trust", "sell"),
    "自營商買進股數(自行買賣)":        ("Dealer_self", "buy"),
    "自營商賣出股數(自行買賣)":        ("Dealer_self", "sell"),
    "自營商買進股數(避險)":            ("Dealer_Hedging", "buy"),
    "自營商賣出股數(避險)":            ("Dealer_Hedging", "sell"),
}


def _to_int(x) -> int:
    if pd.isna(x):
        return 0
    s = str(x).replace(",", "").strip()
    if s == "" or s == "--":
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def fetch_twse_t86(target_date: date) -> pd.DataFrame:
    """Fetch one day of TWSE T86 institutional data. Returns long-format
    DataFrame matching FinMind schema."""
    date_str = target_date.strftime("%Y%m%d")
    params = {"date": date_str, "selectType": "ALL", "response": "csv"}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.twse.com.tw/zh/page/trading/fund/T86.html",
    }
    r = requests.get(TWSE_T86, params=params, headers=headers, timeout=30)
    if r.status_code != 200:
        return pd.DataFrame()

    # TWSE CSV uses ms950 (Big5) encoding (Content-Type: text/csv;charset=ms950).
    # Try ms950 first, fall back to utf-8-sig.
    try:
        text = r.content.decode("ms950", errors="ignore")
    except Exception:
        text = r.content.decode("utf-8-sig", errors="ignore")
    lines = text.splitlines()
    if len(lines) < 5:
        return pd.DataFrame()

    # Find header row (the one starting with "證券代號")
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith('"證券代號"') or line.startswith("證券代號"):
            header_idx = i
            break
    if header_idx is None:
        return pd.DataFrame()

    # Read from header row, skipping summary rows at end
    csv_text = "\n".join(lines[header_idx:])
    try:
        df = pd.read_csv(io.StringIO(csv_text), thousands=",")
    except Exception as e:
        print(f"    parse fail {target_date}: {e!r}")
        return pd.DataFrame()

    # Clean column names (remove " in column names that wrap quoted)
    df.columns = [c.strip().strip('"') for c in df.columns]
    if "證券代號" not in df.columns:
        return pd.DataFrame()

    # Strip the "=\"2330\"" wrappers TWSE uses
    df["stock_id"] = df["證券代號"].astype(str).str.replace("=", "").str.replace('"', "").str.strip()
    df = df[df["stock_id"].str.match(r"^\d{4}$") | df["stock_id"].str.match(r"^\d{4}\w?$")]
    df = df[~df["stock_id"].str.contains("合計", na=False)]

    # Pivot to long format: (date, stock_id, name, buy, sell)
    rows = []
    for _, r_ in df.iterrows():
        sid = r_["stock_id"]
        per_investor = {}
        for col, (inv_name, side) in INVESTOR_MAP.items():
            if col in df.columns:
                per_investor.setdefault(inv_name, {"buy": 0, "sell": 0})
                per_investor[inv_name][side] = _to_int(r_[col])
        for inv_name, sides in per_investor.items():
            if sides["buy"] == 0 and sides["sell"] == 0:
                continue
            rows.append({
                "date":     target_date.isoformat(),
                "stock_id": sid,
                "name":     inv_name,
                "buy":      sides["buy"],
                "sell":     sides["sell"],
            })
    return pd.DataFrame(rows)


def fetch_tpex_inst(target_date: date) -> pd.DataFrame:
    """Fetch one day of TPEx 3insti data. Same schema as FinMind. May fail
    on holidays / no data days."""
    date_str = target_date.strftime("%Y/%m/%d")
    url = TPEX_3INSTI.format(date=date_str)
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=30)
    except Exception as e:
        print(f"    tpex fetch fail {target_date}: {e!r}")
        return pd.DataFrame()
    if r.status_code != 200:
        return pd.DataFrame()
    try:
        text = r.content.decode("ms950", errors="ignore")
    except Exception:
        text = r.content.decode("utf-8-sig", errors="ignore")
    if "證券代號" not in text and "代號" not in text:
        return pd.DataFrame()
    lines = text.splitlines()
    header_idx = next((i for i, l in enumerate(lines)
                       if l.startswith('"代號"') or l.startswith("代號")
                       or "證券代號" in l), None)
    if header_idx is None:
        return pd.DataFrame()
    try:
        df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])), thousands=",")
    except Exception:
        return pd.DataFrame()
    df.columns = [c.strip().strip('"') for c in df.columns]
    sid_col = "代號" if "代號" in df.columns else ("證券代號" if "證券代號" in df.columns else None)
    if sid_col is None:
        return pd.DataFrame()
    df["stock_id"] = df[sid_col].astype(str).str.strip()
    df = df[df["stock_id"].str.match(r"^\d{4}\w?$")]

    rows = []
    for _, r_ in df.iterrows():
        sid = r_["stock_id"]
        for col, (inv_name, side) in INVESTOR_MAP.items():
            if col in df.columns:
                rows.append((target_date.isoformat(), sid, inv_name, side, _to_int(r_[col])))
    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows, columns=["date", "stock_id", "name", "side", "val"])
    pivoted = raw.pivot_table(index=["date", "stock_id", "name"],
                               columns="side", values="val",
                               fill_value=0).reset_index()
    pivoted.columns.name = None
    if "buy" not in pivoted.columns:
        pivoted["buy"] = 0
    if "sell" not in pivoted.columns:
        pivoted["sell"] = 0
    return pivoted[["date", "stock_id", "name", "buy", "sell"]]


def save_day(df: pd.DataFrame, target_date: date) -> Path:
    if df.empty:
        return None
    out = OUT_DIR / f"inst_twse_{target_date.strftime('%Y%m%d')}.parquet"
    df.to_parquet(out, index=False)
    return out


def merge_into_combined() -> Path:
    """Concatenate all per-day files into one combined parquet for fast lookup."""
    files = sorted(OUT_DIR.glob("inst_twse_*.parquet"))
    files = [f for f in files if "combined" not in f.name]
    if not files:
        return None
    dfs = [pd.read_parquet(f) for f in files]
    combined = pd.concat(dfs, ignore_index=True).drop_duplicates(
        subset=["date", "stock_id", "name"], keep="last"
    ).sort_values(["date", "stock_id", "name"])
    out = OUT_DIR / "inst_twse_combined.parquet"
    combined.to_parquet(out, index=False)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYYMMDD")
    parser.add_argument("--backfill", type=int, default=0,
                        help="last N trading days")
    args = parser.parse_args()

    if args.date:
        dates = [datetime.strptime(args.date, "%Y%m%d").date()]
    elif args.backfill > 0:
        # Roughly N calendar days; weekends will return empty (skipped)
        today = date.today()
        dates = [today - timedelta(days=i) for i in range(args.backfill)]
    else:
        dates = [date.today()]

    print(f"Fetching {len(dates)} day(s) from TWSE T86")
    saved = 0
    for d in dates:
        if d.weekday() >= 5:  # Sat/Sun
            continue
        out_path = OUT_DIR / f"inst_twse_{d.strftime('%Y%m%d')}.parquet"
        if out_path.exists():
            print(f"  {d}: cache hit, skip")
            saved += 1
            continue
        print(f"  {d}: fetching TWSE T86...")
        df_twse = fetch_twse_t86(d)
        time.sleep(1.0)  # be polite
        print(f"    TWSE: {len(df_twse)} rows")
        # TPEx (上櫃) — appended to same file. Skip if TWSE empty (likely holiday).
        if len(df_twse) > 0:
            df_tpex = fetch_tpex_inst(d)
            print(f"    TPEx: {len(df_tpex)} rows")
            time.sleep(1.0)
            df = pd.concat([df_twse, df_tpex], ignore_index=True) if len(df_tpex) else df_twse
        else:
            df = df_twse
        if len(df) > 0:
            save_day(df, d)
            saved += 1
            print(f"    saved {len(df)} rows -> {out_path.name}")

    if saved > 0:
        merged = merge_into_combined()
        if merged:
            cdf = pd.read_parquet(merged)
            print(f"\nCombined: {len(cdf):,} rows, "
                  f"{cdf['date'].nunique()} dates, "
                  f"{cdf['stock_id'].nunique()} stocks -> {merged.name}")


if __name__ == "__main__":
    main()
