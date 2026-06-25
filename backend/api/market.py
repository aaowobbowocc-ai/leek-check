"""大盤儀表板 — TAIEX + VIX + 美股主要指數."""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from backend.lib.quote import fetch_quote

router = APIRouter(tags=["market"])


class MarketIndex(BaseModel):
    symbol: str
    name: str
    price: float
    change_pct: float
    asof: str


class MarketDashboard(BaseModel):
    taiex: MarketIndex | None
    vix: MarketIndex | None
    sp500: MarketIndex | None
    nasdaq: MarketIndex | None
    dxj: MarketIndex | None
    nikkei: MarketIndex | None


def _fetch_yf_index(symbol: str, name: str) -> MarketIndex | None:
    """yfinance 拉一個 index."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        h = t.history(period="5d", auto_adjust=False)
        if h.empty:
            return None
        close = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else close
        chg = (close / prev - 1) * 100 if prev else 0.0
        return MarketIndex(
            symbol=symbol,
            name=name,
            price=close,
            change_pct=round(chg, 2),
            asof=h.index[-1].strftime("%Y-%m-%d"),
        )
    except Exception:
        return None


@router.get("/market/dashboard", response_model=MarketDashboard)
def market_dashboard():
    return MarketDashboard(
        taiex=_fetch_yf_index("^TWII", "台股加權指數"),
        vix=_fetch_yf_index("^VIX", "美股恐慌指數"),
        sp500=_fetch_yf_index("^GSPC", "S&P 500"),
        nasdaq=_fetch_yf_index("^IXIC", "NASDAQ"),
        dxj=_fetch_yf_index("DXJ", "日股 DXJ"),
        nikkei=_fetch_yf_index("^N225", "日經 225"),
    )
