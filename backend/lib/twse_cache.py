"""TWSE rolling cache reader — backend API 用,讀 daily ETL 寫的 parquet."""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TWSE_DIR = ROOT / "data" / "cache" / "twse"


@lru_cache(maxsize=4)
def _load_inst() -> pd.DataFrame:
    fp = TWSE_DIR / "institutional_latest.parquet"
    if not fp.exists():
        return pd.DataFrame()
    df = pd.read_parquet(fp)
    df["date"] = pd.to_datetime(df["date"])
    return df


@lru_cache(maxsize=4)
def _load_per() -> pd.DataFrame:
    fp = TWSE_DIR / "per_pbr_latest.parquet"
    if not fp.exists():
        return pd.DataFrame()
    df = pd.read_parquet(fp)
    df["date"] = pd.to_datetime(df["date"])
    return df


def clear_cache():
    """ETL 後呼叫,讓 backend 讀到新資料."""
    _load_inst.cache_clear()
    _load_per.cache_clear()


def get_inst_20d(ticker: str) -> dict[str, Any] | None:
    """某 ticker 最近 20 個交易日法人累計."""
    df = _load_inst()
    if df.empty:
        return None
    sub = df[df["ticker"] == str(ticker)].sort_values("date").tail(20)
    if sub.empty:
        return None
    return {
        "foreign_net_20d": int(sub["foreign_net"].fillna(0).sum()),
        "inv_trust_net_20d": int(sub["inv_trust_net"].fillna(0).sum()),
        "dealer_net_20d": int(sub["dealer_net"].fillna(0).sum()),
        "total_net_20d": int(sub["total_net"].fillna(0).sum()),
        "days": len(sub),
        "latest_date": str(sub["date"].max().date()),
    }


def get_per_latest(ticker: str) -> dict[str, Any] | None:
    """某 ticker 最新 PER / PBR / 殖利率."""
    df = _load_per()
    if df.empty:
        return None
    sub = df[df["ticker"] == str(ticker)].sort_values("date")
    if sub.empty:
        return None
    row = sub.iloc[-1]
    return {
        "per": float(row["per"]) if pd.notna(row.get("per")) else None,
        "pbr": float(row["pbr"]) if pd.notna(row.get("pbr")) else None,
        "dividend_yield": float(row["dividend_yield"]) if pd.notna(row.get("dividend_yield")) else None,
        "asof": str(row["date"].date()),
    }


def cache_status() -> dict[str, Any]:
    """檢查 cache 健康度."""
    inst_fp = TWSE_DIR / "institutional_latest.parquet"
    per_fp = TWSE_DIR / "per_pbr_latest.parquet"
    out: dict[str, Any] = {}
    for label, fp, loader in [
        ("institutional", inst_fp, _load_inst),
        ("per_pbr", per_fp, _load_per),
    ]:
        if not fp.exists():
            out[label] = {"exists": False}
            continue
        try:
            df = loader()
            out[label] = {
                "exists": True,
                "rows": len(df),
                "tickers": int(df["ticker"].nunique()) if not df.empty else 0,
                "days": int(df["date"].nunique()) if not df.empty else 0,
                "latest_date": str(df["date"].max().date()) if not df.empty else None,
                "file_mb": round(fp.stat().st_size / 1024 / 1024, 2),
            }
        except Exception as e:
            out[label] = {"exists": True, "error": str(e)}
    return out
