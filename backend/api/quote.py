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
def _resolve_name_industry(tk: str, q: dict) -> tuple[str, str]:
    """name + industry 解析 — ticker_map 先,空就用 q.name (TWSE 即時) fallback."""
    info = get_ticker_info(tk)
    name = (info or {}).get("name", "").strip()
    industry = (info or {}).get("industry", "").strip()
    # ticker_map 沒名稱 → 用 TWSE quote 回的 name
    if not name and q.get("name"):
        name = str(q["name"]).strip()
    # ETF / KY / 新股 industry 推測
    if not industry:
        if tk.startswith("00") or tk.startswith("0050"):
            industry = "ETF"
        elif "KY" in name.upper():
            industry = "F-公司"
        else:
            industry = "—"
    return name or tk, industry


@router.get("/quote/batch", response_model=list[QuoteOut])
def get_quote_batch(tickers: str = Query(..., description="逗號分隔")):
    tks = [t.strip() for t in tickers.split(",") if t.strip()][:50]
    quotes = fetch_quotes_batch(tks)
    out = []
    for tk in tks:
        q = quotes.get(tk)
        if not q:
            continue
        name, industry = _resolve_name_industry(tk, q)
        out.append(QuoteOut(
            ticker=tk, name=name, industry=industry,
            price=q["price"], prev_close=q["prev_close"],
            change_pct=q["change_pct"], open=q["open"], high=q["high"],
            low=q["low"], volume=q["volume"], asof=q["asof"],
        ))
    return out


@router.get("/quote/{ticker}", response_model=QuoteOut)
def get_quote(ticker: str):
    q = fetch_quote(ticker)
    if not q:
        raise HTTPException(status_code=404, detail=f"ticker {ticker} not found")
    name, industry = _resolve_name_industry(ticker, q)
    return QuoteOut(
        ticker=ticker,
        name=name,
        industry=industry,
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
