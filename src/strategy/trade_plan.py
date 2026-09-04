"""
Trade Plan Generator（Phase 18b v2 — 軌跡回測校準版）。

每個 triggered Vol Anomaly 訊號產出可執行的交易計畫。

設計依據（v2 軌跡回測 — 56 樣本 × 6 年觀察）：
  策略對比結果：
    持滿 60 天                : mean +14.2%  median  +2.8%  hit15 30.4%  fail 19.6%
    持滿 252 天               : mean +27.6%  median  +6.9%  hit15 46.4%  fail 26.8%
    持滿 756 天 (3y)          : mean +56.2%  median  +8.3%  hit15 48.2%  fail 33.9%
    Trailing -25pp from peak  : mean +17.2%  median +15.4%  hit15 51.8%  fail 23.2%  ⭐
    C: 252d + MA × 0.95       : mean +27.0%  median  +1.8%  hit15 42.9%  fail 32.1%

  結論：
    - 「Trailing -25pp」 median 最高 (+15.4%)、hit-rate 最高 (51.8%)
    - 「200MA × 0.95 結構停損」會誤殺 32% 中途回撤後反彈的好單
    - 「+15%/+30% 雙層停利」反而錯失主升段（達 +30% 後通常還會繼續漲）

出場規則（最簡單、實證最優）：
  1. 動態：**從歷史高點回撤 25pp 即出**（trailing stop）
  2. 硬停損：**跌破 200MA × 0.85**（極端情境 — 不誤殺中途回撤）
  3. 不設時間上限（讓贏家奔跑）

風險警告：
  - trailing 觸發前 median 最大回撤 -20.8% — 心理紀律要求高
  - 從未漲 +5% 的「假信號」佔 9%
  - 幽靈追蹤期建議 paper 跟蹤 3-6 個月再考慮真倉
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd


ConfidenceLevel = Literal["high", "medium", "low", "reject"]


@dataclass(frozen=True)
class TradePlan:
    """單一訊號的可執行交易計畫（v2 軌跡校準版）。"""
    ticker: str
    as_of: date
    reference_close: float

    # 進場
    entry_low: float
    entry_high: float
    entry_zone_pct_below: float

    # 硬停損（極端情境）
    hard_stop: float
    hard_stop_pct: float           # vs entry_high
    hard_stop_logic: str

    # 動態出場
    trailing_drawdown_pp: float    # 從高點回撤 N pp 出

    # 預估時程（基於回測中位數，僅供參考）
    expected_15pct_days: int       # 達 +15% 的中位天數（32 天）
    expected_30pct_days: int       # 達 +30% 的中位天數（50 天）
    expected_peak_days: int        # 達高點的中位天數（281 天）

    # 部位
    confidence: ConfidenceLevel
    suggested_position_pct: float
    suggested_position_twd: int

    reasoning: str


# ─────────────────────────────────────────
# 200MA
# ─────────────────────────────────────────
def compute_ma200(ohlcv: pd.DataFrame) -> float | None:
    if ohlcv is None or ohlcv.empty or len(ohlcv) < 200:
        return None
    return float(ohlcv.sort_values("date")["close"].tail(200).mean())


# ─────────────────────────────────────────
# 信心等級判定
# ─────────────────────────────────────────
def determine_confidence(
    z: float,
    chip_level: str | None,
    direction: str,
) -> ConfidenceLevel:
    """信心等級基於 z 甜蜜區 + 籌碼方向 + 內盤比方向。"""
    if chip_level == "distribution":
        return "reject"
    if direction == "selling":
        return "reject"

    in_sweet_spot = 3.0 <= z < 3.5

    if in_sweet_spot:
        if chip_level in ("strong_accumulation", "moderate_accumulation"):
            return "high"
        if direction == "buying":
            return "high"
        return "medium"

    if z < 3.0 or z >= 3.5:
        return "low"
    return "medium"


CONFIDENCE_POSITION_PCT = {
    "high": 1.5,
    "medium": 1.0,
    "low": 0.5,
    "reject": 0.0,
}


# ─────────────────────────────────────────
# 主生成函式
# ─────────────────────────────────────────
def generate_trade_plan(
    ticker: str,
    as_of: date,
    close: float,
    z: float,
    direction: str,
    chip_level: str | None,
    ohlcv: pd.DataFrame,
    total_assets_twd: int = 600_000,
    entry_pullback_pct: float = 0.03,
    trailing_drawdown_pp: float = 25.0,
    hard_stop_ma_pct: float = 0.85,
) -> TradePlan | None:
    """
    根據 Vol Anomaly 訊號產出可執行的交易計畫（v2 軌跡校準版）。

    主要規則：
      - 進場：close × [-3%, 0%] 限價
      - 動態出場：從歷史高點回撤 25pp 即出（trailing stop）
      - 硬停損：跌破 200MA × 0.85（極端情境）
      - 不設時間上限
    """
    ma200 = compute_ma200(ohlcv)

    entry_high = close
    entry_low = close * (1 - entry_pullback_pct)

    # 硬停損：用 200MA × 0.85；無 200MA 時 fallback 到 close × 0.80
    if ma200 is not None:
        hard_stop = ma200 * hard_stop_ma_pct
        hard_stop_logic = f"200MA × {hard_stop_ma_pct} ({ma200:.2f} → {hard_stop:.2f})"
    else:
        hard_stop = close * 0.80
        hard_stop_logic = "close × 0.80（無 200MA 資料 fallback）"

    # 確保硬停損在 entry 下方
    if hard_stop >= entry_low * 0.95:
        hard_stop = entry_low * 0.85
        hard_stop_logic += "（已調整避免過早觸發）"

    hard_stop_pct = (hard_stop / entry_high - 1.0) * 100

    confidence = determine_confidence(z, chip_level, direction)
    pos_pct = CONFIDENCE_POSITION_PCT[confidence]
    pos_twd = int(total_assets_twd * pos_pct / 100)

    confidence_reason = {
        "high": "z 落在甜蜜區 3.0-3.5 + 籌碼/方向多項確認",
        "medium": "z 甜蜜但方向尚未確認（內盤比資料缺）",
        "low": "z 不在甜蜜區，alpha 較弱，僅實驗性追蹤",
        "reject": "籌碼或方向顯示出貨，**不建議交易**",
    }
    reasoning = (
        f"z={z:.2f} | direction={direction} | chip={chip_level or 'N/A'} → "
        f"{confidence_reason[confidence]}"
    )

    return TradePlan(
        ticker=ticker,
        as_of=as_of,
        reference_close=round(close, 2),
        entry_low=round(entry_low, 2),
        entry_high=round(entry_high, 2),
        entry_zone_pct_below=-entry_pullback_pct * 100,
        hard_stop=round(hard_stop, 2),
        hard_stop_pct=round(hard_stop_pct, 1),
        hard_stop_logic=hard_stop_logic,
        trailing_drawdown_pp=trailing_drawdown_pp,
        expected_15pct_days=32,
        expected_30pct_days=50,
        expected_peak_days=281,
        confidence=confidence,
        suggested_position_pct=pos_pct,
        suggested_position_twd=pos_twd,
        reasoning=reasoning,
    )


# ─────────────────────────────────────────
# 渲染
# ─────────────────────────────────────────
def render_trade_plan_block(plan: TradePlan) -> str:
    badge = {
        "high": "🟢 高信心",
        "medium": "🟡 中信心",
        "low": "🟠 低信心（實驗）",
        "reject": "🔴 拒絕（不交易）",
    }[plan.confidence]

    # 查股票名稱
    try:
        from src.strategy.volume_anomaly_scanner import lookup_ticker_name
        name = lookup_ticker_name(plan.ticker)
    except Exception:
        name = plan.ticker
    label = f"{plan.ticker} {name}" if name and name != plan.ticker else plan.ticker

    if plan.confidence == "reject":
        return (
            f"  - **{label}** {badge} — {plan.reasoning}\n"
            f"    停止建議任何進場操作。\n"
        )

    return (
        f"  - **{label}** {badge}（建議部位 {plan.suggested_position_pct:.1f}%，"
        f"≈ NT$ {plan.suggested_position_twd:,}）\n"
        f"    - 進場：限價 **{plan.entry_low:.2f} ~ {plan.entry_high:.2f}** "
        f"（{plan.entry_zone_pct_below:+.1f}% ~ 0%）\n"
        f"    - 出場規則（核心）：**從歷史高點回撤 {plan.trailing_drawdown_pp:.0f}pp 即出**"
        f"（trailing stop）\n"
        f"    - 硬停損（極端）：**{plan.hard_stop:.2f}**（{plan.hard_stop_pct:+.1f}%，"
        f"{plan.hard_stop_logic}）\n"
        f"    - 不主動停利、不主動停損、不設時間上限 — 讓贏家奔跑\n"
        f"    - 參考時程：+15% 中位 {plan.expected_15pct_days} 天 / "
        f"+30% 中位 {plan.expected_30pct_days} 天 / "
        f"高點中位 {plan.expected_peak_days} 天\n"
        f"    - {plan.reasoning}\n"
    )
