"""
晨報部署排程 section — 顯示未來 7 日內 pending action 與集中度警報。

讀 config/deployment_schedule.yaml + data/assets.json，輸出：
  1. 未來 7 日 pending DCA actions
  2. 持股集中度警報（單檔 > 30%）
  3. 現金 buffer 偏離（目標 26%）
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml


def _load_schedule(root: Path):
    p = root / "config" / "deployment_schedule.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _load_assets(root: Path):
    p = root / "data" / "assets.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _get_price(ticker: str) -> float:
    """快取 + yfinance 取現價（自動處理 .TW / .TWO / 海外）"""
    try:
        import yfinance as yf
        # 海外 ticker（例 EWY, DXJ, IAU, GLD）直接打
        if not ticker.replace(".", "").isdigit():
            yf_tk = ticker
            hist = yf.Ticker(yf_tk).history(period="5d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
            return 0.0
        # TW: 試 .TW / .TWO
        for sfx in [".TW", ".TWO"]:
            try:
                hist = yf.Ticker(ticker + sfx).history(period="5d")
                if not hist.empty:
                    return float(hist["Close"].iloc[-1])
            except Exception:
                continue
    except Exception:
        pass
    return 0.0


def _resolve_price_range(
    item: dict,
    spot_price: float,
) -> tuple[float, float, str]:
    """根據 price_strategy 算出限價區間。

    回傳 (low, high, label)。label 用於顯示「±2% 動態 / 固定 / fallback」。
    """
    strategy = item.get("price_strategy", "fixed")

    if strategy == "spot_pm2pct" and spot_price > 0:
        return (round(spot_price * 0.98, 2), round(spot_price * 1.02, 2),
                f"當下 {spot_price:.2f} ±2%")
    if strategy == "spot_pm5pct" and spot_price > 0:
        return (round(spot_price * 0.95, 2), round(spot_price * 1.05, 2),
                f"當下 {spot_price:.2f} ±5%")

    # fallback: fallback_price_range or fixed price
    if "fallback_price_range" in item:
        pr = item["fallback_price_range"]
        return (pr[0], pr[1], "fallback 區間")
    if "price" in item:
        pr = item["price"]
        if isinstance(pr, list):
            return (pr[0], pr[1], "固定")
        return (pr, pr, "固定")
    if "price_range" in item:
        pr = item["price_range"]
        return (pr[0], pr[1], "固定區間")
    return (0.0, 0.0, "無價格資訊")


def render_deployment_section(root: Path, today: date | None = None) -> str:
    today = today or date.today()
    schedule = _load_schedule(root)
    assets = _load_assets(root)

    lines = ["## 💰 部署排程 + 集中度監控"]

    # ── 1. 未來 7 日 pending actions ─────────────
    actions = schedule.get("actions", [])
    upcoming = []
    for a in actions:
        if a.get("status") != "pending": continue
        try:
            ad = datetime.fromisoformat(a["date"]).date()
        except Exception:
            continue
        days = (ad - today).days
        if -1 <= days <= 7:
            upcoming.append((days, ad, a))

    upcoming.sort(key=lambda x: x[0])
    if upcoming:
        lines.append("\n### 📅 未來 7 日內 DCA 動作（**動態限價，當下價 ±2%**）")
        for days, ad, a in upcoming:
            tag = "🔴 今日" if days == 0 else (f"🟡 {days} 日後" if days > 0 else "⏰ 過期")
            note = a.get("note", "")
            skip_after = a.get("skip_after_days", 0)
            if "composite" in a:
                lines.append(f"- {tag} **{ad}** — {note}（總額 NT${a.get('budget_twd', 0):,}）")
                for c in a["composite"]:
                    spot = _get_price(c["ticker"])
                    low, high, label = _resolve_price_range(c, spot)
                    lines.append(f"    - {c['ticker']} × {c['shares']} 股 "
                                 f"@ **{low}~{high}** ({label})  "
                                 f"NT${c.get('budget_twd', 0):,}")
            else:
                tk = a.get("ticker", "")
                shares = a.get("shares", 0)
                spot = _get_price(tk)
                low, high, label = _resolve_price_range(a, spot)
                lines.append(f"- {tag} **{ad}** — {tk} × {shares} 股 "
                             f"@ **{low}~{high}** ({label})（{note}）")
            if skip_after:
                lines.append(f"    _掛 {skip_after} 日不到自動跳批_")
    else:
        lines.append("\n### 📅 未來 7 日內 DCA 動作")
        lines.append("- 無 pending action（下一批請查 [config/deployment_schedule.yaml](config/deployment_schedule.yaml)）")

    # ── 2. 持股集中度警報 ─────────────
    cash = assets.get("cash", 0)
    holdings = assets.get("holdings", {}).get("long_term", [])
    if not holdings:
        lines.append("\n### ⚠️ 集中度監控\n- 無持股資料")
        return "\n".join(lines) + "\n"

    rows = []
    total_mv = 0
    for h in holdings:
        tk = h.get("ticker", "")
        sh = h.get("shares", 0)
        cost = h.get("cost", 0)
        price = _get_price(tk)
        if price <= 0: price = cost
        mv = sh * price
        rows.append({"tk": tk, "shares": sh, "cost": cost, "price": price, "mv": mv,
                     "pl_pct": (price/cost - 1) * 100 if cost > 0 else 0})
        total_mv += mv
    total = cash + total_mv

    lines.append(f"\n### ⚠️ 持股集中度（總資產 NT${total:,.0f}）")
    lines.append(f"- 現金: NT${cash:,} ({cash/total*100:.1f}%)")
    lines.append(f"- 持股: NT${total_mv:,.0f} ({total_mv/total*100:.1f}%)")

    # 集中度警報
    alerts = []
    for r in sorted(rows, key=lambda x: -x["mv"]):
        pct_of_holdings = r["mv"] / total_mv * 100 if total_mv > 0 else 0
        pct_of_total = r["mv"] / total * 100 if total > 0 else 0
        flag = ""
        if pct_of_holdings > 30:
            flag = f" 🚨 占持股 {pct_of_holdings:.0f}% (>30%)"
            alerts.append(f"{r['tk']} 占持股 {pct_of_holdings:.0f}% — 集中度風險")
        elif pct_of_holdings > 20:
            flag = f" ⚠️ 占持股 {pct_of_holdings:.0f}%"
        lines.append(f"  - {r['tk']}: {r['shares']} 股 × {r['price']:.2f} = "
                     f"NT${r['mv']:,.0f} ({pct_of_total:.1f}%) "
                     f"P/L {r['pl_pct']:+.1f}%{flag}")

    if alerts:
        lines.append(f"\n  🚨 **警報**:")
        for a in alerts:
            lines.append(f"  - {a}")

    # 現金 buffer 偏離
    cash_pct = cash / total * 100 if total > 0 else 0
    target_cash_pct = (schedule.get("target_state", {}).get("cash_buffer", {}).get("pct", 26))
    deviation = cash_pct - target_cash_pct
    if abs(deviation) > 10:
        if deviation > 0:
            lines.append(f"\n  💰 現金 {cash_pct:.0f}% vs 目標 {target_cash_pct}% "
                         f"(+{deviation:.0f}pp 過多) → 加速 DCA")
        else:
            lines.append(f"\n  💰 現金 {cash_pct:.0f}% vs 目標 {target_cash_pct}% "
                         f"({deviation:.0f}pp 過少) → 暫停 DCA")

    return "\n".join(lines) + "\n"
