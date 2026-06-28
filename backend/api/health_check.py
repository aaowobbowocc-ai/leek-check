"""4 面健檢 API endpoint — 含 OHLCV + 技術指標 + 法人 + 月營收."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.lib.quote import fetch_quote
from backend.lib.score import calc_composite_health
from backend.lib.ticker_map import get_ticker_info
from backend.lib import twse_cache

router = APIRouter(tags=["health"])

ROOT = Path(__file__).resolve().parents[2]
OHLCV_CACHE = ROOT / "data" / "cache" / "ohlcv"
FINMIND_INST = ROOT / "data" / "cache" / "finmind" / "institutional"
FINMIND_REV = ROOT / "data" / "cache" / "finmind" / "revenue"
FINMIND_PER = ROOT / "data" / "cache" / "finmind" / "extras"


def _load_local_ohlcv(ticker: str, days: int = 250) -> pd.DataFrame | None:
    p = OHLCV_CACHE / f"{ticker}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").tail(days).reset_index(drop=True)
    except Exception:
        return None


def _yf_ohlcv_fallback(ticker: str, days: int = 250) -> pd.DataFrame | None:
    try:
        import yfinance as yf
        period = "1y" if days <= 250 else "2y"
        for suffix in (".TW", ".TWO"):
            t = yf.Ticker(f"{ticker}{suffix}")
            h = t.history(period=period, auto_adjust=False)
            if h.empty:
                continue
            df = pd.DataFrame({
                "date": pd.to_datetime(h.index).tz_localize(None),
                "open": h["Open"].astype(float),
                "high": h["High"].astype(float),
                "low": h["Low"].astype(float),
                "close": h["Close"].astype(float),
                "volume": h["Volume"].astype(float),
            }).reset_index(drop=True)
            return df.tail(days).reset_index(drop=True)
    except Exception:
        pass
    return None


def _calc_tech(df: pd.DataFrame | None) -> dict | None:
    if df is None or len(df) < 60:
        return None
    df = df.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    n = 9
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rsv = ((df["close"] - low_n) / (high_n - low_n) * 100).fillna(50)
    df["k"] = rsv.ewm(alpha=1/3, adjust=False).mean()
    df["d"] = df["k"].ewm(alpha=1/3, adjust=False).mean()
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))
    last = df.iloc[-1]

    def num(x, default=0):
        return float(x) if pd.notna(x) else default

    return {
        "price": num(last["close"]),
        "ma5": num(last["ma5"]),
        "ma20": num(last["ma20"]),
        "ma60": num(last["ma60"]),
        "ma200": num(last["ma200"]),
        "rsi": num(last["rsi"], 50),
        "k": num(last["k"], 50),
        "d": num(last["d"], 50),
    }


def _load_chip(ticker: str) -> dict | None:
    """讀法人 — 優先 FinMind cache,fallback TWSE 官方."""
    # 1) FinMind cache
    p = FINMIND_INST / f"{ticker}.parquet"
    if p.exists():
        try:
            df = pd.read_parquet(p)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").tail(120)
            df["net"] = df["buy"] - df["sell"]
            last_dates = df["date"].unique()[-20:]
            sub = df[df["date"].isin(last_dates)]
            agg = sub.groupby("name")["net"].sum() / 1000  # → 張
            f = int(agg.get("Foreign_Investor", 0))
            i = int(agg.get("Investment_Trust", 0))
            d = int(agg.get("Dealer_self", 0))
            # 若 FinMind 有資料就用,否則 fall through 到 TWSE
            if abs(f) + abs(i) + abs(d) > 0:
                return {
                    "foreign_20d": f, "invtrust_20d": i, "dealer_20d": d,
                    "source": "finmind",
                }
        except Exception:
            pass

    # 2) TWSE rolling cache fallback
    twse_data = twse_cache.get_inst_20d(ticker)
    if twse_data:
        # TWSE 已是「股數」單位,÷ 1000 → 張數對齊 FinMind
        return {
            "foreign_20d": twse_data["foreign_net_20d"] // 1000,
            "invtrust_20d": twse_data["inv_trust_net_20d"] // 1000,
            "dealer_20d": twse_data["dealer_net_20d"] // 1000,
            "source": "twse",
            "asof": twse_data.get("latest_date"),
            "days": twse_data.get("days"),
        }
    return None


def _load_funda(ticker: str) -> dict:
    """讀月營收 + PER cache — PER 缺值 fallback TWSE."""
    out: dict = {}
    # 1) FinMind PER cache
    per_p = FINMIND_PER / f"{ticker}_per.parquet"
    if per_p.exists():
        try:
            df = pd.read_parquet(per_p)
            df["date"] = pd.to_datetime(df["date"])
            latest = df.sort_values("date").iloc[-1]
            for k, dst in (("PER", "per"), ("PBR", "pbr"),
                            ("dividend_yield", "yield")):
                v = latest.get(k)
                if pd.notna(v):
                    out[dst] = float(v)
        except Exception:
            pass

    # 2) TWSE fallback:若 per/pbr/yield 缺,從 TWSE 補
    missing = [k for k in ("per", "pbr", "yield") if k not in out]
    if missing:
        twse_per = twse_cache.get_per_latest(ticker)
        if twse_per:
            if "per" in missing and twse_per.get("per") is not None:
                out["per"] = twse_per["per"]
                out["per_source"] = "twse"
            if "pbr" in missing and twse_per.get("pbr") is not None:
                out["pbr"] = twse_per["pbr"]
            if "yield" in missing and twse_per.get("dividend_yield") is not None:
                out["yield"] = twse_per["dividend_yield"]
    # 月營收 YoY
    rev_p = FINMIND_REV / f"{ticker}.parquet"
    if rev_p.exists():
        try:
            df = pd.read_parquet(rev_p)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            df["yoy"] = df["revenue"].pct_change(12) * 100
            latest_yoy = df["yoy"].iloc[-1]
            if pd.notna(latest_yoy):
                out["rev_yoy"] = float(latest_yoy)
            # history 12 期 for chart
            tail12 = df.tail(12)
            out["rev_history"] = [
                {
                    "month": d.strftime("%Y/%m"),
                    "rev_yi": float(r) / 100_000_000 if pd.notna(r) else 0,
                    "yoy": float(y) if pd.notna(y) else 0,
                }
                for d, r, y in zip(tail12["date"], tail12["revenue"], tail12["yoy"])
            ]
        except Exception:
            pass
    return out


class HealthCheckOut(BaseModel):
    ticker: str
    name: str
    industry: str
    quote: dict
    health: dict
    tech: dict | None         # MA5/20/60/200 + RSI + K + D
    chip: dict | None         # 外資/投信/自營 20d net
    funda: dict               # PER/PBR/yield/rev_yoy + rev_history[]
    ohlcv_60d: list[dict]     # 過去 60 日 [{date, close, ma20, ma60}]
    sparkline: list[float]    # 20 日 close (legacy)
    has_full_data: bool


@router.get("/health-check/{ticker}", response_model=HealthCheckOut)
def health_check(ticker: str):
    info = get_ticker_info(ticker) or {"name": "", "industry": ""}
    quote = fetch_quote(ticker)
    if not quote:
        raise HTTPException(status_code=404, detail=f"ticker {ticker} not found")

    ohlcv = _load_local_ohlcv(ticker, days=250)
    has_full = ohlcv is not None
    if not has_full:
        ohlcv = _yf_ohlcv_fallback(ticker, days=250)

    tech = _calc_tech(ohlcv) if ohlcv is not None else None
    chip = _load_chip(ticker)
    funda = _load_funda(ticker)

    # 抓近期新聞 title list 給 news score 用
    news_titles: list[str] = []
    try:
        from backend.api.news import _fetch_rss
        name = info.get("name", "") or ""
        query = f"{ticker} {name}".strip()
        news_items = _fetch_rss(query, max_n=10)
        # _fetch_rss 回 List[NewsItem],pydantic obj
        for n in news_items[:10]:
            t = getattr(n, "title", None) or (n.get("title") if isinstance(n, dict) else "")
            if t:
                news_titles.append(t)
    except Exception:
        pass

    health = calc_composite_health(
        tech, chip,
        {k: funda.get(k) for k in ("per", "pbr", "yield", "rev_yoy")} if funda else None,
        news_titles or None,
    )

    # 60 日 OHLCV + MA(供前端 chart)
    ohlcv_60d: list[dict] = []
    if ohlcv is not None:
        df_chart = ohlcv.tail(60).copy()
        df_chart["ma20"] = ohlcv["close"].rolling(20).mean().tail(60)
        df_chart["ma60"] = ohlcv["close"].rolling(60).mean().tail(60)
        for _, r in df_chart.iterrows():
            ohlcv_60d.append({
                "date": r["date"].strftime("%Y-%m-%d"),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(r["volume"]) if pd.notna(r["volume"]) else 0,
                "ma20": float(r["ma20"]) if pd.notna(r["ma20"]) else 0,
                "ma60": float(r["ma60"]) if pd.notna(r["ma60"]) else 0,
            })

    spark = [float(c) for c in ohlcv["close"].tail(20)] if ohlcv is not None else []

    return HealthCheckOut(
        ticker=ticker,
        name=info["name"],
        industry=info["industry"],
        quote={
            "price": quote["price"],
            "prev_close": quote["prev_close"],
            "change_pct": quote["change_pct"],
            "asof": quote["asof"],
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "volume": quote["volume"],
        },
        health=health,
        tech=tech,
        chip=chip,
        funda=funda,
        ohlcv_60d=ohlcv_60d,
        sparkline=spark,
        has_full_data=has_full,
    )
