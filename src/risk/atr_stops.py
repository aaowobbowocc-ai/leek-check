"""
ATR 動態止損 / 止盈 — 單筆交易全週期的停損管理（計畫 §3.3）。

- 初始止損 = entry − k_stop × ATR（預設 k_stop = 2.0）
- 目標價   = entry + k_target × ATR（預設 k_target = 3.0）→ 風報比 1.5
- 移動止盈：股價觸及 +1×ATR 利潤時，把止損上移到成本（保本）
- 進一步到 +2×ATR 時，止損上移到 +1×ATR（鎖住一半利潤）

高波 / 狂波機制下由 regime_detector 調整乘數（例如放寬到 2.5）。

Phase 13 全數撤銷（2026-04-24）：
  - 13-1 結構止損：四年回放顯示前 20 日低幾乎從未比 1.5×ATR 緊，實質零影響
  - 13-2 布林部分出場：強勢股「飆著上軌走」→ 2024 Sharpe 1.49 → 0.48 慘退
  - 13-3 MACD 背離作為 target 折扣：觸發罕見且 2024 微損（-0.11 Sharpe）
  結論：對本 16 檔半導體設備 watchlist 而言，簡單趨勢跟隨優於指標微操。
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class StopState:
    entry: float
    atr: float
    stop: float
    target: float
    k_stop: float = 2.0
    k_target: float = 3.0
    locked_profit_steps: int = 0   # 已鎖利階數（0 / 1 / 2）
    running_high: float = 0.0       # 進場後的累計高點（吊燈止損用）
    chandelier_k: float = 2.0       # 吊燈止損乘數（最高價 − k × ATR）


def initial_stops(
    entry: float,
    atr: float,
    k_stop: float = 2.0,
    k_target: float = 3.0,
    chandelier_k: float = 2.0,
) -> StopState:
    return StopState(
        entry=entry,
        atr=atr,
        stop=entry - k_stop * atr,
        target=entry + k_target * atr,
        k_stop=k_stop,
        k_target=k_target,
        locked_profit_steps=0,
        running_high=entry,
        chandelier_k=chandelier_k,
    )


def trail(state: StopState, latest_high: float) -> StopState:
    """
    依最新高點更新鎖利狀態。
    - 股價達 entry + 1×ATR 且尚未鎖利 → 止損上移到成本
    - 股價達 entry + 2×ATR 且已達 step 1 → 止損上移到 entry + 1×ATR
    - step 2 之後啟動吊燈止損（Chandelier Exit）：stop = max(stop, running_high − k × ATR)
      抱住強勢主升段，同時在創高後回落時果斷退出
    """
    if state.atr <= 0:
        return state

    step = state.locked_profit_steps
    new_stop = state.stop
    running_high = max(state.running_high, latest_high)

    if step < 1 and latest_high >= state.entry + 1.0 * state.atr:
        new_stop = max(new_stop, state.entry)
        step = 1
    if step < 2 and latest_high >= state.entry + 2.0 * state.atr:
        new_stop = max(new_stop, state.entry + 1.0 * state.atr)
        step = 2
    if step >= 2:
        chandelier = running_high - state.chandelier_k * state.atr
        new_stop = max(new_stop, chandelier)

    if (new_stop == state.stop and step == state.locked_profit_steps
            and running_high == state.running_high):
        return state
    return replace(
        state, stop=new_stop, locked_profit_steps=step, running_high=running_high
    )


def exit_signal(state: StopState, bar_low: float, bar_high: float) -> str | None:
    """
    根據當日 OHLC 的 low / high 判斷是否觸發出場。
    回傳 'stop' / 'target' / None。若同時觸發，保守回傳 'stop'（計畫 §8.1）。
    """
    hit_stop = bar_low <= state.stop
    hit_target = bar_high >= state.target
    if hit_stop and hit_target:
        return "stop"
    if hit_stop:
        return "stop"
    if hit_target:
        return "target"
    return None
