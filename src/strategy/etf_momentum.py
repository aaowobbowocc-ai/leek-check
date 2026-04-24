"""
ETF 動能輪動策略（Phase 15）— 每月選 top N 動能最強的 ETF 持有。

學術依據：
  - Jegadeesh & Titman (1993) 動能效應：過去 3-12 月強勢資產未來 1-3 月延續
  - Antonacci (2014) Dual Momentum：絕對動能 + 相對動能雙重過濾
  - 台股 ETF 實證：月度輪動年化超額 +2-5% vs 買入持有 0050

設計原則（避免 Phase 12/13 過度複雜的教訓）：
  - 只用 close-to-close 報酬率（最乾淨的訊號）
  - 沒有 look-ahead：評分用 T-1 收盤，T 日 rebalance
  - 全部現金防禦（絕對動能過濾）：若所有 ETF 6M 報酬 < 0 → 空手
  - 單一因子，不疊加量能 / 波動等 — MVP 階段先驗證動能本身
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import yaml


@dataclass(frozen=True)
class ETFRanking:
    """某個 rebalance 日的排名結果。"""
    as_of: date
    rankings: list[tuple[str, float]]   # (ticker, 6M 報酬) 降冪
    selected: list[str]                  # top N
    defensive: bool                      # 全現金防禦模式
    reason: str = ""


@dataclass(frozen=True)
class ETFConfig:
    etfs: list[str]
    lookback_months: int
    top_n: int
    equal_weight: bool
    cash_when_all_negative: bool


def load_config(yaml_path: Path | str) -> ETFConfig:
    raw = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    strat = raw.get("strategy", {})
    return ETFConfig(
        etfs=[e["ticker"] for e in raw["etfs"]],
        lookback_months=int(strat.get("lookback_months", 6)),
        top_n=int(strat.get("top_n", 2)),
        equal_weight=bool(strat.get("equal_weight", True)),
        cash_when_all_negative=bool(strat.get("cash_when_all_negative", True)),
    )


def compute_return(ohlcv: pd.DataFrame, as_of: date, lookback_days: int) -> float | None:
    """
    計算 as_of 日往回 lookback_days 的 close-to-close 報酬。
    只使用 date <= as_of 的資料，嚴格禁止 look-ahead。
    回傳 None 代表資料不足。
    """
    if ohlcv is None or ohlcv.empty:
        return None
    df = ohlcv.sort_values("date").copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of]
    if len(df) < lookback_days:
        return None
    end_close = float(df.iloc[-1]["close"])
    start_close = float(df.iloc[-lookback_days]["close"])
    if start_close <= 0:
        return None
    return end_close / start_close - 1.0


def rank_etfs(
    ohlcv_map: dict[str, pd.DataFrame],
    as_of: date,
    config: ETFConfig,
) -> ETFRanking:
    """
    對 config.etfs 的所有 ETF 計算 lookback 報酬並排名。
    回傳 top N 選股與絕對動能防禦判斷。
    """
    lookback_days = config.lookback_months * 21      # 月 ≈ 21 交易日
    scored: list[tuple[str, float]] = []

    for ticker in config.etfs:
        df = ohlcv_map.get(ticker)
        r = compute_return(df, as_of, lookback_days)
        if r is not None:
            scored.append((ticker, r))

    scored.sort(key=lambda x: x[1], reverse=True)

    # 絕對動能防禦：若全部負報酬 → 空手
    if config.cash_when_all_negative and scored and all(r < 0 for _, r in scored):
        return ETFRanking(
            as_of=as_of,
            rankings=scored,
            selected=[],
            defensive=True,
            reason=f"全部 {len(scored)} 檔 {config.lookback_months}M 報酬為負，進入現金防禦",
        )

    # 取 top N（且報酬為正才選）
    positive = [(t, r) for t, r in scored if r > 0]
    selected = [t for t, _ in positive[: config.top_n]]

    return ETFRanking(
        as_of=as_of,
        rankings=scored,
        selected=selected,
        defensive=len(selected) == 0,
        reason=f"已選 {len(selected)} 檔（動能 top {config.top_n}，僅選正報酬）",
    )


def portfolio_weights(ranking: ETFRanking, config: ETFConfig) -> dict[str, float]:
    """把 ranking.selected 轉成 {ticker: weight} 分配。"""
    if ranking.defensive or not ranking.selected:
        return {}
    if config.equal_weight:
        w = 1.0 / len(ranking.selected)
        return {t: w for t in ranking.selected}
    # 非等權分配可擴充（這裡只有等權實作）
    return {t: 1.0 / len(ranking.selected) for t in ranking.selected}
