"""Quote fetcher — TWSE mis 即時優先,yfinance 15min delay fallback."""
from __future__ import annotations

from functools import lru_cache
from time import time

import yfinance as yf

from backend.lib import twse_quote


# session-level cache(15 分鐘 TTL)— production 走 redis 之後再換
_CACHE: dict[str, tuple[float, dict]] = {}
TTL = 900  # 15 min


def _from_cache(tk: str):
    if tk in _CACHE:
        ts, data = _CACHE[tk]
        if time() - ts < TTL:
            return data
        del _CACHE[tk]
    return None


def _to_cache(tk: str, data: dict):
    _CACHE[tk] = (time(), data)


def fetch_quote(ticker: str) -> dict | None:
    """單檔 quote — TWSE 即時優先,yfinance fallback."""
    cached = _from_cache(ticker)
    if cached:
        return cached

    # 1) TWSE mis 即時(平日 9:00-13:30 真即時,盤後也有最後價)
    try:
        rt = twse_quote.fetch_realtime(ticker)
        if rt and rt.get("price"):
            _to_cache(ticker, rt)
            return rt
    except Exception:
        pass

    # 2) yfinance fallback
    for suffix in (".TW", ".TWO"):
        try:
            t = yf.Ticker(f"{ticker}{suffix}")
            h = t.history(period="5d", auto_adjust=False)
            if h.empty:
                continue
            close = float(h["Close"].iloc[-1])
            prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else close
            data = {
                "ticker": ticker,
                "price": close,
                "open": float(h["Open"].iloc[-1]),
                "high": float(h["High"].iloc[-1]),
                "low": float(h["Low"].iloc[-1]),
                "volume": int(h["Volume"].iloc[-1]),
                "prev_close": prev,
                "change_pct": round((close / prev - 1) * 100, 2) if prev else 0.0,
                "asof": h.index[-1].strftime("%Y-%m-%d"),
                "suffix": suffix,
            }
            _to_cache(ticker, data)
            return data
        except Exception:
            continue
    return None


def fetch_quotes_batch(tickers: list[str]) -> dict[str, dict]:
    """批次 quote — TWSE 即時批次優先,yfinance 補救."""
    if not tickers:
        return {}
    todo = [t for t in tickers if _from_cache(t) is None]
    out = {t: _from_cache(t) for t in tickers if _from_cache(t)}

    if not todo:
        return out

    # 1) TWSE mis 即時批次(一次最多 50 檔)
    try:
        twse_results = twse_quote.fetch_realtime_batch(todo)
        for tk, data in twse_results.items():
            if data and data.get("price"):
                _to_cache(tk, data)
                out[tk] = data
        # 剩下沒回的,留給 yfinance 補
        todo = [t for t in todo if t not in out]
    except Exception as e:
        print(f"[fetch_quotes_batch twse] {e}")

    if not todo:
        return out

    try:
        sym_tw = " ".join(f"{t}.TW" for t in todo)
        df = yf.download(sym_tw, period="5d", interval="1d", auto_adjust=False,
                          progress=False, threads=True, group_by="ticker")
        retry = []
        for t in todo:
            try:
                sub = df[f"{t}.TW"] if len(todo) > 1 else df
                if sub.empty or sub["Close"].dropna().empty:
                    retry.append(t)
                    continue
                clean = sub.dropna(subset=["Close"])
                close = float(clean["Close"].iloc[-1])
                prev = float(clean["Close"].iloc[-2]) if len(clean) >= 2 else close
                data = {
                    "ticker": t,
                    "price": close,
                    "open": float(clean["Open"].iloc[-1]),
                    "high": float(clean["High"].iloc[-1]),
                    "low": float(clean["Low"].iloc[-1]),
                    "volume": int(clean["Volume"].iloc[-1]),
                    "prev_close": prev,
                    "change_pct": round((close / prev - 1) * 100, 2) if prev else 0.0,
                    "asof": clean.index[-1].strftime("%Y-%m-%d"),
                    "suffix": ".TW",
                }
                _to_cache(t, data)
                out[t] = data
            except Exception:
                retry.append(t)
        # .TWO 補救
        if retry:
            sym_two = " ".join(f"{t}.TWO" for t in retry)
            df2 = yf.download(sym_two, period="5d", interval="1d", auto_adjust=False,
                                progress=False, threads=True, group_by="ticker")
            for t in retry:
                try:
                    sub = df2[f"{t}.TWO"] if len(retry) > 1 else df2
                    if sub.empty or sub["Close"].dropna().empty:
                        continue
                    clean = sub.dropna(subset=["Close"])
                    close = float(clean["Close"].iloc[-1])
                    prev = float(clean["Close"].iloc[-2]) if len(clean) >= 2 else close
                    data = {
                        "ticker": t,
                        "price": close,
                        "open": float(clean["Open"].iloc[-1]),
                        "high": float(clean["High"].iloc[-1]),
                        "low": float(clean["Low"].iloc[-1]),
                        "volume": int(clean["Volume"].iloc[-1]),
                        "prev_close": prev,
                        "change_pct": round((close / prev - 1) * 100, 2) if prev else 0.0,
                        "asof": clean.index[-1].strftime("%Y-%m-%d"),
                        "suffix": ".TWO",
                    }
                    _to_cache(t, data)
                    out[t] = data
                except Exception:
                    continue
    except Exception as e:
        print(f"[fetch_quotes_batch] {e}")

    # 3) 最後一道防線:批次仍漏的 ticker 逐檔 fetch_quote(用單檔邏輯)
    final_miss = [t for t in tickers if t not in out]
    for t in final_miss:
        try:
            data = fetch_quote(t)
            if data:
                out[t] = data
        except Exception as e:
            print(f"[fetch_quotes_batch final_fallback {t}] {e}")
    return out
