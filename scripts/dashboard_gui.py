"""
INVEST 儀表板 GUI — tkinter 純 Python 內建，零依賴。

對應妖幣樂透掃描器同一視覺風格：
  - 深色主題
  - 標題 + 狀態指示器
  - 按鈕 row（啟動 daemon / 立即偵測 / 立即平倉 / 開啟 log）
  - 累計成績 panel
  - 持股表格
  - ORB 訊號狀態
  - DCA 進度條
  - 下一個動作清單
  - 事件 log（最近）

執行：python scripts/dashboard_gui.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path
from queue import Queue, Empty

# === DPI awareness — 解決 4K / 高 DPI 螢幕的字體模糊問題 ===
# 必須在 import tkinter 之前
if sys.platform == "win32":
    try:
        from ctypes import windll
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE (Win 8.1+)
            windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            # fallback: SetProcessDPIAware (Win Vista+)
            windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from tkinter import font as tkfont

import pandas as pd

# pythonw 模式下 sys.stdout 是 None，要先檢查
if sys.stdout is not None and getattr(sys.stdout, "buffer", None) is not None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
        except Exception:
            pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ASSETS_JSON = ROOT / "data" / "assets.json"
LEDGER_PATH = ROOT / "data" / "paper_trades" / "orb_ledger.csv"
LOG_DIR = ROOT / "logs"
PYTHON = sys.executable

# 深色主題色
COLORS = {
    "bg":       "#1e1e1e",
    "bg2":      "#252525",
    "bg3":      "#2d2d2d",
    "fg":       "#d4d4d4",
    "fg_dim":   "#808080",
    "accent":   "#4ec9b0",
    "yellow":   "#dcdcaa",
    "green":    "#6a9955",
    "red":      "#f48771",
    "orange":   "#ce9178",
    "blue":     "#569cd6",
    "border":   "#3e3e42",
}

# DCA 計畫
DCA_PLAN = {
    "0050":  {"target": 1000, "first_batch": (300, "88.5-90.5"), "limit_high": 90.5},
    "00881": {"target": 1100, "first_batch": (350, "45.0-46.5"), "limit_high": 46.5},
    "00947": {"target": 1000, "first_batch": (300, "29.5-30.5"), "limit_high": 30.5},
    "00646": {"target": 1700, "first_batch": (500, "69.5-71.0"), "limit_high": 71.0},
    "EWY":   {"target": 12,   "first_batch": (2,   "152-156 USD"), "limit_high": 156},
}

ORB_RULES = {
    "2408": {"entry_time": "09:15", "vol_threshold": "30%", "ref": "open5"},
    "2485": {"entry_time": "09:45", "vol_threshold": "30%", "ref": "open15"},
}

# 法人訊號規則（驗證有真 alpha 的）
INSTITUTIONAL_SIGNALS = {
    # 跨牛熊 9 年驗證 4/4 期 alpha 正 — 真 robust
    "0050":   {"name": "元大台灣50", "investor": "Dealer_self",
               "n_consec_strong": 5, "n_consec_weak": 3,
               "alpha": "✅ robust 跨 4 期 +1.23% (2017-2026)"},
    # 牛市才強 — 加 caveat
    "2308":   {"name": "台達電", "investor": "Foreign_Investor",
               "n_consec_strong": 5, "n_consec_weak": 3,
               "alpha": "⚠️ regime-dep (2021-2022 熊市 -1.31%)"},
    "006208": {"name": "富邦台50", "investor": "Foreign_Investor",
               "n_consec_strong": 5, "n_consec_weak": 3,
               "alpha": "⚠️ 牛市才強 (2021-2022 熊市 -0.78%)"},
    "00881":  {"name": "國泰台灣5G+", "investor": "Foreign_Investor",
               "n_consec_strong": 5, "n_consec_weak": 3,
               "alpha": "⚠️ IPO 2020-12 sample 不足跨多空"},
}

# 退勢空 Tier B watchlist（觀察用，不 paper trade）
SHORT_WATCHLIST = {
    "3231": {"name": "緯創", "pump": 3.0, "vol": 30, "retreat": 0.5, "tp": 2.0,
             "stats": "win 80%/n=28 OOS +0.67%"},
    "2344": {"name": "華邦電", "pump": 3.0, "vol": 50, "retreat": 1.0, "tp": 1.0,
             "stats": "win 81%/n=47 OOS +0.38% (sample 大)"},
    "1582": {"name": "信錦", "pump": 3.0, "vol": 30, "retreat": 1.0, "tp": 2.0,
             "stats": "win 73%/n=29 OOS +0.34%"},
    "6533": {"name": "晶心科", "pump": 2.0, "vol": 50, "retreat": 1.0, "tp": 2.0,
             "stats": "win 60%/n=50 OOS +0.26%"},
    "6669": {"name": "緯穎", "pump": 2.0, "vol": 50, "retreat": 0.5, "tp": 1.0,
             "stats": "win 70%/n=94 OOS +0.24% (sample 最大)"},
}


# ── 小工具 ──
def _yf_symbol(ticker: str) -> str:
    if ticker.startswith("^") or "." in ticker:
        return ticker
    if ticker.isdigit() and 4 <= len(ticker) <= 6:
        return f"{ticker}.TW"
    return ticker


def fetch_price(ticker: str) -> float:
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


EXTRA_NAMES = {
    # ETFs
    "0050":   "元大台灣50",
    "0056":   "元大高股息",
    "006208": "富邦台50",
    "00646":  "元大S&P500",
    "00878":  "國泰永續高股息",
    "00881":  "國泰台灣5G+",
    "00919":  "群益高息",
    "00929":  "復華科技優息",
    "00940":  "元大價值高息",
    "00946":  "群益台ESG",
    "00947":  "中信半導體",
    "009819": "中信數據基建",
    "EWY":    "iShares 韓國",
    # 持股 / watchlist 個股
    "2345":   "智邦",
    "6770":   "力積電",
    "2408":   "南亞科",
    "2485":   "兆赫",
    "2330":   "台積電",
    "2317":   "鴻海",
    "2454":   "聯發科",
    "2308":   "台達電",
    "2376":   "技嘉",
    "2382":   "廣達",
    "3231":   "緯創",
    "3037":   "欣興",
    "3017":   "奇鋐",
    "2344":   "華邦電",
    "8046":   "南電",
    "3189":   "景碩",
    "3711":   "日月光",
    "6669":   "緯穎",
    "5274":   "信驊",
    "1582":   "信錦",
    "6533":   "晶心科",
    "3596":   "智易",
    "3324":   "雙鴻",
    "3338":   "泰碩",
    "1503":   "士電",
    "1513":   "中興電",
    "1519":   "華城",
    "3105":   "穩懋",
    "2455":   "全新",
}


def lookup_name(ticker: str) -> str:
    if ticker in EXTRA_NAMES:
        return EXTRA_NAMES[ticker]
    try:
        from src.strategy.volume_anomaly_scanner import lookup_ticker_name
        return lookup_ticker_name(str(ticker)) or ""
    except Exception:
        return ""


def market_state_text(now: datetime) -> tuple[str, str]:
    if now.weekday() >= 5:
        return "🟫 假日", COLORS["fg_dim"]
    t = now.time()
    if t < dt_time(9, 0):
        delta = (datetime.combine(now.date(), dt_time(9, 0)) - now).total_seconds() / 60
        return f"🌙 盤前 (距 09:00 還 {int(delta)} 分)", COLORS["blue"]
    if t < dt_time(13, 30):
        return "🟢 盤中", COLORS["green"]
    if t < dt_time(14, 30):
        return "🟡 盤後初期", COLORS["yellow"]
    return "🔵 盤後", COLORS["fg_dim"]


# ── Dashboard 主類 ──
class Dashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("INVEST 儀表板")
        self.geometry("1200x900")
        self.configure(bg=COLORS["bg"])

        self.event_queue: Queue = Queue()
        self.auto_enabled = True
        self.auto_done = {"briefing": False, "morning": False, "close": False,
                          "ledger_scan": False, "health_check": False}
        self.last_auto_check_date = None

        # 警報狀態追蹤（避免重複推 Discord）
        self.alert_state = {
            "2345_strong_alert": False,    # 智邦 > 2280
            "2345_target_alert": False,    # 智邦 ≥ 2460
            "2345_stop_alert": False,      # 智邦 ≤ 1925
            "009819_stop_alert": False,    # 009819 < 9.0
            "0050_pullback_alert": False,  # 0050 < 90.5 (回限價區)
            "00947_pullback_alert": False,
        }

        self._setup_style()
        self._build_layout()

        self.refresh()
        self.after(self._refresh_interval(), self._auto_refresh)
        self.after(500, self._poll_events)
        self.after(5_000, self._check_auto_triggers)  # 每 5 秒檢查時間

    # ── Style ──
    def _setup_style(self):
        # 自動偵測最佳字體（fallback 順序 = 清晰度最高優先）
        available = set(tkfont.families())
        # 中文 / UI font
        for cand in ["Noto Sans CJK TC", "Noto Sans TC",
                      "Microsoft JhengHei UI", "PingFang TC",
                      "Segoe UI", "Arial Unicode MS"]:
            if cand in available:
                self.UI_FONT = cand
                break
        else:
            self.UI_FONT = "Microsoft JhengHei UI"
        # 等寬字（表格 / 程式碼）
        for cand in ["JetBrains Mono", "Cascadia Code", "Cascadia Mono",
                      "Consolas", "Courier New"]:
            if cand in available:
                self.MONO_FONT = cand
                break
        else:
            self.MONO_FONT = "Consolas"

        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=COLORS["bg"], foreground=COLORS["fg"],
                    fieldbackground=COLORS["bg2"], borderwidth=0)
        s.configure("TFrame", background=COLORS["bg"])
        s.configure("Card.TFrame", background=COLORS["bg2"], relief="flat")
        s.configure("TLabel", background=COLORS["bg"], foreground=COLORS["fg"])
        s.configure("Card.TLabel", background=COLORS["bg2"])
        s.configure("Title.TLabel", font=(self.UI_FONT, 18, "bold"),
                    foreground=COLORS["accent"])
        s.configure("Section.TLabel", font=(self.UI_FONT, 13, "bold"),
                    foreground=COLORS["accent"])
        s.configure("Big.TLabel", font=(self.UI_FONT, 16, "bold"),
                    foreground=COLORS["fg"], background=COLORS["bg2"])
        s.configure("Status.TLabel", font=(self.UI_FONT, 12),
                    background=COLORS["bg"])
        s.configure("Treeview", background=COLORS["bg2"], foreground=COLORS["fg"],
                    fieldbackground=COLORS["bg2"], borderwidth=0,
                    rowheight=32, font=(self.UI_FONT, 11))
        s.configure("Treeview.Heading", background=COLORS["bg3"],
                    foreground=COLORS["accent"],
                    font=(self.UI_FONT, 11, "bold"), borderwidth=0)
        s.map("Treeview.Heading", background=[("active", COLORS["bg3"])])
        s.map("Treeview", background=[("selected", COLORS["bg3"])])
        s.configure("TButton", background=COLORS["bg3"], foreground=COLORS["fg"],
                    font=(self.UI_FONT, 11), borderwidth=0, padding=10)
        s.map("TButton",
              background=[("active", COLORS["accent"]), ("pressed", COLORS["accent"])],
              foreground=[("active", COLORS["bg"]), ("pressed", COLORS["bg"])])
        s.configure("Accent.TButton", background=COLORS["accent"], foreground=COLORS["bg"])
        s.map("Accent.TButton",
              background=[("active", COLORS["green"]), ("pressed", COLORS["green"])])
        s.configure("Danger.TButton", background=COLORS["red"], foreground=COLORS["bg"])
        s.map("Danger.TButton",
              background=[("active", "#c8553e"), ("pressed", "#c8553e")])
        s.configure("Horizontal.TProgressbar",
                    background=COLORS["accent"], troughcolor=COLORS["bg3"],
                    borderwidth=0, lightcolor=COLORS["accent"], darkcolor=COLORS["accent"])

    # ── Layout ──
    def _build_layout(self):
        # ── Header ──
        header = ttk.Frame(self, padding=(20, 15, 20, 10))
        header.pack(fill="x")
        ttk.Label(header, text="📊 INVEST 儀表板", style="Title.TLabel").pack(side="left")
        self.status_label = ttk.Label(header, text="", style="Status.TLabel")
        self.status_label.pack(side="left", padx=20)
        self.time_label = ttk.Label(header, text="", style="Status.TLabel",
                                     foreground=COLORS["fg_dim"])
        self.time_label.pack(side="right")

        # ── Buttons row ──
        btn_row = ttk.Frame(self, padding=(20, 5, 20, 10))
        btn_row.pack(fill="x")
        self.auto_btn = ttk.Button(btn_row, text="⏸ 暫停自動",
                                    command=self.toggle_auto, style="Accent.TButton")
        self.auto_btn.pack(side="left", padx=2)
        ttk.Button(btn_row, text="📰 立即晨報",
                   command=self.run_briefing).pack(side="left", padx=2)
        ttk.Button(btn_row, text="📄 看今日晨報",
                   command=self.open_today_briefing).pack(side="left", padx=2)
        ttk.Button(btn_row, text="⏵ 手動 morning",
                   command=lambda: self.run_orb("morning")).pack(side="left", padx=2)
        ttk.Button(btn_row, text="⏵ 手動 close",
                   command=lambda: self.run_orb("close")).pack(side="left", padx=2)
        ttk.Button(btn_row, text="➕ 記錄交易", command=self.open_transaction_dialog,
                   style="Accent.TButton").pack(side="left", padx=2)
        ttk.Button(btn_row, text="🔄 重新整理", command=self.refresh).pack(side="left", padx=2)
        ttk.Button(btn_row, text="🌐 重抓行情",
                   command=self.force_refresh_quotes).pack(side="left", padx=2)
        ttk.Button(btn_row, text="📁 開啟 logs", command=self.open_logs).pack(side="left", padx=2)
        ttk.Button(btn_row, text="📁 開啟專案",
                   command=lambda: os.startfile(ROOT)).pack(side="left", padx=2)
        ttk.Button(btn_row, text="❓ 說明", command=self.show_help).pack(side="left", padx=2)
        self.daemon_status_label = ttk.Label(
            btn_row, text="🟢 自動偵測中 (09:20 / 13:25)",
            foreground=COLORS["green"], font=(self.UI_FONT, 11, "bold"))
        self.daemon_status_label.pack(side="right")

        # ── 主內容區 (左右，可滾動) ──
        main = ttk.Frame(self, padding=(15, 5))
        main.pack(fill="both", expand=True)

        # ── 左側：持股 + ORB + DCA（加 scrollbar）──
        left_wrapper = ttk.Frame(main)
        left_wrapper.pack(side="left", fill="both", expand=True, padx=(0, 8))

        left_canvas = tk.Canvas(left_wrapper, bg=COLORS["bg"], highlightthickness=0)
        left_scroll = ttk.Scrollbar(left_wrapper, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)

        left = ttk.Frame(left_canvas)
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")

        def _on_left_resize(event=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
            left_canvas.itemconfig(left_window, width=left_canvas.winfo_width())
        left.bind("<Configure>", _on_left_resize)
        left_canvas.bind("<Configure>",
                         lambda e: left_canvas.itemconfig(left_window, width=e.width))

        # 滑鼠滾輪（Windows）
        def _on_mousewheel(e):
            left_canvas.yview_scroll(-int(e.delta / 120), "units")
        left_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self._build_hero_action(left)          # Hero Action Panel (P0, 永遠最上)
        self._build_summary(left)
        self._build_v2_regime(left)            # V2 5-regime classifier (added 2026-05-05)
        self._build_hedge_signals(left)        # 5 hedge signals (added 2026-05-05)
        self._build_barbell_target(left)       # Barbell allocation (added 2026-05-05)
        # self._build_regime_status(left)      # REMOVED: old strategy gate (與 V2 重複)
        self._build_overnight(left)
        self._build_dca_timing(left)
        self._build_holdings(left)
        self._build_orb_signal(left)
        self._build_institutional_signal(left)
        self._build_short_watchlist(left)
        self._build_dca(left)

        # ── 右側：動作 + 事件 log（也加 scrollbar）──
        right_wrapper = ttk.Frame(main, width=440)
        right_wrapper.pack(side="right", fill="both", expand=False)
        right_wrapper.pack_propagate(False)

        right_canvas = tk.Canvas(right_wrapper, bg=COLORS["bg"], highlightthickness=0)
        right_scroll = ttk.Scrollbar(right_wrapper, orient="vertical", command=right_canvas.yview)
        right_canvas.configure(yscrollcommand=right_scroll.set)
        right_scroll.pack(side="right", fill="y")
        right_canvas.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(right_canvas)
        right_win = right_canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind("<Configure>",
                    lambda e: right_canvas.configure(scrollregion=right_canvas.bbox("all")))
        right_canvas.bind("<Configure>",
                          lambda e: right_canvas.itemconfig(right_win, width=e.width))

        self._build_actions(right)
        self._build_system_signals(right)
        self._build_recent_trades(right)
        self._build_event_log(right)

    def _add_tooltip(self, widget, text: str):
        """Tkinter native tooltip (hover 顯示說明)。"""
        tooltip = None

        def show(event):
            nonlocal tooltip
            if tooltip is not None:
                return
            x, y, _, _ = widget.bbox("insert") if hasattr(widget, "bbox") else (0, 0, 0, 0)
            x = widget.winfo_rootx() + 20
            y = widget.winfo_rooty() + widget.winfo_height() + 5
            tooltip = tk.Toplevel(widget)
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                tooltip, text=text, justify="left",
                background="#2a2a2a", foreground="#e0e0e0",
                relief="solid", borderwidth=1,
                font=(self.UI_FONT, 9), wraplength=350, padx=8, pady=6,
            )
            label.pack()

        def hide(event):
            nonlocal tooltip
            if tooltip is not None:
                tooltip.destroy()
                tooltip = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # 專業詞彙解說（給 _add_tooltip 用）
    GLOSSARY = {
        "z-score":
            "標準差倍數。z=0 為平均值，|z|>2 通常視為極端事件 (約 95%以外的尾部)。"
            "例: TX OI z=-2 = 外資台指期空單堆積至過去 252 日平均之下 2 個標準差。",
        "pp":
            "Percentage points (百分點). +24pp 不是 +24%。"
            "例: 部位從 4% 變到 28% 是 +24pp 的差距。",
        "regime":
            "5-regime classifier (V2 2026-05-04):\n"
            "• CRASH: 60d ret <-15% AND vol30 >25% (鑽石買點)\n"
            "• BEAR: dist MA200 <-5% AND ret_60d <0\n"
            "• SIDEWAYS: |dist MA200| <5%\n"
            "• BULL_TREND: dist MA200 在 0-20%\n"
            "• STRONG_BULL: dist MA200 >+20%",
        "VIX":
            "S&P 500 隱含波動度（30 日 forward）。"
            ">30 = panic, <15 = complacency。"
            "VIX/VIX3M 比值 >1.05 = term structure 警戒（短期恐慌 > 中期）。",
        "TX OI":
            "外資台指期 net open interest (淨未平倉)。"
            "z<-2 = 外資極度偏空 → 歷史 10d TAIEX alpha +1.43% (mean reversion)。",
        "barbell":
            "8-bucket 槓鈴配置: 核心 TW + 美股 + 黃金 + 日股 + 槓桿 + 衛星 + legacy + 現金。"
            "deltas 顯示與目標差距。",
        "L4 流動性":
            "日均成交額 > 10 億 NT$ 的大型股。"
            "Revenue YoY portfolio 經 L4 filter 後 alpha +25.7%/yr (vs 0050 +21.7%)。",
    }

    def _section(self, parent, title: str) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=(15, 10))
        frame.pack(fill="x", pady=(0, 8))
        ttk.Label(frame, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        body = ttk.Frame(frame, style="Card.TFrame")
        body.pack(fill="x")
        return body

    def _build_summary(self, parent):
        body = self._section(parent, "💰 累計成績")
        cols = ttk.Frame(body, style="Card.TFrame")
        cols.pack(fill="x")
        self.summary_labels = {}
        for i, key in enumerate(["總資產", "現金", "持股市值", "檔數"]):
            cell = ttk.Frame(cols, style="Card.TFrame", padding=(6, 0))
            cell.grid(row=0, column=i, sticky="w", padx=(0, 30))
            ttk.Label(cell, text=key, style="Card.TLabel",
                      foreground=COLORS["fg_dim"]).pack(anchor="w")
            v = ttk.Label(cell, text="—", style="Big.TLabel")
            v.pack(anchor="w")
            self.summary_labels[key] = v

    def _build_regime_status(self, parent):
        body = self._section(parent, "📊 Regime + 策略 Gate (自動暫停 regime-dep 策略)")
        self.regime_label = ttk.Label(body, text="計算中...", style="Card.TLabel",
                                       font=(self.UI_FONT, 12, "bold"))
        self.regime_label.pack(anchor="w", pady=2)
        self.regime_active = ttk.Label(body, text="", style="Card.TLabel",
                                        foreground=COLORS["green"])
        self.regime_active.pack(anchor="w", pady=1)
        self.regime_suspended = ttk.Label(body, text="", style="Card.TLabel",
                                           foreground=COLORS["red"])
        self.regime_suspended.pack(anchor="w", pady=1)

    def _build_hero_action(self, parent):
        """🎯 Hero Action Panel — 今日 Top 行動指令 (P0)"""
        # Use prominent style to grab attention
        frame = ttk.Frame(parent, style="Card.TFrame", padding=(15, 12))
        frame.pack(fill="x", pady=(0, 8))
        # Title with regime indicator
        self.hero_title = ttk.Label(
            frame, text="🎯 今日行動指令", style="Section.TLabel",
            font=(self.UI_FONT, 14, "bold"),
        )
        self.hero_title.pack(anchor="w", pady=(0, 4))
        # Regime status line
        self.hero_status = ttk.Label(
            frame, text="計算中...", style="Card.TLabel",
            foreground=COLORS["fg_dim"],
        )
        self.hero_status.pack(anchor="w", pady=(0, 6))
        # Actions list (5 lines)
        self.hero_action_labels = []
        for _ in range(5):
            lbl = ttk.Label(frame, text="", style="Card.TLabel",
                            wraplength=620, font=(self.UI_FONT, 11))
            lbl.pack(anchor="w", pady=1)
            self.hero_action_labels.append(lbl)
        # Cash bar
        self.hero_cash = ttk.Label(
            frame, text="", style="Card.TLabel",
            foreground=COLORS["yellow"],
            font=(self.UI_FONT, 11, "bold"),
        )
        self.hero_cash.pack(anchor="w", pady=(8, 2))

    def _build_v2_regime(self, parent):
        """V2 5-regime classifier (CRASH / BEAR / SIDEWAYS / BULL_TREND / STRONG_BULL)"""
        body = self._section(parent, "🎯 市場 Regime V2 (5-regime classifier) ⓘ")
        self.v2_regime_label = ttk.Label(body, text="計算中...", style="Card.TLabel",
                                          font=(self.UI_FONT, 13, "bold"))
        self.v2_regime_label.pack(anchor="w", pady=2)
        self._add_tooltip(self.v2_regime_label, self.GLOSSARY["regime"])
        self.v2_regime_metrics = ttk.Label(body, text="", style="Card.TLabel",
                                            foreground=COLORS["fg_dim"])
        self.v2_regime_metrics.pack(anchor="w", pady=1)
        self.v2_regime_action = ttk.Label(body, text="", style="Card.TLabel",
                                           wraplength=600)
        self.v2_regime_action.pack(anchor="w", pady=2)

    def _build_hedge_signals(self, parent):
        """5 hedge signals overlay"""
        body = self._section(parent, "🛡️ Hedge Signals (5-signal crash overlay) ⓘ")
        self.hedge_tilt_label = ttk.Label(body, text="計算中...", style="Card.TLabel",
                                           font=(self.UI_FONT, 12, "bold"))
        self.hedge_tilt_label.pack(anchor="w", pady=2)
        self._add_tooltip(self.hedge_tilt_label,
                           "Cash tilt: 因 hedge 訊號疊加而要超出 baseline 的現金比例。"
                           "0pp = 正常；>10pp = 警戒；>20pp = 多重危機觸發。")
        self.hedge_signals_grid = ttk.Frame(body, style="Card.TFrame")
        self.hedge_signals_grid.pack(fill="x", pady=2)
        self.hedge_signal_labels = {}
        sig_tooltips = {
            "TX OI z": self.GLOSSARY["TX OI"],
            "VIX": self.GLOSSARY["VIX"],
            "VIX/VIX3M": "VIX (30d 隱含波動) / VIX3M (93d). >1.05 = 短期恐慌結構 (term backwardation).",
            "TX basis": "TX 期貨 - TWII 現貨. |z|>2 = 極端結構 (informational only, OOS 1/3 robust).",
            "SPY 隔夜": "SPY today close vs yesterday close (US 04:00 TW 時間). <-2% → TW 隔日 +0.86% reversion 傾向.",
        }
        for i, sig in enumerate(["TX OI z", "VIX", "VIX/VIX3M", "TX basis", "SPY 隔夜"]):
            cell = ttk.Frame(self.hedge_signals_grid, style="Card.TFrame", padding=(4, 2))
            cell.grid(row=0, column=i, sticky="w", padx=(0, 12))
            name_lbl = ttk.Label(cell, text=sig + " ⓘ", style="Card.TLabel",
                      foreground=COLORS["fg_dim"], font=(self.UI_FONT, 9))
            name_lbl.pack(anchor="w")
            self._add_tooltip(name_lbl, sig_tooltips.get(sig, ""))
            v = ttk.Label(cell, text="—", style="Card.TLabel",
                          font=(self.UI_FONT, 11, "bold"))
            v.pack(anchor="w")
            self.hedge_signal_labels[sig] = v

    def _build_barbell_target(self, parent):
        """Barbell allocation target vs current"""
        body = self._section(parent, "💼 Barbell 配置（regime-aware target vs current）ⓘ")
        # Tooltip for the section title (need to grab the title label)
        title_label = body.master.winfo_children()[0]  # the Section.TLabel
        self._add_tooltip(title_label, self.GLOSSARY["barbell"])
        self.barbell_regime_label = ttk.Label(body, text="計算中...", style="Card.TLabel",
                                                font=(self.UI_FONT, 11, "bold"))
        self.barbell_regime_label.pack(anchor="w", pady=2)
        # Table-like grid: bucket / current / target / delta
        self.barbell_grid = ttk.Frame(body, style="Card.TFrame")
        self.barbell_grid.pack(fill="x", pady=4)
        self.barbell_rows = {}  # bucket key → {curr, target, delta} labels
        # Header
        for j, h in enumerate(["類別", "當前", "目標", "Δ"]):
            ttk.Label(self.barbell_grid, text=h, style="Card.TLabel",
                      foreground=COLORS["fg_dim"], font=(self.UI_FONT, 9, "bold")
                      ).grid(row=0, column=j, sticky="w", padx=(0, 12))
        self.barbell_actions_label = ttk.Label(body, text="", style="Card.TLabel",
                                                wraplength=600)
        self.barbell_actions_label.pack(anchor="w", pady=4)

    def _build_dca_timing(self, parent):
        body = self._section(parent, "📅 今日 DCA Timing 評分（基於 9 年日曆 anomaly）")
        self.dca_timing_label = ttk.Label(body, text="計算中...", style="Card.TLabel",
                                           font=(self.UI_FONT, 12, "bold"))
        self.dca_timing_label.pack(anchor="w", pady=2)
        self.dca_timing_detail = ttk.Label(body, text="", style="Card.TLabel",
                                            foreground=COLORS["fg_dim"])
        self.dca_timing_detail.pack(anchor="w", pady=2)

    def _build_overnight(self, parent):
        body = self._section(parent, "🌙 夜盤訊號 (預測明日開盤跳空，hit ~75%)")
        cols = ("symbol", "name", "close", "change", "implied")
        tv = ttk.Treeview(body, columns=cols, show="headings", height=5)
        for c, w, txt in [
            ("symbol", 70, "美股"),
            ("name", 110, "名稱"),
            ("close", 80, "收盤"),
            ("change", 80, "漲跌%"),
            ("implied", 220, "預測明日 TW 開盤"),
        ]:
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="x")
        tv.tag_configure("bullish", foreground=COLORS["green"])
        tv.tag_configure("bearish", foreground=COLORS["red"])
        tv.tag_configure("neutral", foreground=COLORS["fg_dim"])
        self.overnight_tv = tv

    def _build_holdings(self, parent):
        body = self._section(parent, "💼 持股")
        cols = ("ticker", "name", "shares", "cost", "price", "mv", "pnl", "pct")
        tv = ttk.Treeview(body, columns=cols, show="headings", height=5,
                          style="Treeview")
        for c, w, txt, anchor in [
            ("ticker", 60, "代號", "w"),
            ("name", 130, "名稱", "w"),
            ("shares", 70, "股數", "e"),
            ("cost", 70, "成本", "e"),
            ("price", 70, "現價", "e"),
            ("mv", 90, "市值", "e"),
            ("pnl", 95, "損益", "e"),
            ("pct", 75, "%", "e"),
        ]:
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor=anchor, stretch=False)
        tv.pack(fill="x")
        tv.tag_configure("up", foreground=COLORS["green"])
        tv.tag_configure("down", foreground=COLORS["red"])
        self.holdings_tv = tv

    def _build_orb_signal(self, parent):
        body = self._section(parent, "🎯 今日 ORB 訊號 (paper trade)")
        cols = ("ticker", "name", "rule", "status", "detail")
        tv = ttk.Treeview(body, columns=cols, show="headings", height=2)
        for c, w, txt in [
            ("ticker", 60, "代號"), ("name", 110, "名稱"),
            ("rule", 180, "規則"), ("status", 150, "狀態"),
            ("detail", 280, "詳情"),
        ]:
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="x")
        tv.tag_configure("triggered", foreground=COLORS["yellow"])
        tv.tag_configure("waiting", foreground=COLORS["blue"])
        tv.tag_configure("done_win", foreground=COLORS["green"])
        tv.tag_configure("done_loss", foreground=COLORS["red"])
        tv.tag_configure("none", foreground=COLORS["fg_dim"])
        self.orb_tv = tv

    def _build_institutional_signal(self, parent):
        body = self._section(parent, "📡 法人訊號 (真 alpha 驗證後)")
        cols = ("ticker", "name", "investor", "consec", "status", "alpha")
        tv = ttk.Treeview(body, columns=cols, show="headings", height=2)
        for c, w, txt in [
            ("ticker", 60, "代號"), ("name", 110, "名稱"),
            ("investor", 70, "法人"), ("consec", 100, "連買天數"),
            ("status", 100, "訊號"), ("alpha", 200, "歷史 alpha"),
        ]:
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="x")
        tv.tag_configure("strong", foreground=COLORS["green"])
        tv.tag_configure("weak", foreground=COLORS["yellow"])
        tv.tag_configure("none", foreground=COLORS["fg_dim"])
        self.inst_signal_tv = tv

    def _build_short_watchlist(self, parent):
        body = self._section(parent, "👁 退勢空 Watchlist (Tier B 觀察，不下單)")
        cols = ("ticker", "name", "rule", "stats", "today")
        tv = ttk.Treeview(body, columns=cols, show="headings", height=5)
        for c, w, txt in [
            ("ticker", 60, "代號"),
            ("name", 75, "名稱"),
            ("rule", 145, "進場規則"),
            ("stats", 220, "歷史表現 (Tier B)"),
            ("today", 130, "今日狀態"),
        ]:
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="x")
        tv.tag_configure("watching", foreground=COLORS["yellow"])
        tv.tag_configure("hot", foreground=COLORS["red"])
        tv.tag_configure("idle", foreground=COLORS["fg_dim"])
        self.short_tv = tv

    def _build_dca(self, parent):
        body = self._section(parent, "📈 DCA 進度")
        self.dca_widgets = {}
        for i, (tk_, plan) in enumerate(DCA_PLAN.items()):
            row = ttk.Frame(body, style="Card.TFrame", padding=(0, 3))
            row.pack(fill="x")
            ttk.Label(row, text=tk_, style="Card.TLabel",
                      width=8, foreground=COLORS["yellow"]).pack(side="left")
            pb = ttk.Progressbar(row, length=200, maximum=100, value=0)
            pb.pack(side="left", padx=8)
            text_lbl = ttk.Label(row, text="—", style="Card.TLabel",
                                 foreground=COLORS["fg_dim"])
            text_lbl.pack(side="left", padx=8, fill="x", expand=True)
            self.dca_widgets[tk_] = (pb, text_lbl)

    def _build_actions(self, parent):
        body = self._section(parent, "🔔 下一個動作")
        cols = ("priority", "action", "limit")
        tv = ttk.Treeview(body, columns=cols, show="headings", height=10)
        for c, w, txt in [
            ("priority", 50, "優先"),
            ("action", 200, "動作"),
            ("limit", 130, "限價"),
        ]:
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="both", expand=True)
        tv.tag_configure("must", foreground=COLORS["red"])
        tv.tag_configure("suggest", foreground=COLORS["yellow"])
        tv.tag_configure("watch", foreground=COLORS["blue"])
        self.actions_tv = tv

    def _build_system_signals(self, parent):
        """系統訊號面板：部署排程 + 集中度警報 + Alpha Decay。"""
        body = self._section(parent, "📊 系統訊號")
        text = tk.Text(
            body, wrap="word", height=18,
            font=(self.MONO_FONT, 11),
            bg=COLORS["bg2"], fg=COLORS["fg"],
            borderwidth=0, padx=8, pady=8,
        )
        text.pack(fill="both", expand=True)

        text.tag_configure("h2", foreground=COLORS["accent"],
                           font=(self.UI_FONT, 13, "bold"), spacing1=6)
        text.tag_configure("h3", foreground=COLORS["yellow"],
                           font=(self.UI_FONT, 12, "bold"), spacing1=4)
        text.tag_configure("alert", foreground=COLORS["red"])
        text.tag_configure("good", foreground=COLORS["green"])
        text.tag_configure("warn", foreground=COLORS["yellow"])
        text.tag_configure("dim", foreground=COLORS["fg_dim"])
        self.system_signals_text = text

    def _update_system_signals(self):
        """重新渲染部署排程 + Alpha Decay 內容。"""
        if not hasattr(self, "system_signals_text"): return
        text = self.system_signals_text
        text.config(state="normal")
        text.delete("1.0", "end")

        sections_md = []
        try:
            from src.report.deployment_section import render_deployment_section
            sections_md.append(render_deployment_section(ROOT))
        except Exception as e:
            sections_md.append(f"## 部署排程\n讀取失敗: {e}\n")

        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            from alpha_decay_monitor import render_briefing_section as render_decay
            sections_md.append(render_decay())
        except Exception as e:
            sections_md.append(f"## Alpha Decay\n讀取失敗: {e}\n")

        full = "\n".join(sections_md)
        for line in full.splitlines():
            start = text.index("end-1c")
            text.insert("end", line + "\n")
            end = text.index("end-1c")
            if line.startswith("## "):
                text.tag_add("h2", start, end)
            elif line.startswith("### "):
                text.tag_add("h3", start, end)
            elif "🚨" in line:
                text.tag_add("alert", start, end)
            elif "✅" in line or "🟢" in line:
                text.tag_add("good", start, end)
            elif "⚠️" in line or "🟡" in line or "🟠" in line:
                text.tag_add("warn", start, end)
            elif line.startswith("    _"):
                text.tag_add("dim", start, end)
        text.config(state="disabled")

    def _build_recent_trades(self, parent):
        body = self._section(parent, "💹 最近交易")
        cols = ("date", "action", "ticker", "name", "shares", "price", "pnl")
        tv = ttk.Treeview(body, columns=cols, show="headings", height=5)
        for c, w, txt in [
            ("date", 75, "日期"),
            ("action", 40, "動作"),
            ("ticker", 55, "代號"),
            ("name", 80, "名稱"),
            ("shares", 60, "股數"),
            ("price", 55, "價格"),
            ("pnl", 70, "已實現"),
        ]:
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="both", expand=False)
        tv.tag_configure("buy", foreground=COLORS["blue"])
        tv.tag_configure("sell_win", foreground=COLORS["green"])
        tv.tag_configure("sell_loss", foreground=COLORS["red"])
        self.trades_tv = tv

    def _build_event_log(self, parent):
        body = self._section(parent, "📋 事件 Log")
        self.log_box = scrolledtext.ScrolledText(
            body, height=18, width=50, bg=COLORS["bg3"], fg=COLORS["fg"],
            insertbackground=COLORS["fg"], borderwidth=0,
            font=(self.MONO_FONT, 11), wrap="word",
        )
        self.log_box.pack(fill="both", expand=True)
        self.log_box.config(state="disabled")
        self._log("Dashboard started")

    # ── Refresh ──
    def refresh(self):
        try:
            now = datetime.now()
            wd = ['一', '二', '三', '四', '五', '六', '日'][now.weekday()]
            state_text, state_color = market_state_text(now)
            self.status_label.config(text=state_text, foreground=state_color)
            self.time_label.config(
                text=f"{now.strftime('%Y-%m-%d %H:%M:%S')} (週{wd})"
            )

            # auto status — 顯示下個排程或結果
            if self.auto_enabled:
                next_action = []
                if not self.auto_done.get("briefing"):
                    next_action.append("08:00 晨報")
                if not self.auto_done.get("morning"):
                    next_action.append("09:20 morning")
                if not self.auto_done.get("close"):
                    next_action.append("13:25 close")
                if not self.auto_done.get("ledger_scan"):
                    next_action.append("14:00 ledger")
                if next_action:
                    self.daemon_status_label.config(
                        text="🟢 自動: " + " / ".join(next_action),
                        foreground=COLORS["green"])
                else:
                    self.daemon_status_label.config(
                        text="✅ 今日排程已完成",
                        foreground=COLORS["fg_dim"])

            # Holdings + summary
            data = self._load_assets()
            cash = float(data.get("cash", 0))
            holdings = data.get("holdings", {})
            all_h = (holdings.get("long_term", []) or []) + (holdings.get("short_term", []) or [])

            self.holdings_tv.delete(*self.holdings_tv.get_children())
            total_cost = 0.0
            total_mv = 0.0
            ticker_shares = {}
            for h in all_h:
                tk_ = str(h.get("ticker", ""))
                shares = int(h.get("shares", 0))
                cost = float(h.get("cost", 0))
                cost_incl_fee = float(h.get("cost_incl_fee", cost))
                name = lookup_name(tk_)
                price = fetch_price(tk_)
                mv = shares * price
                cost_total = shares * cost_incl_fee
                pnl = mv - cost_total
                pct = (price / cost_incl_fee - 1) * 100 if cost_incl_fee > 0 else 0.0
                total_cost += cost_total
                total_mv += mv
                ticker_shares[tk_] = shares
                tag = "up" if pnl > 0 else ("down" if pnl < 0 else "")
                sign = "+" if pnl >= 0 else ""
                self.holdings_tv.insert(
                    "", "end",
                    values=(tk_, name, f"{shares:,}",
                            f"{cost:.2f}", f"{price:.2f}", f"{mv:,.0f}",
                            f"{sign}{pnl:,.0f}", f"{sign}{pct:.2f}%"),
                    tags=(tag,)
                )

            net = cash + total_mv
            n_holdings = len(holdings)
            cash_pct = (cash / net * 100) if net > 0 else 0
            mv_pct = (total_mv / net * 100) if net > 0 else 0

            self.summary_labels["總資產"].config(text=f"NT${net:,.0f}")
            self.summary_labels["現金"].config(text=f"NT${cash:,} ({cash_pct:.1f}%)")
            self.summary_labels["持股市值"].config(text=f"NT${total_mv:,.0f} ({mv_pct:.1f}%)")
            self.summary_labels["檔數"].config(text=f"{n_holdings} 檔")

            # ORB signals
            self._update_orb_signals(now)

            # Institutional signals
            self._update_institutional_signal()

            # Overnight signals
            self._update_overnight()

            # DCA timing
            self._update_dca_timing(now)

            # V2 regime + hedge + barbell + hero (added 2026-05-05)
            try:
                self._update_v2_regime()
            except Exception as e:
                self._log(f"v2 regime refresh: {e}")
            try:
                self._update_hedge_signals()
            except Exception as e:
                self._log(f"hedge signals refresh: {e}")
            try:
                self._update_barbell()
            except Exception as e:
                self._log(f"barbell refresh: {e}")
            # Hero panel must run LAST (depends on regime + hedge + barbell)
            try:
                self._update_hero_action()
            except Exception as e:
                self._log(f"hero action refresh: {e}")

            # Short watchlist
            self._update_short_watchlist(now)

            # System signals (部署排程 + Alpha Decay)
            try:
                self._update_system_signals()
            except Exception as e:
                self._log(f"system signals refresh: {e}")

            # DCA progress
            for tk_, plan in DCA_PLAN.items():
                owned = ticker_shares.get(tk_, 0)
                pct = (owned / plan["target"]) * 100 if plan["target"] else 0
                pb, lbl = self.dca_widgets[tk_]
                pb["value"] = pct
                if owned == 0:
                    txt = f"  0 / {plan['target']:,}  立刻第1批 {plan['first_batch'][0]} @ {plan['first_batch'][1]}"
                    lbl.config(foreground=COLORS["fg"])
                else:
                    txt = f"  {owned:,} / {plan['target']:,} ({pct:.0f}%)"
                    lbl.config(foreground=COLORS["green"] if pct >= 100 else COLORS["yellow"])
                lbl.config(text=txt)

            # Actions
            self._update_actions(ticker_shares, cash, now)

            # Recent trades
            self._update_recent_trades()

        except Exception as e:
            self._log(f"refresh error: {e}")

    def _update_orb_signals(self, now: datetime):
        today = now.date()
        ledger_today = []
        if LEDGER_PATH.exists():
            try:
                import pandas as pd
                df = pd.read_csv(LEDGER_PATH, dtype={"ticker": str})
                ledger_today = df[df["trade_date"] == today.isoformat()].to_dict("records")
            except Exception:
                pass

        self.orb_tv.delete(*self.orb_tv.get_children())
        is_trading = now.weekday() < 5
        for tk_, rule in ORB_RULES.items():
            name = lookup_name(tk_)
            rule_str = f"{rule['entry_time']} v≥{rule['vol_threshold']} {rule['ref']}"
            if not is_trading:
                status = "🟫 假日"
                detail = "週末或假期"
                tag = "none"
            else:
                eh, em = map(int, rule["entry_time"].split(":"))
                check_dt = datetime.combine(today, dt_time(eh, em + 5))
                tk_today = [r for r in ledger_today if r.get("ticker") == tk_]
                opens = [r for r in tk_today if r.get("status") == "open"]
                closed = [r for r in tk_today if r.get("status") == "closed"]
                if closed:
                    r = closed[-1]
                    net = float(r.get("net_return_pct", 0))
                    if net > 0:
                        status = f"✅ 已平倉 +{net:.2f}%"
                        tag = "done_win"
                    else:
                        status = f"❌ 已平倉 {net:.2f}%"
                        tag = "done_loss"
                    detail = (f"{r['entry_price']:.2f} → {r['exit_price']:.2f}")
                elif opens:
                    r = opens[-1]
                    status = "🟢 已觸發"
                    detail = f"entry {float(r['entry_price']):.2f} @ {r.get('entry_time','')}"
                    tag = "triggered"
                elif now < check_dt:
                    wait = (check_dt - now).total_seconds() / 60
                    status = "⏳ 等待偵測"
                    detail = f"距 {rule['entry_time']} 還有 {int(wait)} 分"
                    tag = "waiting"
                elif now < datetime.combine(today, dt_time(13, 25)):
                    status = "⚪ 未觸發"
                    detail = "條件未達"
                    tag = "none"
                else:
                    status = "⚫ 收盤無訊號"
                    detail = "今日無 ORB"
                    tag = "none"
            self.orb_tv.insert("", "end",
                               values=(tk_, name, rule_str, status, detail),
                               tags=(tag,))

    def _push_alert(self, msg: str, key: str | None = None):
        """推 Discord 警報，避免重複（同一 key 只推一次）。"""
        if key:
            if self.alert_state.get(key):
                return
            self.alert_state[key] = True
        try:
            url = os.environ.get("DISCORD_WEBHOOK_URL", "")
            if url:
                from src.notify.discord_client import DiscordNotifier
                DiscordNotifier(url).send(msg)
                self._log(f"📢 Discord 警報: {msg.split(chr(10))[0]}")
        except Exception as e:
            self._log(f"alert error: {e}")

    def _update_actions(self, ticker_shares: dict, cash: float, now: datetime):
        self.actions_tv.delete(*self.actions_tv.get_children())
        actions = []

        # 先抓即時價判斷追價需求 + 警報觸發
        def get_price(tk_):
            try:
                import yfinance as yf
                t = yf.Ticker(_yf_symbol(tk_) if not tk_.endswith(".TW") else tk_)
                h = t.history(period="1d", auto_adjust=False)
                if not h.empty:
                    return float(h["Close"].iloc[-1])
            except Exception:
                pass
            return 0

        # 計算今日 DCA timing 評分（影響建議量）
        from calendar import monthrange
        d = now.date()
        days_to_end = monthrange(d.year, d.month)[1] - d.day
        is_quarter_end = d.month in [3, 6, 9, 12]
        timing_score = 0
        timing_note = ""
        if is_quarter_end and days_to_end == 0:
            timing_score = -4
            timing_note = "⚠️ 季底最後 1 日 暫停 DCA"
        elif is_quarter_end and days_to_end <= 4:
            timing_score = -1
            timing_note = "🟡 季底前 5 日 量減半"
        elif d.month in [1, 4, 7, 10] and d.day <= 5:
            timing_score = 2
            timing_note = "🟢 季初前 5 日 可加碼"
        elif days_to_end == 0:
            timing_score = -1
            timing_note = "🟡 月底最後 1 日 量減半"

        # 6770 賣出
        if ticker_shares.get("6770", 0) > 0:
            n = ticker_shares["6770"]
            actions.append(("must", f"賣 6770 剩 {n:,} 股", "55.0~55.2"))

        # 加 timing 標示
        if timing_note:
            actions.append(("watch", f"📅 今日 DCA timing: {timing_note}", ""))

        # DCA — 追價 vs 守限價邏輯 + timing 調整
        for tk_, plan in DCA_PLAN.items():
            if tk_ == "EWY":
                continue
            owned = ticker_shares.get(tk_, 0)
            if owned == 0:
                shares, price_label = plan["first_batch"]
                limit_high = plan.get("limit_high", 0)
                cur = get_price(tk_)

                # timing 量調整
                if timing_score <= -4:
                    qty_label = "暫停"
                    actions.append(("watch", f"⏸ 暫停 {tk_} DCA（{timing_note}）", ""))
                    continue
                elif timing_score == -1:
                    shares = shares // 2  # 月底/季底前 5 日減半
                elif timing_score == 2:
                    shares = int(shares * 1.3)  # 季初加碼

                if cur > 0 and limit_high > 0 and cur > limit_high * 1.01:
                    # 已超過限價區，再減量追價
                    half = shares * 2 // 3
                    pct_over = (cur / limit_high - 1) * 100
                    # 偵測回檔到限價區 → push Discord
                    if tk_ in ["0050", "00947"]:
                        # 重置 alert（價格回到區間）
                        self.alert_state[f"{tk_}_pullback_alert"] = False
                    actions.append(("suggest",
                                    f"買 {tk_} {half} 股 (減量追)",
                                    f"~{cur:.1f} (超 +{pct_over:.1f}%)"))
                elif cur > 0 and limit_high > 0 and cur <= limit_high:
                    # 回到限價區 → 推 Discord
                    if tk_ in ["0050", "00947"]:
                        self._push_alert(
                            f"🟢 **{tk_} 回到 DCA 限價區！**\n\n"
                            f"現價: {cur:.2f} (限價區 {price_label})\n"
                            f"建議掛限價買 {shares} 股",
                            key=f"{tk_}_pullback_alert"
                        )
                    actions.append(("suggest", f"買 {tk_} {shares} 股", price_label))
                else:
                    actions.append(("suggest", f"買 {tk_} {shares} 股", price_label))

        # EWY 5/5
        today = now.date()
        if today < date(2026, 5, 5):
            d = (date(2026, 5, 5) - today).days
            actions.append(("watch", f"5/5 EWY 第1批 2 股 ({d}天後)", "152-156 USD"))

        # 2345 動態 trailing stop + 警報
        if ticker_shares.get("2345", 0) > 0:
            cur_2345 = get_price("2345")

            # 達 target 警報
            if cur_2345 >= 2460:
                self._push_alert(
                    f"🎯 **2345 智邦 達停利 target 2,460！**\n\n"
                    f"現價: {cur_2345:.0f}\n"
                    f"成本: 2,140\n"
                    f"獲利: +{(cur_2345/2140-1)*100:.1f}%\n"
                    f"建議: 立刻停利賣出 30 股",
                    key="2345_target_alert"
                )

            # 達停損警報
            elif cur_2345 <= 1925:
                self._push_alert(
                    f"🔴 **2345 智邦 觸發停損 1,925！**\n\n"
                    f"現價: {cur_2345:.0f}\n"
                    f"虧損: {(cur_2345/2140-1)*100:.1f}%\n"
                    f"建議: 立刻停損出場",
                    key="2345_stop_alert"
                )

            # 強勢警報（推薦 trailing）
            elif cur_2345 > 2280:
                self._push_alert(
                    f"🟢 **2345 智邦 強勢 +6%！**\n\n"
                    f"現價: {cur_2345:.0f}\n"
                    f"距 target 2,460 還 {(2460/cur_2345-1)*100:.1f}%\n"
                    f"建議: trailing stop 提至 2,200（鎖利 +60/股）",
                    key="2345_strong_alert"
                )
                actions.append(("watch", f"2345 強勢 ({cur_2345:.0f})，trailing stop 2,200",
                                "target 2,460 / 動態 stop"))
            else:
                actions.append(("watch", "2345 智邦 OCO", "停利2460/停損1925"))

        # 009819 中信數據基建 跌破 9.0 警報
        if ticker_shares.get("009819", 0) > 0:
            cur_809 = get_price("009819")
            if 0 < cur_809 < 9.0:
                self._push_alert(
                    f"🔴 **009819 中信數據基建 跌破停損 9.0！**\n\n"
                    f"現價: {cur_809:.2f}\n"
                    f"成本: 12.54  虧損: {(cur_809/12.54-1)*100:.1f}%\n"
                    f"建議: 認賠出場（避免再 -10%）",
                    key="009819_stop_alert"
                )

        prio_text = {"must": "必做", "suggest": "建議", "watch": "觀察"}
        for tag, action, limit in actions:
            self.actions_tv.insert("", "end",
                                    values=(prio_text[tag], action, limit),
                                    tags=(tag,))

    def _show_crash_modal(self, regime: str, hedge_tilt: int, hedge_notes: list):
        """CRASH 級別警報 Modal — 半透明覆蓋，user 必須 acknowledge"""
        if getattr(self, "_crash_modal_shown", False):
            return  # 已顯示過，session 內不重複
        self._crash_modal_shown = True

        modal = tk.Toplevel(self)
        modal.title("⚠️ 市場警報")
        modal.configure(bg="#3a0d0d")  # 深紅
        modal.geometry("600x420")
        modal.transient(self)
        modal.grab_set()

        ttk.Label(modal,
                  text=f"🚨 {regime} MODE 觸發 🚨",
                  background="#3a0d0d", foreground="#ff6b6b",
                  font=(self.UI_FONT, 18, "bold"),
                  ).pack(pady=(20, 5))

        ttk.Label(modal,
                  text=f"當前市場狀態: {regime}",
                  background="#3a0d0d", foreground="#fff",
                  font=(self.UI_FONT, 12, "bold"),
                  ).pack(pady=4)

        if regime == "CRASH":
            body_text = (
                "歷史實證: CRASH 期 0050 fwd 20d +9.75% (100% win, n=34)\n"
                "建議行動: 部署現金 60% 加碼 0050；可加 00631L 槓桿 15%\n"
                "風險窗口: 等 VIX 從高點回落 30% 再大量加碼"
            )
        else:
            body_text = "\n".join(hedge_notes[:3])

        ttk.Label(modal,
                  text=body_text,
                  background="#3a0d0d", foreground="#ffd1d1",
                  font=(self.UI_FONT, 11), wraplength=540, justify="left",
                  ).pack(pady=10, padx=20)

        if hedge_tilt > 0:
            ttk.Label(modal,
                      text=f"建議 cash tilt: +{hedge_tilt}pp 超出 baseline",
                      background="#3a0d0d", foreground="#ffe0e0",
                      font=(self.UI_FONT, 11, "bold"),
                      ).pack(pady=4)

        ttk.Label(modal,
                  text="此警報僅在 session 內觸發一次。重啟 GUI 才會再次提醒。",
                  background="#3a0d0d", foreground="#888",
                  font=(self.UI_FONT, 9),
                  ).pack(pady=4)

        btn_frame = ttk.Frame(modal)
        btn_frame.pack(pady=20)
        ttk.Button(btn_frame, text="我知道了，進入 Dashboard",
                   command=modal.destroy).pack()

    def _check_crash_modal(self, regime, hedge_reading):
        """檢查是否該觸發 CRASH modal"""
        try:
            if regime is None or hedge_reading is None:
                return
            # Trigger CRASH modal if:
            # - regime == CRASH
            # - OR hedge tilt >= 20pp (multi-signal hedge)
            should_trigger = (
                regime.regime == "CRASH"
                or hedge_reading.cash_tilt_pp >= 20
            )
            if should_trigger:
                self._show_crash_modal(
                    regime.regime,
                    hedge_reading.cash_tilt_pp,
                    hedge_reading.notes,
                )
        except Exception as e:
            self._log(f"CRASH modal check: {e}")

    def _update_hero_action(self):
        """🎯 Hero Action Panel update — re-runs every 60s tick.
        因 hedge / barbell / regime 都會 refresh，hero panel 跟著最新值更新。
        """
        try:
            from src.report.action_advisor import generate_actions
            from src.report.regime_section import compute_current_regime
            from src.report.hedge_signals import compute_hedge_reading
            from src.report.barbell_allocation import (
                ALLOCATION_TABLE, _apply_hedge_tilt, _load_holdings,
            )
            regime_r = compute_current_regime()
            hedge_r = compute_hedge_reading()
            holdings = _load_holdings()
            # CRASH modal trigger (one-time per session)
            self._check_crash_modal(regime_r, hedge_r)
            if not (regime_r and holdings):
                self.hero_status.config(text="資料不足", foreground=COLORS["fg_dim"])
                return

            base_target = ALLOCATION_TABLE.get(regime_r.regime, {})
            target, _, _ = _apply_hedge_tilt(base_target)
            cash_total = holdings.cash_pct / 100 * holdings.total_value
            actions = generate_actions(
                regime_r, hedge_r, target, holdings,
                holdings.total_value, cash_total,
            )

            # Status line
            regime_color_map = {
                "CRASH": COLORS["red"],
                "BEAR": COLORS["yellow"],
                "SIDEWAYS": COLORS["fg_dim"],
                "BULL_TREND": COLORS["green"],
                "STRONG_BULL": COLORS["red"],
            }
            status_color = regime_color_map.get(regime_r.regime, COLORS["fg"])
            self.hero_status.config(
                text=f"`{regime_r.regime}` | TAIEX {regime_r.dist_ma200:+.1f}% MA200 | "
                     f"VIX {hedge_r.vix_current:.1f} | Hedge tilt: {hedge_r.cash_tilt_pp:+d}pp",
                foreground=status_color,
            )

            # Update action labels (top 5)
            priority_color = {
                "critical": COLORS["red"], "warning": COLORS["yellow"],
                "action": COLORS["fg"], "tweak": COLORS["fg_dim"],
                "hold": COLORS["green"], "info": COLORS["fg_dim"],
            }
            priority_marker = {
                "critical": "🚨", "warning": "⚠️", "action": "📌",
                "tweak": "🔧", "hold": "✅", "info": "ℹ️",
            }
            for i in range(5):
                if i < len(actions):
                    a = actions[i]
                    marker = priority_marker.get(a.priority, "•")
                    text = f"{i+1}. {marker} {a.icon} {a.label}"
                    if a.reason:
                        text += f"   _{a.reason[:60]}_"
                    self.hero_action_labels[i].config(
                        text=text,
                        foreground=priority_color.get(a.priority, COLORS["fg"]),
                    )
                else:
                    self.hero_action_labels[i].config(text="")

            # Cash bar
            today_budget = min(int(cash_total * 0.1), 30000)
            self.hero_cash.config(
                text=f"💰 現金 NT${cash_total:,.0f} ({holdings.cash_pct:.0f}%) | "
                     f"今日建議動用 ≤ NT${today_budget:,}",
            )
        except Exception as e:
            self.hero_status.config(text=f"hero action error: {e}",
                                     foreground=COLORS["red"])

    def _update_v2_regime(self):
        """V2 5-regime classifier update."""
        try:
            from src.report.regime_section import compute_current_regime
            reading = compute_current_regime()
            if reading is None:
                self.v2_regime_label.config(text="V2 regime: 資料不足")
                return
            color = {
                "CRASH": COLORS["red"],
                "BEAR": COLORS["yellow"],
                "SIDEWAYS": COLORS["fg_dim"],
                "BULL_TREND": COLORS["green"],
                "STRONG_BULL": COLORS["red"],  # mean reversion warning
            }.get(reading.regime, COLORS["fg"])
            self.v2_regime_label.config(
                text=f"`{reading.regime}` (TAIEX 距 MA200 {reading.dist_ma200:+.1f}%)",
                foreground=color,
            )
            self.v2_regime_metrics.config(
                text=f"vol30: {reading.vol_30d:.1f}%   60d ret: {reading.ret_60d:+.1f}%   "
                     f"歷史 fwd 20d: {reading.expected_fwd_20d}"
            )
            self.v2_regime_action.config(text=f"💡 {reading.recommendation}")
        except Exception as e:
            self.v2_regime_label.config(text=f"V2 regime error: {e}")

    def _update_hedge_signals(self):
        """5 hedge signals overlay update."""
        try:
            from src.report.hedge_signals import compute_hedge_reading
            r = compute_hedge_reading()
            tilt = r.cash_tilt_pp
            color = COLORS["red"] if tilt >= 15 else (COLORS["yellow"] if tilt > 0 else COLORS["green"])
            self.hedge_tilt_label.config(
                text=f"Cash tilt: {'+' + str(tilt) if tilt > 0 else '0'}pp 超出 baseline",
                foreground=color,
            )
            # Update each signal cell
            sig_data = [
                ("TX OI z", f"{r.foreign_tx_z:+.2f}", r.foreign_tx_signal),
                ("VIX", f"{r.vix_current:.1f}", r.vix_signal),
                ("VIX/VIX3M", f"{r.vix_ratio:.3f}", r.vix_ratio_signal),
                ("TX basis", f"z{r.tx_basis_z:+.2f}", r.tx_basis_extreme),
                ("SPY 隔夜", f"{r.spy_overnight_pct:+.2f}%", r.spy_gap_signal),
            ]
            for sig_name, val, active in sig_data:
                lbl = self.hedge_signal_labels.get(sig_name)
                if lbl:
                    lbl.config(
                        text=val,
                        foreground=COLORS["red"] if active else COLORS["green"],
                    )
        except Exception as e:
            self.hedge_tilt_label.config(text=f"Hedge error: {e}")

    def _update_barbell(self):
        """Barbell allocation target vs current update."""
        try:
            from src.report.barbell_allocation import (
                ALLOCATION_TABLE, BUCKET_LABELS, _apply_hedge_tilt, _load_holdings,
            )
            from src.report.regime_section import compute_current_regime
            reading = compute_current_regime()
            if reading is None:
                self.barbell_regime_label.config(text="Barbell: 資料不足")
                return
            base_target = ALLOCATION_TABLE.get(reading.regime, {})
            target, tilt, _ = _apply_hedge_tilt(base_target)
            current = _load_holdings()
            if current is None:
                self.barbell_regime_label.config(text="Barbell: assets.json 缺")
                return

            tilt_text = f" (+{tilt}pp hedge tilt)" if tilt > 0 else ""
            self.barbell_regime_label.config(
                text=f"Regime `{reading.regime}` → 目標配置{tilt_text}"
            )

            # Clear old rows beyond header
            for w in list(self.barbell_grid.winfo_children())[4:]:
                w.destroy()

            big_actions = []
            row = 1
            for key, label in BUCKET_LABELS:
                curr_pct = getattr(current, f"{key}_pct", 0)
                tgt = target.get(key, 0)
                d = tgt - curr_pct
                color = COLORS["red"] if d > 5 else (
                    COLORS["yellow"] if d < -5 else COLORS["green"])
                ttk.Label(self.barbell_grid, text=label, style="Card.TLabel",
                          foreground=COLORS["fg"]
                          ).grid(row=row, column=0, sticky="w", padx=(0, 12))
                ttk.Label(self.barbell_grid, text=f"{curr_pct:.0f}%", style="Card.TLabel",
                          foreground=COLORS["fg_dim"]
                          ).grid(row=row, column=1, sticky="w", padx=(0, 12))
                ttk.Label(self.barbell_grid, text=f"{tgt}%", style="Card.TLabel",
                          font=(self.UI_FONT, 10, "bold")
                          ).grid(row=row, column=2, sticky="w", padx=(0, 12))
                arrow = "↑" if d > 0 else ("↓" if d < 0 else "—")
                ttk.Label(self.barbell_grid, text=f"{d:+.0f}pp {arrow}",
                          style="Card.TLabel", foreground=color
                          ).grid(row=row, column=3, sticky="w")
                row += 1
                if abs(d) >= 5:
                    big_actions.append((label, curr_pct, tgt, d))

            big_actions.sort(key=lambda x: -abs(x[3]))
            top3 = big_actions[:3]
            if top3:
                action_text = " / ".join(
                    f"{'⬆️' if d > 0 else '⬇️'} {label.split('(')[0].strip()} {abs(d):.0f}pp"
                    for label, _, _, d in top3
                )
                self.barbell_actions_label.config(
                    text=f"建議動作: {action_text}",
                    foreground=COLORS["yellow"],
                )
            else:
                self.barbell_actions_label.config(
                    text="✅ 配置已達標 (no major delta)",
                    foreground=COLORS["green"],
                )
        except Exception as e:
            self.barbell_regime_label.config(text=f"Barbell error: {e}")

    def _update_regime_status(self):
        try:
            from src.risk.strategy_regime_gate import detect_current_regime, evaluate_strategies
            r = detect_current_regime()
            result = evaluate_strategies(r)

            # Trend color
            color = COLORS["green"] if r.trend == "bull" else (
                COLORS["red"] if r.trend == "bear" else COLORS["yellow"])
            cycle_color = COLORS["red"] if r.cycle == "late_bull" else color
            pct = (r.taiex_close / r.ma200 - 1) * 100

            self.regime_label.config(
                text=f"🟢 {r.trend.upper()} / {r.cycle.upper()} (距 MA200 {pct:+.1f}%) "
                      f"| Vol {r.vol_state} | VIX {r.vix:.1f}",
                foreground=cycle_color
            )
            active_names = [s["rule"].name.split()[0] for s in result["active"]]
            self.regime_active.config(
                text=f"✅ 啟用 ({len(result['active'])}): {', '.join(active_names)}"
            )
            sus_names = [s["rule"].name.split()[0] for s in result["suspended"]]
            self.regime_suspended.config(
                text=f"⏸ 暫停 ({len(result['suspended'])}): {', '.join(sus_names)}"
                if sus_names else "⏸ 無暫停策略"
            )
        except Exception as e:
            self.regime_label.config(text=f"regime error: {e}")

    def _update_dca_timing(self, now: datetime):
        """根據今天日期判斷 DCA 進場 anomaly。"""
        from calendar import monthrange
        d = now.date()
        month = d.month
        dom = d.day
        days_in_m = monthrange(d.year, d.month)[1]
        days_to_end = days_in_m - dom

        # 春節日期
        cny_dates = {
            2025: pd.Timestamp("2025-01-29").date(),
            2026: pd.Timestamp("2026-02-17").date(),
            2027: pd.Timestamp("2027-02-06").date(),
        }
        cny = cny_dates.get(d.year)
        days_to_cny = (d - cny).days if cny else None

        score = 0
        reasons = []

        if days_to_cny is not None and -7 <= days_to_cny < 0:
            score += 3
            reasons.append(f"🟢 春節前 {-days_to_cny} 日 (+0.58%/天)")
        elif days_to_cny is not None and 0 < days_to_cny <= 10:
            score += 3
            reasons.append(f"🟢 春節後 {days_to_cny} 日 (+0.40%/天)")

        if month in [1, 4, 7, 10] and dom <= 5:
            score += 2
            reasons.append(f"🟢 季初前 {dom} 日 (+0.21%/天)")

        if month in [3, 6, 9, 12] and days_to_end == 0:
            score -= 4
            reasons.append("🔴 季底最後 1 日 (-0.44%/天)")
        elif month in [3, 6, 9, 12] and days_to_end <= 4:
            score -= 1
            reasons.append(f"🟡 季底前 {days_to_end+1} 日")

        if days_to_end == 0:
            score -= 1
            reasons.append("🔴 月底最後 1 日 (-0.12%/天)")

        if 8 <= dom <= 22 and not reasons:
            score += 1
            reasons.append("⚪ 月中 (+0.10%/天 baseline)")

        if score >= 3:
            grade = "🟢 大幅加碼日 (+++)"
            color = COLORS["green"]
        elif score >= 1:
            grade = "🟢 適合進場 (+)"
            color = COLORS["green"]
        elif score == 0:
            grade = "⚪ 中性"
            color = COLORS["fg_dim"]
        elif score >= -2:
            grade = "🟡 不太建議"
            color = COLORS["yellow"]
        else:
            grade = "🔴 強烈避開"
            color = COLORS["red"]

        if not reasons:
            reasons.append("⚪ 無特殊 anomaly")

        self.dca_timing_label.config(text=grade, foreground=color)
        self.dca_timing_detail.config(text="  ".join(reasons))

    def _update_overnight(self):
        """夜盤訊號：抓 TSM/NVDA/SOXX/SPY/VIX 最新收盤，估算對台股 gap 影響。"""
        self.overnight_tv.delete(*self.overnight_tv.get_children())
        symbols = [
            ("TSM",  "TSMC ADR", 0.69),
            ("SOXX", "SOX 半導體 ETF", 0.71),
            ("NVDA", "NVIDIA", 0.50),
            ("SPY",  "S&P 500", 0.64),
            ("^VIX", "恐慌指數",  -0.44),
        ]

        try:
            import yfinance as yf
            for sym, name, beta in symbols:
                try:
                    t = yf.Ticker(sym)
                    h = t.history(period="2d", auto_adjust=False)
                    if h.empty or len(h) < 2:
                        continue
                    last_close = float(h["Close"].iloc[-1])
                    prev_close = float(h["Close"].iloc[-2])
                    change = (last_close / prev_close - 1) * 100

                    # 估算 implied gap：beta × US change
                    implied = beta * change
                    if abs(change) < 0.3:
                        implied_text = "≈ 平盤"
                        tag = "neutral"
                    elif implied > 0.5:
                        implied_text = f"預期 {implied:+.2f}% 跳空高 🟢"
                        tag = "bullish"
                    elif implied < -0.5:
                        implied_text = f"預期 {implied:+.2f}% 跳空低 🔴"
                        tag = "bearish"
                    else:
                        implied_text = f"{implied:+.2f}% 微幅"
                        tag = "neutral"

                    self.overnight_tv.insert(
                        "", "end",
                        values=(sym, name, f"{last_close:.2f}",
                                f"{change:+.2f}%", implied_text),
                        tags=(tag,)
                    )
                except Exception:
                    continue
        except Exception as e:
            self._log(f"overnight error: {e}")

    def _update_institutional_signal(self):
        """讀法人 cache 計算當下連續買超天數。"""
        self.inst_signal_tv.delete(*self.inst_signal_tv.get_children())
        cache_dir = ROOT / "data" / "cache" / "finmind" / "institutional"
        for tk_, info in INSTITUTIONAL_SIGNALS.items():
            cp = cache_dir / f"{tk_}.parquet"
            if not cp.exists():
                self.inst_signal_tv.insert(
                    "", "end",
                    values=(tk_, info["name"], info["investor"][:6], "—", "無資料", info["alpha"]),
                    tags=("none",))
                continue
            try:
                import pandas as pd
                df = pd.read_parquet(cp)
                df["date"] = pd.to_datetime(df["date"]).dt.date
                # filter by investor
                inv_df = df[df["name"] == info["investor"]].sort_values("date")
                if inv_df.empty:
                    continue
                # 計算連續買超天數（從最後一筆往前數）
                inv_df = inv_df.copy()
                inv_df["is_buy"] = inv_df["net_buy"] > 0
                # reverse iterate
                consec = 0
                for is_buy in reversed(inv_df["is_buy"].tolist()):
                    if is_buy:
                        consec += 1
                    else:
                        break
                # 訊號等級
                if consec >= info["n_consec_strong"]:
                    status = f"🟢 強訊號 ({consec}天)"
                    tag = "strong"
                elif consec >= info["n_consec_weak"]:
                    status = f"🟡 弱訊號 ({consec}天)"
                    tag = "weak"
                else:
                    status = f"⚪ {consec} 天"
                    tag = "none"

                consec_text = f"S≥{info['n_consec_strong']}/W≥{info['n_consec_weak']}"
                self.inst_signal_tv.insert(
                    "", "end",
                    values=(tk_, info["name"], info["investor"][:6],
                            consec_text, status, info["alpha"]),
                    tags=(tag,))
            except Exception as e:
                self._log(f"inst signal {tk_}: {e}")

    def _update_short_watchlist(self, now: datetime):
        """退勢空 watchlist 今日狀態（簡單版，依時段）。"""
        self.short_tv.delete(*self.short_tv.get_children())
        is_trading = now.weekday() < 5
        t = now.time()

        if not is_trading:
            today_status = "🟫 假日"
            tag = "idle"
        elif t < dt_time(9, 5):
            today_status = "⏳ 等待開盤"
            tag = "watching"
        elif t < dt_time(11, 30):
            today_status = "🔍 監控中"
            tag = "hot"
        elif t < dt_time(13, 30):
            today_status = "⚪ 已過進場時段"
            tag = "idle"
        else:
            today_status = "⚫ 收盤"
            tag = "idle"

        for tk_, info in SHORT_WATCHLIST.items():
            rule = (f"漲≥{info['pump']:.1f}% 量≥{info['vol']}% "
                    f"回{info['retreat']:.1f}% tp={info['tp']:.1f}%")
            self.short_tv.insert(
                "", "end",
                values=(tk_, info["name"], rule, info["stats"], today_status),
                tags=(tag,)
            )

    def _update_recent_trades(self):
        try:
            from src.portfolio.transaction_log import load_transactions
            txs = load_transactions(ROOT)[-8:]  # 最後 8 筆
            self.trades_tv.delete(*self.trades_tv.get_children())
            for t in reversed(txs):
                action = t.get("action", "")
                action_text = "買" if action == "buy" else "賣"
                pnl = t.get("realized_pnl", 0)
                if action == "sell":
                    pnl_text = f"{pnl:+,.0f}"
                    tag = "sell_win" if pnl > 0 else "sell_loss"
                else:
                    pnl_text = "—"
                    tag = "buy"
                tk_ = t.get("ticker", "")
                name = lookup_name(tk_) if tk_ else ""
                self.trades_tv.insert(
                    "", "end",
                    values=(t.get("date", ""), action_text, tk_, name,
                            f"{t.get('shares', 0):,}",
                            f"{t.get('price', 0):.2f}", pnl_text),
                    tags=(tag,)
                )
        except Exception as e:
            self._log(f"trades update error: {e}")

    def _load_assets(self) -> dict:
        if not ASSETS_JSON.exists():
            return {"cash": 0, "holdings": {}}
        try:
            return json.loads(ASSETS_JSON.read_text(encoding="utf-8"))
        except Exception:
            return {"cash": 0, "holdings": {}}

    # ── Event log ──
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.config(state="normal")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _poll_events(self):
        try:
            while True:
                msg = self.event_queue.get_nowait()
                self._log(msg)
        except Empty:
            pass
        self.after(500, self._poll_events)

    # ── Auto-trigger ──
    def _check_auto_triggers(self):
        """每 5 秒檢查時間，到點自動跑 morning / close。"""
        try:
            now = datetime.now()
            today = now.date()

            # 每天重置 flags
            if self.last_auto_check_date != today:
                self.auto_done = {"briefing": False, "morning": False, "close": False,
                          "ledger_scan": False, "health_check": False}
                self.last_auto_check_date = today

            if self.auto_enabled and now.weekday() < 5:
                # 08:00 morning briefing
                if not self.auto_done["briefing"] and now.time() >= dt_time(8, 0):
                    if now.time() < dt_time(13, 0):
                        self._log("🔔 自動觸發晨報生成 (08:00)")
                        self.run_briefing()
                    self.auto_done["briefing"] = True

                # 09:20 morning
                if not self.auto_done["morning"] and now.time() >= dt_time(9, 20):
                    if now.time() < dt_time(13, 0):
                        self._log("🔔 自動觸發 morning ORB 偵測 (09:20)")
                        self.run_orb("morning")
                    self.auto_done["morning"] = True

                # 13:25 close
                if not self.auto_done["close"] and now.time() >= dt_time(13, 25):
                    if now.time() < dt_time(15, 0):
                        self._log("🔔 自動觸發 close 平倉 (13:25)")
                        self.run_orb("close")
                    self.auto_done["close"] = True

                # 14:00 unified ledger scan (掃配對 / 0050 dealer 訊號)
                if not self.auto_done["ledger_scan"] and now.time() >= dt_time(14, 0):
                    if now.time() < dt_time(15, 30):
                        self._log("🔔 自動觸發 unified ledger 掃描 (14:00)")
                        self.run_ledger_scan()
                    self.auto_done["ledger_scan"] = True

                # 每月 1 號 14:30 health check
                if (not self.auto_done["health_check"] and now.day == 1
                        and now.time() >= dt_time(14, 30)):
                    if now.time() < dt_time(15, 30):
                        self._log("🔔 自動觸發每月 health check (1 號 14:30)")
                        self.run_health_check()
                    self.auto_done["health_check"] = True
        except Exception as e:
            self._log(f"auto trigger error: {e}")

        self.after(5_000, self._check_auto_triggers)

    def toggle_auto(self):
        self.auto_enabled = not self.auto_enabled
        if self.auto_enabled:
            self.auto_btn.config(text="⏸ 暫停自動")
            self.daemon_status_label.config(
                text="🟢 自動偵測中 (09:20 / 13:25)",
                foreground=COLORS["green"])
            self._log("✅ 自動排程已啟用")
        else:
            self.auto_btn.config(text="▶ 恢復自動")
            self.daemon_status_label.config(
                text="🔴 自動已暫停",
                foreground=COLORS["red"])
            self._log("⏸ 自動排程已暫停")

    def run_ledger_scan(self):
        """跑 unified_paper_ledger 掃配對 / 0050 dealer 訊號。"""
        script = ROOT / "scripts" / "unified_paper_ledger.py"
        if not script.exists():
            self._log(f"❌ {script} 不存在")
            return
        self._log("執行 unified_paper_ledger.py...")

        def worker():
            try:
                result = subprocess.run(
                    [PYTHON, str(script)],
                    cwd=str(ROOT), capture_output=True, text=True, timeout=120,
                    encoding="utf-8", errors="replace",
                )
                stdout = result.stdout or ""
                # 只取重要訊息
                for line in stdout.strip().split("\n")[-15:]:
                    if any(k in line for k in ["✅", "❌", "觸發", "平倉", "策略"]):
                        self.event_queue.put(line.strip())
                self.event_queue.put("✅ ledger 掃描完成")
            except Exception as e:
                self.event_queue.put(f"❌ ledger error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def run_health_check(self):
        """跑 monthly health check。"""
        script = ROOT / "scripts" / "monthly_health_check.py"
        if not script.exists():
            return
        self._log("執行 monthly health check...")

        def worker():
            try:
                subprocess.run([PYTHON, str(script)], cwd=str(ROOT),
                               timeout=120, encoding="utf-8", errors="replace")
                self.event_queue.put("✅ Health check 完成 + Discord 已推")
            except Exception as e:
                self.event_queue.put(f"❌ health error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def run_briefing(self):
        """跑 morning_briefing.py 產生晨報 + 推 Discord。"""
        script = ROOT / "scripts" / "morning_briefing.py"
        if not script.exists():
            self._log(f"❌ 找不到 {script}")
            return
        self._log("執行 morning_briefing.py（可能需 30s-2 min）...")

        def worker():
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                result = subprocess.run(
                    [PYTHON, str(script)],
                    cwd=str(ROOT), capture_output=True, text=True, timeout=300,
                    encoding="utf-8", errors="replace", env=env,
                )
                # 只取重要訊息（避免 log 太長）
                stdout = result.stdout or ""
                tail_lines = stdout.strip().split("\n")[-10:]
                for line in tail_lines:
                    if line.strip() and any(k in line for k in
                            ["晨報", "Discord", "Phase", "✅", "❌", "報告", "錯誤"]):
                        self.event_queue.put(line.strip())
                if result.returncode == 0:
                    self.event_queue.put("✅ 晨報完成 + Discord 已推")
                else:
                    self.event_queue.put(f"⚠️ briefing exit {result.returncode}")
            except subprocess.TimeoutExpired:
                self.event_queue.put("❌ 晨報超時 (>5 min)")
            except Exception as e:
                self.event_queue.put(f"❌ briefing error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def run_orb(self, mode: str):
        script = ROOT / "scripts" / "orb_paper_trade.py"
        if not script.exists():
            messagebox.showerror("Error", f"找不到 {script}")
            return
        self._log(f"執行 ORB --mode {mode}...")

        def worker():
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                result = subprocess.run(
                    [PYTHON, str(script), "--mode", mode],
                    cwd=str(ROOT), capture_output=True, text=True, timeout=120,
                    encoding="utf-8", errors="replace", env=env,
                )
                for line in (result.stdout or "").splitlines():
                    if line.strip():
                        self.event_queue.put(line.strip())
                if result.returncode != 0:
                    self.event_queue.put(f"⚠️ exit {result.returncode}")
                self.event_queue.put(f"✅ {mode} 完成")
            except Exception as e:
                self.event_queue.put(f"❌ error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def open_logs(self):
        try:
            os.startfile(LOG_DIR)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def force_refresh_quotes(self):
        """強制重抓 yfinance 行情快取（持股 + 主要 ETF），跑完自動 refresh GUI。"""
        script = ROOT / "scripts" / "refresh_quotes.py"
        if not script.exists():
            messagebox.showerror("Error", f"找不到 {script}")
            return
        self._log("🌐 重抓行情中（30 秒）...")

        def worker():
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                result = subprocess.run(
                    [PYTHON, str(script)],
                    cwd=str(ROOT), capture_output=True, text=True, timeout=120,
                    encoding="utf-8", errors="replace", env=env,
                )
                for line in (result.stdout or "").splitlines():
                    if line.strip().startswith(("✅", "❌", "===")):
                        self.event_queue.put(line.strip())
                if result.returncode == 0:
                    self.event_queue.put("✅ 行情重抓完成 → 自動 refresh GUI")
                    self.after(500, self.refresh)
                else:
                    self.event_queue.put(f"⚠️ refresh exit {result.returncode}")
            except Exception as e:
                self.event_queue.put(f"❌ refresh quotes error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def open_today_briefing(self):
        """在 GUI 內開預覽視窗看今日晨報 markdown（可滾動 + 搜尋）。"""
        today = datetime.now().date().isoformat()
        path = LOG_DIR / f"{today}.md"
        if not path.exists():
            mds = sorted(LOG_DIR.glob("20*-*.md"), reverse=True)
            if not mds:
                messagebox.showinfo("晨報", "尚無晨報檔，請先按「📰 立即晨報」")
                return
            path = mds[0]
            self._log(f"今日晨報未產，開啟最新 {path.name}")

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Error", f"無法讀取 {path}:\n{e}")
            return

        win = tk.Toplevel(self)
        win.title(f"📄 晨報預覽 — {path.name}")
        win.geometry("960x800")
        win.configure(bg=COLORS["bg"])

        # ── 上方搜尋列 ──
        toolbar = ttk.Frame(win, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text=f"📄 {path.name}",
                  font=(self.UI_FONT, 12, "bold")).pack(side="left")
        ttk.Label(toolbar, text="  搜尋：").pack(side="left", padx=(20, 2))
        search_var = tk.StringVar()
        search_entry = ttk.Entry(toolbar, textvariable=search_var, width=24)
        search_entry.pack(side="left")

        def open_external():
            try:
                os.startfile(path)
            except Exception as e:
                messagebox.showerror("Error", str(e))

        ttk.Button(toolbar, text="🔄 重載",
                   command=lambda: _reload()).pack(side="right", padx=2)
        ttk.Button(toolbar, text="🔗 外部開啟",
                   command=open_external).pack(side="right", padx=2)

        # ── 文字區（含 scrollbar）──
        text_frame = ttk.Frame(win)
        text_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        scroll_y = ttk.Scrollbar(text_frame, orient="vertical")
        scroll_y.pack(side="right", fill="y")
        text = tk.Text(
            text_frame, wrap="word", yscrollcommand=scroll_y.set,
            font=(self.MONO_FONT, 12),
            bg=COLORS.get("panel_bg", "#1e1e1e"),
            fg=COLORS.get("fg", "#e0e0e0"),
            insertbackground=COLORS.get("fg", "#e0e0e0"),
            padx=12, pady=12, borderwidth=0,
        )
        text.pack(side="left", fill="both", expand=True)
        scroll_y.config(command=text.yview)

        # ── tag 高亮 markdown headings ──
        text.tag_configure("h2", foreground="#4ec9b0",
                           font=(self.UI_FONT, 16, "bold"), spacing1=10)
        text.tag_configure("h3", foreground="#dcdcaa",
                           font=(self.UI_FONT, 13, "bold"), spacing1=6)
        text.tag_configure("bold", font=(self.MONO_FONT, 12, "bold"))
        text.tag_configure("alert", foreground="#f48771")
        text.tag_configure("good", foreground="#73c991")
        text.tag_configure("warn", foreground="#dcdcaa")
        text.tag_configure("hit", background="#4d4d00", foreground="#ffffff")

        def render_content(c):
            text.config(state="normal")
            text.delete("1.0", "end")
            for line in c.splitlines():
                start = text.index("end-1c")
                text.insert("end", line + "\n")
                end = text.index("end-1c")
                if line.startswith("## "):
                    text.tag_add("h2", start, end)
                elif line.startswith("### "):
                    text.tag_add("h3", start, end)
                elif "🚨" in line or "❌" in line or "嚴重" in line:
                    text.tag_add("alert", start, end)
                elif "✅" in line or "🟢" in line:
                    text.tag_add("good", start, end)
                elif "⚠️" in line or "🟡" in line or "🟠" in line:
                    text.tag_add("warn", start, end)
            text.config(state="disabled")

        render_content(content)

        # ── search ──
        def do_search(*_):
            text.tag_remove("hit", "1.0", "end")
            kw = search_var.get().strip()
            if not kw: return
            idx = "1.0"
            while True:
                idx = text.search(kw, idx, stopindex="end", nocase=True)
                if not idx: break
                end_idx = f"{idx}+{len(kw)}c"
                text.tag_add("hit", idx, end_idx)
                idx = end_idx
            # 跳到第一個 hit
            first = text.search(kw, "1.0", stopindex="end", nocase=True)
            if first:
                text.see(first)

        def _reload():
            try:
                new_c = path.read_text(encoding="utf-8")
                render_content(new_c)
                do_search()
            except Exception as e:
                messagebox.showerror("Error", str(e))

        search_entry.bind("<Return>", do_search)
        search_var.trace_add("write", lambda *_: do_search())

    def open_transaction_dialog(self):
        """跳出交易記錄對話框 — 自動試算 + 寫入 assets。"""
        from src.portfolio.transaction_log import compute_transaction, record_transaction

        dlg = tk.Toplevel(self)
        dlg.title("➕ 記錄交易")
        dlg.geometry("520x540")
        dlg.configure(bg=COLORS["bg"])
        dlg.transient(self)
        dlg.grab_set()

        # ── 輸入區 ──
        frm = ttk.Frame(dlg, padding=20, style="Card.TFrame")
        frm.pack(fill="both", expand=True, padx=15, pady=15)

        # 動作
        action_var = tk.StringVar(value="buy")
        row1 = ttk.Frame(frm, style="Card.TFrame")
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="動作:", style="Card.TLabel", width=10).pack(side="left")
        for txt, val, color in [("買進", "buy", COLORS["green"]),
                                  ("賣出", "sell", COLORS["red"])]:
            ttk.Radiobutton(row1, text=txt, value=val, variable=action_var,
                            command=lambda: update_calc()).pack(side="left", padx=5)

        # 代號
        row2 = ttk.Frame(frm, style="Card.TFrame")
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="代號:", style="Card.TLabel", width=10).pack(side="left")
        ticker_var = tk.StringVar()
        ticker_entry = ttk.Entry(row2, textvariable=ticker_var, width=15,
                                  font=(self.MONO_FONT, 12))
        ticker_entry.pack(side="left", padx=5)

        # 從現有持股快速選
        ttk.Label(row2, text="或選持股:", style="Card.TLabel",
                  foreground=COLORS["fg_dim"]).pack(side="left", padx=(15, 5))
        try:
            data = self._load_assets()
            holdings_list = [str(h.get("ticker", "")) for h in
                             (data.get("holdings", {}).get("long_term", []) +
                              data.get("holdings", {}).get("short_term", []))]
        except Exception:
            holdings_list = []
        holding_combo = ttk.Combobox(row2, values=holdings_list, width=10, state="readonly")
        holding_combo.pack(side="left")

        def on_holding_select(*_):
            if holding_combo.get():
                ticker_var.set(holding_combo.get())
                update_calc()
        holding_combo.bind("<<ComboboxSelected>>", on_holding_select)

        # 股數
        row3 = ttk.Frame(frm, style="Card.TFrame")
        row3.pack(fill="x", pady=4)
        ttk.Label(row3, text="股數:", style="Card.TLabel", width=10).pack(side="left")
        shares_var = tk.StringVar()
        ttk.Entry(row3, textvariable=shares_var, width=15,
                  font=(self.MONO_FONT, 12)).pack(side="left", padx=5)
        ttk.Label(row3, text="(整股，1 張 = 1000 股)", style="Card.TLabel",
                  foreground=COLORS["fg_dim"]).pack(side="left")

        # 價格
        row4 = ttk.Frame(frm, style="Card.TFrame")
        row4.pack(fill="x", pady=4)
        ttk.Label(row4, text="價格:", style="Card.TLabel", width=10).pack(side="left")
        price_var = tk.StringVar()
        ttk.Entry(row4, textvariable=price_var, width=15,
                  font=(self.MONO_FONT, 12)).pack(side="left", padx=5)
        ttk.Label(row4, text="NT$", style="Card.TLabel",
                  foreground=COLORS["fg_dim"]).pack(side="left")

        # 日期
        row5 = ttk.Frame(frm, style="Card.TFrame")
        row5.pack(fill="x", pady=4)
        ttk.Label(row5, text="日期:", style="Card.TLabel", width=10).pack(side="left")
        date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(row5, textvariable=date_var, width=15,
                  font=(self.MONO_FONT, 12)).pack(side="left", padx=5)

        # 當沖
        row6 = ttk.Frame(frm, style="Card.TFrame")
        row6.pack(fill="x", pady=4)
        ttk.Label(row6, text="", style="Card.TLabel", width=10).pack(side="left")
        daytrade_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row6, text="當沖（證交稅減半）",
                        variable=daytrade_var,
                        command=lambda: update_calc()).pack(side="left", padx=5)

        # 備註
        row7 = ttk.Frame(frm, style="Card.TFrame")
        row7.pack(fill="x", pady=4)
        ttk.Label(row7, text="備註:", style="Card.TLabel", width=10).pack(side="left")
        note_var = tk.StringVar()
        ttk.Entry(row7, textvariable=note_var, width=30,
                  font=(self.UI_FONT, 11)).pack(side="left", padx=5)

        # ── 試算區 ──
        ttk.Label(frm, text="── 試算 ──", style="Section.TLabel").pack(pady=(15, 5))

        calc_frame = ttk.Frame(frm, style="Card.TFrame")
        calc_frame.pack(fill="x")
        calc_labels = {}
        for key in ["價金", "手續費(全)", "手續費月退", "證交稅", "應收付", "月退後最終"]:
            r = ttk.Frame(calc_frame, style="Card.TFrame")
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=f"{key}:", style="Card.TLabel", width=12,
                      foreground=COLORS["fg_dim"]).pack(side="left")
            v = ttk.Label(r, text="—", style="Card.TLabel",
                          font=(self.MONO_FONT, 12, "bold"))
            v.pack(side="left")
            calc_labels[key] = v

        def update_calc(*_):
            try:
                tk_ = ticker_var.get().strip()
                sh = int(shares_var.get() or 0)
                pr = float(price_var.get() or 0)
                if sh <= 0 or pr <= 0 or not tk_:
                    for v in calc_labels.values():
                        v.config(text="—", foreground=COLORS["fg_dim"])
                    return
                r = compute_transaction(action_var.get(), tk_, sh, pr,
                                         is_day_trade=daytrade_var.get())
                calc_labels["價金"].config(text=f"{r.gross:,.0f}",
                                            foreground=COLORS["fg"])
                calc_labels["手續費(全)"].config(text=f"{r.fee_immediate:,.2f}",
                                                  foreground=COLORS["red"])
                calc_labels["手續費月退"].config(text=f"+{r.fee_rebate:,.2f}",
                                                  foreground=COLORS["green"])
                calc_labels["證交稅"].config(
                    text=f"{r.tax:,.2f}" if r.tax else "—",
                    foreground=COLORS["red"] if r.tax else COLORS["fg_dim"])
                color = COLORS["green"] if r.net_cash_immediate >= 0 else COLORS["red"]
                sign = "+" if r.net_cash_immediate >= 0 else ""
                calc_labels["應收付"].config(text=f"{sign}{r.net_cash_immediate:,.0f}",
                                              foreground=color)
                color = COLORS["green"] if r.net_cash_final >= 0 else COLORS["red"]
                sign = "+" if r.net_cash_final >= 0 else ""
                calc_labels["月退後最終"].config(text=f"{sign}{r.net_cash_final:,.0f}",
                                                   foreground=color)
            except (ValueError, TypeError):
                for v in calc_labels.values():
                    v.config(text="—", foreground=COLORS["fg_dim"])

        ticker_var.trace_add("write", update_calc)
        shares_var.trace_add("write", update_calc)
        price_var.trace_add("write", update_calc)

        # ── Buttons ──
        btn_row = ttk.Frame(frm, style="Card.TFrame")
        btn_row.pack(fill="x", pady=15)

        def do_submit():
            try:
                tk_ = ticker_var.get().strip()
                sh = int(shares_var.get())
                pr = float(price_var.get())
                d = date.fromisoformat(date_var.get())
                if not tk_:
                    raise ValueError("代號不能空白")
                if sh <= 0 or pr <= 0:
                    raise ValueError("股數和價格必須 > 0")
                ret = record_transaction(
                    ROOT, action_var.get(), tk_, sh, pr,
                    trade_date=d, is_day_trade=daytrade_var.get(),
                    note=note_var.get(),
                )
                action_text = "買進" if action_var.get() == "buy" else "賣出"
                msg = (f"✅ {action_text} {tk_} {sh:,} 股 @ {pr}\n"
                        f"應收付: {ret['result'].net_cash_immediate:+,.0f}")
                if action_var.get() == "sell":
                    msg += f"\n已實現損益: {ret['realized_pnl']:+,.0f}"
                self._log(msg)
                self.refresh()
                dlg.destroy()
            except Exception as e:
                messagebox.showerror("錯誤", str(e))

        ttk.Button(btn_row, text="✅ 確認記錄",
                   command=do_submit, style="Accent.TButton").pack(side="left", padx=5)
        ttk.Button(btn_row, text="取消",
                   command=dlg.destroy).pack(side="left", padx=5)

    def show_help(self):
        """彈出使用說明視窗。"""
        help_win = tk.Toplevel(self)
        help_win.title("INVEST 儀表板 — 使用說明")
        help_win.geometry("780x680")
        help_win.configure(bg=COLORS["bg"])

        text = scrolledtext.ScrolledText(
            help_win, bg=COLORS["bg2"], fg=COLORS["fg"],
            insertbackground=COLORS["fg"], borderwidth=0, padx=20, pady=15,
            font=(self.UI_FONT, 11), wrap="word",
        )
        text.pack(fill="both", expand=True)

        content = """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  INVEST 儀表板 使用說明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

