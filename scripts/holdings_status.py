"""
INVEST 儀表板 — 完整版（持股 + ORB 訊號狀態 + DCA 進度 + 動作提醒）。

設計目標：用戶 4 秒掃完知道「現在該做什麼」。
"""
from __future__ import annotations

import io
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.text import Text  # noqa: E402
from rich.progress import Progress, BarColumn, TextColumn  # noqa: E402
from rich import box  # noqa: E402

ASSETS_JSON = ROOT / "data" / "assets.json"
LEDGER_PATH = ROOT / "data" / "paper_trades" / "orb_ledger.csv"
console = Console(width=100)


# ── DCA 計畫（從 project_allocation_plan_2026_04 來）──
DCA_PLAN = {
    "0050":  {"target": 1000, "batches": [{"shares": 300, "price": "88.5-90.5"},
                                          {"shares": 300, "price": "88.5-90.5"},
                                          {"shares": 400, "price": "88.5-90.5"}]},
    "00881": {"target": 1100, "batches": [{"shares": 350, "price": "45.0-46.5"},
                                          {"shares": 350, "price": "45.0-46.5"},
                                          {"shares": 400, "price": "45.0-46.5"}]},
    "00947": {"target": 1000, "batches": [{"shares": 300, "price": "29.5-30.5"},
                                          {"shares": 300, "price": "29.5-30.5"},
                                          {"shares": 400, "price": "29.5-30.5"}]},
    "00646": {"target": 1700, "batches": [{"shares": 500, "price": "69.5-71.0"},
                                          {"shares": 500, "price": "69.5-71.0"},
                                          {"shares": 700, "price": "69.5-71.0"}]},
    "EWY":   {"target": 12,   "batches": [{"shares": 2, "price": "152-156 USD"} for _ in range(6)]},
}

# ── ORB 規則（從 orb_paper_trade.py 同步）──
ORB_RULES = {
    "2408": {"entry_time": "09:15", "vol_threshold": "30%", "ref": "open5"},
    "2485": {"entry_time": "09:45", "vol_threshold": "30%", "ref": "open15"},
}

# ── 待辦清單規則 ──
PENDING_ACTIONS_FIXED = [
    {"priority": "🔴", "action": "賣 6770 剩 2,000 股", "limit": "55.4 ~ 55.5",
     "reason": "完成 6770 出清，釋出 ~110k 現金", "depends": "ticker_6770_remaining"},
]


def _yf_symbol(ticker: str) -> str:
    if ticker.startswith("^") or "." in ticker:
        return ticker
    if ticker.isdigit() and 4 <= len(ticker) <= 6:
        return f"{ticker}.TW"
    return ticker


def fetch_current_price(ticker: str) -> float:
    try:
        import yfinance as yf
        sym = _yf_symbol(ticker)
        t = yf.Ticker(sym)
        try:
            p = t.fast_info.get("last_price") or t.fast_info.get("regular_market_price")
            if p:
                return float(p)
        except Exception:
            pass
        hist = t.history(period="2d", auto_adjust=False)
        if not hist.empty:
            return float(hist.iloc[-1]["Close"])
    except Exception:
        pass
    return 0.0


# 新 ETF 不在標準 DB 的補充對照
EXTRA_NAMES = {
    "009819": "中信數據基建",
    "00919":  "群益高息",
    "00940":  "元大價值高息",
    "00929":  "復華科技優息",
    "00878":  "國泰永續高股息",
    "00881":  "國泰台灣5G+",
    "00946":  "群益台ESG",
    "00947":  "中信半導體",
    "00646":  "元大S&P500",
    "00937B": "群益ESG投等債",
    "EWY":    "iShares 韓國",
}


def lookup_name(ticker: str) -> str:
    if ticker in EXTRA_NAMES:
        return EXTRA_NAMES[ticker]
    try:
        from src.strategy.volume_anomaly_scanner import lookup_ticker_name
        return lookup_ticker_name(str(ticker)) or ""
    except Exception:
        return ""


