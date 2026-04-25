"""
晨報「全球配置 + 部位建議」生成器（Phase 17a）。

每日晨報根據以下狀態給結構化建議：
  1. 市場估值狀態（TAIEX vs 200MA 偏離 + 0050 P/E 百分位）
  2. DCA 部位比例建議（依估值狀態給「立即進場 / DCA / Buffer」比例）
  3. 個股追蹤：2345 智邦移動停損距離、6770 力積電部分減碼進度
  4. 季度再平衡提醒（每季初）
  5. 持股配置偏離警報

設計參考 memory/project_position_sizing.md + project_global_allocation.md

純函式設計：給 inputs (TAIEX df, 持股, 配置目標) → 輸出 Markdown section。
不打 API、不寫檔。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import pandas as pd


# ─────────────────────────────────────────
# 市場狀態判定
# ─────────────────────────────────────────
RegimeLevel = Literal["cheap", "fair", "expensive", "overheated", "crashing"]

REGIME_RECOMMENDATIONS: dict[RegimeLevel, dict[str, str]] = {
    "cheap": {
        "icon": "🟢",
        "label": "便宜",
        "lump_sum": "70%",
        "monthly_dca": "月薪 50%",
        "buffer": "10%",
        "action": "積極進場、加碼台股",
    },
    "fair": {
        "icon": "🟡",
        "label": "合理",
        "lump_sum": "40%",
        "monthly_dca": "月薪 30%",
        "buffer": "30%",
        "action": "正常 DCA、維持配置",
    },
    "expensive": {
        "icon": "🟠",
        "label": "偏貴",
        "lump_sum": "20%",
        "monthly_dca": "月薪 20%",
        "buffer": "50%",
        "action": "減速進場、海外分散",
    },
    "overheated": {
        "icon": "🔴",
        "label": "過熱",
        "lump_sum": "10-20%",
        "monthly_dca": "月薪 10-15%",
        "buffer": "60-70%",
        "action": "高度警戒、準備崩盤加碼資金",
    },
    "crashing": {
        "icon": "⚫",
        "label": "崩盤中",
        "lump_sum": "啟用 buffer 加碼",
        "monthly_dca": "持續",
        "buffer": "0%",
        "action": "**動用 buffer 分批加碼** — 這是 5 年難得的機會",
    },
}


@dataclass(frozen=True)
class MarketRegime:
    level: RegimeLevel
    taiex_close: float
    ma200: float
    deviation_pct: float
    reason: str


def detect_regime(taiex_ohlcv: pd.DataFrame) -> MarketRegime | None:
    """
    用 TAIEX vs 200MA 偏離度 + 月跌幅判定市場狀態。
    P/E 百分位需另外取 0050 PER cache（呼叫端可選傳）。
    """
    if taiex_ohlcv is None or taiex_ohlcv.empty:
        return None
    df = taiex_ohlcv.sort_values("date").reset_index(drop=True)
    if len(df) < 200:
        return None

    last_close = float(df.iloc[-1]["close"])
    ma200 = float(df["close"].tail(200).mean())
    deviation = (last_close / ma200 - 1.0) * 100.0

    # 月跌幅（近 21 個交易日）
    if len(df) >= 22:
        month_ago = float(df.iloc[-22]["close"])
        month_change = (last_close / month_ago - 1.0) * 100.0
    else:
        month_change = 0.0

    # 規則樹
    if last_close < ma200 and month_change < -10:
        level = "crashing"
        reason = f"跌破 200MA + 月跌 {month_change:.1f}% → 崩盤期"
    elif deviation > 30:
        level = "overheated"
        reason = f"偏離 200MA +{deviation:.1f}%（過熱閾值 +30%）"
    elif deviation > 15:
        level = "expensive"
        reason = f"偏離 200MA +{deviation:.1f}%（偏貴閾值 +15%）"
    elif deviation > -15:
        level = "fair"
        reason = f"偏離 200MA {deviation:+.1f}%（合理區間 ±15%）"
    else:
        level = "cheap"
        reason = f"低於 200MA {deviation:.1f}%（便宜區間）"

    return MarketRegime(
        level=level,
        taiex_close=last_close,
        ma200=ma200,
        deviation_pct=deviation,
        reason=reason,
    )


# ─────────────────────────────────────────
# 個股追蹤：2345 智邦 OCO 停損停利
# ─────────────────────────────────────────
@dataclass(frozen=True)
class StockTracker:
    ticker: str
    name: str
    cost: float
    current: float
    stop_loss: float
    take_profit: float

    @property
    def diff_to_stop_pct(self) -> float:
        """目前距離停損的下方緩衝（正值代表還沒觸發）。"""
        return (self.current - self.stop_loss) / self.current * 100.0

    @property
    def diff_to_target_pct(self) -> float:
        """距離停利的上方距離。"""
        return (self.take_profit - self.current) / self.current * 100.0

    @property
    def status(self) -> str:
        d = self.diff_to_stop_pct
        if self.current >= self.take_profit:
            return "🎯 已觸停利，建議手動賣出鎖利"
        if d < 0:
            return "🛑 已觸停損，建議立刻賣出"
        if d < 2:
            return "🔴 距停損 < 2%，嚴密監控"
        if d < 5:
            return "🟠 距停損 < 5%，保持警覺"
        return "🟢 部位安全"


# ─────────────────────────────────────────
# 配置偏離檢查
# ─────────────────────────────────────────
@dataclass(frozen=True)
class AllocationCheck:
    bucket: str
    target_pct: float
    actual_pct: float
    deviation: float

    @property
    def needs_action(self) -> bool:
        return abs(self.deviation) > 5.0


def check_allocation_drift(
    actual: dict[str, float],   # bucket → 市值
    target: dict[str, float],   # bucket → 目標 %
    total_assets: float,
) -> list[AllocationCheck]:
    """檢查每個資產類別的實際配置 vs 目標，找出偏離 > 5% 的。"""
    out = []
    for bucket, target_pct in target.items():
        actual_value = actual.get(bucket, 0.0)
        actual_pct = (actual_value / total_assets * 100) if total_assets > 0 else 0.0
        out.append(
            AllocationCheck(
                bucket=bucket,
                target_pct=target_pct,
                actual_pct=actual_pct,
                deviation=actual_pct - target_pct,
            )
        )
    return out


def is_quarterly_rebalance_day(d: date) -> bool:
    """每季 1 月 / 4 月 / 7 月 / 10 月的第 1 個交易日。"""
    return d.month in (1, 4, 7, 10) and d.day <= 7


# ─────────────────────────────────────────
# 渲染 Markdown 區段
# ─────────────────────────────────────────
def render_allocation_section(
    regime: MarketRegime | None,
    stock_trackers: list[StockTracker],
    drift_checks: list[AllocationCheck] | None = None,
    is_rebalance_day: bool = False,
) -> str:
    """組裝給晨報的「💰 全球配置 + 部位建議」區段。"""
    lines = ["## 💰 全球配置 + 部位建議"]

    # 1. 市場狀態
    if regime is not None:
        rec = REGIME_RECOMMENDATIONS[regime.level]
        lines.append(f"\n### 市場估值狀態：{rec['icon']} **{rec['label']}**")
        lines.append(f"- TAIEX：{regime.taiex_close:,.0f} / 200MA {regime.ma200:,.0f}")
        lines.append(f"- {regime.reason}")
        lines.append(f"- **建議動作**：{rec['action']}")
        lines.append(f"  - 立即進場比例：{rec['lump_sum']}")
        lines.append(f"  - 月度 DCA：{rec['monthly_dca']}")
        lines.append(f"  - 現金 buffer：{rec['buffer']}")
    else:
        lines.append("\n### 市場估值狀態：**資料不足，無法判定**")

    # 2. 個股追蹤
    if stock_trackers:
        lines.append("\n### 個股追蹤")
        lines.append("| 代號 | 名稱 | 現價 | 停損 | 停利 | 距停損 | 狀態 |")
        lines.append("|------|------|------|------|------|--------|------|")
        for t in stock_trackers:
            lines.append(
                f"| {t.ticker} | {t.name} | {t.current:,.2f} | "
                f"{t.stop_loss:,.0f} | {t.take_profit:,.0f} | "
                f"{t.diff_to_stop_pct:+.1f}% | {t.status} |"
            )

    # 3. 季度再平衡（僅在每季初顯示）
    if is_rebalance_day:
        lines.append("\n### 🔄 季度再平衡提醒（本月觸發）")
        if drift_checks:
            need_action = [c for c in drift_checks if c.needs_action]
            if need_action:
                lines.append("以下類別偏離目標 > ±5%，建議再平衡：")
                lines.append("| 類別 | 目標 % | 實際 % | 偏離 | 建議 |")
                lines.append("|------|--------|--------|------|------|")
                for c in need_action:
                    direction = "賣出" if c.deviation > 0 else "買進"
                    lines.append(
                        f"| {c.bucket} | {c.target_pct:.0f}% | {c.actual_pct:.1f}% | "
                        f"{c.deviation:+.1f}% | {direction}補 {abs(c.deviation):.1f}% |"
                    )
            else:
                lines.append("所有類別偏離 < ±5%，**本季不需再平衡** ✅")
        else:
            lines.append("（資產配置資料未載入）")

    return "\n".join(lines) + "\n"