▸ GUI 開著就自動排程 ◂

雙擊 bat 開 GUI 後：
  ✓ 09:20 自動偵測 ORB（不用按任何按鈕）
  ✓ 13:25 自動平倉
  ✓ Discord 推播訊號 / 平倉結果
  ✓ 每 60 秒 refresh 持股 / ORB 狀態
  ✓ 視窗關掉 = 排程停止

▸ 按鈕功能 ◂

【⏸ 暫停自動 / ▶ 恢復自動】
   切換自動排程開關
   想暫停 daemon 不用關 GUI

【⏵ 手動 morning】（測試用）
   立刻跑 ORB 偵測，不等 09:20
   開盤前按沒意義（minute K 還沒形成）

【⏵ 手動 close】（測試用）
   立刻跑平倉，不等 13:25
   13:20 後想手動觸發

【🔄 重新整理】
   立刻 refresh 儀表板
   自動每 60 秒 refresh，按這個跳過等待

【📁 開啟 logs】
   檔案總管打開 logs/ 資料夾

【📁 開啟專案】
   檔案總管打開 INVEST 根目錄

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

▸ 標準使用流程（每天 1 分鐘）◂

【早上 08:30 起床】
1. 雙擊「INVEST 儀表板.bat」
2. 看「下一個動作」清單
   • 紅色「必做」= 優先做
   • 黃色「建議」= 可做可不做
   • 藍色「觀察」= 提醒，不急著做
