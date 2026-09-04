"""
Tick 微結構訊號計算 — 從 TaiwanStockPriceTick 抽取 daily / intraday metrics。

提供：
  - daily_metrics(ticker, date): 該日整體微結構指標
  - intraday_window_metrics(ticker, date, start_time, end_time): 指定時段指標
  - rolling_metrics(ticker, dates): 多日 rolling

訊號類型：
  M1. 內外盤比（內盤量 / 外盤量）— 散戶/大戶主動傾向
  M2. 內外盤量 z-score — 異常突發
  M3. 大單比例（≥1000 張單筆占比）— 大戶活躍度
  M4. 早盤 (09:00-09:15) 內外盤比 — 開盤動能方向
  M5. 尾盤 (13:00-13:30) 內外盤比 — 主力收盤前操作
  M6. VWAP 偏離 — 個別 tick 相對 VWAP 偏離度
  M7. 大單方向集中（連續 N 筆大單同向）— 主力指紋
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TICK_CACHE = ROOT / "data" / "cache" / "finmind" / "tick"


def load_tick_day(ticker: str, d: date) -> pd.DataFrame:
    """載入單日 tick，返回 None 若 cache 不存在或為空。"""
    cp = TICK_CACHE / f"{ticker}_{d.strftime('%Y%m%d')}.parquet"
    if not cp.exists():
        return pd.DataFrame()
    df = pd.read_parquet(cp)
    if "_empty" in df.columns:
        return pd.DataFrame()  # sentinel 空檔
    df["TickType"] = pd.to_numeric(df["TickType"], errors="coerce").fillna(0).astype(int)
    df["deal_price"] = pd.to_numeric(df["deal_price"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
    df["time_str"] = df["Time"].astype(str).str[:8]
    return df


def daily_metrics(ticker: str, d: date) -> dict:
    """單日微結構指標。"""
    df = load_tick_day(ticker, d)
    if df.empty:
        return {}

    # M1. 內外盤比（按成交量）
    type1_v = df[df["TickType"] == 1]["volume"].sum()  # 外盤（主動買）
    type2_v = df[df["TickType"] == 2]["volume"].sum()  # 內盤（主動賣）
    total_v = type1_v + type2_v
    if total_v <= 0:
        return {}
    inner_ratio = type2_v / total_v
    outer_ratio = type1_v / total_v
    io_ratio = type2_v / type1_v if type1_v > 0 else float("nan")  # 內/外比例

    # M3. 大單比例（≥ 1000 張）
    big_trades = df[df["volume"] >= 1000]
    big_volume = big_trades["volume"].sum()
    big_ratio = big_volume / total_v if total_v > 0 else 0
    big_count = len(big_trades)

    # M4. 早盤 09:00-09:15 內外盤比
    morning = df[df["time_str"].between("09:00:00", "09:14:59")]
    m_t1 = morning[morning["TickType"] == 1]["volume"].sum()
    m_t2 = morning[morning["TickType"] == 2]["volume"].sum()
    morning_inner = m_t2 / (m_t1 + m_t2) if (m_t1 + m_t2) > 0 else None

    # M5. 尾盤 13:00-13:30
    closing = df[df["time_str"].between("13:00:00", "13:30:00")]
    c_t1 = closing[closing["TickType"] == 1]["volume"].sum()
    c_t2 = closing[closing["TickType"] == 2]["volume"].sum()
    closing_inner = c_t2 / (c_t1 + c_t2) if (c_t1 + c_t2) > 0 else None

    # M6. VWAP
    df["dollar"] = df["deal_price"] * df["volume"]
    vwap = df["dollar"].sum() / total_v if total_v > 0 else 0

    # 收盤價（最後一筆）
    close_price = float(df.iloc[-1]["deal_price"])

    # 開盤價
    open_price = float(df.iloc[0]["deal_price"])

    # 高低
    high = float(df["deal_price"].max())
    low = float(df["deal_price"].min())

    return {
        "ticker": ticker,
        "date": d,
        "total_volume": int(total_v),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close_price,
        "vwap": float(vwap),
        # 內外盤
        "outer_volume": int(type1_v),
        "inner_volume": int(type2_v),
        "inner_ratio": float(inner_ratio),       # 內盤量占比 (0~1)
        "outer_ratio": float(outer_ratio),
        "io_ratio": float(io_ratio) if not np.isnan(io_ratio) else None,  # 內/外
        # 大單
        "big_volume": int(big_volume),
        "big_ratio": float(big_ratio),
        "big_count": int(big_count),
        # 時段
        "morning_inner_ratio": float(morning_inner) if morning_inner else None,
        "closing_inner_ratio": float(closing_inner) if closing_inner else None,
        # 偏離度
        "close_vs_vwap_pct": float((close_price / vwap - 1) * 100) if vwap > 0 else 0,
    }


def rolling_daily_metrics(ticker: str, start: date, end: date) -> pd.DataFrame:
    """掃描日期區間，產出 metrics DataFrame。"""
    rows = []
    cur = start
    from datetime import timedelta
    while cur <= end:
        if cur.weekday() < 5:
            m = daily_metrics(ticker, cur)
            if m:
                rows.append(m)
        cur += timedelta(days=1)
    return pd.DataFrame(rows)
