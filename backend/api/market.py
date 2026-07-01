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


_YF_SESSION = None


def _yf_session():
    """curl_cffi + Chrome 指紋 session — 繞 Yahoo Akamai 反爬."""
    global _YF_SESSION
    if _YF_SESSION is not None:
        return _YF_SESSION
    try:
        from curl_cffi import requests as _cc
        _YF_SESSION = _cc.Session(impersonate="chrome")
    except Exception:
        _YF_SESSION = False
    return _YF_SESSION


# Stooq symbol map — yfinance blocked 時走 Stooq
_STOOQ_MAP = {
    "^GSPC":     "^spx",     # S&P 500
    "^IXIC":     "^ndx",     # Nasdaq 100 (closest — Nasdaq Composite 沒有)
    "^SOX":      "^sox",     # 費城半導體
    "DX-Y.NYB":  "dx.f",     # 美元指數 futures
    "^N225":     "^nkx",     # 日經 225
    "GC=F":      "gc.f",     # 黃金 futures
    "CL=F":      "cl.f",     # WTI 原油
    "SI=F":      "si.f",     # 白銀 futures
    "TWD=X":     "usdtwd",   # USD/TWD
    "BTC-USD":   "btc.v",    # BTC
    "ETH-USD":   "eth.v",    # ETH
    "^VIX":      "^vix",     # VIX
    "DXJ":       None,       # WisdomTree Japan Hedged — Stooq 沒
}


def _fetch_stooq(symbol: str, name: str) -> MarketIndex | None:
    """Stooq CSV fallback — 目前有 JS challenge 擋,保留 placeholder 之後接別 API."""
    return None


def _fetch_coingecko(coin_id: str, symbol: str, name: str) -> MarketIndex | None:
    """CoinGecko 免費 API — BTC / ETH / 其他加密貨幣."""
    try:
        import requests as _rq
        from datetime import date as _date
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_change=true"
        r = _rq.get(url, timeout=10)
        if r.status_code != 200:
            return None
        d = r.json().get(coin_id, {})
        if not d:
            return None
        return MarketIndex(
            symbol=symbol, name=name,
            price=float(d["usd"]),
            change_pct=round(float(d.get("usd_24h_change", 0)), 2),
            asof=str(_date.today()),
        )
    except Exception as e:
        print(f"[coingecko] {coin_id}: {e}")
        return None


_SNAPSHOT_CACHE: tuple[float, dict] | None = None
_SNAPSHOT_TTL = 300  # 5 分鐘 cache


def _fetch_from_supabase_snapshot(symbol: str, name: str) -> MarketIndex | None:
    """從 Supabase market_snapshot 讀本地 script upsert 的資料."""
    global _SNAPSHOT_CACHE
    import os as _os
    import time as _t
    # cache 命中
    if _SNAPSHOT_CACHE and (_t.time() - _SNAPSHOT_CACHE[0]) < _SNAPSHOT_TTL:
        indices = _SNAPSHOT_CACHE[1]
    else:
        # 讀 secrets — 跟 ai.py 同套 fallback
        sb_url = _os.getenv("SUPABASE_URL", "")
        sb_anon = _os.getenv("SUPABASE_ANON_KEY", "")
        if (not sb_url or not sb_anon):
            from pathlib import Path as _P
            secrets = _P(__file__).resolve().parents[2] / ".streamlit" / "secrets.toml"
            if secrets.exists():
                for line in secrets.read_text(encoding="utf-8").splitlines():
                    s = line.strip()
                    if s.startswith("SUPABASE_URL") and "=" in s:
                        sb_url = s.split("=", 1)[1].strip().strip('"').strip("'")
                    elif s.startswith("SUPABASE_ANON_KEY") and "=" in s:
                        sb_anon = s.split("=", 1)[1].strip().strip('"').strip("'")
        if not sb_url or not sb_anon:
            return None
        try:
            from supabase import create_client
            sb = create_client(sb_url, sb_anon)
            r = sb.table("market_snapshot").select("data").eq("id", 1).execute()
            if not r.data:
                return None
            indices = r.data[0]["data"].get("indices", {})
            _SNAPSHOT_CACHE = (_t.time(), indices)
        except Exception as e:
            print(f"[snapshot] {e}")
            return None

    d = indices.get(symbol)
    if not d:
        return None
    return MarketIndex(
        symbol=symbol, name=name,
        price=float(d["price"]),
        change_pct=float(d["change_pct"]),
        asof=str(d["asof"]),
    )


