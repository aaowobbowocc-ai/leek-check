"""ETF 除權息 pre-drift 提示 section (晨報用).

Strategy: D-10 close → D-1 close, 5 ETF basket validated.
Full 46-ETF stress test (2026-05-08): n=720 net +1.33% t=11.3 cluster-SE +3.60.
Survivor bias 僅 +0.02pp。Quarterly subset 最強 +1.80% t=7.8。

memory: project_dividend_etf_drift.md
"""
from __future__ import annotations
from pathlib import Path
from datetime import date, timedelta
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache" / "finmind" / "dividend"
TWSE_CACHE = ROOT / "data" / "cache" / "twse" / "exdiv_schedule.parquet"

# 17 檔 deploy core (validated alpha)
DEPLOY_CORE = {
    "0050", "0056", "00713", "00878", "00891", "00919", "00929",
    "00900", "00892", "00894", "00915", "00918", "006208",
    "00692", "00731", "00850", "00961",
}

# 排除清單(2024 vintage 月配,window 重疊)
EXCLUDED = {"00920", "00939", "00940", "00946"}

# ETF name lookup
NAME = {
    "0050": "元大台灣50", "0056": "元大高股息", "006208": "富邦台50",
    "00692": "富邦公司治理", "00713": "元大台灣高息低波",
    "00731": "復華富時高息低波", "00850": "元大臺灣ESG永續",
    "00878": "國泰永續高股息", "00891": "中信關鍵半導體",
    "00892": "富邦台灣半導體", "00894": "中信小資高價30",
    "00900": "富邦特選高股息30", "00915": "凱基優選高股息30",
    "00918": "大華優利高填息30", "00919": "群益台灣精選高息",
    "00929": "復華台灣科技優息", "00961": "兆豐洲際半導體",
    "00920": "富邦ESG台灣旗艦30", "00939": "統一台灣高息動能",
    "00940": "元大台灣價值高息", "00946": "群益科技高息成長",
}


def _trading_days_between(d1: date, d2: date) -> int:
    """Estimate trading days between d1 and d2 (skip weekends)."""
    if d1 > d2:
        return -_trading_days_between(d2, d1)
    days, cur = 0, d1
    while cur < d2:
        cur += timedelta(days=1)
        if cur.weekday() < 5:   # Mon-Fri
            days += 1
    return days


def _load_upcoming_events(today: date, horizon_days: int = 30) -> list[dict]:
    """Read TWSE primary + FinMind fallback,merge deduped events."""
    horizon = today + timedelta(days=horizon_days)
    seen: dict[tuple, dict] = {}   # (ticker, ex_date) -> event

    # Source 1: TWSE 除權息預告 (primary, 5/20 後仍可用)
    if TWSE_CACHE.exists():
        try:
            df = pd.read_parquet(TWSE_CACHE)
            for _, r in df.iterrows():
                ticker = str(r["stock_id"]).strip()
                if ticker not in (DEPLOY_CORE | EXCLUDED):
                    continue
                # 2026-05-22 fix: 用 substring match,防 TWSE API 改回「除息/除權息」全形
                if "息" not in str(r.get("type", "")):
                    continue
                try:
                    ex_d = pd.to_datetime(r["ex_date"]).date()
                except Exception:
                    continue
                if not (today <= ex_d <= horizon):
                    continue
                key = (ticker, ex_d)
                seen[key] = {
                    "ticker": ticker,
                    "name": NAME.get(ticker, str(r.get("name", ""))),
                    "ex_date": ex_d,
                    "cash_div": float(r.get("cash_div", 0) or 0),
                    "announce": "?",
                    "days_to_ex": _trading_days_between(today, ex_d),
                    "in_deploy": ticker in DEPLOY_CORE,
                    "source": "TWSE",
                }
        except Exception:
            pass

    # Source 2: FinMind announce cache (fallback,5/20 後失效但 historical depth)
    for ticker in (DEPLOY_CORE | EXCLUDED):
        f = CACHE / f"{ticker}_announce.parquet"
        if not f.exists():
            continue
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if df.empty:
            continue
        for _, r in df.iterrows():
            ex_str = r.get("CashExDividendTradingDate") or r.get("ex_date") or ""
            if not ex_str or pd.isna(ex_str):
                continue
            try:
                ex_d = pd.to_datetime(ex_str).date()
            except Exception:
                continue
            if not (today <= ex_d <= horizon):
                continue
            cash_div = float(r.get("CashEarningsDistribution", 0) or 0)
            stock_div = float(r.get("StockEarningsDistribution", 0) or 0)
            if stock_div > 0 and cash_div <= 0:
                continue
            key = (ticker, ex_d)
            if key in seen:
                # TWSE primary already has it; only update cash if FinMind has confirmed amount
                if cash_div > 0 and seen[key]["cash_div"] == 0:
                    seen[key]["cash_div"] = cash_div
                    seen[key]["announce"] = str(r.get("AnnouncementDate", "?"))
                continue
            seen[key] = {
                "ticker": str(ticker),
                "name": NAME.get(str(ticker), ""),
                "ex_date": ex_d,
                "cash_div": cash_div,
                "announce": str(r.get("AnnouncementDate", "?")),
                "days_to_ex": _trading_days_between(today, ex_d),
                "in_deploy": ticker in DEPLOY_CORE,
                "source": "FinMind",
            }

    events = list(seen.values())
    events.sort(key=lambda x: x["ex_date"])
    return events


