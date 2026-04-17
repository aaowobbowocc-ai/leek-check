"""
部位規模計算 — Half-Kelly + Fixed Fractional + 流動性限制（計畫 §3.2）。

單筆最大風險金額 = min(
    總資金 × max_per_trade_pct,               # Fixed Fractional
    kelly_fraction × 凱利分數 × 可用現金,     # Half-Kelly
    max_single_position_pct × 總資金,        # 單檔部位上限
    T−1 成交量 × liquidity_volume_pct × 股價, # 流動性限制
)

凱利分數公式（連續型，勝率 p 盈虧比 b）：
    f* = (p × b − (1 − p)) / b
    當 b ≤ 0 或 p × b ≤ (1 − p) → f* ≤ 0，部位 0

回傳股數（不是金額）— 自動換算為台股 1000 股/張的整張數；
允許零股回傳（小資金情境）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingInput:
    total_equity: float          # 總資金（現金 + 持倉市值）
    available_cash: float        # 短線可用現金
    entry_price: float
    stop_price: float
    recent_volume: int           # T−1 成交量（股）
    win_rate: float              # 歷史勝率 p (0–1)
    avg_win: float               # 平均獲利（報酬率，例如 0.08 = 8%）
    avg_loss: float              # 平均虧損（絕對值，正數，例如 0.04 = 4%）
    max_per_trade_pct: float = 2.0
    max_single_position_pct: float = 20.0
    kelly_fraction: float = 0.5
    liquidity_volume_pct: float = 1.0


@dataclass(frozen=True)
class SizingResult:
    shares: int
    dollar_risk: float           # 預計最大虧損金額
    dollar_exposure: float       # 預計部位金額
    binding_constraint: str      # 哪個限制卡住（用於晨報說明）


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """經典凱利公式，回傳 [0, 1]（負值給 0）。"""
    if avg_loss <= 0 or avg_win <= 0:
        return 0.0
    b = avg_win / avg_loss
    f = (win_rate * b - (1.0 - win_rate)) / b
    return max(0.0, min(1.0, f))


def size_position(spec: SizingInput) -> SizingResult:
    risk_per_share = spec.entry_price - spec.stop_price
    if risk_per_share <= 0 or spec.entry_price <= 0:
        return SizingResult(0, 0.0, 0.0, "invalid_stop")

    fixed_risk = spec.total_equity * spec.max_per_trade_pct / 100.0
    kelly_f = kelly_fraction(spec.win_rate, spec.avg_win, spec.avg_loss)
    kelly_risk = spec.kelly_fraction * kelly_f * spec.available_cash
    position_cap = spec.max_single_position_pct * spec.total_equity / 100.0
    liquidity_cap = (
        spec.recent_volume * spec.liquidity_volume_pct / 100.0 * spec.entry_price
    )
    cash_cap = spec.available_cash

    candidates = {
        "fixed_fractional": fixed_risk,
        "half_kelly": kelly_risk,
        "position_cap": position_cap,
        "liquidity": liquidity_cap,
        "cash": cash_cap,
    }

    # Kelly 限制的是「部位」，其他幾個也是「部位」，但 fixed_fractional 與凱利公式原生限制的是「虧損金額」。
    # 為避免語意混淆，我們統一成：risk_dollar = 每股風險 × 股數 ≤ min(fixed_risk, kelly_risk)
    # 其他上限則是部位金額。取最嚴格者。
    risk_budget = min(fixed_risk, kelly_risk) if kelly_risk > 0 else fixed_risk
    exposure_budget = min(position_cap, liquidity_cap, cash_cap)

    shares_by_risk = int(risk_budget / risk_per_share) if risk_budget > 0 else 0
    shares_by_exposure = int(exposure_budget / spec.entry_price) if exposure_budget > 0 else 0
    shares = max(0, min(shares_by_risk, shares_by_exposure))

    if shares == 0:
        binding = _binding_constraint(candidates, 0)
        return SizingResult(0, 0.0, 0.0, binding)

    dollar_risk = shares * risk_per_share
    dollar_exposure = shares * spec.entry_price
    binding = _binding_constraint(
        candidates, shares, risk_per_share, spec.entry_price
    )
    return SizingResult(shares, round(dollar_risk, 2), round(dollar_exposure, 2), binding)


def _binding_constraint(
    candidates: dict[str, float],
    shares: int,
    risk_per_share: float = 0.0,
    entry_price: float = 0.0,
) -> str:
    if shares == 0:
        tightest = min(candidates, key=lambda k: candidates[k])
        return tightest
    # 判斷實際卡住部位的限制：將每個 cap 換算成能允許的股數
    capacities: dict[str, float] = {}
    for key, val in candidates.items():
        if key in ("fixed_fractional", "half_kelly") and risk_per_share > 0:
            capacities[key] = val / risk_per_share
        elif entry_price > 0:
            capacities[key] = val / entry_price
    if not capacities:
        return "unknown"
    return min(capacities, key=lambda k: capacities[k])
