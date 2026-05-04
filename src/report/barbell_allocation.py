"""
Barbell Allocation Advisor (regime-aware).

依當前 regime 給出建議資產配置，並對比 user 實際持倉計算 deltas。

Allocation Rules（基於 regime_strategy_map.md 9 年實證）:

| Regime      | Core 0050 | 00631L Tilt | Satellite (Revenue YoY) | Cash |
|-------------|-----------|-------------|-------------------------|------|
| CRASH       | 50%       | 30%         | 0%                      | 20%  |
| BEAR        | 60%       | 0%          | 0%                      | 40%  |
| SIDEWAYS    | 55%       | 0%          | 25%                     | 20%  |
| BULL_TREND  | 55%       | 25%         | 10%                     | 10%  |
| STRONG_BULL | 50%       | 0%          | 0%                      | 50%  |

Notes:
  - STRONG_BULL: 取消 DCA + 累積現金等 CRASH（fwd 20d -0.62% 是 mean reversion 區）
  - BEAR: 不加 00631L（daily reset decay）
  - CRASH: 鑽石買點（100% win, 0050 +9.75%/20d），00631L tilt 加倍
  - 衛星僅在 SIDEWAYS / BULL_TREND（Revenue YoY 廣度因子適用區）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .regime_section import compute_current_regime

ROOT = Path(__file__).resolve().parents[2]
ASSETS_JSON = ROOT / "data" / "assets.json"


# Target allocation per regime (must sum to 100%)
ALLOCATION_TABLE: dict[str, dict[str, int]] = {
    "CRASH":       {"core_0050": 50, "leverage_00631L": 30, "satellite": 0,  "cash": 20},
    "BEAR":        {"core_0050": 60, "leverage_00631L": 0,  "satellite": 0,  "cash": 40},
    "SIDEWAYS":    {"core_0050": 55, "leverage_00631L": 0,  "satellite": 25, "cash": 20},
    "BULL_TREND":  {"core_0050": 55, "leverage_00631L": 25, "satellite": 10, "cash": 10},
    "STRONG_BULL": {"core_0050": 50, "leverage_00631L": 0,  "satellite": 0,  "cash": 50},
}

REGIME_NOTES = {
    "CRASH":       "🚨 鑽石買點 — 加碼 00631L 30% (fwd 20d +22.71%, 100% win)；分批 5 個交易日進場",
    "BEAR":        "🟠 防禦 — 不買 00631L（decay）；保留 40% 現金等 CRASH 訊號",
    "SIDEWAYS":    "🟡 廣度因子適用 — 25% Revenue YoY 衛星（max=20 yoy_asc 預期 +17%/yr）",
    "BULL_TREND":  "🟢 標準持有 — 25% 00631L 吃趨勢；10% satellite 補位",
    "STRONG_BULL": "🔴 mean reversion 區 — 取消 DCA，取出 50% 現金等下次 CRASH（fwd 20d -0.62%）",
}


@dataclass
class CurrentHoldings:
    """User 當前持倉摘要（基於 assets.json）。
    金額已乘 USER_UUID 遮罩才印出。原值保留供計算 delta。"""
    core_0050_pct: float
    leverage_00631L_pct: float
    satellite_pct: float
    cash_pct: float
    total_value: float


def _latest_close(ticker: str) -> float:
    """從 OHLCV cache 取最新收盤價。"""
    p = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / f"{ticker}.parquet"
    if not p.exists():
        return 0.0
    try:
        import pandas as pd
        df = pd.read_parquet(p, columns=["date", "close"])
        return float(df["close"].iloc[-1])
    except Exception:
        return 0.0


def _load_holdings() -> CurrentHoldings | None:
    """從 assets.json 計算當前配置百分比。

    assets.json 結構: {cash, holdings: {long_term: [...], short_term: [...]}}
    每筆持股 dict: {ticker, shares, cost, cost_incl_fee}

    分類規則:
      - "0050"        → core
      - "00631L"      → leverage
      - 其他 ticker   → satellite
      - cash 欄位     → cash
    """
    if not ASSETS_JSON.exists():
        return None
    try:
        data = json.loads(ASSETS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None

    cash = float(data.get("cash", 0) or 0)
    holdings_groups = data.get("holdings", {})

    core = leverage = satellite = 0.0
    for _bucket, items in holdings_groups.items():
        if not isinstance(items, list):
            continue
        for h in items:
            ticker = str(h.get("ticker", "")).strip()
            shares = float(h.get("shares", 0) or 0)
            # 用最新收盤價估算市值（cost basis 不準）
            price = _latest_close(ticker)
            if price <= 0:
                # fallback to cost
                price = float(h.get("cost_incl_fee", 0) or h.get("cost", 0) or 0)
            value = shares * price
            if ticker == "0050":
                core += value
            elif ticker == "00631L":
                leverage += value
            else:
                satellite += value

    total = core + leverage + satellite + cash
    if total <= 0:
        return None
    return CurrentHoldings(
        core_0050_pct=core / total * 100,
        leverage_00631L_pct=leverage / total * 100,
        satellite_pct=satellite / total * 100,
        cash_pct=cash / total * 100,
        total_value=total,
    )


def _apply_hedge_tilt(target: dict[str, int]) -> tuple[dict[str, int], int, list[str]]:
    """Apply hedge cash tilt: shift cash_tilt_pp from leverage/satellite to cash."""
    try:
        from .hedge_signals import compute_hedge_reading
    except Exception:
        return target, 0, []
    h = compute_hedge_reading()
    tilt = h.cash_tilt_pp
    if tilt == 0:
        return target, 0, h.notes
    adjusted = dict(target)
    # Take from leverage first, then satellite
    take_from_leverage = min(adjusted["leverage_00631L"], tilt)
    adjusted["leverage_00631L"] -= take_from_leverage
    remaining = tilt - take_from_leverage
    take_from_satellite = min(adjusted["satellite"], remaining)
    adjusted["satellite"] -= take_from_satellite
    remaining -= take_from_satellite
    # If still need to take, reduce core (last resort)
    if remaining > 0:
        adjusted["core_0050"] -= remaining
    adjusted["cash"] = 100 - adjusted["core_0050"] - adjusted["leverage_00631L"] - adjusted["satellite"]
    return adjusted, tilt, h.notes


def render_barbell_section() -> str:
    reading = compute_current_regime()
    if reading is None:
        return ""

    base_target = ALLOCATION_TABLE.get(reading.regime)
    if base_target is None:
        return ""

    target, tilt, hedge_notes = _apply_hedge_tilt(base_target)

    lines = [
        "## 💼 Barbell 配置建議（regime-aware + hedge overlay）",
        "",
        f"**當前 regime: `{reading.regime}`** （TAIEX dist MA200 {reading.dist_ma200:+.1f}%）",
        "",
    ]
    if tilt > 0:
        lines.append(f"⚠️ **Hedge overlay 啟動**：cash +{tilt}pp（leverage/satellite 減倉）")
        for note in hedge_notes:
            lines.append(f"  - {note}")
        lines.append("")
    lines.extend([
        "### 目標配置",
        "",
        "| 類別 | 目標 % | 說明 |",
        "|------|--------|------|",
        f"| 核心 0050 | **{target['core_0050']}%** | 吃 TSMC AI 集中度 |",
        f"| 00631L 槓桿 tilt | **{target['leverage_00631L']}%** | "
        f"{'2x leverage（避 daily decay）' if target['leverage_00631L'] > 0 else '不持有（decay 風險）'}|",
        f"| Revenue YoY 衛星 | **{target['satellite']}%** | "
        f"{'max=20 yoy_asc 廣度策略' if target['satellite'] > 0 else '此 regime 不適用'} |",
        f"| 現金 | **{target['cash']}%** | "
        f"{'累積等 CRASH 訊號' if target['cash'] >= 30 else '日常流動性'} |",
        "",
        f"**規則重點**：{REGIME_NOTES.get(reading.regime, '')}",
        "",
    ])

    # Compare with current holdings
    current = _load_holdings()
    if current is not None:
        deltas = {
            "core_0050": target["core_0050"] - current.core_0050_pct,
            "leverage_00631L": target["leverage_00631L"] - current.leverage_00631L_pct,
            "satellite": target["satellite"] - current.satellite_pct,
            "cash": target["cash"] - current.cash_pct,
        }
        lines.append("### 當前 vs 目標 deltas")
        lines.append("")
        lines.append("| 類別 | 當前 % | 目標 % | Delta |")
        lines.append("|------|--------|--------|-------|")
        for k, label in [
            ("core_0050", "核心 0050"),
            ("leverage_00631L", "00631L"),
            ("satellite", "衛星"),
            ("cash", "現金"),
        ]:
            curr_pct = getattr(current, f"{k}_pct" if k != "core_0050" else "core_0050_pct")
            tgt = target[k]
            d = deltas[k]
            arrow = "⬆️ 加碼" if d > 5 else ("⬇️ 減碼" if d < -5 else "✅ 已達標")
            lines.append(f"| {label} | {curr_pct:.0f}% | {tgt}% | {d:+.0f}pp {arrow} |")
        lines.append("")

        # Concrete actions for biggest deltas
        big_deltas = sorted(
            [(k, deltas[k]) for k in deltas],
            key=lambda x: -abs(x[1]),
        )
        lines.append("### 建議動作（依 delta 排序）")
        lines.append("")
        for k, d in big_deltas[:3]:
            if abs(d) < 5:
                continue
            label_map = {
                "core_0050": "0050",
                "leverage_00631L": "00631L",
                "satellite": "Revenue YoY 衛星",
                "cash": "現金",
            }
            action = "增加" if d > 0 else "減少"
            lines.append(f"- **{action} {label_map[k]} {abs(d):.0f}pp**")
        lines.append("")

    lines.append("_配置基於 9 年 TAIEX 實證 (regime_strategy_mapping.csv)；非實時 quote，每週至少 review 一次_")
    lines.append("")
    return "\n".join(lines)
