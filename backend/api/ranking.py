"""排行榜 — 漲幅 / 跌幅 / 量爆 / 健檢分數."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.lib.quote import fetch_quotes_batch
from backend.lib.ticker_map import get_ticker_info

router = APIRouter(tags=["ranking"])

# Top ~80 熱門權值/ETF (sync streamlit TICKER_UNIVERSE_FALLBACK 精選)
RANKING_UNIVERSE = [
    "2330", "2454", "2317", "2308", "2382", "2412", "2002", "2891",
    "2884", "2885", "2881", "2890", "2603", "2618", "2609", "2615",
    "1101", "1216", "1301", "1303", "2207", "3008", "2207", "2027",
    "3231", "2376", "3702", "8069", "2492", "2356", "3034", "3711",
    "2207", "1102", "2105", "2912", "1326", "5871", "2606", "2207",
    "0050", "0056", "00878", "00692", "00919", "00713", "00679B",
    "00940", "00929", "00713", "00876", "00891", "00892", "00713",
]


class RankItem(BaseModel):
    ticker: str
    name: str
    industry: str
    price: float
    change_pct: float
    volume: int
    composite: int | None = None  # 健檢分數(僅 health 模式)
    verdict: str | None = None     # 健康/亞健康/韭菜病


class RankOut(BaseModel):
    type: str
    items: list[RankItem]


@router.get("/ranking", response_model=RankOut)
def ranking(
    by: str = Query("up", description="up / down / volume / health"),
    limit: int = Query(20, ge=1, le=50),
):
    universe = list(dict.fromkeys(RANKING_UNIVERSE))
    quotes = fetch_quotes_batch(universe)
    items: list[RankItem] = []
    for tk in universe:
        q = quotes.get(tk)
        if not q:
            continue
        info = get_ticker_info(tk) or {"name": "", "industry": ""}
        items.append(RankItem(
            ticker=tk, name=info["name"], industry=info["industry"],
            price=q["price"], change_pct=q["change_pct"],
            volume=q["volume"],
        ))

    if by == "health":
        # 健檢排行:批次跑 health-check 算分(parallel)
        from backend.api.health_check import health_check as run_hc
        scored = []
        def _score(tk: str):
            try:
                hc = run_hc(tk)
                return tk, hc.health.get("composite"), hc.health.get("verdict")
            except Exception:
                return tk, None, None
        with ThreadPoolExecutor(max_workers=8) as ex:
            for tk, comp, verdict in ex.map(_score, [x.ticker for x in items]):
                if comp is not None:
                    scored.append((tk, comp, verdict))
        score_map = {tk: (comp, v) for tk, comp, v in scored}
        for it in items:
            if it.ticker in score_map:
                it.composite, it.verdict = score_map[it.ticker]
        items = [x for x in items if x.composite is not None]
        items.sort(key=lambda x: x.composite or 0, reverse=True)
    elif by == "up":
        items.sort(key=lambda x: x.change_pct, reverse=True)
    elif by == "down":
        items.sort(key=lambda x: x.change_pct)
    elif by == "volume":
        items.sort(key=lambda x: x.volume, reverse=True)
    return RankOut(type=by, items=items[:limit])
