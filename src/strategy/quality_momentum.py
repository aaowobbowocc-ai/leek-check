"""
Quality Momentum Screener（Phase 16）— 全市場個股選股器。

設計目標：
  讓 AI 算力真正幫到你 — 在 1800 檔台股中每月跑一次多因子評分，
  挑出「品質好 + 動能強 + 估值合理」的 15-20 檔。這是 Simons 精神
  （系統化 + 全市場掃描）不是 Buffett 精神（深度定性分析）。

五因子合成（z-score 標準化後加權）：
  1. Momentum 12M (30%):   過去 12 個月價格動能 — Jegadeesh-Titman
  2. Quality ROE (25%):    ROE — Novy-Marx / Piotroski
  3. Value P/E (20%):      P/E 倒數（Earnings Yield）— Fama-French
  4. Low Vol 60D (15%):    近 60 日年化波動的負值 — 反直覺 factor
  5. Revenue Growth (10%): 月營收 YoY — 台股 specific 超前指標

Look-ahead 防線：
  - 動能 / 波動：用 as_of 及以前的收盤
  - 財報：僅採用「date < as_of - 3 個月」的資料（避免未公告即用）
  - 營收：僅採用「date < as_of - 10 天」的資料（月營收 10 日內公告）
  - P/E：FinMind 的 date 即當日，直接 date < as_of

Phase 16 階段：
  - 這個 module 只提供計算邏輯 + factor score 合成
  - 實際的回測引擎、月度 rebalance 在 scripts/quality_momentum_backtest.py（下次實作）
  - 先確保資料介面 + 評分邏輯 + 單元測試正確
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import pandas as pd


# ─────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────
@dataclass(frozen=True)
class QualityMomentumScore:
    """某檔股票在某日的 QM 分數快照。"""
    ticker: str
    as_of: date
    score: float                 # 總分（z-score 加權）
    breakdown: dict[str, float] = field(default_factory=dict)
    raw: dict[str, float] = field(default_factory=dict)      # 原始因子值


@dataclass(frozen=True)
class FactorWeights:
    momentum: float = 0.30
    quality_roe: float = 0.25
    value_pe: float = 0.20
    low_vol: float = 0.15
    revenue_growth: float = 0.10


# ─────────────────────────────────────────
# 個別因子計算
# ─────────────────────────────────────────
def factor_momentum_12m(ohlcv: pd.DataFrame, as_of: date) -> float | None:
    """過去約 252 交易日的 close-to-close 報酬。"""
    if ohlcv is None or ohlcv.empty:
        return None
    df = ohlcv.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of].sort_values("date")
    if len(df) < 252:
        return None
    end = float(df.iloc[-1]["close"])
    start = float(df.iloc[-252]["close"])
    if start <= 0:
        return None
    return end / start - 1.0


def factor_low_vol_60d(ohlcv: pd.DataFrame, as_of: date) -> float | None:
    """
    過去 60 交易日日報酬標準差（年化）。
    回傳 `-annualized_vol`，值越大代表越低波。
    """
    if ohlcv is None or ohlcv.empty:
        return None
    df = ohlcv.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of].sort_values("date")
    if len(df) < 61:
        return None
    returns = df["close"].tail(61).astype(float).pct_change().dropna()
    if len(returns) < 60 or returns.std() == 0:
        return None
    return -float(returns.std() * math.sqrt(252))


def factor_value_earnings_yield(per_pbr: pd.DataFrame, as_of: date) -> float | None:
    """
    Earnings Yield = 1 / P/E（若 PE <= 0 視為 None 避免賠錢公司誤當 value）。
    取 as_of 前最近一日的 PE。
    """
    if per_pbr is None or per_pbr.empty:
        return None
    df = per_pbr.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] < as_of].sort_values("date")
    if df.empty or "per" not in df.columns:
        return None
    pe = float(df.iloc[-1]["per"])
    if pe is None or pd.isna(pe) or pe <= 0:
        return None
    return 1.0 / pe


def factor_quality_roe(financials: pd.DataFrame, as_of: date) -> float | None:
    """
    ROE (TTM 近似值): 取最近 4 季 ROE 平均（若不足則取最近一筆）。
    財報延後 3 個月才能看，避免 look-ahead。
    """
    if financials is None or financials.empty:
        return None
    df = financials.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    cutoff = as_of - timedelta(days=90)         # 3 個月公告延遲假設
    df = df[(df["date"] < cutoff) & (df["type"] == "ROE")].sort_values("date")
    if df.empty:
        return None
    recent = df.tail(4)
    if recent.empty:
        return None
    return float(recent["value"].mean())


def factor_revenue_growth_yoy(revenue: pd.DataFrame, as_of: date) -> float | None:
    """
    最新一筆月營收年增率（revenue_yoy）。
    月營收每月 10 日前公告上月資料，保守採 date < as_of - 10 天。
    """
    if revenue is None or revenue.empty:
        return None
    df = revenue.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    cutoff = as_of - timedelta(days=10)
    df = df[df["date"] < cutoff].sort_values("date")
    if df.empty or "revenue_yoy" not in df.columns:
        return None
    yoy = df.iloc[-1]["revenue_yoy"]
    if pd.isna(yoy):
        return None
    return float(yoy)


# ─────────────────────────────────────────
# 橫斷面 z-score 合成
# ─────────────────────────────────────────
def zscore(series: pd.Series) -> pd.Series:
    """標準 z-score；若 std=0 或全 NaN 則回全 0。"""
    valid = series.dropna()
    if len(valid) < 2 or valid.std() == 0:
        return pd.Series(0.0, index=series.index)
    z = (series - valid.mean()) / valid.std()
    # 對 NaN 填 0（中性）
    return z.fillna(0.0)


def cross_sectional_score(
    factor_table: pd.DataFrame,
    weights: FactorWeights = FactorWeights(),
) -> pd.DataFrame:
    """
    factor_table: DataFrame index=ticker, columns=['momentum','quality_roe',
                  'value_pe','low_vol','revenue_growth']
    對每欄做橫斷面 z-score → 加權總和 → 回加入 'score' 欄位。
    """
    if factor_table.empty:
        return factor_table.assign(score=0.0)
    df = factor_table.copy()
    z_mom = zscore(df["momentum"]) * weights.momentum
    z_roe = zscore(df["quality_roe"]) * weights.quality_roe
    z_pe = zscore(df["value_pe"]) * weights.value_pe
    z_vol = zscore(df["low_vol"]) * weights.low_vol
    z_rev = zscore(df["revenue_growth"]) * weights.revenue_growth
    df["score"] = z_mom + z_roe + z_pe + z_vol + z_rev
    return df


# ─────────────────────────────────────────
# 便利包裝：對單一 ticker 算全部因子
# ─────────────────────────────────────────
DataFetcher = Callable[[str, date], dict[str, pd.DataFrame]]


def compute_ticker_factors(
    ticker: str,
    as_of: date,
    ohlcv: pd.DataFrame,
    per_pbr: pd.DataFrame,
    financials: pd.DataFrame,
    revenue: pd.DataFrame,
) -> dict[str, float | None]:
    """對單檔算 5 個因子的原始值（不 z-score）。"""
    return {
        "momentum": factor_momentum_12m(ohlcv, as_of),
        "quality_roe": factor_quality_roe(financials, as_of),
        "value_pe": factor_value_earnings_yield(per_pbr, as_of),
        "low_vol": factor_low_vol_60d(ohlcv, as_of),
        "revenue_growth": factor_revenue_growth_yoy(revenue, as_of),
    }
