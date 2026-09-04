"""Shioaji 即時報價 client (純讀價,**不下單**).

- Login once on first call, cache session
- TTL cache for snapshots (default 30s) so 多 ticker refresh 不重打 API
- TWSE + TPEx 自動辨識
- Free tier: simulation=False 但只用 read endpoints (不下單)
- Read-only — 即使 paper_mode=False 也只 query 不下單

Usage:
    from src.data.shioaji_quote import get_snapshot_price
    p = get_snapshot_price("2345")  # ~ NT$ 2,555 即時
"""
from __future__ import annotations
import os
import time
import threading
from typing import Optional

# Module-level singleton
_LOCK = threading.Lock()
_API = None
_CACHE: dict[str, tuple[float, float]] = {}  # ticker -> (price, ts)
_CACHE_TTL = 30.0  # seconds
_LOGIN_OK = False
_LOGIN_FAILED_AT = 0.0  # back-off if login fails


def _ensure_logged_in() -> bool:
    global _API, _LOGIN_OK, _LOGIN_FAILED_AT
    if _LOGIN_OK and _API is not None:
        return True
    # Back off 5 min after a failure to avoid hammering
    if time.time() - _LOGIN_FAILED_AT < 300:
        return False

    api_key = os.environ.get("SHIOAJI_API_KEY", "")
    secret  = os.environ.get("SHIOAJI_SECRET_KEY", "")
    if not api_key or not secret:
        _LOGIN_FAILED_AT = time.time()
        return False

    try:
        import shioaji as sj
    except ImportError:
        _LOGIN_FAILED_AT = time.time()
        return False

    try:
        with _LOCK:
            if _LOGIN_OK and _API is not None:
                return True
            # simulation=True: 下單是 mock,但 snapshot/quote 回傳真實市場資料
            # 不需要 production permission 即可即時報價
            api = sj.Shioaji(simulation=True)
            api.login(api_key=api_key, secret_key=secret, fetch_contract=True)
            _API = api
            _LOGIN_OK = True
        return True
    except Exception:
        _LOGIN_FAILED_AT = time.time()
        _LOGIN_OK = False
        _API = None
        return False


def _resolve_contract(ticker: str):
    """Find contract for ticker. Tries Stocks (TWSE + TPEx)."""
    if _API is None:
        return None
    try:
        # Shioaji Contracts.Stocks supports both TWSE + TPEx by ticker code
        return _API.Contracts.Stocks[ticker]
    except Exception:
        return None


def get_snapshot_price(ticker: str, ttl: float = _CACHE_TTL) -> Optional[float]:
    """Return latest snapshot close price, or None on failure.

    TTL-cached: same ticker won't hit API again within `ttl` seconds.
    """
    now = time.time()
    cached = _CACHE.get(ticker)
    if cached and now - cached[1] < ttl:
        return cached[0]

    if not _ensure_logged_in():
        return None

    contract = _resolve_contract(ticker)
    if contract is None:
        return None

    try:
        snaps = _API.snapshots([contract])
        if not snaps:
            return None
        s = snaps[0]
        # snapshot.close 是當前最新成交價(盤中即時、盤後收盤)
        price = float(getattr(s, "close", 0) or 0)
        if price <= 0:
            return None
        _CACHE[ticker] = (price, now)
        return price
    except Exception:
        return None


def is_available() -> bool:
    """Check if Shioaji is configured + login attempt would likely succeed."""
    return bool(os.environ.get("SHIOAJI_API_KEY")) and bool(os.environ.get("SHIOAJI_SECRET_KEY"))
