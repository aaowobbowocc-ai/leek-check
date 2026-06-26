"""TWSE mis.twse.com.tw 即時報價 — 取代 yfinance 15min delay.

API: https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw|otc_4148.tw
回 JSON,欄位:
  z:  最新成交價(若 "-" 表示尚未成交)
  y:  昨收價
  v:  累計成交量(張)
  o:  開盤價
  h:  當日最高
  l:  當日最低
  d:  日期 YYYYMMDD
  t:  時間 HH:MM:SS
  n:  證券名稱
  c:  股票代號
  tv: 單筆成交量(瞬時)
  a:  五檔賣價(底線分隔)
  b:  五檔買價

限制:
- 盤中(9:00-13:30 平日)才有最新價,盤後一段時間後 z 變成最後價
- 5 秒內重複 query 同 ticker 可能被 throttle
- 每次最多 100 檔 (我們限 50 防呆)
"""
from __future__ import annotations

import time
from typing import Any

import requests

# session 級快取(2 秒 TTL — 即時報價更新很頻繁)
_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}
TTL = 2.0


def _from_cache(tk: str) -> dict | None:
    if tk in _QUOTE_CACHE:
        ts, data = _QUOTE_CACHE[tk]
        if time.time() - ts < TTL:
            return data
    return None


def _to_cache(tk: str, data: dict):
    _QUOTE_CACHE[tk] = (time.time(), data)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://mis.twse.com.tw/stock/index.jsp",
}


def _parse_one(item: dict) -> dict | None:
    """把 mis.twse 回的 dict 轉成我們的標準格式."""
    code = item.get("c")
    if not code:
        return None
    z_raw = item.get("z", "-")
    y_raw = item.get("y", "0")
    # "-" 表示尚未成交,用昨收
    try:
        price = float(z_raw) if z_raw and z_raw != "-" else float(y_raw)
        prev = float(y_raw) if y_raw and y_raw != "-" else price
        change_pct = round((price / prev - 1) * 100, 2) if prev else 0.0
    except (ValueError, TypeError):
        return None
    try:
        op = float(item.get("o", "0") or "0") if (item.get("o") or "0") != "-" else price
        hi = float(item.get("h", "0") or "0") if (item.get("h") or "0") != "-" else price
        lo = float(item.get("l", "0") or "0") if (item.get("l") or "0") != "-" else price
        vol = int(float(item.get("v", "0") or "0"))
    except (ValueError, TypeError):
        op = hi = lo = price
        vol = 0
    d = item.get("d", "")
    t = item.get("t", "")
    return {
        "ticker": code,
        "price": price,
        "prev_close": prev,
        "change_pct": change_pct,
        "open": op,
        "high": hi,
        "low": lo,
        "volume": vol,
        "asof": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d,
        "time": t,
        "source": "twse_realtime",
        "name": item.get("n", "").strip(),
        "suffix": ".TW",
    }


def fetch_realtime(ticker: str) -> dict | None:
    """單檔即時 — 試 tse(上市)再 otc(上櫃)."""
    cached = _from_cache(ticker)
    if cached:
        return cached
    for prefix in ("tse", "otc"):
        try:
            url = (
                "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                f"?ex_ch={prefix}_{ticker}.tw&json=1&delay=0&_={int(time.time()*1000)}"
            )
            r = requests.get(url, headers=HEADERS, timeout=8)
            if r.status_code != 200:
                continue
            data = r.json()
            items = data.get("msgArray", [])
            if not items:
                continue
            parsed = _parse_one(items[0])
            if parsed:
                _to_cache(ticker, parsed)
                return parsed
        except Exception:
            continue
    return None


def fetch_realtime_batch(tickers: list[str]) -> dict[str, dict]:
    """批次 — 一次最多 50 檔,先 tse 再 otc 補救."""
    if not tickers:
        return {}
    out: dict[str, dict] = {}
    todo = []
    for tk in tickers:
        c = _from_cache(tk)
        if c:
            out[tk] = c
        else:
            todo.append(tk)
    if not todo:
        return out

    # 一次 batch 50 檔
    for chunk_start in range(0, len(todo), 50):
        chunk = todo[chunk_start:chunk_start + 50]
        ex_ch_tse = "|".join(f"tse_{t}.tw" for t in chunk)
        try:
            url = (
                "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                f"?ex_ch={ex_ch_tse}&json=1&delay=0&_={int(time.time()*1000)}"
            )
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                items = r.json().get("msgArray", [])
                for item in items:
                    parsed = _parse_one(item)
                    if parsed:
                        tk = parsed["ticker"]
                        out[tk] = parsed
                        _to_cache(tk, parsed)
        except Exception as e:
            print(f"[twse_realtime batch tse] {e}")

        # 上櫃補救
        miss = [t for t in chunk if t not in out]
        if miss:
            ex_ch_otc = "|".join(f"otc_{t}.tw" for t in miss)
            try:
                url2 = (
                    "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                    f"?ex_ch={ex_ch_otc}&json=1&delay=0&_={int(time.time()*1000)}"
                )
                r2 = requests.get(url2, headers=HEADERS, timeout=10)
                if r2.status_code == 200:
                    items = r2.json().get("msgArray", [])
                    for item in items:
                        parsed = _parse_one(item)
                        if parsed:
                            tk = parsed["ticker"]
                            parsed["suffix"] = ".TWO"
                            out[tk] = parsed
                            _to_cache(tk, parsed)
            except Exception as e:
                print(f"[twse_realtime batch otc] {e}")
    return out
