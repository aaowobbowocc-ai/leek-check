"""
回測資料視窗 — 計畫 §8.3 核心防線：look-ahead bias 防護。

所有回測內部讀取資料，都必須透過 `HistoricalDataView.at(simulated_today)` 取得切片；
切片保證：
  - 只回傳 date < simulated_today 的列
  - 原始 dataframe 不被修改（使用 copy）

任何試圖繞過 view 直接 index 原始 df 的程式都視為 bug，
會在 Phase 8 最後階段加 linter 檢查（grep 未來比較運算子）。

---

設計：
  - 原始 dataframe 不動（記憶體共享）
  - at() 回傳子類 `DailySnapshot`，提供便利存取器
  - 所有存取器都會用 `date_col < cutoff` 過濾
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class DailySnapshot:
    """單一模擬日的視窗 — 所有資料都嚴格 < cutoff。"""

    cutoff: date
    ohlcv_by_ticker: dict[str, pd.DataFrame]
    institutional_by_ticker: dict[str, pd.DataFrame]
    broker_by_ticker: dict[str, pd.DataFrame]
    taiex: pd.DataFrame
    overnight: dict        # OvernightReport（前一美股交易日收盤）

    def ohlcv(self, ticker: str) -> pd.DataFrame:
        return _slice_before(self.ohlcv_by_ticker.get(ticker, pd.DataFrame()), self.cutoff)

    def institutional(self, ticker: str) -> pd.DataFrame:
        return _slice_before(
            self.institutional_by_ticker.get(ticker, pd.DataFrame()), self.cutoff
        )

    def broker_on(self, ticker: str, d: date) -> pd.DataFrame:
        """取特定日（通常 = cutoff − 1 營業日）的分點。"""
        df = self.broker_by_ticker.get(ticker, pd.DataFrame())
        if df.empty or "date" not in df:
            return pd.DataFrame()
        mask = pd.to_datetime(df["date"]).dt.date == d
        return df.loc[mask].copy()

    def prev_close(self, ticker: str) -> float:
        df = self.ohlcv(ticker)
        if df.empty:
            return 0.0
        return float(df.sort_values("date").iloc[-1]["close"])

    def taiex_window(self) -> pd.DataFrame:
        return _slice_before(self.taiex, self.cutoff)


class HistoricalDataView:
    def __init__(
        self,
        ohlcv_by_ticker: dict[str, pd.DataFrame],
        institutional_by_ticker: dict[str, pd.DataFrame],
        broker_by_ticker: dict[str, pd.DataFrame],
        taiex: pd.DataFrame,
        overnight_by_date: dict[date, dict],
    ) -> None:
        self._ohlcv = {k: _normalize_dates(v) for k, v in ohlcv_by_ticker.items()}
        self._inst = {k: _normalize_dates(v) for k, v in institutional_by_ticker.items()}
        self._broker = {k: _normalize_dates(v) for k, v in broker_by_ticker.items()}
        self._taiex = _normalize_dates(taiex)
        self._overnight = overnight_by_date

    def at(self, simulated_today: date) -> DailySnapshot:
        overnight = self._overnight.get(simulated_today) or _empty_overnight()
        return DailySnapshot(
            cutoff=simulated_today,
            ohlcv_by_ticker=self._ohlcv,
            institutional_by_ticker=self._inst,
            broker_by_ticker=self._broker,
            taiex=self._taiex,
            overnight=overnight,
        )

    def bar(self, ticker: str, d: date) -> dict | None:
        """
        取某 ticker 在 date=d 那天的 OHLCV（僅供「模擬成交」使用，禁止用來判斷分數）。
        回測 engine 使用這個取得模擬日的 high/low 判斷是否觸發止損止盈。
        """
        df = self._ohlcv.get(ticker)
        if df is None or df.empty:
            return None
        mask = pd.to_datetime(df["date"]).dt.date == d
        row = df.loc[mask]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            "date": d,
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]),
        }


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "date" not in df:
        return df
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    return out


def _slice_before(df: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    if df.empty or "date" not in df:
        return df
    mask = pd.to_datetime(df["date"]).dt.date < cutoff
    return df.loc[mask].copy()


def _empty_overnight() -> dict:
    return {
        "as_of_date": "",
        "tsmc_adr_close": float("nan"),
        "tsmc_adr_change_pct": 0.0,
        "nvda_close": float("nan"),
        "nvda_change_pct": 0.0,
        "sox_close": float("nan"),
        "sox_change_pct": 0.0,
        "vix": 15.0,
        "market_mode": "normal",
    }