# ── Header（市場狀態）──
def market_state(now: datetime) -> tuple[str, str]:
    """回傳 (狀態文字, 顏色)"""
    if now.weekday() >= 5:
        return "🟫 假日（市場休市）", "yellow"
    t = now.time()
    if t < dt_time(9, 0):
        delta = (datetime.combine(now.date(), dt_time(9, 0)) - now).total_seconds() / 60
        return f"🌙 盤前  距離 09:00 開盤 {delta:.0f} 分", "cyan"
    if t < dt_time(13, 30):
        return "🟢 盤中", "green"
    if t < dt_time(14, 30):
        return "🟡 盤後初期", "yellow"
    return "🔵 盤後", "blue"


# ── 持股 panel ──
def holdings_panel(data: dict) -> tuple[Panel, dict]:
    cash = float(data.get("cash", 0))
    holdings = data.get("holdings", {})
    long_term = holdings.get("long_term", []) or []
    short_term = holdings.get("short_term", []) or []
    all_holdings = [("長期", h) for h in long_term] + [("短期", h) for h in short_term]

    if not all_holdings:
        return Panel("[dim](無持股)[/dim]", title="💼 持股", box=box.ROUNDED), \
               {"cash": cash, "total_mv": 0, "ticker_shares": {}}

    table = Table(box=box.SIMPLE_HEAD, show_lines=False, header_style="bold cyan",
                  padding=(0, 1), expand=True)
    table.add_column("代號", style="bold yellow", width=6)
    table.add_column("名稱", width=12, no_wrap=False)
    table.add_column("股數", justify="right", width=7)
    table.add_column("成本", justify="right", style="dim", width=8)
    table.add_column("現價", justify="right", style="bold", width=8)
    table.add_column("市值", justify="right", style="cyan", width=10)
    table.add_column("損益", justify="right", width=11)
    table.add_column("%", justify="right", width=8)

    total_cost = 0.0
    total_mv = 0.0
    ticker_shares = {}
    for tag, h in all_holdings:
        tk = str(h.get("ticker", ""))
        shares = int(h.get("shares", 0))
        cost = float(h.get("cost", 0))
        name = lookup_name(tk)
        price = fetch_current_price(tk)
        mv = shares * price
        cost_total = shares * cost
        pnl = mv - cost_total
        pct = (price / cost - 1) * 100 if cost > 0 else 0.0
        ticker_shares[tk] = shares
        total_cost += cost_total
        total_mv += mv

        pnl_color = "green" if pnl > 0 else ("red" if pnl < 0 else "white")
        pct_color = "green" if pct > 0 else ("red" if pct < 0 else "white")
        sign = "+" if pnl >= 0 else ""

        table.add_row(
            tk, name[:6],
            f"{shares:,}",
            f"{cost:.2f}",
            f"{price:.2f}",
            f"{mv:,.0f}",
            f"[bold {pnl_color}]{sign}{pnl:,.0f}[/bold {pnl_color}]",
            f"[bold {pct_color}]{sign}{pct:.2f}%[/bold {pct_color}]",
        )

    total_pnl = total_mv - total_cost
    total_pct = (total_mv / total_cost - 1) * 100 if total_cost > 0 else 0
    net_worth = cash + total_mv

    summary = Text()
    summary.append("\n持股總計: ", style="dim")
    summary.append(f"市值 ", style="dim")
    summary.append(f"{total_mv:,.0f}", style="bold cyan")
    summary.append(" │ 損益 ")
    pnl_style = "bold green" if total_pnl > 0 else ("bold red" if total_pnl < 0 else "white")
    summary.append(f"{'+' if total_pnl >= 0 else ''}{total_pnl:,.0f}", style=pnl_style)
    summary.append(f" ({'+' if total_pct >= 0 else ''}{total_pct:.2f}%)", style=pnl_style)
    summary.append("\n💰 現金 ", style="dim")
    summary.append(f"{cash:,.0f}", style="bold green")
    summary.append("  │  📦 淨資產 ", style="dim")
    summary.append(f"{net_worth:,.0f}", style="bold cyan")

    from rich.console import Group
    return Panel(Group(table, summary), title="💼 持股", box=box.ROUNDED, style="white"), \
           {"cash": cash, "total_mv": total_mv, "net_worth": net_worth,
            "ticker_shares": ticker_shares}


