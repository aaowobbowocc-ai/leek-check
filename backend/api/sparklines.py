"""批次 sparkline endpoint — 給 watch/book/pick 卡片畫 mini 折線用."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from fastapi import APIRouter, Query

router = APIRouter(tags=["sparklines"])

ROOT = Path(__file__).resolve().parents[2]
OHLCV_CACHE = ROOT / "data" / "cache" / "ohlcv"

# 記憶體 cache 6h — sparkline 收盤級,不需即時
_MEM: dict[str, tuple[float, list[float]]] = {}
_TTL = 6 * 3600


def _load_local(ticker: str, days: int) -> list[float] | None:
    """本地 parquet cache — dev / 有 disk 的 backend 用."""
    p = OHLCV_CACHE / f"{ticker}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        df = df.sort_values("date").tail(days)
        return [float(c) for c in df["close"]]
    except Exception:
        return None


def _fetch_twse_stock_day(ticker: str, days: int) -> list[float]:
    """從 TWSE STOCK_DAY 抓 ~2 個月的收盤(足夠 20-40 天 sparkline).
    endpoint 每次一個月 → 抓 2 次."""
    rows: list[tuple[str, float]] = []
    today = date.today()
    cur = today.replace(day=1)
    for i in range(3):  # 3 個月保險
        url = ("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
               f"?date={cur.strftime('%Y%m%d')}&stockNo={ticker}&response=json")
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                for row in data.get("data", []):
                    try:
                        dt = row[0]  # 民國 YYY/MM/DD
                        yy, mm, dd = dt.split("/")
                        dt_iso = f"{int(yy) + 1911}-{int(mm):02d}-{int(dd):02d}"
                        close = float(str(row[6]).replace(",", ""))
                        rows.append((dt_iso, close))
                    except (ValueError, IndexError):
                        pass
        except Exception:
            pass
        cur = (cur - timedelta(days=1)).replace(day=1)
        if len(rows) >= days + 5:
            break
    # 去重、排序、取最後 days 天
    seen = {}
    for dt, c in rows:
        seen[dt] = c
    sorted_close = [seen[k] for k in sorted(seen.keys())]
    return sorted_close[-days:]


def _get_sparkline(ticker: str, days: int) -> list[float]:
    """單檔 sparkline: local cache → TWSE → 空 list."""
    key = f"{ticker}_{days}"
    cached = _MEM.get(key)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]
    data = _load_local(ticker, days)
    if not data or len(data) < 2:
        data = _fetch_twse_stock_day(ticker, days) or []
    _MEM[key] = (time.time(), data)
    return data


@router.get("/sparklines/batch")
def get_sparklines_batch(
    tickers: str = Query(..., description="逗號分隔,最多 30"),
    days: int = Query(20, ge=5, le=60),
):
    """回傳 {ticker: [close_1, close_2, ..., close_N]}."""
    tks = [t.strip() for t in tickers.split(",") if t.strip()][:30]
    out: dict[str, list[float]] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {tk: ex.submit(_get_sparkline, tk, days) for tk in tks}
        for tk, f in futs.items():
            out[tk] = f.result()
    return out
