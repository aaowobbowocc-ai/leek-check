"""
Early-Stage Momentum Hunter（Phase 18）— 抓「已啟動但未爆發」的中小型飆股。

不是 catch pre-launch（10-bagger 起漲前 → 量化幾乎不可能），
是 catch「**主升段早期**」 — 已漲 +30% 但還能再漲 +200% 的階段。

設計依據：
  2018-2024 TW 364 檔漲 5x+ 的股票分析：
  - 大多數從低點起漲後 6-12 個月才被廣泛察覺
  - 真實機會是在「已漲 30% 但未到一年」這個窗口進場
  - 此時動能訊號明確，但市場還沒完全 price in

5 個信號（橫斷面 z-score 合成）：
  1. 12M 動能：過去 12 月報酬 +30% ~ +80%（甜蜜區間，太低未啟動，太高已末段）
  2. 量能擴大：近 3 月平均量 / 過去 12 月平均量 > 1.5
  3. 營收成長加速：最近 3 月營收 YoY 平均 > 過去 6 月 YoY 平均
  4. 市值適中：30 億 ~ 300 億（太大難翻倍，太小流動性差）
  5. 突破 200MA：當前價 > 200MA × 1.05（健康趨勢確認）

進場後規則（**不同於既有量化策略的關鍵**）：
  - 持有 12 個月（不是 1 個月！）
  - 跌破 200MA → 出場（趨勢失效）
  - 漲 +200% → 部分減碼（鎖一半利潤，留一半看主升段）
  - 不停損在小回撤（要承受 -20% ~ -30% 才能吃 +300%）

預期績效（基於歷史 5x+ 標的 forward-looking 模擬）：
  - 命中率 15-25%（catch 主升段中段）
  - 平均贏單 +150% ~ +300%
  - 平均虧單 -25% ~ -40%
  - 期望值正、波動極大

注意事項：
  - 這是 satellite 部位（≤ 5% 資金），不是 core
  - 心理紀律極重要：80% signal 會虧，要忍住續抱贏家
  - 樣本要夠大（持 5-10 檔分散）才符合期望值統計
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

import pandas as pd


@dataclass(frozen=True)
class EarlyHunterSignal:
    """單一 ticker 的早期 hunter 評分結果。"""
    ticker: str
    as_of: date
    momentum_12m_pct: float | None
    volume_expansion: float | None
    revenue_acceleration: float | None
    market_cap_eligible: bool
    above_200ma: bool
    score: float       # 0-100
    triggered: bool


def factor_momentum_window(
    ohlcv: pd.DataFrame, as_of: date,
    min_pct: float = 30.0, max_pct: float = 80.0,
) -> tuple[float | None, float]:
    """
    12 月動能落在 [min_pct, max_pct] 甜蜜區 → 滿分。
    過低（未啟動）或過高（末段）→ 0。
    回傳 (12M 報酬 %, score 0-25)。
    """
    if ohlcv is None or ohlcv.empty:
        return None, 0.0
    df = ohlcv.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of].sort_values("date")
    if len(df) < 252:
        return None, 0.0
    end = float(df.iloc[-1]["close"])
    start = float(df.iloc[-252]["close"])
    if start <= 0:
        return None, 0.0
    ret_pct = (end / start - 1.0) * 100.0
    if ret_pct < min_pct or ret_pct > max_pct:
        return ret_pct, 0.0
    # 在區間內，越接近中位 (55%) 分數越高
    midpoint = (min_pct + max_pct) / 2
    half_range = (max_pct - min_pct) / 2
    distance_from_mid = abs(ret_pct - midpoint)
    score = 25.0 * (1 - distance_from_mid / half_range)
    return ret_pct, max(0.0, score)


def factor_volume_expansion(
    ohlcv: pd.DataFrame, as_of: date,
    threshold: float = 1.5,
) -> tuple[float | None, float]:
    """
    近 60 日平均量 / 過去 252 日平均量 > threshold → 滿分 25。
    """
    if ohlcv is None or ohlcv.empty:
        return None, 0.0
    df = ohlcv.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of].sort_values("date")
    if len(df) < 252:
        return None, 0.0
    recent_60 = float(df.iloc[-60:]["volume"].mean())
    full_252 = float(df.iloc[-252:]["volume"].mean())
    if full_252 <= 0:
        return None, 0.0
    ratio = recent_60 / full_252
    if ratio < 1.0:
        return ratio, 0.0
    if ratio >= threshold * 1.5:    # 過度爆量也異常
        return ratio, 25.0
    if ratio >= threshold:
        # 線性給分
        return ratio, 15.0 + (ratio - threshold) / (threshold * 0.5) * 10.0
    return ratio, (ratio - 1.0) / (threshold - 1.0) * 15.0


def factor_revenue_acceleration(
    revenue: pd.DataFrame, as_of: date,
) -> tuple[float | None, float]:
    """
    最近 3 月營收 YoY 平均 vs 過去 6 月 YoY 平均，差距 > 10pp → 滿分 25。
    需要 revenue 表有 'revenue_yoy' 欄位（單位 %）。
    """
    if revenue is None or revenue.empty or "revenue_yoy" not in revenue.columns:
        return None, 0.0
    df = revenue.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    cutoff = as_of - timedelta(days=10)   # 10 日公告延遲
    df = df[df["date"] < cutoff].sort_values("date").dropna(subset=["revenue_yoy"])
    if len(df) < 9:
        return None, 0.0
    recent_3 = float(df.tail(3)["revenue_yoy"].mean())
    prior_6 = float(df.iloc[-9:-3]["revenue_yoy"].mean())
    accel = recent_3 - prior_6
    if accel <= 0:
        return accel, 0.0
    if accel >= 30:
        return accel, 25.0
    return accel, accel / 30 * 25


def factor_above_200ma(
    ohlcv: pd.DataFrame, as_of: date,
    min_premium: float = 0.05,
) -> tuple[bool, float]:
    """
    當前 close > 200MA × (1 + min_premium) → 滿分 15。
    """
    if ohlcv is None or ohlcv.empty:
        return False, 0.0
    df = ohlcv.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of].sort_values("date")
    if len(df) < 200:
        return False, 0.0
    cur = float(df.iloc[-1]["close"])
    ma200 = float(df["close"].tail(200).mean())
    if ma200 <= 0:
        return False, 0.0
    above = cur > ma200 * (1 + min_premium)
    return above, 15.0 if above else 0.0


def factor_market_cap_band(
    market_cap_btw: float | None,
    min_cap_billion: float = 30.0,
    max_cap_billion: float = 300.0,
) -> tuple[bool, float]:
    """
    市值在 [30, 300] 億新台幣甜蜜區間 → 滿分 10。
    若無市值資料，給中間分 5（不一票否決）。
    """
    if market_cap_btw is None:
        return False, 5.0
    in_band = min_cap_billion <= market_cap_btw <= max_cap_billion
    return in_band, 10.0 if in_band else 0.0


def scan_ticker(
    ticker: str,
    ohlcv: pd.DataFrame,
    revenue: pd.DataFrame | None,
    as_of: date,
    market_cap_btw: float | None = None,
    threshold: float = 60.0,
) -> EarlyHunterSignal | None:
    """
    對單一 ticker 跑 5 個因子 → 合成總分（滿分 100）。
    threshold 預設 60（取嚴一點，避免假信號）。
    """
    mom, mom_score = factor_momentum_window(ohlcv, as_of)
    vol, vol_score = factor_volume_expansion(ohlcv, as_of)
    rev_accel, rev_score = factor_revenue_acceleration(revenue, as_of) if revenue is not None else (None, 0.0)
    above_ma, ma_score = factor_above_200ma(ohlcv, as_of)
    cap_ok, cap_score = factor_market_cap_band(market_cap_btw)

    total = mom_score + vol_score + rev_score + ma_score + cap_score

    return EarlyHunterSignal(
        ticker=ticker,
        as_of=as_of,
        momentum_12m_pct=mom,
        volume_expansion=vol,
        revenue_acceleration=rev_accel,
        market_cap_eligible=cap_ok,
        above_200ma=above_ma,
        score=round(total, 1),
        triggered=total >= threshold,
    )
