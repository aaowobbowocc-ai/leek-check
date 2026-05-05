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


# Target allocation per regime — V2 (2026-05-05 align with deployment_schedule.yaml v4)
# Each regime sums to 95%, 5% drift buffer
# Buckets:
#   core_tw     : 0050 + 00881 + 00947 (TW core ETF, deployment v4 33%)
#   leverage    : 00631L (2x TW leverage, regime-conditional)
#   us_00646    : S&P 500 TW listed (貨幣分散，3-AI 共識保留)
#   gold        : IAU + 00635U (corr 0.21 真分散)
#   japan_dxj   : DXJ (trigger-based)
#   satellite   : Revenue YoY portfolio (only SIDEWAYS regime activates)
#   cash        : 流動性 + opportunity buffer
#   legacy      : 個股 2345 + 2408 + 009819 (set stop loss -15%)
ALLOCATION_TABLE: dict[str, dict[str, int]] = {
    "CRASH":       {"core_tw": 33, "leverage": 15, "us_00646": 18, "gold": 10, "japan_dxj": 5, "satellite": 0,  "cash":  9, "legacy": 5},
    "BEAR":        {"core_tw": 33, "leverage":  0, "us_00646": 18, "gold": 10, "japan_dxj": 5, "satellite": 0,  "cash": 24, "legacy": 5},
    "SIDEWAYS":    {"core_tw": 30, "leverage":  0, "us_00646": 18, "gold": 10, "japan_dxj": 5, "satellite": 12, "cash": 15, "legacy": 5},
    "BULL_TREND":  {"core_tw": 33, "leverage":  5, "us_00646": 18, "gold": 10, "japan_dxj": 5, "satellite": 0,  "cash": 19, "legacy": 5},
    "STRONG_BULL": {"core_tw": 28, "leverage":  0, "us_00646": 15, "gold": 10, "japan_dxj": 5, "satellite": 0,  "cash": 32, "legacy": 5},
}

REGIME_NOTES = {
    "CRASH":       "🚨 鑽石買點 — 加 leverage 00631L 15% (fwd 20d +22.71%, 100% win)；deploy 現金降至 9%；分批 5 日進場",
    "BEAR":        "🟠 防禦 — 不買 leverage (decay)；現金 24% 維持等 CRASH",
    "SIDEWAYS":    "🟡 廣度因子適用 — 加 12% Revenue YoY 衛星 (max=20 yoy_asc, L4 流動性, +25.7%/yr 預期)",
    "BULL_TREND":  "🟢 標準持有 — 5% leverage 吃趨勢；現金 19% 流動性",
    "STRONG_BULL": "🔴 mean reversion 區 — 取消 leverage; 現金升至 32% 等 CRASH; core 縮 5pp 取利",
}

TICKER_CATEGORY = {
    # Core TW ETF (deployment v4: tw_core)
    "0050": "core_tw", "00881": "core_tw", "00947": "core_tw",
    # Leverage
    "00631L": "leverage",
    # US currency diversification
    "00646": "us_00646",
    # Gold
    "IAU": "gold", "00635U": "gold", "GLD": "gold",
    # Japan
    "DXJ": "japan_dxj", "EWJ": "japan_dxj",
    # Legacy individual stocks (set stop loss -15%)
    "2345": "legacy", "2408": "legacy", "009819": "legacy",
}


