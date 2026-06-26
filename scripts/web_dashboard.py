"""
INVEST Web Dashboard — Streamlit 手機友善版本

啟動：
  streamlit run scripts/web_dashboard.py

或 LAN/外網存取（手機開瀏覽器）：
  streamlit run scripts/web_dashboard.py --server.address 0.0.0.0 --server.port 8501
  → 手機打 http://你電腦IP:8501

預設 localhost 只能本機。要手機看須開放 LAN 或 Tailscale。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ASSETS = ROOT / "data" / "assets.json"
LEDGER = ROOT / "data" / "paper_trades" / "triggered_signals.csv"
STATE = ROOT / "data" / "paper_trades" / "daily_state.csv"
SCANNER_HITS = ROOT / "data" / "paper_trades" / "scanner_hits.csv"

st.set_page_config(
    page_title="INVEST Dashboard",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Mobile-friendly CSS ──
st.markdown("""
<style>
    .stMetric { background-color: #1e1e1e; padding: 12px; border-radius: 8px; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    div[data-testid="stMetricLabel"] { font-size: 0.85rem; color: #888; }
    .stAlert { border-radius: 8px; }
    h1, h2, h3 { color: #4ec9b0; }
    @media (max-width: 768px) {
        .block-container { padding: 1rem 0.5rem; }
    }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=60)
def load_assets():
    if not ASSETS.exists(): return {}
    return json.loads(ASSETS.read_text(encoding="utf-8"))


@st.cache_data(ttl=300)
def get_price(ticker: str) -> float:
    try:
        import yfinance as yf
        if not ticker.replace(".", "").isdigit():
            hist = yf.Ticker(ticker).history(period="5d")
            if not hist.empty: return float(hist["Close"].iloc[-1])
            return 0.0
        for sfx in [".TW", ".TWO"]:
            try:
                hist = yf.Ticker(ticker + sfx).history(period="5d")
                if not hist.empty: return float(hist["Close"].iloc[-1])
            except Exception:
                continue
    except Exception:
        return 0.0
    return 0.0


@st.cache_data(ttl=120)
def load_ledger():
    if not LEDGER.exists(): return pd.DataFrame()
    return pd.read_csv(LEDGER, dtype=str)


@st.cache_data(ttl=120)
def load_scanner_hits():
    if not SCANNER_HITS.exists(): return pd.DataFrame()
    return pd.read_csv(SCANNER_HITS)


@st.cache_data(ttl=120)
def load_daily_state():
    if not STATE.exists(): return pd.DataFrame()
    return pd.read_csv(STATE)


# ── Header ──
col1, col2 = st.columns([3, 1])
with col1:
    st.title("💰 INVEST Dashboard")
    st.caption(f"📅 {date.today()} • {datetime.now().strftime('%H:%M:%S')}")
with col2:
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── 系統說明（新手 onboarding）──
with st.expander("📖 INVEST 系統說明（第一次看點開）"):
    st.markdown("""
**這是什麼？**
INVEST 是台股量化顧問系統（純顧問，不下單）。每日掃 1962 檔 + 整合多個已驗證 alpha 訊號 + 風控 gate。

**已驗證 alpha 訊號（按 trigger 強度）：**
| 訊號 | n | alpha | t-stat | 用途 |
|---|---|---|---|---|
| EH v3.7 | 8 年 OOS | +14.94pp | — | 月度核心策略 |
| 月營收 Relative YoY | 24,476 | **+3.95%/60d** | **+24.19** | 個股篩選 |
| 妖股多因子 S1+S3 | ~1K | +8.13pp/60d | — | 中小股篩選 |
| 連漲+法人 | ~500 | +11.23pp/60d | — | 妖股 #1 |
| 配對交易 6 對 | 30-50 | +1-3%/筆 | MCPT | market neutral |
| 外資 TX z<-2.0 | 123 | +1.43%/10d | +4.09 | crash hedge |
| 異常量能 z 3-3.5 | ~600 | hit 24% | — | 吃貨期偵測 |

**晨報每日 8:30 推 Discord，包含：**
- 風控狀態（DCA Gate / Crash Hedge / 集中度警報）
- 當日掃描訊號
- 配對交易 z-score 監控
- 法人連買訊號
- 持股集中度 + 部署排程

**已驗證 dead-end（避免再走的路）：**
ORB 個股當沖 / TW 配對 entry signals / Tick entry signal / Prop firm overnight rule / 高股息 ETF / Value 投資 5 preset
    """)


# ── Portfolio Overview ──
assets = load_assets()
cash = assets.get("cash", 0)
holdings = assets.get("holdings", {}).get("long_term", [])

total_mv = 0
holding_rows = []
for h in holdings:
    tk = h.get("ticker", "")
    sh = h.get("shares", 0)
    cost = h.get("cost", 0)
    cost_incl_fee = h.get("cost_incl_fee", cost)  # fallback to cost if not set
    price = get_price(tk) or cost
    # Gross PnL convention (matches broker display)
    mv = sh * price
    pl_pct = (price/cost_incl_fee - 1) * 100 if cost_incl_fee > 0 else 0
    holding_rows.append({"ticker": tk, "shares": sh, "cost": cost,
                          "price": price, "mv": mv, "pl_pct": pl_pct})
    total_mv += mv
total = cash + total_mv

st.divider()
m1, m2, m3, m4 = st.columns(4)
m1.metric("總資產", f"NT${total:,.0f}")
m2.metric("現金", f"NT${cash:,}", f"{cash/total*100:.1f}%")
m3.metric("持股", f"NT${total_mv:,.0f}", f"{total_mv/total*100:.1f}%")
m4.metric("持股檔數", f"{len(holdings)} 檔")


# ── Concentration Alert ──
alerts = []
for r in holding_rows:
    pct = r["mv"] / total_mv * 100 if total_mv > 0 else 0
    if pct > 30:
        alerts.append(f"🚨 **{r['ticker']}** 占持股 **{pct:.0f}%**（>30%）— 集中度風險")
cash_pct = cash / total * 100 if total > 0 else 0
if cash_pct > 50:
    alerts.append(f"💰 現金 **{cash_pct:.0f}%** vs 目標 26% — 加速 DCA")

if alerts:
    for a in alerts:
        st.warning(a)


# ── Holdings table ──
st.subheader("📊 持股明細")
if holding_rows:
    df = pd.DataFrame(holding_rows)
    df["pct_of_holdings"] = df["mv"] / df["mv"].sum() * 100
    df_display = df.copy()
    df_display["mv"] = df_display["mv"].apply(lambda x: f"NT${x:,.0f}")
    df_display["price"] = df_display["price"].apply(lambda x: f"{x:.2f}")
    df_display["cost"] = df_display["cost"].apply(lambda x: f"{x:.2f}")
    df_display["pl_pct"] = df_display["pl_pct"].apply(lambda x: f"{x:+.1f}%")
    df_display["pct_of_holdings"] = df_display["pct_of_holdings"].apply(lambda x: f"{x:.1f}%")
    df_display.columns = ["代號", "股數", "成本", "現價", "市值", "P/L", "占持股"]
    st.dataframe(df_display, use_container_width=True, hide_index=True)
else:
    st.info("尚無持股")


# ── Deployment Schedule（完整渲染）──
try:
    from src.report.deployment_section import render_deployment_section
    deploy_md = render_deployment_section(ROOT)
    # 直接整段 render（含「未來 7 日 DCA」+ 集中度警報）
    # 把 H2 ## 換成 H3 ### 才不會跟頁面標題搶
    deploy_md = deploy_md.replace("## 💰 部署排程", "### 💰 部署排程")
    st.markdown(deploy_md)
except Exception as e:
    st.error(f"部署排程讀取失敗: {e}")

# ── Alpha Decay ──
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    from alpha_decay_monitor import render_briefing_section as render_decay
    decay_md = render_decay()
    decay_md = decay_md.replace("## 📉", "### 📉")
    st.markdown(decay_md)
except Exception as e:
    st.warning(f"Alpha Decay 區塊未渲染: {e}")


# ── Daily Signals ──
st.subheader("🔔 今日 Daily Scanner 結果")
with st.expander("📖 訊號類型說明"):
    st.markdown("""
**3 類訊號（按 alpha 強度）：**

1. **🐉 妖股 #1（連漲+法人）**：3 日內 ≥2 次漲幅 ≥9% AND 當日法人 +200 張 → 60d alpha **+11.23pp**
2. **📊 多因子 S1+S3**：散戶占比 <20% 分位 AND 量爆 z ≥2.5 → 60d alpha **+8.13pp**（中小股）
3. **💰 月營收 Relative YoY**：個股 YoY - 市場 median > +30% → 60d alpha **+3.95%**, t=24.19

⚠️ Alpha 是 trade-level 對 same-ticker random baseline 的 lift。Portfolio-level 受 V2 framework dilution，實際報酬較低。
    """)
hits_df = load_scanner_hits()
if hits_df.empty:
    st.info("⚪ 尚無觸發紀錄（每日 14:00 自動掃 1962 檔）")
else:
    today_str = date.today().isoformat()
    today_hits = hits_df[hits_df["scan_date"] == today_str] if "scan_date" in hits_df.columns else pd.DataFrame()
    if today_hits.empty:
        st.info(f"⚪ 今日（{today_str}）無觸發；歷史共 {len(hits_df)} 筆")
    else:
        st.success(f"🚨 今日 {len(today_hits)} 筆觸發")
        st.dataframe(today_hits, use_container_width=True, hide_index=True)


# ── Daily State ──
st.subheader("📊 全策略當下狀態")
state_df = load_daily_state()
if not state_df.empty:
    last = state_df.iloc[-1]
    cs1, cs2 = st.columns(2)
    with cs1:
        st.markdown("**配對交易 z-score**")
        for col in [c for c in state_df.columns if c.startswith("pair_") and c.endswith("_z")]:
            label = col.replace("pair_", "").replace("_z", "")
            val = last[col]
            color = "🟢" if abs(float(val)) > 2.5 else ("🟡" if abs(float(val)) > 1.5 else "⚪")
            st.text(f"  {color} {label}: z={val:+.2f}")
    with cs2:
        st.markdown("**法人連買天數**")
        for col in [c for c in state_df.columns if "consec_buy" in c]:
            val = int(last[col])
            tk = col.replace("inst_", "").replace("_consec_buy", "")
            badge = "🟢" if val >= 3 else ("🟡" if val >= 2 else "⚪")
            st.text(f"  {badge} {tk}: {val} 日")
        if "orb_regime_status" in state_df.columns:
            st.text(f"\n  ORB: {last['orb_regime_status']}")


# ── Recent Briefing ──
st.subheader("📄 最新晨報")
log_dir = ROOT / "logs"
mds = sorted(log_dir.glob("20*-*.md"), reverse=True)
if mds:
    latest = mds[0]
    with st.expander(f"📑 {latest.name}（點開預覽）"):
        try:
            content = latest.read_text(encoding="utf-8")
            st.markdown(content)
        except Exception as e:
            st.error(f"讀取失敗: {e}")
else:
    st.info("尚無晨報")


# ── Actions ──
st.divider()
st.subheader("⚡ 動作")
ac1, ac2, ac3 = st.columns(3)


def run_script(script_name: str, args: list[str] | None = None):
    """跑腳本並回傳 stdout"""
    cmd = [sys.executable, str(ROOT / "scripts" / script_name)] + (args or [])
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace", env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


with ac1:
    if st.button("📰 跑晨報", use_container_width=True):
        with st.spinner("執行中（30s-2min）..."):
            code, out, err = run_script("morning_briefing.py")
            if code == 0:
                st.success("✅ 晨報完成")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"❌ exit {code}: {err[:200]}")

with ac2:
    if st.button("🌐 重抓行情", use_container_width=True):
        with st.spinner("重抓中（~1 分鐘）..."):
            code, out, err = run_script("refresh_quotes.py")
            if code == 0:
                st.success("✅ 行情已更新")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"❌ exit {code}")

with ac3:
    if st.button("🔍 跑 daily scanner", use_container_width=True):
        with st.spinner("掃描 1962 檔..."):
            code, out, err = run_script("daily_signal_scanner.py")
            if code == 0:
                st.success("✅ 掃描完成")
                # show last few output lines
                tail = out.strip().split("\n")[-5:]
                st.code("\n".join(tail))
                st.cache_data.clear()
            else:
                st.error(f"❌ exit {code}")


# Footer
st.divider()
st.caption("🤖 INVEST Dashboard • 資料每 60-300 秒快取 • Powered by Streamlit")
