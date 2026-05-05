"""
晨報補充 sections — ORB 訊號 / 法人訊號 / DCA 進度。

用法：在 morning_briefing.py 主流程中呼叫 render_strategy_section()，
回傳 markdown 字串 append 到報告尾端。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd


# 配置（與 dashboard_gui.py 同步）
ORB_WHITELIST = {
    "2408": {"name": "南亞科", "entry": "09:15", "vol": "30%", "ref": "open5"},
    "2485": {"name": "兆赫",   "entry": "09:45", "vol": "30%", "ref": "open15"},
}

INSTITUTIONAL_SIGNALS = {
    "0050":   {"name": "元大台灣50", "investor": "Dealer_self",
               "n_consec_strong": 5, "n_consec_weak": 3,
               "alpha": "真 alpha +3.81% / 20d (sigma 6.98)"},
    "006208": {"name": "富邦台50", "investor": "Foreign_Investor",
               "n_consec_strong": 5, "n_consec_weak": 3,
               "alpha": "真 alpha +4.15% / 20d (sigma 8.15)"},
    "00881":  {"name": "國泰台灣5G+", "investor": "Foreign_Investor",
               "n_consec_strong": 5, "n_consec_weak": 3,
               "alpha": "真 alpha +2.99% / 20d (sigma 4.40)"},
    "2308":   {"name": "台達電", "investor": "Foreign_Investor",
               "n_consec_strong": 5, "n_consec_weak": 3,
               "alpha": "真 alpha +7.73% / 20d (sigma 7.50)"},
}

DCA_PLAN = {
    "0050":  {"target": 1000, "first_batch": (300, "88.5-90.5")},
    "00881": {"target": 1100, "first_batch": (350, "45.0-46.5")},
    "00947": {"target": 1000, "first_batch": (300, "29.5-30.5")},
    "00646": {"target": 1700, "first_batch": (500, "69.5-71.0")},
    "EWY":   {"target": 12,   "first_batch": (2,   "152-156 USD")},
}


def render_orb_section(project_root: Path) -> str:
    """ORB paper trade 今日狀態 + ledger 累計。"""
    lines = ["## 🎯 [Paper] ORB Paper Trade 訊號\n"]
    lines.append("<details><summary>📖 ORB 策略（點開）</summary>\n")
    lines.append("**Opening Range Breakout（開盤區間突破）：**")
    lines.append("- 09:00-09:15 量能爆發 + 突破開盤 5 分高點 → 進場做多")
    lines.append("- 13:25 強制平倉（避過夜風險）")
    lines.append("")
    lines.append("**白名單僅 2 檔過 Tier A**（31 ticker × 24 變體掃完）：")
    lines.append("- **2408 南亞科**：OOS +0.99%/筆，57% win, n=14, CI [+0.10, +2.65]")
    lines.append("- **2485 兆赫**：OOS +1.58%/筆，69% win, n=16, CI [+0.35, +3.07]")
    lines.append("")
    lines.append("**Paper trade only**（紙上模擬，不實際下單）：累積 20 筆才考慮實盤")
    lines.append("</details>\n")
    lines.append("**自動排程：** 09:20 偵測訊號 / 13:25 強制平倉\n")
    lines.append("| 代號 | 名稱 | 進場規則 | 預期 alpha |")
    lines.append("|---|---|---|---|")
    alpha_text = {
        "2408": "OOS +0.99%/57% (n=14, CI [+0.10, +2.65]) ✅",
        "2485": "OOS +1.58%/69% (n=16, CI [+0.35, +3.07]) ✅",
    }
    for tk, info in ORB_WHITELIST.items():
        rule = f"{info['entry']} / vol≥{info['vol']} / ref={info['ref']}"
        lines.append(f"| {tk} | {info['name']} | {rule} | {alpha_text.get(tk, '—')} |")

    # Ledger 累計
    ledger = project_root / "data" / "paper_trades" / "orb_ledger.csv"
    if ledger.exists():
        try:
            df = pd.read_csv(ledger)
            if not df.empty and "status" in df.columns:
                closed = df[df["status"] == "closed"]
                if len(closed) >= 1:
                    wins = (closed["is_winner"].astype(str).str.lower() == "true").sum()
                    win_rate = wins / len(closed) * 100
                    mean_net = closed["net_return_pct"].mean()
                    total = closed["net_return_pct"].sum()
                    lines.append(f"\n**累計 paper trade：** {len(closed)} 筆 | "
                                 f"勝率 {win_rate:.0f}% | 平均 {mean_net:+.2f}%/筆 | "
                                 f"累計 {total:+.2f}%")
                else:
                    lines.append("\n**累計 paper trade：** 尚無平倉紀錄")
        except Exception:
            pass

    return "\n".join(lines) + "\n"


def render_institutional_section(project_root: Path) -> str:
    """法人訊號當下狀態（讀 cache 算連買天數）。"""
    cache_dir = project_root / "data" / "cache" / "finmind" / "institutional"
    lines = ["## 📡 [Paper] 法人訊號（真 alpha 驗證後）\n"]
    lines.append("<details><summary>📖 法人連買訊號（點開）</summary>\n")
    lines.append("**核心：** 法人連續買超 N 天暗示「持續吃貨」，是台股 momentum alpha 來源。")
    lines.append("")
    lines.append("**驗證方法（重點）：**「真 alpha = 扣 0050 baseline 後仍有 alpha」")
    lines.append("- 9 年實證 baseline 必須是「同 ticker 隨機進場」")
    lines.append("- 不是 vs 0050（會對個股高估、對 0050 姊妹 ETF 抵銷）")
    lines.append("")
    lines.append("**已驗證真 alpha 訊號：**")
    lines.append("- 0050 自營商連買 3d/20d → +1.23% (MCPT p<0.001, 4/4 期 robust 唯一)")
    lines.append("- 006208 外資連買 3d → +1.84%/20d")
    lines.append("- 00881 外資連買 → +2.99%/20d")
    lines.append("- 2308 外資連買 → +7.73%/20d (受 LATE_BULL 暫停)")
    lines.append("</details>\n")
    lines.append("**僅顯示扣 0050 baseline 後仍有真 alpha 的訊號**\n")
    lines.append("| 代號 | 名稱 | 法人 | 連買天數 | 訊號 | 歷史 alpha |")
    lines.append("|---|---|---|---|---|---|")

    for tk, info in INSTITUTIONAL_SIGNALS.items():
        cp = cache_dir / f"{tk}.parquet"
        if not cp.exists():
            lines.append(f"| {tk} | {info['name']} | — | 無資料 | — | {info['alpha']} |")
            continue
        try:
            df = pd.read_parquet(cp)
            df["date"] = pd.to_datetime(df["date"]).dt.date
            inv_df = df[df["name"] == info["investor"]].sort_values("date")
            if inv_df.empty:
                continue
            inv_df = inv_df.copy()
            inv_df["is_buy"] = inv_df["net_buy"] > 0

            consec = 0
            for is_buy in reversed(inv_df["is_buy"].tolist()):
                if is_buy:
                    consec += 1
                else:
                    break

            if consec >= info["n_consec_strong"]:
                status = f"🟢 **強 ({consec} 日)** — 建議加大下批 DCA / 進場"
            elif consec >= info["n_consec_weak"]:
                status = f"🟡 弱 ({consec} 日) — 偏多但訊號弱"
            elif consec == 0:
                # 也算最近賣超天數
                consec_sell = 0
                for is_buy in reversed(inv_df["is_buy"].tolist()):
                    if not is_buy:
                        consec_sell += 1
                    else:
                        break
                status = f"⚪ 連續 {consec_sell} 日賣超" if consec_sell else "⚪ 中性"
            else:
                status = f"⚪ {consec} 日"

            consec_text = f"S≥{info['n_consec_strong']}/W≥{info['n_consec_weak']}"
            lines.append(f"| {tk} | {info['name']} | "
                         f"{info['investor'].replace('_', ' ')[:7]} | "
                         f"{consec_text} | {status} | {info['alpha']} |")
        except Exception as e:
            lines.append(f"| {tk} | {info['name']} | — | 錯誤 | — | {info['alpha']} |")

    return "\n".join(lines) + "\n"


def render_dca_section(project_root: Path) -> str:
    """DCA 進度 + 下一批建議。"""
    assets_path = project_root / "data" / "assets.json"
    ticker_shares = {}
    cash = 0
    if assets_path.exists():
        try:
            data = json.loads(assets_path.read_text(encoding="utf-8"))
            cash = float(data.get("cash", 0))
            for h in (data.get("holdings", {}).get("long_term", []) +
                      data.get("holdings", {}).get("short_term", [])):
                ticker_shares[str(h.get("ticker", ""))] = int(h.get("shares", 0))
        except Exception:
            pass

    lines = ["## 📈 DCA 進度（9 週分批計畫）\n"]
    lines.append(f"**現金：** NT$ {cash:,.0f}\n")
    lines.append("| ETF | 已買/目標 | % | 下一批建議 |")
    lines.append("|---|---|---|---|")
    for tk, plan in DCA_PLAN.items():
        owned = ticker_shares.get(tk, 0)
        pct = (owned / plan["target"]) * 100 if plan["target"] else 0
        if owned == 0:
            next_action = f"立刻第1批 **{plan['first_batch'][0]}** 股 @ {plan['first_batch'][1]}"
        elif pct >= 100:
            next_action = "✅ 完成"
        else:
            next_action = "進行中"
        lines.append(f"| {tk} | {owned:,} / {plan['target']:,} | {pct:.0f}% | {next_action} |")

    return "\n".join(lines) + "\n"


def render_regime_section() -> str:
    """市場 Regime + 啟用/暫停策略。"""
    try:
        import sys
        from pathlib import Path
        ROOT = Path(__file__).resolve().parents[2]
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from src.risk.strategy_regime_gate import detect_current_regime, evaluate_strategies

        r = detect_current_regime()
        result = evaluate_strategies(r)
        pct = (r.taiex_close / r.ma200 - 1) * 100

        lines = [
            "## 📊 當下市場 Regime + 策略 Gate\n",
            f"**TAIEX:** {r.taiex_close:,.0f} (距 MA200 {pct:+.1f}%, MA60 {r.ma60:,.0f})",
            f"**Trend:** `{r.trend.upper()}` | **Cycle:** `{r.cycle.upper()}` | "
            f"**Vol:** `{r.vol_state.upper()}` (30d {r.realized_vol_30d:.1f}%) | "
            f"**VIX:** {r.vix:.1f}",
            "",
        ]
        if r.cycle == "late_bull":
            lines.append("⚠️ **LATE_BULL 過熱** — 部分 regime-dep 策略已自動暫停\n")
        elif r.cycle == "bear":
            lines.append("🔴 **BEAR 熊市** — 多數策略暫停，僅 market-neutral 啟用\n")

        lines.append(f"### ✅ 啟用策略 ({len(result['active'])})\n")
        for s in result["active"]:
            rule = s["rule"]
            lines.append(f"- **{rule.name}** (α {rule.expected_alpha:+.2f}%) — {rule.backtest_status}")

        if result["suspended"]:
            lines.append(f"\n### ⏸ 暫停策略 ({len(result['suspended'])})\n")
            for s in result["suspended"]:
                rule = s["rule"]
                reasons = "; ".join(s["reasons"][:2])
                lines.append(f"- {rule.name} — {reasons}")

        return "\n".join(lines) + "\n"
    except Exception as e:
        return f"## 📊 Regime\n\n計算失敗: {e}\n"


def render_overnight_section() -> str:
    """夜盤訊號預測明日台股開盤跳空。"""
    try:
        import yfinance as yf
        symbols = [
            ("TSM", "TSMC ADR", 0.69),
            ("SOXX", "SOX 半導體 ETF", 0.71),
            ("NVDA", "NVIDIA", 0.50),
            ("SPY", "S&P 500", 0.64),
            ("^VIX", "恐慌指數", -0.44),
        ]
        lines = ["## 🌙 夜盤訊號（預測明日 TW 開盤跳空，hit ~76%）\n"]
        lines.append("| 美股 | 名稱 | 收盤 | 漲跌% | 預測 TW 跳空 |")
        lines.append("|---|---|---|---|---|")
        for sym, name, beta in symbols:
            try:
                t = yf.Ticker(sym)
                h = t.history(period="2d", auto_adjust=False)
                if h.empty or len(h) < 2:
                    continue
                last = float(h["Close"].iloc[-1])
                prev = float(h["Close"].iloc[-2])
                ch = (last / prev - 1) * 100
                implied = beta * ch
                if abs(ch) < 0.3:
                    impl_text = "≈ 平盤"
                elif implied > 0.5:
                    impl_text = f"預期 {implied:+.2f}% 跳空高 🟢"
                elif implied < -0.5:
                    impl_text = f"預期 {implied:+.2f}% 跳空低 🔴"
                else:
                    impl_text = f"{implied:+.2f}% 微幅"
                lines.append(f"| {sym} | {name} | {last:.2f} | {ch:+.2f}% | {impl_text} |")
            except Exception:
                continue
        return "\n".join(lines) + "\n"
    except Exception as e:
        return f"## 🌙 夜盤\n\n抓取失敗: {e}\n"


def render_pair_spread_section(project_root: Path) -> str:
    """配對交易 spread z-score 即時。"""
    import numpy as np
    try:
        cache_yf = project_root / "data" / "cache" / "yfinance" / "tw_ohlcv"

        intro = (
            "<details><summary>📖 配對交易原理（點開）</summary>\n\n"
            "**核心：** 兩檔高度相關股票（如 DRAM 雙雄 2408 vs 2344），股價偏離常態時做反向。\n\n"
            "**規則：**\n"
            "- spread = log(A) - log(B)，60 日 rolling z-score\n"
            "- |z| > 2.5 進場（spread 偏離 2.5σ）：z>2.5 → short A long B；z<-2.5 → 反向\n"
            "- |z| < 0.5 出場（spread 回到均值）\n\n"
            "**已驗證 alpha**（41 對篩到 6 對 Tier A）：\n"
            "- DRAM **2408-2344**：+3.16%/筆，77% win，n=30，累計 +94.8%\n"
            "- 半導體 **2330-3711**：+2.46%/筆，61% win，n=36\n"
            "- 重電 **1513-1519**：+2.58%/筆，65% win，n=23\n\n"
            "**為何是真 alpha：** market-neutral（不受大盤拉抬），純 spread move。\n"
            "**需要：** 信用帳戶 + Shioaji API（永豐金開戶中）\n"
            "</details>\n\n"
        )

        def load(tk):
            p = cache_yf / f"{tk}.parquet"
            if not p.exists(): return pd.DataFrame()
            df = pd.read_parquet(p)
            df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date").reset_index(drop=True)

        pairs = [("DRAM 2408-2344", "2408", "2344"),
                  ("重電 1513-1519", "1513", "1519"),
                  ("半導體 2330-3711", "2330", "3711"),
                  ("半導體 2454-3711", "2454", "3711"),
                  ("航運 2609-2615", "2609", "2615"),
                  ("塑化 1301-1326", "1301", "1326")]
        lines = ["## 💱 配對交易 Spread Z-score（觸 ±2.5 進場）\n", intro]
        lines.append("| Pair | corr | 當下 z-score | 訊號 |")
        lines.append("|---|---|---|---|")
        for name, a, b in pairs:
            a_df = load(a); b_df = load(b)
            if a_df.empty or b_df.empty: continue
            merged = pd.merge(
                a_df[["date", "close"]].rename(columns={"close": "a"}),
                b_df[["date", "close"]].rename(columns={"close": "b"}),
                on="date").sort_values("date").reset_index(drop=True)
            if len(merged) < 60: continue
            merged["log_a"] = np.log(merged["a"])
            merged["log_b"] = np.log(merged["b"])
            merged["spread"] = merged["log_a"] - merged["log_b"]
            merged["spread_mean"] = merged["spread"].rolling(60).mean()
            merged["spread_std"] = merged["spread"].rolling(60).std()
            merged["z"] = (merged["spread"] - merged["spread_mean"]) / merged["spread_std"]
            corr = merged["log_a"].corr(merged["log_b"])
            z_now = float(merged["z"].iloc[-1])
            if z_now > 2.5:
                signal = f"🟢 **進場！short {a} long {b}**"
            elif z_now < -2.5:
                signal = f"🟢 **進場！long {a} short {b}**"
            elif abs(z_now) > 1.5:
                signal = f"🟡 接近觸發 (|z|>{abs(z_now):.2f})"
            else:
                signal = "⚪ 平靜"
            lines.append(f"| {name} | {corr:.2f} | {z_now:+.2f} | {signal} |")
        return "\n".join(lines) + "\n"
    except Exception as e:
        return f"## 💱 配對\n\n計算失敗: {e}\n"


def render_dxj_timing_section() -> str:
    """DXJ 日股 DCA timing — SPY/JPY 訊號偵測"""
    try:
        import yfinance as yf

        lines = ["## 🇯🇵 DXJ 日股 DCA Timing（加碼訊號檢測）\n"]
        lines.append("<details><summary>📖 策略原理（點開）</summary>\n")
        lines.append("**核心：** DXJ 是 hedged 日股 ETF（移除日圓影響），對台股有分散效果。")
        lines.append("")
        lines.append("**加碼觸發**（16 年實證 alpha）：")
        lines.append("- SPY 30 日跌 >5% → 90日 alpha **+3.27%**（n=90, z=5.01）")
        lines.append("- SPY 90 日跌 >10% → 90日 alpha **+3.71%**（n=164, z=4.24）")
        lines.append("- USD/JPY 30 日變動 >5% → 90日 alpha **+5.22%**（n=136, z=5.44）")
        lines.append("")
        lines.append("**Why:** 全球 risk-off 後日股相對美股 P/E 低 → 反彈彈性大。")
        lines.append("</details>\n")
        lines.append("**基礎配置:** 8% | **加碼目標:** 12% (遇 trigger)\n")
        lines.append("| 訊號 | 規則 | 過去 alpha | 當下狀態 |")
        lines.append("|---|---|---|---|")

        triggers = []

        try:
            spy = yf.Ticker("SPY")
            spy_hist = spy.history(period="100d", auto_adjust=False)
            if len(spy_hist) >= 90:
                spy_30d_change = (spy_hist["Close"].iloc[-1] / spy_hist["Close"].iloc[-30] - 1) * 100
                spy_90d_change = (spy_hist["Close"].iloc[-1] / spy_hist["Close"].iloc[-90] - 1) * 100

                if spy_30d_change < -5:
                    triggers.append("SPY 30日跌 >5%")
                    lines.append(f"| SPY 30日跌 >5% | 30日 +3.27% | ✅ **觸發** ({spy_30d_change:+.2f}%) |")
                elif spy_90d_change < -10:
                    triggers.append("SPY 90日跌 >10%")
                    lines.append(f"| SPY 90日跌 >10% | 90日 +3.71% | ✅ **觸發** ({spy_90d_change:+.2f}%) |")
                else:
                    lines.append(f"| SPY 訊號 | — | — | ⚪ 未觸發 (30d {spy_30d_change:+.2f}%, 90d {spy_90d_change:+.2f}%) |")
        except Exception:
            lines.append("| SPY | — | — | ⚠️ 資料抓取失敗 |")

        try:
            jpy = yf.Ticker("USDJPY=X")
            jpy_hist = jpy.history(period="40d", auto_adjust=False)
            if len(jpy_hist) >= 30:
                jpy_30d_change = abs(jpy_hist["Close"].iloc[-1] / jpy_hist["Close"].iloc[-30] - 1) * 100

                if jpy_30d_change > 5:
                    triggers.append("USD/JPY 30日變動 >5%")
                    jpy_direction = "升值" if jpy_hist["Close"].iloc[-1] > jpy_hist["Close"].iloc[-30] else "貶值"
                    lines.append(f"| JPY 30日變動 >5% | 90日 +5.22% | ✅ **觸發** ({jpy_direction} {jpy_30d_change:+.2f}%) |")
                else:
                    lines.append(f"| JPY 30日變動 | — | — | ⚪ 未觸發 ({jpy_30d_change:+.2f}%) |")
        except Exception:
            lines.append("| JPY | — | — | ⚠️ 資料抓取失敗 |")

        if triggers:
            lines.append(f"\n**🟢 加碼建議：{' + '.join(triggers)}**")
            lines.append(f"目前 EWY 配置是否已升至 12% ? 若未，加碼至配置目標。")
        else:
            lines.append("\n⚪ 無加碼訊號，維持 base DCA 8%")

        return "\n".join(lines) + "\n"
    except Exception as e:
        return f"## 🇯🇵 DXJ Timing\n\n計算失敗: {e}\n"


def render_strategy_section(project_root: Path) -> str:
    """主入口：6 段組合（夜盤 / 配對 / DXJ / ORB / 法人 / DCA）

    舊 regime_section 已撤除 (2026-05-05) — 與 V2 Regime + Hedge + Barbell 重複。
    """
    parts = [
        # render_regime_section(),   # REMOVED: 舊 strategy gate, 與 V2 重複
        render_overnight_section(),
        render_pair_spread_section(project_root),
        render_dxj_timing_section(),
        render_orb_section(project_root),
        render_institutional_section(project_root),
        render_dca_section(project_root),
    ]
    return "\n\n---\n\n".join(parts)