# ── ORB 訊號狀態 panel ──
def orb_signal_panel(now: datetime) -> Panel:
    # 讀今日 ledger
    today = now.date()
    ledger_today = pd.DataFrame()
    if LEDGER_PATH.exists():
        try:
            df = pd.read_csv(LEDGER_PATH, dtype={"ticker": str})
            ledger_today = df[df["trade_date"] == today.isoformat()]
        except Exception:
            pass

    is_trading_day = now.weekday() < 5
    table = Table(box=box.SIMPLE_HEAD, show_lines=False, header_style="bold cyan",
                  padding=(0, 1), expand=True)
    table.add_column("代號", style="bold yellow", width=6)
    table.add_column("名稱", width=8)
    table.add_column("規則", style="dim", width=20)
    table.add_column("狀態", width=24)
    table.add_column("詳情", width=28)

    for tk, rule in ORB_RULES.items():
        name = lookup_name(tk)[:6]
        rule_str = f"{rule['entry_time']} v≥{rule['vol_threshold']} {rule['ref']}"

        # 解析狀態
        if not is_trading_day:
            status = "[dim]🟫 假日[/dim]"
            detail = "[dim]週末或假期[/dim]"
        else:
            entry_h, entry_m = map(int, rule["entry_time"].split(":"))
            entry_dt = datetime.combine(today, dt_time(entry_h, entry_m))
            check_dt = entry_dt + pd.Timedelta(minutes=5)  # 偵測時點

            tk_today = ledger_today[ledger_today["ticker"] == tk] if not ledger_today.empty else pd.DataFrame()
            opens = tk_today[tk_today["status"] == "open"] if not tk_today.empty else pd.DataFrame()
            closed = tk_today[tk_today["status"] == "closed"] if not tk_today.empty else pd.DataFrame()

            if not closed.empty:
                r = closed.iloc[-1]
                net = float(r.get("net_return_pct", 0))
                color = "green" if net > 0 else "red"
                status = f"[bold {color}]✅ 已平倉[/bold {color}]"
                detail = (f"entry [yellow]{float(r['entry_price']):.2f}[/yellow] → "
                          f"exit [yellow]{float(r['exit_price']):.2f}[/yellow] "
                          f"[bold {color}]{'+' if net >= 0 else ''}{net:.2f}%[/bold {color}]")
            elif not opens.empty:
                r = opens.iloc[-1]
                status = "[bold yellow]🟢 已觸發 (待 13:20 平倉)[/bold yellow]"
                detail = (f"entry [yellow]{float(r['entry_price']):.2f}[/yellow] @ "
                          f"{r.get('entry_time', '')}")
            elif now < check_dt:
                wait = (check_dt - now).total_seconds() / 60
                status = f"[cyan]⏳ 等待偵測[/cyan]"
                detail = f"距 {rule['entry_time']} 還有 {wait:.0f} 分"
            elif now < datetime.combine(today, dt_time(13, 25)):
                status = "[dim]⚪ 今日未觸發[/dim]"
                detail = f"條件未達（pump / vol / 突破不成立）"
            else:
                status = "[dim]⚫ 收盤無訊號[/dim]"
                detail = "今日無 ORB 訊號"

        table.add_row(tk, name, rule_str, status, detail)

    return Panel(table, title="🎯 今日 ORB 訊號狀態 (paper trade)",
                 box=box.ROUNDED, style="cyan")


