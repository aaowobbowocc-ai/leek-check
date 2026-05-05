"""
Action Advisor — 整合 regime / hedge / barbell / 持倉 → 生成「今日行動指令」

被 GUI 60s tick 呼叫即時更新；被晨報每日呼叫一次。

決策邏輯（優先序由上而下）:
  1. CRASH regime + Hedge tilt >= 20 → 全力部署現金 (priority=critical)
  2. Hedge tilt 10-15 → 加倉現金，保留子彈 (priority=warning)
  3. Barbell delta > 10pp → 漸進 rebalance (priority=action)
  4. Barbell delta 5-10pp → 微調 (priority=tweak)
  5. STRONG_BULL + 配置 OK → hold + 觀望 (priority=hold)

Output: List[Action] 含 NT$ 換算、原因、來源訊號
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Priority = Literal["critical", "warning", "action", "tweak", "hold", "info"]


@dataclass
class Action:
    priority: Priority           # 警報層級
    icon: str                    # 顯示 emoji
    label: str                   # 動作標題
    amount_twd: int              # 換算 NT$ (0 = 不適用)
    ticker: str | None           # 涉及標的
    reason: str                  # 為何要做
    source: str                  # 訊號來源 (regime / hedge / barbell)


PRIORITY_ORDER = {"critical": 0, "warning": 1, "action": 2, "tweak": 3, "hold": 4, "info": 5}


def _bp_to_twd(pp: float, total_value: float) -> int:
    return int(pp / 100 * total_value)


def generate_actions(
    regime_reading,
    hedge_reading,
    barbell_target: dict,
    barbell_current,
    total_value: float,
    cash: float,
) -> list[Action]:
    """
    Args:
        regime_reading: RegimeReading from regime_section.compute_current_regime()
        hedge_reading: HedgeReading from hedge_signals.compute_hedge_reading()
        barbell_target: dict from ALLOCATION_TABLE[regime] after _apply_hedge_tilt
        barbell_current: CurrentHoldings dataclass from barbell_allocation._load_holdings()
        total_value: total portfolio value NT$
        cash: cash NT$ (for capacity guidance)
    """
    actions: list[Action] = []

    if regime_reading is None or barbell_current is None:
        actions.append(Action(
            priority="info", icon="⏸️", label="資料不足",
            amount_twd=0, ticker=None,
            reason="regime / 持倉資料缺失",
            source="system",
        ))
        return actions

    # 1. CRASH MODE
    if regime_reading.regime == "CRASH":
        deploy_amount = int(cash * 0.6)  # deploy 60% cash aggressively
        actions.append(Action(
            priority="critical",
            icon="🚨",
            label=f"CRASH 鑽石買點 — 立即部署 0050 (NT${deploy_amount:,})",
            amount_twd=deploy_amount,
            ticker="0050",
            reason=f"CRASH 9 年實證 fwd 20d +9.75% / 100% win (n=34)。當前 60d ret {regime_reading.ret_60d:+.1f}%, vol30 {regime_reading.vol_30d:.0f}%",
            source="regime_v2",
        ))
        # Add 00631L tilt for CRASH (from barbell allocation)
        leverage_target = barbell_target.get("leverage", 0)
        if leverage_target > 0:
            lev_amount = _bp_to_twd(leverage_target - barbell_current.leverage_pct, total_value)
            if lev_amount > 0:
                actions.append(Action(
                    priority="critical",
                    icon="⚡",
                    label=f"加 00631L 槓桿 +{leverage_target - barbell_current.leverage_pct:.0f}pp (NT${lev_amount:,})",
                    amount_twd=lev_amount,
                    ticker="00631L",
                    reason="CRASH 期 00631L fwd 20d +22.71% / 100% win (n=34)",
                    source="regime_v2",
                ))

    # 2. Hedge tilt critical / warning
    if hedge_reading is not None:
        tilt = hedge_reading.cash_tilt_pp
        if tilt >= 20:
            actions.append(Action(
                priority="critical",
                icon="🛡️",
                label=f"多重 hedge 觸發 → 提高現金 +{tilt}pp (NT${_bp_to_twd(tilt, total_value):,})",
                amount_twd=_bp_to_twd(tilt, total_value),
                ticker=None,
                reason="; ".join(hedge_reading.notes[:2]),
                source="hedge_signals",
            ))
        elif tilt >= 10:
            actions.append(Action(
                priority="warning",
                icon="⚠️",
                label=f"Hedge 警示 — 提高現金 +{tilt}pp (NT${_bp_to_twd(tilt, total_value):,})",
                amount_twd=_bp_to_twd(tilt, total_value),
                ticker=None,
                reason="; ".join(hedge_reading.notes[:2]),
                source="hedge_signals",
            ))

    # 3. Barbell deltas (regime adjust)
    bucket_labels = {
        "core_tw": "核心 TW (0050+00881+00947)",
        "us_00646": "美股 00646",
        "gold": "黃金 (IAU+00635U)",
        "japan_dxj": "日股 DXJ",
        "leverage": "00631L 槓桿",
        "satellite": "Revenue YoY 衛星",
        "legacy": "個股 (2345/2408/009819)",
        "cash": "現金",
    }

    deltas = []
    for key, label in bucket_labels.items():
        curr_pct = getattr(barbell_current, f"{key}_pct", 0)
        tgt_pct = barbell_target.get(key, 0)
        d = tgt_pct - curr_pct
        deltas.append((key, label, curr_pct, tgt_pct, d))

    # Sort by abs(delta), top 3
    deltas.sort(key=lambda x: -abs(x[4]))
    for key, label, curr_pct, tgt_pct, d in deltas[:5]:
        if abs(d) < 5:
            continue
        amount = abs(_bp_to_twd(d, total_value))
        if d > 0:
            verb = "加碼"
            icon = "⬆️"
        else:
            verb = "減持"
            icon = "⬇️"

        if abs(d) >= 15:
            priority = "action"
        elif abs(d) >= 8:
            priority = "tweak"
        else:
            priority = "info"

        # Skip cash actions (covered by other actions)
        if key == "cash":
            continue

        # In STRONG_BULL, downgrade aggressive add actions
        if regime_reading.regime == "STRONG_BULL" and d > 0 and abs(d) >= 15:
            priority = "tweak"  # don't push aggressive buy in mean reversion zone
            extra = "  (STRONG_BULL 慎入：分批 8-12 週)"
        else:
            extra = ""

        actions.append(Action(
            priority=priority,
            icon=icon,
            label=f"{verb} {label} {abs(d):.0f}pp (NT${amount:,}){extra}",
            amount_twd=amount,
            ticker=None,
            reason=f"current {curr_pct:.0f}% → target {tgt_pct}%",
            source="barbell",
        ))

    # 4. STRONG_BULL specific guidance
    if regime_reading.regime == "STRONG_BULL" and not actions:
        actions.append(Action(
            priority="hold",
            icon="🔴",
            label="STRONG_BULL — 暫停 DCA + 累積現金等 CRASH",
            amount_twd=0,
            ticker=None,
            reason=f"TAIEX 距 MA200 {regime_reading.dist_ma200:+.1f}%；fwd 20d 跨期 +0.31% (2020-22) vs -2.13% (2023-25) 不穩",
            source="regime_v2",
        ))

    # 5. Default fallback
    if not actions:
        actions.append(Action(
            priority="hold",
            icon="✅",
            label="配置已達標 — 持續觀察",
            amount_twd=0,
            ticker=None,
            reason=f"Regime {regime_reading.regime}；無明顯 delta 需調整",
            source="system",
        ))

    # Sort by priority
    actions.sort(key=lambda a: PRIORITY_ORDER.get(a.priority, 99))
    return actions


def render_hero_section() -> str:
    """晨報用 Hero Action Panel markdown."""
    try:
        from .regime_section import compute_current_regime
        from .hedge_signals import compute_hedge_reading
        from .barbell_allocation import (
            ALLOCATION_TABLE, _apply_hedge_tilt, _load_holdings,
        )
    except Exception as e:
        return f"## 🎯 今日行動指令\n\n_系統初始化失敗: {e}_\n"

    regime_r = compute_current_regime()
    hedge_r = compute_hedge_reading()
    holdings = _load_holdings()
    if not (regime_r and holdings):
        return "## 🎯 今日行動指令\n\n_資料不足_\n"

    base_target = ALLOCATION_TABLE.get(regime_r.regime, {})
    target, _, _ = _apply_hedge_tilt(base_target)
    actions = generate_actions(
        regime_r, hedge_r, target, holdings,
        holdings.total_value, getattr(holdings, "cash_pct", 0) * holdings.total_value / 100,
    )

    # Compute available cash for today
    cash_total = holdings.cash_pct / 100 * holdings.total_value
    today_budget = min(int(cash_total * 0.1), 30000)  # 10% or NT$30K cap

    regime_color = {
        "CRASH": "🚨", "BEAR": "🟠", "SIDEWAYS": "🟡",
        "BULL_TREND": "🟢", "STRONG_BULL": "🔴",
    }.get(regime_r.regime, "⚪")

    lines = [
        "## 🎯 今日行動指令（Hero Panel）",
        "",
        f"**狀態**: {regime_color} **`{regime_r.regime}`** | "
        f"TAIEX {regime_r.dist_ma200:+.1f}% MA200 | "
        f"VIX {hedge_r.vix_current:.1f} | "
        f"Hedge tilt: {hedge_r.cash_tilt_pp:+d}pp",
        "",
        "**今日 Top 行動**:",
        "",
    ]

    # Top 3-5 actions
    for i, action in enumerate(actions[:5], 1):
        marker = {
            "critical": "🚨", "warning": "⚠️", "action": "📌",
            "tweak": "🔧", "hold": "✅", "info": "ℹ️",
        }.get(action.priority, "•")
        lines.append(f"{i}. {marker} {action.icon} **{action.label}**")
        if action.reason:
            lines.append(f"   _原因: {action.reason}_")

    lines.append("")
    lines.append(
        f"**💰 資金狀態**: 現金 NT${cash_total:,.0f} ({holdings.cash_pct:.0f}%) | "
        f"今日建議動用 ≤ NT${today_budget:,}"
    )
    lines.append("")
    lines.append(
        f"_整合來源: 5-regime classifier + 5 hedge signals + 8-bucket barbell vs deployment v4。每 60s 自動 refresh._"
    )
    lines.append("")
    return "\n".join(lines)
