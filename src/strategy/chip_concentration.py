"""
Chip Concentration（Phase 18b Step 2 — Sponsor 升級版）。

Vol Anomaly 偵測「量先動」，本模組驗證「誰在動」。
Sponsor 方案啟用後新增兩個訊號：

  L3 大戶持股斜率 — TaiwanStockHoldingSharesPer（週更新）
      ≥ 1000 張大戶（"more than 1,000,001 shares"）持股率 4 週斜率
      斜率上升 = 大戶吃貨；下降 = 大戶出清

  L4 分點集中度 — TaiwanStockTradingDailyReport（Sponsor 啟用）
      4 週內 top-5 淨買超券商 佔 4 週總成交量 %
      高集中度 = 少數主力在悄悄累積；低集中度 = 散戶主導

原有訊號（Free 方案）：
  L1 三大法人累積淨買（4 週淨買 / 成交量 %）
  L2 外資持股斜率（4 週 foreign_pct 斜率）

判定邏輯（4 訊號加總）：
  各訊號 0/1 布林，正向 signal 各計 1 分，distribution 計 -1 分
  總分 3-4 → strong_accumulation     (+20)
  總分 2   → moderate_accumulation   (+10)
  總分 1   → weak_accumulation       (+5)
  總分 0   → no_accumulation         (0)
  有 distribution 訊號 → distribution (-15)

Look-ahead bias 控制：所有 df filter date <= as_of。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import pandas as pd


ConcentrationLevel = Literal[
    "strong_accumulation",
    "moderate_accumulation",
    "weak_accumulation",
    "no_accumulation",
    "distribution",
]

LEVEL_BONUS: dict[ConcentrationLevel, float] = {
    "strong_accumulation": 20.0,
    "moderate_accumulation": 10.0,
    "weak_accumulation": 5.0,
    "no_accumulation": 0.0,
    "distribution": -15.0,
}


@dataclass(frozen=True)
class ChipConcentrationSignal:
    ticker: str
    as_of: date

    # L2 — 外資持股斜率
    foreign_pct_now: float | None
    foreign_pct_4w_ago: float | None
    foreign_slope_4w_pp_per_week: float | None

    # L1 — 三大法人累積淨買
    inst_net_buy_4w_shares: int | None
    inst_net_buy_pct_volume: float | None

    # L3 — 大戶持股斜率（Sponsor）
    big_holder_pct_now: float | None
    big_holder_pct_4w_ago: float | None
    big_holder_slope_4w_pp_per_week: float | None

    # L4 — 分點集中度（Sponsor）
    broker_top5_net_buy_pct_volume: float | None

    # 評分
    level: ConcentrationLevel
    score_bonus: float
    reason: str
    signal_count: int


def _filter_by_date(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    return out[out["date"] <= as_of].sort_values("date").reset_index(drop=True)


# ─────────────────────────────────────
# L2 — 外資持股斜率
# ─────────────────────────────────────
def compute_foreign_pct_slope(
    foreign_holding: pd.DataFrame,
    as_of: date,
    weeks: int = 4,
) -> tuple[float | None, float | None, float | None]:
    df = _filter_by_date(foreign_holding, as_of)
    if df.empty or "foreign_pct" not in df.columns:
        return None, None, None

    now_pct = float(df.iloc[-1]["foreign_pct"])
    target_date = as_of - timedelta(days=weeks * 7)
    past = df[df["date"] <= target_date]
    if past.empty:
        return now_pct, None, None
    past_pct = float(past.iloc[-1]["foreign_pct"])
    slope = (now_pct - past_pct) / weeks
    return now_pct, past_pct, slope


# ─────────────────────────────────────
# L1 — 三大法人累積淨買
# ─────────────────────────────────────
def compute_institutional_accumulation(
    institutional: pd.DataFrame,
    ohlcv: pd.DataFrame,
    as_of: date,
    weeks: int = 4,
) -> tuple[int | None, float | None]:
    df = _filter_by_date(institutional, as_of)
    if df.empty or "net_buy" not in df.columns:
        return None, None

    cutoff = as_of - timedelta(days=weeks * 7)
    recent = df[df["date"] >= cutoff]
    if recent.empty:
        return None, None

    total_net_buy_shares = int(recent["net_buy"].sum() * 1000)

    ohlcv_df = _filter_by_date(ohlcv, as_of)
    if ohlcv_df.empty:
        return total_net_buy_shares, None
    period_vol = ohlcv_df[ohlcv_df["date"] >= cutoff]
    if period_vol.empty:
        return total_net_buy_shares, None
    total_volume = float(period_vol["volume"].sum() * 1000)
    if total_volume <= 0:
        return total_net_buy_shares, None

    pct = total_net_buy_shares / total_volume * 100.0
    return total_net_buy_shares, pct


# ─────────────────────────────────────
# L3 — 大戶持股斜率（Sponsor）
# ─────────────────────────────────────
def compute_big_holder_slope(
    holding_shares: pd.DataFrame,
    as_of: date,
    weeks: int = 4,
) -> tuple[float | None, float | None, float | None]:
    """
    回傳 (now_pct, n_weeks_ago_pct, slope_pp_per_week)
    holding_shares 欄位: date | big_holder_pct（≥1000 張大戶持股率 %）
    資料為週頻，斜率用最近 N 個資料點 vs 4 週前 lasso。
    """
    df = _filter_by_date(holding_shares, as_of)
    if df.empty or "big_holder_pct" not in df.columns:
        return None, None, None

    now_pct = float(df.iloc[-1]["big_holder_pct"])
    target_date = as_of - timedelta(days=weeks * 7)
    past = df[df["date"] <= target_date]
    if past.empty:
        return now_pct, None, None
    past_pct = float(past.iloc[-1]["big_holder_pct"])
    slope = (now_pct - past_pct) / weeks
    return now_pct, past_pct, slope


# ─────────────────────────────────────
# L4 — 分點集中度（Sponsor）
# ─────────────────────────────────────
def compute_broker_concentration(
    broker_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    as_of: date,
    weeks: int = 4,
    top_n: int = 5,
) -> float | None:
    """
    Top-N 淨買超券商的 4 週累積淨買 / 4 週總成交量 %。
    高值 = 少數主力悄悄吃貨；負值 = 主力在出。
    broker_df 欄位: date | broker_id | buy | sell
    """
    df = _filter_by_date(broker_df, as_of)
    if df.empty or "buy" not in df.columns:
        return None

    cutoff = as_of - timedelta(days=weeks * 7)
    recent = df[df["date"] >= cutoff].copy()
    if recent.empty:
        return None

    # 每個券商 4 週累積淨買（張）
    broker_agg = (
        recent.groupby("broker_id")
        .agg(net_buy=("buy", "sum"))
        .assign(net_buy=lambda x: x["net_buy"] - recent.groupby("broker_id")["sell"].sum())
        .reset_index()
    )
    # 正確算法：分開聚合
    buy_agg = recent.groupby("broker_id")["buy"].sum()
    sell_agg = recent.groupby("broker_id")["sell"].sum()
    broker_net = (buy_agg - sell_agg).reset_index()
    broker_net.columns = ["broker_id", "net_buy"]

    top5 = broker_net.nlargest(top_n, "net_buy")
    top5_net = float(top5["net_buy"].sum())

    # 4 週總成交量（張）
    ohlcv_df = _filter_by_date(ohlcv, as_of)
    period_vol = ohlcv_df[ohlcv_df["date"] >= cutoff]
    if period_vol.empty:
        return None
    total_vol = float(period_vol["volume"].sum())
    if total_vol <= 0:
        return None

    return top5_net / total_vol * 100.0


# ─────────────────────────────────────
# 主判定
# ─────────────────────────────────────
def evaluate_concentration(
    ticker: str,
    foreign_holding: pd.DataFrame | None,
    institutional: pd.DataFrame | None,
    ohlcv: pd.DataFrame,
    as_of: date,
    holding_shares: pd.DataFrame | None = None,
    broker_df: pd.DataFrame | None = None,
    # 閾值
    foreign_slope_threshold_pp: float = 0.05,
    inst_net_buy_pct_threshold: float = 1.0,
    distribution_pct_threshold: float = -1.0,
    big_holder_slope_threshold: float = 0.05,
    broker_concentration_threshold: float = 3.0,
) -> ChipConcentrationSignal:
    # ── L2 外資斜率 ──
    fp_now, fp_past, fp_slope = compute_foreign_pct_slope(
        foreign_holding if foreign_holding is not None else pd.DataFrame(), as_of, weeks=4
    )
    l2_positive = fp_slope is not None and fp_slope > foreign_slope_threshold_pp

    # ── L1 三大法人 ──
    inst_net, inst_pct = compute_institutional_accumulation(
        institutional if institutional is not None else pd.DataFrame(), ohlcv, as_of, weeks=4
    )
    l1_accumulating = inst_pct is not None and inst_pct > inst_net_buy_pct_threshold
    l1_distributing = inst_pct is not None and inst_pct < distribution_pct_threshold

    # ── L3 大戶持股（Sponsor） ──
    bh_now, bh_past, bh_slope = compute_big_holder_slope(
        holding_shares if holding_shares is not None else pd.DataFrame(), as_of, weeks=4
    )
    l3_positive = bh_slope is not None and bh_slope > big_holder_slope_threshold
    l3_distributing = bh_slope is not None and bh_slope < -big_holder_slope_threshold

    # ── L4 分點集中度（Sponsor） ──
    broker_conc = compute_broker_concentration(
        broker_df if broker_df is not None else pd.DataFrame(), ohlcv, as_of, weeks=4
    )
    l4_positive = broker_conc is not None and broker_conc > broker_concentration_threshold
    l4_distributing = broker_conc is not None and broker_conc < -broker_concentration_threshold

    # ── 綜合判定 ──
    has_distribution = l1_distributing or l3_distributing or l4_distributing
    positive_signals = sum([l1_accumulating, l2_positive, l3_positive, l4_positive])

    if has_distribution:
        level: ConcentrationLevel = "distribution"
        parts = []
        if l1_distributing:
            parts.append(f"法人淨賣 {inst_pct:.1f}% vol")
        if l3_distributing:
            parts.append(f"大戶持股減少 {bh_slope:.3f} pp/wk")
        if l4_distributing:
            parts.append(f"主力券商淨賣 {broker_conc:.1f}% vol")
        reason = "⚠️ 出貨訊號：" + "；".join(parts)
    elif positive_signals >= 3:
        level = "strong_accumulation"
        reason = f"強力吃貨 {positive_signals}/4 訊號同時成立"
    elif positive_signals == 2:
        level = "moderate_accumulation"
        reason = f"溫和吃貨 {positive_signals}/4 訊號成立"
    elif positive_signals == 1:
        level = "weak_accumulation"
        parts = []
        if l1_accumulating:
            parts.append(f"法人淨買 {inst_pct:.1f}% vol")
        if l2_positive:
            parts.append(f"外資斜率 +{fp_slope:.3f} pp/wk")
        if l3_positive:
            parts.append(f"大戶持股 +{bh_slope:.3f} pp/wk")
        if l4_positive:
            parts.append(f"主力券商 +{broker_conc:.1f}% vol")
        reason = "弱吃貨：" + "；".join(parts)
    else:
        level = "no_accumulation"
        reason = "法人、外資、大戶、分點皆未顯著加碼"

    return ChipConcentrationSignal(
        ticker=ticker,
        as_of=as_of,
        foreign_pct_now=round(fp_now, 3) if fp_now is not None else None,
        foreign_pct_4w_ago=round(fp_past, 3) if fp_past is not None else None,
        foreign_slope_4w_pp_per_week=round(fp_slope, 4) if fp_slope is not None else None,
        inst_net_buy_4w_shares=inst_net,
        inst_net_buy_pct_volume=round(inst_pct, 3) if inst_pct is not None else None,
        big_holder_pct_now=round(bh_now, 3) if bh_now is not None else None,
        big_holder_pct_4w_ago=round(bh_past, 3) if bh_past is not None else None,
        big_holder_slope_4w_pp_per_week=round(bh_slope, 4) if bh_slope is not None else None,
        broker_top5_net_buy_pct_volume=round(broker_conc, 3) if broker_conc is not None else None,
        level=level,
        score_bonus=LEVEL_BONUS[level],
        reason=reason,
        signal_count=positive_signals,
    )
