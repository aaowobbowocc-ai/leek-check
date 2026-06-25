from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.lib.quote import fetch_quote, fetch_quotes_batch
from backend.lib.ticker_map import get_ticker_info, search_tickers

router = APIRouter(tags=["quote"])


class TickerInfo(BaseModel):
    ticker: str
    name: str
    industry: str
    type: str


class QuoteOut(BaseModel):
    ticker: str
    name: str
    industry: str
    price: float
    prev_close: float
    change_pct: float
    open: float
    high: float
    low: float
    volume: int
    asof: str


# IMPORTANT: 具體 path 必須定義在 {ticker} 動態 path 之前,
# 不然 /quote/batch 會被 /quote/{ticker} 吃成 ticker="batch"
@router.get("/quote/batch", response_model=list[QuoteOut])
def get_quote_batch(tickers: str = Query(..., description="逗號分隔")):
    tks = [t.strip() for t in tickers.split(",") if t.strip()][:50]
    quotes = fetch_quotes_batch(tks)
    out = []
    for tk in tks:
        q = quotes.get(tk)
        if not q:
            continue
        info = get_ticker_info(tk) or {"name": "", "industry": ""}
        out.append(QuoteOut(
            ticker=tk, name=info["name"], industry=info["industry"],
            price=q["price"], prev_close=q["prev_close"],
            change_pct=q["change_pct"], open=q["open"], high=q["high"],
            low=q["low"], volume=q["volume"], asof=q["asof"],
        ))
    return out


@router.get("/quote/{ticker}", response_model=QuoteOut)
def get_quote(ticker: str):
    info = get_ticker_info(ticker) or {"ticker": ticker, "name": "", "industry": "", "type": ""}
    q = fetch_quote(ticker)
    if not q:
        raise HTTPException(status_code=404, detail=f"ticker {ticker} not found")
    return QuoteOut(
        ticker=ticker,
        name=info["name"],
        industry=info["industry"],
        price=q["price"],
        prev_close=q["prev_close"],
        change_pct=q["change_pct"],
        open=q["open"],
        high=q["high"],
        low=q["low"],
        volume=q["volume"],
        asof=q["asof"],
    )


@router.get("/search", response_model=list[TickerInfo])
def search(q: str = Query(..., min_length=1)):
    return [TickerInfo(**info) for info in search_tickers(q, limit=20)]
