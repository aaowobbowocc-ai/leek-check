"""
Volume Anomaly Detector（Phase 18b）— 抓「股價未動但量先動」的吃貨期訊號。

設計目標：
  在 Early Hunter（catch 主升段早期）之前，偵測 T0 吃貨期：
    T0 (吃貨期) → T1 (突破) → T2 (主升早期，Early Hunter 接手)

核心算法：Modified Z-Score (對數常態 + MAD)
  log_vol = log(volume + 1)
  z = 0.6745 × (log_vol - median_60) / MAD_60

  為何用 MAD 不用 std：對數常態下偶發極端值會嚴重污染 std；
  MAD 對離群值穩健（breakdown point = 50%）。
  常數 0.6745 ≈ 1/Φ⁻¹(0.75)，使 MAD 在常態分布下與 std 等效。

觸發條件（必須全部滿足）：
  1. 當日 z > 板別閾值（上市 2.0、OTC 2.5）
  2. 過去 10 日內 ≥ 4 天 z > 2.0（排除單日巧合）
  3. 過去 10 日內 ≥ 1 天 z > 3.0（必須有一天「異常強」）
  4. 內盤比 < 45%（買方主動為主，**最重要的方向確認**）
     — 沒有資料時不一票否決，但分數降低
  5. 不在除權息前後 5 日（避免 reference 跳水污染）
  6. 5 日累計漲幅 < 15%（不追末段）
  7. 流動性下限：上市 > 1000 張/日、OTC > 500 張/日
  8. 市值下限：上市 > 30 億、OTC > 50 億

Score 0-100（≥ 60 觸發）：
  - z 強度：0-40
  - 滑動窗口確認：0-25
  - 方向確認（內盤比）：0-25  ← 最關鍵
  - 200MA 確認：0-10

關於 Look-ahead Bias（CLAUDE.md 強制要求）：
  所有 baseline 計算都用 strict_rolling_mad，每個時點 t 的 baseline 用
  [t-60, t-1] 區間（不含 t），確保回測時不會偷看未來資料。

使用範例：
  signal = scan_volume_anomaly(
      ticker="3595",
      ohlcv=ohlcv_df,
      inner_outer=inner_df,
      as_of=date(2026, 4, 25),
      market_cap_btw=80.0,
      board="OTC",
      ex_dividend_dates=ex_div_df,
  )
  if signal and signal.triggered:
      print(f"⚡ {signal.ticker} z={signal.modified_z:.2f}, 內盤比={signal.inner_ratio}")
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

import numpy as np
import pandas as pd


Board = Literal["TWSE", "OTC"]
Direction = Literal["buying", "selling", "unknown"]


# ─────────────────────────────────────────
# 板別分層閾值
# ─────────────────────────────────────────
@dataclass(frozen=True)
class BoardThresholds:
    z_min: float
    daily_volume_floor: int          # 張數
    market_cap_floor_btw: float       # 億新台幣

    @classmethod
    def for_board(cls, board: Board) -> "BoardThresholds":
        if board == "OTC":
            return cls(z_min=2.5, daily_volume_floor=500, market_cap_floor_btw=50.0)
        return cls(z_min=2.0, daily_volume_floor=1000, market_cap_floor_btw=30.0)


# ─────────────────────────────────────────
# 訊號資料結構
# ─────────────────────────────────────────
@dataclass(frozen=True)
class VolumeAnomalySignal:
    ticker: str
    as_of: date
    board: Board

    # z-score
    modified_z: float
    median_log_vol_60d: float
    mad_log_vol_60d: float

    # 滑動窗口（過去 10 日）
    days_z_above_2: int
    days_z_above_3: int
    max_z_recent_10d: float

    # 方向確認
    inner_ratio: float | None
    direction: Direction

    # 價格背景
    close: float
    price_change_5d_pct: float
    above_200ma: bool

    # 流動性 / 市值
    avg_volume_60d: float
    market_cap_btw: float | None

    # 污染檢查
    is_ex_dividend_window: bool

    # 評分
    score: float
    triggered: bool
    rejection_reason: str | None = None
    breakdown: dict[str, float] = field(default_factory=dict)


# ─────────────────────────────────────────
# 嚴格 rolling MAD（避免 look-ahead）
# ─────────────────────────────────────────
def strict_rolling_modified_z(
    log_volumes: pd.Series, window: int = 60, min_periods: int = 30
) -> pd.Series:
    """
    對每個時點 t，計算 log_vol[t] 相對於 [t-window, t-1] 的 modified z-score。

    嚴格不含當天（避免 look-ahead bias）：
      median_t = median(log_vol[t-window:t])  # 不含 t
      mad_t    = median(|log_vol[t-window:t] - median_t|)
      z_t      = 0.6745 × (log_vol[t] - median_t) / mad_t

    比 pandas rolling.median().shift(1) 嚴格，因為這裡的 MAD 是用「歷史中位數」
    當基準算的，不是用「滑動視窗自己的中位數」。
    """
    n = len(log_volumes)
    z = np.full(n, np.nan)
    arr = log_volumes.to_numpy()

    for i in range(min_periods, n):
        start = max(0, i - window)
        history = arr[start:i]   # [t-window, t-1]，不含 t
        history = history[~np.isnan(history)]
        if len(history) < min_periods:
            continue
        med = float(np.median(history))
        mad = float(np.median(np.abs(history - med)))
        if mad <= 0:
            continue
        z[i] = 0.6745 * (arr[i] - med) / mad

    return pd.Series(z, index=log_volumes.index)


# ─────────────────────────────────────────
# 方向判定（內盤比 → OHLC proxy）
# ─────────────────────────────────────────
def detect_direction(
    inner_ratio: float | None,
    close: float,
    open_price: float,
    high: float | None = None,
    low: float | None = None,
) -> Direction:
    """
    優先用內盤比；無資料則用 OHLC proxy `(close-low)/(high-low)`。

    OHLC proxy 邏輯：
      pos = (close - low) / (high - low) ∈ [0, 1]
      pos > 0.65 + 收盤 ≥ 開盤 × 0.98 → buying（收盤接近高位 = 買盤主動）
      pos < 0.35                       → selling（收盤接近低位 = 賣盤壓制）

    內盤比邏輯（保留以利未來資料源）：
      < 45% + 收盤 ≥ 開盤 → buying
      > 55% → selling
    """
    # Priority 1: 內盤比（任何 FinMind 方案都沒有，永遠 None）
    if inner_ratio is not None and not math.isnan(inner_ratio):
        if inner_ratio < 0.45 and close >= open_price * 0.98:
            return "buying"
        if inner_ratio > 0.55:
            return "selling"
        return "unknown"

    # Priority 2: OHLC proxy
    if high is None or low is None or math.isnan(high) or math.isnan(low):
        return "unknown"
    if high <= low:
        return "unknown"
    pos = (close - low) / (high - low)
    if pos > 0.65 and close >= open_price * 0.98:
        return "buying"
    if pos < 0.35:
        return "selling"
    return "unknown"


# ─────────────────────────────────────────
# 除權息污染檢查
# ─────────────────────────────────────────
def is_in_ex_dividend_window(
    ticker: str,
    as_of: date,
    ex_dividend_dates: pd.DataFrame | None,
    window_days: int = 5,
) -> bool:
    """
    若 as_of 在某次除權息前後 window_days 內 → True，訊號污染需排除。
    ex_dividend_dates 欄位: ticker | ex_date
    """
    if ex_dividend_dates is None or ex_dividend_dates.empty:
        return False
    if "ticker" not in ex_dividend_dates.columns or "ex_date" not in ex_dividend_dates.columns:
        return False
    df = ex_dividend_dates[ex_dividend_dates["ticker"].astype(str) == str(ticker)]
    if df.empty:
        return False
    df = df.copy()
    df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
    for ex_d in df["ex_date"]:
        if abs((as_of - ex_d).days) <= window_days:
            return True
    return False


# ─────────────────────────────────────────
# 評分
# ─────────────────────────────────────────
def compute_anomaly_score(
    z: float,
    days_z_above_2: int,
    direction: Direction,
    above_200ma: bool,
) -> tuple[float, dict[str, float]]:
    """
    回傳 (總分 0-100, 子項分數)

    z 強度評分基於 2020-2024 500x5y 回測結果（找反證）：
      z 區間   T+60d hit%  T+60d mean   結論
      2.0-2.5    10.3      +4.4%       弱（噪音）
      2.5-3.0     8.8      +7.3%       中（無顯著 alpha）
      3.0-3.5    24.2      +13.3%      ⭐ 甜蜜區
      3.5-4.0     2.4      -2.2%       ⚠️ 反轉警告
      >4.0        5.8      +0.7%       已反映，避開
    """
    breakdown: dict[str, float] = {}

    # 1) z 強度（0-40），曲線基於回測甜蜜區
    if z >= 4.0:
        z_score = 0.0    # 已反映，反向警戒
    elif z >= 3.5:
        z_score = 5.0    # 3.5-4.0 失敗率高
    elif z >= 3.0:
        z_score = 40.0   # ⭐ 甜蜜區
    elif z >= 2.5:
        z_score = 20.0
    elif z >= 2.0:
        z_score = 10.0
    else:
        z_score = 0.0
    breakdown["z_strength"] = z_score

    # 2) 滑動窗口確認（0-25）
    if days_z_above_2 >= 5:
        window_score = 25.0
    elif days_z_above_2 >= 4:
        window_score = 20.0
    elif days_z_above_2 >= 3:
        window_score = 12.0
    else:
        window_score = 0.0
    breakdown["window_consistency"] = window_score

    # 3) 方向確認（0-25）— 最關鍵，selling 直接 0
    if direction == "buying":
        dir_score = 25.0
    elif direction == "unknown":
        dir_score = 8.0
    else:  # selling
        dir_score = 0.0
    breakdown["direction"] = dir_score

    # 4) 200MA 確認（0-10）
    ma_score = 10.0 if above_200ma else 0.0
    breakdown["above_200ma"] = ma_score

    total = z_score + window_score + dir_score + ma_score
    return min(total, 100.0), breakdown


# ─────────────────────────────────────────
# 主掃描函式
# ─────────────────────────────────────────
def scan_volume_anomaly(
    ticker: str,
    ohlcv: pd.DataFrame,
    as_of: date,
    inner_outer: pd.DataFrame | None = None,
    market_cap_btw: float | None = None,
    board: Board = "TWSE",
    ex_dividend_dates: pd.DataFrame | None = None,
    score_threshold: float = 70.0,
) -> VolumeAnomalySignal | None:
    """
    對單一 ticker 跑量能異常偵測。

    參數：
      ohlcv           : 欄位 date | open | close | volume（張數，已含當日）
      inner_outer     : 欄位 date | inner_ratio（內盤比 0-1），可選
      market_cap_btw  : 當前市值（億新台幣），可選但會影響評分
      board           : "TWSE" | "OTC"
      ex_dividend_dates: 欄位 ticker | ex_date，可選
      score_threshold : triggered 門檻（預設 60）

    回傳：
      VolumeAnomalySignal（含 triggered 與 rejection_reason）
      若資料嚴重不足回傳 None
    """
    if ohlcv is None or ohlcv.empty:
        return None

    df = ohlcv.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of].sort_values("date").reset_index(drop=True)

    # 至少要有 90 日資料（60 baseline + 滑動窗口 10 + 緩衝）
    if len(df) < 90:
        return None

    # 確認當日資料存在
    today_rows = df[df["date"] == as_of]
    if today_rows.empty:
        # as_of 不是交易日，取最近一筆
        today = df.iloc[-1]
    else:
        today = today_rows.iloc[-1]

    today_volume = float(today["volume"])
    today_close = float(today["close"])
    today_open = float(today.get("open", today_close))
    today_high = float(today.get("high", today_close))
    today_low = float(today.get("low", today_close))

    if today_volume <= 0 or today_close <= 0:
        return None

    thresholds = BoardThresholds.for_board(board)

    # ── log volume 與 z-score ──
    df["log_vol"] = np.log(df["volume"].clip(lower=1).astype(float))
    df["mod_z"] = strict_rolling_modified_z(df["log_vol"], window=60, min_periods=30)

    today_z = float(df.iloc[-1]["mod_z"]) if not pd.isna(df.iloc[-1]["mod_z"]) else float("nan")
    if math.isnan(today_z):
        return None

    # 60 日歷史 stats（不含今天）
    history_60 = df.iloc[-61:-1]["log_vol"]
    median_log_vol = float(history_60.median())
    mad_log_vol = float((history_60 - median_log_vol).abs().median())

    # 60 日平均量（張）
    avg_volume_60d = float(df.iloc[-61:-1]["volume"].mean())

    # ── 滑動窗口（過去 10 日，含今天）──
    z_recent = df.iloc[-10:]["mod_z"].dropna()
    days_z_above_2 = int((z_recent > 2.0).sum())
    days_z_above_3 = int((z_recent > 3.0).sum())
    max_z_recent = float(z_recent.max()) if not z_recent.empty else 0.0

    # ── 5 日漲幅 ──
    if len(df) >= 6:
        price_5d_ago = float(df.iloc[-6]["close"])
        price_change_5d = (today_close / price_5d_ago - 1.0) * 100.0
    else:
        price_change_5d = 0.0

    # ── 200MA ──
    if len(df) >= 200:
        ma200 = float(df.iloc[-200:]["close"].mean())
        above_200ma = today_close > ma200
    else:
        above_200ma = False

    # ── 內盤比（同日）──
    inner_ratio: float | None = None
    if inner_outer is not None and not inner_outer.empty:
        io = inner_outer.copy()
        io["date"] = pd.to_datetime(io["date"]).dt.date
        match = io[io["date"] == as_of]
        if not match.empty and "inner_ratio" in match.columns:
            ir = match.iloc[-1]["inner_ratio"]
            if pd.notna(ir):
                inner_ratio = float(ir)

    direction = detect_direction(inner_ratio, today_close, today_open,
                                  high=today_high, low=today_low)

    # ── 除權息污染 ──
    is_ex_div = is_in_ex_dividend_window(ticker, as_of, ex_dividend_dates, window_days=5)

    # ── 評分 ──
    score, breakdown = compute_anomaly_score(
        z=today_z,
        days_z_above_2=days_z_above_2,
        direction=direction,
        above_200ma=above_200ma,
    )

    # ── 否決條件（hard rejection，覆蓋 score）──
    rejection: str | None = None
    if today_z < thresholds.z_min:
        rejection = f"z={today_z:.2f} < 板別閾值 {thresholds.z_min}"
    elif days_z_above_2 < 4:
        rejection = f"過去 10 日僅 {days_z_above_2} 天 z>2，未滿 4 天"
    elif days_z_above_3 < 1:
        rejection = "過去 10 日無 z>3 的強信號日"
    elif direction == "selling":
        if inner_ratio is not None:
            rejection = f"內盤比 {inner_ratio:.2f} > 0.55，疑似出貨"
        else:
            pos = (today_close - today_low) / (today_high - today_low) if today_high > today_low else 0
            rejection = f"OHLC 收盤位 {pos:.0%}（< 35%），疑似出貨"
    elif is_ex_div:
        rejection = "除權息前後 5 日內，訊號污染"
    elif price_change_5d > 15.0:
        rejection = f"5 日漲幅 {price_change_5d:.1f}% > 15%，已進末段"
    elif avg_volume_60d < thresholds.daily_volume_floor:
        rejection = f"日均量 {avg_volume_60d:.0f} 張 < {thresholds.daily_volume_floor}，流動性不足"
    elif market_cap_btw is not None and market_cap_btw < thresholds.market_cap_floor_btw:
        rejection = f"市值 {market_cap_btw:.0f} 億 < {thresholds.market_cap_floor_btw} 億"

    triggered = rejection is None and score >= score_threshold

    return VolumeAnomalySignal(
        ticker=ticker,
        as_of=as_of,
        board=board,
        modified_z=round(today_z, 3),
        median_log_vol_60d=round(median_log_vol, 3),
        mad_log_vol_60d=round(mad_log_vol, 4),
        days_z_above_2=days_z_above_2,
        days_z_above_3=days_z_above_3,
        max_z_recent_10d=round(max_z_recent, 3),
        inner_ratio=round(inner_ratio, 3) if inner_ratio is not None else None,
        direction=direction,
        close=round(today_close, 2),
        price_change_5d_pct=round(price_change_5d, 2),
        above_200ma=above_200ma,
        avg_volume_60d=round(avg_volume_60d, 0),
        market_cap_btw=market_cap_btw,
        is_ex_dividend_window=is_ex_div,
        score=round(score, 1),
        triggered=triggered,
        rejection_reason=rejection,
        breakdown=breakdown,
    )


# ─────────────────────────────────────────
# 批次掃描 + 歷史紀錄
# ─────────────────────────────────────────
def screen_universe(
    universe: dict[str, dict],
    as_of: date,
    score_threshold: float = 70.0,
) -> list[VolumeAnomalySignal]:
    """
    對 universe 內所有 ticker 跑掃描，回傳全部訊號（含未觸發，便於診斷）。

    universe 結構：
      {
        ticker: {
          "ohlcv": pd.DataFrame,
          "inner_outer": pd.DataFrame | None,
          "market_cap_btw": float | None,
          "board": "TWSE" | "OTC",
          "ex_dividend_dates": pd.DataFrame | None,
        }
      }
    """
    results = []
    for ticker, payload in universe.items():
        try:
            sig = scan_volume_anomaly(
                ticker=ticker,
                ohlcv=payload["ohlcv"],
                as_of=as_of,
                inner_outer=payload.get("inner_outer"),
                market_cap_btw=payload.get("market_cap_btw"),
                board=payload.get("board", "TWSE"),
                ex_dividend_dates=payload.get("ex_dividend_dates"),
                score_threshold=score_threshold,
            )
            if sig is not None:
                results.append(sig)
        except Exception:
            continue
    return results


def append_to_history(
    history_path,
    signals: list[VolumeAnomalySignal],
) -> None:
    """
    把今日觸發的訊號 append 到 data/state/volume_anomaly_history.parquet。
    供後續「幽靈追蹤」與 Early Hunter 雙重確認查詢使用。
    """
    from pathlib import Path
    history_path = Path(history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)

    triggered = [s for s in signals if s.triggered]
    if not triggered:
        return

    rows = [
        {
            "ticker": s.ticker,
            "trigger_date": s.as_of,
            "board": s.board,
            "modified_z": s.modified_z,
            "score": s.score,
            "inner_ratio": s.inner_ratio,
            "direction": s.direction,
            "close_at_trigger": s.close,
            "avg_volume_60d": s.avg_volume_60d,
        }
        for s in triggered
    ]
    new_df = pd.DataFrame(rows)

    if history_path.exists():
        old_df = pd.read_parquet(history_path)
        # 去重：同一 ticker 同一天只保留一次
        combined = pd.concat([old_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["ticker", "trigger_date"], keep="last")
    else:
        combined = new_df

    combined.to_parquet(history_path, index=False)


def lookup_recent_anomalies(
    history_path,
    ticker: str,
    today: date,
    lookback_min_days: int = 90,
    lookback_max_days: int = 180,
) -> list[date]:
    """
    查 ticker 在 [today - lookback_max_days, today - lookback_min_days] 區間內
    曾被偵測為異常量能的日期清單。

    Early Hunter 觸發時用此函式做雙重確認：
      若該 ticker 在 3-6 個月前曾出現吃貨期訊號 → ⭐ 雙重確認標記
    """
    from pathlib import Path
    history_path = Path(history_path)
    if not history_path.exists():
        return []

    df = pd.read_parquet(history_path)
    if df.empty:
        return []

    df["trigger_date"] = pd.to_datetime(df["trigger_date"]).dt.date
    df = df[df["ticker"].astype(str) == str(ticker)]

    window_end = today - timedelta(days=lookback_min_days)
    window_start = today - timedelta(days=lookback_max_days)

    matches = df[(df["trigger_date"] >= window_start) & (df["trigger_date"] <= window_end)]
    return sorted(matches["trigger_date"].tolist())
