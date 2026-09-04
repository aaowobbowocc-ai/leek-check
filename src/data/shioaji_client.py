"""
Shioaji 客戶端 — 永豐金證券 API wrapper。

主要用途：
  1. 即時報價（盤中 streaming + 即時 snapshot）
  2. Tick data accumulation（每天 streaming 累積 → parquet 快取）
  3. （Phase 11+）下單與部位管理

認證：
  - API Key + Secret Key（從 e-Leader 取得，存 config/.env）
  - 報價 / 查詢：只需 API Key + Secret
  - 真實下單：另需憑證檔（.pfx，不在此模組處理）

環境模式：
  - simulation=True   : 連模擬環境（無風險，可測流程；fake 成交）
  - simulation=False  : 連正式環境（真實報價；要下單需憑證）

設計原則：
  - 連線失敗不拋例外，回傳 None 或空 dict（呼叫方自決）
  - 所有 ticker 統一用 4 碼字串（"2330" 不是 "2330.TW"）
  - 與 FugleClient / FinMindClient 同 pattern：可作為 AssetManager.price_fetcher 後端
  - Token 一律從 env vars 讀取，模組內絕不寫死

用法：
    from src.data.shioaji_client import ShioajiClient
    client = ShioajiClient(simulation=True)
    quote = client.get_snapshot("2330")
    print(quote)  # {"close": 808.0, "volume": 12345, "bid": 807.5, ...}
"""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "shioaji"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class ShioajiClient:
    """永豐金 Shioaji API 封裝。"""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        simulation: bool = True,
    ) -> None:
        self._api_key = api_key or os.environ.get("SHIOAJI_API_KEY", "")
        self._secret_key = secret_key or os.environ.get("SHIOAJI_SECRET_KEY", "")
        self._simulation = simulation
        self._api: Any = None      # lazy-init 避免 import 時連線
        self._connected = False

    # ─────────────────────────────────────
    # Connection management
    # ─────────────────────────────────────
    def connect(self) -> bool:
        """登入 Shioaji。回傳是否成功。"""
        if self._connected:
            return True
        if not self._api_key or not self._secret_key:
            logger.warning("SHIOAJI_API_KEY / SECRET_KEY 未設定，跳過 Shioaji 連線")
            return False

        try:
            import shioaji as sj  # type: ignore
        except ImportError:
            logger.warning("shioaji 套件未安裝（pip install shioaji）")
            return False

        try:
            self._api = sj.Shioaji(simulation=self._simulation)
            self._api.login(
                api_key=self._api_key,
                secret_key=self._secret_key,
                contracts_cb=lambda sec: logger.debug("contracts loaded: %s", sec),
            )
            self._connected = True
            mode = "SIMULATION" if self._simulation else "PRODUCTION"
            logger.info("Shioaji 連線成功 [%s]", mode)
            return True
        except Exception as exc:
            logger.error("Shioaji 連線失敗: %s", exc)
            return False

    def disconnect(self) -> None:
        """登出。"""
        if self._connected and self._api is not None:
            try:
                self._api.logout()
            except Exception as exc:
                logger.warning("Shioaji logout 失敗: %s", exc)
            finally:
                self._connected = False
                self._api = None

    # ─────────────────────────────────────
    # 即時報價
    # ─────────────────────────────────────
    def get_snapshot(self, ticker: str) -> dict | None:
        """
        取得最新即時 snapshot。
        回傳 dict: {close, volume, bid, ask, total_volume, ...}
        失敗回傳 None。
        """
        if not self.connect():
            return None
        try:
            contract = self._api.Contracts.Stocks[ticker]
            snapshots = self._api.snapshots([contract])
            if not snapshots:
                return None
            s = snapshots[0]
            return {
                "ticker": ticker,
                "close": float(s.close),
                "open": float(s.open),
                "high": float(s.high),
                "low": float(s.low),
                "volume": int(s.volume),
                "total_volume": int(s.total_volume),
                "bid": float(s.buy_price),
                "ask": float(s.sell_price),
                "bid_size": int(s.buy_volume),
                "ask_size": int(s.sell_volume),
                "amount": float(s.amount),
                "ts": s.ts,
            }
        except Exception as exc:
            logger.warning("Shioaji snapshot %s 失敗: %s", ticker, exc)
            return None

    def get_realtime_quote(self, ticker: str) -> float:
        """簡易回傳最新成交價（給 AssetManager.price_fetcher 用）。"""
        snap = self.get_snapshot(ticker)
        return float(snap["close"]) if snap else 0.0

    def as_price_fetcher(self) -> Callable[[str], float]:
        return self.get_realtime_quote

    # ─────────────────────────────────────
    # Tick streaming（給 daemon 累積 tick data 用）
    # ─────────────────────────────────────
    def subscribe_tick(self, ticker: str, callback: Callable[[Any, Any], None]) -> bool:
        """
        訂閱即時 tick。每筆成交呼叫 callback(exchange, tick)。

        Tick data 包含：
          - close, volume
          - bid_price, ask_price
          - tick_type: 1=外盤(主動買), 2=內盤(主動賣)
          - time

        callback 範例：
            def on_tick(exchange, tick):
                save_to_parquet(tick)
        """
        if not self.connect():
            return False
        try:
            contract = self._api.Contracts.Stocks[ticker]
            self._api.quote.subscribe(
                contract, quote_type="tick", callback=callback
            )
            logger.info("已訂閱 %s tick", ticker)
            return True
        except Exception as exc:
            logger.error("Shioaji subscribe %s 失敗: %s", ticker, exc)
            return False

    def unsubscribe_tick(self, ticker: str) -> None:
        if not self._connected:
            return
        try:
            contract = self._api.Contracts.Stocks[ticker]
            self._api.quote.unsubscribe(contract, quote_type="tick")
        except Exception as exc:
            logger.warning("Shioaji unsubscribe %s 失敗: %s", ticker, exc)

    # ─────────────────────────────────────
    # 短期 K 線（Shioaji 給散戶有限歷史，主要 backtest 仍用 FinMind）
    # ─────────────────────────────────────
    def get_kbars(self, ticker: str, start: date, end: date) -> list[dict]:
        """
        取 1 分鐘 K 線（Shioaji 散戶限約 30 日）。
        回傳 list of dict (date, minute, open, high, low, close, volume)。
        """
        if not self.connect():
            return []
        try:
            contract = self._api.Contracts.Stocks[ticker]
            kbars = self._api.kbars(
                contract, start=start.isoformat(), end=end.isoformat()
            )
            df = self._kbars_to_records(kbars)
            return df
        except Exception as exc:
            logger.warning("Shioaji kbars %s 失敗: %s", ticker, exc)
            return []

    @staticmethod
    def _kbars_to_records(kbars: Any) -> list[dict]:
        """Shioaji kbars 物件 → list[dict]。"""
        try:
            import pandas as pd
            df = pd.DataFrame({**kbars})
            df["ts"] = pd.to_datetime(df["ts"])
            df["date"] = df["ts"].dt.date
            df["minute"] = df["ts"].dt.strftime("%H:%M:%S")
            return df[["date", "minute", "Open", "High", "Low", "Close", "Volume"]].rename(
                columns={
                    "Open": "open", "High": "high",
                    "Low": "low", "Close": "close", "Volume": "volume",
                }
            ).to_dict("records")
        except Exception:
            return []

    # ─────────────────────────────────────
    # Context manager 支援
    # ─────────────────────────────────────
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