def _fetch_er_fx(target: str, symbol: str, name: str) -> MarketIndex | None:
    """open.er-api.com 免費 FX — 支援 TWD/JPY/所有貨幣,免 key.
    只有現價無歷史,chg_pct 我們算不出來就給 0."""
    try:
        import requests as _rq
        from datetime import date as _date
        r = _rq.get("https://open.er-api.com/v6/latest/USD", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        rate = data.get("rates", {}).get(target)
        if not rate:
            return None
        return MarketIndex(
            symbol=symbol, name=name,
            price=float(rate),
            change_pct=0.0,   # 免費版無歷史,顯示 0
            asof=str(_date.today()),
        )
    except Exception as e:
        print(f"[er-api] USD/{target}: {e}")
        return None


def _fetch_yf_index(symbol: str, name: str) -> MarketIndex | None:
    try:
        import math
        import yfinance as yf
        sess = _yf_session()
        t = yf.Ticker(symbol, session=sess) if sess else yf.Ticker(symbol)
        h = t.history(period="10d", auto_adjust=False)
        if h.empty:
            # yfinance 空 → 落 Supabase snapshot
            return _fetch_from_supabase_snapshot(symbol, name)
        close_raw = h["Close"].iloc[-1]
        if close_raw is None or (isinstance(close_raw, float) and math.isnan(close_raw)):
            return _fetch_from_supabase_snapshot(symbol, name)
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
        # yfinance exception → 落 Supabase snapshot
        return _fetch_from_supabase_snapshot(symbol, name)


def _fetch_taiex_from_twse() -> pd.DataFrame | None:
    """從 TWSE 官方 FMTQIK API 抓 TAIEX 每日收盤 — yfinance fallback.
    每次 request 一整月,~13 個 request 拿到 250 交易日,足夠 MA200."""
    try:
        import requests as _rq
        from datetime import date as _date, timedelta as _td
        rows: list[dict] = []
        today = _date.today()
        cur = today.replace(day=1)
        # 抓最近 13 個月(足夠 200 交易日)
        for i in range(13):
            url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={cur.strftime('%Y%m%d')}&response=json"
            try:
                r = _rq.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    data = r.json()
                    for row in data.get("data", []):
                        # row = [日期(民國), 成交股數, 成交金額, 成交筆數, 收盤指數, 漲跌]
                        try:
                            # 民國轉西元: 115/06/26 → 2026-06-26
                            dt_raw = row[0]
                            yy, mm, dd = dt_raw.split("/")
                            dt_str = f"{int(yy) + 1911}-{int(mm):02d}-{int(dd):02d}"
                            close = float(str(row[4]).replace(",", ""))
                            rows.append({"date": dt_str, "close": close})
                        except (ValueError, IndexError):
                            pass
            except Exception:
                pass
            # 上個月 1 號
            cur = (cur - _td(days=1)).replace(day=1)
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date")
        return df.reset_index(drop=True) if not df.empty else None
    except Exception as e:
        print(f"[taiex_twse] {e}")
        return None


def _fetch_taiex_full() -> TaiexFull | None:
    """TAIEX 完整 — 含 MA200 距、20d 60d return、30d sparkline、溫度判讀."""
    # 1) 先試 yfinance
    h = None
    try:
        import yfinance as yf
        t = yf.Ticker("^TWII")
        h = t.history(period="2y", auto_adjust=False)
        if h.empty:
            h = None
    except Exception:
        h = None

    # 2) yfinance fail → TWSE 官方 API
    if h is None:
        twse_df = _fetch_taiex_from_twse()
        if twse_df is None or twse_df.empty:
            return None
        # 轉成 yfinance 相同的 shape
        h = pd.DataFrame({"Close": twse_df["close"].values},
                         index=pd.DatetimeIndex(twse_df["date"]))

    try:
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


import time as _time
from concurrent.futures import ThreadPoolExecutor as _TPE

_DASHBOARD_CACHE: tuple[float, MarketDashboard] | None = None
_DASHBOARD_TTL = 120  # 2 分鐘 — 國際 index 分鐘級變動無感


@router.get("/market/dashboard", response_model=MarketDashboard)
def market_dashboard():
    global _DASHBOARD_CACHE
    # 60 秒內熱資料直接回,不再打 yfinance
    if _DASHBOARD_CACHE and (_time.time() - _DASHBOARD_CACHE[0]) < _DASHBOARD_TTL:
        return _DASHBOARD_CACHE[1]

    # 平行抓 14 個 yfinance(單線程要 ~40s → 平行 ~5s)
    symbols = [
        ("sp500", "^GSPC", "S&P 500"),
        ("nasdaq", "^IXIC", "NASDAQ"),
        ("sox", "^SOX", "費城半導體"),
        ("dxy", "DX-Y.NYB", "美元指數"),
        ("dxj", "DXJ", "日股 DXJ"),
        ("nikkei", "^N225", "日經 225"),
        ("gold", "GC=F", "黃金"),
        ("oil", "CL=F", "WTI 原油"),
        ("silver", "SI=F", "白銀"),
        ("usdtwd", "TWD=X", "USD/TWD"),
        ("btc", "BTC-USD", "BTC"),
        ("eth", "ETH-USD", "ETH"),
        ("vix", "^VIX", "美股恐慌指數"),
    ]
    with _TPE(max_workers=10) as ex:
        futs = {key: ex.submit(_fetch_yf_index, sym, name) for key, sym, name in symbols}
        taiex_fut = ex.submit(_fetch_taiex_full)
        inst_fut = ex.submit(_fetch_institutional_total)
        # 免 key 的 fallback,不依賴 Yahoo
        btc_fut = ex.submit(_fetch_coingecko, "bitcoin", "BTC-USD", "BTC")
        eth_fut = ex.submit(_fetch_coingecko, "ethereum", "ETH-USD", "ETH")
        usdtwd_fut = ex.submit(_fetch_er_fx, "TWD", "TWD=X", "USD/TWD")
        usdjpy_fut = ex.submit(_fetch_er_fx, "JPY", "JPY=X", "USD/JPY")

        intl = {key: f.result() for key, f in futs.items()}
        # 覆蓋 yfinance 空的欄位
        if not intl.get("btc"):
            intl["btc"] = btc_fut.result()
        if not intl.get("eth"):
            intl["eth"] = eth_fut.result()
        if not intl.get("usdtwd"):
            intl["usdtwd"] = usdtwd_fut.result()
        taiex = taiex_fut.result()
        inst = inst_fut.result()

    result = MarketDashboard(
        taiex=taiex,
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
        institutional=inst,
        international_note=_intl_note(intl),
    )
    _DASHBOARD_CACHE = (_time.time(), result)
    return result
