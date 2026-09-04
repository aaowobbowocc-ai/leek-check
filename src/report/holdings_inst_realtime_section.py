"""
持股法人即時 section（從 TWSE T86 cache 讀取，比 FinMind 早 12-14h）

每日晨報顯示用戶持股的最近 5 日法人 net buy
資料來源：data/cache/twse_t86/{date}.parquet
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import sys


def render_holdings_inst_section(project_root: Path) -> str:
    """渲染持股法人即時 section"""
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    try:
        from src.data.twse_t86_query import (
            get_inst_recent, get_latest_available_date, get_market_total
        )
    except Exception as e:
        return f"## 📡 持股法人即時\n\n載入 helper 失敗: {e}\n"

    # 載入持股
    assets_path = project_root / "data" / "assets.json"
    if not assets_path.exists():
        return ""
    data = json.loads(assets_path.read_text(encoding="utf-8"))
    holdings = data.get("holdings", {}).get("long_term", [])
    tickers = [str(h.get("ticker")) for h in holdings if h.get("ticker")]

    latest_d = get_latest_available_date()
    if latest_d is None:
        return ("## 📡 持股法人即時（TWSE T86）\n\n"
                "_TWSE T86 cache 為空，跑 `python scripts/twse_t86_realtime.py --backfill 5` 補抓_\n")

    lines = ["## 📡 持股法人即時（TWSE T86 直抓，比 FinMind 早 12-14h）\n"]
    lines.append(f"**最新可用日期：{latest_d}**\n")
    lines.append("<details><summary>📖 為什麼有這個 section？（點開）</summary>\n")
    lines.append("**FinMind 法人資料 T+1 早上才更新**（5/4 收盤資料 → 5/5 早上 7-8 點）")
    lines.append("**TWSE T86 直抓收盤後 1-2h 即有**（5/4 收盤 13:30 → 18:00 後可抓）")
    lines.append("")
    lines.append("→ 提早 12-14h 看到法人動向，給次日 8:30 晨報補強")
    lines.append("→ 設置 18:30 daily 排程：`scripts/run_twse_t86_daily.bat`")
    lines.append("→ FinMind 訂閱結束後仍可用（永久免費）")
    lines.append("</details>\n")

    lines.append("### 持股最近 5 日法人 net buy（張）\n")
    lines.append("| 代號 | 名稱 | 最新日 | 外資 | 投信 | 自營 | 三大法人 |")
    lines.append("|---|---|---|---|---|---|---|")

    for tk in tickers:
        df = get_inst_recent(tk, days_back=5)
        if df.empty:
            lines.append(f"| {tk} | — | 無資料 | — | — | — | — |")
            continue
        last = df.iloc[-1]
        name = str(last.get("stock_name", "")).strip()[:8]
        date_s = str(last["date"])[-5:]  # MM-DD
        f_lots = last.get("foreign_net_lots", 0)
        t_lots = last.get("trust_net_lots", 0)
        d_lots = last.get("dealer_net_lots", 0)
        total_lots = (last.get("total_3_inst_net", 0) or 0) / 1000

        # 色彩
        def fmt(v):
            sign = "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")
            return f"{sign}{v:+,.0f}"

        lines.append(f"| {tk} | {name} | {date_s} | {fmt(f_lots)} | "
                     f"{fmt(t_lots)} | {fmt(d_lots)} | {fmt(total_lots)} |")

    # 5 日趨勢（外資累計）
    lines.append(f"\n### 持股最近 5 日外資累計 net（張）\n")
    lines.append("| 代號 | 5 日累計外資 | 趨勢 |")
    lines.append("|---|---|---|")
    for tk in tickers:
        df = get_inst_recent(tk, days_back=5)
        if df.empty: continue
        cumsum = df["foreign_net_lots"].sum() if "foreign_net_lots" in df.columns else 0
        trend = "🟢 持續買" if cumsum > 1000 else ("🔴 持續賣" if cumsum < -1000 else "⚪ 中性")
        if abs(cumsum) > 10000:
            trend += " ⭐"
        lines.append(f"| {tk} | {cumsum:+,.0f} | {trend} |")

    # 全市場狀態
    market = get_market_total(latest_d)
    if market:
        lines.append(f"\n### 全市場 {latest_d} 法人 net 總和（億 NT$）\n")
        lines.append(f"- 外資: {market['foreign_net_total']/1e8:+.2f} 億")
        lines.append(f"- 投信: {market['trust_net_total']/1e8:+.2f} 億")
        lines.append(f"- 自營: {market['dealer_net_total']/1e8:+.2f} 億")
        lines.append(f"- **三大法人**: {market['total_3_inst']/1e8:+.2f} 億")

    return "\n".join(lines) + "\n"
