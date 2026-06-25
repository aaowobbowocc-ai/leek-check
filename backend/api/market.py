"""大盤儀表板 — TAIEX 完整 + 法人 + 國際市場 9 樣 + 商品."""
from __future__ import annotations
import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel
from pathlib import Path

router = APIRouter(tags=["market"])

ROOT = Path(__file__).resolve().parents[2]
FINMIND_INST_TOTAL = ROOT / "data" / "cache" / "finmind" / "institutional_total.parquet"


class MarketIndex(BaseModel):
    symbol: str
    name: str
    price: float
    change_pct: float
    asof: str


class TaiexFull(BaseModel):
    price: float
    prev_close: float
    change_pct: float
    asof: str
    ma200_dist_pct: float | None
    ret_20d: float | None
    ret_60d: float | None
    sparkline_30d: list[float]
    temperature: str   # 過熱 / 正常 / 偏冷
    temperature_emoji: str


class InstitutionalSummary(BaseModel):
    foreign_20d: int     # 張數
    invtrust_20d: int
    dealer_20d: int
    note: str


class MarketDashboard(BaseModel):
    taiex: TaiexFull | None
    vix: MarketIndex | None
    sp500: MarketIndex | None
    nasdaq: MarketIndex | None
    sox: MarketIndex | None
    dxy: MarketIndex | None
    dxj: MarketIndex | None
    nikkei: MarketIndex | None
    gold: MarketIndex | None
    oil: MarketIndex | None
    silver: MarketIndex | None
    usdtwd: MarketIndex | None
    btc: MarketIndex | None
    eth: MarketIndex | None
    institutional: InstitutionalSummary | None
    international_note: str  # 國際市場連動文字判讀


def _fetch_yf_index(symbol: str, name: str) -> MarketIndex | None:
    try:
        import math
        import yfinance as yf
        t = yf.Ticker(symbol)
        h = t.history(period="10d", auto_adjust=False)
        if h.empty:
            return None
        close_raw = h["Close"].iloc[-1]
        if close_raw is None or (isinstance(close_raw, float) and math.isnan(close_raw)):
            return None
        close = float(close_raw)
        prev_raw = h["Close"].iloc[-2] if len(h) >= 2 else close_raw
        prev = float(prev_raw) if prev_raw and not math.isnan(prev_raw) else close
        chg = (close / prev - 1) * 100 if prev else 0.0
        return MarketIndex(
            symbol=symbol, name=name,
            price=close, change_pct=round(chg, 2),
            asof=h.index[-1].strftime("%Y-%m-%d"),
        )
    except Exception as e:
        print(f"[_fetch_yf_index] {symbol} failed: {e}")
        return None


def _fetch_taiex_full() -> TaiexFull | None:
    """TAIEX 完整 — 含 MA200 距、20d 60d return、30d sparkline、溫度判讀."""
    try:
        import yfinance as yf
        t = yf.Ticker("^TWII")
        h = t.history(period="2y", auto_adjust=False)
        if h.empty:
            return None
        h = h.dropna(subset=["Close"]).reset_index(drop=True)
        close = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else close
        chg = (close / prev - 1) * 100 if prev else 0
        ma200 = float(h["Close"].tail(200).mean()) if len(h) >= 200 else None
        ma200_dist = ((close / ma200) - 1) * 100 if ma200 else None
        ret_20d = ((close / float(h["Close"].iloc[-21])) - 1) * 100 if len(h) > 21 else None
        ret_60d = ((close / float(h["Close"].iloc[-61])) - 1) * 100 if len(h) > 61 else None
        spark = [float(x) for x in h["Close"].tail(30)]

        # 溫度判讀(基於 MA200 距)
        if ma200_dist is None:
            temp, emoji = "—", "❓"
        elif ma200_dist > 30:
            temp, emoji = "過熱", "🔥"
        elif ma200_dist > 15:
            temp, emoji = "偏熱", "🌡️"
        elif ma200_dist > -5:
            temp, emoji = "正常", "✅"
        elif ma200_dist > -20:
            temp, emoji = "偏冷", "❄️"
        else:
            temp, emoji = "深度低估", "🥶"

        return TaiexFull(
            price=close,
            prev_close=prev,
            change_pct=round(chg, 2),
            asof=str(h.index[-1].date()) if hasattr(h.index[-1], "date") else "",
            ma200_dist_pct=round(ma200_dist, 1) if ma200_dist is not None else None,
            ret_20d=round(ret_20d, 2) if ret_20d is not None else None,
            ret_60d=round(ret_60d, 2) if ret_60d is not None else None,
            sparkline_30d=spark,
            temperature=temp,
            temperature_emoji=emoji,
        )
    except Exception as e:
        print(f"[taiex_full] {e}")
        return None