# ── DCA 進度 panel ──
def dca_progress_panel(ticker_shares: dict) -> Panel:
    table = Table(box=box.SIMPLE_HEAD, show_lines=False, header_style="bold cyan",
                  padding=(0, 1), expand=True)
    table.add_column("ETF", style="bold yellow", width=6)
    table.add_column("進度", width=12)
    table.add_column("已買 / 目標", justify="right", width=14)
    table.add_column("%", justify="right", width=6)
    table.add_column("下一批", width=44)

    for tk, plan in DCA_PLAN.items():
        owned = ticker_shares.get(tk, 0)
        target = plan["target"]
        pct = owned / target * 100 if target else 0

        # 進度條（10 格）
        bar_filled = min(int(pct / 10), 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        if pct >= 100:
            bar_color = "green"
            next_action = "[bold green]✅ 完成[/bold green]"
        elif pct >= 60:
            bar_color = "cyan"
            # 找下一批
            cumulative = 0
            for i, b in enumerate(plan["batches"]):
                cumulative += b["shares"]
                if owned < cumulative:
                    next_action = f"第{i+1}批 [yellow]{b['shares']}[/yellow] 股 @ {b['price']}"
                    break
            else:
                next_action = "完成"
        elif pct > 0:
            bar_color = "yellow"
            cumulative = 0
            for i, b in enumerate(plan["batches"]):
                cumulative += b["shares"]
                if owned < cumulative:
                    next_action = f"第{i+1}批 [yellow]{b['shares']}[/yellow] 股 @ {b['price']}"
                    break
            else:
                next_action = "完成"
        else:
            bar_color = "white"
            b = plan["batches"][0]
            next_action = f"[bold]立刻第1批[/bold] [yellow]{b['shares']}[/yellow] 股 @ {b['price']}"

        table.add_row(
            tk,
            f"[{bar_color}]{bar}[/{bar_color}]",
            f"{owned:,} / {target:,}",
            f"{pct:.0f}%",
            next_action,
        )

    return Panel(table, title="📈 DCA 進度 (依 9 週分批計畫)", box=box.ROUNDED, style="green")


# ── 下一個動作清單 panel ──
def next_actions_panel(ticker_shares: dict, cash: float, now: datetime) -> Panel:
    actions = []

    # 1. 6770 剩餘 → 賣
    if ticker_shares.get("6770", 0) > 0:
        n = ticker_shares["6770"]
        actions.append({
            "priority": "🔴",
            "action": f"賣 6770 剩 {n:,} 股",
            "limit": "55.0 ~ 55.5",
            "why": "完成 6770 出清計畫",
        })

    # 2-5. ETF DCA 第 1 批（如果還沒買）
    for tk, plan in DCA_PLAN.items():
        if tk == "EWY":
            continue
        owned = ticker_shares.get(tk, 0)
        if owned == 0:
            b = plan["batches"][0]
            actions.append({
                "priority": "🟡",
                "action": f"買 {tk} {b['shares']} 股",
                "limit": b["price"],
                "why": "DCA 第1批 (TAIEX 過熱分批進場)",
            })

    # EWY 5/5 提醒
    today = now.date()
    if today < date(2026, 5, 5):
        days = (date(2026, 5, 5) - today).days
        actions.append({
            "priority": "🟢",
            "action": f"5/5 EWY 第1批 2 股",
            "limit": "152-156 USD",
            "why": f"等候中（{days} 天後）— 永豐 e-Leader 複委託",
        })

    # 智邦 OCO 提醒
    s2345 = ticker_shares.get("2345", 0)
    if s2345 > 0:
        actions.append({
            "priority": "🟢",
            "action": "2345 智邦 OCO 監控",
            "limit": "停利 2,460 / 停損 1,925",
            "why": "現在 ~2,175，向上向下都有空間",
        })

    if not actions:
        return Panel("[green]✅ 暫無待辦動作 — 目前部位達標[/green]",
                     title="🔔 下一個動作", box=box.ROUNDED, style="green")

    table = Table(box=box.SIMPLE_HEAD, show_lines=False, header_style="bold cyan",
                  padding=(0, 1), expand=True)
    table.add_column("優先", width=6, justify="center")
    table.add_column("#", style="dim", width=2)
    table.add_column("動作", width=22, no_wrap=False)
    table.add_column("限價", style="yellow", width=14)
    table.add_column("為什麼", style="dim", width=40, no_wrap=False)

    for i, a in enumerate(actions, 1):
        # 用文字標籤替代 emoji 避免顯示問題
        prio_map = {"🔴": "[bold red]必做[/bold red]",
                    "🟡": "[yellow]建議[/yellow]",
                    "🟢": "[dim green]觀察[/dim green]"}
        table.add_row(prio_map.get(a["priority"], a["priority"]), str(i),
                      a["action"], a["limit"], a["why"])

    return Panel(table, title=f"🔔 下一個動作 ({len(actions)} 項)",
                 box=box.ROUNDED, style="yellow")


# ── ORB Ledger summary panel ──
def orb_ledger_panel() -> Panel:
    if not LEDGER_PATH.exists():
        return Panel("[dim]尚無紀錄 — 第一筆 ORB 訊號還沒觸發[/dim]",
                     title="📋 ORB Ledger 累計", box=box.ROUNDED, style="dim")
    try:
        df = pd.read_csv(LEDGER_PATH, dtype={"ticker": str})
    except Exception:
        return Panel("[red]ledger 讀取失敗[/red]", title="📋 ORB Ledger", box=box.ROUNDED)

    if df.empty:
        return Panel("[dim]尚無紀錄[/dim]", title="📋 ORB Ledger", box=box.ROUNDED, style="dim")

    closed = df[df["status"] == "closed"] if "status" in df.columns else pd.DataFrame()
    opens = df[df["status"] == "open"] if "status" in df.columns else pd.DataFrame()

    lines = []
    lines.append(f"[bold]共 {len(df)} 筆[/bold]  ([green]closed: {len(closed)}[/green] / [yellow]open: {len(opens)}[/yellow])")

    if not closed.empty:
        wins = (closed["is_winner"].astype(str).str.lower() == "true").sum()
        win_rate = wins / len(closed) * 100
        mean_net = closed["net_return_pct"].mean()
        total = closed["net_return_pct"].sum()
        worst = closed["net_return_pct"].min()
        best = closed["net_return_pct"].max()

        wr_color = "green" if win_rate >= 50 else "red"
        m_color = "green" if mean_net > 0 else "red"
        t_color = "green" if total > 0 else "red"

        lines.append("")
        lines.append(
            f"勝率 [bold {wr_color}]{win_rate:.0f}%[/bold {wr_color}]  "
            f"({wins}W / {len(closed)-wins}L)  "
            f"│  Mean [bold {m_color}]{'+' if mean_net >= 0 else ''}{mean_net:.2f}%[/bold {m_color}]/筆"
        )
        lines.append(
            f"累計 [bold {t_color}]{'+' if total >= 0 else ''}{total:.2f}%[/bold {t_color}]  "
            f"│  最佳 [green]+{best:.2f}%[/green]  "
            f"│  最差 [red]{worst:.2f}%[/red]"
        )

    return Panel("\n".join(lines), title="📋 ORB Ledger 累計",
                 box=box.ROUNDED, style="cyan")


def main() -> None:
    now = datetime.now()

    # ── Header ──
    state_text, state_color = market_state(now)
    weekday = ['一', '二', '三', '四', '五', '六', '日'][now.weekday()]
    header = Text()
    header.append("📊 INVEST 儀表板  ", style="bold cyan")
    header.append(now.strftime("%Y-%m-%d %H:%M:%S"), style="white")
    header.append(f"  (週{weekday})  ", style="dim")
    header.append(state_text, style=f"bold {state_color}")
    console.print()
    console.print(Panel(header, box=box.DOUBLE, style=state_color))

    # 讀 assets
    if not ASSETS_JSON.exists():
        console.print("[red]❌ assets.json 不存在[/red]")
        return
    with ASSETS_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # ── Holdings ──
    holdings_p, info = holdings_panel(data)
    console.print(holdings_p)

    # ── ORB 訊號狀態 ──
    console.print(orb_signal_panel(now))

    # ── DCA 進度 ──
    console.print(dca_progress_panel(info["ticker_shares"]))

    # ── 下一個動作 ──
    console.print(next_actions_panel(info["ticker_shares"], info["cash"], now))

    # ── ORB Ledger ──
    console.print(orb_ledger_panel())


if __name__ == "__main__":
    main()
