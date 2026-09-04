"""
策略 Regime Gate — 動態判定哪些策略當下啟用。

針對 cross-regime 驗證後 regime-dependent 的策略：
  - 牛市才啟用 ORB / 法人 momentum / 散戶極值
  - 配對 + 0050 dealer 永遠啟用 (跨 4 期 robust)
  - 熊市自動全暫停 regime-dep 策略
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass
class CurrentRegime:
    trend: Literal["bull", "bear", "sideways"]
    vol_state: Literal["high", "normal", "low"]
    risk: Literal["risk_on", "risk_neutral", "risk_off"]
    cycle: Literal["early_bull", "mid_bull", "late_bull", "bear"]
    taiex_close: float
    ma60: float
    ma200: float
    realized_vol_30d: float
    vix: float
    timestamp: str


def detect_current_regime() -> CurrentRegime:
    """偵測當下市場 regime"""
    taiex = yf.Ticker("^TWII").history(period="300d", auto_adjust=False)
    if taiex.empty:
        raise RuntimeError("TAIEX 抓不到")
    close = taiex["Close"]
    last = float(close.iloc[-1])
    ma60 = float(close.tail(60).mean())
    ma200 = float(close.tail(200).mean()) if len(close) >= 200 else last

    daily_ret = close.pct_change().dropna()
    vol_30 = float(daily_ret.tail(30).std() * np.sqrt(252) * 100)
    vol_1y = float(daily_ret.tail(252).std() * np.sqrt(252) * 100) if len(daily_ret) >= 252 else vol_30

    vix_h = yf.Ticker("^VIX").history(period="2d", auto_adjust=False)
    vix = float(vix_h["Close"].iloc[-1]) if not vix_h.empty else 20.0

    # Trend
    if last > ma200 and ma60 > ma200:
        trend = "bull"
    elif last < ma60 and ma60 < ma200:
        trend = "bear"
    else:
        trend = "sideways"

    # Vol
    ratio = vol_30 / vol_1y
    vol_state = "high" if ratio > 1.3 else ("low" if ratio < 0.7 else "normal")

    # Risk
    risk = "risk_on" if vix < 18 else ("risk_off" if vix > 25 else "risk_neutral")

    # Cycle
    pct_above = (last / ma200 - 1) * 100
    if trend == "bear" or pct_above < 0:
        cycle = "bear"
    elif pct_above > 25:
        cycle = "late_bull"
    elif pct_above > 10:
        cycle = "mid_bull"
    else:
        cycle = "early_bull"

    return CurrentRegime(
        trend=trend, vol_state=vol_state, risk=risk, cycle=cycle,
        taiex_close=last, ma60=ma60, ma200=ma200,
        realized_vol_30d=vol_30, vix=vix,
        timestamp=datetime.now().isoformat(timespec="seconds"),
    )


@dataclass
class StrategyRule:
    """策略 regime 適用條件"""
    name: str
    requires_trend: list[str] = field(default_factory=list)  # 空=任何
    requires_cycle: list[str] = field(default_factory=list)
    requires_risk: list[str] = field(default_factory=list)
    expected_alpha: float = 0.0
    backtest_status: str = ""


# 策略 → regime 規則對照（基於跨牛熊驗證）
STRATEGY_RULES = {
    # ⭐⭐⭐ 永遠啟用（4/4 期 robust + MCPT）
    "pair_2408_2344": StrategyRule(
        name="配對交易 DRAM 2408-2344",
        expected_alpha=3.16,
        backtest_status="MCPT p=0.002 (4/4 期 robust)",
    ),
    "0050_dealer_buy_3d": StrategyRule(
        name="0050 自營商連 3 日買超",
        expected_alpha=1.23,
        backtest_status="MCPT p<0.001 (4/4 期 robust)",
    ),

    # ⚠️ 牛市才啟用
    "ORB_long_2408": StrategyRule(
        name="ORB long 2408",
        requires_trend=["bull"],
        requires_cycle=["early_bull", "mid_bull"],
        requires_risk=["risk_on", "risk_neutral"],
        expected_alpha=0.99,
        backtest_status="2021-2022 熊市 +0.08%（持平）",
    ),
    "ORB_long_2485": StrategyRule(
        name="ORB long 2485",
        requires_trend=["bull"],
        requires_cycle=["early_bull", "mid_bull"],
        requires_risk=["risk_on", "risk_neutral"],
        expected_alpha=1.58,
        backtest_status="2021-2022 熊市 -0.97%（虧）",
    ),
    "inst_2308_foreign_buy_3d": StrategyRule(
        name="2308 外資連 3 日買超",
        requires_trend=["bull"],
        requires_cycle=["early_bull", "mid_bull"],
        expected_alpha=2.62,
        backtest_status="2021-2022 熊市 -1.31%",
    ),
    "inst_006208_foreign_buy_3d": StrategyRule(
        name="006208 外資連 3 日買超",
        requires_trend=["bull"],
        expected_alpha=1.84,
        backtest_status="C 期 -0.78% / D 期 +1.84%",
    ),

    # 🟡 中度啟用 (mid/late bull)
    "retail_extreme_2376": StrategyRule(
        name="2376 散戶比例極低反向 long",
        requires_trend=["bull"],
        requires_cycle=["mid_bull", "late_bull"],
        expected_alpha=13.61,
        backtest_status="只 D 期強 (+13.61pp)",
    ),

    # ⏸ 熊市策略（盤跌時才啟用）
    "limitup_break_short": StrategyRule(
        name="限漲停打開 short scalping",
        requires_trend=["bull", "sideways"],  # bear 時無漲停股
        requires_risk=["risk_on", "risk_neutral"],
        expected_alpha=5.0,  # 平均
        backtest_status="9 年 + cross-regime 部分 ticker robust",
    ),
}


def evaluate_strategies(regime: CurrentRegime) -> dict:
    """根據當下 regime 判斷各策略 enable/disable"""
    active = []
    suspended = []

    for key, rule in STRATEGY_RULES.items():
        reasons = []
        if rule.requires_trend and regime.trend not in rule.requires_trend:
            reasons.append(f"需 trend={'/'.join(rule.requires_trend)}, 現 {regime.trend}")
        if rule.requires_cycle and regime.cycle not in rule.requires_cycle:
            reasons.append(f"需 cycle={'/'.join(rule.requires_cycle)}, 現 {regime.cycle}")
        if rule.requires_risk and regime.risk not in rule.requires_risk:
            reasons.append(f"需 risk={'/'.join(rule.requires_risk)}, 現 {regime.risk}")

        if reasons:
            suspended.append({"key": key, "rule": rule, "reasons": reasons})
        else:
            active.append({"key": key, "rule": rule})

    return {"regime": regime, "active": active, "suspended": suspended}


# ════════════════════════════════════════════
# Test
# ════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 70)
    print("Strategy Regime Gate")
    print("=" * 70)

    r = detect_current_regime()
    pct = (r.taiex_close / r.ma200 - 1) * 100
    print(f"\n📊 當下 Regime ({r.timestamp})")
    print(f"  Trend:  🟢 {r.trend.upper()}  (TAIEX {r.taiex_close:,.0f}, "
          f"MA200 {r.ma200:,.0f}, MA60 {r.ma60:,.0f})")
    print(f"  Cycle:  {r.cycle.upper()}  (距 MA200 {pct:+.1f}%)")
    print(f"  Vol:    {r.vol_state.upper()}  (30d 年化 {r.realized_vol_30d:.1f}%)")
    print(f"  Risk:   {r.risk.upper()}  (VIX {r.vix:.1f})")

    result = evaluate_strategies(r)

    print(f"\n✅ 啟用策略 ({len(result['active'])}):")
    for s in result["active"]:
        print(f"  ⭐ {s['rule'].name}")
        print(f"     α {s['rule'].expected_alpha:+.2f}%, {s['rule'].backtest_status}")

    print(f"\n⏸ 暫停策略 ({len(result['suspended'])}):")
    for s in result["suspended"]:
        print(f"  - {s['rule'].name}")
        for r_msg in s["reasons"]:
            print(f"      ✗ {r_msg}")