def _fetch_institutional_total() -> InstitutionalSummary | None:
    """讀全市場 20 日法人累計(本地 cache)."""
    if not FINMIND_INST_TOTAL.exists():
        return None
    try:
        df = pd.read_parquet(FINMIND_INST_TOTAL)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").tail(60)
        df["net"] = df["buy"] - df["sell"]
        last_dates = df["date"].unique()[-20:]
        sub = df[df["date"].isin(last_dates)]
        agg = sub.groupby("name")["net"].sum()
        f = int(agg.get("Foreign_Investor", 0))
        i = int(agg.get("Investment_Trust", 0))
        d = int(agg.get("Dealer_self", 0))
        notes = []
        if f > 50_000: notes.append("🔴 外資強力買超")
        elif f < -50_000: notes.append("🟢 外資強力賣超")
        if i > 20_000: notes.append("🔴 投信積極加碼")
        elif i < -20_000: notes.append("🟢 投信明顯減碼")
        return InstitutionalSummary(
            foreign_20d=f, invtrust_20d=i, dealer_20d=d,
            note=" · ".join(notes) if notes else "📋 法人動向中性",
        )
    except Exception as e:
        print(f"[institutional_total] {e}")
        return None


def _intl_note(items: dict) -> str:
    """國際市場連動簡易判讀."""
    sp = items.get("sp500")
    nasdaq = items.get("nasdaq")
    if sp and nasdaq:
        if sp.change_pct > 0.5 and nasdaq.change_pct > 0.5:
            return "🟢 美股強勁 → 台股早盤大機率跟漲"
        if sp.change_pct < -0.5 and nasdaq.change_pct < -0.5:
            return "🔴 美股大跌 → 台股早盤恐承壓"
    return "⚪ 國際市場波動平穩,無明顯訊號"


@router.get("/market/dashboard", response_model=MarketDashboard)
def market_dashboard():
    intl = {
        "sp500":   _fetch_yf_index("^GSPC", "S&P 500"),
        "nasdaq":  _fetch_yf_index("^IXIC", "NASDAQ"),
        "sox":     _fetch_yf_index("^SOX", "費城半導體"),
        "dxy":     _fetch_yf_index("DX-Y.NYB", "美元指數"),
        "dxj":     _fetch_yf_index("DXJ", "日股 DXJ"),
        "nikkei":  _fetch_yf_index("^N225", "日經 225"),
        "gold":    _fetch_yf_index("GC=F", "黃金"),
        "oil":     _fetch_yf_index("CL=F", "WTI 原油"),
        "silver":  _fetch_yf_index("SI=F", "白銀"),
        "usdtwd":  _fetch_yf_index("TWD=X", "USD/TWD"),
        "btc":     _fetch_yf_index("BTC-USD", "BTC"),
        "eth":     _fetch_yf_index("ETH-USD", "ETH"),
        "vix":     _fetch_yf_index("^VIX", "美股恐慌指數"),
    }
    return MarketDashboard(
        taiex=_fetch_taiex_full(),
        vix=intl["vix"],
        sp500=intl["sp500"],
        nasdaq=intl["nasdaq"],
        sox=intl["sox"],
        dxy=intl["dxy"],
        dxj=intl["dxj"],
        nikkei=intl["nikkei"],
        gold=intl["gold"],
        oil=intl["oil"],
        silver=intl["silver"],
        usdtwd=intl["usdtwd"],
        btc=intl["btc"],
        eth=intl["eth"],
        institutional=_fetch_institutional_total(),
        international_note=_intl_note(intl),
    )