3. （可選）依清單到永豐 e-Leader 下單
4. 視窗 minimize 放著

【上班/上學時】
- 視窗不用管，自動排程跑
- 09:20 / 13:25 自動觸發 + Discord 推播

【中午 12:30 休息（可選）】
- 切回 GUI 看一眼

【收盤後 13:30】
- GUI「ORB 訊號」區顯示最終結果
  ✅ 已平倉 +X% (綠)
  ❌ 已平倉 -X% (紅)
  ⚪ 未觸發 (今日無訊號)
- 視窗可以關了

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

▸ 不該做的事 ◂

✗ 09:20 前手動 morning
   → minute K 還沒形成，必定無訊號

✗ 看到 ORB 訊號就真的下單
   → 目前 paper trade only，累積 10+ 筆再實盤

✗ 13:30 前關 GUI 視窗
   → 會中斷自動排程（minimize 即可，不要關）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

▸ 5 個面板說明 ◂

【💰 累計成績】
   淨資產 / 持股市值 / 未實現損益 / 現金

【💼 持股】
   每檔代號、名稱、股數、成本、現價、損益

【🎯 ORB 訊號】（paper trade 狀態）
   2408 / 2485 今日訊號狀態
   ⏳ 等待 / 🟢 已觸發 / ✅ 已平倉 / ⚪ 未觸發

