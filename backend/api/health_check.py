"""4 面健檢 API endpoint."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.lib.quote import fetch_quote
from backend.lib.score import calc_composite_health
from backend.lib.ticker_map import get_ticker_info

router = APIRouter(tags=["health"])

ROOT = Path(__file__).resolve().parents[2]
OHLCV_CACHE = ROOT / "data" / "cache" / "ohlcv"


def _load_local_ohlcv(ticker: str, days: int = 250) -> pd.DataFrame | None:
    """嘗試本地 cache,失敗回 None(API 之後 fallback yfinance)."""
    p = OHLCV_CACHE / f"{ticker}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").tail(days).reset_index(drop=True)
    except Exception:
        return None


def _calc_tech_indicators(df: pd.DataFrame) -> dict | None:
    """從 OHLCV df 算 MA / KD / RSI / 布林."""
    if df is None or len(df) < 60:
        return None
    df = df.copy()
    # MA
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    # KD (9)
    n = 9
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n) * 100
    rsv = rsv.fillna(50)
    df["k"] = rsv.ewm(alpha=1/3, adjust=False).mean()
    df["d"] = df["k"].ewm(alpha=1/3, adjust=False).mean()
    # RSI (14)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    last = df.iloc[-1]
    return {
        "price": float(last["close"]),
        "ma5": float(last["ma5"]) if pd.notna(last["ma5"]) else 0.0,
        "ma20": float(last["ma20"]) if pd.notna(last["ma20"]) else 0.0,
        "ma60": float(last["ma60"]) if pd.notna(last["ma60"]) else 0.0,
        "k": float(last["k"]) if pd.notna(last["k"]) else 50.0,
        "d": float(last["d"]) if pd.notna(last["d"]) else 50.0,
        "rsi": float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0,
    }


class HealthCheckOut(BaseModel):
    ticker: str
    name: str
    industry: str
    quote: dict
    health: dict
    sparkline: list[float]  # close 20d
    has_full_data: bool


@router.get("/health-check/{ticker}", response_model=HealthCheckOut)
def health_check(ticker: str):
    info = get_ticker_info(ticker) or {"name": "", "industry": ""}
    quote = fetch_quote(ticker)
    if not quote:
        raise HTTPException(status_code=404, detail=f"ticker {ticker} not found")

    # 試本地 cache,沒就用 yfinance 即時抓
    ohlcv = _load_local_ohlcv(ticker, days=120)
    has_full = ohlcv is not None

    if not has_full:
        # fallback: 用 yfinance 抓 3 個月
        try:
            import yfinance as yf
            for suffix in (".TW", ".TWO"):
                t = yf.Ticker(f"{ticker}{suffix}")
                h = t.history(period="6mo", auto_adjust=False)
                if not h.empty:
                    ohlcv = pd.DataFrame({
                        "date": pd.to_datetime(h.index).tz_localize(None),
                        "open": h["Open"].astype(float),
                        "high": h["High"].astype(float),
                        "low": h["Low"].astype(float),
                        "close": h["Close"].astype(float),
                        "volume": h["Volume"].astype(float),
                    }).reset_index(drop=True)
                    break
        except Exception as e:
            print(f"[health_check] yfinance fallback failed: {e}")

    tech = _calc_tech_indicators(ohlcv) if ohlcv is not None else None
    # 籌碼面 / 基本面 / 新聞 — placeholder,之後接 FinMind 整合
    chip = None
    funda = None
    news = None

    health = calc_composite_health(tech, chip, funda, news)

    # sparkline 20 日收盤
    spark = []
    if ohlcv is not None and len(ohlcv) > 0:
        spark = ohlcv["close"].tail(20).tolist()

    return HealthCheckOut(
        ticker=ticker,
        name=info["name"],
        industry=info["industry"],
        quote={
            "price": quote["price"],
            "change_pct": quote["change_pct"],
            "asof": quote["asof"],
        },
        health=health,
        sparkline=spark,
        has_full_data=has_full,
    )
