"""
Valuation Guard（Phase 12）— PBR 歷史百分位過濾器。

設計原理：
  - 一檔股票的 PBR 在過去 5 年中處於第 X 百分位，反映「相對於自己的歷史」是貴還是便宜
  - 當 PBR > 過去 5 年的第 90 百分位，代表估值已站上歷史最貴的 10% → composite 扣 10 分
  - 純相對比較，不依賴產業比較或絕對值（避免跨產業 PBR 失真）

為什麼是 PBR 而非 PER：
  - PBR 穩定（淨值變動慢）、歷史分位數可比
  - PER 在虧損年分子 / 分母飆升或翻負，百分位失真
  - 對短線題材股（AI 設備）更實用：當 PBR 百分位 > 90%，通常接近族群末升段

函式只做「純計算」；要不要應用懲罰、門檻設多少，由 composite_scorer 讀 strategy.yaml 決定。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

# 5 年 = 約 1260 個交易日（252 × 5）
_DEFAULT_LOOKBACK_YEARS = 5
# 樣本不足此數 → 百分位不可靠，回 None。測試保留 60 的寬鬆門檻；
# 生產環境（strategy.yaml）建議 252（約一年交易日），代表性更足。
_DEFAULT_MIN_SAMPLES = 60


def compute_pbr_percentile(
    pbr_history: pd.DataFrame,
    as_of: date,
    lookback_years: int = _DEFAULT_LOOKBACK_YEARS,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
) -> float | None:
    """
    計算 as_of 當天的 PBR 在過去 lookback_years 年中的百分位。

    參數：
      pbr_history: 至少含 `date`, `pbr` 兩欄；date 為 datetime.date，pbr 為 float。
                   允許包含 as_of 當天（會作為「當前值」取用），但歷史分佈嚴格使用 < as_of 的資料。
      as_of: 評估當天（通常是 pipeline 的 simulated_today 或真實 T 日）。
      lookback_years: 回看年數，預設 5。

    回傳：
      float ∈ [0, 1]：PBR 在歷史分佈中的百分位（0 = 最便宜、1 = 最貴）
      None：資料不足（樣本 < _MIN_SAMPLES）或缺 as_of 當天 PBR

    Look-ahead 防線：
      歷史分佈 **嚴格使用 < as_of** 的資料點（不含 as_of 當天），避免用到未來資訊。
      當前 PBR 則使用 <= as_of 中最近的一筆（允許取 as_of 本身當作晨報前已知的最新值）。
    """
    if pbr_history is None or pbr_history.empty:
        return None
    if "pbr" not in pbr_history.columns or "date" not in pbr_history.columns:
        return None

    df = pbr_history[["date", "pbr"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.dropna(subset=["pbr"])
    df = df[df["pbr"] > 0]  # PBR ≤ 0 代表淨值為負或資料異常
    if df.empty:
        return None

    window_start = as_of - timedelta(days=int(lookback_years * 365.25))
    history = df[(df["date"] >= window_start) & (df["date"] < as_of)]
    if len(history) < min_samples:
        return None

    current_rows = df[df["date"] <= as_of]
    if current_rows.empty:
        return None
    current_pbr = float(current_rows.sort_values("date").iloc[-1]["pbr"])
    if current_pbr <= 0:
        return None

    # 百分位 = 歷史樣本中 ≤ current_pbr 的比例
    hist_values = history["pbr"].to_numpy()
    rank = (hist_values <= current_pbr).sum()
    return float(rank) / float(len(hist_values))


def is_overvalued(
    pbr_history: pd.DataFrame,
    as_of: date,
    threshold: float = 0.90,
    lookback_years: int = _DEFAULT_LOOKBACK_YEARS,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
) -> tuple[bool, float | None]:
    """
    封裝布林判斷 — 回傳 (是否過貴, 實際百分位)。

    threshold: 預設 0.90，即站上過去 5 年最貴的 10% 區間。
    min_samples: 不足此樣本數視為資料不可靠，回 (False, None) 保守不擋。
    """
    pct = compute_pbr_percentile(
        pbr_history, as_of, lookback_years=lookback_years, min_samples=min_samples
    )
    if pct is None:
        return False, None
    return pct > threshold, pct
