"""
妖股雷達（MVP） — 偵測「量能突破 + 動能爆發 + 漲停動能」的中小型股機會。

設計哲學（避免重踩 Phase 12/13 坑）：
  - MVP 只用 yfinance OHLCV 可算的訊號，不碰 FinMind broker（省時/省 API 額度）
  - 訊號權重平均分配，避免單一訊號主導，先驗證整體有沒有 edge 再優化
  - 嚴格 look-ahead 防護：所有計算只用 cutoff 日（含）之前的 bar
  - 「妖股」定義：5-10 天內 +20% 以上的中小型股（非長線持有）

四個訊號（各 25 分，滿分 100，進場門檻 60）：
  1. 量能突破 — 量 > 20MA × 2 且價突破 60 日新高
  2. 動能爆發 — 過去 5 日收盤報酬 > 8%
  3. 漲停動能 — 過去 3 日內有觸及漲停（close >= prev × 1.099）
  4. 爆量近期 — 最近 5 日平均量 / 前 15 日平均量 > 1.5（主力開始吸收的跡象）

出場規則（由 backtest 端決定，非此模組）：
  - 止損 entry × 0.93
  - 目標 entry × 1.20
  - 時間停損 7 個交易日
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class YaoguSignal:
    """某檔股票在某日的妖股訊號快照。"""
    ticker: str
    as_of: date
    score: float                       # 0-100
    triggered: bool                     # score >= threshold
    close: float                       # 當日收盤
    flags: list[str] = field(default_factory=list)
    breakdown: dict[str, float] = field(default_factory=dict)


def scan_ticker(
    ohlcv: pd.DataFrame,
    as_of: date,
    threshold: float = 60.0,
) -> YaoguSignal | None:
    """
    對單一 ticker 計算妖股訊號。ohlcv 必須已按 date 升冪排序、只含 as_of 及更早。

    回傳 None 代表資料不足（至少需 65 個交易日供 60 日新高判斷 + 5 日前值）。
    """
    if ohlcv is None or ohlcv.empty:
        return None

    df = ohlcv.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of]

    if len(df) < 65:
        return None

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    volume = df["volume"].astype(float)

    score_vb = _signal_volume_breakout(close, high, volume)
    score_mom = _signal_momentum(close)
    score_lu = _signal_limit_up(close)
    score_vol = _signal_volume_expansion(volume)

    total = score_vb + score_mom + score_lu + score_vol
    flags = []
    if score_vb > 0:
        flags.append("vol_breakout")
    if score_mom > 0:
        flags.append("momentum")
    if score_lu > 0:
        flags.append("limit_up_recent")
    if score_vol > 0:
        flags.append("vol_expansion")

    return YaoguSignal(
        ticker="",   # 呼叫端填入
        as_of=as_of,
        score=round(total, 1),
        triggered=total >= threshold,
        close=float(close.iloc[-1]),
        flags=flags,
        breakdown={
            "volume_breakout": round(score_vb, 1),
            "momentum": round(score_mom, 1),
            "limit_up": round(score_lu, 1),
            "vol_expansion": round(score_vol, 1),
        },
    )


# ─────────────────────────────────────────
# 訊號實作
# ─────────────────────────────────────────
def _signal_volume_breakout(
    close: pd.Series, high: pd.Series, volume: pd.Series
) -> float:
    """
    量能突破：價突破前 60 日最高 + 量 > 20MA × 2。
    分數組成：
      - 基礎 10 分（雙條件均達成）
      - 突破幅度 0-10 分（突破 0 ~ 3% 線性）
      - 量倍數  0-5  分（2x → 0, 5x+ → 5）
    """
    if len(high) < 61 or len(volume) < 21:
        return 0.0
    prior_60d_high = float(high.iloc[-61:-1].max())   # 排除今日
    close_today = float(close.iloc[-1])
    if close_today <= prior_60d_high:
        return 0.0

    vol_ma20 = float(volume.iloc[-21:-1].mean())
    vol_today = float(volume.iloc[-1])
    if vol_ma20 <= 0 or vol_today < vol_ma20 * 2.0:
        return 0.0

    score = 10.0
    breakout_pct = (close_today / prior_60d_high - 1.0) * 100.0
    score += min(10.0, breakout_pct / 3.0 * 10.0)

    vol_mult = vol_today / vol_ma20
    score += min(5.0, (vol_mult - 2.0) / 3.0 * 5.0)

    return score


def _signal_momentum(close: pd.Series) -> float:
    """
    動能爆發：過去 5 交易日收盤報酬 > 8%。
    分數：0-25，報酬 8-25% 線性給分。
    """
    if len(close) < 6:
        return 0.0
    ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1.0) * 100.0
    if ret_5d < 8.0:
        return 0.0
    return min(25.0, (ret_5d - 8.0) / 17.0 * 25.0 + 5.0)


def _signal_limit_up(close: pd.Series) -> float:
    """
    漲停動能：過去 3 日內至少一次觸及漲停（close >= prev × 1.099）。
    分數：0 次 → 0、1 次 → 15、2+ 次 → 25。
    """
    if len(close) < 4:
        return 0.0
    limit_up_count = 0
    for i in range(-3, 0):
        if i - 1 < -len(close):
            break
        prev = float(close.iloc[i - 1])
        cur = float(close.iloc[i])
        if prev <= 0:
            continue
        if cur >= prev * 1.099:
            limit_up_count += 1
    if limit_up_count == 0:
        return 0.0
    if limit_up_count == 1:
        return 15.0
    return 25.0


def _signal_volume_expansion(volume: pd.Series) -> float:
    """
    爆量近期：近 5 日平均量 / 前 15 日平均量 > 1.5。
    分數：1.5x → 10, 3.0x+ → 25。
    """
    if len(volume) < 20:
        return 0.0
    recent_5 = float(volume.iloc[-5:].mean())
    prior_15 = float(volume.iloc[-20:-5].mean())
    if prior_15 <= 0:
        return 0.0
    ratio = recent_5 / prior_15
    if ratio < 1.5:
        return 0.0
    return min(25.0, (ratio - 1.5) / 1.5 * 15.0 + 10.0)
