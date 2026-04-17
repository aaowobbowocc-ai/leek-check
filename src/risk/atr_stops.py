"""
ATR 動態止損 / 止盈 — 單筆交易全週期的停損管理（計畫 §3.3）。

- 初始止損 = entry − k_stop × ATR（預設 k_stop = 2.0）
- 目標價   = entry + k_target × ATR（預設 k_target = 3.0）→ 風報比 1.5
- 移動止盈：股價觸及 +1×ATR 利潤時，把止損上移到成本（保本）
- 進一步到 +2×ATR 時，止損上移到 +1×ATR（鎖住一半利潤）

高波 / 狂波機制下由 regime_detector 調整乘數（例如放寬到 2.5）。
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


def initial_stops(
    entry: float,
    atr: float,
    k_stop: float = 2.0,
    k_target: float = 3.0,
) -> StopState:
    return StopState(
        entry=entry,
        atr=atr,
        stop=entry - k_stop * atr,
        target=entry + k_target * atr,
        k_stop=k_stop,
        k_target=k_target,
        locked_profit_steps=0,
    )


def trail(state: StopState, latest_high: float) -> StopState:
    """
    依最新高點更新鎖利狀態。
    - 股價達 entry + 1×ATR 且尚未鎖利 → 止損上移到成本
    - 股價達 entry + 2×ATR 且已達 step 1 → 止損上移到 entry + 1×ATR
    """
    if state.atr <= 0:
        return state

    step = state.locked_profit_steps
    new_stop = state.stop

    if step < 1 and latest_high >= state.entry + 1.0 * state.atr:
        new_stop = max(new_stop, state.entry)
        step = 1
    if step < 2 and latest_high >= state.entry + 2.0 * state.atr:
        new_stop = max(new_stop, state.entry + 1.0 * state.atr)
        step = 2

    if new_stop == state.stop and step == state.locked_profit_steps:
        return state
    return replace(state, stop=new_stop, locked_profit_steps=step)


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
