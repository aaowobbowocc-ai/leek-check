"""讀取 stock_info.parquet 建 ticker → 公司名 / 產業 map。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TICKER_PARQUET = ROOT / "data" / "cache" / "finmind" / "extras" / "stock_info.parquet"


@lru_cache(maxsize=1)
def load_ticker_map() -> dict[str, dict]:
    """回傳 {ticker: {name, industry, type}}."""
    if not TICKER_PARQUET.exists():
        return {}
    df = pd.read_parquet(TICKER_PARQUET)
    # 預期欄位: stock_id, stock_name, industry_category, type
    cols = {c.lower(): c for c in df.columns}
    sid = cols.get("stock_id") or cols.get("ticker") or "stock_id"
    sname = cols.get("stock_name") or cols.get("name") or "stock_name"
    sind = cols.get("industry_category") or cols.get("industry") or None
    stype = cols.get("type") or None

    result = {}
    for _, row in df.iterrows():
        tk = str(row[sid]).strip()
        if not tk:
            continue
        result[tk] = {
            "ticker": tk,
            "name": str(row[sname]) if sname in df.columns else "",
            "industry": str(row[sind]) if sind else "",
            "type": str(row[stype]) if stype else "twse",
        }
    return result


def get_ticker_info(ticker: str) -> dict | None:
    return load_ticker_map().get(ticker)


def search_tickers(query: str, limit: int = 20) -> list[dict]:
    """模糊比對 ticker code 或 公司名稱."""
    q = query.strip().lower()
    if not q:
        return []
    out = []
    for tk, info in load_ticker_map().items():
        if q in tk.lower() or q in info["name"].lower():
            out.append(info)
            if len(out) >= limit:
                break
    return out