def render(today: date | None = None) -> str:
    today = today or date.today()
    events = _load_upcoming_events(today)

    lines = []
    lines.append("## 💰 [Paper] ETF 除權息 pre-drift (D-10 → D-1 close)")
    lines.append("")
    lines.append("<details><summary>📖 策略原理(點開)</summary>")
    lines.append("")
    lines.append("**TW 散戶搶配息文化 + ETF 公告日預知 + 投信滿倉機械化** → 三 flows 在 D-10~D-1 窗口疊加產生 drift。")
    lines.append("")
    lines.append("**驗證:** Full 46-ETF universe stress test n=720 net **+1.33%/event** t=11.3,cluster-SE +3.60。Quarterly subset n=238 最強 **+1.80%** t=7.8。Survivor bias 僅 +0.02pp。")
    lines.append("")
    lines.append("**Devil's advocate 拒絕的 3 個假象:** 不是 dividend yield priced-in (corr -0.21)、不是 gap anomaly 雙重計算、不是 yfinance auto_adjust 污染。")
    lines.append("")
    lines.append("**規則:**")
    lines.append("- 進場: ex-div 前 10 個交易日收盤")
    lines.append("- 出場: ex-div 前 1 個交易日收盤(**不 hold 過 ex-div 日**)")
    lines.append("- 跳過 4 檔 2024 月配新 ETF (00920/00939/00940/00946,window 重疊)")
    lines.append("")
    lines.append("**期望:** 季配 ETF +1.80%/event,對 portfolio 年化 alpha 加成 ~1-3%。")
    lines.append("</details>")
    lines.append("")

    if not events:
        lines.append("_未來 30 天內無 deploy 白名單 ETF 除權息事件。_")
        lines.append("_(Cache 最新更新時間參考 `data/cache/finmind/dividend/`,排程 17:30 daily refresh)_")
        return "\n".join(lines)

    # 分類三個區塊:今天該進場 / 持倉中 / 預告
    today_action = [e for e in events if e["days_to_ex"] == 10 and e["in_deploy"]]
    today_exit = [e for e in events if e["days_to_ex"] == 1 and e["in_deploy"]]
    in_window = [e for e in events if 2 <= e["days_to_ex"] <= 9 and e["in_deploy"]]
    upcoming = [e for e in events if e["days_to_ex"] >= 11 and e["in_deploy"]]
    excluded_alerts = [e for e in events if not e["in_deploy"]]

    if today_action:
        lines.append("### 🟢 **今天 D-10 — 進場日!**")
        for e in today_action:
            cash_str = f"{e['cash_div']:.2f}" if e['cash_div'] > 0 else "待公告"
            lines.append(f"- **{e['ticker']} {e['name']}** ex-div {e['ex_date']} (cash {cash_str})")
            lines.append(f"  → 今天收盤前買進,**{e['ex_date']} 前 1 個交易日收盤前出場**")
        lines.append("")

    if today_exit:
        lines.append("### 🔴 **今天 D-1 — 出場日!**")
        for e in today_exit:
            lines.append(f"- **{e['ticker']} {e['name']}** ex-div 明天 {e['ex_date']}")
            lines.append(f"  → 今天收盤前賣出,**不要 hold 過 ex-div 日**")
        lines.append("")

    if in_window:
        lines.append("### ⏳ 進場窗口中(若已 D-10 進場則持有,若未進場則 SKIP)")
        for e in in_window:
            cash_str = f"{e['cash_div']:.2f}" if e['cash_div'] > 0 else "待公告"
            lines.append(f"- {e['ticker']} {e['name']}: ex-div {e['ex_date']} 前 {e['days_to_ex']} 個交易日 (cash {cash_str})")
        lines.append("")

    if upcoming:
        lines.append("### 📅 未來預告(尚未到 D-10)")
        for e in upcoming:
            lines.append(f"- {e['ticker']} {e['name']}: ex-div {e['ex_date']} (D-10 在 {e['days_to_ex']-10} 個交易日後)")
        lines.append("")

    if excluded_alerts:
        lines.append("### ⚠️ 排除清單(2024 月配,**不交易**)")
        for e in excluded_alerts:
            lines.append(f"- ~~{e['ticker']} {e['name']}~~ ex-div {e['ex_date']} — D-10 window 跟前月配息週期重疊,backtest 為負 alpha")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys, io
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    print(render())
