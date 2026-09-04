"""Local data source loader — reads TWSE/MOPS scraped caches.

Provides FinMind-compatible interface for the 4 critical datasets after
FinMind subscription expires (2026-05-20):

    - TaiwanStockInstitutionalInvestorsBuySell  → cache/twse/inst_twse_*.parquet
    - TaiwanStockPER                            → cache/twse/per_twse_*.parquet
    - TaiwanStockMonthRevenue                   → cache/mops/revenue_*.parquet

Each function returns a DataFrame matching FinMind's schema exactly, so
existing downstream code (morning_briefing, scanner, etc.) works unchanged.

If local cache is empty/stale for the requested date range, returns empty
DataFrame — caller can then fall back to FinMind API (while still subscribed).
"""
from __future__ import annotations
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TWSE_DIR = ROOT / "data" / "cache" / "twse"
MOPS_DIR = ROOT / "data" / "cache" / "mops"


def _load_combined(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def load_institutional_local(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Same schema as FinMind TaiwanStockInstitutionalInvestorsBuySell:
    columns = date, stock_id, name, buy, sell
    """
    df = _load_combined(TWSE_DIR / "inst_twse_combined.parquet")
    if df.empty:
        return df
    df = df[df["stock_id"] == ticker].copy()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return df.reset_index(drop=True)


def load_per_local(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Same schema as FinMind TaiwanStockPER:
    columns = date, stock_id, dividend_yield, PER, PBR
    """
    df = _load_combined(TWSE_DIR / "per_twse_combined.parquet")
    if df.empty:
        return df
    df = df[df["stock_id"] == ticker].copy()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return df.reset_index(drop=True)


def load_monthly_revenue_local(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Same schema as FinMind TaiwanStockMonthRevenue:
    columns = date, stock_id, country, revenue, revenue_month, revenue_year
    """
    df = _load_combined(MOPS_DIR / "revenue_combined.parquet")
    if df.empty:
        return df
    df = df[df["stock_id"] == ticker].copy()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return df.reset_index(drop=True)


def coverage_check() -> dict:
    """Diagnostic — what's in the local cache?"""
    out = {}
    for label, path in [
        ("inst",    TWSE_DIR / "inst_twse_combined.parquet"),
        ("per",     TWSE_DIR / "per_twse_combined.parquet"),
        ("revenue", MOPS_DIR / "revenue_combined.parquet"),
    ]:
        df = _load_combined(path)
        if df.empty:
            out[label] = {"status": "empty"}
        else:
            df["date_d"] = pd.to_datetime(df["date"]).dt.date
            out[label] = {
                "status":      "ok",
                "rows":        len(df),
                "stocks":      df["stock_id"].nunique(),
                "date_min":    str(df["date_d"].min()),
                "date_max":    str(df["date_d"].max()),
            }
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(coverage_check(), indent=2))
