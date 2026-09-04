"""每日本地抓 MOPS 月營收 + TWSE PER 算 YoY → 上傳 Supabase.

雲端 (Render) 沒 FinMind cache,靠這個 script 補基本面資料。
Windows Task Scheduler 排每天 03:00 跑一次即可。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"
MOPS = ROOT / "data" / "cache" / "mops" / "revenue_combined.parquet"
TWSE_PER = ROOT / "data" / "cache" / "twse" / "per_pbr_latest.parquet"
TPE = ZoneInfo("Asia/Taipei")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
if (not SUPABASE_URL or not SUPABASE_SERVICE_KEY) and SECRETS.exists():
    for line in SECRETS.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("SUPABASE_URL") and "=" in s:
            SUPABASE_URL = s.split("=", 1)[1].strip().strip('"').strip("'")
        elif s.startswith("SUPABASE_SERVICE_KEY") and "=" in s:
            SUPABASE_SERVICE_KEY = s.split("=", 1)[1].strip().strip('"').strip("'")


def compute_ticker_funda(ticker: str, rev_df: pd.DataFrame, per_df: pd.DataFrame) -> dict | None:
    """一個 ticker 的完整 funda snapshot."""
    out: dict = {}

    import math

    def _safe(v, digits=2, default=0.0):
        """Filter NaN / Inf 給 JSON safe."""
        if not pd.notna(v):
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        # clamp 極端值(YoY 通常不會超過 500%)
        return round(max(-999.0, min(999.0, f)), digits)

    # rev_yoy + rev_history (12 期給前端 chart)
    r = rev_df[rev_df["stock_id"] == ticker].sort_values("date")
    if len(r) >= 13:
        r = r.copy()
        r["yoy"] = r["revenue"].pct_change(12) * 100
        latest_yoy = _safe(r["yoy"].iloc[-1])
        if latest_yoy != 0.0 or pd.notna(r["yoy"].iloc[-1]):
            out["rev_yoy"] = latest_yoy
        tail12 = r.tail(12)
        out["rev_history"] = [
            {
                "month": d.strftime("%Y/%m") if hasattr(d, "strftime") else str(d),
                "rev_yi": _safe(rv / 100_000_000 if pd.notna(rv) else 0),
                "yoy": _safe(y),
            }
            for d, rv, y in zip(tail12["date"], tail12["revenue"], tail12["yoy"])
        ]

    # PER / PBR / dividend_yield from TWSE
    if per_df is not None and not per_df.empty:
        p = per_df[per_df["ticker"] == ticker].sort_values("date")
        if not p.empty:
            latest = p.iloc[-1]
            if pd.notna(latest.get("per")):
                out["per"] = round(float(latest["per"]), 2)
            if pd.notna(latest.get("pbr")):
                out["pbr"] = round(float(latest["pbr"]), 2)
            if pd.notna(latest.get("dividend_yield")):
                out["yield"] = round(float(latest["dividend_yield"]), 2)

    return out if out else None


def main():
    print(f"=== stock funda snapshot | {datetime.now(TPE).strftime('%Y-%m-%d %H:%M:%S')} ===")

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("✗ Supabase 未設定,skip")
        sys.exit(1)

    if not MOPS.exists():
        print(f"✗ 找不到 {MOPS}(先跑 fetch_mops_revenue)")
        sys.exit(1)

    print(f"[1/3] 讀 MOPS 月營收 ...")
    rev = pd.read_parquet(MOPS)
    rev["date"] = pd.to_datetime(rev["date"])
    rev["stock_id"] = rev["stock_id"].astype(str)
    print(f"       {len(rev):,} rows / {rev['stock_id'].nunique()} stocks / {rev['date'].min().date()} → {rev['date'].max().date()}")

    per_df = None
    if TWSE_PER.exists():
        per_df = pd.read_parquet(TWSE_PER)
        per_df["date"] = pd.to_datetime(per_df["date"])
        per_df["ticker"] = per_df["ticker"].astype(str)
        print(f"       TWSE PER cache: {len(per_df):,} rows / {per_df['ticker'].nunique()} tickers")

    print(f"[2/3] 算 per-ticker funda ...")
    tickers = sorted(rev["stock_id"].unique())
    funda: dict[str, dict] = {}
    for tk in tickers:
        f = compute_ticker_funda(tk, rev, per_df)
        if f:
            funda[tk] = f
    print(f"       {len(funda)} tickers 有 funda data")

    print(f"[3/3] 上傳 Supabase (market_snapshot.data.stocks_funda) ...")
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        # 讀當前 snapshot,merge 進去(不動 indices)
        r = sb.table("market_snapshot").select("data").eq("id", 1).execute()
        existing = r.data[0]["data"] if r.data else {}
        existing["stocks_funda"] = funda
        payload = {
            "id": 1,
            "data": existing,
            "updated_at": datetime.now(TPE).isoformat(),
        }
        sb.table("market_snapshot").upsert(payload).execute()
        print(f"✓ upsert {len(funda)} tickers 的 funda (indices 不動)")
    except Exception as e:
        print(f"✗ Supabase upsert 失敗: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