【📈 DCA 進度】
   5 個 ETF 進度條（0050 / 00881 / 00947 / 00646 / EWY）
   下一批應該買多少 + 限價區間

【🔔 下一個動作】
   優先級分三色：必做 / 建議 / 觀察
   從計畫書 + 持股狀態自動產生

【📋 事件 Log】
   按下按鈕跑 ORB 後即時顯示輸出
   ORB 偵測 / 平倉結果第一時間看到

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

▸ 進階：未來能做什麼 ◂

目前：paper trade only（不下實單）
   → Discord 推播訊號，你手動到永豐下單

Shioaji 開戶通過（1-2 週後）：
   → GUI 可加「一鍵真實下單」按鈕
   → 自動串 Shioaji API 下單
"""
        text.insert("1.0", content)
        text.config(state="disabled")

        ttk.Button(help_win, text="關閉", command=help_win.destroy).pack(pady=10)

    def _refresh_interval(self) -> int:
        """盤中 30 秒、盤外 5 分鐘（智慧切換省 API）。"""
        now = datetime.now()
        if now.weekday() >= 5:
            return 600_000  # 假日 10 分
        t = now.time()
        if dt_time(9, 0) <= t <= dt_time(13, 30):
            return 30_000   # 盤中 30 秒
        return 300_000      # 盤外 5 分鐘

    def _auto_refresh(self):
        self.refresh()
        self.after(self._refresh_interval(), self._auto_refresh)


def main():
    app = Dashboard()
    app.mainloop()


if __name__ == "__main__":
    main()
