"""
共用 parquet 快取工具。

快取策略：
- 每個 (source, dataset, ticker) 一個 parquet 檔
- 首次：抓 2017-01-01 至今的完整歷史
- 後續：若 cache 最後一筆 < 昨天，增量抓新資料後 append
- 呼叫端拿到 DataFrame 再自行 slice 所需時間範圍
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd


def cache_path(root: Path, source: str, key: str) -> Path:
    p = root / source
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{key}.parquet"


def load_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    return df if not df.empty else None


def save_cache(path: Path, df: pd.DataFrame) -> None:
    if not df.empty:
        df.to_parquet(path, index=False)


def cache_last_date(df: pd.DataFrame, date_col: str = "date") -> date | None:
    if df is None or df.empty or date_col not in df.columns:
        return None
    return pd.to_datetime(df[date_col]).dt.date.max()


def is_cache_fresh(df: pd.DataFrame | None, date_col: str = "date") -> bool:
    """快取最後一筆是否 >= 昨天（即不需要更新）。"""
    last = cache_last_date(df, date_col)
    if last is None:
        return False
    return last >= date.today() - timedelta(days=1)


def slice_by_date(
    df: pd.DataFrame, start: date, end: date, date_col: str = "date"
) -> pd.DataFrame:
    dates = pd.to_datetime(df[date_col]).dt.date
    return df[(dates >= start) & (dates <= end)].reset_index(drop=True)
