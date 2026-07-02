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


# ────── 單檔 OHLCV(K 線用)+ 時間尺度 ──────
_OHLCV_MEM: dict[str, tuple[float, list[dict]]] = {}
_OHLCV_TTL = 3600  # 1h


def _fetch_twse_ohlcv(ticker: str, months: int) -> list[dict]:
    """從 TWSE STOCK_DAY 拉 N 個月 OHLCV."""
    rows: list[dict] = []
    today = date.today()
    cur = today.replace(day=1)
    for _ in range(months + 1):
        url = ("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
               f"?date={cur.strftime('%Y%m%d')}&stockNo={ticker}&response=json")
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                for row in data.get("data", []):
                    try:
                        yy, mm, dd = row[0].split("/")
                        dt_iso = f"{int(yy) + 1911}-{int(mm):02d}-{int(dd):02d}"
                        rows.append({
                            "date": dt_iso,
                            "open": float(str(row[3]).replace(",", "")),
                            "high": float(str(row[4]).replace(",", "")),
                            "low": float(str(row[5]).replace(",", "")),
                            "close": float(str(row[6]).replace(",", "")),
                            "volume": int(str(row[1]).replace(",", "")) // 1000,  # 張
                        })
                    except (ValueError, IndexError):
                        pass
        except Exception:
            pass
        cur = (cur - timedelta(days=1)).replace(day=1)
    # 去重排序
    seen = {r["date"]: r for r in rows}
    return sorted(seen.values(), key=lambda x: x["date"])


@router.get("/ohlcv/{ticker}")
def get_ohlcv(
    ticker: str,
    days: int = Query(60, ge=5, le=500),
):
    """單檔 OHLCV — K 線 / 折線 都用它.
    days=5/20/60/120/250 常用."""
    key = f"{ticker}_{days}"
    hit = _OHLCV_MEM.get(key)
    if hit and (time.time() - hit[0]) < _OHLCV_TTL:
        return {"ticker": ticker, "bars": hit[1]}
    # 本地 parquet 先
    p = OHLCV_CACHE / f"{ticker}.parquet"
    bars: list[dict] = []
    if p.exists():
        try:
            df = pd.read_parquet(p).sort_values("date").tail(days)
            for _, r in df.iterrows():
                bars.append({
                    "date": r["date"].strftime("%Y-%m-%d") if hasattr(r["date"], "strftime") else str(r["date"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": int(r["volume"]) if not pd.isna(r["volume"]) else 0,
                })
        except Exception:
            bars = []
    if not bars or len(bars) < min(5, days // 2):
        # 沒本地 → TWSE 拉,依 days 決定月數
        months = max(1, (days + 20) // 20)
        bars = _fetch_twse_ohlcv(ticker, months)[-days:]
    _OHLCV_MEM[key] = (time.time(), bars)
    return {"ticker": ticker, "bars": bars}