@dataclass
class CurrentHoldings:
    """User 當前持倉摘要（基於 assets.json）— V2 8-bucket structure.
    金額已乘 USER_UUID 遮罩才印出。原值保留供計算 delta。"""
    core_tw_pct: float          # 0050 + 00881 + 00947
    leverage_pct: float         # 00631L
    us_00646_pct: float         # S&P 500 TW listed
    gold_pct: float             # IAU + 00635U + GLD
    japan_dxj_pct: float        # DXJ
    satellite_pct: float        # Revenue YoY signals (deployed)
    legacy_pct: float           # 個股: 2345 + 2408 + 009819
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
    """從 assets.json 計算當前配置百分比 (V2 — 8-bucket structure).

    分類規則 (TICKER_CATEGORY map):
      0050/00881/00947 → core_tw
      00631L → leverage
      00646 → us_00646
      IAU/00635U/GLD → gold
      DXJ/EWJ → japan_dxj
      2345/2408/009819 → legacy
      其他未列 → satellite (Revenue YoY paper trade etc.)
      cash 欄位 → cash
    """
    if not ASSETS_JSON.exists():
        return None
    try:
        data = json.loads(ASSETS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None

    cash = float(data.get("cash", 0) or 0)
    holdings_groups = data.get("holdings", {})

    buckets = {
        "core_tw": 0.0, "leverage": 0.0, "us_00646": 0.0,
        "gold": 0.0, "japan_dxj": 0.0, "satellite": 0.0, "legacy": 0.0,
    }

    for _bucket, items in holdings_groups.items():
        if not isinstance(items, list):
            continue
        for h in items:
            ticker = str(h.get("ticker", "")).strip()
            shares = float(h.get("shares", 0) or 0)
            price = _latest_close(ticker)
            if price <= 0:
                price = float(h.get("cost_incl_fee", 0) or h.get("cost", 0) or 0)
            value = shares * price
            cat = TICKER_CATEGORY.get(ticker, "satellite")
            buckets[cat] += value

    total = sum(buckets.values()) + cash
    if total <= 0:
        return None
    return CurrentHoldings(
        core_tw_pct=buckets["core_tw"] / total * 100,
        leverage_pct=buckets["leverage"] / total * 100,
        us_00646_pct=buckets["us_00646"] / total * 100,
        gold_pct=buckets["gold"] / total * 100,
        japan_dxj_pct=buckets["japan_dxj"] / total * 100,
        satellite_pct=buckets["satellite"] / total * 100,
        legacy_pct=buckets["legacy"] / total * 100,
        cash_pct=cash / total * 100,
        total_value=total,
    )


BUCKET_LABELS = [
    ("core_tw", "核心 TW (0050+00881+00947)"),
    ("us_00646", "美股 00646 (S&P 500)"),
    ("gold", "黃金 (IAU+00635U)"),
    ("japan_dxj", "日股 DXJ"),
    ("leverage", "00631L 槓桿"),
    ("satellite", "Revenue YoY 衛星"),
    ("legacy", "個股 (2345/2408/009819)"),
    ("cash", "現金"),
]

BUCKET_NOTES = {
    "core_tw": "吃 TSMC AI 集中度",
    "us_00646": "貨幣分散 (與 0050 corr 0.62 但非結構同步)",
    "gold": "真分散 (corr 0.21 - 近 60d 0.09)",
    "japan_dxj": "DXJ trigger-based: SPY -10%/90d 或 JPY +5%/30d",
    "leverage": "2x TW leverage (regime-conditional)",
    "satellite": "Revenue YoY portfolio (僅 SIDEWAYS regime 啟用)",
    "legacy": "現有個股 + stop loss -15%",
    "cash": "流動性 + CRASH 子彈",
}


def _apply_hedge_tilt(target: dict[str, int]) -> tuple[dict[str, int], int, list[str]]:
    """Apply hedge cash tilt: shift cash_tilt_pp from leverage/satellite/japan first."""
    try:
        from .hedge_signals import compute_hedge_reading
    except Exception:
        return target, 0, []
    h = compute_hedge_reading()
    tilt = h.cash_tilt_pp
    if tilt == 0:
        return target, 0, h.notes
    adjusted = dict(target)
    # Priority: take from leverage → satellite → japan_dxj → us_00646
    for src in ["leverage", "satellite", "japan_dxj", "us_00646"]:
        if tilt <= 0:
            break
        take = min(adjusted.get(src, 0), tilt)
        adjusted[src] = adjusted.get(src, 0) - take
        tilt -= take
    adjusted["cash"] = adjusted.get("cash", 0) + (h.cash_tilt_pp - tilt)
    return adjusted, h.cash_tilt_pp, h.notes


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
        lines.append(f"⚠️ **Hedge overlay 啟動**：cash +{tilt}pp")
        for note in hedge_notes:
            lines.append(f"  - {note}")
        lines.append("")
    lines.extend([
        "### 目標配置",
        "",
        "| 類別 | 目標 % | 說明 |",
        "|------|--------|------|",
    ])
    for key, label in BUCKET_LABELS:
        pct = target.get(key, 0)
        note = BUCKET_NOTES.get(key, "")
        if pct == 0 and key not in ("leverage", "satellite", "japan_dxj"):
            continue  # hide irrelevant zero rows; keep optional buckets visible
        marker = "**" if pct >= 15 else ""
        lines.append(f"| {label} | {marker}{pct}%{marker} | {note} |")
    lines.append("")
    lines.append(f"**規則重點**：{REGIME_NOTES.get(reading.regime, '')}")
    lines.append("")

    # Compare with current holdings
    current = _load_holdings()
    if current is not None:
        lines.append("### 當前 vs 目標 deltas")
        lines.append("")
        lines.append("| 類別 | 當前 % | 目標 % | Delta |")
        lines.append("|------|--------|--------|-------|")

        delta_records = []
        for key, label in BUCKET_LABELS:
            curr_pct = getattr(current, f"{key}_pct", 0)
            tgt = target.get(key, 0)
            d = tgt - curr_pct
            delta_records.append((key, label, curr_pct, tgt, d))

        for key, label, curr_pct, tgt, d in delta_records:
            arrow = "⬆️ 加碼" if d > 5 else ("⬇️ 減碼" if d < -5 else "✅ 達標")
            lines.append(f"| {label} | {curr_pct:.0f}% | {tgt}% | {d:+.0f}pp {arrow} |")
        lines.append("")

        # Top 3 actions by abs delta
        big_deltas = sorted(
            [r for r in delta_records if abs(r[4]) >= 5],
            key=lambda r: -abs(r[4]),
        )[:3]
        if big_deltas:
            lines.append("### 建議動作（依 delta 排序）")
            lines.append("")
            for key, label, curr_pct, tgt, d in big_deltas:
                action = "增加" if d > 0 else "減少"
                lines.append(f"- **{action} {label} {abs(d):.0f}pp** (current {curr_pct:.0f}% → target {tgt}%)")
            lines.append("")
    lines.append("_配置基於 9 年 TAIEX 實證 (regime_strategy_mapping.csv) + deployment v4 (2026-05-05 EWY 撤回後)；每週至少 review 一次_")
    lines.append("")
    return "\n".join(lines)
