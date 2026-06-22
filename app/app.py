"""我的投資記帳本 — 純本地記錄,不給任何投資建議

執行: streamlit run app/app.py
"""
from __future__ import annotations
import json, os
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "user_data"
DATA_DIR.mkdir(exist_ok=True)

# 自動載入 config/.env (GEMINI_API_KEY, FINMIND_TOKEN 等)
try:
    from dotenv import load_dotenv
    _ENV_PATH = ROOT.parent / "config" / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, encoding="utf-8")
except ImportError:
    # 沒裝 python-dotenv 也 OK,settings.json fallback
    pass

import streamlit as st
import pandas as pd

try:
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

st.set_page_config(
    page_title="韭菜健檢",
    page_icon="🩺",
    layout="centered",
    initial_sidebar_state="collapsed",
)
TW = timezone(timedelta(hours=8))


def _time_bucket() -> str:
    """根據台股交易時段動態 bucket key,讓 cache_data 自動失效:
       - 週六日 / 國定假日:整天共用 key
       - 00:00 - 08:00:沿用前一日 eod(夜間休眠)
       - 08:00 - 09:00:盤前 30 min refresh
       - 09:00 - 13:30:盤中 15 min refresh
       - 13:30 - 17:00:盤後 30 min refresh(等法人公告)
       - 17:00 之後:eod 整夜 cache"""
    now = datetime.now(TW)
    if now.weekday() >= 5:
        return f"weekend_{now.strftime('%Y%m%d')}"
    h, m = now.hour, now.minute
    if h < 8:
        return f"eod_{(now - timedelta(days=1)).strftime('%Y%m%d')}"
    if h == 8:
        return f"pre_{now.strftime('%Y%m%d')}_{m // 30}"
    if 9 <= h < 13 or (h == 13 and m < 30):
        return f"intra_{now.strftime('%Y%m%d_%H')}_{m // 15}"
    if (h == 13 and m >= 30) or (14 <= h < 17):
        return f"close_{now.strftime('%Y%m%d_%H')}_{m // 30}"
    return f"eod_{now.strftime('%Y%m%d')}"

# ── 多 user 模式:接 Supabase auth(本機沒設 env → 自動 local-user 單機 mode)──
import sys as _sys
_sys.path.insert(0, str(ROOT.parent))

# OAuth callback 已改成靜態頁面 /app/static/oauth_callback.html(避開 Streamlit iframe sandbox)
# 那頁 JS 抓 #access_token 寫 cookie 再 redirect 回 /,本檔不再注入 bridge JS。

try:
    from src import db as _db, auth as _auth
    USER_ID = _auth.get_current_user_id()  # 雲端模式未登入會 st.stop()
    _auth.render_user_menu()
except Exception as _e:
    # auth 模組掛了 → 退回 local-user 不阻塞
    USER_ID = "local-user"
    print(f"[auth] init failed, fallback local-user: {_e}")

# ── 深色主題 + 手機直式比例 ──
st.markdown("""
<style>
    /* 整體背景 */
    .stApp { background-color: #16181d; }

    /* 強制手機直式比例 — max 480px (約 iPhone 16 寬) */
    .main .block-container {
        max-width: 480px !important;
        padding: 0.8rem 0.6rem !important;
    }

    /* 側邊欄 */
    section[data-testid="stSidebar"] {
        background-color: #1e2128;
        border-right: 1px solid #2f343d;
    }

    /* Metric card 卡片風 */
    div[data-testid="stMetric"] {
        background-color: #1e2128;
        padding: 16px 18px;
        border-radius: 10px;
        border: 1px solid #2f343d;
        transition: border-color 0.2s;
    }
    div[data-testid="stMetric"]:hover {
        border-color: #14b8a6;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.7rem;
        color: #e4e6eb;
        font-weight: 600;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.85rem;
        color: #8b92a0;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* 標題 — 米白(讓 lime 留給強調用) */
    h1, h2, h3 {
        color: #e4e6eb !important;
    }
    h1 { font-size: 2rem !important; }
    h2 { font-size: 1.4rem !important; }
    h3 { font-size: 1.15rem !important; }

    /* 一般文字 */
    .stMarkdown, .stText, p, label {
        color: #e4e6eb;
    }
    .stCaption, [data-testid="stCaptionContainer"] {
        color: #8b92a0 !important;
    }

    /* Button — primary 用韭菜 lime */
    .stButton > button {
        background-color: #1e2128;
        color: #e4e6eb;
        border: 1px solid #2f343d;
        border-radius: 8px;
        padding: 8px 16px;
        transition: all 0.2s;
    }
    .stButton > button:hover {
        border-color: #14b8a6;
        color: #5eead4;
    }
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #14b8a6 0%, #0d9488 100%);
        color: #16181d;
        border: none;
        font-weight: 700;
        box-shadow: 0 2px 8px rgba(132, 204, 22, 0.25);
    }
    .stButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #5eead4 0%, #14b8a6 100%);
        color: #0f1a0a;
        box-shadow: 0 4px 14px rgba(132, 204, 22, 0.4);
    }

    /* DataFrame */
    [data-testid="stDataFrame"] {
        background-color: #1e2128;
        border-radius: 8px;
        border: 1px solid #2f343d;
    }

    /* Alert boxes */
    .stAlert {
        border-radius: 8px;
        background-color: #1e2128;
        border-left-width: 4px;
    }
    div[data-baseweb="notification"][kind="info"] {
        background-color: rgba(96, 165, 250, 0.1);
        border-left-color: #60a5fa;
    }
    div[data-baseweb="notification"][kind="success"] {
        background-color: rgba(52, 211, 153, 0.1);
        border-left-color: #34d399;
    }
    div[data-baseweb="notification"][kind="warning"] {
        background-color: rgba(251, 191, 36, 0.1);
        border-left-color: #fbbf24;
    }

    /* Input 欄位 */
    input, .stNumberInput input, .stTextInput input, .stSelectbox > div {
        background-color: #16181d !important;
        color: #e4e6eb !important;
        border: 1px solid #2f343d !important;
        border-radius: 6px !important;
    }

    /* Divider */
    hr {
        border-color: #2f343d !important;
    }

    /* Expander */
    .streamlit-expanderHeader {
        background-color: #1e2128;
        border-radius: 8px;
        color: #e4e6eb;
    }

    /* 手機優化 — 加強 */
    @media (max-width: 768px) {
        .block-container { padding: 0.5rem 0.5rem; }
        h1 { font-size: 1.3rem !important; }
        h2 { font-size: 1.1rem !important; }
        h3 { font-size: 1rem !important; }
        div[data-testid="stMetricValue"] { font-size: 1.2rem !important; }
        div[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
        div[data-testid="stMetric"] { padding: 10px 12px !important; }
        /* metric delta 文字縮小 */
        div[data-testid="stMetricDelta"] { font-size: 0.8rem !important; }
        /* sidebar 直接收起 (手機) */
        section[data-testid="stSidebar"][aria-expanded="true"] { width: 80% !important; }
        /* form 欄位 column 改成豎排 */
        div[data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; }
        /* 按鈕大一點手指好點 */
        .stButton > button { min-height: 44px; font-size: 1rem; }
    }

    /* 數字 monospace (損益看起來整齊) */
    div[data-testid="stMetricValue"], div[data-testid="stMetricDelta"] {
        font-feature-settings: "tnum" 1;
        font-variant-numeric: tabular-nums;
    }

    /* 表格 row hover */
    [data-testid="stDataFrame"] tr:hover {
        background-color: rgba(132, 204, 22, 0.06) !important;
    }

    /* Progress bar lime */
    div[role="progressbar"] > div > div {
        background-color: #14b8a6 !important;
    }

    /* sidebar 標題 */
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2 {
        color: #e4e6eb !important;
        font-size: 1.5rem !important;
    }

    /* 隱藏 Streamlit 自帶 chrome */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header[data-testid="stHeader"] { background: transparent; }

    /* divider 變細 */
    hr { margin: 1rem 0 !important; opacity: 0.5; }

    /* radio (sidebar 選單) 變大變漂亮 */
    section[data-testid="stSidebar"] div[role="radiogroup"] label {
        padding: 12px 14px !important;
        font-size: 1rem !important;
        background-color: #16181d !important;
        border-radius: 8px !important;
        margin-bottom: 4px !important;
        border: 1px solid transparent;
        transition: all 0.15s;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        background-color: #2a2e37 !important;
        border-color: #14b8a6 !important;
    }

    /* Tabs 大字 */
    button[data-baseweb="tab"] {
        font-size: 1rem !important;
        padding: 12px 20px !important;
        color: #94a3b8 !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #14b8a6 !important;
        border-bottom-color: #14b8a6 !important;
    }
    div[data-baseweb="tab-highlight"] {
        background-color: #14b8a6 !important;
    }

    /* 連結預設不要藍色(避免新聞 / MOPS 連結出現預設 blue) */
    .stMarkdown a {
        color: #e4e6eb !important;
        text-decoration: none !important;
    }
    .stMarkdown a:hover {
        color: #5eead4 !important;
    }

    /* AI 結果區排版優化(stylable_container 內的 markdown) */
    div[class*="ai_box"] .stMarkdown,
    div[class*="ai_result"] .stMarkdown,
    div[class*="ai_box"] p,
    div[class*="ai_result"] p {
        line-height: 1.75 !important;
        font-size: 0.95rem !important;
    }
    div[class*="ai_box"] h1,
    div[class*="ai_box"] h2,
    div[class*="ai_box"] h3,
    div[class*="ai_box"] h4,
    div[class*="ai_result"] h1,
    div[class*="ai_result"] h2,
    div[class*="ai_result"] h3,
    div[class*="ai_result"] h4 {
        margin-top: 1.2em !important;
        margin-bottom: 0.5em !important;
        color: #5eead4 !important;
    }
    div[class*="ai_box"] ol,
    div[class*="ai_box"] ul,
    div[class*="ai_result"] ol,
    div[class*="ai_result"] ul {
        padding-left: 1.5em !important;
        margin-top: 0.5em !important;
    }
    div[class*="ai_box"] li,
    div[class*="ai_result"] li {
        margin-bottom: 0.4em !important;
        line-height: 1.7 !important;
    }
    div[class*="ai_box"] strong,
    div[class*="ai_result"] strong {
        color: #fff !important;
    }
    div[class*="ai_box"] p,
    div[class*="ai_result"] p {
        margin-bottom: 0.8em !important;
    }
    /* 但保留 expander 內 markdown 連結 hover 提示 */

    /* PWA inject iframe 強制隱藏(高度 0 但仍佔空間) */
    iframe[srcdoc*="serviceWorker"],
    iframe[height="0"],
    div[data-testid="stIFrame"]:has(iframe[height="0"]),
    div:has(> iframe[srcdoc*="manifest.json"]) {
        display: none !important;
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    /* Expander 卡片化 */
    div[data-testid="stExpander"] {
        background: linear-gradient(135deg, #1e293b 0%, #1e2128 100%);
        border-radius: 10px;
        border: 1px solid #2f343d;
        transition: border-color 0.2s;
    }
    div[data-testid="stExpander"]:hover {
        border-color: #14b8a6;
    }

    /* 全域:面板 hover 加 teal 邊框暗示可點 */
    div[data-testid="stMarkdownContainer"] > div[style*="border-left:3px solid"] {
        transition: transform 0.15s, box-shadow 0.15s;
    }
</style>
""", unsafe_allow_html=True)


# ───────────────────────────────────────────────────────
# 資料存讀(純本地檔)
# ───────────────────────────────────────────────────────
def load_json(name: str, default=None):
    # 多 user 模式:watchlist / settings 走 DB,其餘走 file
    if name == "watchlist":
        try:
            return _db.load_watchlist(USER_ID)
        except Exception as e:
            print(f"[load_json watchlist] DB failed, fallback file: {e}")
    if name == "settings":
        try:
            return _db.load_settings(USER_ID)
        except Exception as e:
            print(f"[load_json settings] DB failed, fallback file: {e}")
    p = DATA_DIR / f"{name}.json"
    if not p.exists():
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def save_json(name: str, data):
    if name == "watchlist":
        try:
            _db.save_watchlist(data, USER_ID)
            return
        except Exception as e:
            print(f"[save_json watchlist] DB failed, fallback file: {e}")
    if name == "settings":
        try:
            _db.save_settings(data, USER_ID)
            return
        except Exception as e:
            print(f"[save_json settings] DB failed, fallback file: {e}")
    p = DATA_DIR / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_setting(key, default):
    return load_json("settings", {}).get(key, default)


def set_setting(key, value):
    s = load_json("settings", {})
    s[key] = value
    save_json("settings", s)


def take_snapshot():
    """記錄當下總資產 snapshot,用於時間軸圖."""
    tw_holdings = load_json("tw_holdings", {"cash_twd": 0, "holdings": []})
    tw_prices = load_json("tw_prices", {})
    crypto_holdings = load_json("crypto_holdings", {})
    usd_twd = get_setting("usd_twd", 32.0)

    tw_mv = sum(h.get("shares", 0) * tw_prices.get(h["ticker"], 0)
                for h in tw_holdings.get("holdings", []))
    tw_cash = tw_holdings.get("cash_twd", 0)
    tw_total = tw_mv + tw_cash

    btc_qty = crypto_holdings.get("btc_qty", 0)
    btc_px = crypto_holdings.get("btc_px_usd", 0)
    crypto_total_usd = (btc_qty * btc_px + crypto_holdings.get("simple_earn_usd", 0)
                        + crypto_holdings.get("futures_usd", 0)
                        + crypto_holdings.get("spot_usd", 0))
    crypto_total_twd = crypto_total_usd * usd_twd
    grand = tw_total + crypto_total_twd

    if grand == 0:
        return  # 沒資料不存

    history = load_json("snapshots", {"records": []})
    today = datetime.now(TW).strftime("%Y-%m-%d")
    # 同日只留最新一筆
    history["records"] = [r for r in history["records"] if r.get("date") != today]
    history["records"].append({
        "date": today,
        "tw_mv": tw_mv,
        "tw_cash": tw_cash,
        "tw_total": tw_total,
        "crypto_usd": crypto_total_usd,
        "crypto_twd": crypto_total_twd,
        "grand_total": grand,
        "btc_px": btc_px,
    })
    history["records"] = sorted(history["records"], key=lambda r: r["date"])[-365:]
    save_json("snapshots", history)


def disclaimer():
    """每個頁面底部都顯示免責"""
    st.markdown("---")
    st.caption(
        "⚠️ **免責聲明**:這是個人記帳工具,所有數字由你自己輸入。"
        "本程式**不提供任何投資建議**,不分析、不預測、不推薦標的。"
        "投資決策請自行判斷或諮詢專業顧問,盈虧自負。"
    )


# ───────────────────────────────────────────────────────
# 頁面
# ───────────────────────────────────────────────────────
def page_welcome():
    """新手第一次來會看到這頁"""
    st.title("💰 我的投資記帳本")
    st.markdown("##### 一個簡單的工具,幫你記錄持股、看資產配置")

    tw_h = load_json("tw_holdings", {"cash_twd": 0, "holdings": []})
    crypto_h = load_json("crypto_holdings", {})
    is_first_time = (not tw_h.get("holdings")) and (not crypto_h.get("btc_qty"))

    if is_first_time:
        st.info("👋 看起來是第一次使用!跟著下面 3 步驟就能開始。")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("### 1️⃣ 輸入你的股票")
            st.write("有買台股就在 **TW 股票** 頁加入")
            st.write("有買加密貨幣就在 **加密貨幣** 頁填數量")

        with col2:
            st.markdown("### 2️⃣ 更新最新價格")
            st.write("到 **更新價格** 頁手動輸入")
            st.write("(每天看一次盤後價就好)")

        with col3:
            st.markdown("### 3️⃣ 看你的資產")
            st.write("回 **首頁** 看總值跟配置圓餅圖")
            st.write("一目瞭然")

        st.divider()
        st.write("👈 左邊選單切換頁面")
    else:
        # 已有資料 → 顯示 summary
        page_home()


def page_home():
    # ── 頂部:大字總值 + 變化 ──
    tw_holdings = load_json("tw_holdings", {"cash_twd": 0, "holdings": []})
    tw_prices = load_json("tw_prices", {})
    tw_mv = sum(h.get("shares", 0) * tw_prices.get(h["ticker"], 0)
                for h in tw_holdings.get("holdings", []))
    tw_cash = tw_holdings.get("cash_twd", 0)
    grand_total = tw_mv + tw_cash

    # 找昨天 snapshot 算變化
    history = load_json("snapshots", {"records": []})
    records = history.get("records", [])
    yesterday_total = records[-2]["grand_total"] if len(records) >= 2 else grand_total
    daily_change = grand_total - yesterday_total
    daily_pct = (daily_change / yesterday_total * 100) if yesterday_total > 0 else 0

    if grand_total == 0:
        st.markdown("<h1 style='text-align:center; color:#5eead4; margin-top:80px'>👋 歡迎</h1>",
                    unsafe_allow_html=True)
        st.markdown("<p style='text-align:center; font-size:1.2rem; color:#8b92a0'>"
                     "先到 <b>💼 我的持股</b> 加入你買的股票就能開始</p>",
                     unsafe_allow_html=True)
        return

    chg_color = "#34d399" if daily_change >= 0 else "#f43f5e"
    chg_sign = "+" if daily_change >= 0 else ""

    st.markdown(f"""
    <div style='text-align:center; padding:30px 0 10px 0'>
      <div style='font-size:0.95rem; color:#8b92a0; letter-spacing:0.1em'>我的總資產</div>
      <div style='font-size:3.5rem; font-weight:700; color:#e4e6eb; line-height:1.1; margin:10px 0;
                  font-feature-settings: "tnum"'>
        NT$ {grand_total:,.0f}
      </div>
      <div style='font-size:1.1rem; color:{chg_color}; font-weight:600'>
        {chg_sign}{daily_change:,.0f} ({chg_sign}{daily_pct:.2f}%) 今天
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 3 個快速按鈕 ──
    st.markdown("<div style='margin-top:20px'></div>", unsafe_allow_html=True)
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("🔍 找個股看分析", use_container_width=True, type="primary"):
            st.session_state["sidebar_target"] = "🔍 找個股"
            st.rerun()
    with b2:
        if st.button("💼 看我的持股", use_container_width=True):
            st.session_state["sidebar_target"] = "💼 我的持股"
            st.rerun()
    with b3:
        if st.button("📈 更新今天價格", use_container_width=True):
            st.session_state["sidebar_target"] = "💼 我的持股"
            st.session_state["jump_to_prices"] = True
            st.rerun()

    # 集中度警示(僅大於 30% 才顯示)
    alerts = []
    if tw_holdings.get("holdings") and tw_mv > 0:
        for h in tw_holdings["holdings"]:
            tk = h["ticker"]
            mv = h.get("shares", 0) * tw_prices.get(tk, 0)
            if mv > 0:
                pct = mv / tw_mv * 100
                if pct > 30:
                    alerts.append(f"⚠️ **{tk}** 占持股 **{pct:.0f}%**(超過 30%)— 風險集中")

    if alerts:
        st.divider()
        for a in alerts:
            st.warning(a)

    # ── 目標進度條(僅當有設目標才顯示)──
    goal = get_setting("goal_amount_twd", 0)
    if goal > 0:
        st.divider()
        st.markdown("### 🎯 我的目標")
        progress_pct = min(100, grand_total / goal * 100)
        remaining = max(0, goal - grand_total)
        col1, col2, col3 = st.columns(3)
        col1.metric("目標金額", f"NT$ {goal:,.0f}")
        col2.metric("已達成", f"{progress_pct:.1f}%")
        col3.metric("還差", f"NT$ {remaining:,.0f}")
        st.progress(progress_pct / 100)

        # 預估達成時間 (用最近 30 天平均成長率)
        history = load_json("snapshots", {"records": []})
        records = history.get("records", [])
        if len(records) >= 7:
            try:
                recent = records[-min(30, len(records)):]
                first = recent[0]["grand_total"]
                last = recent[-1]["grand_total"]
                days = (datetime.fromisoformat(recent[-1]["date"]) - datetime.fromisoformat(recent[0]["date"])).days or 1
                daily_growth = (last - first) / days
                if daily_growth > 0 and remaining > 0:
                    eta_days = remaining / daily_growth
                    eta_str = f"約 **{eta_days/365:.1f} 年** ({eta_days:.0f} 天)" if eta_days > 365 else f"約 **{eta_days:.0f} 天**"
                    st.caption(f"💡 以最近成長速度估,還需 {eta_str} 達成")
                elif daily_growth <= 0:
                    st.caption("💡 最近資產沒成長,沒辦法估達成時間")
            except Exception:
                pass

    # ── 資產走勢圖(已 load_json 過) ──
    if len(records) >= 2:
        st.divider()
        st.markdown("### 📈 資產走勢")
        time_range = st.radio(
            "時間範圍",
            ["7 天", "30 天", "90 天", "1 年", "全部"],
            horizontal=True, label_visibility="collapsed",
        )
        days_map = {"7 天": 7, "30 天": 30, "90 天": 90, "1 年": 365, "全部": 9999}
        n = days_map[time_range]
        sub = records[-n:] if n < 9999 else records

        chart_df = pd.DataFrame(sub)
        chart_df["date"] = pd.to_datetime(chart_df["date"])

        if HAS_PLOTLY:
            import plotly.graph_objects as go
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=chart_df["date"], y=chart_df["grand_total"],
                name="總資產", line=dict(color="#5eead4", width=3),
                fill='tozeroy', fillcolor='rgba(94, 234, 212, 0.1)',
            ))
            fig.add_trace(go.Scatter(
                x=chart_df["date"], y=chart_df["tw_total"],
                name="台股", line=dict(color="#60a5fa", width=2, dash='dot'),
            ))
            fig.update_layout(
                plot_bgcolor="#16181d", paper_bgcolor="#16181d",
                font=dict(color="#e4e6eb"),
                xaxis=dict(gridcolor="#2f343d"),
                yaxis=dict(gridcolor="#2f343d", tickformat=","),
                hovermode="x unified",
                height=350,
                margin=dict(l=10, r=10, t=20, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.line_chart(chart_df.set_index("date")[["grand_total", "tw_total"]])

        # 期間統計
        if len(sub) >= 2:
            first_val = sub[0]["grand_total"]
            last_val = sub[-1]["grand_total"]
            change = last_val - first_val
            change_pct = (last_val/first_val - 1) * 100 if first_val > 0 else 0
            c1, c2, c3 = st.columns(3)
            c1.metric(f"{time_range}前", f"NT$ {first_val:,.0f}")
            c2.metric("現在", f"NT$ {last_val:,.0f}",
                       f"{change:+,.0f} ({change_pct:+.1f}%)")
            c3.metric("最高", f"NT$ {max(r['grand_total'] for r in sub):,.0f}")
    # 配置圓餅圖
    st.divider()
    st.markdown("### 💼 資產分布")

    alloc = pd.DataFrame({
        "類別": ["台股股票", "台幣現金"],
        "金額 (NT$)": [tw_mv, tw_cash],
    })
    alloc["比例"] = alloc["金額 (NT$)"] / alloc["金額 (NT$)"].sum() * 100
    alloc = alloc[alloc["金額 (NT$)"] > 0]   # 隱藏 0 項

    col1, col2 = st.columns([1, 2])
    with col1:
        st.dataframe(
            alloc.style.format({"金額 (NT$)": "{:,.0f}", "比例": "{:.1f}%"}),
            use_container_width=True, hide_index=True,
        )
    with col2:
        if HAS_PLOTLY and not alloc.empty:
            fig = px.pie(alloc, values="金額 (NT$)", names="類別",
                         color_discrete_sequence=px.colors.qualitative.Set2)
            fig.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig, use_container_width=True)

    # 損益快覽 (台股) — 收合版
    if tw_holdings.get("holdings"):
        st.divider()
        with st.expander("📊 看各檔詳細損益", expanded=False):
            rows = []
            for h in tw_holdings["holdings"]:
                tk = h["ticker"]
                shares = h.get("shares", 0)
                cost = h.get("cost_incl_fee", h.get("cost", 0))
                cur = tw_prices.get(tk, 0)
                mv = shares * cur
                cost_total = shares * cost
                pnl = mv - cost_total
                pct = (cur/cost - 1) * 100 if cost > 0 else 0
                rows.append({
                    "代號": tk, "股數": shares, "成本": cost, "現價": cur,
                    "目前價值": mv, "賺賠": pnl, "%": pct,
                })
            df = pd.DataFrame(rows).sort_values("目前價值", ascending=False)

            def color_pnl(val):
                if isinstance(val, (int, float)):
                    if val > 0: return 'color: #16a34a; font-weight: bold'
                    elif val < 0: return 'color: #dc2626; font-weight: bold'
                return ''

            st.dataframe(
                df.style.format({
                    "成本": "{:.2f}", "現價": "{:.2f}",
                    "目前價值": "{:,.0f}", "賺賠": "{:+,.0f}", "%": "{:+.1f}%",
                }).map(color_pnl, subset=["賺賠", "%"]),
                use_container_width=True, hide_index=True,
            )

    disclaimer()


def page_tw_portfolio():
    st.title("🇹🇼 我的台股")
    tw = load_json("tw_holdings", {"cash_twd": 0, "holdings": []})

    # 現金區
    st.subheader("💴 銀行現金")
    cash = st.number_input(
        "可投資的台幣餘額 (NT$)",
        value=int(tw.get("cash_twd", 0)),
        step=1000, min_value=0,
        help="放在券商或銀行隨時可動的錢"
    )
    if cash != tw.get("cash_twd", 0):
        tw["cash_twd"] = cash
        save_json("tw_holdings", tw)
        st.toast(f"✅ 現金已更新", icon="💰")

    st.divider()
    st.subheader("📈 我的持股")

    holdings = tw.get("holdings", [])
    if holdings:
        df = pd.DataFrame(holdings)
        if "cost_incl_fee" not in df.columns:
            df["cost_incl_fee"] = df["cost"]
        display = df[["ticker", "shares", "cost_incl_fee"]].rename(columns={
            "ticker": "代號", "shares": "股數", "cost_incl_fee": "成本/股",
        })
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info("還沒有持股,下面表單加進去")

    st.divider()
    st.subheader("✏️ 新增 / 修改 / 刪除")

    with st.form("add_holding", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns([2, 2, 2, 2])
        new_ticker = col1.text_input("股票代號", placeholder="例如 0050",
                                       help="台股 4 位數字代號")
        new_shares = col2.number_input("股數", min_value=0, value=0, step=100,
                                         help="1 張 = 1000 股")
        new_cost = col3.number_input("每股買進成本", min_value=0.0, value=0.0,
                                       step=0.1, format="%.2f",
                                       help="不用算手續費,程式幫你加")
        action = col4.selectbox("動作", ["新增/更新", "刪除"])
        submitted = st.form_submit_button("送出", type="primary")
        if submitted and new_ticker:
            if action == "刪除":
                tw["holdings"] = [h for h in holdings if h["ticker"] != new_ticker]
                st.success(f"✅ 已刪除 {new_ticker}")
            else:
                existing = next((h for h in holdings if h["ticker"] == new_ticker), None)
                if existing:
                    existing["shares"] = new_shares
                    existing["cost"] = new_cost
                    existing["cost_incl_fee"] = round(new_cost * 1.001425, 4)
                    st.success(f"✅ 已更新 {new_ticker}")
                else:
                    holdings.append({
                        "ticker": new_ticker,
                        "shares": new_shares,
                        "cost": new_cost,
                        "cost_incl_fee": round(new_cost * 1.001425, 4),
                    })
                    tw["holdings"] = holdings
                    st.success(f"✅ 已新增 {new_ticker}")
            save_json("tw_holdings", tw)
            st.rerun()

    disclaimer()


def page_crypto():
    st.title("💎 我的加密貨幣")
    c = load_json("crypto_holdings", {})

    with st.form("crypto_form"):
        st.subheader("🟠 比特幣 BTC")
        col1, col2 = st.columns(2)
        btc_qty = col1.number_input(
            "BTC 數量",
            value=float(c.get("btc_qty", 0)),
            min_value=0.0, step=0.0001, format="%.6f",
            help="你錢包/交易所裡的 BTC 顆數"
        )
        btc_px = col2.number_input(
            "BTC 目前價格 USD",
            value=float(c.get("btc_px_usd", 0)),
            min_value=0.0, step=100.0,
            help="美元計價的單顆 BTC 價格,可去 CoinGecko 看"
        )
        avg_cost = st.number_input(
            "BTC 平均買進成本 USD (沒填先 0)",
            value=float(c.get("btc_avg_cost", 0)),
            min_value=0.0, step=100.0,
            help="用來算現在賺賠,如果你不在意可以不填"
        )

        st.divider()
        st.subheader("💵 美元穩定幣 (USDT)")
        col1, col2, col3 = st.columns(3)
        simple_earn = col1.number_input(
            "理財/活期錢包 USD",
            value=float(c.get("simple_earn_usd", 0)),
            min_value=0.0, step=10.0,
            help="放在 Simple Earn / Earn / 活儲 等生利息的"
        )
        futures = col2.number_input(
            "合約錢包 USD",
            value=float(c.get("futures_usd", 0)),
            min_value=0.0, step=10.0,
            help="保證金錢包(沒玩合約留 0)"
        )
        spot = col3.number_input(
            "現貨錢包 USD",
            value=float(c.get("spot_usd", 0)),
            min_value=0.0, step=10.0,
            help="主錢包的 USDT"
        )

        submitted = st.form_submit_button("💾 儲存", type="primary")
        if submitted:
            c.update({
                "btc_qty": btc_qty, "btc_px_usd": btc_px,
                "simple_earn_usd": simple_earn, "futures_usd": futures, "spot_usd": spot,
                "btc_avg_cost": avg_cost,
                "updated": datetime.now(TW).isoformat(timespec="seconds"),
            })
            save_json("crypto_holdings", c)
            st.success("✅ 已儲存")

    st.divider()

    # 顯示
    total = btc_qty * btc_px + simple_earn + futures + spot
    if total > 0:
        c1, c2 = st.columns(2)
        c1.metric("加密貨幣總值 USD", f"{total:,.2f}")
        c2.metric("BTC 部分", f"USD {btc_qty * btc_px:,.2f}")

        if avg_cost > 0 and btc_qty > 0:
            unreal = (btc_px - avg_cost) * btc_qty
            pct = (btc_px / avg_cost - 1) * 100
            color = "🟢" if unreal > 0 else "🔴"
            st.write(f"### {color} BTC 賺賠")
            st.write(f"未實現損益: **USD {unreal:+,.2f}** ({pct:+.1f}%)")

    disclaimer()


def page_prices():
    st.title("📈 更新最新價格")
    st.write("**手動輸入今天的收盤價,首頁的資產才會更新**")
    st.caption("💡 可以去券商 App 或財經網站看價,例如 Yahoo 股市 / 鉅亨網 / CoinGecko")

    tw_prices = load_json("tw_prices", {})
    tw_holdings = load_json("tw_holdings", {"holdings": []})
    held = tw_holdings.get("holdings", [])

    st.subheader("🇹🇼 台股報價")
    if not held:
        st.info("還沒有台股持倉,先去 **TW 股票** 頁加入")
    else:
        with st.form("tw_prices_form"):
            updates = {}
            for h in held:
                tk = h["ticker"]
                updates[tk] = st.number_input(
                    f"{tk} 收盤價 NT$",
                    value=float(tw_prices.get(tk, 0)),
                    min_value=0.0, step=0.1, format="%.2f",
                    key=f"px_{tk}"
                )
            if st.form_submit_button("💾 儲存台股報價", type="primary"):
                for tk, px in updates.items():
                    tw_prices[tk] = px
                tw_prices["_updated"] = datetime.now(TW).isoformat(timespec="seconds")
                save_json("tw_prices", tw_prices)
                take_snapshot()
                st.success("✅ 已儲存(資產也已記錄一筆 snapshot)")
        last_upd = tw_prices.get("_updated", "")
        if last_upd:
            st.caption(f"最後更新: {last_upd[:16]}")

    st.divider()
    st.subheader("🟠 比特幣報價")
    crypto = load_json("crypto_holdings", {})
    with st.form("btc_form"):
        new_btc = st.number_input(
            "BTC 現價 USD",
            value=float(crypto.get("btc_px_usd", 0)),
            min_value=0.0, step=100.0
        )
        if st.form_submit_button("💾 儲存 BTC 價格", type="primary"):
            crypto["btc_px_usd"] = new_btc
            crypto["btc_px_updated"] = datetime.now(TW).isoformat(timespec="seconds")
            save_json("crypto_holdings", crypto)
            take_snapshot()
            st.success(f"✅ BTC 已更新: USD {new_btc:,.0f}(已記錄 snapshot)")

    disclaimer()


def page_settings():
    st.title("🔧 設定")

    # ── AI 解讀方式說明 (不再需要 API key) ──
    st.subheader("🤖 AI 白話解讀")
    st.info("""
    **本 app 不接外部 AI API,改用「複製 prompt → 貼到 Claude 對話」模式。**

    步驟:
    1. 在「🩺 韭菜健檢」頁面查任何個股
    2. 點開「📋 複製健檢資料 prompt」展開區
    3. 按 code block 右上角 📋 一鍵複製
    4. 貼到任何 AI 對話(Claude / ChatGPT / 其他都可以)

    好處: 🎉 **0 API 成本** · 🧠 用最強推理模型 · 🔒 你掌控 AI 廠商
    """)

    st.divider()

    # ── 匯率(保留) ──
    st.subheader("💱 匯率")
    usd_twd = st.number_input(
        "美金/台幣 匯率",
        value=float(get_setting("usd_twd", 32.0)),
        min_value=20.0, max_value=50.0, step=0.1,
    )
    if st.button("儲存匯率"):
        set_setting("usd_twd", usd_twd)
        st.success(f"✅ 已儲存: 1 USD = {usd_twd} TWD")

    st.divider()
    st.subheader("資料備份")
    st.caption("所有資料存在 `app/user_data/` 資料夾,你可以直接複製整個資料夾備份")

    files = sorted(DATA_DIR.glob("*.json"))
    if files:
        for f in files:
            with st.expander(f"📄 {f.name}"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    st.json(data)
                    if st.button(f"⚠️ 清除 {f.name}", key=f"clr_{f.name}"):
                        f.unlink()
                        st.success(f"已刪除 {f.name}")
                        st.rerun()
                except Exception as e:
                    st.error(str(e))
    else:
        st.info("還沒有任何資料檔")

    disclaimer()


def render_market_thermo():
    """自動抓 TAIEX + MA200,顯示溫度計"""
    state = fetch_taiex_state()
    if not state:
        st.warning("⚠️ TAIEX 資料抓不到,可能網路問題")
        return

    cur, ma200 = state["value"], state["ma200"]
    if not ma200:
        st.info("⚠️ MA200 資料不足")
        return

    dist = (cur / ma200 - 1) * 100

    # 6 級判讀(純狀態描述,不指示動作)
    if dist > 40:
        level, color = "🔴 過熱", "#f43f5e"
        advice = "歷史相對高位(過去 10 年僅 5% 時間在此區間)"
    elif dist > 25:
        level, color = "🟠 偏熱", "#fb923c"
        advice = "牛市後段位置"
    elif dist > 5:
        level, color = "🟢 健康牛", "#34d399"
        advice = "接近長期均線(合理區間)"
    elif dist > -5:
        level, color = "🟡 盤整", "#fbbf24"
        advice = "均線附近"
    elif dist > -15:
        level, color = "🔵 偏空", "#60a5fa"
        advice = "低於長期均線"
    else:
        level, color = "🟣 大跌", "#a78bfa"
        advice = "顯著低於長期均線(歷史 70% 案例 1 年內漲回)"

    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown(f"""
        <div style='background:#1e2128; padding:24px; border-radius:12px;
                    border-left:4px solid {color}'>
          <div style='font-size:1.8rem; color:{color}; font-weight:700'>{level}</div>
          <div style='font-size:0.85rem; color:#8b92a0; margin-top:8px'>
            距 200 日均線 {dist:+.1f}%
          </div>
          <div style='font-size:1rem; color:#e4e6eb; margin-top:14px'>
            💡 {advice}
          </div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.metric("TAIEX 收盤", f"{cur:,.0f}",
                  f"5d {state['ret_5d']:+.1f}% / 20d {state['ret_20d']:+.1f}%")
        st.caption(f"📅 {state['date']} • 200日均 {ma200:,.0f}")


def page_market():
    st.title("📰 市場資訊")

    # ── 大盤溫度計(全自動) ──
    st.markdown("### 🌡️ 大盤溫度計")
    render_market_thermo()

    st.divider()

    # ── TradingView 嵌入 widget ──
    st.subheader("📊 即時走勢圖(嵌入)")
    st.caption("由 TradingView 提供的官方免費 widget,需要連網")

    tab1, tab2, tab3 = st.tabs(["🇹🇼 TAIEX", "🟠 BTC", "💵 USD/TWD"])

    with tab1:
        st.components.v1.html("""
        <div class="tradingview-widget-container">
          <div id="tv_taiex"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
          <script type="text/javascript">
          new TradingView.widget({
            "width": "100%", "height": 500,
            "symbol": "TWSE:TAIEX",
            "interval": "D", "timezone": "Asia/Taipei",
            "theme": "dark", "style": "1", "locale": "zh_TW",
            "toolbar_bg": "#1e2128", "enable_publishing": false,
            "container_id": "tv_taiex"
          });
          </script>
        </div>
        """, height=520)

    with tab2:
        st.components.v1.html("""
        <div class="tradingview-widget-container">
          <div id="tv_btc"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
          <script type="text/javascript">
          new TradingView.widget({
            "width": "100%", "height": 500,
            "symbol": "BINANCE:BTCUSDT",
            "interval": "D", "timezone": "Asia/Taipei",
            "theme": "dark", "style": "1", "locale": "zh_TW",
            "toolbar_bg": "#1e2128", "enable_publishing": false,
            "container_id": "tv_btc"
          });
          </script>
        </div>
        """, height=520)

    with tab3:
        st.components.v1.html("""
        <div class="tradingview-widget-container">
          <div id="tv_usdtwd"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
          <script type="text/javascript">
          new TradingView.widget({
            "width": "100%", "height": 500,
            "symbol": "FX_IDC:USDTWD",
            "interval": "D", "timezone": "Asia/Taipei",
            "theme": "dark", "style": "1", "locale": "zh_TW",
            "toolbar_bg": "#1e2128", "enable_publishing": false,
            "container_id": "tv_usdtwd"
          });
          </script>
        </div>
        """, height=520)

    st.divider()

    # ── 商品行情(黃金/油價/銀) ──
    st.subheader("⛏️ 商品行情")
    tab_gold, tab_oil, tab_silver = st.tabs(["🪙 黃金", "🛢️ 西德州原油", "🥈 白銀"])
    with tab_gold:
        st.components.v1.html("""
        <div class="tradingview-widget-container">
          <div id="tv_gold"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
          <script type="text/javascript">
          new TradingView.widget({
            "width": "100%", "height": 450, "symbol": "TVC:GOLD",
            "interval": "D", "timezone": "Asia/Taipei",
            "theme": "dark", "style": "1", "locale": "zh_TW",
            "toolbar_bg": "#1e2128", "container_id": "tv_gold"
          });
          </script>
        </div>
        """, height=470)
    with tab_oil:
        st.components.v1.html("""
        <div class="tradingview-widget-container">
          <div id="tv_oil"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
          <script type="text/javascript">
          new TradingView.widget({
            "width": "100%", "height": 450, "symbol": "TVC:USOIL",
            "interval": "D", "timezone": "Asia/Taipei",
            "theme": "dark", "style": "1", "locale": "zh_TW",
            "toolbar_bg": "#1e2128", "container_id": "tv_oil"
          });
          </script>
        </div>
        """, height=470)
    with tab_silver:
        st.components.v1.html("""
        <div class="tradingview-widget-container">
          <div id="tv_silver"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
          <script type="text/javascript">
          new TradingView.widget({
            "width": "100%", "height": 450, "symbol": "TVC:SILVER",
            "interval": "D", "timezone": "Asia/Taipei",
            "theme": "dark", "style": "1", "locale": "zh_TW",
            "toolbar_bg": "#1e2128", "container_id": "tv_silver"
          });
          </script>
        </div>
        """, height=470)

    st.divider()

    # ── 多空指標 (Fear & Greed) ──
    st.subheader("😱 多空情緒指標")
    st.caption("Alternative.me 提供的官方公開圖片,綠 = 貪婪,紅 = 恐懼")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**🪙 加密貨幣 Fear & Greed**")
        st.image("https://alternative.me/crypto/fear-and-greed-index.png",
                 caption="0=極度恐懼 / 100=極度貪婪",
                 use_container_width=True)
    with col_b:
        st.markdown("**🇺🇸 美股 VIX 恐慌指數**")
        st.components.v1.html("""
        <div class="tradingview-widget-container">
          <div id="tv_vix"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
          <script type="text/javascript">
          new TradingView.widget({
            "width": "100%", "height": 280, "symbol": "TVC:VIX",
            "interval": "D", "timezone": "Asia/Taipei",
            "theme": "dark", "style": "1", "locale": "zh_TW",
            "toolbar_bg": "#1e2128", "container_id": "tv_vix"
          });
          </script>
        </div>
        """, height=300)
        st.caption("VIX < 15 平靜 / 15-25 正常 / 25-35 緊張 / >35 恐慌")

    st.divider()

    # ── 經濟事件 calendar (TradingView) ──
    st.subheader("📅 重要經濟事件")
    st.caption("升降息 / 非農 / CPI 等公布時間")
    st.components.v1.html("""
    <div class="tradingview-widget-container">
      <div class="tradingview-widget-container__widget"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-events.js" async>
      {
        "colorTheme": "dark",
        "isTransparent": false,
        "width": "100%",
        "height": 450,
        "locale": "zh_TW",
        "importanceFilter": "0,1",
        "currencyFilter": "USD,EUR,JPY,GBP,CNY,TWD"
      }
      </script>
    </div>
    """, height=470)

    st.divider()

    # ── 我的觀察清單 ──
    st.subheader("📊 我的觀察清單")
    watchlist = load_json("watchlist", {"tickers": []})
    tickers = watchlist.get("tickers", [])

    # 顯示既有觀察 (chart preview)
    if tickers:
        # 提供 mini chart for each
        for tk_info in tickers:
            tk = tk_info["ticker"]
            note = tk_info.get("note", "")
            asset_type = tk_info.get("type", "tw")
            col1, col2 = st.columns([4, 1])
            with col1:
                label = f"**{tk}** — {note}" if note else f"**{tk}**"
                st.markdown(label)
            with col2:
                if st.button("❌", key=f"del_{tk}_{asset_type}",
                            help=f"刪除 {tk}"):
                    watchlist["tickers"] = [t for t in tickers if not (t["ticker"] == tk and t.get("type") == asset_type)]
                    save_json("watchlist", watchlist)
                    st.rerun()

            # Mini chart embed
            if asset_type == "tw":
                tv_sym = f"TWSE:{tk}"
            elif asset_type == "tpex":
                tv_sym = f"TPEX:{tk}"
            elif asset_type == "crypto":
                tv_sym = f"BINANCE:{tk}USDT"
            elif asset_type == "us":
                tv_sym = f"NASDAQ:{tk}"
            else:
                tv_sym = tk
            st.components.v1.html(f"""
            <div class="tradingview-widget-container">
              <div id="tv_wl_{tk}_{asset_type}"></div>
              <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
              <script type="text/javascript">
              new TradingView.widget({{
                "width": "100%", "height": 250,
                "symbol": "{tv_sym}", "interval": "D",
                "timezone": "Asia/Taipei",
                "theme": "dark", "style": "1", "locale": "zh_TW",
                "toolbar_bg": "#1e2128", "hide_top_toolbar": true,
                "container_id": "tv_wl_{tk}_{asset_type}"
              }});
              </script>
            </div>
            """, height=270)
    else:
        st.info("還沒有觀察的標的,下面表單加進去")

    # 新增 watchlist 表單
    with st.form("add_watchlist", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns([2, 1, 3, 1])
        new_tk = col1.text_input("代號", placeholder="例: 2330")
        new_type = col2.selectbox("類型",
                                    ["tw", "tpex", "crypto", "us"],
                                    format_func=lambda x: {
                                        "tw": "🇹🇼 上市", "tpex": "🇹🇼 上櫃",
                                        "crypto": "🪙 加密", "us": "🇺🇸 美股"
                                    }[x])
        new_note = col3.text_input("筆記(可選)", placeholder="例: 等回測 250 進場")
        submitted = col4.form_submit_button("➕ 加入", type="primary")
        if submitted and new_tk:
            tickers.append({"ticker": new_tk, "type": new_type, "note": new_note})
            watchlist["tickers"] = tickers
            save_json("watchlist", watchlist)
            st.success(f"已加入 {new_tk}")
            st.rerun()

    # ── 快速連結(只是 link,沒抓任何資料) ──
    st.subheader("🔗 快速連結(點開新分頁)")
    st.caption("只是把網址 list 出來,點了會去你瀏覽器開該網站。沒抓任何資料")

    link_groups = {
        "📰 台股新聞": [
            ("Yahoo 股市", "https://tw.stock.yahoo.com/"),
            ("鉅亨網台股", "https://www.cnyes.com/twstock/"),
            ("Anue 鉅亨", "https://www.cnyes.com/news/cat/tw_stock"),
            ("MoneyDJ", "https://www.moneydj.com/kmdj/news/newsreallist.aspx?index1=1"),
            ("經濟日報", "https://money.udn.com/money/cate/12017"),
        ],
        "📈 看盤工具": [
            ("Yahoo TAIEX", "https://tw.stock.yahoo.com/quote/%5ETWII"),
            ("TWSE 證交所", "https://www.twse.com.tw/zh/"),
            ("TPEX 櫃買中心", "https://www.tpex.org.tw/"),
            ("StockQ", "https://www.stockq.org/"),
            ("Goodinfo", "https://goodinfo.tw/"),
        ],
        "🪙 加密貨幣": [
            ("CoinGecko", "https://www.coingecko.com/zh-tw"),
            ("CoinMarketCap", "https://coinmarketcap.com/zh-tw/"),
            ("Coinglass", "https://www.coinglass.com/zh-tw"),
            ("Bitget 行情", "https://www.bitget.com/zh-TW/price"),
            ("區塊客", "https://blockcast.it/"),
        ],
        "🌍 國際": [
            ("TradingView", "https://tw.tradingview.com/"),
            ("Yahoo Finance", "https://finance.yahoo.com/"),
            ("Investing", "https://hk.investing.com/"),
            ("FRED 美聯儲資料", "https://fred.stlouisfed.org/"),
        ],
    }

    for group_name, links in link_groups.items():
        st.markdown(f"**{group_name}**")
        cols = st.columns(min(len(links), 5))
        for i, (name, url) in enumerate(links):
            cols[i % len(cols)].link_button(name, url, use_container_width=True)
        st.write("")

    disclaimer()


def page_goal():
    st.title("🎯 我的目標")
    st.caption("設一個讓自己有方向感的數字。記得設實際一點 ✨")

    cur_goal = get_setting("goal_amount_twd", 0)
    cur_name = get_setting("goal_name", "")

    with st.form("goal_form"):
        goal_name = st.text_input(
            "目標名稱",
            value=cur_name,
            placeholder="例如:退休基金 / 買房頭期款 / 寶寶教育金",
        )
        goal_amount = st.number_input(
            "目標金額 NT$",
            value=int(cur_goal),
            min_value=0, step=10000,
            help="先別管多久能達成,先想清楚目標數字",
        )
        if st.form_submit_button("💾 儲存目標", type="primary"):
            set_setting("goal_name", goal_name)
            set_setting("goal_amount_twd", goal_amount)
            st.success("✅ 已儲存。首頁會自動顯示進度條")

    # 顯示現況
    if cur_goal > 0:
        st.divider()
        st.subheader("📊 目前進度")
        tw_holdings = load_json("tw_holdings", {"cash_twd": 0, "holdings": []})
        tw_prices = load_json("tw_prices", {})
        crypto_holdings = load_json("crypto_holdings", {})
        usd_twd = get_setting("usd_twd", 32.0)

        tw_mv = sum(h.get("shares", 0) * tw_prices.get(h["ticker"], 0)
                    for h in tw_holdings.get("holdings", []))
        tw_cash = tw_holdings.get("cash_twd", 0)
        tw_total = tw_mv + tw_cash
        btc_qty = crypto_holdings.get("btc_qty", 0)
        btc_px = crypto_holdings.get("btc_px_usd", 0)
        crypto_total_usd = (btc_qty * btc_px + crypto_holdings.get("simple_earn_usd", 0)
                            + crypto_holdings.get("futures_usd", 0)
                            + crypto_holdings.get("spot_usd", 0))
        grand = tw_total + crypto_total_usd * usd_twd

        pct = min(100, grand / cur_goal * 100)
        st.progress(pct / 100)
        st.markdown(f"### {pct:.1f}% — NT$ {grand:,.0f} / NT$ {cur_goal:,.0f}")
        if grand < cur_goal:
            st.write(f"還差 **NT$ {cur_goal - grand:,.0f}** 達標")
        else:
            st.success(f"🎉 你已超過目標 NT$ {grand - cur_goal:,.0f}!")

        # 月存推估
        st.divider()
        st.subheader("💡 月存推估")
        col1, col2 = st.columns(2)
        with col1:
            monthly_save = st.number_input(
                "每月可存 NT$",
                value=10000, min_value=0, step=1000,
            )
        with col2:
            annual_return = st.number_input(
                "預期年化報酬率 %",
                value=7.0, min_value=0.0, max_value=30.0, step=0.5,
                help="保守 4% / 一般 7% / 積極 10%",
            )
        if monthly_save > 0:
            r = annual_return / 100 / 12
            target_remaining = cur_goal - grand
            if r > 0 and target_remaining > 0:
                # PMT formula reverse: months = log(1 + r*remaining/PMT) / log(1+r) — using future value of current + monthly
                # FV = PV(1+r)^n + PMT[(1+r)^n - 1]/r ... 解 n
                # 簡化:逐月模擬
                months = 0
                bal = grand
                while bal < cur_goal and months < 600:
                    bal = bal * (1 + r) + monthly_save
                    months += 1
                if months < 600:
                    years = months / 12
                    st.success(f"預估 **{months} 個月**({years:.1f} 年)達成 🎯")
                else:
                    st.warning("以這個速度可能要 50+ 年。考慮增加月存或提高報酬率假設")
            elif target_remaining <= 0:
                st.success("已達成,繼續存就是 bonus 🎁")

    disclaimer()


def page_dividend():
    st.title("📅 股利月行事曆")
    st.caption("記錄你領過或預期會領的股利(配息日 + 配息額)")

    div = load_json("dividends", {"records": []})
    records = div.get("records", [])

    # 新增表單
    with st.form("add_div", clear_on_submit=True):
        st.subheader("➕ 新增配息紀錄")
        col1, col2, col3, col4 = st.columns([2, 2, 2, 2])
        new_tk = col1.text_input("代號", placeholder="例: 0050")
        new_date = col2.date_input("配息日")
        new_per_share = col3.number_input("每股配多少 (NT$)",
                                            min_value=0.0, value=0.0, step=0.1, format="%.4f")
        new_shares = col4.number_input("當時持股數",
                                         min_value=0, value=0, step=100)
        new_type = st.selectbox("配發類型", ["現金股利", "股票股利"])
        submitted = st.form_submit_button("加入", type="primary")
        if submitted and new_tk:
            records.append({
                "ticker": new_tk,
                "date": new_date.isoformat(),
                "per_share": new_per_share,
                "shares": new_shares,
                "total": round(new_per_share * new_shares, 2),
                "type": new_type,
                "added_at": datetime.now(TW).isoformat(timespec="seconds"),
            })
            div["records"] = records
            save_json("dividends", div)
            st.success(f"✅ 已新增 {new_tk}")
            st.rerun()

    st.divider()

    if not records:
        st.info("還沒有紀錄。配息日來臨時手動加進去,以後就能看月度行事曆 / 年度總額")
        disclaimer()
        return

    # 顯示
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date", ascending=False)

    # 統計
    today = datetime.now(TW)
    this_year = df[df["date"].dt.year == today.year]
    last_year = df[df["date"].dt.year == today.year - 1]
    future = df[df["date"] > today.tz_localize(None) if df["date"].dt.tz is None else df["date"] > today]

    c1, c2, c3 = st.columns(3)
    c1.metric(f"今年累積", f"NT$ {this_year['total'].sum():,.0f}",
              f"共 {len(this_year)} 次")
    c2.metric(f"去年總額", f"NT$ {last_year['total'].sum():,.0f}",
              f"共 {len(last_year)} 次")
    c3.metric("未來預計", f"NT$ {future['total'].sum():,.0f}",
              f"共 {len(future)} 次")

    st.divider()

    # 月度 bar chart
    st.subheader("📊 月度配息")
    df["year_month"] = df["date"].dt.strftime("%Y-%m")
    monthly = df.groupby("year_month")["total"].sum().reset_index()
    if HAS_PLOTLY:
        fig = px.bar(monthly, x="year_month", y="total",
                      labels={"year_month": "年月", "total": "配息 NT$"},
                      color_discrete_sequence=["#5eead4"])
        fig.update_layout(
            plot_bgcolor="#16181d", paper_bgcolor="#16181d",
            font=dict(color="#e4e6eb"),
            xaxis=dict(gridcolor="#2f343d"),
            yaxis=dict(gridcolor="#2f343d", tickformat=","),
            height=300,
            margin=dict(l=10, r=10, t=20, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.bar_chart(monthly.set_index("year_month")["total"])

    st.divider()

    # 明細表
    st.subheader("📋 完整紀錄")
    show = df[["ticker", "date", "per_share", "shares", "total", "type"]].copy()
    show["date"] = show["date"].dt.strftime("%Y-%m-%d")
    show.columns = ["代號", "日期", "每股", "股數", "總額 NT$", "類型"]
    st.dataframe(
        show.style.format({"每股": "{:.4f}", "總額 NT$": "{:,.0f}"}),
        use_container_width=True, hide_index=True,
    )

    # 刪除
    with st.expander("🗑️ 刪除某筆"):
        if not df.empty:
            idx = st.number_input(
                "輸入要刪的「最舊起算第幾筆」(從 0 開始)",
                min_value=0, max_value=len(records)-1, value=0,
            )
            if st.button("確認刪除"):
                del records[idx]
                save_json("dividends", div)
                st.success("已刪除")
                st.rerun()

    disclaimer()


def page_simulator():
    st.title("🔮 What-if 模擬器")
    st.caption("假如我多買 / 多賣某檔,配置會怎變?(只是模擬,不會改你的持倉)")

    tw_holdings = load_json("tw_holdings", {"cash_twd": 0, "holdings": []})
    tw_prices = load_json("tw_prices", {})

    if not tw_holdings.get("holdings"):
        st.info("先去 **TW 股票** 加入持股,才能模擬")
        disclaimer()
        return

    # 計算目前狀態
    current_holdings = {h["ticker"]: dict(h) for h in tw_holdings["holdings"]}
    current_cash = tw_holdings.get("cash_twd", 0)

    st.subheader("📋 假設動作")
    st.caption("輸入你想模擬的買賣動作,可加多筆")

    if "sim_actions" not in st.session_state:
        st.session_state.sim_actions = []

    with st.form("sim_form", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns([2, 2, 2, 2])
        action = col1.selectbox("動作", ["買進", "賣出"])
        ticker = col2.text_input("代號", placeholder="0050")
        shares = col3.number_input("股數", min_value=0, value=0, step=100)
        price = col4.number_input("價格 NT$", min_value=0.0, value=0.0, step=0.1, format="%.2f")
        if st.form_submit_button("➕ 加進模擬清單"):
            if ticker and shares > 0 and price > 0:
                st.session_state.sim_actions.append({
                    "action": action, "ticker": ticker,
                    "shares": shares, "price": price,
                })

    if st.session_state.sim_actions:
        st.write("**目前模擬清單:**")
        for i, a in enumerate(st.session_state.sim_actions):
            col1, col2 = st.columns([5, 1])
            col1.write(f"  {i+1}. {a['action']} {a['ticker']} × {a['shares']} 股 @ NT${a['price']:.2f}"
                       f" = NT${a['shares']*a['price']:,.0f}")
            if col2.button("❌", key=f"rm_sim_{i}"):
                st.session_state.sim_actions.pop(i)
                st.rerun()

        if st.button("🔄 清空模擬"):
            st.session_state.sim_actions = []
            st.rerun()

    if not st.session_state.sim_actions:
        st.info("加幾筆動作,下面會顯示模擬結果")
        disclaimer()
        return

    # 套用模擬
    sim_holdings = {tk: dict(h) for tk, h in current_holdings.items()}
    sim_cash = current_cash

    for a in st.session_state.sim_actions:
        tk = a["ticker"]
        sh = a["shares"]
        px = a["price"]
        if a["action"] == "買進":
            sim_cash -= sh * px * 1.001425   # 含手續費
            if tk in sim_holdings:
                old_total_cost = sim_holdings[tk].get("shares", 0) * sim_holdings[tk].get("cost_incl_fee", 0)
                new_total_cost = old_total_cost + sh * px * 1.001425
                new_shares = sim_holdings[tk]["shares"] + sh
                sim_holdings[tk]["shares"] = new_shares
                sim_holdings[tk]["cost_incl_fee"] = new_total_cost / new_shares if new_shares > 0 else 0
            else:
                sim_holdings[tk] = {
                    "ticker": tk, "shares": sh,
                    "cost": px, "cost_incl_fee": px * 1.001425,
                }
        else:  # 賣出
            if tk in sim_holdings:
                cost = sim_holdings[tk].get("cost_incl_fee", 0)
                sim_holdings[tk]["shares"] -= sh
                sim_cash += sh * px * (1 - 0.001425 - 0.003)  # 賣出含費 + 證交稅
                if sim_holdings[tk]["shares"] <= 0:
                    del sim_holdings[tk]

    # 比較
    def calc_state(holdings, cash, prices):
        mv = sum(h["shares"] * prices.get(h.get("ticker", t), 0)
                 for t, h in holdings.items())
        for t, h in holdings.items():
            if "ticker" not in h: h["ticker"] = t
        return mv + cash, mv

    cur_total, cur_mv = calc_state(current_holdings, current_cash, tw_prices)
    sim_total, sim_mv = calc_state(sim_holdings, sim_cash, tw_prices)

    st.divider()
    st.subheader("📊 模擬結果")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**目前**")
        st.metric("總資產", f"NT$ {cur_total:,.0f}")
        st.metric("現金", f"NT$ {current_cash:,.0f}")
        st.metric("股票市值", f"NT$ {cur_mv:,.0f}")
    with col2:
        st.markdown("**模擬後**")
        st.metric("總資產", f"NT$ {sim_total:,.0f}",
                   f"{sim_total - cur_total:+,.0f}")
        st.metric("現金", f"NT$ {sim_cash:,.0f}",
                   f"{sim_cash - current_cash:+,.0f}")
        st.metric("股票市值", f"NT$ {sim_mv:,.0f}",
                   f"{sim_mv - cur_mv:+,.0f}")

    # 持股集中度比較
    st.divider()
    st.subheader("📊 持股配置變化")
    rows = []
    all_tickers = set(current_holdings.keys()) | set(sim_holdings.keys())
    for tk in all_tickers:
        cur_sh = current_holdings.get(tk, {}).get("shares", 0)
        sim_sh = sim_holdings.get(tk, {}).get("shares", 0)
        price = tw_prices.get(tk, 0)
        cur_mv_t = cur_sh * price
        sim_mv_t = sim_sh * price
        rows.append({
            "代號": tk,
            "目前股數": cur_sh, "目前市值": cur_mv_t,
            "目前占比": cur_mv_t / cur_mv * 100 if cur_mv > 0 else 0,
            "模擬股數": sim_sh, "模擬市值": sim_mv_t,
            "模擬占比": sim_mv_t / sim_mv * 100 if sim_mv > 0 else 0,
        })
    df = pd.DataFrame(rows).sort_values("模擬占比", ascending=False)
    df["占比變化"] = df["模擬占比"] - df["目前占比"]
    st.dataframe(
        df.style.format({
            "目前市值": "{:,.0f}", "模擬市值": "{:,.0f}",
            "目前占比": "{:.1f}%", "模擬占比": "{:.1f}%",
            "占比變化": "{:+.1f}pp",
        }),
        use_container_width=True, hide_index=True,
    )

    # 風險警示
    over_concentrated = df[df["模擬占比"] > 30]
    if not over_concentrated.empty:
        for _, r in over_concentrated.iterrows():
            st.warning(f"⚠️ 模擬後 **{r['代號']}** 占 **{r['模擬占比']:.0f}%**(超過 30% 集中度警示)")

    if sim_cash < 0:
        st.error(f"❌ 現金不夠!模擬後會差 NT$ {-sim_cash:,.0f}")

    disclaimer()


# ───────────────────────────────────────────────────────
# TW 股票中心 — 三竹級看盤
# ───────────────────────────────────────────────────────
INVEST_ROOT = Path(__file__).resolve().parents[1]
STOCK_INFO_PATH = INVEST_ROOT / "data" / "cache" / "finmind" / "extras" / "stock_info.parquet"
IPO_PATH = INVEST_ROOT / "data" / "cache" / "finmind" / "ipo" / "ipo_list.parquet"
TW_OHLCV_CACHE = INVEST_ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
FINMIND_CACHE = INVEST_ROOT / "data" / "cache" / "finmind" / "finmind"


@st.cache_data(ttl=86400)  # 1 天 — ticker 對照本來就 daily 級
def load_ticker_map():
    """完整 ticker → name 對照(~4000 檔)。優先用 stock_info,fallback IPO list。
    同 ticker 多筆時優先序: twse > tpex > emerging
    (避免上櫃股被 emerging 記錄覆蓋而看不見)。"""
    m = {}
    _PRIORITY = {"twse": 0, "tpex": 1, "emerging": 2}
    # 1. 完整 stock_info
    if STOCK_INFO_PATH.exists():
        try:
            df = pd.read_parquet(STOCK_INFO_PATH)
            for _, r in df.iterrows():
                tk = str(r["stock_id"])
                new_type = r["type"]
                existing = m.get(tk)
                # 既有的優先級較高(數字較小) → 跳過
                if existing and _PRIORITY.get(existing["type"], 99) <= _PRIORITY.get(new_type, 99):
                    continue
                m[tk] = {
                    "name": r["stock_name"],
                    "industry": r["industry_category"] or "—",
                    "type": new_type,
                }
        except Exception:
            pass
    # 2. IPO list 補充新上市
    if IPO_PATH.exists():
        try:
            df_ipo = pd.read_parquet(IPO_PATH)
            for _, r in df_ipo.iterrows():
                tk = str(r["stock_id"])
                if tk not in m:
                    m[tk] = {
                        "name": r["stock_name"],
                        "industry": r["industry_category"] or "—",
                        "type": r["type"],
                    }
        except Exception:
            pass
    return m


@st.cache_data(ttl=1800)  # 30 分鐘
def fetch_stock_news(ticker: str, ticker_name: str, max_n: int = 12):
    """從 Google News RSS 抓個股新聞(完全合法,Google 官方 RSS)."""
    try:
        import feedparser
        from urllib.parse import quote
        # Google News RSS — 全合法,Google 官方提供 syndication
        query = quote(f"{ticker} {ticker_name}")
        url = f"https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        feed = feedparser.parse(url)
        items = []
        for e in feed.entries[:max_n]:
            # source 從 title 結尾「 - source」抽出
            title = e.get("title", "")
            source = ""
            if " - " in title:
                source = title.split(" - ")[-1]
                title_clean = title.rsplit(" - ", 1)[0]
            else:
                title_clean = title
            items.append({
                "title": title_clean,
                "source": source,
                "link": e.get("link", ""),
                "published": e.get("published", ""),
            })
        return items
    except Exception as e:
        return []


@st.cache_data(ttl=3600)
def fetch_taiex_state():
    """自動抓 TAIEX 收盤 + 200 日均線(yfinance)."""
    try:
        import yfinance as yf
        t = yf.Ticker("^TWII")
        h = t.history(period="1y", auto_adjust=False)
        if h.empty:
            return None
        last_close = float(h["Close"].iloc[-1])
        last_date = h.index[-1].strftime("%Y-%m-%d")
        ma200 = float(h["Close"].tail(200).mean()) if len(h) >= 200 else None
        # 計算 5d / 20d 變動
        ret_5d = (last_close / h["Close"].iloc[-6] - 1) * 100 if len(h) > 5 else 0
        ret_20d = (last_close / h["Close"].iloc[-21] - 1) * 100 if len(h) > 20 else 0
        return {
            "value": last_close, "date": last_date,
            "ma200": ma200, "ret_5d": ret_5d, "ret_20d": ret_20d,
        }
    except Exception:
        return None


def fetch_yfinance_quote(ticker: str):
    """前端 wrapper:把 time bucket 拼進 cache key,讓盤前/盤中/盤後自動失效。"""
    return _fetch_yfinance_quote_bucketed(ticker, _time_bucket())


@st.cache_data(ttl=86400, show_spinner=False)
def _ranking_batch_fetch(tickers: tuple, bucket: str) -> list[dict]:
    """一次 yfinance.download() N 檔(parallel)做排行榜用 — 比一檔一檔快 10 倍。
    回傳:[{代號, 收盤, 漲跌%, 成交量}, ...](產業/名稱由 caller 後補)"""
    try:
        import yfinance as yf
        # 同時加 .TW 跟 .TWO 後綴一起抓,免得錯過上櫃
        symbol_map = {}  # yf_sym → 原 ticker
        yf_symbols = []
        for tk in tickers:
            for suf in [".TW", ".TWO"]:
                yf_symbols.append(f"{tk}{suf}")
                symbol_map[f"{tk}{suf}"] = tk
        # batch download 5 日,避免單 ticker 的 noise
        df = yf.download(" ".join(yf_symbols), period="5d",
                          group_by="ticker", auto_adjust=False,
                          progress=False, threads=True)
        if df.empty:
            return []
        results = []
        seen = set()
        for yf_sym in yf_symbols:
            tk = symbol_map[yf_sym]
            if tk in seen:
                continue
            try:
                sub = df[yf_sym].dropna()
                if len(sub) < 2:
                    continue
                last = sub.iloc[-1]
                prev = sub.iloc[-2]
                price = float(last["Close"])
                prev_close = float(prev["Close"])
                if price <= 0 or prev_close <= 0:
                    continue
                chg_pct = (price / prev_close - 1) * 100
                results.append({
                    "代號": tk,
                    "收盤": price,
                    "漲跌%": chg_pct,
                    "成交量": int(last.get("Volume", 0)) if not pd.isna(last.get("Volume", 0)) else 0,
                })
                seen.add(tk)
            except Exception:
                continue
        return results
    except Exception as e:
        print(f"[ranking batch] {e}")
        return []


@st.cache_data(ttl=86400, show_spinner=False)  # bucket 變就會 miss,ttl 只是上限
def _fetch_yfinance_quote_bucketed(ticker: str, bucket: str):
    """Return latest close + 5d series from yfinance."""
    try:
        import yfinance as yf
        for suffix in [".TW", ".TWO"]:
            t = yf.Ticker(f"{ticker}{suffix}")
            h = t.history(period="5d", auto_adjust=False)
            if not h.empty:
                return {
                    "price": float(h["Close"].iloc[-1]),
                    "open": float(h["Open"].iloc[-1]),
                    "high": float(h["High"].iloc[-1]),
                    "low": float(h["Low"].iloc[-1]),
                    "volume": int(h["Volume"].iloc[-1]),
                    "prev_close": float(h["Close"].iloc[-2]) if len(h) >= 2 else float(h["Close"].iloc[-1]),
                    "suffix": suffix,
                    "asof": h.index[-1].strftime("%Y-%m-%d"),
                }
    except Exception:
        return None
    return None


@st.cache_data(ttl=14400)  # 4h — OHLCV daily 級
def load_local_ohlcv(ticker: str, days: int = 250):
    """Load OHLCV from local cache,fallback yfinance live(部署環境用)."""
    p = TW_OHLCV_CACHE / f"{ticker}.parquet"
    if p.exists():
        try:
            df = pd.read_parquet(p)
            df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date").tail(days).reset_index(drop=True)
        except Exception:
            pass
    # ── Fallback: yfinance live(無 cache 環境,例如 Streamlit Cloud)──
    return _yf_ohlcv_fallback(ticker, days)


def _yf_ohlcv_fallback(ticker: str, days: int = 250):
    return _yf_ohlcv_fallback_bucketed(ticker, days, _time_bucket())


@st.cache_data(ttl=86400, show_spinner=False)
def _yf_ohlcv_fallback_bucketed(ticker: str, days: int, bucket: str):
    """yfinance live OHLCV → 統一回傳跟本地 parquet 同 shape。
    用於部署環境沒 cache parquet 時。15 分鐘 cache。"""
    try:
        import yfinance as yf
        # period 推估:days=250 → 1y, 50 → 3mo, etc.
        period = "2y" if days > 300 else "1y" if days > 100 else "3mo" if days > 40 else "1mo"
        for suffix in [".TW", ".TWO"]:
            t = yf.Ticker(f"{ticker}{suffix}")
            h = t.history(period=period, auto_adjust=False)
            if h.empty:
                continue
            df = pd.DataFrame({
                "date": pd.to_datetime(h.index).tz_localize(None),
                "open": h["Open"].astype(float),
                "high": h["High"].astype(float),
                "low": h["Low"].astype(float),
                "close": h["Close"].astype(float),
                "volume": h["Volume"].astype(float),
            }).reset_index(drop=True)
            return df.tail(days).reset_index(drop=True)
    except Exception:
        pass
    return None


@st.cache_data(ttl=86400)  # 1 天 — FinMind 月營收/法人/PER 都是 daily 級
def load_finmind_for_ticker(ticker: str, data_type: str):
    """Try local parquet cache,fallback FinMind live API(部署環境用)."""
    p = FINMIND_CACHE / f"{data_type}_{ticker}.parquet"
    if p.exists():
        try:
            return pd.read_parquet(p)
        except Exception:
            pass
    # ── Fallback: FinMind live API(無 cache,例如 Streamlit Cloud)──
    return _finmind_live_fetch(data_type, ticker)


@st.cache_data(ttl=86400, show_spinner=False)  # 1 天
def _finmind_live_fetch(data_type: str, ticker: str):
    """FinMind v4 API live fetch — 用使用者的 token。1h cache。
    支援:MonthRevenue / InstitutionalInvestorsBuySell / PER /
          HoldingSharesPer / FinancialStatements / MarginPurchaseShortSale 等"""
    try:
        import requests as _rq
        token = os.environ.get("FINMIND_TOKEN", "").strip()
        try:
            if not token:
                token = (st.secrets.get("FINMIND_TOKEN", "") or "").strip()
        except Exception:
            pass
        # 推估 date range — 月營收 / 財報抓 24 個月,日資料抓 90 天
        from datetime import date as _dt_f, timedelta as _td
        if "Month" in data_type or "Financial" in data_type or "PER" in data_type:
            start_date = (_dt_f.today() - _td(days=730)).isoformat()
        else:
            start_date = (_dt_f.today() - _td(days=90)).isoformat()
        params = {
            "dataset": data_type,
            "data_id": ticker,
            "start_date": start_date,
        }
        if token:
            params["token"] = token
        r = _rq.get("https://api.finmindtrade.com/api/v4/data",
                     params=params, timeout=15)
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("status") != 200 or not j.get("data"):
            return None
        df = pd.DataFrame(j["data"])
        if df.empty:
            return None
        return df
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_govbank_live(days: int = 30) -> pd.DataFrame | None:
    """FinMind v4:八大行庫買賣超(全市場 / per-day)。"""
    try:
        import requests as _rq
        from datetime import date as _dt_g, timedelta as _td
        token = os.environ.get("FINMIND_TOKEN", "").strip()
        try:
            if not token:
                token = (st.secrets.get("FINMIND_TOKEN", "") or "").strip()
        except Exception:
            pass
        start_date = (_dt_g.today() - _td(days=days)).isoformat()
        params = {
            "dataset": "TaiwanStockGovernmentBankBuySell",
            "start_date": start_date,
        }
        if token:
            params["token"] = token
        r = _rq.get("https://api.finmindtrade.com/api/v4/data",
                     params=params, timeout=15)
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("status") != 200 or not j.get("data"):
            return None
        df = pd.DataFrame(j["data"])
        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"[_fetch_govbank_live] {e}")
        return None


@st.cache_data(ttl=86400, show_spinner=False)  # 1 天
def _fetch_inst_total_live(days: int = 30) -> pd.DataFrame | None:
    """FinMind v4:全市場三大法人總計(per day)。
    回傳 DataFrame with columns: date / 外資 / 投信 / 自營(單位 = 張)"""
    try:
        import requests as _rq
        from datetime import date as _dt_f, timedelta as _td
        token = os.environ.get("FINMIND_TOKEN", "").strip()
        try:
            if not token:
                token = (st.secrets.get("FINMIND_TOKEN", "") or "").strip()
        except Exception:
            pass
        start_date = (_dt_f.today() - _td(days=days)).isoformat()
        params = {
            "dataset": "TaiwanStockTotalInstitutionalInvestors",
            "start_date": start_date,
        }
        if token:
            params["token"] = token
        r = _rq.get("https://api.finmindtrade.com/api/v4/data",
                     params=params, timeout=15)
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("status") != 200 or not j.get("data"):
            return None
        df = pd.DataFrame(j["data"])
        if df.empty or "date" not in df.columns or "name" not in df.columns:
            return None
        df["net"] = (df.get("buy", 0).astype(float) - df.get("sell", 0).astype(float)) / 1000  # 股→張
        pivot = (df.pivot_table(index="date", columns="name", values="net",
                                  aggfunc="sum", fill_value=0)
                    .reset_index())
        pivot.columns.name = None
        out = pd.DataFrame({
            "date": pd.to_datetime(pivot["date"]),
            "外資": pivot.get("Foreign_Investor", 0).astype(int),
            "投信": pivot.get("Investment_Trust", 0).astype(int),
            "自營": (pivot.get("Dealer_self", 0) + pivot.get("Dealer_Hedging", 0)).astype(int),
        })
        return out.sort_values("date").tail(20).reset_index(drop=True)
    except Exception as e:
        print(f"[_fetch_inst_total_live] {e}")
        return None


@st.cache_data(ttl=86400)
def fetch_balance_sheet(ticker: str):
    """Live fetch FinMind BalanceSheet — 24h cache.免 token,免費."""
    try:
        import requests
        r = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={"dataset": "TaiwanStockBalanceSheet",
                    "data_id": ticker, "start_date": "2023-01-01"},
            timeout=12,
        )
        data = r.json()
        if data.get("status") == 200 and data.get("data"):
            df = pd.DataFrame(data["data"])
            df["date"] = pd.to_datetime(df["date"])
            return df
    except Exception:
        pass
    return None


@st.cache_data(ttl=86400)
def fetch_full_financial_statements(ticker: str):
    """Live fetch 完整損益表 (Revenue / GrossProfit / IncomeAfterTaxes 等)。"""
    try:
        import requests
        r = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={"dataset": "TaiwanStockFinancialStatements",
                    "data_id": ticker, "start_date": "2023-01-01"},
            timeout=12,
        )
        data = r.json()
        if data.get("status") == 200 and data.get("data"):
            df = pd.DataFrame(data["data"])
            df["date"] = pd.to_datetime(df["date"])
            return df
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# Gemini 2.5 Flash 接口(免費 tier:1500 RPD / 1M tokens/day)
# ──────────────────────────────────────────────
def _get_gemini_key() -> str:
    """從 env 或 settings.json 取 Gemini API key."""
    import os
    return os.environ.get("GEMINI_API_KEY", "") or get_setting("gemini_api_key", "")


@st.cache_data(ttl=86400, show_spinner=False)
def _gemini_call_cached(prompt: str, cache_key: str = "", key_hash: str = ""):
    """Gemini REST API — 用 URL param 傳 key 避開 latin-1 header bug。
    只 cache 成功結果,失敗 raise(不入 cache)。"""
    import urllib.request, urllib.parse, json as _j, os as _os
    key = (_os.environ.get("GEMINI_API_KEY", "") or get_setting("gemini_api_key", "")).strip()
    if not key:
        raise RuntimeError("未設定智能 API key(去 ❓ 關於 tab 加 key,或設 config/.env GEMINI_API_KEY)")
    # 強制 key 為純 ASCII(防 BOM / 中文標點)
    key_ascii = key.encode("ascii", "ignore").decode("ascii")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={urllib.parse.quote(key_ascii, safe='')}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 4096},
    }
    body_bytes = _j.dumps(payload, ensure_ascii=False).encode("utf-8")
    # 用 urllib (純 stdlib) — 避開 requests 在 Windows 中文系統可能的 latin-1 header 轉換
    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            data = _j.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            err_data = _j.loads(err_body)
            err_msg = err_data.get("error", {}).get("message", err_body[:200])
        except Exception:
            err_msg = str(e)
        raise RuntimeError(f"智能 API 錯誤 [{e.code}]: {err_msg}")
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"智能 API 沒回傳結果: {str(data)[:200]}")
    text_parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in text_parts)
    return text


def gemini_call(prompt: str, cache_key: str = ""):
    """公開接口 — 回傳 (text, error_msg)。失敗不入 cache。"""
    import hashlib
    key = (os.environ.get("GEMINI_API_KEY", "") or get_setting("gemini_api_key", "")).strip()
    key_hash = hashlib.md5(key.encode()).hexdigest()[:8] if key else "nokey"
    try:
        text = _gemini_call_cached(prompt, cache_key, key_hash)
        return text, None
    except Exception as e:
        return None, str(e)


# ──────────────────────────────────────────────
# 共用 file cache(所有用戶共享當天結果,省 API 配額)
# ──────────────────────────────────────────────
_AI_CACHE_DIR = ROOT.parent / "data" / "ai_cache"


def shared_ai_call(prompt: str, cache_key: str, time_frame: str = ""):
    """共享 file cache — 同 cache_key + 同日所有用戶共用結果。
    回傳 (text, error_msg, from_cache_bool)。"""
    import hashlib
    from datetime import date as _d
    today = _d.today().isoformat()

    # 算 cache 檔名 (cache_key + time_frame + 日期)
    raw_key = f"{cache_key}:{time_frame}:{today}"
    file_key = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
    _AI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _AI_CACHE_DIR / f"{file_key}.json"

    # 讀 cache
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("date") == today and data.get("text"):
                return data["text"], None, True
        except Exception:
            pass

    # 沒命中 → 打 API
    text, err = gemini_call(prompt, cache_key)
    if text:
        try:
            cache_file.write_text(json.dumps({
                "text": text, "date": today, "frame": time_frame,
                "cache_key": cache_key,
                "ts": datetime.now(TW).isoformat(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return text, err, False


# ──────────────────────────────────────────────
# 共用 AI 渲染 helper(時間框架 + 用詞 + 生成 + 顯示)
# ──────────────────────────────────────────────
FRAME_FOCUS = {
    "short": "請重點放在短期(1-4 週):技術指標(KD/RSI/布林)、單日量價、近 5 日資金動向。長期基本面只提一句。",
    "mid": "請重點放在中期(1-3 個月):月營收 YoY 趨勢、20 日法人佈局、距 60 日均線。技術面與長期基本面各提一句。",
    "long": "請重點放在長期(6 個月-1 年):基本面(月營收連續 6 期 / EPS / 殖利率)、體質掃描 4 維度、200 日均線。技術面只提一句。",
}
TONE_FOCUS = {
    "pro": "用詞:專業金融術語、數據驅動、簡潔精準。可使用 RSI / KD / OBV / Beta / 估值 etc 行話。",
    "casual": "用詞:親民有比喻、易理解但不失準確。少用術語,多用例子。",
    "simple": "用詞:全白話、像跟小學生解釋、絕對不用 RSI / KD 這類縮寫,用「強弱指標」「均線」描述。",
}
FRAME_LABEL = {"short": "🕐 短期(1-4 週)", "mid": "🗓️ 中期(1-3 個月)", "long": "📅 長期(6 個月-1 年)"}
TONE_LABEL = {"pro": "👔 專業", "casual": "💬 親民", "simple": "👶 白話"}


def render_ai_options(ss_prefix: str):
    """顯示時間框架 + 用詞 radio,回傳 (frame, tone) 字串。"""
    fc1, fc2 = st.columns(2)
    with fc1:
        frame = st.radio(
            "分析時間框架",
            options=list(FRAME_LABEL.keys()),
            format_func=lambda x: FRAME_LABEL[x],
            key=f"{ss_prefix}_frame",
            horizontal=False,
        )
    with fc2:
        tone = st.radio(
            "用詞程度",
            options=list(TONE_LABEL.keys()),
            format_func=lambda x: TONE_LABEL[x],
            key=f"{ss_prefix}_tone",
            horizontal=False,
        )
    return frame, tone


def render_ai_section(prompt_base: str, cache_key: str, ss_prefix: str,
                       button_label: str = "🔍 查看智能解讀",
                       no_key_hint: str = "去「❓ 關於」加智能 key 即可一鍵自動分析",
                       show_options: bool = True):
    """完整渲染:選項 + 生成按鈕 + 結果框 + caption。

    show_options=False 時不顯示 frame/tone radio(用 default short+casual)。"""
    from streamlit_extras.stylable_container import stylable_container

    if show_options:
        frame, tone = render_ai_options(ss_prefix)
    else:
        frame, tone = "mid", "casual"

    prompt_full = prompt_base + f"\n【分析框架】{FRAME_FOCUS[frame]}\n【{TONE_FOCUS[tone]}】\n"
    ss_result = f"{ss_prefix}_result_{frame}_{tone}"
    ss_err = f"{ss_prefix}_err_{frame}_{tone}"
    ss_badge = f"{ss_prefix}_badge_{frame}_{tone}"

    if not _get_gemini_key():
        st.info(f"💡 {no_key_hint}")
        return

    if st.button(button_label,
                   key=f"{ss_prefix}_btn_{frame}_{tone}",
                   type="primary", use_container_width=True):
        with st.spinner("分析中..."):
            r, e, from_cache = shared_ai_call(prompt_full, cache_key, f"{frame}_{tone}")
        if r:
            st.session_state[ss_result] = r
            st.session_state[ss_badge] = ("💾 來自共享 cache" if from_cache
                                            else "🆕 剛產生(已存共享 cache)")
            st.session_state.pop(ss_err, None)
        elif e:
            st.session_state[ss_err] = e

    if st.session_state.get(ss_result):
        with stylable_container(
            key=f"{ss_prefix}_box_{frame}_{tone}",
            css_styles="""
                > div {
                    background: linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);
                    padding: 18px 22px;
                    border-radius: 12px;
                    border-left: 4px solid #14b8a6;
                    margin-top: 8px;
                }
            """,
        ):
            st.markdown(st.session_state[ss_result])
        badge = st.session_state.get(ss_badge, "")
        st.caption(f"✨ 智能判讀 · {badge} · 純客觀,不構成投資建議")
    elif st.session_state.get(ss_err):
        st.error(st.session_state[ss_err])


# ──────────────────────────────────────────────
# 真 alpha 訊號偵測器(based on memory 驗證過的策略)
# ──────────────────────────────────────────────
def fetch_multi_market_data():
    return _fetch_multi_market_data_bucketed(_time_bucket())


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_multi_market_data_bucketed(bucket: str):
    """抓多市場資料(yfinance,4 小時 cache)。"""
    import yfinance as yf
    GROUPS = {
        "🇺🇸 美股 ETF": [
            ("SPY", "S&P 500"),
            ("QQQ", "NASDAQ 100"),
            ("DIA", "道瓊"),
            ("IWM", "羅素 2000"),
            ("^SOX", "費半"),
            ("^VIX", "VIX 恐慌指數"),
        ],
        "🦄 美股巨頭(Mag 7)": [
            ("NVDA", "Nvidia"),
            ("AAPL", "Apple"),
            ("MSFT", "Microsoft"),
            ("GOOGL", "Google"),
            ("META", "Meta"),
            ("AMZN", "Amazon"),
            ("TSLA", "Tesla"),
        ],
        "🌏 亞洲市場": [
            ("0700.HK", "騰訊"),
            ("BABA", "阿里巴巴"),
            ("EWY", "韓國 ETF"),
            ("DXJ", "日本 ETF"),
            ("INDA", "印度 ETF"),
            ("EWZ", "巴西 ETF"),
        ],
        "₿ 加密貨幣": [
            ("BTC-USD", "比特幣"),
            ("ETH-USD", "以太坊"),
            ("BNB-USD", "幣安幣"),
            ("SOL-USD", "Solana"),
        ],
        "🥇 商品 / 匯率": [
            ("GC=F", "黃金"),
            ("SI=F", "白銀"),
            ("CL=F", "WTI 原油"),
            ("DX-Y.NYB", "美元指數"),
            ("TWD=X", "USD/TWD"),
            ("JPY=X", "USD/JPY"),
        ],
    }
    result = {}
    for group_name, items in GROUPS.items():
        result[group_name] = []
        for sym, label in items:
            try:
                t = yf.Ticker(sym)
                h = t.history(period="35d", auto_adjust=False)
                if h.empty or len(h) < 2: continue
                p = float(h["Close"].iloc[-1])
                prev = float(h["Close"].iloc[-2])
                chg_pct = (p / prev - 1) * 100 if prev > 0 else 0
                m30 = h["Close"].tail(30).tolist()
                m30_chg = (m30[-1] / m30[0] - 1) * 100 if len(m30) >= 2 and m30[0] > 0 else 0
                result[group_name].append({
                    "sym": sym, "label": label, "price": p,
                    "chg_pct": chg_pct, "m30": m30, "m30_chg": m30_chg,
                })
            except Exception:
                continue
    return result


# 雲端 fallback universe — 排行榜 + 策略掃描共用
TICKER_UNIVERSE_FALLBACK = [
    "0050", "0056", "00631L", "00878", "00919", "00929", "00939", "00940",
    "00713", "00892", "00881", "00891", "006208", "00646", "00692", "00701",
    "2330", "2317", "2454", "2412", "2308", "2382", "2891", "2882", "2881",
    "2884", "2885", "2886", "2887", "2890", "2892", "5871", "5876", "5880",
    "2002", "1301", "1303", "1326", "1101", "1102", "2207", "2105", "2603",
    "2609", "2615", "2618", "3008", "3017", "3034", "3037", "3045", "3231",
    "3661", "3702", "4904", "4938", "5483", "6271", "6285", "6415", "6505",
    "6669", "6770", "9904", "9910", "9921", "9933", "9945", "9939",
    "1815", "2356", "2376", "2379", "2383", "2385", "2408", "2474",
    "2880", "2883", "2888", "2889", "2912", "3380", "3443", "3653", "3711",
    "4915", "4961", "5269", "6781", "7402", "8069",
]


def _cloud_strategy_universe() -> list[str]:
    """雲端策略掃描 universe = 觀察清單 + ~80 檔熱門權值/ETF。"""
    _TW_TYPES = ("tw", "twse", "tpex", "emerging")
    wl_book = load_json("watchlist", {"tickers": []})
    wl_tickers = [t["ticker"] for t in wl_book.get("tickers", []) if t.get("type") in _TW_TYPES]
    return list(dict.fromkeys(wl_tickers + TICKER_UNIVERSE_FALLBACK))


@st.cache_data(ttl=3600, show_spinner=False)
def scan_revenue_yoy_signals(min_yoy: float = 30.0, max_yoy: float = 300.0,
                               min_value_yi: float = 1.0,
                               min_prev_revenue: float = 1e7,
                               top_n: int = 12):
    """掃描符合「月營收 YoY + 流動性」條件的個股。

    依據:memory 「Revenue YoY 60d alpha +3.95% (t=24.19, n=24K)」

    本機 cache → 掃全市場 ~2000 檔
    雲端 → 掃 watchlist + ~80 熱門 universe(live API)
    """
    import numpy as np
    finmind_dir = FINMIND_CACHE
    rev_files = list(finmind_dir.glob("TaiwanStockMonthRevenue_*.parquet"))
    hits = []

    # 雲端 fallback
    if not rev_files:
        universe = _cloud_strategy_universe()
        for tk in universe:
            try:
                rev = load_finmind_for_ticker(tk, "TaiwanStockMonthRevenue")
                if rev is None or rev.empty:
                    continue
                rev = rev.copy()
                rev["date"] = pd.to_datetime(rev["date"])
                rev = rev.sort_values("date")
                if len(rev) < 13: continue
                latest_rev = float(rev["revenue"].iloc[-1])
                prev_year_rev = float(rev["revenue"].iloc[-13])
                if prev_year_rev < min_prev_revenue: continue
                if latest_rev <= 0: continue
                yoy = (latest_rev / prev_year_rev - 1) * 100
                if not np.isfinite(yoy): continue
                if yoy < min_yoy or yoy > max_yoy: continue
                ohlcv = load_local_ohlcv(tk, 25)
                if ohlcv is None or len(ohlcv) < 20: continue
                recent = ohlcv.tail(20)
                avg_value_yi = float((recent["close"] * recent["volume"]).mean() / 1e8)
                if avg_value_yi < min_value_yi: continue
                hits.append({
                    "tk": tk,
                    "yoy": float(yoy),
                    "avg_value_yi": avg_value_yi,
                    "latest_rev_yi": latest_rev / 1e8,
                })
            except Exception:
                continue
        hits.sort(key=lambda x: x["yoy"], reverse=True)
        return hits[:top_n]

    # 本機 cache 模式
    for f in rev_files:
        tk = f.stem.replace("TaiwanStockMonthRevenue_", "")
        try:
            rev = pd.read_parquet(f)
            rev["date"] = pd.to_datetime(rev["date"])
            rev = rev.sort_values("date")
            if len(rev) < 13: continue  # 需要至少 13 個月才能算 YoY
            latest_rev = float(rev["revenue"].iloc[-1])
            prev_year_rev = float(rev["revenue"].iloc[-13])
            # 過濾低基期:去年同月 < 1000 萬 → 算出的 YoY 不可信
            if prev_year_rev < min_prev_revenue: continue
            if latest_rev <= 0: continue
            yoy = (latest_rev / prev_year_rev - 1) * 100
            # 過濾 inf / nan / 範圍外
            if not np.isfinite(yoy): continue
            if yoy < min_yoy or yoy > max_yoy: continue
            # L4 流動性 filter
            ohlcv_p = TW_OHLCV_CACHE / f"{tk}.parquet"
            if not ohlcv_p.exists(): continue
            ohlcv = pd.read_parquet(ohlcv_p)
            if len(ohlcv) < 20: continue
            recent = ohlcv.tail(20)
            avg_value_yi = float((recent["close"] * recent["volume"]).mean() / 1e8)
            if avg_value_yi < min_value_yi: continue
            hits.append({
                "tk": tk,
                "yoy": float(yoy),
                "avg_value_yi": avg_value_yi,
                "latest_rev_yi": latest_rev / 1e8,  # 億
            })
        except Exception:
            continue
    hits.sort(key=lambda x: x["yoy"], reverse=True)
    return hits[:top_n]


_RETAIL_LEVELS = (
    "1-999", "1,000-5,000", "5,001-10,000",
    "10,001-15,000", "15,001-20,000",
    "20,001-30,000", "30,001-40,000", "40,001-50,000",
)


def _scan_retail_pct(top_n: int, min_pct: float, max_pct: float,
                       reverse_sort: bool, min_value_yi: float) -> list[dict]:
    """散戶比例掃描共用 — 本機 cache + 雲端 universe live FinMind。"""
    cache_files = list(FINMIND_CACHE.glob("TaiwanStockHoldingSharesPer_*.parquet"))
    hits = []

    def _eval(tk: str, df_h, df_ohlcv) -> dict | None:
        if df_h is None or df_h.empty:
            return None
        h2 = df_h.copy()
        h2["date"] = pd.to_datetime(h2["date"])
        latest_d = h2["date"].max()
        sub = h2[h2["date"] == latest_d]
        retail_pct = float(
            sub[sub["HoldingSharesLevel"].isin(list(_RETAIL_LEVELS))]["percent"].sum()
        )
        if retail_pct < min_pct or retail_pct > max_pct:
            return None
        if df_ohlcv is None or len(df_ohlcv) < 20:
            return None
        oh = df_ohlcv.tail(20)
        avg_value_yi = float((oh["close"] * oh["volume"]).mean() / 1e8)
        if avg_value_yi < min_value_yi:
            return None
        return {"tk": tk, "retail_pct": retail_pct, "avg_value_yi": avg_value_yi}

    if cache_files:
        for f in cache_files:
            tk = f.stem.replace("TaiwanStockHoldingSharesPer_", "")
            try:
                df_h = pd.read_parquet(f)
                ohlcv_p = TW_OHLCV_CACHE / f"{tk}.parquet"
                df_oh = pd.read_parquet(ohlcv_p) if ohlcv_p.exists() else None
                hit = _eval(tk, df_h, df_oh)
                if hit:
                    hits.append(hit)
            except Exception:
                continue
    else:
        # 雲端 — universe + live FinMind
        for tk in _cloud_strategy_universe():
            try:
                df_h = load_finmind_for_ticker(tk, "TaiwanStockHoldingSharesPer")
                df_oh = load_local_ohlcv(tk, 25)
                hit = _eval(tk, df_h, df_oh)
                if hit:
                    hits.append(hit)
            except Exception:
                continue

    hits.sort(key=lambda x: x["retail_pct"], reverse=reverse_sort)
    return hits[:top_n]


@st.cache_data(ttl=3600, show_spinner=False)
def scan_low_retail_concentration(top_n: int = 12):
    """散戶比例反向 — 散戶最少 = 法人主導(memory 真 alpha)。"""
    return _scan_retail_pct(top_n, 0.01, 100, reverse_sort=False, min_value_yi=1.0)


@st.cache_data(ttl=3600, show_spinner=False)
def scan_high_retail_warning(top_n: int = 12):
    """散戶比例極高警示 — 韭菜聚集警示(反向訊號)。"""
    return _scan_retail_pct(top_n, 60, 100, reverse_sort=True, min_value_yi=0.5)


def _scan_ohlcv_pattern(top_n: int, chg_filter, vr_max: float = 0.8) -> list[dict]:
    """OHLCV 共用掃描:近 3 日符合 chg_filter(chg %) + VR < vr_max(量縮)。
    本機 cache 模式 → 掃 ~2000 檔
    雲端 → 掃 ~80 檔 universe (live yfinance OHLCV)"""
    cache_files = list(TW_OHLCV_CACHE.glob("*.parquet"))
    hits = []

    def _scan_df(tk: str, df):
        if df is None or len(df) < 25:
            return None
        last3 = df.tail(3)
        for _, row in last3.iterrows():
            if row["close"] <= 0 or row["open"] <= 0:
                continue
            chg = (row["close"] / row["open"] - 1) * 100
            if not chg_filter(chg):
                continue
            idx_pos = df.index[df["date"] == row["date"]][0]
            if idx_pos < 20:
                continue
            avg_vol_20 = df.iloc[idx_pos-20:idx_pos]["volume"].mean()
            if avg_vol_20 <= 0:
                continue
            vr = row["volume"] / avg_vol_20
            if vr >= vr_max:
                continue
            return {"tk": tk, "date": str(row["date"])[:10],
                    "chg": chg, "vr": vr, "close": row["close"]}
        return None

    if cache_files:
        for f in cache_files:
            try:
                df = pd.read_parquet(f)
                hit = _scan_df(f.stem, df)
                if hit:
                    hits.append(hit)
            except Exception:
                continue
    else:
        # 雲端 — universe + live OHLCV
        for tk in _cloud_strategy_universe():
            try:
                df = load_local_ohlcv(tk, 60)
                hit = _scan_df(tk, df)
                if hit:
                    hits.append(hit)
            except Exception:
                continue

    hits.sort(key=lambda x: x["date"], reverse=True)
    return hits[:top_n]


@st.cache_data(ttl=3600, show_spinner=False)
def scan_quiet_limitdown_bounce(top_n: int = 12):
    """量縮跌停反彈訊號(memory 真 alpha:20d alpha +7.99%, OOS 2020-25 robust)。
    條件:跌幅 ≤ -9.5% + VR < 0.8(量縮)"""
    return _scan_ohlcv_pattern(top_n, lambda chg: chg <= -9.5)


@st.cache_data(ttl=3600, show_spinner=False)
def scan_ab_consensus(top_n: int = 12):
    """外資+投信雙重共識買進(memory: AB consensus n=126 alpha +8.78%)。
    條件:外資 20d > +5000 張 AND 投信 20d > +500 張"""
    inst_dir = FINMIND_CACHE
    cache_files = list(inst_dir.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))
    hits = []

    def _eval(tk: str, df_inst, df_oh) -> dict | None:
        if df_inst is None or df_inst.empty:
            return None
        d = df_inst.copy()
        d["date"] = pd.to_datetime(d["date"])
        d = d.sort_values("date")
        last20 = d["date"].unique()[-20:]
        sub = d[d["date"].isin(last20)].copy()
        sub["net"] = sub["buy"] - sub["sell"]
        agg = sub.groupby("name")["net"].sum() / 1000
        f20 = int(agg.get("Foreign_Investor", 0))
        it20 = int(agg.get("Investment_Trust", 0))
        if f20 < 5000 or it20 < 500:
            return None
        if df_oh is None or len(df_oh) < 20:
            return None
        oh = df_oh.tail(20)
        avg_value_yi = float((oh["close"] * oh["volume"]).mean() / 1e8)
        if avg_value_yi < 1:
            return None
        return {"tk": tk, "f20": f20, "it20": it20, "avg_value_yi": avg_value_yi}

    if cache_files:
        for f in cache_files:
            tk = f.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
            try:
                df_inst = pd.read_parquet(f)
                ohlcv_p = TW_OHLCV_CACHE / f"{tk}.parquet"
                df_oh = pd.read_parquet(ohlcv_p) if ohlcv_p.exists() else None
                hit = _eval(tk, df_inst, df_oh)
                if hit:
                    hits.append(hit)
            except Exception:
                continue
    else:
        # 雲端 — universe + live FinMind
        for tk in _cloud_strategy_universe():
            try:
                df_inst = load_finmind_for_ticker(tk, "TaiwanStockInstitutionalInvestorsBuySell")
                df_oh = load_local_ohlcv(tk, 25)
                hit = _eval(tk, df_inst, df_oh)
                if hit:
                    hits.append(hit)
            except Exception:
                continue

    hits.sort(key=lambda x: x["f20"] + x["it20"], reverse=True)
    return hits[:top_n]


@st.cache_data(ttl=3600, show_spinner=False)
def scan_govbank_reverse(top_n: int = 12):
    """行庫共識度反向(memory: 5+ 行庫同買後 60d alpha -1.62% t=-28.46 反向真 alpha)。
    這個是 ANTI-signal,顯示 = 警示「政府護盤股後續弱勢」"""
    bank_dir = FINMIND_CACHE.parent.parent / "extras"
    bank_file = bank_dir / "government_bank_buysell.parquet"
    # 雲端 fallback:直接打 FinMind v4 拿最近 30 日全市場八大行庫
    if not bank_file.exists():
        df = _fetch_govbank_live(days=30)
        if df is None or df.empty:
            return []
    else:
        df = pd.read_parquet(bank_file)
        df["date"] = pd.to_datetime(df["date"])
        # 最近 20 日 5+ 行庫同買的個股
        recent = df[df["date"] >= df["date"].max() - pd.Timedelta(days=30)]
        if recent.empty: return []
        df = recent
    try:
        # 計算每檔有幾家銀行買超(本機 cache 跟 live 共用)
        bank_buy = df.copy()
        if "date" in bank_buy.columns:
            bank_buy["date"] = pd.to_datetime(bank_buy["date"])
        bank_buy["net"] = bank_buy["buy"] - bank_buy["sell"]
        # 每天 + 每檔 → 多少家銀行買超
        daily_bank_count = (bank_buy[bank_buy["net"] > 0]
                            .groupby(["date", "stock_id"])["bank"]
                            .nunique().reset_index())
        # 5+ 家
        flagged = daily_bank_count[daily_bank_count["bank"] >= 5]
        if flagged.empty: return []
        # 取最新觸發
        hits_raw = (flagged.sort_values("date", ascending=False)
                     .drop_duplicates("stock_id"))
        result = []
        for _, row in hits_raw.head(top_n).iterrows():
            result.append({"tk": str(row["stock_id"]),
                            "date": str(row["date"])[:10],
                            "bank_count": int(row["bank"])})
        return result
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def scan_quiet_limitup(top_n: int = 12):
    """量縮漲停 (vr<0.8 + 漲幅 ≥ 9%) 最近 3 日訊號。
    memory: 「量縮漲停 20d alpha +4.83% post-2020 robust」"""
    return _scan_ohlcv_pattern(top_n, lambda chg: chg >= 9.5)


@st.cache_data(ttl=86400)
def fetch_dividend_calendar(ticker: str):
    """Live fetch 歷年股利 + 除權息日."""
    try:
        import requests
        r = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={"dataset": "TaiwanStockDividend",
                    "data_id": ticker, "start_date": "2022-01-01"},
            timeout=12,
        )
        data = r.json()
        if data.get("status") == 200 and data.get("data"):
            df = pd.DataFrame(data["data"])
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            return df
    except Exception:
        pass
    return None


@st.cache_data(ttl=86400)  # 1 天
def load_dividend_for_ticker(ticker: str):
    """歷年除權息 from FinMind dividend cache."""
    div_dir = INVEST_ROOT / "data" / "cache" / "finmind" / "dividend"
    for pattern in [f"TaiwanStockDividend_{ticker}.parquet",
                    f"{ticker}.parquet",
                    f"dividend_{ticker}.parquet"]:
        p = div_dir / pattern
        if p.exists():
            try:
                return pd.read_parquet(p)
            except Exception:
                pass
    return None


def calc_technical_indicators(df):
    """Add KD, Bollinger, MA to OHLCV df. df must have 'close','high','low'."""
    df = df.copy()
    # MA
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    # 布林通道 (20, 2)
    df["bb_mid"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_up"] = df["bb_mid"] + 2 * bb_std
    df["bb_dn"] = df["bb_mid"] - 2 * bb_std
    # KD (9)
    n = 9
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n) * 100
    rsv = rsv.fillna(50)
    df["k"] = rsv.ewm(alpha=1/3, adjust=False).mean()
    df["d"] = df["k"].ewm(alpha=1/3, adjust=False).mean()
    # RSI (14)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def calc_composite_score(tech_data, chip_data, funda_data):
    """綜合評分 0-100 — 純客觀,基於數據."""
    scores = {"技術": 50, "籌碼": 50, "基本": 50}

    # 技術面 (40% weight)
    if tech_data:
        s = 50
        # MA 多頭排列 + 10
        if (tech_data.get("price", 0) > tech_data.get("ma5", 0) > tech_data.get("ma20", 0)
            > tech_data.get("ma60", 0)):
            s += 15
        # 跌破 MA60 -15
        elif tech_data.get("price", 0) < tech_data.get("ma60", 1e9):
            s -= 15
        # RSI 在 30-70 健康 +5; >70 -5; <30 +5 (超賣)
        rsi = tech_data.get("rsi", 50)
        if 30 < rsi < 70: s += 5
        elif rsi > 80: s -= 10
        elif rsi < 20: s += 5
        # KD 黃金交叉 +10 / 死亡交叉 -10
        k = tech_data.get("k", 50); d = tech_data.get("d", 50)
        if k > d and k < 80: s += 5
        elif k < d and k > 20: s -= 5
        scores["技術"] = max(0, min(100, s))

    # 籌碼面 (30% weight)
    if chip_data:
        s = 50
        # 外資+投信淨買超 +10
        f_net = chip_data.get("foreign_20d", 0)
        i_net = chip_data.get("invtrust_20d", 0)
        if f_net + i_net > 1000: s += 15
        elif f_net + i_net > 0: s += 5
        elif f_net + i_net < -1000: s -= 15
        elif f_net + i_net < 0: s -= 5
        scores["籌碼"] = max(0, min(100, s))

    # 基本面 (30% weight)
    if funda_data:
        s = 50
        # PER < 15 +10 / PER 15-25 +0 / PER > 30 -10
        per = funda_data.get("per", 30)
        if 0 < per < 15: s += 15
        elif per < 25: s += 5
        elif per > 40: s -= 10
        # YoY +10
        yoy = funda_data.get("rev_yoy", 0)
        if yoy > 20: s += 10
        elif yoy > 0: s += 5
        elif yoy < -10: s -= 10
        # 殖利率 > 4% +5
        if funda_data.get("yield", 0) > 4: s += 5
        scores["基本"] = max(0, min(100, s))

    composite = round(scores["技術"] * 0.4 + scores["籌碼"] * 0.3 + scores["基本"] * 0.3, 1)
    return composite, scores


# ──────────────────────────────────────────────
# 商業模式旗標 (以後接訂閱檢查只改這行)
# ──────────────────────────────────────────────
IS_PRO_USER = True  # TODO: 接訂閱系統時改成 check_subscription(user_id)


# ──────────────────────────────────────────────
# 卡牌列 helper (觀察 / 搜尋 / 熱門 / 排行 共用)
# ──────────────────────────────────────────────

# 產業 icon (跟稀有度分離,icon 看產業類別 / 稀有度看市場規模)
_INDUSTRY_ICON = {
    "半導體業":           "🔬",
    "電腦及週邊設備業":   "💻",
    "電子零組件業":       "⚡",
    "光電業":             "💡",
    "通信網路業":         "📡",
    "電子通路業":         "📦",
    "其他電子業":         "🔌",
    "資訊服務業":         "🖥️",
    "金融保險業":         "🏦",
    "航運業":             "🚢",
    "食品工業":           "🍱",
    "鋼鐵工業":           "🏗️",
    "塑膠工業":           "🛢️",
    "化學工業":           "⚗️",
    "紡織纖維":           "🧵",
    "生技醫療業":         "💊",
    "觀光餐旅":           "🍽️",
    "汽車工業":           "🚗",
    "建材營造":           "🧱",
    "貿易百貨":           "🛍️",
    "油電燃氣業":         "⛽",
    "電器電纜":           "🔌",
    "電機機械":           "⚙️",
    "玻璃陶瓷":           "🍶",
    "水泥工業":           "🧱",
    "造紙工業":           "📃",
    "橡膠工業":           "🛞",
    "農業科技":           "🌾",
}

# 稀有度規則 (基於 20 日平均成交額,免費 teaser,跟健檢分數獨立)
_RARITY_TIERS = [
    (50,   "#fcd34d", "#f59e0b", "LEGENDARY"),   # > 50 億/日
    (10,   "#a78bfa", "#7c3aed", "EPIC"),         # 10-50 億
    (3,    "#38bdf8", "#0284c7", "RARE"),         # 3-10 億
    (0.5,  "#5eead4", "#0d9488", "UNCOMMON"),     # 0.5-3 億
    (0,    "#94a3b8", "#475569", "COMMON"),       # < 0.5 億
]


def _industry_icon(industry: str, tk: str = "") -> str:
    if tk.startswith("00") or tk == "0050":
        return "🌐"
    return _INDUSTRY_ICON.get(industry or "", "📊")


def card_rarity(tk: str, industry: str, avg_value_yi: float = None):
    """根據 20 日平均成交額(單位:億)決定稀有度。
    avg_value_yi 沒給就 fallback COMMON。
    Returns (light_color, dark_color, icon, rarity_label).
    """
    icon = _industry_icon(industry, tk)
    if avg_value_yi is None:
        return ("#94a3b8", "#475569", icon, "COMMON")
    for threshold, light, dark, label in _RARITY_TIERS:
        if avg_value_yi >= threshold:
            return (light, dark, icon, label)
    return ("#94a3b8", "#475569", icon, "COMMON")


# ──────────────────────────────────────────────
# ❓ 名詞解釋系統(點問號看白話)
# ──────────────────────────────────────────────
HELP_TEXTS = {
    "健檢分數": "0-100 分綜合評估個股體質(技術 40% + 籌碼 30% + 基本 30%)。70+ 體質好、50-69 普通、<50 體質不好。\n\n📊 歷史驗證(2020-26, 6.4 年):70+ 體質股 **60 日中期視角** 平均 +10.93% / win 60.3% / vs 0050 alpha +4.57pp(n=194)。OOS 2023-26 更強:+19.23% / win 85.7% / alpha +9.91pp(n=56)。\n\n⚠️ 注意:(1) 適用 ~60 日中期,短線(20d alpha 僅 +0.92pp)、長線(120d 衰退到 +1pp)效果差;(2) 2022 熊年小輸 0.34pp,regime 敏感;(3) 平均每月只 2-3 檔過 70 分,訊號稀少屬常態;(4) 50-69 中等股 alpha -1.34pp 反而拖累,本 app 排行已過濾。",
    "體質掃描": "4 維度的連續 6 期方向圖:接單能力(月營收 YoY)、獲利能力(EPS)、經營能力(毛利率)、償債能力(流動比)。↑↗ 紅 = 上升 / ↓↘ 綠 = 下降。",
    "稀有度": "用 20 日平均成交額分:LEGENDARY > 50億、EPIC 10-50億、RARE 3-10億、UNCOMMON 0.5-3億、COMMON <0.5億。代表流動性,跟好壞無關。",
    "VIX": "美股恐慌指數,反映 30 天波動預期。> 30 = 高恐慌、< 18 = 過度樂觀、20-30 = 正常範圍。",
    "MA200": "200 日移動平均線。股價距 MA200 反映長期趨勢:+30% 過熱、合理區間 ±5%、-15% 低估。",
    "AB 雙重共識": "外資 + 投信同時大買的訊號。memory 真 alpha:60d +8.78% / t=+3.83 / n=126 OOS PASS。",
    "量縮漲停": "單日漲停但成交量低於 20 日均量 0.8 倍。memory 真 alpha:20d +4.83% / n=5437。「無量上漲」反映籌碼穩定。",
    "量縮跌停反彈": "單日跌停但成交量低。memory 真 alpha:20d +7.99% / 5d +4.27% / n=4733。籌碼不亂出反彈機率高。",
    "行庫共識度反向": "5+ 家公股行庫同買 = 政府護盤股。memory:後續 60d alpha -1.62% / t=-28.46。「越多護盤後續越弱」反直覺真 alpha。",
    "散戶比例": "持股 < 50 張的散戶合計佔比。比例極端區(極低 = 法人主導 / 極高 = 韭菜聚集)有 alpha,lift +11.3pp。",
    "月營收 YoY": "本月營收 vs 去年同月年增率。> 30% + 流動性過濾 = memory 真 alpha 60d +3.95% / t=24.19 / n=24K。",
    "月營收 YoY 真 alpha": "正向策略。條件:月營收 YoY > 30% + 20 日成交額 > 1 億。memory 實證:60d 平均 +3.95% / t=24.19 / n=24K,OOS 2020-25 robust(但 2017-19 失效,屬 post-2020 結構性 alpha,每年需重驗)。",
    "散戶最少": "正向策略。散戶持股比例(< 50 張)最低 = 法人主導股。memory:散戶比例極端區 lift +11.3pp / p<0.001(weekly n=9991 真 alpha)。流動性 > 1 億過濾掉殭屍股。",
    "韭菜聚集警示": "反向策略。散戶比例 ≥ 60% = 韭菜密集區,後續易出現法人倒貨。memory 真 alpha 雙向 +11.3pp。看到這訊號表示「等多數人發現時通常已晚」。",
    "VR(成交量比)": "Volume Ratio = 當日成交量 / 過去 N 日均量。<0.8 = 量縮、>1.5 = 量增、>2.5 = 量爆。",
    "PER 本益比": "股價 / 每股盈餘。反映「賺一塊錢市場願意出多少錢買」。低 = 估值便宜 / 高 = 估值偏貴(但成長股可接受)。",
    "外資持股比例": "外資投資人持有股數 / 流通股數。50% 以上 = 法人主導股,通常波動較穩。",
    "韭菜病": "投資行為偏差導致虧損的傾向。常見:FOMO 追高、損失趨避(不停損)、過度自信、群眾從眾。本 app 自檢 tab 可測。",
    "PRO": "訂閱者專屬功能。目前 beta 階段全免費試用。將來會以 NT$ 99/月 提供 AI 解讀、策略市集、健檢分數排行、開盤前個人化重點等進階功能。",
}


def title_with_help(title_html: str, help_key: str, key_suffix: str = ""):
    """渲染標題 + ❓ popover。
    key_suffix:加唯一後綴避免重複(中文 key 會被 streamlit sanitize 成相同字串)。"""
    import hashlib
    from streamlit_extras.stylable_container import stylable_container
    safe_key = hashlib.md5(f"{help_key}{key_suffix}".encode()).hexdigest()[:10]
    c1, c2 = st.columns([15, 1])
    with c1:
        st.markdown(title_html, unsafe_allow_html=True)
    with c2:
        with stylable_container(
            key=f"hb_{safe_key}",
            css_styles="""
                div[data-testid="stPopover"] button {
                    background: transparent !important;
                    border: 1px solid #2f343d !important;
                    border-radius: 50% !important;
                    width: 26px !important;
                    height: 26px !important;
                    min-height: 26px !important;
                    padding: 0 !important;
                    color: #94a3b8 !important;
                    font-size: 0.8rem !important;
                    margin-top: 8px !important;
                }
                div[data-testid="stPopover"] button:hover {
                    border-color: #14b8a6 !important;
                    color: #5eead4 !important;
                }
            """,
        ):
            with st.popover("?"):
                st.markdown(f"**{help_key}**")
                st.markdown(HELP_TEXTS.get(help_key, "說明待補"))


def pro_gate(feature_name: str = "此功能", show_card: bool = True) -> bool:
    """檢查使用者是否為 PRO。
    非 PRO 時若 show_card=True 顯示升級提示卡。
    Returns True 才能繼續執行 PRO 功能。"""
    if IS_PRO_USER:
        return True
    if show_card:
        st.markdown(
            "<div style='background:linear-gradient(135deg, #1f2937 0%, #1a1f27 100%);"
            "border:1px solid #f59e0b; border-radius:12px; padding:18px 22px;"
            "margin:12px 0; text-align:center'>"
            "<div style='font-size:0.7rem; color:#f59e0b; letter-spacing:2px; font-weight:700'>"
            "🔒 PRO 專屬功能</div>"
            f"<div style='color:#fff; font-size:1.1rem; margin-top:6px; font-weight:600'>"
            f"{feature_name}</div>"
            "<div style='color:#94a3b8; font-size:0.85rem; margin-top:6px'>"
            "升級訂閱解鎖完整健檢報告 + AI 解讀 prompt</div>"
            "<div style='color:#f59e0b; font-size:0.85rem; margin-top:8px; font-weight:700'>"
            "PRO 月費 NT$ 99(目前 beta 階段免費)</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    return False


def _sparkline(closes, n=14):
    """舊版 unicode block (保留向後相容)."""
    closes = list(closes[-n:])
    if len(closes) < 2: return ""
    lo, hi = min(closes), max(closes)
    if hi == lo: return "▄" * len(closes)
    bars = "▁▂▃▄▅▆▇█"
    return "".join(bars[min(7, int((c - lo) / (hi - lo) * 7))] for c in closes)


def _svg_sparkline(closes, width=160, height=42, color="#ef4444"):
    """SVG 折線圖 — 比 unicode 清楚 10 倍."""
    closes = [float(c) for c in closes if c is not None]
    if len(closes) < 2:
        return ""
    lo, hi = min(closes), max(closes)
    if hi == lo:
        return ""

    n = len(closes)
    pad_y = 4  # 上下留邊
    eff_h = height - 2 * pad_y
    points = []
    for i, c in enumerate(closes):
        x = i / (n - 1) * width
        y = pad_y + eff_h - (c - lo) / (hi - lo) * eff_h
        points.append(f"{x:.1f},{y:.1f}")
    path_d = "M " + " L ".join(points)
    area_d = path_d + f" L {width},{height} L 0,{height} Z"

    # 抽 rgb
    r = int(color[1:3], 16); g = int(color[3:5], 16); b = int(color[5:7], 16)

    # 最後一點圓圈
    last_x, last_y = points[-1].split(",")

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
                    xmlns="http://www.w3.org/2000/svg"
                    style="display:block">
      <defs>
        <linearGradient id="g{id(closes)}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="rgb({r},{g},{b})" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="rgb({r},{g},{b})" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path d="{area_d}" fill="url(#g{id(closes)})"/>
      <path d="{path_d}" stroke="{color}" stroke-width="1.8"
            fill="none" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="{last_x}" cy="{last_y}" r="3" fill="{color}"
              stroke="#16181d" stroke-width="1.5"/>
    </svg>"""


# ──────────────────────────────────────────────
# 持股 P&L helpers (Phase 1 記帳功能)
# 對齊 CLAUDE.md 「成本基數計算規則」:
#   cost_incl_fee = cost × (1 + 0.001425 × (1 - rebate_pct/100))
#   永豐 / 華南永昌結構:當下扣 0.1425% 全額,月底退 X%(預設 70%)
#   pnl  = shares × (price - cost_incl_fee)  # gross 慣例,跟券商一致
#   pct  = (price / cost_incl_fee - 1) * 100
# ──────────────────────────────────────────────
BUY_FEE_RATE = 0.001425   # 法定買進手續費
SELL_FEE_RATE = 0.001425  # 賣出手續費
SELL_TAX_RATE = 0.003     # 證交稅 0.3%
DEFAULT_FEE_REBATE_PCT = 70  # 主券商手續費月退 70%(可設定)


def get_user_settings(wl: dict | None = None) -> dict:
    """讀取使用者設定(預設 70% rebate, 0.1425% 手續費, 0.3% 證交稅)。"""
    if wl is None:
        wl = load_json("watchlist", {"tickers": []})
    s = wl.get("settings", {}) or {}
    return {
        "buy_fee_pct": float(s.get("buy_fee_pct", BUY_FEE_RATE * 100)),    # %
        "sell_fee_pct": float(s.get("sell_fee_pct", SELL_FEE_RATE * 100)), # %
        "sell_tax_pct": float(s.get("sell_tax_pct", SELL_TAX_RATE * 100)), # %
        "fee_rebate_pct": float(s.get("fee_rebate_pct", DEFAULT_FEE_REBATE_PCT)),  # 0-100
    }


def effective_buy_fee_rate(settings: dict) -> float:
    """扣折抵後的實際買進手續費率(0-1 浮點)。"""
    raw = settings["buy_fee_pct"] / 100
    rebate = settings["fee_rebate_pct"] / 100
    return raw * (1 - rebate)


def effective_sell_total_rate(settings: dict) -> float:
    """賣出總扣費率(手續費 × (1-折抵) + 證交稅)。"""
    fee = settings["sell_fee_pct"] / 100 * (1 - settings["fee_rebate_pct"] / 100)
    tax = settings["sell_tax_pct"] / 100
    return fee + tax


def compute_holding_pnl(item: dict, current_price: float | None = None,
                          settings: dict | None = None) -> dict | None:
    """從 watchlist item 算 P&L。沒填 shares/cost 回 None。
    settings=None 時自動讀使用者設定。"""
    shares = item.get("shares")
    cost = item.get("cost_per_share")
    if not shares or not cost or shares <= 0 or cost <= 0:
        return None
    if current_price is None:
        q = fetch_yfinance_quote(item["ticker"])
        if not q:
            return None
        current_price = q["price"]
    if settings is None:
        settings = get_user_settings()
    eff_buy = effective_buy_fee_rate(settings)
    cost_incl_fee = cost * (1 + eff_buy)
    total_cost = shares * cost_incl_fee
    mv = shares * current_price
    pnl = mv - total_cost
    pct = (current_price / cost_incl_fee - 1) * 100
    return {
        "shares": shares,
        "cost_per_share": cost,
        "cost_incl_fee": cost_incl_fee,
        "total_cost": total_cost,
        "current_price": current_price,
        "mv": mv,
        "pnl": pnl,
        "pct": pct,
        "entry_date": item.get("entry_date", ""),
        "eff_buy_pct": eff_buy * 100,
    }


def build_stock_card_html(tk: str, info: dict, rank_medal: str = "") -> str:
    """純函數:回傳卡牌 HTML 字串(輕量,只抓即時報價 + 20 日成交額)。
    觀察清單預設顯示用,不跑 overview 重資料。"""
    # 算 20 日平均成交額(億)→ 決定稀有度
    avg_value_yi = None
    try:
        df_v = load_local_ohlcv(tk, 25)
        if df_v is not None and len(df_v) > 0:
            recent_v = df_v.tail(20)
            # OHLCV volume 是「股」單位 (yfinance / FinMind 都是)
            trade_values = (recent_v["close"] * recent_v["volume"]).mean()
            avg_value_yi = float(trade_values / 1e8)
    except Exception:
        pass
    light, dark, icon, rarity = card_rarity(tk, info.get("industry", ""), avg_value_yi)
    name = info.get("name", "")
    ind = info.get("industry") or "—"

    quote = fetch_yfinance_quote(tk)
    if quote:
        price = quote["price"]
        chg = price - quote["prev_close"]
        chg_pct = chg / quote["prev_close"] * 100 if quote["prev_close"] > 0 else 0
        if chg > 0: pcolor, parrow = "#ef4444", "▲"
        elif chg < 0: pcolor, parrow = "#10b981", "▼"
        else: pcolor, parrow = "#8b92a0", "—"
        price_str = f"{price:.2f}"
        chg_str = f"{parrow} {abs(chg):.2f} ({chg_pct:+.2f}%)"
    else:
        pcolor = "#8b92a0"; price_str = "—"; chg_str = "—"

    medal_chip = (
        f"<div style='font-size:0.7rem; color:#cbd5e1; margin-bottom:2px'>"
        f"<span style='background:rgba(0,0,0,0.45); padding:2px 8px; border-radius:5px; font-weight:700'>{rank_medal}</span>"
        f"</div>" if rank_medal else ""
    )

    return (
        f"<div style='background:linear-gradient(155deg, {dark} 0%, #1a1d24 50%, #16181d 100%);"
        f"border:2px solid {light}; border-radius:14px;"
        f"padding:14px 16px; margin-bottom:6px;"
        f"box-shadow: 0 4px 14px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.08);"
        f"position:relative; display:flex; align-items:center; gap:14px'>"
        f"<div style='flex-shrink:0; text-align:center; min-width:60px'>"
        f"<div style='font-size:2rem; line-height:1'>{icon}</div>"
        f"<div style='margin-top:4px; background:rgba(0,0,0,0.4); padding:2px 6px;"
        f"border-radius:5px; font-size:0.55rem; color:{light}; letter-spacing:1px; font-weight:700'>{rarity}</div>"
        f"</div>"
        f"<div style='flex:1; min-width:0'>"
        f"{medal_chip}"
        f"<div style='font-size:1.4rem; color:#fff; font-weight:800; letter-spacing:1px; line-height:1'>{tk}</div>"
        f"<div style='font-size:0.9rem; color:#e4e6eb; margin-top:3px; font-weight:600'>{name}</div>"
        f"<div style='font-size:0.7rem; color:{light}; margin-top:2px'>{ind}</div>"
        f"</div>"
        f"<div style='text-align:right; flex-shrink:0; min-width:90px'>"
        f"<div style='font-size:1.4rem; color:{pcolor}; font-weight:700; line-height:1'>{price_str}</div>"
        f"<div style='font-size:0.75rem; color:{pcolor}; margin-top:3px; white-space:nowrap'>{chg_str}</div>"
        f"</div>"
        f"</div>"
    )


def render_stock_row(tk: str, info: dict, key_prefix: str, idx: int,
                      rank_medal: str = "",
                      button_label: str = "🩺 翻開健檢",
                      skip_card: bool = False):
    """直排卡牌列:卡片 + 基本概述 + 按鈕。
    button_label 可改成「⭐ 加入觀察清單」等。
    skip_card=True 時不渲染卡片(供觀察清單在 expander 外已單獨渲染卡)。
    回傳 True 如果按鈕被按。"""
    light, dark, icon, rarity = card_rarity(tk, info.get("industry", ""))
    name = info.get("name", "")
    ind = info.get("industry") or "—"

    # 即時報價
    quote = fetch_yfinance_quote(tk)
    if quote:
        price = quote["price"]
        chg = price - quote["prev_close"]
        chg_pct = chg / quote["prev_close"] * 100 if quote["prev_close"] > 0 else 0
        if chg > 0: pcolor, parrow = "#ef4444", "▲"
        elif chg < 0: pcolor, parrow = "#10b981", "▼"
        else: pcolor, parrow = "#8b92a0", "—"
        price_str = f"{price:.2f}"
        chg_str = f"{parrow} {abs(chg):.2f} ({chg_pct:+.2f}%)"
    else:
        pcolor = "#8b92a0"; price_str = "—"; chg_str = "—"

    # 從本機 cache 算 5/20/60 日 + SVG sparkline
    df_o = load_local_ohlcv(tk, 80)
    ret_5d = ret_20d = ret_60d = None
    spark_svg = ""
    high_30 = low_30 = None
    if df_o is not None and len(df_o) > 5:
        last_c = df_o["close"].iloc[-1]
        if len(df_o) > 5:
            ret_5d = (last_c / df_o["close"].iloc[-6] - 1) * 100
        if len(df_o) > 20:
            ret_20d = (last_c / df_o["close"].iloc[-21] - 1) * 100
        if len(df_o) > 60:
            ret_60d = (last_c / df_o["close"].iloc[-61] - 1) * 100
        # SVG 30 日線
        closes_30 = df_o["close"].tail(30).tolist()
        if closes_30:
            high_30 = max(closes_30)
            low_30 = min(closes_30)
            # 期間漲色:起點 vs 終點
            line_col = "#ef4444" if closes_30[-1] >= closes_30[0] else "#10b981"
            spark_svg = _svg_sparkline(closes_30, width=180, height=40, color=line_col)

    # 法人 20d (cache,快)
    f20 = it20 = de20 = None
    try:
        inst = load_finmind_for_ticker(tk, "TaiwanStockInstitutionalInvestorsBuySell")
        if inst is not None and not inst.empty:
            i2 = inst.copy()
            i2["date"] = pd.to_datetime(i2["date"])
            i2 = i2.sort_values("date").tail(40)
            last20_d = i2["date"].unique()[-20:]
            i2["net"] = i2["buy"] - i2["sell"]
            sub20 = i2[i2["date"].isin(last20_d)]
            agg = sub20.groupby("name")["net"].sum() / 1000
            f20 = int(agg.get("Foreign_Investor", 0))
            it20 = int(agg.get("Investment_Trust", 0))
            de20 = int(agg.get("Dealer_self", 0))
    except Exception:
        pass

    # 月營收 YoY (cache)
    rev_yoy = None
    try:
        rev = load_finmind_for_ticker(tk, "TaiwanStockMonthRevenue")
        if rev is not None and not rev.empty:
            rev["date"] = pd.to_datetime(rev["date"])
            rev = rev.sort_values("date")
            yoy_series = rev["revenue"].pct_change(12) * 100
            v = yoy_series.iloc[-1]
            if not pd.isna(v):
                rev_yoy = float(v)
    except Exception:
        pass

    # PER (cache)
    per_v = None
    try:
        per_df = load_finmind_for_ticker(tk, "TaiwanStockPER")
        if per_df is not None and not per_df.empty:
            per_df["date"] = pd.to_datetime(per_df["date"])
            lp = per_df.sort_values("date").iloc[-1]
            per_v = float(lp.get("PER", 0))
    except Exception:
        pass

    medal_html = (f"""<div style='position:absolute; top:8px; left:10px;
                        background:rgba(0,0,0,0.45); padding:3px 10px;
                        border-radius:6px; font-size:0.78rem;
                        color:#fff; font-weight:700'>{rank_medal}</div>"""
                   if rank_medal else "")

    # ─── 手機直式:全部垂直堆疊 (卡片 → 概述 → 按鈕) ───
    c_card = st.container()
    c_info = st.container()
    c_btn = st.container()

    if not skip_card:
        with c_card:
            st.markdown(build_stock_card_html(tk, info, rank_medal),
                          unsafe_allow_html=True)

    with c_info:
        # 概述 HTML
        def _ret_html(label, v):
            if v is None: return f"<div><span style='color:#64748b'>{label}</span> <span style='color:#94a3b8'>—</span></div>"
            c = "#ef4444" if v > 0 else "#10b981" if v < 0 else "#8b92a0"
            return f"<div><span style='color:#94a3b8; font-size:0.75rem'>{label}</span> <span style='color:{c}; font-weight:600'>{v:+.1f}%</span></div>"

        def _inst_html(label, v):
            if v is None: return ""
            c = "#ef4444" if v > 0 else "#10b981" if v < 0 else "#8b92a0"
            arr = "▲" if v > 0 else "▼" if v < 0 else "—"
            return f"<div><span style='color:#94a3b8; font-size:0.75rem'>{label}</span> <span style='color:{c}; font-weight:600'>{arr} {abs(v):,}</span></div>"

        # 全部 HTML 用單行避免被 markdown 當 code block
        ret_block = (
            "<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:8px;font-size:0.9rem'>"
            + _ret_html("5 日", ret_5d) + _ret_html("20 日", ret_20d) + _ret_html("60 日", ret_60d)
            + "</div>"
        )

        inst_block = ""
        if any(v is not None for v in [f20, it20, de20]):
            inst_block = (
                "<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:8px;font-size:0.85rem;margin-top:6px'>"
                + _inst_html("外資 20d", f20) + _inst_html("投信 20d", it20) + _inst_html("自營 20d", de20)
                + "</div>"
            )

        funda_parts = []
        if per_v is not None and per_v > 0:
            funda_parts.append(f"<span style='color:#94a3b8'>PER</span> <span style='color:#e4e6eb;font-weight:600'>{per_v:.1f}</span>")
        if rev_yoy is not None:
            yc = "#ef4444" if rev_yoy > 10 else "#10b981" if rev_yoy < -10 else "#fbbf24"
            funda_parts.append(f"<span style='color:#94a3b8'>月營收 YoY</span> <span style='color:{yc};font-weight:600'>{rev_yoy:+.1f}%</span>")
        funda_html = (
            "<div style='font-size:0.85rem;margin-top:6px;display:flex;gap:14px'>"
            + " · ".join(funda_parts) + "</div>"
        ) if funda_parts else ""

        # SVG 折線 + 30 日高低
        spark_html = ""
        if spark_svg and high_30 and low_30:
            spark_html = (
                "<div style='margin-top:10px;display:flex;align-items:center;gap:12px;background:rgba(0,0,0,0.2);padding:8px 12px;border-radius:8px'>"
                f"<div style='flex-shrink:0'>{spark_svg}</div>"
                "<div style='font-size:0.72rem;color:#94a3b8;line-height:1.4'>"
                "<div>📅 近 30 日</div>"
                f"<div>📈 高 <span style='color:#ef4444;font-weight:600'>{high_30:.2f}</span></div>"
                f"<div>📉 低 <span style='color:#10b981;font-weight:600'>{low_30:.2f}</span></div>"
                "</div></div>"
            )

        overview_html = (
            "<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%);padding:12px 16px;border-radius:10px;border:1px solid #2f343d;min-height:160px;display:flex;flex-direction:column;justify-content:center'>"
            "<div style='color:#94a3b8;font-size:0.7rem;letter-spacing:1.5px;font-weight:600;margin-bottom:6px'>📊 基本概述</div>"
            + ret_block + inst_block + funda_html + spark_html
            + "</div>"
        )
        st.markdown(overview_html, unsafe_allow_html=True)

    with c_btn:
        clicked = st.button(button_label, key=f"{key_prefix}_{idx}_{tk}",
                              use_container_width=True, type="primary")
        return clicked


@st.cache_data(ttl=86400)
def ai_analyze_4in1(ticker: str, ticker_name: str, full_data: str,
                     provider: str = "openai"):
    """一次 API call 跑完 4 個解讀,節省成本."""
    api_key = get_setting(f"{provider}_api_key", "")
    if not api_key:
        return None, "未設 API key"

    system_prompt = """你是專業財經分析師,把數據翻成普通人聽得懂的白話。

嚴格規則:
1. 只描述事實,不給「建議/應該/值得」等指令性語言
2. 不預測股價、不推薦買賣
3. 術語要加白話解釋(例 PER→本益比→回本年數)
4. 每段 100 字內
5. 最後加「以上純客觀數據解讀,不構成投資建議」

請以 markdown 格式回 4 段:
### 📈 技術面
### 📊 籌碼面
### 💰 基本面
### 📰 整體印象"""

    user_prompt = f"請用白話解讀 {ticker} {ticker_name} 的數據:\n\n{full_data}"

    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return resp.content[0].text, None
        else:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=800,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return resp.choices[0].message.content, None
    except Exception as e:
        return None, f"AI 失敗: {str(e)[:200]}"


def add_to_watchlist(ticker: str, condition: str, price: float, note: str = ""):
    """加 alert 到 price_alerts.yaml."""
    import yaml
    yaml_path = INVEST_ROOT / "config" / "price_alerts.yaml"
    if not yaml_path.exists():
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text("rules: []\n", encoding="utf-8")
    try:
        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        rules = cfg.get("rules", [])
        rules.append({
            "ticker": ticker,
            "condition": condition,
            "price": float(price),
            "action": note or f"App 一鍵加入: {ticker} {condition} {price}",
        })
        cfg["rules"] = rules
        yaml_path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
        return True, "已加入"
    except Exception as e:
        return False, str(e)


def list_price_alerts():
    """列出所有 price alert rules."""
    import yaml
    yaml_path = INVEST_ROOT / "config" / "price_alerts.yaml"
    if not yaml_path.exists():
        return []
    try:
        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        return cfg.get("rules", [])
    except Exception:
        return []


def remove_price_alert(index: int):
    """根據 index 刪除 alert rule."""
    import yaml
    yaml_path = INVEST_ROOT / "config" / "price_alerts.yaml"
    if not yaml_path.exists(): return False
    try:
        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        rules = cfg.get("rules", [])
        if 0 <= index < len(rules):
            rules.pop(index)
            cfg["rules"] = rules
            yaml_path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
            return True
    except Exception:
        pass
    return False


def check_triggered_alerts():
    """掃描所有 alert rules,回傳已觸發的(條件達成,還沒被使用者按已讀)。
    觸發狀態存在 data/alert_state.json,避免重複觸發。"""
    rules = list_price_alerts()
    if not rules: return []
    state_path = DATA_DIR / "alert_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}

    triggered = []
    for idx, rule in enumerate(rules):
        tk = rule.get("ticker")
        cond = rule.get("condition", "above")
        target = float(rule.get("price", 0))
        if not tk or target <= 0: continue
        quote = fetch_yfinance_quote(tk)
        if not quote: continue
        cur = quote["price"]
        is_hit = (cur >= target) if cond == "above" else (cur <= target)
        rule_key = f"{tk}:{cond}:{target}"
        prev_state = state.get(rule_key, {})
        # 只在條件第一次達成時觸發,避免重複
        was_triggered = prev_state.get("triggered", False)
        was_read = prev_state.get("read", False)
        if is_hit and not was_triggered:
            # 新觸發
            state[rule_key] = {
                "triggered": True,
                "read": False,
                "trigger_price": cur,
                "trigger_time": datetime.now(TW).isoformat(),
            }
        elif not is_hit and was_triggered:
            # 條件離開 → reset(下次再達成才會再觸發)
            state[rule_key] = {"triggered": False, "read": False}
        if state.get(rule_key, {}).get("triggered") and not state.get(rule_key, {}).get("read"):
            triggered.append({
                "rule_key": rule_key,
                "ticker": tk,
                "condition": cond,
                "target": target,
                "trigger_price": state[rule_key].get("trigger_price", cur),
                "trigger_time": state[rule_key].get("trigger_time", ""),
                "action": rule.get("action", ""),
                "rule_idx": idx,
            })

    try:
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except Exception:
        pass
    return triggered


def mark_alert_read(rule_key: str):
    """標記 alert 為已讀(從訊息中心消失)."""
    state_path = DATA_DIR / "alert_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if rule_key in state:
            state[rule_key]["read"] = True
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    except Exception:
        pass


@st.cache_data(ttl=86400)  # 1 天
def ai_analyze(section: str, ticker: str, ticker_name: str, data_summary: str,
                provider: str = "anthropic"):
    """用 AI 做白話分析,不給投資建議.

    section: '技術面' / '籌碼面' / '基本面' / '新聞面' / '股利政策'
    data_summary: 客觀數據摘要(我們傳給 AI)
    """
    api_key = get_setting(f"{provider}_api_key", "")
    if not api_key:
        return None, f"未設 {provider} API key (去 設定 頁加入)"

    system_prompt = """你是專業財經分析師,任務是把數據翻譯成「普通人聽得懂的白話」。

嚴格規則(違反就失敗):
1. 只描述事實,不給任何「建議」「應該」「值得」等指令性語言
2. 不預測股價漲跌、不推薦買賣
3. 避免術語,術語要加括號白話解釋(例:PER → 本益比,意思是回本年數)
4. 結尾必須有句:「以上純客觀數據解讀,不構成投資建議」
5. 答案 200 字內

你只解讀「數據說了什麼」,不解讀「該怎麼做」。"""

    user_prompt = f"""請用白話解讀以下{section}數據:

股票: {ticker} {ticker_name}

數據摘要:
{data_summary}

請用 200 字以內的白話解讀,結尾加上免責聲明。"""

    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return resp.content[0].text, None
        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=400,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return resp.choices[0].message.content, None
        else:
            return None, f"不支援的 provider: {provider}"
    except Exception as e:
        return None, f"AI 呼叫失敗: {str(e)[:200]}"


def page_tw_stock_center():
    # 共用常數(放這裡讓 tab_brief 也能用)
    TW_TYPES = ("tw", "twse", "tpex", "emerging")

    # ─── Hero ───
    st.markdown("""
    <div style='background:linear-gradient(135deg, #0f766e 0%, #0a1a1f 35%, #16181d 100%);
                padding:24px 30px; border-radius:14px; margin-bottom:18px;
                border:1px solid #2f343d;
                box-shadow: inset 0 1px 0 rgba(94,234,212,0.1)'>
      <div style='font-size:0.85rem; color:#5eead4; letter-spacing:2px; margin-bottom:4px;
                  font-weight:600'>
        LEEK CHECK · v0.1
      </div>
      <div style='font-size:2.2rem; color:#fff; font-weight:800; line-height:1.1'>
        🩺 韭菜健檢
      </div>
      <div style='font-size:1.05rem; color:#5eead4; margin-top:6px'>
        買進前,先做一次韭菜健檢
      </div>
      <div style='font-size:0.85rem; color:#94a3b8; margin-top:4px; font-style:italic'>
        韭菜不是命,是健檢不夠勤
      </div>
      <div style='display:flex; gap:14px; margin-top:14px; flex-wrap:wrap; color:#cbd5e1; font-size:0.85rem'>
        <div>🩺 技術面健檢</div>
        <div>🩺 籌碼面健檢</div>
        <div>🩺 基本面健檢</div>
        <div>🩺 新聞面健檢</div>
        <div style='color:#5eead4; font-weight:600'>+ 0-100 健檢分數</div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.caption("⚖️ 純客觀數據觀察 · 不構成任何投資建議 · 數據結果不代表未來表現")
    st.markdown(
        "<div style='background:linear-gradient(135deg, #422006 0%, #1a1f27 100%);"
        "padding:8px 14px; border-radius:8px; border-left:3px solid #f59e0b;"
        "margin-top:6px; font-size:0.85rem; color:#fbbf24'>"
        "🌙 <b>本 app 是盤後分析工具,不適合盤中即時下單</b><br>"
        "<span style='color:#cbd5e1; font-size:0.78rem'>"
        "報價 ~15 分鐘延遲 · 法人籌碼 T+0 盤後 · 財報資料月報前後"
        "</span></div>",
        unsafe_allow_html=True,
    )

    ticker_map = load_ticker_map()
    if not ticker_map:
        st.error("⚠️ ticker 對照表沒抓到。請去 settings 確認 IPO 資料")
        return

    # ── Tab CSS:最小化(只給 tab 橫滑,不再強制 sticky 避免副作用)──
    st.markdown("""
    <style>
      .stTabs [data-baseweb="tab-list"] {
        overflow-x: auto !important;
        white-space: nowrap !important;
        flex-wrap: nowrap !important;
        scrollbar-width: none !important;
      }
      .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {
        height: 0 !important;
        display: none !important;
      }
      .stTabs [data-baseweb="tab"] {
        flex-shrink: 0 !important;
        white-space: nowrap !important;
      }
    </style>
    """, unsafe_allow_html=True)

    # 10 tabs (策略升頂層)
    tab_book, tab_brief, tab_market, tab_global, tab_watch, tab_search, tab_rank, tab_strat_top, tab_quiz, tab_about = st.tabs([
        "💰 記帳", "📰 晨報", "🌡️ 大盤", "🌍 多市場", "⭐ 觀察清單", "🔍 搜尋", "🏆 排行榜", "🔬 策略", "🥬 自檢", "❓ 關於"
    ])

    # ────── Tab 💰 記帳 (Phase 1 — 持股 P&L + portfolio 總覽) ──────
    with tab_book:
        st.markdown("### 💰 記帳 · 持股 Portfolio")

        _book_wl = load_json("watchlist", {"tickers": []})
        _book_items = [t for t in _book_wl.get("tickers", []) if t.get("type") in TW_TYPES]
        _book_holdings = []
        for _it in _book_items:
            _hp = compute_holding_pnl(_it)
            if _hp:
                _book_holdings.append({
                    "item": _it,
                    "info": ticker_map.get(_it["ticker"], {"name": "—", "industry": "—", "type": "twse"}),
                    "pnl": _hp,
                })

        # ── ➕ 新增 / 加碼持股 form(直接在記帳 tab 內,不用跳轉)──
        _book_all_options = [
            f"{tk} {info['name']}"
            for tk, info in ticker_map.items()
            if info.get("type") in TW_TYPES
        ]
        with st.expander("➕ 新增 / 加碼持股", expanded=(not _book_holdings)):
            with st.form("book_add_holding", clear_on_submit=True):
                ba_sel = st.selectbox(
                    "代號 / 名稱",
                    options=_book_all_options,
                    index=None,
                    placeholder="例:輸入 2330 或 台積電",
                )
                bc1, bc2, bc3 = st.columns(3)
                with bc1:
                    ba_shares = st.number_input("股數", min_value=0, value=0, step=100,
                                                  help="持有張數 × 1000(零股直接填)")
                with bc2:
                    ba_cost = st.number_input("每股成本(未含手續費)", min_value=0.0,
                                                value=0.0, step=0.5, format="%.2f",
                                                help="計算時自動加 +0.1425%")
                with bc3:
                    ba_date = st.text_input("進場日(可選)", value="",
                                              placeholder="YYYY-MM-DD")
                ba_note = st.text_input("筆記(可選)", value="")
                ba_submit = st.form_submit_button("💾 加入持股", type="primary",
                                                     use_container_width=True)
                if ba_submit:
                    if not ba_sel:
                        st.error("⚠️ 請選擇個股")
                    elif ba_shares <= 0 or ba_cost <= 0:
                        st.error("⚠️ 股數和成本都要 > 0")
                    else:
                        ba_tk = ba_sel.split(" ")[0]
                        if ba_tk not in ticker_map:
                            st.error(f"⚠️ {ba_tk} 不在資料庫")
                        else:
                            ba_info = ticker_map[ba_tk]
                            # 同檔已存在 → 加權平均加碼
                            existing = next((t for t in _book_wl["tickers"]
                                              if t.get("ticker") == ba_tk
                                              and t.get("type") == ba_info["type"]), None)
                            if existing and existing.get("shares") and existing.get("cost_per_share"):
                                old_shares = float(existing["shares"])
                                old_cost = float(existing["cost_per_share"])
                                total_shares = old_shares + ba_shares
                                weighted_cost = (old_shares * old_cost + ba_shares * ba_cost) / total_shares
                                existing["shares"] = int(total_shares)
                                existing["cost_per_share"] = round(weighted_cost, 4)
                                if ba_date.strip():
                                    existing["entry_date"] = ba_date.strip()
                                if ba_note:
                                    existing["note"] = ba_note
                                st.success(f"✅ {ba_tk} 加碼成功:總 {int(total_shares):,} 股,"
                                            f"平均成本 {weighted_cost:.2f}")
                            elif existing:
                                # 已存在但只是觀察 → 升級為持股
                                existing["shares"] = int(ba_shares)
                                existing["cost_per_share"] = float(ba_cost)
                                if ba_date.strip():
                                    existing["entry_date"] = ba_date.strip()
                                if ba_note:
                                    existing["note"] = ba_note
                                st.success(f"✅ {ba_tk} 升級為持股")
                            else:
                                # 全新加入
                                _book_wl.setdefault("tickers", []).append({
                                    "ticker": ba_tk,
                                    "type": ba_info["type"],
                                    "shares": int(ba_shares),
                                    "cost_per_share": float(ba_cost),
                                    "entry_date": ba_date.strip() if ba_date.strip() else "",
                                    "note": ba_note,
                                })
                                st.success(f"✅ 已加入 {ba_tk} {ba_info['name']}")
                            save_json("watchlist", _book_wl)
                            st.rerun()

        if not _book_holdings:
            st.info(
                "📭 還沒有持股資料。\n\n"
                "**怎麼用:**\n"
                "1. 先到 **⭐ 觀察清單** tab 加入個股\n"
                "2. 展開該檔的「⚙️ 編輯持股」\n"
                "3. 填入 **股數 + 每股成本** → 自動升級為記帳模式\n"
                "4. 回到這裡看 Portfolio 總覽 + 個股損益"
            )
        else:
            # ── Portfolio summary ──
            _total_mv = sum(h["pnl"]["mv"] for h in _book_holdings)
            _total_cost = sum(h["pnl"]["total_cost"] for h in _book_holdings)
            _total_pnl = _total_mv - _total_cost
            _total_pct = (_total_pnl / _total_cost * 100) if _total_cost > 0 else 0
            _color = "#ef4444" if _total_pnl > 0 else ("#10b981" if _total_pnl < 0 else "#94a3b8")
            _arrow = "▲" if _total_pnl > 0 else ("▼" if _total_pnl < 0 else "—")
            _sector_mv = {}
            for h in _book_holdings:
                _ind = h["info"].get("industry") or "—"
                _sector_mv[_ind] = _sector_mv.get(_ind, 0) + h["pnl"]["mv"]
            _top_sector = max(_sector_mv.items(), key=lambda x: x[1])
            _top_sector_pct = _top_sector[1] / _total_mv * 100 if _total_mv > 0 else 0
            _max_holding = max(_book_holdings, key=lambda x: x["pnl"]["mv"])
            _max_pct = _max_holding["pnl"]["mv"] / _total_mv * 100 if _total_mv > 0 else 0

            st.markdown(
                f"<div style='background:linear-gradient(135deg, {_color}33 0%, #1a1f27 80%);"
                f"padding:20px 24px; border-radius:14px; margin-bottom:14px;"
                f"border:1px solid {_color}55;"
                f"box-shadow: 0 4px 14px rgba(0,0,0,0.3)'>"
                f"<div style='display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap'>"
                f"<div>"
                f"<div style='color:#94a3b8; font-size:0.72rem; letter-spacing:1.5px; margin-bottom:4px'>"
                f"💰 PORTFOLIO 總覽 · {len(_book_holdings)} 檔持股</div>"
                f"<div style='font-size:1.8rem; color:#fff; font-weight:800; line-height:1'>"
                f"NT$ {_total_mv:,.0f}</div>"
                f"<div style='font-size:0.8rem; color:#cbd5e1; margin-top:4px'>"
                f"成本 NT$ {_total_cost:,.0f}</div>"
                f"</div>"
                f"<div style='text-align:right'>"
                f"<div style='color:#94a3b8; font-size:0.72rem; letter-spacing:1.5px; margin-bottom:4px'>未實現損益</div>"
                f"<div style='font-size:1.8rem; color:{_color}; font-weight:800; line-height:1'>"
                f"{_arrow} {_total_pnl:+,.0f}</div>"
                f"<div style='font-size:0.9rem; color:{_color}; margin-top:4px; font-weight:600'>"
                f"({_total_pct:+.2f}%)</div>"
                f"</div>"
                f"</div>"
                f"<div style='margin-top:14px; padding-top:12px; border-top:1px solid #2f343d;"
                f"display:grid; grid-template-columns:repeat(2,1fr); gap:8px; font-size:0.78rem'>"
                f"<div><span style='color:#94a3b8'>最大持股</span> "
                f"<b style='color:#fff'>{_max_holding['item']['ticker']}</b> "
                f"<span style='color:#cbd5e1'>{_max_pct:.1f}%</span></div>"
                f"<div><span style='color:#94a3b8'>最大產業</span> "
                f"<b style='color:#fff'>{_top_sector[0]}</b> "
                f"<span style='color:#cbd5e1'>{_top_sector_pct:.1f}%</span></div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if _max_pct > 40:
                st.caption(f"⚠️ 單檔 {_max_holding['item']['ticker']} 集中度 {_max_pct:.1f}% 偏高,分散可降風險。")
            if _top_sector_pct > 60:
                st.caption(f"⚠️ {_top_sector[0]} 產業集中度 {_top_sector_pct:.1f}%,建議跨產業配置。")

            # ── 加入買進/賣出記錄(扣手續費 0.4255% 賣出 / 0.1425% 買進)──
            st.markdown("##### 📋 持股明細(按損益 % 排序)")
            _sorted = sorted(_book_holdings, key=lambda x: x["pnl"]["pct"], reverse=True)
            for h in _sorted:
                _it = h["item"]; _info = h["info"]; _p = h["pnl"]
                _tk = _it["ticker"]
                _pc = "#ef4444" if _p["pnl"] > 0 else ("#10b981" if _p["pnl"] < 0 else "#94a3b8")
                _pa = "▲" if _p["pnl"] > 0 else ("▼" if _p["pnl"] < 0 else "—")
                _weight_pct = _p["mv"] / _total_mv * 100 if _total_mv > 0 else 0

                # 簡潔行式卡片
                st.markdown(
                    f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                    f"padding:12px 16px; border-radius:10px; margin-bottom:8px;"
                    f"border-left:4px solid {_pc}'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap'>"
                    f"<div style='min-width:120px'>"
                    f"<div style='font-size:1.05rem; color:#fff; font-weight:700'>{_tk} "
                    f"<span style='color:#cbd5e1; font-weight:500; font-size:0.85rem'>{_info.get('name','—')}</span></div>"
                    f"<div style='color:#94a3b8; font-size:0.7rem; margin-top:2px'>"
                    f"{_info.get('industry','—')} · 持倉 {_weight_pct:.1f}%</div>"
                    f"</div>"
                    f"<div style='text-align:center; min-width:90px'>"
                    f"<div style='color:#94a3b8; font-size:0.7rem'>股數 · 成本</div>"
                    f"<div style='color:#fff; font-size:0.85rem; font-weight:600'>"
                    f"{int(_p['shares']):,} · {_p['cost_per_share']:.2f}</div>"
                    f"</div>"
                    f"<div style='text-align:center; min-width:100px'>"
                    f"<div style='color:#94a3b8; font-size:0.7rem'>現價 · 市值</div>"
                    f"<div style='color:#fff; font-size:0.85rem; font-weight:600'>"
                    f"{_p['current_price']:.2f} · {_p['mv']:,.0f}</div>"
                    f"</div>"
                    f"<div style='text-align:right; min-width:120px'>"
                    f"<div style='color:#94a3b8; font-size:0.7rem'>未實現損益</div>"
                    f"<div style='color:{_pc}; font-size:1.0rem; font-weight:700'>"
                    f"{_pa} {_p['pnl']:+,.0f}</div>"
                    f"<div style='color:{_pc}; font-size:0.8rem; font-weight:600'>"
                    f"({_p['pct']:+.2f}%)</div>"
                    f"</div>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                # ✏️ 編輯 / 🗑️ 刪除 / 📉 部分出場
                _edit_col, _sell_col, _del_col, _pad = st.columns([1, 1, 1, 8])
                with _edit_col:
                    with st.popover("✏️", use_container_width=True, help="修改股數 / 成本 / 進場日"):
                        st.markdown(f"**編輯 {_tk} {_info.get('name','')}**")
                        with st.form(f"book_edit_{_tk}", clear_on_submit=False, border=False):
                            ec1, ec2 = st.columns(2)
                            with ec1:
                                new_s = st.number_input("股數", min_value=0,
                                                          value=int(_p["shares"]), step=100,
                                                          key=f"bk_es_{_tk}")
                            with ec2:
                                new_c = st.number_input("每股成本", min_value=0.0,
                                                          value=float(_p["cost_per_share"]),
                                                          step=0.5, format="%.2f",
                                                          key=f"bk_ec_{_tk}")
                            new_d = st.text_input("進場日", value=_it.get("entry_date", ""),
                                                    placeholder="YYYY-MM-DD",
                                                    key=f"bk_ed_{_tk}")
                            new_n = st.text_input("筆記", value=_it.get("note", ""),
                                                    key=f"bk_en_{_tk}")
                            if st.form_submit_button("💾 儲存", type="primary",
                                                       use_container_width=True):
                                for t in _book_wl["tickers"]:
                                    if t.get("ticker") == _tk and t.get("type") == _it.get("type"):
                                        t["shares"] = int(new_s)
                                        t["cost_per_share"] = float(new_c)
                                        t["entry_date"] = new_d.strip()
                                        t["note"] = new_n
                                save_json("watchlist", _book_wl)
                                st.success("✅ 已更新"); st.rerun()
                with _sell_col:
                    with st.popover("📉", use_container_width=True, help="部分出場 / 全部賣出"):
                        st.markdown(f"**賣出 {_tk}**")
                        st.caption(f"目前持有 {int(_p['shares']):,} 股 · 成本 {_p['cost_per_share']:.2f}")
                        with st.form(f"book_sell_{_tk}", clear_on_submit=False, border=False):
                            sell_s = st.number_input("賣出股數", min_value=0,
                                                       max_value=int(_p["shares"]),
                                                       value=int(_p["shares"]), step=100,
                                                       key=f"bk_ss_{_tk}")
                            sell_p = st.number_input("賣出價", min_value=0.0,
                                                       value=float(_p["current_price"]),
                                                       step=0.5, format="%.2f",
                                                       key=f"bk_sp_{_tk}")
                            if st.form_submit_button("💵 確認賣出", type="primary",
                                                       use_container_width=True):
                                if sell_s <= 0:
                                    st.error("⚠️ 賣出股數必須 > 0")
                                else:
                                    # 算實現損益(扣賣出 0.4255% = 0.1425% 手續費 + 0.3% 證交稅)
                                    SELL_FEE = 0.004255
                                    realized_gross = sell_s * (sell_p - _p["cost_incl_fee"])
                                    realized_net = realized_gross - sell_s * sell_p * SELL_FEE
                                    remaining = int(_p["shares"] - sell_s)
                                    for t in _book_wl["tickers"]:
                                        if t.get("ticker") == _tk and t.get("type") == _it.get("type"):
                                            if remaining > 0:
                                                t["shares"] = remaining
                                            else:
                                                # 全部賣出 → 移回純觀察(保留 ticker 不刪)
                                                t.pop("shares", None)
                                                t.pop("cost_per_share", None)
                                                t.pop("entry_date", None)
                                    save_json("watchlist", _book_wl)
                                    _color_r = "🔴" if realized_net > 0 else "🟢"
                                    st.success(
                                        f"✅ 已賣出 {int(sell_s):,} 股 @ {sell_p:.2f}\n\n"
                                        f"實現損益(扣手續費+稅):**{realized_net:+,.0f}** {_color_r}"
                                    )
                                    st.rerun()
                with _del_col:
                    if st.button("🗑️", key=f"book_del_{_tk}",
                                   use_container_width=True,
                                   help="從觀察清單完全移除"):
                        _book_wl["tickers"] = [
                            t for t in _book_wl["tickers"]
                            if not (t.get("ticker") == _tk and t.get("type") == _it.get("type"))
                        ]
                        save_json("watchlist", _book_wl)
                        st.toast(f"已移除 {_tk}", icon="🗑️"); st.rerun()

            st.caption(
                "💡 **損益計算**:含買進 0.1425% 手續費(gross 慣例,跟券商一致)。"
                "實際賣出會再扣 ~0.4255%(手續費 + 證交稅),用 📉 按鈕賣出會自動扣。"
            )

    # ────── Tab 晨報 ──────
    with tab_brief:
        from datetime import datetime as _dt
        now = _dt.now(TW)
        hour = now.hour
        if hour < 12: greeting = "🌅 早安"
        elif hour < 18: greeting = "☀️ 午安"
        else: greeting = "🌙 晚安"

        # 📅 日期 + 問候(放最頂,固定不 flicker)
        st.markdown(f"""
        <div style='background:linear-gradient(135deg, #0f766e 0%, #0a1a1f 50%, #16181d 100%);
                    padding:20px 24px; border-radius:14px; margin-bottom:14px;
                    border:1px solid #2f343d'>
          <div style='color:#5eead4; font-size:0.75rem; letter-spacing:2px; font-weight:700'>
            {now.strftime('%Y-%m-%d')} · {['週一','週二','週三','週四','週五','週六','週日'][now.weekday()]}
          </div>
          <div style='color:#fff; font-size:1.8rem; font-weight:800; margin-top:4px; line-height:1.1'>
            {greeting},今日市場健檢
          </div>
          <div style='color:#5eead4; font-size:0.9rem; margin-top:6px'>
            開盤前 5 分鐘看一眼 · 盤後分析,不適合盤中即時下單
          </div>
        </div>
        """, unsafe_allow_html=True)

        # ⭐ PRO 智能助理 placeholder(放問候下面,不再閃)
        _brief_ai_slot = st.empty()

        # ── 2. 觀察清單健康巡禮(PRO,顯示全部,排序:有警示優先) ──
        st.divider()
        st.markdown("### ⭐ 觀察清單健康巡禮 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                      unsafe_allow_html=True)
        wl_data = load_json("watchlist", {"tickers": []})
        wl_briefing = [t for t in wl_data.get("tickers", []) if t.get("type") in TW_TYPES]
        # 讀晨報精選設定(優先 session_state,fallback file)
        _featured_tks = st.session_state.get("briefing_featured")
        if _featured_tks is None:
            _b_settings = load_json("settings", {}) or {}
            _featured_tks = _b_settings.get("briefing_featured", []) or []
        _valid_featured_tks = [tk for tk in _featured_tks
                                  if tk in {t["ticker"] for t in wl_briefing}]
        if not pro_gate("觀察清單健康巡禮 (一鍵掃描所有持股)"):
            pass  # 非 PRO 顯示 paywall,跳過下方掃描
        elif not wl_briefing:
            st.info("還沒加觀察清單,去 ⭐ tab 加幾檔追蹤")
        else:
            # 顯示提示:有沒有精選
            if _valid_featured_tks:
                st.caption(f"📋 共 {len(wl_briefing)} 檔 · 🌅 精選 {len(_valid_featured_tks)} 檔(詳細卡顯示)")
            else:
                st.caption(f"📋 共 {len(wl_briefing)} 檔 · 💡 去 ⭐ 觀察清單選晨報精選最多 5 檔,可看更詳細指標")
            # session_state 快取:同 session 內算一次就不重算(避免 re-render 卡 / 閃)
            import hashlib as _hl_w
            from datetime import date as _dt_w
            wl_sig = ",".join(t["ticker"] for t in wl_briefing)
            wl_cache_key = f"wl_briefing_rows_{_dt_w.today().isoformat()}_{_hl_w.md5(wl_sig.encode()).hexdigest()[:8]}"
            if wl_cache_key in st.session_state:
                all_rows = st.session_state[wl_cache_key]
            else:
                with st.spinner(f"巡禮 {len(wl_briefing)} 檔..."):
                    all_rows = []
                    for item in wl_briefing:
                        tk_b = item["ticker"]
                        if tk_b not in ticker_map:
                            all_rows.append({"tk": tk_b, "name": "(資料庫無此檔)",
                                               "tag": "⚠️ 查不到", "color": "#94a3b8",
                                               "d1": 0, "d5": 0, "price": 0, "priority": 99})
                            continue
                        df_b = load_local_ohlcv(tk_b, 30)
                        name_b = ticker_map[tk_b]["name"]
                        if df_b is None or len(df_b) < 6:
                            all_rows.append({"tk": tk_b, "name": name_b,
                                               "tag": "⚪ 無本機資料", "color": "#94a3b8",
                                               "d1": 0, "d5": 0, "price": 0, "priority": 98})
                            continue
                        last_c = float(df_b["close"].iloc[-1])
                        prev_c = float(df_b["close"].iloc[-2])
                        d1_pct = (last_c/prev_c - 1) * 100
                        d5_pct = (last_c/float(df_b["close"].iloc[-6]) - 1) * 100

                        tag, color, priority = "✅ 正常", "#5eead4", 50
                        if abs(d1_pct) >= 5:
                            if d1_pct < 0:
                                tag, color, priority = "🚨 單日急殺", "#dc2626", 1
                            else:
                                tag, color, priority = "🚀 單日大漲", "#ef4444", 2
                        elif d5_pct <= -10:
                            tag, color, priority = "📉 5 日跌深", "#10b981", 4
                        elif d5_pct >= 10:
                            tag, color, priority = "🔥 5 日強勢", "#ef4444", 5
                        vol_last = float(df_b["volume"].iloc[-1])
                        vol_avg = float(df_b["volume"].tail(20).mean()) if len(df_b) >= 20 else vol_last
                        if vol_avg > 0 and vol_last / vol_avg >= 2.5:
                            tag, color, priority = "📊 量爆(>20日均 2.5x)", "#fbbf24", 3

                        all_rows.append({
                            "tk": tk_b, "name": name_b, "tag": tag, "color": color,
                            "d1": d1_pct, "d5": d5_pct, "price": last_c,
                            "priority": priority,
                        })
                    # 排序:警示優先(priority 小的在前)
                    all_rows.sort(key=lambda x: x["priority"])
                    st.session_state[wl_cache_key] = all_rows
            # 分組:精選(詳細卡) vs 其餘(精簡 + 收進 expander)
            _featured_rows = [a for a in all_rows if a["tk"] in _valid_featured_tks]
            _other_rows = [a for a in all_rows if a["tk"] not in _valid_featured_tks]

            def _render_compact(a):
                price_str = f"{a['price']:.2f}" if a['price'] else "—"
                stats_str = (f"今 {a['d1']:+.1f}% · 5d {a['d5']:+.1f}%"
                              if a['price'] else "—")
                st.markdown(
                    f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:10px 14px;"
                    f"border-radius:8px; border-left:3px solid {a['color']};"
                    f"margin-bottom:5px; display:flex; justify-content:space-between; align-items:center'>"
                    f"<div><span style='color:#fff; font-weight:700'>{a['tk']}</span> "
                    f"<span style='color:#cbd5e1'>{a['name']}</span> "
                    f"<span style='color:{a['color']}; font-weight:600; margin-left:6px'>{a['tag']}</span></div>"
                    f"<div style='color:#94a3b8; font-size:0.85rem; text-align:right'>"
                    f"<div style='color:#fff'>{price_str}</div>"
                    f"<div style='font-size:0.75rem'>{stats_str}</div></div></div>",
                    unsafe_allow_html=True,
                )

            def _render_detailed(a):
                tk_d = a["tk"]
                info_d = ticker_map.get(tk_d, {})
                ind_d = info_d.get("industry", "—")
                price_str = f"{a['price']:.2f}" if a['price'] else "—"
                # 額外抓 7 個面向(都有 cache)
                fi_20d = it_20d = de_20d = 0
                yoy = None
                health = None
                try:
                    inst_df = load_finmind_for_ticker(tk_d, "TaiwanStockInstitutionalInvestorsBuySell")
                    if inst_df is not None and not inst_df.empty:
                        i2 = inst_df.copy()
                        i2["date"] = pd.to_datetime(i2["date"])
                        i2 = i2.sort_values("date").tail(40)
                        last20d = i2["date"].unique()[-20:]
                        i2["net"] = i2["buy"] - i2["sell"]
                        sub20 = i2[i2["date"].isin(last20d)]
                        agg = sub20.groupby("name")["net"].sum() / 1000
                        fi_20d = int(agg.get("Foreign_Investor", 0))
                        it_20d = int(agg.get("Investment_Trust", 0))
                        de_20d = int(agg.get("Dealer_self", 0))
                except Exception:
                    pass
                try:
                    rev = load_finmind_for_ticker(tk_d, "TaiwanStockMonthRevenue")
                    if rev is not None and not rev.empty:
                        r2 = rev.copy()
                        r2["date"] = pd.to_datetime(r2["date"])
                        r2 = r2.sort_values("date")
                        if len(r2) >= 13:
                            cur = float(r2["revenue"].iloc[-1])
                            prev = float(r2["revenue"].iloc[-13])
                            if prev > 0:
                                yoy = (cur / prev - 1) * 100
                except Exception:
                    pass
                # 健檢分數(粗略,跟個股健檢頁同邏輯但簡化)
                try:
                    df_h = load_local_ohlcv(tk_d, 250)
                    if df_h is not None and len(df_h) >= 60:
                        ind_h = calc_technical_indicators(df_h)
                        lt = ind_h.iloc[-1]
                        tech_h = {
                            "price": float(lt["close"]),
                            "ma5": float(lt["ma5"] or 0),
                            "ma20": float(lt["ma20"] or 0),
                            "ma60": float(lt["ma60"] or 0),
                            "rsi": float(lt["rsi"] or 50),
                            "k": float(lt["k"] or 50),
                            "d": float(lt["d"] or 50),
                        }
                        chip_h = {"foreign_20d": fi_20d, "invtrust_20d": it_20d, "dealer_20d": de_20d}
                        funda_h = {"rev_yoy": yoy or 0}
                        comp, _ = calc_composite_score(tech_h, chip_h, funda_h)
                        health = int(comp)
                except Exception:
                    pass

                fi_s = f"外資 {fi_20d:+,}"
                it_s = f"投信 {it_20d:+,}"
                de_s = f"自營 {de_20d:+,}"
                yoy_s = f"{yoy:+.1f}%" if yoy is not None else "—"
                health_s = f"{health}/100" if health is not None else "—"

                st.markdown(
                    f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%); padding:14px 16px;"
                    f"border-radius:12px; border-left:4px solid {a['color']};"
                    f"margin-bottom:10px; box-shadow: 0 2px 8px rgba(0,0,0,0.3)'>"
                    # 標題列
                    f"<div style='display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px'>"
                    f"<div><div style='color:{a['color']}; font-size:0.78rem; font-weight:700; margin-bottom:2px'>"
                    f"🌅 {a['tag']}</div>"
                    f"<div style='color:#fff; font-size:1.05rem; font-weight:700'>{tk_d} "
                    f"<span style='color:#cbd5e1; font-weight:500; font-size:0.85rem'>{a['name']}</span></div>"
                    f"<div style='color:#94a3b8; font-size:0.7rem'>{ind_d}</div>"
                    f"</div>"
                    f"<div style='text-align:right'>"
                    f"<div style='color:#fff; font-size:1.2rem; font-weight:700'>{price_str}</div>"
                    f"<div style='color:#94a3b8; font-size:0.75rem'>今 {a['d1']:+.1f}% · 5d {a['d5']:+.1f}%</div>"
                    f"</div>"
                    f"</div>"
                    # 7 面向細項
                    f"<div style='display:grid; grid-template-columns:repeat(2,1fr); gap:6px 14px;"
                    f"font-size:0.78rem; color:#cbd5e1; padding-top:8px; border-top:1px solid #2f343d'>"
                    f"<div>🏛️ 法人 20d:<br><span style='color:#fff'>{fi_s} / {it_s} / {de_s}</span></div>"
                    f"<div>📈 月營收 YoY:<br><span style='color:#fff'>{yoy_s}</span></div>"
                    f"<div>🩺 健檢分:<br><span style='color:#fff; font-weight:700'>{health_s}</span></div>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            # 精選 → 詳細卡
            if _featured_rows:
                st.markdown("#### 🌅 精選詳細卡")
                for a in _featured_rows:
                    _render_detailed(a)
            else:
                # 沒選精選 → 顯示警示優先的 top 3 詳細
                _top3 = [a for a in all_rows if a["priority"] < 50][:3]
                if _top3:
                    st.markdown("#### 🚨 自動精選(警示優先 Top 3)")
                    for a in _top3:
                        _render_detailed(a)
                    _other_rows = [a for a in all_rows if a not in _top3]
            # 其餘 → 收進 expander
            if _other_rows:
                with st.expander(f"📋 其餘觀察 {len(_other_rows)} 檔(精簡)", expanded=False):
                    for a in _other_rows:
                        _render_compact(a)
            alerts = [a for a in all_rows if a["priority"] < 50]  # for prompt use

        # ── 📡 真 alpha 訊號偵測(PRO,memory 驗證過策略) ──
        st.divider()
        st.markdown("### 📡 今日真 alpha 訊號偵測 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                      unsafe_allow_html=True)
        st.caption("以下為**條件偵測**,非投資建議。基於本 app 1 年量化研究驗證過的歷史訊號。")

        if pro_gate("真 alpha 訊號偵測器(20+ 條驗證過的策略)"):
            sig_tab1, sig_tab2 = st.tabs([
                "📈 月營收 YoY (歷史 60d alpha +4%)",
                "🎯 量縮漲停 (歷史 20d alpha +4.8%)",
            ])

            with sig_tab1:
                with st.spinner("掃描全市場月營收 YoY..."):
                    rev_hits = scan_revenue_yoy_signals(min_yoy=30.0, top_n=12)
                if rev_hits:
                    st.caption(f"✅ 觸發條件 {len(rev_hits)} 檔 · 條件:月營收 YoY > 30% + 20d 成交額 > 1 億")
                    for h in rev_hits:
                        if h["tk"] not in ticker_map: continue
                        info_s = ticker_map[h["tk"]]
                        c_btn1, c_btn2 = st.columns([3, 1])
                        with c_btn1:
                            st.markdown(
                                f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                                f"padding:10px 14px; border-radius:8px; border-left:3px solid #14b8a6;"
                                f"display:flex; justify-content:space-between; align-items:center'>"
                                f"<div><b style='color:#fff'>{h['tk']}</b> "
                                f"<span style='color:#cbd5e1'>{info_s['name']}</span>"
                                f"<span style='color:#94a3b8; font-size:0.75rem; margin-left:6px'>"
                                f"{info_s['industry']}</span></div>"
                                f"<div style='color:#5eead4; font-weight:700'>YoY {h['yoy']:+.1f}%</div>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                        with c_btn2:
                            if st.button("⭐ 加觀察", key=f"sig_rev_add_{h['tk']}",
                                           use_container_width=True):
                                wl = load_json("watchlist", {"tickers": []})
                                existing = {t["ticker"] for t in wl.get("tickers", [])}
                                if h["tk"] in existing:
                                    st.toast(f"{h['tk']} 已在觀察清單", icon="⚠️")
                                else:
                                    wl.setdefault("tickers", []).append({
                                        "ticker": h["tk"],
                                        "type": info_s["type"], "note": "",
                                    })
                                    save_json("watchlist", wl)
                                    st.toast(f"已加入 {h['tk']}", icon="⭐")
                else:
                    st.info("⚪ 今日無觸發此訊號")
                st.caption("📊 歷史回測:此訊號 60 日平均 +3.95% (t=24.19, n=24K, OOS 2020-25 robust)")

            with sig_tab2:
                with st.spinner("掃描量縮漲停..."):
                    qlu_hits = scan_quiet_limitup(top_n=12)
                if qlu_hits:
                    st.caption(f"✅ 觸發 {len(qlu_hits)} 檔 · 條件:漲幅 ≥ 9.5% + 量縮(VR < 0.8)")
                    for h in qlu_hits:
                        if h["tk"] not in ticker_map: continue
                        info_s = ticker_map[h["tk"]]
                        c_btn1, c_btn2 = st.columns([3, 1])
                        with c_btn1:
                            st.markdown(
                                f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                                f"padding:10px 14px; border-radius:8px; border-left:3px solid #ef4444;"
                                f"display:flex; justify-content:space-between; align-items:center'>"
                                f"<div><b style='color:#fff'>{h['tk']}</b> "
                                f"<span style='color:#cbd5e1'>{info_s['name']}</span>"
                                f"<span style='color:#94a3b8; font-size:0.75rem; margin-left:6px'>"
                                f"{h['date']}</span></div>"
                                f"<div style='color:#ef4444; font-weight:700'>"
                                f"+{h['chg']:.1f}% · VR {h['vr']:.2f}</div>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                        with c_btn2:
                            if st.button("⭐ 加觀察", key=f"sig_qlu_add_{h['tk']}",
                                           use_container_width=True):
                                wl = load_json("watchlist", {"tickers": []})
                                existing = {t["ticker"] for t in wl.get("tickers", [])}
                                if h["tk"] in existing:
                                    st.toast(f"{h['tk']} 已在觀察清單", icon="⚠️")
                                else:
                                    wl.setdefault("tickers", []).append({
                                        "ticker": h["tk"],
                                        "type": info_s["type"], "note": "",
                                    })
                                    save_json("watchlist", wl)
                                    st.toast(f"已加入 {h['tk']}", icon="⭐")
                else:
                    st.info("⚪ 今日無觸發此訊號")
                st.caption("📊 歷史回測:此訊號 20 日平均 +4.83% (n=5437, 2020-25 robust)")

        # ── 1. 大盤現況 mini ──
        st.markdown("### 🌡️ 大盤一句話")
        try:
            import yfinance as yf
            twii_h = yf.Ticker("^TWII").history(period="1y", auto_adjust=False)
            vix_h = yf.Ticker("^VIX").history(period="1mo", auto_adjust=False)
            if not twii_h.empty:
                twii_c = float(twii_h["Close"].iloc[-1])
                twii_p = float(twii_h["Close"].iloc[-2]) if len(twii_h) >= 2 else twii_c
                twii_chg_pct = (twii_c/twii_p - 1) * 100
                ma200_v = float(twii_h["Close"].tail(200).mean()) if len(twii_h) >= 200 else None
                dist_ma200_v = (twii_c/ma200_v - 1) * 100 if ma200_v else 0

                # 描述狀態(可活潑,不指示動作)
                if dist_ma200_v > 30:
                    temp = "🔥 過熱(過去 10 年僅 5% 時間在此區間)"
                elif dist_ma200_v > 15:
                    temp = "🟠 偏熱 — 牛市後段"
                elif dist_ma200_v > -5:
                    temp = "🟢 健康成長期"
                elif dist_ma200_v > -15:
                    temp = "🟡 盤整 / 偏冷"
                else:
                    temp = "💎 大跌中(過去 10 年此區間 70% 案例 1 年後為正)"

                vix_v = float(vix_h["Close"].iloc[-1]) if not vix_h.empty else 0
                if vix_v >= 30:
                    vix_msg = f"😱 極度恐慌(VIX {vix_v:.0f})"
                elif vix_v >= 20:
                    vix_msg = f"😨 緊張(VIX {vix_v:.0f})"
                elif vix_v >= 15:
                    vix_msg = f"😐 平靜(VIX {vix_v:.0f})"
                else:
                    vix_msg = f"😎 過度樂觀(VIX {vix_v:.0f})"

                t_col = "#ef4444" if twii_chg_pct > 0 else "#10b981"
                t_arr = "▲" if twii_chg_pct > 0 else "▼"

                st.markdown(f"""
                <div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:18px 22px; border-radius:12px;
                            border-left:4px solid #5eead4'>
                  <div style='display:flex; justify-content:space-between; align-items:center'>
                    <div>
                      <div style='color:#94a3b8; font-size:0.75rem'>加權指數</div>
                      <div style='color:{t_col}; font-size:1.8rem; font-weight:800; line-height:1'>
                        {twii_c:,.0f}
                      </div>
                      <div style='color:{t_col}; font-size:0.9rem'>{t_arr} {twii_chg_pct:+.2f}%</div>
                    </div>
                    <div style='text-align:right'>
                      <div style='color:#94a3b8; font-size:0.75rem'>距 MA200</div>
                      <div style='color:#fff; font-size:1.4rem; font-weight:700'>{dist_ma200_v:+.1f}%</div>
                    </div>
                  </div>
                  <div style='margin-top:12px; color:#e4e6eb; font-size:1rem; font-weight:600'>
                    👉 {temp}
                  </div>
                  <div style='margin-top:4px; color:#94a3b8; font-size:0.85rem'>{vix_msg}</div>
                </div>
                """, unsafe_allow_html=True)
        except Exception as e:
            st.info(f"⚪ 大盤資料抓取失敗: {e}")

        # ── 3. 全市場異常 (top 5 漲幅 / 跌幅 / 量爆) ──
        st.divider()
        st.markdown("### 📊 全市場異常 Top 5")
        try:
            cache_files = list(TW_OHLCV_CACHE.glob("*.parquet"))
            mkt_results = []
            for f_m in cache_files[:300]:  # 限 300 檔以控速度
                tk_m = f_m.stem
                if tk_m not in ticker_map: continue
                df_m = load_local_ohlcv(tk_m, 5)
                if df_m is None or len(df_m) < 2: continue
                last = df_m.iloc[-1]; prev = df_m.iloc[-2]
                if last["close"] == 0 or prev["close"] == 0: continue
                chg_m = (last["close"]/prev["close"] - 1) * 100
                mkt_results.append({"tk": tk_m, "name": ticker_map[tk_m]["name"],
                                     "chg": chg_m, "vol": int(last["volume"])})
            if mkt_results:
                df_m = pd.DataFrame(mkt_results)
                colA, colB = st.columns(2)
                with colA:
                    st.markdown("**🔴 漲幅前 5**")
                    for _, r in df_m.sort_values("chg", ascending=False).head(5).iterrows():
                        st.markdown(
                            f"<div style='color:#cbd5e1; font-size:0.88rem; padding:3px 0'>"
                            f"<b style='color:#fff'>{r['tk']}</b> {r['name']} "
                            f"<span style='color:#ef4444; font-weight:700; margin-left:6px'>+{r['chg']:.2f}%</span></div>",
                            unsafe_allow_html=True,
                        )
                with colB:
                    st.markdown("**🟢 跌幅前 5**")
                    for _, r in df_m.sort_values("chg").head(5).iterrows():
                        st.markdown(
                            f"<div style='color:#cbd5e1; font-size:0.88rem; padding:3px 0'>"
                            f"<b style='color:#fff'>{r['tk']}</b> {r['name']} "
                            f"<span style='color:#10b981; font-weight:700; margin-left:6px'>{r['chg']:.2f}%</span></div>",
                            unsafe_allow_html=True,
                        )
        except Exception as e:
            st.info(f"⚪ 排行算不出來: {e}")

        # ── 4. 大盤新聞 3 則 ──
        st.divider()
        st.markdown("### 📰 今日大盤新聞")
        try:
            news_brief = fetch_stock_news("台股", "加權指數", max_n=5)
            if news_brief:
                for nb in news_brief:
                    st.markdown(
                        f"<a href='{nb['link']}' target='_blank' style='text-decoration:none'>"
                        f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:10px 14px; border-radius:8px;"
                        f"border:1px solid #2f343d; margin-bottom:5px'>"
                        f"<div style='color:#e4e6eb; font-size:0.9rem'>{nb['title']}</div>"
                        f"<div style='color:#8b92a0; font-size:0.7rem; margin-top:3px'>"
                        f"📰 {nb['source']}</div></div></a>",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("⚪ 抓不到新聞")
                news_brief = []
        except Exception as e:
            st.caption(f"新聞抓取失敗: {e}")
            news_brief = []

        st.divider()

        # ── 🌅 智能個人化早晨重點(PRO)— 填到頂部 placeholder ──
        with _brief_ai_slot.container():
         st.markdown("### 🌅 智能個人化早晨重點 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                      unsafe_allow_html=True)
         st.caption("AI 整合你的觀察清單 + 大盤狀況,告訴你今日該注意什麼(純客觀,不指示動作)")

         if pro_gate("智能個人化早晨重點 (每日個人化分析)"):
            if _get_gemini_key():
                from datetime import date as _dt_b
                import hashlib as _hl_b
                # cache 必須跟著觀察清單內容變(不只是檔數,還含 ticker 順序)
                wl_sig = ",".join(t["ticker"] for t in (wl_briefing or []))
                wl_hash = _hl_b.md5(wl_sig.encode()).hexdigest()[:8]
                cache_k_brief = f"watchbrief:{_dt_b.today().isoformat()}:{wl_hash}"
                # 給使用者看「即將分析」的標的清單
                if wl_briefing:
                    _preview = " · ".join(
                        f"{t['ticker']} {ticker_map.get(t['ticker'], {}).get('name', '')}"
                        for t in wl_briefing[:20]
                    )
                    st.caption(f"📋 將分析你的 **{len(wl_briefing)} 檔觀察清單**:{_preview}")
                else:
                    st.warning("⚠️ 你還沒加任何觀察清單個股 — 晨報只會做大盤分析。"
                                "去 ⭐ 觀察清單 tab 加股,下次再點。")

                # 組大盤狀況
                market_lines = []
                try:
                    if not twii_h.empty:
                        market_lines.append(
                            f"加權指數 {twii_c:,.0f}({twii_chg_pct:+.2f}%),"
                            f"距 MA200 {dist_ma200_v:+.1f}% — {temp}"
                        )
                        market_lines.append(vix_msg)
                except Exception:
                    pass

                # 觀察清單每檔詳細狀態
                watch_lines = []
                if wl_briefing:
                    for item_w in wl_briefing[:20]:
                        tk_w = item_w["ticker"]
                        if tk_w not in ticker_map: continue
                        info_w_b = ticker_map[tk_w]
                        df_w = load_local_ohlcv(tk_w, 30)
                        if df_w is None or len(df_w) < 6:
                            watch_lines.append(f"  • {tk_w} {info_w_b['name']} — 本機無資料")
                            continue
                        last_c_w = float(df_w["close"].iloc[-1])
                        prev_c_w = float(df_w["close"].iloc[-2])
                        d1_w = (last_c_w/prev_c_w - 1) * 100
                        d5_w = (last_c_w/float(df_w["close"].iloc[-6]) - 1) * 100
                        # 檢查 alpha 訊號(過濾異常 YoY:inf / 低基期 / >300%)
                        sig_tags = []
                        try:
                            import numpy as _np
                            rev_w = load_finmind_for_ticker(tk_w, "TaiwanStockMonthRevenue")
                            if rev_w is not None and not rev_w.empty:
                                rev_w = rev_w.copy()
                                rev_w["date"] = pd.to_datetime(rev_w["date"])
                                rev_w = rev_w.sort_values("date")
                                if len(rev_w) >= 13:
                                    cur_r = float(rev_w["revenue"].iloc[-1])
                                    prev_r = float(rev_w["revenue"].iloc[-13])
                                    if prev_r >= 1e7 and cur_r > 0:
                                        yoy_w = (cur_r / prev_r - 1) * 100
                                        if _np.isfinite(yoy_w) and 30 < yoy_w < 300:
                                            sig_tags.append(f"📈 月營收YoY +{yoy_w:.0f}%")
                        except Exception:
                            pass
                        tag_str = " ".join(sig_tags) if sig_tags else ""
                        watch_lines.append(
                            f"  • {tk_w} {info_w_b['name']} ({info_w_b['industry']}): "
                            f"{last_c_w:.2f}, 今 {d1_w:+.1f}%, 5d {d5_w:+.1f}% {tag_str}"
                        )

                if not market_lines and not watch_lines:
                    st.info("⚪ 大盤資料或觀察清單沒準備好,稍後再試")
                else:
                    brief_prompt = f"""請用「韭菜健檢」風格,做我今日早晨開盤前的個人化簡報。

【今日大盤狀況】
{chr(10).join(market_lines) if market_lines else '(資料抓不到)'}

【⭐ 我的觀察清單({len(wl_briefing) if wl_briefing else 0} 檔)— 必須每一檔都點到!】
{chr(10).join(watch_lines) if watch_lines else '(空,使用者還沒加觀察清單)'}

【大盤新聞頭條】
{chr(10).join(f'  • {n["title"]}' for n in news_brief[:5])}

請給我:
1. 🌡️ **今天大盤一句話狀況**(1-2 句)
2. ⭐ **我的觀察清單逐檔點評** — **必須**對上述每一檔都給一句話狀況(描述近況、為什麼值得看、有無 alpha 訊號)。**不可以只點 2-3 檔就跳過**。
3. ⚠️ **開盤可能影響你觀察清單的事件** — 連結新聞頭條到你的持股

規則:
- 純客觀觀察、不指示買/賣動作、不報明牌、不給目標價
- 觀察清單每檔都要點到名,不可省略
- 直接從第 1 點開始,不要開場白(禁:「好的」「以下是」)
- 不要結尾贅述
"""

                    render_ai_section(
                        prompt_base=brief_prompt,
                        cache_key=cache_k_brief,
                        ss_prefix="ai_watchbrief",
                        button_label="🔍 查看今日個人化早晨重點",
                        no_key_hint="去「❓ 關於」加智能 key 即可自動產生個人化重點",
                    )
            else:
                st.info("💡 去「❓ 關於」加智能 key 即可一鍵自動產生個人化重點")

        st.divider()
        st.caption("💡 PRO 用戶未來會在每天 8:30 收到推播,毋須打開 app")

    # ────── Tab 0: 觀察清單 ──────
    with tab_watch:
        # 如果目前卡片被點開 → 直接渲染 inline 健檢頁,不要顯示卡牌
        if st.session_state.get("_inline_view_ticker"):
            inline_tk = st.session_state["_inline_view_ticker"]
            if inline_tk in ticker_map:
                inline_info = ticker_map[inline_tk]

                # 回卡牆按鈕(放最上面)
                back_c1, back_c2 = st.columns([1, 5])
                with back_c1:
                    if st.button("🔙 回卡牆", use_container_width=True,
                                  key="inline_back"):
                        st.session_state.pop("_inline_view_ticker", None)
                        st.rerun()
                with back_c2:
                    st.markdown(f"""
                    <div style='padding:8px 0; color:#94a3b8; font-size:0.9rem'>
                      🎴 翻開卡片 → <b style='color:#e4e6eb'>{inline_tk} {inline_info['name']}</b>
                      · {inline_info['industry'] or '—'}
                    </div>
                    """, unsafe_allow_html=True)

                st.divider()

                # ── 🔔 設定價格警示 ──
                with st.expander("🔔 設定價格警示(達標 push 到訊息中心)", expanded=False):
                    quote_curr = fetch_yfinance_quote(inline_tk)
                    cur_p = quote_curr["price"] if quote_curr else 0
                    with st.form(f"alert_form_{inline_tk}", clear_on_submit=True):
                        ac1, ac2 = st.columns([1, 2])
                        cond_in = ac1.selectbox(
                            "條件",
                            options=["above", "below"],
                            format_func=lambda x: "📈 漲到" if x == "above" else "📉 跌到",
                        )
                        price_in = ac2.number_input(
                            "目標價",
                            min_value=0.01,
                            value=float(cur_p) if cur_p > 0 else 100.0,
                            step=1.0, format="%.2f",
                        )
                        add_alert = st.form_submit_button("🔔 新增警示",
                                                            type="primary",
                                                            use_container_width=True)
                        if add_alert:
                            ok, msg = add_to_watchlist(inline_tk, cond_in, price_in,
                                                          f"{inline_info['name']}")
                            if ok:
                                st.success(f"✅ 已設警示: {inline_tk} "
                                              f"{'漲到' if cond_in == 'above' else '跌到'} "
                                              f"NT$ {price_in:.2f}")
                                st.rerun()
                            else:
                                st.error(f"❌ {msg}")

                # ── ⭐ PRO 區塊:健檢分數 + AI 報告(放最上面,user 一打開就看)──
                _score_slot = st.empty()
                _ai_slot = st.empty()
                st.divider()

                # ── 即時報價區 ──
                quote_i = fetch_yfinance_quote(inline_tk)
                if quote_i:
                    p = quote_i["price"]
                    chg = p - quote_i["prev_close"]
                    chg_pct = chg / quote_i["prev_close"] * 100 if quote_i["prev_close"] > 0 else 0
                    if chg > 0: col_q, ar = "#ef4444", "▲"
                    elif chg < 0: col_q, ar = "#10b981", "▼"
                    else: col_q, ar = "#8b92a0", "—"
                    st.markdown(f"""
                    <div style='display:grid; grid-template-columns:repeat(4, 1fr);
                                gap:12px; margin-bottom:6px'>
                      <div style='background:#1e2128; padding:14px 18px; border-radius:10px;
                                  border-left:4px solid {col_q}'>
                        <div style='color:#8b92a0; font-size:0.78rem'>收盤</div>
                        <div style='color:{col_q}; font-size:1.9rem; font-weight:700; line-height:1'>
                          {p:.2f}
                        </div>
                        <div style='color:{col_q}; font-size:0.95rem; margin-top:2px'>
                          {ar} {abs(chg):.2f} ({chg_pct:+.2f}%)
                        </div>
                      </div>
                      <div style='background:#1e2128; padding:14px 18px; border-radius:10px'>
                        <div style='color:#8b92a0; font-size:0.78rem'>開盤</div>
                        <div style='color:#e4e6eb; font-size:1.4rem; font-weight:600'>{quote_i['open']:.2f}</div>
                      </div>
                      <div style='background:#1e2128; padding:14px 18px; border-radius:10px'>
                        <div style='color:#8b92a0; font-size:0.78rem'>高 / 低</div>
                        <div style='color:#e4e6eb; font-size:1.4rem; font-weight:600'>
                          <span style='color:#ef4444'>{quote_i['high']:.2f}</span>
                          <span style='color:#8b92a0; font-size:1rem'> / </span>
                          <span style='color:#10b981'>{quote_i['low']:.2f}</span>
                        </div>
                      </div>
                      <div style='background:#1e2128; padding:14px 18px; border-radius:10px'>
                        <div style='color:#8b92a0; font-size:0.78rem'>成交量</div>
                        <div style='color:#e4e6eb; font-size:1.4rem; font-weight:600'>{quote_i['volume']:,}</div>
                      </div>
                    </div>
                    """, unsafe_allow_html=True)
                    st.caption(f"yfinance ~15 分鐘延遲 · {quote_i['asof']}")

                # ── 走勢圖 plotly ──
                st.markdown("### 📈 走勢圖")
                cc1, cc2 = st.columns([3, 1])
                with cc1:
                    period_label_i = st.radio(
                        "期間", ["1 個月", "3 個月", "6 個月", "1 年", "2 年"],
                        index=2, horizontal=True, label_visibility="collapsed",
                        key=f"inline_period_{inline_tk}",
                    )
                with cc2:
                    chart_type_i = st.radio(
                        "圖型", ["📈 折線", "🕯️ K 線"],
                        index=0, horizontal=True, label_visibility="collapsed",
                        key=f"inline_chart_type_{inline_tk}",
                    )
                period_days_i = {"1 個月": 22, "3 個月": 66, "6 個月": 132,
                                  "1 年": 252, "2 年": 504}[period_label_i]
                chart_df_i = load_local_ohlcv(inline_tk, period_days_i + 200)

                if chart_df_i is None or len(chart_df_i) < 5:
                    st.warning(f"⚠️ 本機沒有 {inline_tk} 的 OHLCV 資料")
                else:
                    import plotly.graph_objects as go
                    from plotly.subplots import make_subplots
                    ind_i = calc_technical_indicators(chart_df_i.copy())
                    view_i = ind_i.tail(period_days_i).copy()
                    # 判斷大盤色調 (起始 vs 結尾)
                    start_p = view_i["close"].iloc[0]
                    end_p = view_i["close"].iloc[-1]
                    line_color = "#ef4444" if end_p >= start_p else "#10b981"
                    fill_color = ("rgba(239,68,68,0.10)" if end_p >= start_p
                                   else "rgba(16,185,129,0.10)")

                    fig_i = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                            vertical_spacing=0.03,
                                            row_heights=[0.78, 0.22])

                    if chart_type_i == "📈 折線":
                        # 折線(收盤) + 填色
                        fig_i.add_trace(go.Scatter(
                            x=view_i["date"], y=view_i["close"],
                            mode="lines", line=dict(color=line_color, width=2.2),
                            fill="tozeroy", fillcolor=fill_color,
                            name="收盤", showlegend=False,
                            hovertemplate="%{x|%Y-%m-%d}<br>收盤 %{y:.2f}<extra></extra>",
                        ), row=1, col=1)
                    else:
                        # K 線
                        fig_i.add_trace(go.Candlestick(
                            x=view_i["date"], open=view_i["open"], high=view_i["high"],
                            low=view_i["low"], close=view_i["close"],
                            increasing_line_color="#ef4444", increasing_fillcolor="#ef4444",
                            decreasing_line_color="#10b981", decreasing_fillcolor="#10b981",
                            showlegend=False, name="K",
                        ), row=1, col=1)

                    for ma_col, c_ma, n_ma in [
                        ("ma20", "#14b8a6", "MA20"),
                        ("ma60", "#5eead4", "MA60"),
                    ]:
                        if ma_col in view_i.columns:
                            fig_i.add_trace(go.Scatter(
                                x=view_i["date"], y=view_i[ma_col],
                                mode="lines", name=n_ma,
                                line=dict(color=c_ma, width=1.2, dash="dot"),
                            ), row=1, col=1)

                    vol_c = ["#ef4444" if c >= o else "#10b981"
                              for c, o in zip(view_i["close"], view_i["open"])]
                    fig_i.add_trace(go.Bar(x=view_i["date"], y=view_i["volume"],
                                            marker_color=vol_c, opacity=0.6,
                                            showlegend=False),
                                     row=2, col=1)
                    fig_i.update_layout(
                        height=480, paper_bgcolor="#16181d", plot_bgcolor="#1e2128",
                        font=dict(color="#e4e6eb", size=11),
                        xaxis_rangeslider_visible=False,
                        margin=dict(l=10, r=10, t=10, b=10),
                        legend=dict(orientation="h", y=1.05, x=1, xanchor="right",
                                    bgcolor="rgba(0,0,0,0)"),
                        hovermode="x unified",
                    )
                    fig_i.update_xaxes(gridcolor="#2f343d",
                                        rangebreaks=[dict(bounds=["sat", "mon"])])
                    fig_i.update_yaxes(gridcolor="#2f343d")
                    st.plotly_chart(fig_i, use_container_width=True,
                                     config={"displayModeBar": False})

                # ── 健檢分數 ──
                ohlcv_i = load_local_ohlcv(inline_tk, 250)
                tech_i, chip_i, funda_i = None, None, None
                if ohlcv_i is not None and len(ohlcv_i) >= 20:
                    indi_t = calc_technical_indicators(ohlcv_i)
                    lt = indi_t.iloc[-1]
                    tech_i = {
                        "price": float(lt["close"]),
                        "ma5": float(lt["ma5"]) if not pd.isna(lt["ma5"]) else 0,
                        "ma20": float(lt["ma20"]) if not pd.isna(lt["ma20"]) else 0,
                        "ma60": float(lt["ma60"]) if not pd.isna(lt["ma60"]) else 0,
                        "ma200": float(lt["ma200"]) if not pd.isna(lt["ma200"]) else 0,
                        "rsi": float(lt["rsi"]) if not pd.isna(lt["rsi"]) else 50,
                        "k": float(lt["k"]) if not pd.isna(lt["k"]) else 50,
                        "d": float(lt["d"]) if not pd.isna(lt["d"]) else 50,
                    }
                inst_i = load_finmind_for_ticker(inline_tk, "TaiwanStockInstitutionalInvestorsBuySell")
                if inst_i is not None and not inst_i.empty:
                    i2 = inst_i.copy()
                    i2["date"] = pd.to_datetime(i2["date"])
                    i2 = i2.sort_values("date").tail(60)
                    i2["net"] = i2["buy"] - i2["sell"]
                    last20_d = i2["date"].unique()[-20:]
                    sub20_i = i2[i2["date"].isin(last20_d)]
                    agg20_i = sub20_i.groupby("name")["net"].sum() / 1000
                    chip_i = {
                        "foreign_20d": int(agg20_i.get("Foreign_Investor", 0)),
                        "invtrust_20d": int(agg20_i.get("Investment_Trust", 0)),
                        "dealer_20d": int(agg20_i.get("Dealer_self", 0)),
                    }
                per_i = load_finmind_for_ticker(inline_tk, "TaiwanStockPER")
                rev_i = load_finmind_for_ticker(inline_tk, "TaiwanStockMonthRevenue")
                funda_i = {}
                if per_i is not None and not per_i.empty:
                    per_i["date"] = pd.to_datetime(per_i["date"])
                    lp = per_i.sort_values("date").iloc[-1]
                    funda_i["per"] = float(lp.get("PER", 0))
                    funda_i["pbr"] = float(lp.get("PBR", 0))
                    funda_i["yield"] = float(lp.get("dividend_yield", 0))
                if rev_i is not None and not rev_i.empty:
                    rev_i["date"] = pd.to_datetime(rev_i["date"])
                    rev_i = rev_i.sort_values("date")
                    rev_i["yoy"] = rev_i["revenue"].pct_change(12) * 100
                    last_yoy_i = rev_i["yoy"].iloc[-1]
                    if not pd.isna(last_yoy_i):
                        funda_i["rev_yoy"] = float(last_yoy_i)

                composite_i, sub_i = calc_composite_score(tech_i, chip_i, funda_i)
                s_color, s_ring, s_label = (
                    ("#14b8a6", "rgba(20,184,166,0.18)", "體質很好")
                    if composite_i >= 70 else
                    ("#f59e0b", "rgba(245,158,11,0.18)", "普通")
                    if composite_i >= 50 else
                    ("#f43f5e", "rgba(244,63,94,0.18)", "體質不好")
                )
                # 填入頂部 _score_slot placeholder (PRO 會員限定)
                with _score_slot.container():
                    title_with_help(
                        "### 🩺 健檢分數 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                        "健檢分數",
                    )
                    if pro_gate("健檢分數 0-100"):
                        st.markdown(f"""
                        <div style='background:linear-gradient(135deg, #1f2937 0%, #1a1f27 100%);
                                    padding:22px 26px; border-radius:14px;
                                    border:1px solid #2f343d;
                                    display:flex; align-items:center; gap:22px'>
                          <div style='width:120px; height:120px; border-radius:50%;
                                      background:{s_ring}; border:3px solid {s_color};
                                      display:flex; flex-direction:column; align-items:center;
                                      justify-content:center; flex-shrink:0;
                                      box-shadow: 0 0 20px {s_ring}'>
                            <div style='font-size:2.1rem; color:#fff; font-weight:800; line-height:1'>
                              {composite_i}
                            </div>
                            <div style='font-size:0.65rem; color:#94a3b8; margin-top:2px'>/ 100</div>
                            <div style='font-size:0.8rem; color:{s_color};
                                        margin-top:3px; font-weight:700'>{s_label}</div>
                          </div>
                          <div style='flex:1; display:grid; grid-template-columns:repeat(3,1fr); gap:12px'>
                            <div style='background:#16181d; padding:10px 12px; border-radius:8px;
                                        border-left:3px solid #5eead4'>
                              <div style='font-size:0.7rem; color:#94a3b8'>📈 技術</div>
                              <div style='font-size:1.4rem; color:#fff; font-weight:700; line-height:1.1'>
                                {sub_i["技術"]}
                              </div>
                            </div>
                            <div style='background:#16181d; padding:10px 12px; border-radius:8px;
                                        border-left:3px solid #5eead4'>
                              <div style='font-size:0.7rem; color:#94a3b8'>📊 籌碼</div>
                              <div style='font-size:1.4rem; color:#fff; font-weight:700; line-height:1.1'>
                                {sub_i["籌碼"]}
                              </div>
                            </div>
                            <div style='background:#16181d; padding:10px 12px; border-radius:8px;
                                        border-left:3px solid #5eead4'>
                              <div style='font-size:0.7rem; color:#94a3b8'>💰 基本</div>
                              <div style='font-size:1.4rem; color:#fff; font-weight:700; line-height:1.1'>
                                {sub_i["基本"]}
                              </div>
                            </div>
                          </div>
                        </div>
                        """, unsafe_allow_html=True)
                        # 中期 60 日視角註腳(2026-06 backtest 驗證)
                        st.caption(
                            "📊 適用 **中期 60 日視角** · 歷史驗證(2020-26):"
                            "70+ 體質股 60 日 +10.93% / win 60.3% / vs 0050 alpha +4.57pp(n=194)。"
                            "OOS 2023-26 alpha +9.91pp(n=56)。短線/長線 alpha 衰退。"
                        )

                # ── 3 個 mini 區塊:技術 / 籌碼 / 基本 ──
                tcol1, tcol2, tcol3 = st.columns(3)
                with tcol1:
                    st.markdown("**📈 技術摘要**")
                    if tech_i:
                        kv = tech_i.get("k", 50); dv = tech_i.get("d", 50)
                        rsi_v = tech_i.get("rsi", 50)
                        st.metric("KD (9日)", f"K={kv:.0f} D={dv:.0f}",
                                    "🟢 黃金交叉" if kv > dv else "🔴 死亡交叉")
                        st.metric("RSI (14日)", f"{rsi_v:.0f}",
                                    "🟠 超買" if rsi_v > 70 else
                                    "🟢 超賣" if rsi_v < 30 else "⚪ 健康")
                        ma_st = ("🟢 多頭排列" if (tech_i["price"] > tech_i["ma5"] > tech_i["ma20"] > tech_i["ma60"])
                                  else "🔴 空頭排列" if (tech_i["price"] < tech_i["ma5"] < tech_i["ma20"] < tech_i["ma60"])
                                  else "🟡 糾結")
                        st.metric("均線排列", ma_st)
                    else:
                        st.caption("⚪ 無技術資料")

                with tcol2:
                    st.markdown("**📊 籌碼摘要 (20 日)**")
                    if chip_i:
                        f20 = chip_i.get("foreign_20d", 0)
                        st.metric("外資 (張)", f"{f20:+,}",
                                    "🟢 買超" if f20 > 0 else "🔴 賣超")
                        st.metric("投信 (張)", f"{chip_i.get('invtrust_20d', 0):+,}")
                        st.metric("自營 (張)", f"{chip_i.get('dealer_20d', 0):+,}")
                    else:
                        st.caption("⚪ 無法人資料")

                with tcol3:
                    st.markdown("**💰 基本摘要**")
                    if funda_i:
                        per_v = funda_i.get("per", 0)
                        st.metric("PER", f"{per_v:.1f}",
                                    "🟢 低估" if 0 < per_v < 15 else
                                    "🟠 偏高" if per_v > 25 else "⚪ 一般")
                        if "yield" in funda_i:
                            st.metric("殖利率", f"{funda_i['yield']:.2f}%")
                        if "rev_yoy" in funda_i:
                            yoy_v = funda_i["rev_yoy"]
                            st.metric("月營收 YoY", f"{yoy_v:+.1f}%",
                                        "🟢 強勁" if yoy_v > 10 else
                                        "🔴 衰退" if yoy_v < -10 else "⚪ 平穩")
                    else:
                        st.caption("⚪ 無財報資料")

                st.divider()

                # ── 一年高/低 + 距離 ──
                df_1y = load_local_ohlcv(inline_tk, 252)
                if df_1y is not None and len(df_1y) > 20:
                    yr_high = float(df_1y["high"].tail(252).max())
                    yr_low = float(df_1y["low"].tail(252).min())
                    cur_price_y = float(df_1y["close"].iloc[-1])
                    dist_high = (cur_price_y / yr_high - 1) * 100
                    dist_low = (cur_price_y / yr_low - 1) * 100
                    st.markdown("### 📏 一年位置")
                    yc1, yc2 = st.columns(2)
                    yc1.markdown(
                        f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:14px 18px; border-radius:10px; border-left:3px solid #ef4444'>"
                        f"<div style='color:#94a3b8; font-size:0.78rem'>一年最高</div>"
                        f"<div style='color:#fff; font-size:1.5rem; font-weight:700; line-height:1.1; margin-top:3px'>{yr_high:.2f}</div>"
                        f"<div style='color:#ef4444; font-size:0.85rem; margin-top:3px'>目前距高點 {dist_high:.2f}%</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    yc2.markdown(
                        f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:14px 18px; border-radius:10px; border-left:3px solid #10b981'>"
                        f"<div style='color:#94a3b8; font-size:0.78rem'>一年最低</div>"
                        f"<div style='color:#fff; font-size:1.5rem; font-weight:700; line-height:1.1; margin-top:3px'>{yr_low:.2f}</div>"
                        f"<div style='color:#10b981; font-size:0.85rem; margin-top:3px'>目前距低點 +{dist_low:.2f}%</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                st.divider()

                # ── 體質掃描(2 維度,接單 + 獲利) ──
                title_with_help("### 🏥 體質掃描(連續方向)", "體質掃描")

                def _trend_arrows(values, n=6):
                    """把連續 n 期數值轉成方向箭頭。"""
                    if len(values) < 2: return "—"
                    arr = []
                    for i in range(1, min(len(values), n + 1)):
                        prev_v = values[-i - 1] if i + 1 <= len(values) else None
                        curr_v = values[-i]
                        if prev_v is None: continue
                        if curr_v > prev_v * 1.05:
                            arr.append(("↑", "#ef4444"))
                        elif curr_v > prev_v:
                            arr.append(("↗", "#fb923c"))
                        elif curr_v < prev_v * 0.95:
                            arr.append(("↓", "#10b981"))
                        elif curr_v < prev_v:
                            arr.append(("↘", "#22c55e"))
                        else:
                            arr.append(("→", "#94a3b8"))
                    arr.reverse()
                    return "".join(f"<span style='color:{c}; font-size:1.4rem; font-weight:700; margin:0 2px'>{a}</span>" for a, c in arr)

                # 接單能力(月營收 YoY 連續 6 期 — 過濾 inf / 極端值)
                rev_arrows_html = "—"
                yoy_list = []
                if rev_i is not None and not rev_i.empty:
                    import numpy as _np_y
                    rev_i_sorted = rev_i.sort_values("date").reset_index(drop=True)
                    # 手動算 YoY 並過濾異常
                    raw_yoy = []
                    for i_y in range(12, len(rev_i_sorted)):
                        cur_y = float(rev_i_sorted["revenue"].iloc[i_y])
                        prev_y = float(rev_i_sorted["revenue"].iloc[i_y - 12])
                        if prev_y < 1e7 or cur_y <= 0: continue  # 過濾低基期
                        y_v = (cur_y / prev_y - 1) * 100
                        if not _np_y.isfinite(y_v): continue
                        if abs(y_v) > 500: continue  # 極端值排除
                        raw_yoy.append(y_v)
                    yoy_list = raw_yoy[-6:]
                    if yoy_list:
                        rev_arrows_html = _trend_arrows(yoy_list, 6)

                # 獲利能力(EPS 連續 6 期)
                eps_arrows_html = "—"
                eps_data = None
                try:
                    fin = load_finmind_for_ticker(inline_tk, "TaiwanStockFinancialStatements")
                    if fin is not None and not fin.empty:
                        eps_data = fin[fin["type"] == "EPS"].copy()
                        if not eps_data.empty:
                            eps_data["date"] = pd.to_datetime(eps_data["date"])
                            eps_data = eps_data.sort_values("date")
                            eps_list = eps_data["value"].dropna().tail(6).tolist()
                            if eps_list:
                                eps_arrows_html = _trend_arrows(eps_list, 6)
                except Exception:
                    pass

                # ── 經營能力(毛利率趨勢) ──
                margin_arrows_html = "—"
                margin_data = None
                full_fin = fetch_full_financial_statements(inline_tk)
                if full_fin is not None and not full_fin.empty:
                    fin_p = full_fin.copy()
                    rev_p = fin_p[fin_p["type"] == "Revenue"].sort_values("date").tail(6)
                    gp_p = fin_p[fin_p["type"] == "GrossProfit"].sort_values("date").tail(6)
                    if not rev_p.empty and not gp_p.empty:
                        gp_merged = pd.merge(rev_p, gp_p, on="date", suffixes=("_rev", "_gp"))
                        if not gp_merged.empty:
                            gp_merged["margin"] = gp_merged["value_gp"] / gp_merged["value_rev"] * 100
                            margin_data = gp_merged["margin"].tolist()
                            if margin_data:
                                margin_arrows_html = _trend_arrows(margin_data, 6)

                # ── 償債能力(流動比 + 負債比) ──
                solvency_arrows_html = "—"
                current_ratio_data = None
                debt_ratio_data = None
                bs_df = fetch_balance_sheet(inline_tk)
                if bs_df is not None and not bs_df.empty:
                    bs_p = bs_df.pivot_table(
                        index="date", columns="type", values="value", aggfunc="first"
                    ).sort_index().tail(6)
                    if not bs_p.empty:
                        current_ratios = []
                        debt_ratios = []
                        for _, row in bs_p.iterrows():
                            ca = row.get("CurrentAssets", 0) or 0
                            cl = row.get("CurrentLiabilities", 0) or 0
                            ncl = row.get("NoncurrentLiabilities", 0) or 0
                            ta = row.get("TotalAssets", 0) or 0
                            if cl > 0:
                                current_ratios.append(ca / cl * 100)
                            if ta > 0:
                                debt_ratios.append((cl + ncl) / ta * 100)
                        if current_ratios:
                            current_ratio_data = current_ratios
                            # 流動比上升 = 償債能力提升 (上升好)
                            solvency_arrows_html = _trend_arrows(current_ratios, 6)
                        if debt_ratios:
                            debt_ratio_data = debt_ratios

                _panel_bg = "background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%);"
                st.markdown(
                    f"<div style='{_panel_bg} padding:14px 18px; border-radius:10px;"
                    f"border:1px solid #2f343d; border-left:3px solid #f59e0b; margin-bottom:8px'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center'>"
                    f"<div style='color:#fff; font-weight:600'>📈 接單能力 <span style='color:#94a3b8; font-size:0.75rem'>(月營收 YoY)</span></div>"
                    f"<div>{rev_arrows_html}</div>"
                    f"</div></div>"
                    f"<div style='{_panel_bg} padding:14px 18px; border-radius:10px;"
                    f"border:1px solid #2f343d; border-left:3px solid #f59e0b; margin-bottom:8px'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center'>"
                    f"<div style='color:#fff; font-weight:600'>💰 獲利能力 <span style='color:#94a3b8; font-size:0.75rem'>(EPS 趨勢)</span></div>"
                    f"<div>{eps_arrows_html}</div>"
                    f"</div></div>"
                    f"<div style='{_panel_bg} padding:14px 18px; border-radius:10px;"
                    f"border:1px solid #2f343d; border-left:3px solid #14b8a6; margin-bottom:8px'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center'>"
                    f"<div style='color:#fff; font-weight:600'>⚙️ 經營能力 <span style='color:#94a3b8; font-size:0.75rem'>(毛利率趨勢)</span></div>"
                    f"<div>{margin_arrows_html}</div>"
                    f"</div></div>"
                    f"<div style='{_panel_bg} padding:14px 18px; border-radius:10px;"
                    f"border:1px solid #2f343d; border-left:3px solid #14b8a6; margin-bottom:8px'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center'>"
                    f"<div style='color:#fff; font-weight:600'>🛡️ 償債能力 <span style='color:#94a3b8; font-size:0.75rem'>(流動比趨勢)</span></div>"
                    f"<div>{solvency_arrows_html}</div>"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )
                st.caption("💡 ↑↗ = 紅(上升) / ↓↘ = 綠(下降) / → = 平 · 4 維度體質一目了然")

                st.divider()

                # ── 📊 趨勢強弱(短/中/長) ──
                st.markdown("### 📊 趨勢強弱")
                trend_summary_txt = ""
                if tech_i and ohlcv_i is not None and len(ohlcv_i) >= 200:
                    closes_ow = ohlcv_i["close"].tail(200).tolist()
                    cur = closes_ow[-1]
                    # 短 / 中 / 長 = 距 MA5 / MA60 / MA200
                    ma5_v = tech_i.get("ma5", 0)
                    ma60_v = tech_i.get("ma60", 0)
                    ma200_v = tech_i.get("ma200", 0)
                    def _dir_chip(label_dir, cur_p, ma_v, period_desc):
                        if ma_v == 0:
                            return f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:12px 16px; border-radius:10px; border:1px solid #2f343d'><div style='color:#94a3b8; font-size:0.78rem'>{label_dir} <span style='font-size:0.7rem'>{period_desc}</span></div><div style='color:#8b92a0; margin-top:4px'>—</div></div>"
                        dist = (cur_p / ma_v - 1) * 100
                        if dist > 5:
                            arr_v, col_v, txt = "↑↑", "#ef4444", "多頭強勢"
                        elif dist > 0:
                            arr_v, col_v, txt = "↗", "#fb923c", "偏多"
                        elif dist > -5:
                            arr_v, col_v, txt = "↘", "#22c55e", "偏空"
                        else:
                            arr_v, col_v, txt = "↓↓", "#10b981", "空頭強勢"
                        return (
                            f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:12px 16px; border-radius:10px; border:1px solid #2f343d; border-left:3px solid {col_v}'>"
                            f"<div style='color:#94a3b8; font-size:0.78rem'>{label_dir} <span style='font-size:0.7rem; color:#64748b'>{period_desc}</span></div>"
                            f"<div style='color:{col_v}; font-size:1.3rem; font-weight:700; margin-top:3px; line-height:1.1'>{arr_v} {txt}</div>"
                            f"<div style='color:#94a3b8; font-size:0.72rem; margin-top:2px'>距 {label_dir}: {dist:+.1f}%</div>"
                            f"</div>"
                        )
                    st.markdown(
                        f"<div style='display:flex; flex-direction:column; gap:8px'>"
                        + _dir_chip("短線", cur, ma5_v, "(MA5)")
                        + _dir_chip("中線", cur, ma60_v, "(MA60)")
                        + _dir_chip("長線", cur, ma200_v, "(MA200)")
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                    trend_summary_txt = (
                        f"\n  • 短線(距 MA5): {(cur/ma5_v-1)*100 if ma5_v else 0:+.2f}%"
                        f"\n  • 中線(距 MA60): {(cur/ma60_v-1)*100 if ma60_v else 0:+.2f}%"
                        f"\n  • 長線(距 MA200): {(cur/ma200_v-1)*100 if ma200_v else 0:+.2f}%"
                    )

                st.divider()

                # ── 🔬 同業比較 (PER / 殖利率 / 月營收 YoY) ──
                st.markdown("### 🔬 同業比較")
                peer_summary_txt = ""
                if inline_info.get("industry") and inline_info["industry"] != "—":
                    industry_n = inline_info["industry"]
                    peers = [tk for tk, v in ticker_map.items()
                              if v.get("industry") == industry_n and tk != inline_tk]
                    # 取同業最多 30 檔算同業中位數
                    peer_per_list = []
                    peer_yld_list = []
                    peer_rev_list = []
                    for ptk in peers[:30]:
                        try:
                            pper = load_finmind_for_ticker(ptk, "TaiwanStockPER")
                            if pper is not None and not pper.empty:
                                pper["date"] = pd.to_datetime(pper["date"])
                                lp_p = pper.sort_values("date").iloc[-1]
                                if lp_p.get("PER", 0) > 0:
                                    peer_per_list.append(float(lp_p.get("PER", 0)))
                                if lp_p.get("dividend_yield", 0) > 0:
                                    peer_yld_list.append(float(lp_p.get("dividend_yield", 0)))
                            prev = load_finmind_for_ticker(ptk, "TaiwanStockMonthRevenue")
                            if prev is not None and not prev.empty:
                                prev["date"] = pd.to_datetime(prev["date"])
                                prev_s = prev.sort_values("date")
                                prev_yoy = (prev_s["revenue"].pct_change(12) * 100).iloc[-1]
                                if not pd.isna(prev_yoy):
                                    peer_rev_list.append(float(prev_yoy))
                        except Exception:
                            continue

                    # 算同業中位數
                    import statistics
                    cur_per = funda_i.get("per", 0) if funda_i else 0
                    cur_yld = funda_i.get("yield", 0) if funda_i else 0
                    cur_rev_yoy = funda_i.get("rev_yoy", 0) if funda_i else 0

                    median_per = statistics.median(peer_per_list) if peer_per_list else 0
                    median_yld = statistics.median(peer_yld_list) if peer_yld_list else 0
                    median_rev = statistics.median(peer_rev_list) if peer_rev_list else 0

                    def _peer_chip(label_p, cur_v, med_v, unit, lower_better=True):
                        if med_v == 0:
                            return f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:12px 16px; border-radius:10px; border:1px solid #2f343d'><div style='color:#94a3b8; font-size:0.78rem'>{label_p}</div><div style='color:#8b92a0; margin-top:4px'>同業資料不足</div></div>"
                        if lower_better:
                            better = cur_v < med_v
                        else:
                            better = cur_v > med_v
                        col_p = "#14b8a6" if better else "#f59e0b"
                        ratio = (cur_v - med_v) / med_v * 100
                        return (
                            f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:12px 16px; border-radius:10px; border:1px solid #2f343d; border-left:3px solid {col_p}'>"
                            f"<div style='color:#94a3b8; font-size:0.78rem'>{label_p}</div>"
                            f"<div style='display:flex; gap:14px; align-items:baseline; margin-top:3px'>"
                            f"<div><span style='color:#fff; font-size:1.2rem; font-weight:700'>{cur_v:.2f}{unit}</span><span style='color:#94a3b8; font-size:0.7rem; margin-left:6px'>本檔</span></div>"
                            f"<div><span style='color:#cbd5e1; font-size:0.95rem'>{med_v:.2f}{unit}</span><span style='color:#94a3b8; font-size:0.7rem; margin-left:4px'>同業中位</span></div>"
                            f"</div>"
                            f"<div style='color:{col_p}; font-size:0.72rem; margin-top:3px; font-weight:600'>差 {ratio:+.1f}%</div>"
                            f"</div>"
                        )

                    st.caption(f"📦 {industry_n} 同業 {len(peer_per_list)} 檔比較")
                    st.markdown(
                        f"<div style='display:flex; flex-direction:column; gap:8px'>"
                        + _peer_chip("PER 本益比", cur_per, median_per, "x", lower_better=True)
                        + _peer_chip("殖利率", cur_yld, median_yld, "%", lower_better=False)
                        + _peer_chip("月營收 YoY", cur_rev_yoy, median_rev, "%", lower_better=False)
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                    peer_summary_txt = (
                        f"\n  • PER 本檔 {cur_per:.2f}x vs 同業中位 {median_per:.2f}x"
                        f"\n  • 殖利率 本檔 {cur_yld:.2f}% vs 同業中位 {median_yld:.2f}%"
                        f"\n  • 月營收 YoY 本檔 {cur_rev_yoy:+.2f}% vs 同業中位 {median_rev:+.2f}%"
                    )

                st.divider()

                # ── 💵 歷年股利 + 📅 行事曆 ──
                st.markdown("### 💵 歷年股利 + 行事曆")
                div_summary_txt = ""
                calendar_summary_txt = ""
                div_df = fetch_dividend_calendar(inline_tk)

                if div_df is not None and not div_df.empty:
                    div_df2 = div_df.copy()
                    cash_col = "CashEarningsDistribution"
                    if cash_col in div_df2.columns:
                        # 歷年累計(by year)
                        div_df2["year"] = pd.to_datetime(div_df2["date"]).dt.year
                        # 各 year 加總 cash earnings(同年可能有多季)
                        year_div = div_df2.groupby("year")[cash_col].sum().reset_index()
                        year_div = year_div.sort_values("year", ascending=False).head(5)
                        years_s = year_div["year"].tolist()
                        cash_s = year_div[cash_col].tolist()
                        rows_html = "".join(
                            f"<tr><td style='padding:6px 8px; color:#cbd5e1'>{int(y)}</td>"
                            f"<td style='padding:6px 8px; color:#fff; text-align:right; font-weight:600'>{c:.2f}</td></tr>"
                            for y, c in zip(years_s, cash_s)
                        )
                        st.markdown(
                            f"<table style='width:100%; background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); border-radius:10px; border-collapse:collapse; border:1px solid #2f343d'>"
                            f"<thead><tr><th style='padding:8px; text-align:left; color:#94a3b8; font-size:0.78rem'>年度</th>"
                            f"<th style='padding:8px; text-align:right; color:#94a3b8; font-size:0.78rem'>累計現金股利</th></tr></thead>"
                            f"<tbody>{rows_html}</tbody></table>",
                            unsafe_allow_html=True,
                        )
                        div_summary_txt = ", ".join(
                            f"{int(y)}: {c:.2f}" for y, c in zip(years_s, cash_s)
                        )

                        # 行事曆 — 找最近一筆除權息 + 支付日
                        try:
                            latest_div = div_df2.sort_values("date", ascending=False).iloc[0]
                            ex_div_date = str(latest_div.get("CashExDividendTradingDate", "")).strip()
                            pay_date = str(latest_div.get("CashDividendPaymentDate", "")).strip()
                            ann_date = str(latest_div.get("AnnouncementDate", "")).strip()
                            cash_amt = float(latest_div.get(cash_col, 0))
                            cal_lines = []
                            if ex_div_date:
                                cal_lines.append(
                                    f"<div style='padding:6px 0'>📅 <b style='color:#fff'>除權息交易日</b>: "
                                    f"<span style='color:#14b8a6; font-weight:700'>{ex_div_date}</span>"
                                    f"<span style='color:#94a3b8; margin-left:6px'>配 {cash_amt:.2f} 元</span></div>"
                                )
                            if pay_date:
                                cal_lines.append(
                                    f"<div style='padding:6px 0'>💵 <b style='color:#fff'>現金股利發放日</b>: "
                                    f"<span style='color:#14b8a6; font-weight:700'>{pay_date}</span></div>"
                                )
                            if ann_date:
                                cal_lines.append(
                                    f"<div style='padding:6px 0; color:#94a3b8; font-size:0.85rem'>📢 公告日 {ann_date}</div>"
                                )
                            if cal_lines:
                                st.markdown(
                                    f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%); "
                                    f"padding:14px 18px; border-radius:10px; border:1px solid #2f343d;"
                                    f"border-left:3px solid #14b8a6; margin-top:10px'>"
                                    + "".join(cal_lines) + "</div>",
                                    unsafe_allow_html=True,
                                )
                                calendar_summary_txt = (
                                    f"最新除權息日 {ex_div_date}(配 {cash_amt:.2f} 元),"
                                    f"發放 {pay_date}"
                                )
                        except Exception:
                            pass
                    else:
                        st.caption("⚪ 股利資料格式不符")
                else:
                    st.caption("⚪ 無歷年股利資料(可能此檔不配息)")

                # 智能健檢報告的 prompt 在這裡組(實際 UI 渲染在頂部 _ai_slot)
                def _fmt_d(d):
                    if not d: return "(無資料)"
                    return "\n".join(f"  • {k}: {v}" for k, v in d.items())

                # 融資融券最新餘額
                margin_summary = ""
                try:
                    mp = load_finmind_for_ticker(inline_tk, "TaiwanStockMarginPurchaseShortSale")
                    if mp is not None and not mp.empty:
                        mp2 = mp.copy()
                        mp2["date"] = pd.to_datetime(mp2["date"])
                        mp2 = mp2.sort_values("date").tail(5)
                        latest_mp = mp2.iloc[-1]
                        margin_5d_chg = int(latest_mp["MarginPurchaseTodayBalance"]) - int(mp2.iloc[0]["MarginPurchaseTodayBalance"])
                        short_5d_chg = int(latest_mp["ShortSaleTodayBalance"]) - int(mp2.iloc[0]["ShortSaleTodayBalance"])
                        margin_summary = (
                            f"\n  • 融資餘額: {int(latest_mp['MarginPurchaseTodayBalance']):,} 張 "
                            f"(5日 {margin_5d_chg:+,})"
                            f"\n  • 融券餘額: {int(latest_mp['ShortSaleTodayBalance']):,} 張 "
                            f"(5日 {short_5d_chg:+,})"
                        )
                except Exception:
                    pass

                # 股權分布 (外資佔比 + 散戶/大戶分級)
                share_summary = ""
                try:
                    sh = load_finmind_for_ticker(inline_tk, "TaiwanStockShareholding")
                    if sh is not None and not sh.empty:
                        sh2 = sh.copy()
                        sh2["date"] = pd.to_datetime(sh2["date"])
                        latest_sh = sh2.sort_values("date").iloc[-1]
                        foreign_pct = float(latest_sh.get("ForeignInvestmentSharesRatio", 0))
                        share_summary = f"\n  • 外資持股佔比: {foreign_pct:.2f}%"
                    hsh = load_finmind_for_ticker(inline_tk, "TaiwanStockHoldingSharesPer")
                    if hsh is not None and not hsh.empty:
                        hsh2 = hsh.copy()
                        hsh2["date"] = pd.to_datetime(hsh2["date"])
                        latest_d = hsh2["date"].max()
                        last_hsh = hsh2[hsh2["date"] == latest_d]
                        # 散戶(< 50 張)粗略合計
                        retail_levels = ["1-999", "1,000-5,000", "5,001-10,000",
                                          "10,001-15,000", "15,001-20,000",
                                          "20,001-30,000", "30,001-40,000",
                                          "40,001-50,000"]
                        retail_pct = last_hsh[last_hsh["HoldingSharesLevel"].isin(retail_levels)]["percent"].sum()
                        share_summary += f"\n  • 散戶(< 50 張)合計佔比: {retail_pct:.2f}%"
                except Exception:
                    pass

                # 一年高低位置
                yr_summary = ""
                if df_1y is not None and len(df_1y) > 20:
                    yr_summary = (
                        f"\n  • 一年最高: {yr_high:.2f}(距離 {dist_high:.2f}%)"
                        f"\n  • 一年最低: {yr_low:.2f}(距離 +{dist_low:.2f}%)"
                    )

                # 體質掃描文字
                health_scan_txt = ""
                if rev_i is not None:
                    yoy_list_s = (rev_i.sort_values("date")["revenue"].pct_change(12) * 100).dropna().tail(6).tolist()
                    if yoy_list_s:
                        health_scan_txt = f"\n  • 接單(月營收 YoY 近6期): {' → '.join(f'{v:+.1f}%' for v in yoy_list_s)}"
                if eps_data is not None and not eps_data.empty:
                    eps_list_s = eps_data["value"].dropna().tail(6).tolist()
                    if eps_list_s:
                        health_scan_txt += f"\n  • 獲利(EPS 近6期): {' → '.join(f'{v:.2f}' for v in eps_list_s)}"
                if margin_data:
                    health_scan_txt += f"\n  • 經營(毛利率 % 近6期): {' → '.join(f'{v:.1f}' for v in margin_data)}"
                if current_ratio_data:
                    health_scan_txt += f"\n  • 償債(流動比 % 近6期): {' → '.join(f'{v:.0f}' for v in current_ratio_data)}"
                if debt_ratio_data:
                    health_scan_txt += f"\n  • 負債比(% 近6期): {' → '.join(f'{v:.1f}' for v in debt_ratio_data)}"
                cur_p_str = f"{quote_i['price']:.2f}" if quote_i else "—"
                chg_p_str = (f"▲ +{(quote_i['price']-quote_i['prev_close']):.2f} "
                              f"({((quote_i['price']/quote_i['prev_close']-1)*100):+.2f}%)"
                              if quote_i and quote_i['prev_close'] > 0 else "—")
                inline_prompt = f"""請幫我做韭菜健檢:

【標的】{inline_tk} {inline_info['name']} ({inline_info['industry'] or '—'} · {'上市' if inline_info['type']=='twse' else '上櫃'})
【目前報價】NT$ {cur_p_str} {chg_p_str}

【技術面】
{_fmt_d(tech_i)}

【一年位置】{yr_summary}

【籌碼面 20 日法人(張)】
{_fmt_d(chip_i)}

【股權結構】{share_summary}

【融資融券】{margin_summary}

【基本面】
{_fmt_d(funda_i)}

【體質掃描(連續趨勢)】{health_scan_txt}

【趨勢強弱(短/中/長)】{trend_summary_txt}

【同業比較(本檔 vs 同業中位)】{peer_summary_txt}

【歷年股利(近 5 年)】{div_summary_txt}

【行事曆】{calendar_summary_txt}

【健檢分數】{composite_i}/100 ({s_label})
  • 技術 {sub_i["技術"]}/100
  • 籌碼 {sub_i["籌碼"]}/100
  • 基本 {sub_i["基本"]}/100

請用「韭菜健檢」風格幫我:
1. 🩺 技術面健檢 — 含一年位置觀察(2-3 句)
2. 🩺 籌碼面健檢 — 含外資/散戶比例 + 融資融券解讀(2-3 句)
3. 🩺 基本面健檢 — 含接單/獲利趨勢解讀(2-3 句)
4. 🚨 綜合判斷 + 韭菜病風險警示

規則:
- 不報明牌、不給買賣建議、純客觀判讀
- 直接從第 1 點開始,不要開場白(禁:「好的」「這就為您」「以下是」)
- 不要結尾贅述(禁:「以上純客觀」「不構成投資建議」這類 — disclaimer 已在 UI 顯示)
- 不要重複問題、不要假提綱、不要客套
"""
                # 把 AI 報告 填到頂部 placeholder (PRO 會員限定,時間框架可選 + 共享 cache)
                cache_k_health = f"health:{inline_tk}"
                with _ai_slot.container():
                    st.markdown("### 🤖 智能健檢報告 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                                  unsafe_allow_html=True)
                    if pro_gate("智能健檢報告"):
                        render_ai_section(
                            prompt_base=inline_prompt,
                            cache_key=cache_k_health,
                            ss_prefix=f"ai_health_{inline_tk}",
                            button_label="🔍 查看智能健檢報告",
                            no_key_hint="去「❓ 關於」加智能 key(免費 1500 次/天)",
                        )

                # ── 最近新聞 ──
                st.divider()
                st.markdown("### 📰 近期新聞")
                with st.spinner("抓新聞..."):
                    news_inline = fetch_stock_news(inline_tk, inline_info["name"], max_n=6)
                if news_inline:
                    for nw in news_inline:
                        st.markdown(f"""
                        <a href='{nw['link']}' target='_blank' style='text-decoration:none'>
                          <div style='background:#1e2128; padding:10px 14px; border-radius:8px;
                                      border:1px solid #2f343d; margin-bottom:5px'>
                            <div style='color:#e4e6eb; font-size:0.92rem'>{nw['title']}</div>
                            <div style='color:#8b92a0; font-size:0.72rem; margin-top:2px'>
                              📰 {nw['source']} • {nw['published'][:22]}
                            </div>
                          </div>
                        </a>
                        """, unsafe_allow_html=True)
                else:
                    st.caption("⚪ 抓不到新聞")

                # 底部再放一個回卡牆
                st.divider()
                if st.button("🔙 回卡牆繼續看其他", use_container_width=True,
                              key="inline_back_btm", type="primary"):
                    st.session_state.pop("_inline_view_ticker", None)
                    st.rerun()
                # 阻止下方卡牌渲染
                st.stop()

        st.subheader("⭐ 我的觀察清單")
        st.caption("點卡片進場分析 → 直接展開健檢頁,不用跳分頁")

        watchlist = load_json("watchlist", {"tickers": []})
        # 用外層定義的 TW_TYPES (tw/twse/tpex/emerging)
        wl_items_raw = [t for t in watchlist.get("tickers", []) if t.get("type") in TW_TYPES]
        # 自動去重(同 ticker 多次加入只保留第一筆)
        seen_keys = set()
        wl_items = []
        dup_count = 0
        for _it in wl_items_raw:
            tk_key = _it.get("ticker")
            if tk_key in seen_keys:
                dup_count += 1
                continue
            seen_keys.add(tk_key)
            wl_items.append(_it)
        if dup_count > 0:
            # 寫回去重後的清單
            watchlist["tickers"] = [t for t in watchlist.get("tickers", [])
                                      if t.get("type") not in TW_TYPES] + wl_items
            save_json("watchlist", watchlist)
            st.info(f"✅ 自動移除了 {dup_count} 個重複項目")

        # 新增表單(垂直對齊 + autocomplete)
        st.markdown("**➕ 加入觀察清單**")
        # 全部 ticker 作為 selectbox options(輸入會即時 filter)
        all_options = [
            f"{tk} {info['name']}"
            for tk, info in ticker_map.items()
            if info.get("type") in TW_TYPES
        ]
        with st.form("wl_add", clear_on_submit=True):
            wl_sel = st.selectbox(
                "代號 / 名稱(輸入會搜尋)",
                options=all_options,
                index=None,
                placeholder="例:輸入 2330 或 台積電",
            )
            wl_note = st.text_input(
                "筆記(可選)",
                placeholder="例: 等回測 800 進場",
            )
            wl_submit = st.form_submit_button("➕ 加入觀察清單", type="primary",
                                                use_container_width=True)
            if wl_submit and wl_sel:
                wl_tk = wl_sel.split(" ")[0]
                if wl_tk not in ticker_map:
                    st.error(f"⚠️ {wl_tk} 不在資料庫")
                elif wl_tk in {t["ticker"] for t in wl_items}:
                    st.warning(f"⚠️ {wl_tk} 已經在觀察清單裡了")
                else:
                    info_add = ticker_map[wl_tk]
                    wl_items.append({
                        "ticker": wl_tk,
                        "type": info_add["type"],
                        "note": wl_note,
                    })
                    watchlist["tickers"] = [t for t in watchlist.get("tickers", [])
                                              if t.get("type") not in TW_TYPES] + wl_items
                    save_json("watchlist", watchlist)
                    st.success(f"✅ 已加入 {wl_tk} {info_add['name']}")
                    st.rerun()

        if not wl_items:
            st.info("還沒加入任何個股,用上方表單加入")
        else:
            # ── 🌅 晨報精選(最多 5 檔詳細卡)──
            # session_state 當主來源(DB 沒 column 也 OK),file 當 backup
            if "briefing_featured" not in st.session_state:
                _init_settings = load_json("settings", {}) or {}
                st.session_state["briefing_featured"] = _init_settings.get("briefing_featured", []) or []
            _current_featured = st.session_state["briefing_featured"]
            _valid_featured = [t for t in _current_featured
                                if t in {it["ticker"] for it in wl_items}]
            _opt_labels = {
                it["ticker"]: f"{it['ticker']} {ticker_map.get(it['ticker'], {}).get('name', '')}"
                for it in wl_items
            }
            _all_opts = list(_opt_labels.keys())
            with st.expander(
                f"🌅 晨報精選(最多 5 檔 · 已選 {len(_valid_featured)}/5)",
                expanded=False,
            ):
                st.caption("選 5 檔加入晨報詳細卡 — 看法人 / 月營收 / 健檢分 / 警示。其餘檔還會出現在「📋 其餘觀察」expander 內。")
                _picked = st.multiselect(
                    "晨報精選 ticker",
                    options=_all_opts,
                    default=_valid_featured,
                    format_func=lambda tk: _opt_labels.get(tk, tk),
                    max_selections=5,
                    label_visibility="collapsed",
                )
                if set(_picked) != set(_valid_featured):
                    st.session_state["briefing_featured"] = _picked
                    _bk = load_json("settings", {}) or {}
                    _bk["briefing_featured"] = _picked
                    try:
                        save_json("settings", _bk)
                    except Exception:
                        pass
                    st.toast(f"✅ 晨報精選已更新({len(_picked)}/5)", icon="🌅")
                    st.rerun()

            # ── 🔔 訊息中心(觸發中的價格警示)──
            _triggered_alerts = check_triggered_alerts()
            if _triggered_alerts:
                with st.expander(f"🔔 **訊息中心 — {len(_triggered_alerts)} 個觸發中警示**",
                                  expanded=True):
                    for ta in _triggered_alerts:
                        tk_n = ta["ticker"]
                        name_n = ticker_map.get(tk_n, {}).get("name", tk_n)
                        cond_label = "漲到" if ta["condition"] == "above" else "跌到"
                        tc1, tc2 = st.columns([5, 1])
                        with tc1:
                            st.markdown(
                                f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                                f"padding:10px 14px; border-radius:8px; border-left:3px solid #f59e0b'>"
                                f"<div><b style='color:#fff'>{tk_n} {name_n}</b> "
                                f"<span style='color:#f59e0b; margin-left:6px'>"
                                f"{cond_label} NT$ {ta['target']:.2f}</span></div>"
                                f"<div style='color:#94a3b8; font-size:0.75rem; margin-top:3px'>"
                                f"🕐 {ta['trigger_time'][:16].replace('T', ' ')} · "
                                f"觸發價 {ta['trigger_price']:.2f}</div></div>",
                                unsafe_allow_html=True,
                            )
                        with tc2:
                            if st.button("✓ 已讀", key=f"mark_read_{ta['rule_key']}",
                                           use_container_width=True):
                                mark_alert_read(ta["rule_key"])
                                st.rerun()

            # ── 📋 已設定的價格警示(管理 — fragment 加速刪除) ──
            _all_rules = list_price_alerts()
            if _all_rules:
                with st.expander(f"⚙️ 我設定的價格警示 ({len(_all_rules)})", expanded=False):
                    @st.fragment
                    def _render_alert_manager():
                        rules_now = list_price_alerts()
                        # 批次刪除(用 checkbox 勾選後一次刪)
                        to_delete = []
                        for ri, rule in enumerate(rules_now):
                            rc0, rc1, rc2 = st.columns([1, 5, 1])
                            cond_lbl = "漲到" if rule.get("condition") == "above" else "跌到"
                            with rc0:
                                chk = st.checkbox("", key=f"chk_alert_{ri}",
                                                    label_visibility="collapsed")
                                if chk: to_delete.append(ri)
                            with rc1:
                                st.markdown(
                                    f"📌 **{rule.get('ticker')}** {cond_lbl} "
                                    f"NT$ {rule.get('price'):.2f}"
                                )
                            with rc2:
                                if st.button("🗑️", key=f"rm_alert_{ri}",
                                               use_container_width=True):
                                    remove_price_alert(ri)
                                    st.rerun(scope="fragment")
                        # 批次刪除按鈕
                        if to_delete:
                            if st.button(f"🗑️ 刪除已勾選 {len(to_delete)} 條",
                                           type="primary",
                                           use_container_width=True):
                                # 從大到小刪(避免 index 偏移)
                                import yaml as _yml
                                yaml_path = INVEST_ROOT / "config" / "price_alerts.yaml"
                                cfg = _yml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                                rules_all = cfg.get("rules", [])
                                for idx in sorted(to_delete, reverse=True):
                                    if 0 <= idx < len(rules_all):
                                        rules_all.pop(idx)
                                cfg["rules"] = rules_all
                                yaml_path.write_text(_yml.dump(cfg, allow_unicode=True),
                                                       encoding="utf-8")
                                st.toast(f"已刪除 {len(to_delete)} 條", icon="🗑️")
                                st.rerun(scope="fragment")
                        # 全清按鈕
                        if rules_now:
                            if st.button(f"🚮 全部清空 ({len(rules_now)} 條)",
                                           use_container_width=True):
                                yaml_path = INVEST_ROOT / "config" / "price_alerts.yaml"
                                yaml_path.write_text("rules: []\n", encoding="utf-8")
                                st.toast("已全部清空", icon="🚮")
                                st.rerun(scope="fragment")

                    _render_alert_manager()

            # ── 隱藏的編輯/移除/排序管理區 ──
            with st.expander("⚙️ 編輯持股 / 筆記 / 排序 / 移除", expanded=False):
                st.caption("💡 填入股數 + 成本 = 升級為記帳模式,卡片會自動顯示損益。空白 = 純觀察清單。")
                for idx, item in enumerate(wl_items):
                    tk = item["ticker"]
                    if tk not in ticker_map:
                        continue
                    info_w = ticker_map[tk]
                    # 排序按鈕在 form 外(form 內 button 要走 submit)
                    rc1, rc2, rc3 = st.columns([8, 1, 1])
                    with rc1:
                        st.markdown(f"##### {tk} {info_w['name']} "
                                       f"<span style='color:#94a3b8; font-size:0.75rem'>"
                                       f"({info_w.get('industry','—')})</span>",
                                       unsafe_allow_html=True)
                    with rc2:
                        if st.button("⬆️", key=f"wl_mgr_up_{idx}_{tk}",
                                       disabled=(idx == 0),
                                       help="往上移", use_container_width=True):
                            _reorder_watchlist(idx, idx - 1)
                            st.rerun()
                    with rc3:
                        if st.button("⬇️", key=f"wl_mgr_dn_{idx}_{tk}",
                                       disabled=(idx == len(wl_items) - 1),
                                       help="往下移", use_container_width=True):
                            _reorder_watchlist(idx, idx + 1)
                            st.rerun()
                    with st.form(f"wl_manage_{idx}_{tk}", clear_on_submit=False,
                                   border=True):
                        # 持股欄位(全部 optional,空白 = 純觀察)
                        hc1, hc2, hc3 = st.columns(3)
                        with hc1:
                            new_shares = st.number_input(
                                "股數",
                                min_value=0,
                                value=int(item.get("shares") or 0),
                                step=100,
                                key=f"wl_shares_{idx}_{tk}",
                                help="持有張數 × 1000(零股直接填)",
                            )
                        with hc2:
                            new_cost = st.number_input(
                                "每股成本(未含手續費)",
                                min_value=0.0,
                                value=float(item.get("cost_per_share") or 0.0),
                                step=0.5,
                                format="%.2f",
                                key=f"wl_cost_{idx}_{tk}",
                                help="計算時自動加 +0.1425% 手續費",
                            )
                        with hc3:
                            new_date = st.text_input(
                                "進場日(可選)",
                                value=item.get("entry_date", ""),
                                placeholder="YYYY-MM-DD",
                                key=f"wl_date_{idx}_{tk}",
                            )
                        new_note = st.text_input(
                            "筆記",
                            value=item.get("note", ""),
                            placeholder="例: 等回測 800 進場",
                            key=f"wl_note_in_{idx}_{tk}",
                        )
                        mc1, mc2 = st.columns(2)
                        save_btn = mc1.form_submit_button("💾 儲存",
                                                              type="primary",
                                                              use_container_width=True)
                        del_clk = mc2.form_submit_button("🗑️ 移除此檔",
                                                            use_container_width=True)
                        if save_btn:
                            for t in watchlist.get("tickers", []):
                                if (t.get("ticker") == tk
                                      and t.get("type") == item.get("type")):
                                    t["note"] = new_note
                                    if new_shares > 0:
                                        t["shares"] = int(new_shares)
                                    else:
                                        t.pop("shares", None)
                                    if new_cost > 0:
                                        t["cost_per_share"] = float(new_cost)
                                    else:
                                        t.pop("cost_per_share", None)
                                    if new_date.strip():
                                        t["entry_date"] = new_date.strip()
                                    else:
                                        t.pop("entry_date", None)
                            save_json("watchlist", watchlist)
                            st.success("✅ 已更新"); st.rerun()
                        if del_clk:
                            watchlist["tickers"] = [
                                t for t in watchlist.get("tickers", [])
                                if not (t["ticker"] == tk and t.get("type") == item.get("type"))
                            ]
                            save_json("watchlist", watchlist)
                            st.toast(f"已移除 {tk}", icon="🗑️"); st.rerun()


            # ── 卡片 + 緊貼下方小箭頭按鈕(可點到觸發詳細) ──
            from streamlit_extras.stylable_container import stylable_container

            def _reorder_watchlist(from_idx: int, to_idx: int):
                """搬移觀察清單第 from_idx 個到 to_idx 位置。"""
                if not (0 <= from_idx < len(wl_items)) or not (0 <= to_idx < len(wl_items)):
                    return
                item = wl_items.pop(from_idx)
                wl_items.insert(to_idx, item)
                # 寫回 watchlist.json(保留非 TW 類型在前)
                watchlist["tickers"] = [t for t in watchlist.get("tickers", [])
                                          if t.get("type") not in TW_TYPES] + wl_items
                save_json("watchlist", watchlist)

            for idx, item in enumerate(wl_items):
                tk = item["ticker"]
                if tk not in ticker_map:
                    st.warning(f"⚠️ {tk} 不在資料庫")
                    continue
                info_w = ticker_map[tk]

                # 卡片
                st.markdown(build_stock_card_html(tk, info_w),
                              unsafe_allow_html=True)
                if item.get("note"):
                    st.caption(f"📝 {item['note']}")

                # 持股 P&L 顯示(若已填股數+成本)
                _hp = compute_holding_pnl(item)
                if _hp:
                    _pnl_color = "#ef4444" if _hp["pnl"] > 0 else ("#10b981" if _hp["pnl"] < 0 else "#94a3b8")
                    _pnl_arrow = "▲" if _hp["pnl"] > 0 else ("▼" if _hp["pnl"] < 0 else "—")
                    st.markdown(
                        f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                        f"padding:8px 12px; border-radius:8px; margin-top:-4px; margin-bottom:6px;"
                        f"border-left:3px solid {_pnl_color};"
                        f"display:grid; grid-template-columns:repeat(4,1fr); gap:8px; font-size:0.75rem'>"
                        f"<div><span style='color:#94a3b8'>股數</span> "
                        f"<b style='color:#fff'>{int(_hp['shares']):,}</b></div>"
                        f"<div><span style='color:#94a3b8'>成本</span> "
                        f"<b style='color:#fff'>{_hp['cost_per_share']:.2f}</b></div>"
                        f"<div><span style='color:#94a3b8'>市值</span> "
                        f"<b style='color:#fff'>NT$ {_hp['mv']:,.0f}</b></div>"
                        f"<div><span style='color:#94a3b8'>損益</span> "
                        f"<b style='color:{_pnl_color}'>{_pnl_arrow} {_hp['pnl']:+,.0f} "
                        f"({_hp['pct']:+.2f}%)</b></div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                # 緊貼卡片下方的小型可見按鈕(取代之前漏 CSS 的透明覆蓋)
                with stylable_container(
                    key=f"wl_btn_{idx}_{tk}",
                    css_styles="""
                        button {
                            margin-top: -8px !important;
                            background: rgba(20,184,166,0.08) !important;
                            color: #5eead4 !important;
                            border: 1px solid rgba(20,184,166,0.3) !important;
                            border-top: none !important;
                            border-radius: 0 0 12px 12px !important;
                            padding: 4px 0 !important;
                            font-size: 0.78rem !important;
                            min-height: 28px !important;
                            margin-bottom: 4px !important;
                        }
                        button:hover {
                            background: rgba(20,184,166,0.18) !important;
                            color: #fff !important;
                        }
                    """,
                ):
                    if st.button("🔍 翻開健檢", key=f"wl_card_click_{idx}_{tk}",
                                   use_container_width=True):
                        st.session_state["_inline_view_ticker"] = tk
                        st.rerun()


        # 跳轉提示已廢棄(個股詳情 tab 移除,inline view 取代)

    # ────── Tab 1: 搜尋 ──────
    with tab_search:
        st.subheader("🔍 搜尋個股")
        col1, col2 = st.columns([3, 1])
        with col1:
            query = st.text_input(
                "輸入代號 / 名稱 / 產業",
                placeholder="例: 2330 / 台積電 / 半導體",
                help="支援模糊搜尋,代號完整或名稱關鍵字都可",
            )
        with col2:
            asset_type = st.selectbox(
                "市場",
                ["全部", "上市 (twse)", "上櫃 (tpex)"],
                help="預設全部,可篩選",
            )

        if query:
            q = query.strip().lower()
            results = []
            for tk, info in ticker_map.items():
                name = info["name"].lower()
                ind = info["industry"].lower() if info["industry"] else ""
                t = info["type"]
                if asset_type == "上市 (twse)" and t != "twse": continue
                if asset_type == "上櫃 (tpex)" and t != "tpex": continue
                if (q in tk.lower() or q in name or q in ind):
                    results.append({
                        "代號": tk,
                        "名稱": info["name"],
                        "產業": info["industry"] or "—",
                        "市場": "上市" if t == "twse" else "上櫃",
                    })

            if not results:
                st.info(f"找不到「{query}」相關標的")
            else:
                show_n = min(10, len(results))
                st.success(f"找到 {len(results)} 檔(顯示前 {show_n})")

                # 直排:卡片 + 概述 + 加入觀察(搜尋不給看健檢,健檢要從觀察清單翻)
                for idx, r in enumerate(results[:show_n]):
                    tk_r = r["代號"]
                    if tk_r not in ticker_map:
                        continue
                    info_r = ticker_map[tk_r]

                    clicked = render_stock_row(tk_r, info_r, "sv", idx,
                                                  button_label="⭐ 加入觀察清單")
                    if clicked:
                        wl = load_json("watchlist", {"tickers": []})
                        existing = {t["ticker"] for t in wl.get("tickers", [])}
                        if tk_r in existing:
                            st.toast(f"{tk_r} 已在觀察清單", icon="⚠️")
                        else:
                            wl.setdefault("tickers", []).append({
                                "ticker": tk_r,
                                "type": ticker_map[tk_r]["type"],
                                "note": "",
                            })
                            save_json("watchlist", wl)
                            st.toast(f"已加入觀察清單: {tk_r}", icon="⭐")
                    st.divider()

                if len(results) > show_n:
                    st.caption(f"💡 還有 {len(results)-show_n} 檔,輸入更精確的關鍵字縮小範圍")
        else:
            st.info("💡 輸入代號或名稱開始搜尋,或下面挑產業類別 / 熱門標的")

            # ─── 產業類別 下拉 (放最上,不強迫看熱門) ───
            st.markdown("### 🏷️ 看產業類別")
            # 排除 ETF / ETN / Index 等非實質產業類別
            _SKIP_INDUSTRIES = {
                "ETF", "ETN", "Index", "上櫃ETF",
                "上櫃指數股票型基金(ETF)", "指數股票型基金(ETF)",
                "指數股票型基金", "上櫃ETN", "受益證券",
            }
            all_industries_list = sorted({v.get("industry") for v in ticker_map.values()
                                            if v.get("industry") and v["industry"] != "—"
                                            and v["industry"] not in _SKIP_INDUSTRIES})
            sel_industry = st.selectbox(
                "選產業類別",
                options=[None] + all_industries_list,
                format_func=lambda x: "請選擇..." if x is None else x,
                label_visibility="collapsed",
            )
            if sel_industry:
                hits_ind = [(tk, v) for tk, v in ticker_map.items()
                             if v.get("industry") == sel_industry][:12]
                st.caption(f"📦 {sel_industry} 前 {len(hits_ind)} 檔")
                for idx_h, (tk_h, info_h) in enumerate(hits_ind):
                    # 排行榜編排 — 全展開,直接顯示 卡片 + 概述 + 加入觀察
                    clicked_h = render_stock_row(tk_h, info_h, f"ind_{sel_industry}",
                                                    idx_h,
                                                    button_label="⭐ 加入觀察清單")
                    if clicked_h:
                        wl = load_json("watchlist", {"tickers": []})
                        existing = {t["ticker"] for t in wl.get("tickers", [])}
                        if tk_h in existing:
                            st.toast(f"{tk_h} 已在觀察清單", icon="⚠️")
                        else:
                            wl.setdefault("tickers", []).append({
                                "ticker": tk_h, "type": info_h["type"], "note": "",
                            })
                            save_json("watchlist", wl)
                            st.toast(f"已加入觀察清單: {tk_h}", icon="⭐")
                    st.divider()

            st.divider()

            # ─── 熱門個股(常見大型權值股 + ETF) ──
            st.markdown("### 🔥 熱門個股")
            hot_picks = [
                ("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"),
                ("2412", "中華電"), ("2308", "台達電"), ("2382", "廣達"),
                ("2891", "中信金"), ("2881", "富邦金"),
                ("0050", "元大台灣50"), ("0056", "高股息"),
                ("00878", "國泰永續高股息"), ("00919", "群益精選高息"),
            ]

            for idx, (tk_h, _name) in enumerate(hot_picks):
                if tk_h not in ticker_map:
                    continue
                info_h = ticker_map[tk_h]
                # 排行榜編排 — 全展開
                clicked = render_stock_row(tk_h, info_h, "hot", idx,
                                              button_label="⭐ 加入觀察清單")
                if clicked:
                    wl = load_json("watchlist", {"tickers": []})
                    existing = {t["ticker"] for t in wl.get("tickers", [])}
                    if tk_h in existing:
                        st.toast(f"{tk_h} 已在觀察清單", icon="⚠️")
                    else:
                        wl.setdefault("tickers", []).append({
                            "ticker": tk_h, "type": info_h["type"], "note": "",
                        })
                        save_json("watchlist", wl)
                        st.toast(f"已加入觀察清單: {tk_h}", icon="⭐")
                st.divider()

            # ─── 大盤新聞 ───
            st.divider()
            st.markdown("### 📰 大盤新聞(Google News RSS)")
            market_news = fetch_stock_news("台股", "加權指數", max_n=10)
            if market_news:
                for item in market_news:
                    st.markdown(f"""
                    <a href='{item['link']}' target='_blank' style='text-decoration:none'>
                      <div style='background:#1e2128; padding:12px 16px; border-radius:8px;
                                  border:1px solid #2f343d; margin-bottom:6px;
                                  transition:border-color 0.15s'
                           onmouseover="this.style.borderColor='#5eead4'"
                           onmouseout="this.style.borderColor='#2f343d'">
                        <div style='color:#e4e6eb; font-size:0.95rem; line-height:1.4; margin-bottom:4px'>
                          {item['title']}
                        </div>
                        <div style='color:#8b92a0; font-size:0.75rem'>
                          📰 {item['source']} • {item['published'][:25]}
                        </div>
                      </div>
                    </a>
                    """, unsafe_allow_html=True)
                st.caption("資料來源: Google News • 30 分鐘快取")
            else:
                st.info("⚪ 抓不到大盤新聞(可能網路問題)")

    # ────── Tab 2: 個股詳情(技術/籌碼/基本/新聞 4 分類)──────
    if False:  # 個股詳情 tab 拔掉(雞肋,點卡都會 inline 翻健檢),保留 code 以防
        default_ticker = st.session_state.get("detail_ticker", "2330")
        ticker = st.text_input(
            "代號",
            value=default_ticker,
            help="輸入完整代號 (例: 2330)",
            key="detail_input",
        ).strip()

        if not ticker:
            st.info("輸入代號看詳細資料")
        elif ticker not in ticker_map:
            st.warning(f"⚠️ {ticker} 不在資料庫")
        else:
            info = ticker_map[ticker]

            # ── 頂部:代號 + 名稱 + 即時報價 + 一鍵警示 ──
            header_l, header_r = st.columns([3, 1])
            with header_l:
                st.markdown(f"## {ticker} {info['name']}")
                st.caption(f"{info['industry'] or '—'}  •  {'🏛️ 上市' if info['type'] == 'twse' else '🏢 上櫃'}")
            with header_r:
                # 一鍵警示快速 button
                if st.button("🔔 一鍵加價格警示", use_container_width=True,
                              key=f"alert_btn_{ticker}"):
                    st.session_state["show_alert_form"] = ticker

            quote = fetch_yfinance_quote(ticker)
            if quote:
                price = quote["price"]
                chg = price - quote["prev_close"]
                chg_pct = chg / quote["prev_close"] * 100 if quote["prev_close"] > 0 else 0
                # TW 配色 紅漲綠跌
                if chg > 0:
                    color = "#ef4444"; arrow = "▲"
                elif chg < 0:
                    color = "#10b981"; arrow = "▼"
                else:
                    color = "#8b92a0"; arrow = "—"

                st.markdown(f"""
                <div style='display:grid; grid-template-columns:repeat(4, 1fr);
                            gap:12px; margin-bottom:6px'>
                  <div style='background:#1e2128; padding:12px 16px; border-radius:10px;
                              border-left:4px solid {color}'>
                    <div style='color:#8b92a0; font-size:0.8rem'>收盤</div>
                    <div style='color:{color}; font-size:1.8rem; font-weight:700; line-height:1.1'>
                      {price:.2f}
                    </div>
                    <div style='color:{color}; font-size:0.95rem; margin-top:2px'>
                      {arrow} {abs(chg):.2f} ({chg_pct:+.2f}%)
                    </div>
                  </div>
                  <div style='background:#1e2128; padding:12px 16px; border-radius:10px'>
                    <div style='color:#8b92a0; font-size:0.8rem'>開盤</div>
                    <div style='color:#e4e6eb; font-size:1.4rem; font-weight:600'>{quote['open']:.2f}</div>
                  </div>
                  <div style='background:#1e2128; padding:12px 16px; border-radius:10px'>
                    <div style='color:#8b92a0; font-size:0.8rem'>最高 / 最低</div>
                    <div style='color:#e4e6eb; font-size:1.4rem; font-weight:600'>
                      <span style='color:#ef4444'>{quote['high']:.2f}</span>
                      <span style='color:#8b92a0; font-size:1rem'> / </span>
                      <span style='color:#10b981'>{quote['low']:.2f}</span>
                    </div>
                  </div>
                  <div style='background:#1e2128; padding:12px 16px; border-radius:10px'>
                    <div style='color:#8b92a0; font-size:0.8rem'>成交量</div>
                    <div style='color:#e4e6eb; font-size:1.4rem; font-weight:600'>{quote['volume']:,}</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)
                st.caption(f"yfinance ~15 分鐘延遲 • {quote['asof']} • 🔴 紅漲 🟢 綠跌(台股慣例)")
            else:
                st.info("⚪ yfinance 抓不到當日報價")

            # ── 走勢圖:折線預設、可切 K 線 ──
            st.divider()
            st.markdown("### 📈 走勢圖")

            ccc1, ccc2 = st.columns([3, 1])
            with ccc1:
                period_label = st.radio(
                    "期間",
                    ["1 個月", "3 個月", "6 個月", "1 年", "2 年"],
                    index=2, horizontal=True,
                    key=f"chart_period_{ticker}",
                    label_visibility="collapsed",
                )
            with ccc2:
                chart_type = st.radio(
                    "圖型", ["📈 折線", "🕯️ K 線"],
                    index=0, horizontal=True, label_visibility="collapsed",
                    key=f"chart_type_{ticker}",
                )
            period_days = {"1 個月": 22, "3 個月": 66, "6 個月": 132,
                            "1 年": 252, "2 年": 504}[period_label]

            chart_df = load_local_ohlcv(ticker, period_days + 200)
            if chart_df is None or len(chart_df) < 5:
                st.warning(f"⚠️ 本機沒有 {ticker} 的 OHLCV 資料(可能不在 finmind cache)")
            else:
                import plotly.graph_objects as go
                from plotly.subplots import make_subplots

                ind_df = calc_technical_indicators(chart_df.copy())
                view = ind_df.tail(period_days).copy()

                # 期間漲跌判色
                start_p = view["close"].iloc[0]
                end_p = view["close"].iloc[-1]
                line_color = "#ef4444" if end_p >= start_p else "#10b981"
                fill_color = ("rgba(239,68,68,0.10)" if end_p >= start_p
                               else "rgba(16,185,129,0.10)")

                fig = make_subplots(
                    rows=2, cols=1, shared_xaxes=True,
                    vertical_spacing=0.03,
                    row_heights=[0.78, 0.22],
                )

                if chart_type == "📈 折線":
                    fig.add_trace(go.Scatter(
                        x=view["date"], y=view["close"],
                        mode="lines",
                        line=dict(color=line_color, width=2.4),
                        fill="tozeroy", fillcolor=fill_color,
                        name="收盤", showlegend=False,
                        hovertemplate="%{x|%Y-%m-%d}<br>收盤 %{y:.2f}<extra></extra>",
                    ), row=1, col=1)
                else:
                    fig.add_trace(go.Candlestick(
                        x=view["date"], open=view["open"], high=view["high"],
                        low=view["low"], close=view["close"],
                        increasing_line_color="#ef4444", increasing_fillcolor="#ef4444",
                        decreasing_line_color="#10b981", decreasing_fillcolor="#10b981",
                        name="K", showlegend=False,
                    ), row=1, col=1)

                # MA20 / MA60(配品牌 lime 系)
                for ma_col, c_ma, n_ma in [
                    ("ma20", "#14b8a6", "MA20"),
                    ("ma60", "#5eead4", "MA60"),
                ]:
                    if ma_col in view.columns:
                        fig.add_trace(go.Scatter(
                            x=view["date"], y=view[ma_col],
                            mode="lines", name=n_ma,
                            line=dict(color=c_ma, width=1.2, dash="dot"),
                        ), row=1, col=1)

                vol_colors = [
                    "#ef4444" if c >= o else "#10b981"
                    for c, o in zip(view["close"], view["open"])
                ]
                fig.add_trace(go.Bar(
                    x=view["date"], y=view["volume"],
                    marker_color=vol_colors, opacity=0.55,
                    name="量", showlegend=False,
                ), row=2, col=1)

                fig.update_layout(
                    height=520,
                    paper_bgcolor="#16181d", plot_bgcolor="#1e2128",
                    font=dict(color="#e4e6eb", size=11),
                    xaxis_rangeslider_visible=False,
                    margin=dict(l=10, r=10, t=10, b=10),
                    legend=dict(orientation="h", yanchor="top", y=1.05,
                                xanchor="right", x=1, bgcolor="rgba(0,0,0,0)"),
                    hovermode="x unified",
                )
                fig.update_xaxes(
                    gridcolor="#2f343d", zeroline=False,
                    rangebreaks=[dict(bounds=["sat", "mon"])],
                )
                fig.update_yaxes(gridcolor="#2f343d", zeroline=False)
                fig.update_yaxes(title_text="量", row=2, col=1,
                                  title_font=dict(size=10, color="#8b92a0"))

                st.plotly_chart(fig, use_container_width=True,
                                  config={"displayModeBar": False})
                st.caption("🔴 紅漲 / 🟢 綠跌(台股慣例) · MA20 / MA60 用 lime 虛線")

            # ── 健檢分數 (0-100) ──
            st.divider()
            ohlcv_full = load_local_ohlcv(ticker, 250)
            tech_data, chip_data, funda_data = None, None, None

            if ohlcv_full is not None and len(ohlcv_full) >= 20:
                indi = calc_technical_indicators(ohlcv_full)
                last_indi = indi.iloc[-1]
                tech_data = {
                    "price": float(last_indi["close"]),
                    "ma5": float(last_indi["ma5"]) if not pd.isna(last_indi["ma5"]) else 0,
                    "ma20": float(last_indi["ma20"]) if not pd.isna(last_indi["ma20"]) else 0,
                    "ma60": float(last_indi["ma60"]) if not pd.isna(last_indi["ma60"]) else 0,
                    "ma200": float(last_indi["ma200"]) if not pd.isna(last_indi["ma200"]) else 0,
                    "rsi": float(last_indi["rsi"]) if not pd.isna(last_indi["rsi"]) else 50,
                    "k": float(last_indi["k"]) if not pd.isna(last_indi["k"]) else 50,
                    "d": float(last_indi["d"]) if not pd.isna(last_indi["d"]) else 50,
                }

            inst = load_finmind_for_ticker(ticker, "TaiwanStockInstitutionalInvestorsBuySell")
            if inst is not None and not inst.empty:
                inst2 = inst.copy()
                inst2["date"] = pd.to_datetime(inst2["date"])
                inst2 = inst2.sort_values("date").tail(60)
                inst2["net"] = inst2["buy"] - inst2["sell"]
                last20_dates = inst2["date"].unique()[-20:]
                sub20 = inst2[inst2["date"].isin(last20_dates)]
                agg20 = sub20.groupby("name")["net"].sum() / 1000
                chip_data = {
                    "foreign_20d": int(agg20.get("Foreign_Investor", 0)),
                    "invtrust_20d": int(agg20.get("Investment_Trust", 0)),
                    "dealer_20d": int(agg20.get("Dealer_self", 0)),
                }

            per_full = load_finmind_for_ticker(ticker, "TaiwanStockPER")
            rev_full = load_finmind_for_ticker(ticker, "TaiwanStockMonthRevenue")
            funda_data = {}
            if per_full is not None and not per_full.empty:
                per_full["date"] = pd.to_datetime(per_full["date"])
                latest_per = per_full.sort_values("date").iloc[-1]
                funda_data["per"] = float(latest_per.get("PER", 0))
                funda_data["pbr"] = float(latest_per.get("PBR", 0))
                funda_data["yield"] = float(latest_per.get("dividend_yield", 0))
            if rev_full is not None and not rev_full.empty:
                rev_full["date"] = pd.to_datetime(rev_full["date"])
                rev_full = rev_full.sort_values("date")
                rev_full["yoy"] = rev_full["revenue"].pct_change(12) * 100
                latest_yoy = rev_full["yoy"].iloc[-1]
                if not pd.isna(latest_yoy):
                    funda_data["rev_yoy"] = float(latest_yoy)

            composite, sub_scores = calc_composite_score(tech_data, chip_data, funda_data)

            # 顯示健檢分數(體檢報告風 — 大字 + ring + 跟 hero 同色系)
            st.markdown("### 🩺 健檢分數")
            score_color, ring_bg, label_text = (
                ("#5eead4", "rgba(20,184,166,0.18)", "健康")
                if composite >= 70 else
                ("#fbbf24", "rgba(245,158,11,0.18)", "亞健康")
                if composite >= 50 else
                ("#f43f5e", "rgba(220,38,38,0.18)", "韭菜病")
            )
            score_html = (
                f"<div style='background:linear-gradient(135deg, #1f2937 0%, #1a1f27 100%);"
                f"padding:20px; border-radius:14px; border:1px solid #2f343d; margin-bottom:1rem'>"
                # 圓環 — 置中
                f"<div style='display:flex; justify-content:center; margin-bottom:14px'>"
                f"<div style='width:130px;height:130px;border-radius:50%;background:{ring_bg};"
                f"border:3px solid {score_color};display:flex;flex-direction:column;"
                f"align-items:center;justify-content:center;box-shadow:0 0 24px {ring_bg}'>"
                f"<div style='font-size:2.4rem;color:#fff;font-weight:800;line-height:1'>{composite}</div>"
                f"<div style='font-size:0.7rem;color:#94a3b8;margin-top:2px'>/ 100</div>"
                f"<div style='font-size:0.85rem;color:{score_color};margin-top:4px;font-weight:700'>{label_text}</div>"
                f"</div></div>"
                # 三個分項 (橫排)
                f"<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:8px'>"
                f"<div style='background:#16181d;padding:10px 8px;border-radius:8px;border-left:3px solid #5eead4;text-align:center'>"
                f"<div style='font-size:0.7rem;color:#94a3b8'>📈 技術</div>"
                f"<div style='font-size:1.4rem;color:#fff;font-weight:700'>{sub_scores['技術']}</div>"
                f"<div style='font-size:0.65rem;color:#64748b'>40%</div></div>"
                f"<div style='background:#16181d;padding:10px 8px;border-radius:8px;border-left:3px solid #5eead4;text-align:center'>"
                f"<div style='font-size:0.7rem;color:#94a3b8'>📊 籌碼</div>"
                f"<div style='font-size:1.4rem;color:#fff;font-weight:700'>{sub_scores['籌碼']}</div>"
                f"<div style='font-size:0.65rem;color:#64748b'>30%</div></div>"
                f"<div style='background:#16181d;padding:10px 8px;border-radius:8px;border-left:3px solid #5eead4;text-align:center'>"
                f"<div style='font-size:0.7rem;color:#94a3b8'>💰 基本</div>"
                f"<div style='font-size:1.4rem;color:#fff;font-weight:700'>{sub_scores['基本']}</div>"
                f"<div style='font-size:0.65rem;color:#64748b'>30%</div></div>"
                f"</div></div>"
            )
            st.markdown(score_html, unsafe_allow_html=True)
            st.caption("💡 70+ 健康 / 50-69 亞健康 / <50 韭菜病 · 純客觀數據,不構成投資建議")

            # ── 📋 複製健檢資料 → 貼給 Claude 對話 ──
            st.markdown("### 🤖 想看白話解讀? → 複製資料貼給 Claude 對話")
            cur_price_str = f"{quote['price']:.2f}" if quote else "—"
            chg_str_for_prompt = f"▲ +{(quote['price']-quote['prev_close']):.2f} ({((quote['price']/quote['prev_close']-1)*100):+.2f}%)" if quote and quote['prev_close']>0 else "—"

            def _fmt_dict(d):
                if not d: return "(無資料)"
                return "\n".join(f"  • {k}: {v}" for k, v in d.items())

            health_check_prompt = f"""請幫我做韭菜健檢:

【標的】{ticker} {info['name']} ({info['industry'] or '—'} · {'上市' if info['type']=='twse' else '上櫃'})
【目前報價】NT$ {cur_price_str} {chg_str_for_prompt}

【技術面】
{_fmt_dict(tech_data)}

【籌碼面 20 日法人(張)】
{_fmt_dict(chip_data)}

【基本面】
{_fmt_dict(funda_data)}

【健檢分數】{composite}/100 ({label_text})
  • 技術 {sub_scores["技術"]}/100
  • 籌碼 {sub_scores["籌碼"]}/100
  • 基本 {sub_scores["基本"]}/100

請用「韭菜健檢」風格幫我:
1. 🩺 技術面健檢 (白話 2-3 句)
2. 🩺 籌碼面健檢 (白話 2-3 句)
3. 🩺 基本面健檢 (白話 2-3 句)
4. 🚨 綜合判斷 + 韭菜病風險警示

規則:
- 不報明牌、不給買賣建議、純客觀判讀
- 直接從第 1 點開始,不要開場白(禁:「好的」「這就為您」「以下是」)
- 不要結尾贅述(禁:「以上純客觀」「不構成投資建議」這類 — disclaimer 已在 UI 顯示)
- 不要重複問題、不要假提綱、不要客套
"""
            with st.expander("📋 點開 → 複製健檢資料 prompt(按右上角複製鈕 → 貼到 Claude 對話)",
                              expanded=False):
                st.code(health_check_prompt, language=None)
                st.caption("💡 按 code block 右上角 📋 一鍵複製,貼到任何 AI 對話都可以")

            # 一鍵警示 modal
            if st.session_state.get("show_alert_form") == ticker:
                with st.form(f"alert_form_{ticker}", clear_on_submit=True):
                    st.subheader("🔔 加入價格警示")
                    cur_price = quote["price"] if quote else 0
                    c1, c2, c3 = st.columns(3)
                    cond = c1.selectbox("條件", ["above", "below"],
                                          format_func=lambda x: "漲到" if x == "above" else "跌到")
                    target = c2.number_input("價格", value=float(cur_price),
                                                min_value=0.0, step=0.1)
                    note = c3.text_input("筆記",
                                          value=f"{ticker} {'漲到' if cond=='above' else '跌到'} {target}")
                    c1, c2 = st.columns(2)
                    if c1.form_submit_button("✅ 儲存", type="primary"):
                        ok, msg = add_to_watchlist(ticker, cond, target, note)
                        if ok:
                            st.success(f"已加入警示")
                            st.session_state.pop("show_alert_form", None)
                            st.rerun()
                        else:
                            st.error(msg)
                    if c2.form_submit_button("取消"):
                        st.session_state.pop("show_alert_form", None)
                        st.rerun()

            st.divider()

            # ── 4 大類分析 tabs ──
            t_chart, t_chip, t_funda, t_news = st.tabs([
                "📈 技術面健檢", "📊 籌碼面健檢", "💰 基本面健檢", "📰 新聞面健檢"
            ])

            # ═══ 技術面 ═══
            with t_chart:
                st.caption("👆 K 線圖在上方,這裡是各項技術指標解讀")
                # 用本機 cache 算 KD / 布林通道
                if ohlcv_full is not None and len(ohlcv_full) >= 20 and tech_data:
                    st.markdown("### 📊 技術指標摘要(本機資料計算)")
                    indi_last = indi.iloc[-1]
                    c1, c2, c3, c4 = st.columns(4)

                    # KD
                    k_val = tech_data["k"]
                    d_val = tech_data["d"]
                    kd_status = "🟢 黃金交叉" if k_val > d_val else "🔴 死亡交叉"
                    if k_val > 80: kd_status = "🟠 超買區"
                    elif k_val < 20: kd_status = "🟢 超賣區"
                    c1.metric("KD (9日)", f"K={k_val:.0f} D={d_val:.0f}",
                              kd_status,
                              help="K 在 D 之上 = 黃金交叉;K > 80 = 超買;K < 20 = 超賣")

                    # RSI
                    rsi = tech_data["rsi"]
                    rsi_status = ("🟠 超買" if rsi > 70 else
                                   "🟢 超賣" if rsi < 30 else "⚪ 健康")
                    c2.metric("RSI (14日)", f"{rsi:.0f}", rsi_status,
                              help="0-100 之間。>70 過熱;<30 過冷")

                    # 布林通道
                    bb_up = float(indi_last["bb_up"]) if not pd.isna(indi_last["bb_up"]) else 0
                    bb_dn = float(indi_last["bb_dn"]) if not pd.isna(indi_last["bb_dn"]) else 0
                    bb_mid = float(indi_last["bb_mid"]) if not pd.isna(indi_last["bb_mid"]) else 0
                    price_now = tech_data["price"]
                    bb_pos = ((price_now - bb_dn) / (bb_up - bb_dn) * 100
                                if bb_up > bb_dn else 50)
                    bb_status = ("🟠 接近上軌" if bb_pos > 80 else
                                  "🟢 接近下軌" if bb_pos < 20 else
                                  "⚪ 通道內")
                    c3.metric("布林位置", f"{bb_pos:.0f}%", bb_status,
                              help=f"上軌 {bb_up:.1f} / 中 {bb_mid:.1f} / 下軌 {bb_dn:.1f}")

                    # MA 排列
                    ma_status = ("🟢 多頭排列" if (tech_data["price"] > tech_data["ma5"]
                                                  > tech_data["ma20"] > tech_data["ma60"])
                                   else "🔴 空頭排列" if (tech_data["price"] < tech_data["ma5"]
                                                          < tech_data["ma20"] < tech_data["ma60"])
                                   else "🟡 糾結")
                    c4.metric("均線排列",
                              f"5/{tech_data['ma5']:.1f} 20/{tech_data['ma20']:.1f} 60/{tech_data['ma60']:.1f}",
                              ma_status,
                              help="多頭排列 = 短線在中線上、中線在長線上,趨勢向上")

                # 自動算技術摘要 (從本機 cache)
                ohlcv = load_local_ohlcv(ticker, 250)
                if ohlcv is not None and len(ohlcv) >= 20:
                    last = ohlcv.iloc[-1]
                    ma5 = ohlcv["close"].tail(5).mean()
                    ma20 = ohlcv["close"].tail(20).mean()
                    ma60 = ohlcv["close"].tail(60).mean()
                    ma200 = ohlcv["close"].tail(200).mean() if len(ohlcv) >= 200 else None
                    ret_5d = (last["close"] / ohlcv["close"].iloc[-6] - 1) * 100 if len(ohlcv) > 5 else 0
                    ret_20d = (last["close"] / ohlcv["close"].iloc[-21] - 1) * 100 if len(ohlcv) > 20 else 0
                    ret_60d = (last["close"] / ohlcv["close"].iloc[-61] - 1) * 100 if len(ohlcv) > 60 else 0

                    st.markdown("### 📊 技術摘要")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("5 日漲跌", f"{ret_5d:+.1f}%")
                    c2.metric("20 日漲跌", f"{ret_20d:+.1f}%")
                    c3.metric("60 日漲跌", f"{ret_60d:+.1f}%")
                    if ma200:
                        dist = (last["close"] / ma200 - 1) * 100
                        c4.metric("vs 200 日均線", f"{dist:+.1f}%")

                    st.write(f"  - **5 日均價**: NT$ {ma5:.2f} ({'股價在均線上方' if last['close'] > ma5 else '股價在均線下方'})")
                    st.write(f"  - **20 日均價**: NT$ {ma20:.2f}")
                    st.write(f"  - **60 日均價**: NT$ {ma60:.2f}")
                    if ma200:
                        st.write(f"  - **200 日均價**: NT$ {ma200:.2f}")

                    # 想看白話 → 上方「複製健檢資料」已含技術面
                    st.caption("💡 想看 AI 白話解讀? 上方「📋 複製健檢資料」已包含技術面,貼到 Claude 對話即可")
                else:
                    st.info("⚪ 本機沒這檔的 K 線資料")

            # ═══ 籌碼面 ═══
            with t_chip:
                st.markdown("### 三大法人(近 20 日)")
                inst = load_finmind_for_ticker(ticker, "TaiwanStockInstitutionalInvestorsBuySell")
                inst_summary = ""
                if inst is not None and not inst.empty:
                    inst["date"] = pd.to_datetime(inst["date"])
                    inst = inst.sort_values("date").tail(60)
                    inst["net"] = inst["buy"] - inst["sell"]
                    last20 = inst["date"].unique()[-20:]
                    sub = inst[inst["date"].isin(last20)]
                    agg = sub.groupby("name")["net"].sum() / 1000
                    f_net = int(agg.get("Foreign_Investor", 0))
                    d_net = int(agg.get("Dealer_self", 0))
                    i_net = int(agg.get("Investment_Trust", 0))
                    c1, c2, c3, c4 = st.columns(4)
                    icon_f = "🟢" if f_net > 0 else "🔴" if f_net < 0 else "⚪"
                    icon_i = "🟢" if i_net > 0 else "🔴" if i_net < 0 else "⚪"
                    icon_d = "🟢" if d_net > 0 else "🔴" if d_net < 0 else "⚪"
                    c1.metric(f"{icon_f} 外資", f"{f_net:+,} 張")
                    c2.metric(f"{icon_i} 投信", f"{i_net:+,} 張")
                    c3.metric(f"{icon_d} 自營商", f"{d_net:+,} 張")
                    c4.metric("3 法人合計", f"{f_net+d_net+i_net:+,} 張")
                    inst_summary = f"外資 20d {f_net:+,}張, 投信 {i_net:+,}張, 自營商 {d_net:+,}張"

                    if HAS_PLOTLY:
                        import plotly.graph_objects as go
                        pivot = inst.pivot_table(index="date", columns="name", values="net",
                                                  aggfunc="sum").fillna(0) / 1000
                        pivot["cum_F"] = pivot.get("Foreign_Investor", 0).cumsum()
                        pivot["cum_IT"] = pivot.get("Investment_Trust", 0).cumsum()
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(x=pivot.index, y=pivot["cum_F"],
                                                  name="外資累計", line=dict(color="#5eead4")))
                        fig.add_trace(go.Scatter(x=pivot.index, y=pivot["cum_IT"],
                                                  name="投信累計", line=dict(color="#60a5fa")))
                        fig.update_layout(
                            plot_bgcolor="#16181d", paper_bgcolor="#16181d",
                            font=dict(color="#e4e6eb"), height=280,
                            xaxis=dict(gridcolor="#2f343d"),
                            yaxis=dict(gridcolor="#2f343d", title="累計 (張)"),
                            margin=dict(l=10, r=10, t=20, b=10),
                        )
                        st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("⚪ 法人資料未快取")

                # 持股結構
                st.markdown("### 👥 持股結構(誰擁有最多股票)")
                holding = load_finmind_for_ticker(ticker, "TaiwanStockHoldingSharesPer")
                holding_summary = ""
                if holding is not None and not holding.empty:
                    holding["date"] = pd.to_datetime(holding["date"])
                    latest = holding["date"].max()
                    sub = holding[holding["date"] == latest]
                    want_map = {"1-999": "散戶(< 1 張)",
                                 "1,000-5,000": "中戶(1-5 張)",
                                 "more than 1,000,001": "大戶(1000+ 張)"}
                    disp_rows = []
                    for k, label in want_map.items():
                        r = sub[sub["HoldingSharesLevel"] == k]
                        if not r.empty:
                            disp_rows.append({
                                "誰": label,
                                "人數": int(r["people"].iloc[0]),
                                "持股 %": f"{r['percent'].iloc[0]:.2f}%",
                            })
                    if disp_rows:
                        retail = next((r for r in disp_rows if "散戶" in r["誰"]), None)
                        big = next((r for r in disp_rows if "大戶" in r["誰"]), None)
                        if retail and big:
                            holding_summary = f"散戶占 {retail['持股 %']}, 大戶占 {big['持股 %']}"
                        st.dataframe(pd.DataFrame(disp_rows), use_container_width=True, hide_index=True)
                        st.caption(f"資料日: {latest.strftime('%Y-%m-%d')}")
                else:
                    st.info("⚪ 持股結構未快取")

                st.divider()
                st.caption("💡 想看 AI 白話解讀? 上方「📋 複製健檢資料」已包含籌碼面,貼到 Claude 對話即可")

            # ═══ 基本面 ═══
            with t_funda:
                st.markdown("### 💰 估值(便宜還是貴)")
                per = load_finmind_for_ticker(ticker, "TaiwanStockPER")
                val_summary = ""
                if per is not None and not per.empty:
                    per["date"] = pd.to_datetime(per["date"])
                    latest_per = per.sort_values("date").iloc[-1]
                    c1, c2, c3 = st.columns(3)
                    p = latest_per.get("PER", 0)
                    b = latest_per.get("PBR", 0)
                    y = latest_per.get("dividend_yield", 0)
                    c1.metric("本益比 PER", f"{p:.2f}",
                              help="股價 ÷ 每股盈餘。30 以上算貴,15 以下算便宜")
                    c2.metric("股價淨值比 PBR", f"{b:.2f}",
                              help="股價 ÷ 每股淨值。1 以下算便宜")
                    c3.metric("殖利率", f"{y:.2f}%",
                              help="現金股利 ÷ 股價。4% 以上算高")
                    st.caption(f"資料日: {latest_per['date'].strftime('%Y-%m-%d')}")
                    val_summary = f"PER {p:.2f}, PBR {b:.2f}, 殖利率 {y:.2f}%"
                else:
                    st.info("⚪ 估值資料未快取")

                # 月營收
                st.divider()
                st.markdown("### 📈 月營收(每個月賣多少錢)")
                rev = load_finmind_for_ticker(ticker, "TaiwanStockMonthRevenue")
                rev_summary = ""
                if rev is not None and not rev.empty:
                    rev["date"] = pd.to_datetime(rev["date"])
                    rev = rev.sort_values("date").tail(15)
                    rev["yoy"] = rev["revenue"].pct_change(12) * 100
                    disp = rev.tail(6).copy()
                    disp["月份"] = disp["date"].dt.strftime("%Y-%m")
                    disp["營收(億)"] = disp["revenue"] / 1e8
                    disp["年增率 (與去年同月比)"] = disp["yoy"]
                    st.dataframe(
                        disp[["月份", "營收(億)", "年增率 (與去年同月比)"]].style.format({
                            "營收(億)": "{:.2f}", "年增率 (與去年同月比)": "{:+.1f}%",
                        }),
                        use_container_width=True, hide_index=True,
                    )
                    if HAS_PLOTLY:
                        fig = px.bar(disp, x="月份", y="營收(億)",
                                      color_discrete_sequence=["#5eead4"])
                        fig.update_layout(
                            plot_bgcolor="#16181d", paper_bgcolor="#16181d",
                            font=dict(color="#e4e6eb"), height=240,
                            xaxis=dict(gridcolor="#2f343d"),
                            yaxis=dict(gridcolor="#2f343d"),
                            margin=dict(l=10, r=10, t=20, b=10),
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    latest_yoy = rev["yoy"].iloc[-1] if not pd.isna(rev["yoy"].iloc[-1]) else 0
                    rev_summary = f"最近月營收 {rev['revenue'].iloc[-1]/1e8:.1f}億, 年增率 {latest_yoy:+.1f}%"
                else:
                    st.info("⚪ 月營收資料未快取")

                # 股利政策
                st.divider()
                st.markdown("### 💵 股利政策(每年配多少錢)")
                div_data = load_dividend_for_ticker(ticker)
                div_summary = ""
                if div_data is not None and not div_data.empty:
                    cash_col = next((c for c in div_data.columns
                                       if "ash" in c.lower() and ("dividend" in c.lower() or "earning" in c.lower())), None)
                    year_col = next((c for c in div_data.columns if "year" in c.lower()), None) or "date"
                    if cash_col and year_col:
                        try:
                            d2 = div_data.copy()
                            if year_col == "date":
                                d2[year_col] = pd.to_datetime(d2[year_col]).dt.year
                            d2 = d2.groupby(year_col)[cash_col].sum().reset_index()
                            d2 = d2.tail(10).rename(columns={year_col: "年度", cash_col: "每股現金股利"})
                            st.dataframe(
                                d2.style.format({"每股現金股利": "{:.2f}"}),
                                use_container_width=True, hide_index=True,
                            )
                            recent_avg = d2["每股現金股利"].tail(3).mean()
                            div_summary = f"近 3 年平均每股配息 {recent_avg:.2f} 元"
                        except Exception as e:
                            st.caption(f"股利資料格式: {e}")
                    else:
                        st.caption(f"資料欄位: {list(div_data.columns)[:5]}")
                else:
                    st.info("⚪ 股利歷史資料未快取")

                st.divider()
                st.caption("💡 想看 AI 白話解讀? 上方「📋 複製健檢資料」已包含基本面,貼到 Claude 對話即可")

            # ═══ 新聞 / MOPS ═══
            with t_news:
                # 真的把新聞列出來 — Google News RSS (合法 + 免費)
                st.markdown("### 📰 近期相關新聞")
                with st.spinner("抓新聞中..."):
                    news_items = fetch_stock_news(ticker, info["name"])

                if news_items:
                    news_text_for_ai = ""  # 累積給 AI 用
                    for i, item in enumerate(news_items):
                        # 卡片式
                        st.markdown(f"""
                        <a href='{item['link']}' target='_blank' style='text-decoration:none'>
                          <div style='background:#1e2128; padding:14px 18px; border-radius:10px;
                                      border:1px solid #2f343d; margin-bottom:8px; cursor:pointer;
                                      transition:border-color 0.15s'
                               onmouseover="this.style.borderColor='#5eead4'"
                               onmouseout="this.style.borderColor='#2f343d'">
                            <div style='color:#e4e6eb; font-size:1rem; line-height:1.4; margin-bottom:6px'>
                              {item['title']}
                            </div>
                            <div style='color:#8b92a0; font-size:0.8rem'>
                              📰 {item['source']} • {item['published'][:25]}
                            </div>
                          </div>
                        </a>
                        """, unsafe_allow_html=True)
                        news_text_for_ai += f"- {item['title']} ({item['source']})\n"
                    st.caption(f"資料來源: Google News • 30 分鐘快取")
                else:
                    st.info("⚪ 抓不到新聞(可能網路問題或 Google News 暫時無回應)")
                    news_text_for_ai = ""

                st.divider()

                # 📋 複製新聞 → 貼給 Claude 總結
                if news_items:
                    news_prompt = f"""請用「韭菜健檢」風格,白話總結 {info['name']} ({ticker}) 最近新聞的市場關注點:

{news_text_for_ai}

請給我:
1. 🩺 市場情緒(看多 / 看空 / 中性)
2. 🩺 散戶 vs 法人觀點差距
3. 🚨 韭菜病風險警示(會不會被新聞牽著走?)

規則: 不報明牌、不下投資建議、客觀判讀。直接給結論,不要開場白或結尾贅述。
"""
                    with st.expander("📋 點開 → 複製新聞 prompt(貼到 Claude 對話)", expanded=False):
                        st.code(news_prompt, language=None)
                        st.caption("💡 按 code block 右上角 📋 一鍵複製,貼到 Claude/ChatGPT 都行")

                st.divider()

                # 公開資訊觀測站(政府公告)
                st.markdown("### 📑 公司必揭露(政府公告)")
                st.caption("公開資訊觀測站 = 上市公司必揭露的重大訊息(財報、董事會決議等)")
                col_m1, col_m2 = st.columns(2)
                with col_m1:
                    st.link_button(
                        "🔍 看這檔的重訊",
                        f"https://mops.twse.com.tw/mops/web/t05st01?step=1&firstin=true&off=1&keyword4=&code1=&TYPEK2=&checkbtn=&queryName=co_id&isnew=true&co_id={ticker}",
                        use_container_width=True,
                    )
                with col_m2:
                    st.link_button(
                        "📅 看法說會行事曆",
                        "https://mops.twse.com.tw/mops/web/t100sb02_q1",
                        use_container_width=True,
                    )

                st.divider()

                # 5 個分析網站連結
                st.markdown("### 🔗 其他研究網站")
                st.caption("點開新分頁去看,法律 100% 安全")
                links = [
                    ("Yahoo 股市", f"https://tw.stock.yahoo.com/quote/{ticker}"),
                    ("鉅亨網", f"https://www.cnyes.com/twstock/{ticker}"),
                    ("Goodinfo", f"https://goodinfo.tw/tw/StockInfo/StockDetail.asp?STOCK_ID={ticker}"),
                    ("MoneyDJ", f"https://www.moneydj.com/KMDJ/Quote/Quote007.aspx?a={ticker}"),
                    ("CMoney 討論", f"https://www.cmoney.tw/forum/stock/{ticker}"),
                ]
                cols = st.columns(len(links))
                for i, (name, url) in enumerate(links):
                    cols[i].link_button(name, url, use_container_width=True)

    # ────── Tab 0: 大盤 ──────
    with tab_market:
        # ── ⭐ PRO 區塊:AI 國際情勢 + AI 新聞情緒 (放最頂) ──
        st.markdown("### 🤖 智能國際情勢 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                      unsafe_allow_html=True)
        _global_ai_slot = st.empty()
        st.markdown("### 🤖 智能新聞情緒 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                      unsafe_allow_html=True)
        _news_ai_slot = st.empty()
        st.divider()

        # ── 大盤現況 (yfinance ^TWII) ──
        try:
            import yfinance as yf
            twii = yf.Ticker("^TWII")
            twii_hist = twii.history(period="1y", auto_adjust=False)
        except Exception:
            twii_hist = None

        try:
            vix = yf.Ticker("^VIX")
            vix_hist = vix.history(period="3mo", auto_adjust=False)
            vix_last = float(vix_hist["Close"].iloc[-1]) if not vix_hist.empty else None
        except Exception:
            vix_last = None

        # ── 頂部:加權指數現況 卡片 ──
        if twii_hist is not None and not twii_hist.empty:
            twii_close = float(twii_hist["Close"].iloc[-1])
            twii_prev = float(twii_hist["Close"].iloc[-2]) if len(twii_hist) >= 2 else twii_close
            twii_chg = twii_close - twii_prev
            twii_chg_pct = twii_chg / twii_prev * 100 if twii_prev > 0 else 0
            ma20 = float(twii_hist["Close"].tail(20).mean())
            ma60 = float(twii_hist["Close"].tail(60).mean())
            ma200 = float(twii_hist["Close"].tail(200).mean()) if len(twii_hist) >= 200 else None

            ret_20 = (twii_close / twii_hist["Close"].iloc[-21] - 1) * 100 if len(twii_hist) >= 21 else 0
            ret_60 = (twii_close / twii_hist["Close"].iloc[-61] - 1) * 100 if len(twii_hist) >= 61 else 0

            if twii_chg > 0: tcol, tar = "#ef4444", "▲"
            elif twii_chg < 0: tcol, tar = "#10b981", "▼"
            else: tcol, tar = "#8b92a0", "—"

            # 大盤狀態(描述,不指示動作)
            if ma200:
                dist_ma200 = (twii_close / ma200 - 1) * 100
                if dist_ma200 > 30:
                    temp_label = "🔥 過熱"; temp_color = "#ef4444"
                elif dist_ma200 > 15:
                    temp_label = "🟠 偏熱"; temp_color = "#f59e0b"
                elif dist_ma200 > -5:
                    temp_label = "🟢 健康牛"; temp_color = "#14b8a6"
                elif dist_ma200 > -15:
                    temp_label = "🟡 盤整偏冷"; temp_color = "#fbbf24"
                else:
                    temp_label = "💎 大跌中"; temp_color = "#22c55e"
            else:
                dist_ma200 = 0; temp_label = "—"; temp_color = "#8b92a0"

            # SVG 30 日折線
            twii_30d = twii_hist["Close"].tail(30).tolist()
            twii_line_color = "#ef4444" if twii_30d[-1] >= twii_30d[0] else "#10b981"
            twii_svg = _svg_sparkline(twii_30d, width=200, height=50, color=twii_line_color)
            twii_30d_chg = (twii_30d[-1] / twii_30d[0] - 1) * 100

            st.markdown(f"""
            <div style='background:linear-gradient(135deg, #1e293b 0%, #16181d 100%);
                        padding:20px 24px; border-radius:14px; margin-bottom:12px;
                        border:1px solid #2f343d'>
              <div style='display:grid; grid-template-columns: 1.4fr 1fr 1fr 1.2fr;
                          gap:18px; align-items:center'>
                <div>
                  <div style='color:#94a3b8; font-size:0.78rem; letter-spacing:1.5px'>
                    加權指數 ^TWII
                  </div>
                  <div style='color:{tcol}; font-size:2.4rem; font-weight:800;
                              line-height:1; margin-top:4px'>{twii_close:,.2f}</div>
                  <div style='color:{tcol}; font-size:1rem; margin-top:4px'>
                    {tar} {abs(twii_chg):.2f} ({twii_chg_pct:+.2f}%)
                  </div>
                </div>
                <div>
                  <div style='color:#94a3b8; font-size:0.78rem'>大盤溫度</div>
                  <div style='color:{temp_color}; font-size:1.3rem; font-weight:700;
                              margin-top:4px'>{temp_label}</div>
                  <div style='color:#94a3b8; font-size:0.72rem; margin-top:3px'>
                    距 MA200 <span style='color:{temp_color}; font-weight:600'>{dist_ma200:+.1f}%</span>
                  </div>
                </div>
                <div>
                  <div style='color:#94a3b8; font-size:0.78rem'>近期表現</div>
                  <div style='font-size:0.95rem; margin-top:6px; line-height:1.6'>
                    <div>20 日 <span style='color:{"#ef4444" if ret_20>0 else "#10b981"}; font-weight:600'>{ret_20:+.2f}%</span></div>
                    <div>60 日 <span style='color:{"#ef4444" if ret_60>0 else "#10b981"}; font-weight:600'>{ret_60:+.2f}%</span></div>
                  </div>
                </div>
                <div>
                  <div style='color:#94a3b8; font-size:0.78rem'>30 日走勢
                    <span style='color:{twii_line_color}; font-weight:600'>{twii_30d_chg:+.2f}%</span>
                  </div>
                  <div style='margin-top:6px'>{twii_svg}</div>
                </div>
              </div>
            </div>
            """, unsafe_allow_html=True)

        # ── 市場情緒 + 規則化專家看法 ──
        st.markdown("### 🧠 市場情緒(規則化判讀,非預測)")

        # VIX 情緒 — 描述狀態,不指示動作
        vix_emo, vix_color, vix_msg = "—", "#8b92a0", "VIX 資料抓不到"
        if vix_last is not None:
            if vix_last >= 35:
                vix_emo, vix_color = "😱 極度恐慌", "#dc2626"
                vix_msg = "歷史經驗:VIX > 35 後 12 個月,S&P500 平均 +18%(統計觀察)"
            elif vix_last >= 25:
                vix_emo, vix_color = "😨 高度緊張", "#f43f5e"
                vix_msg = "市場氣氛繃緊,常出現劇烈波動"
            elif vix_last >= 18:
                vix_emo, vix_color = "😐 平靜", "#fbbf24"
                vix_msg = "市場情緒正常區間"
            else:
                vix_emo, vix_color = "😎 過度樂觀", "#14b8a6"
                vix_msg = "市場情緒指標位於極低區,歷史上常伴隨突發大跌"

        # 大盤估值 (距 MA200) — 描述 + 歷史統計
        if twii_hist is not None and not twii_hist.empty and ma200:
            if dist_ma200 > 30:
                val_emo = "🚨 過熱"; val_color = "#dc2626"
                val_msg = "過去 10 年僅 5% 時間在此區間,歷史上常見均值回歸"
            elif dist_ma200 > 15:
                val_emo = "⚠️ 偏熱"; val_color = "#f59e0b"
                val_msg = "距 200 日均線 +15~30%,牛市中後段位"
            elif dist_ma200 > -5:
                val_emo = "✅ 合理"; val_color = "#14b8a6"
                val_msg = "距 200 日均線 -5~+15%,健康成長期"
            else:
                val_emo = "💎 大跌中"; val_color = "#22c55e"
                val_msg = "過去 10 年此區間,70% 案例 1 年後為正報酬"
        else:
            val_emo = "—"; val_color = "#8b92a0"; val_msg = ""

        vix_display = f"{vix_last:.1f}" if vix_last is not None else "—"
        emo_cols = st.columns(2)
        with emo_cols[0]:
            st.markdown(f"""
            <div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:16px 20px; border-radius:12px;
                        border-left:4px solid {vix_color}; min-height:130px'>
              <div style='color:#94a3b8; font-size:0.75rem; letter-spacing:1.5px;
                          font-weight:600'>📉 VIX 恐慌指數</div>
              <div style='color:{vix_color}; font-size:2rem; font-weight:800;
                          margin-top:4px; line-height:1'>
                {vix_display}
              </div>
              <div style='color:{vix_color}; font-size:1rem; font-weight:700; margin-top:4px'>{vix_emo}</div>
              <div style='color:#94a3b8; font-size:0.78rem; margin-top:6px;
                          line-height:1.4'>{vix_msg}</div>
            </div>
            """, unsafe_allow_html=True)
        with emo_cols[1]:
            st.markdown(f"""
            <div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:16px 20px; border-radius:12px;
                        border-left:4px solid {val_color}; min-height:130px'>
              <div style='color:#94a3b8; font-size:0.75rem; letter-spacing:1.5px;
                          font-weight:600'>📊 大盤估值狀態</div>
              <div style='color:{val_color}; font-size:2rem; font-weight:800;
                          margin-top:4px; line-height:1'>
                {dist_ma200:+.1f}%
              </div>
              <div style='color:{val_color}; font-size:1rem; font-weight:700; margin-top:4px'>{val_emo}</div>
              <div style='color:#94a3b8; font-size:0.78rem; margin-top:6px;
                          line-height:1.4'>{val_msg}</div>
            </div>
            """, unsafe_allow_html=True)

        st.divider()

        # ── 三大法人 ──
        st.markdown("### 📊 三大法人(全市場 20 日累計)")
        try:
            twse_dir = INVEST_ROOT / "data" / "cache" / "twse"
            all_files = sorted(twse_dir.glob("inst_twse_*.parquet"))
            files = [f for f in all_files
                      if f.stem.split("_")[-1].isdigit()
                      and len(f.stem.split("_")[-1]) == 8][-20:]
            df_inst = None
            if files:
                rows = []
                for f in files:
                    try:
                        df_d = pd.read_parquet(f)
                        df_d["net"] = df_d["buy"] - df_d["sell"]
                        agg = df_d.groupby("name")["net"].sum() / 1000
                        rows.append({
                            "date": f.stem.split("_")[-1],
                            "外資": int(agg.get("Foreign_Investor", 0)),
                            "投信": int(agg.get("Investment_Trust", 0)),
                            "自營": int(agg.get("Dealer_self", 0)),
                        })
                    except Exception:
                        continue
                if rows:
                    df_inst = pd.DataFrame(rows)
                    df_inst["date"] = pd.to_datetime(df_inst["date"], format="%Y%m%d")
                    df_inst = df_inst.sort_values("date")
            # Fallback: FinMind live(無 cache 環境用)
            if df_inst is None or df_inst.empty:
                df_inst = _fetch_inst_total_live(days=30)
            if df_inst is not None and not df_inst.empty:

                    fsum = int(df_inst["外資"].sum())
                    isum = int(df_inst["投信"].sum())
                    dsum = int(df_inst["自營"].sum())

                    # 解讀 — 動作描述 + 力道強度
                    inst_msg_parts = []
                    if fsum > 50000: inst_msg_parts.append("🔴 **外資強力買超**")
                    elif fsum < -50000: inst_msg_parts.append("🟢 **外資強力賣超**")
                    elif fsum > 0: inst_msg_parts.append("⚪ 外資小幅買超")
                    else: inst_msg_parts.append("⚪ 外資小幅賣超")
                    if isum > 10000: inst_msg_parts.append("🔴 投信積極加碼")
                    elif isum < -10000: inst_msg_parts.append("🟢 投信明顯減碼")

                    cols3 = st.columns(3)
                    for c, (label, v, key) in enumerate([("外資 20d", fsum, "f"),
                                                          ("投信 20d", isum, "i"),
                                                          ("自營 20d", dsum, "d")]):
                        ic = "#ef4444" if v > 0 else "#10b981" if v < 0 else "#8b92a0"
                        iar = "▲" if v > 0 else "▼" if v < 0 else "—"
                        with cols3[c]:
                            st.markdown(f"""
                            <div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%); padding:14px 18px;
                                        border-radius:10px; border:1px solid #2f343d;
                                        border-left:4px solid {ic}'>
                              <div style='color:#94a3b8; font-size:0.78rem'>{label}</div>
                              <div style='color:{ic}; font-size:1.8rem; font-weight:800;
                                          line-height:1.1; margin-top:4px'>
                                {iar} {abs(v):,}
                              </div>
                              <div style='color:#64748b; font-size:0.7rem'>張</div>
                            </div>
                            """, unsafe_allow_html=True)

                    st.markdown(
                        f"<div style='margin-top:10px; color:#cbd5e1; font-size:0.9rem'>"
                        f"📋 {'  ·  '.join(inst_msg_parts)}</div>",
                        unsafe_allow_html=True,
                    )

                    if HAS_PLOTLY:
                        import plotly.graph_objects as go
                        fig = go.Figure()
                        for col, color in [("外資", "#14b8a6"), ("投信", "#5eead4"),
                                            ("自營", "#fbbf24")]:
                            fig.add_trace(go.Bar(x=df_inst["date"], y=df_inst[col],
                                                  name=col, marker_color=color))
                        fig.update_layout(
                            plot_bgcolor="#1a1f27", paper_bgcolor="#16181d",
                            font=dict(color="#e4e6eb"), height=280, barmode="group",
                            margin=dict(l=10, r=10, t=20, b=10),
                            legend=dict(orientation="h", y=1.1, x=1, xanchor="right"),
                            xaxis=dict(gridcolor="#2f343d"),
                            yaxis=dict(gridcolor="#2f343d", title="淨買賣超 (張)"),
                        )
                        st.plotly_chart(fig, use_container_width=True,
                                          config={"displayModeBar": False})
            else:
                st.info("無 TWSE cache,資料無法顯示")
        except Exception as e:
            st.info(f"資料讀取失敗: {e}")

        st.divider()

        # ── 🌍 國際市場連動(美股 / 期貨 / 商品 / 匯率 / 加密) ──
        st.markdown("### 🌍 國際市場連動")
        st.caption("台股早盤常跟美股夜盤連動 — 看完這些再看大盤更有 sense")

        @st.cache_data(ttl=900)
        def _fetch_global_markets():
            """一次抓 9 個國際指標 (yfinance,~5 秒)。"""
            try:
                import yfinance as yf
                tickers_map = {
                    "S&P 500": "^GSPC",
                    "NASDAQ": "^IXIC",
                    "費半 SOX": "^SOX",
                    "美元指數": "DX-Y.NYB",
                    "WTI 原油": "CL=F",
                    "黃金": "GC=F",
                    "USD/TWD": "TWD=X",
                    "BTC": "BTC-USD",
                    "ETH": "ETH-USD",
                }
                results = {}
                # batch download
                for label_g, sym in tickers_map.items():
                    try:
                        h = yf.Ticker(sym).history(period="5d", auto_adjust=False)
                        if not h.empty and len(h) >= 2:
                            p_g = float(h["Close"].iloc[-1])
                            prev_g = float(h["Close"].iloc[-2])
                            chg_pct_g = (p_g / prev_g - 1) * 100
                            results[label_g] = (p_g, chg_pct_g)
                    except Exception:
                        continue
                return results
            except Exception:
                return {}

        with st.spinner("抓國際市場資料..."):
            global_data = _fetch_global_markets()

        if global_data:
            # 三欄 grid 顯示
            global_html_parts = ["<div style='display:grid; grid-template-columns:repeat(3, 1fr); gap:6px; margin-bottom:8px'>"]
            for label_g, (p_g, chg_g) in global_data.items():
                if chg_g > 0: col_g, ar_g = "#ef4444", "▲"
                elif chg_g < 0: col_g, ar_g = "#10b981", "▼"
                else: col_g, ar_g = "#94a3b8", "—"
                # 格式化價格 (USD/TWD 顯示 4 位小數,其他 2 位)
                if "USD/TWD" in label_g or "USD/JPY" in label_g:
                    p_fmt = f"{p_g:.4f}"
                elif "BTC" in label_g:
                    p_fmt = f"${p_g:,.0f}"
                elif "ETH" in label_g:
                    p_fmt = f"${p_g:,.0f}"
                elif "原油" in label_g or "黃金" in label_g:
                    p_fmt = f"${p_g:,.2f}"
                else:
                    p_fmt = f"{p_g:,.2f}"
                global_html_parts.append(
                    f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                    f"padding:10px 12px; border-radius:8px; border:1px solid #2f343d;"
                    f"border-left:3px solid {col_g}'>"
                    f"<div style='color:#94a3b8; font-size:0.7rem'>{label_g}</div>"
                    f"<div style='color:#fff; font-size:1rem; font-weight:700; margin-top:2px'>{p_fmt}</div>"
                    f"<div style='color:{col_g}; font-size:0.78rem; font-weight:600'>{ar_g} {chg_g:+.2f}%</div>"
                    f"</div>"
                )
            global_html_parts.append("</div>")
            st.markdown("".join(global_html_parts), unsafe_allow_html=True)

            # 規則化解讀(描述狀態,不指示動作)
            interpret_lines = []
            spy_chg = global_data.get("S&P 500", (0, 0))[1]
            sox_chg = global_data.get("費半 SOX", (0, 0))[1]
            twd_chg = global_data.get("USD/TWD", (0, 0))[1]
            wti_chg = global_data.get("WTI 原油", (0, 0))[1]
            btc_chg = global_data.get("BTC", (0, 0))[1]

            if sox_chg <= -3:
                interpret_lines.append(f"🔴 費半暴跌 {sox_chg:.1f}% — 半導體早盤注意")
            elif sox_chg >= 3:
                interpret_lines.append(f"🟢 費半大漲 {sox_chg:+.1f}% — 半導體早盤連動")
            if spy_chg <= -2:
                interpret_lines.append(f"🔴 S&P 500 跌 {spy_chg:.1f}% — 全球風險情緒下行")
            if twd_chg >= 0.5:
                interpret_lines.append(f"🔴 USD/TWD 升 +{twd_chg:.2f}% — 台幣貶值,外資可能撤")
            elif twd_chg <= -0.5:
                interpret_lines.append(f"🟢 USD/TWD 跌 {twd_chg:.2f}% — 台幣升值,外資流入訊號")
            if wti_chg >= 3:
                interpret_lines.append(f"⚠️ 原油 +{wti_chg:.1f}% — 通膨壓力 + 航運/石化族群連動")
            if abs(btc_chg) >= 5:
                emoji_b = "🔴" if btc_chg < 0 else "🟢"
                interpret_lines.append(f"{emoji_b} BTC {btc_chg:+.1f}% — 風險偏好變動訊號")
            if interpret_lines:
                st.markdown(
                    "<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                    "padding:12px 16px; border-radius:10px; border-left:3px solid #14b8a6; margin-top:6px'>"
                    + "".join(f"<div style='color:#cbd5e1; font-size:0.88rem; padding:3px 0'>{x}</div>"
                                for x in interpret_lines)
                    + "</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("⚪ 國際市場波動平穩,無明顯訊號")
        else:
            st.info("⚪ 國際市場資料抓取中…(可能網路問題)")

        st.divider()

        # ── 🗞️ 世界大事 (川普 / 聯準會 / 國際) ──
        st.markdown("### 🗞️ 世界大事")
        st.caption("篩選對台股可能有影響的國際新聞")
        ww_queries = [
            ("🇺🇸 美股 / 聯準會", "美股 聯準會 通膨"),
            ("🇨🇳 中美關係", "川普 中國 關稅"),
            ("⚡ 地緣 / 大事", "戰爭 油價 黃金"),
        ]
        all_ww_news = []
        ww_cols = st.columns(3)
        for col_i, (label_w, q_w) in enumerate(ww_queries):
            with ww_cols[col_i]:
                st.markdown(f"**{label_w}**")
                try:
                    ww_news = fetch_stock_news(q_w, "", max_n=3)
                    if ww_news:
                        for nw in ww_news:
                            st.markdown(
                                f"<a href='{nw['link']}' target='_blank' style='text-decoration:none'>"
                                f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                                f"padding:8px 10px; border-radius:6px; border:1px solid #2f343d;"
                                f"margin-bottom:4px'>"
                                f"<div style='color:#e4e6eb; font-size:0.78rem; line-height:1.3'>{nw['title']}</div>"
                                f"<div style='color:#94a3b8; font-size:0.65rem; margin-top:2px'>{nw['source']}</div>"
                                f"</div></a>",
                                unsafe_allow_html=True,
                            )
                            all_ww_news.append(f"[{label_w}] {nw['title']}")
                except Exception:
                    st.caption("⚪")

        st.divider()

        # 組 prompt(按鈕已在頂部 placeholder 渲染)
        global_prompt_parts = ["請用客觀統計觀察角度,分析國際情勢對台股可能的連動(不指示買賣動作):\n\n【國際市場昨夜收盤】"]
        for label_g, (p_g, chg_g) in global_data.items():
            global_prompt_parts.append(f"  • {label_g}: {p_g:,.2f}({chg_g:+.2f}%)")
        global_prompt_parts.append("\n【世界大事頭條】")
        for nw_s in all_ww_news[:9]:
            global_prompt_parts.append(f"  • {nw_s}")
        global_prompt_parts.append(
            "\n請給我:\n"
            "1. 🔴 對台股可能不利的訊號(統計觀察)\n"
            "2. 🟢 對台股可能有利的訊號(統計觀察)\n"
            "3. ⚠️ 需要留意的族群(描述,不指示動作)\n"
            "規則:純客觀數據判讀、不報明牌、不指示買賣。直接給結論,不要開場白或結尾贅述。"
        )
        global_prompt = "\n".join(global_prompt_parts)
        from datetime import date as _dt_g
        cache_k_global = f"global:{_dt_g.today().isoformat()}"

        # 填頂部 _global_ai_slot placeholder (PRO)
        with _global_ai_slot.container():
            if pro_gate("智能國際情勢"):
                render_ai_section(
                    prompt_base=global_prompt,
                    cache_key=cache_k_global,
                    ss_prefix="ai_global",
                    button_label="🔍 查看今日國際情勢",
                )

        st.divider()

        # ── 大盤新聞 (Google News RSS) → 等同「專家看法」彙整 ──
        st.markdown("### 📰 大盤新聞 / 專家看法(Google News)")
        try:
            news_items = fetch_stock_news("台股", "加權指數", max_n=10)
            if news_items:
                for item in news_items:
                    st.markdown(
                        f"<a href='{item['link']}' target='_blank' "
                        f"style='text-decoration:none !important; color:#e4e6eb !important; display:block'>"
                        f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 60%, #16181d 100%);"
                        f"padding:12px 16px; border-radius:10px; border:1px solid #2f343d;"
                        f"border-left:3px solid #14b8a6; margin-bottom:6px;"
                        f"transition:border-color 0.15s'"
                        f" onmouseover=\"this.style.borderColor='#14b8a6'\""
                        f" onmouseout=\"this.style.borderLeftColor='#14b8a6'; this.style.borderTopColor='#2f343d'; this.style.borderRightColor='#2f343d'; this.style.borderBottomColor='#2f343d'\">"
                        f"<div style='color:#e4e6eb !important; font-size:0.95rem; line-height:1.4'>{item['title']}</div>"
                        f"<div style='color:#8b92a0 !important; font-size:0.72rem; margin-top:4px'>📰 {item['source']} • {item['published'][:25]}</div>"
                        f"</div></a>",
                        unsafe_allow_html=True,
                    )
                st.caption("資料來源: Google News • 30 分鐘快取")

                # 組 prompt(按鈕已在頂部 placeholder)
                from datetime import date as _dt_n
                cache_k_news = f"news_market:{_dt_n.today().isoformat()}"
                news_txt = "\n".join(f"  • {it['title']} ({it['source']})"
                                      for it in news_items[:10])
                news_market_prompt = f"""請用「韭菜健檢」風格,白話總結今日大盤新聞的市場情緒:

【今日大盤新聞 10 則】
{news_txt}

請給我:
1. 🎭 整體市場情緒(看多/看空/分歧 + 統計觀察依據)
2. 🔥 媒體焦點族群(描述,不指示動作)
3. ⚠️ 散戶可能被牽走的訊息(描述韭菜病風險)
規則:
- 純客觀觀察、不指示動作、不報明牌
- 直接從第 1 點開始,不要開場白
- 不要結尾贅述
- 不要客套
"""
                # 填頂部 _news_ai_slot placeholder (PRO)
                with _news_ai_slot.container():
                    if pro_gate("智能新聞情緒"):
                        render_ai_section(
                            prompt_base=news_market_prompt,
                            cache_key=cache_k_news,
                            ss_prefix="ai_news_market",
                            button_label="🔍 查看今日新聞情緒",
                        )
            else:
                st.info("⚪ 抓不到新聞")
        except Exception as e:
            st.info(f"新聞抓取失敗: {e}")

    # ────── Tab 多市場 ──────
    with tab_global:
        st.markdown("### 🌍 多市場")
        st.caption("美股 / 加密 / 商品 / 匯率 · yfinance 15 分鐘延遲")

        # ⭐ AI 區塊放最上面 — placeholder
        _multi_ai_slot = st.empty()
        st.divider()

        with st.spinner("抓全球市場資料..."):
            multi_data = fetch_multi_market_data()

        for group_name, items in multi_data.items():
            if not items: continue
            st.markdown(f"#### {group_name}")
            # 3 欄 grid 顯示
            for row_start in range(0, len(items), 3):
                row_items = items[row_start:row_start + 3]
                cols_g = st.columns(3)
                for c_i, it in enumerate(row_items):
                    if it["chg_pct"] > 0:
                        col_g, ar_g = "#ef4444", "▲"
                    elif it["chg_pct"] < 0:
                        col_g, ar_g = "#10b981", "▼"
                    else:
                        col_g, ar_g = "#8b92a0", "—"
                    # 30 日 SVG sparkline
                    spark = _svg_sparkline(it["m30"], width=160, height=36, color=col_g) if it["m30"] else ""
                    # 價格格式
                    if "BTC" in it["sym"] or "ETH" in it["sym"]:
                        p_fmt = f"${it['price']:,.0f}"
                    elif it["sym"] in ("TWD=X", "JPY=X"):
                        p_fmt = f"{it['price']:.3f}"
                    elif "USD" in it["sym"] and it["price"] < 100:
                        p_fmt = f"${it['price']:.2f}"
                    elif it["price"] < 100:
                        p_fmt = f"{it['price']:.2f}"
                    else:
                        p_fmt = f"{it['price']:,.2f}"
                    with cols_g[c_i]:
                        st.markdown(
                            f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                            f"padding:12px 14px; border-radius:10px;"
                            f"border:1px solid #2f343d; border-left:3px solid {col_g}; margin-bottom:8px'>"
                            f"<div style='color:#94a3b8; font-size:0.7rem'>{it['sym']}</div>"
                            f"<div style='color:#fff; font-size:0.92rem; font-weight:600; margin-top:2px'>{it['label']}</div>"
                            f"<div style='color:{col_g}; font-size:1.15rem; font-weight:700; margin-top:6px'>{p_fmt}</div>"
                            f"<div style='color:{col_g}; font-size:0.78rem'>{ar_g} {it['chg_pct']:+.2f}%</div>"
                            f"<div style='margin-top:6px'>{spark}</div>"
                            f"<div style='color:#64748b; font-size:0.7rem; margin-top:2px'>30 日 {it['m30_chg']:+.1f}%</div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
            st.divider()

        # ── 🤖 智能多市場連動 (PRO) — 填到頂部 placeholder ──
        with _multi_ai_slot.container():
         st.markdown("### 🤖 智能多市場連動 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                      unsafe_allow_html=True)
         if pro_gate("智能多市場連動"):
            if _get_gemini_key():
                # 組 prompt
                multi_lines = []
                for g_name, items in multi_data.items():
                    multi_lines.append(f"\n【{g_name}】")
                    for it in items:
                        multi_lines.append(
                            f"  • {it['sym']} {it['label']}: 今日 {it['chg_pct']:+.2f}%, 30d {it['m30_chg']:+.1f}%"
                        )
                multi_prompt = f"""請用「韭菜健檢」風格,客觀分析全球市場對台股可能的連動(3-5 句):

{chr(10).join(multi_lines)}

請給我:
1. 🔴 對台股可能不利的訊號(統計觀察)
2. 🟢 對台股可能有利的訊號(統計觀察)
3. ⚠️ 哪個族群要留意(描述,不指示動作)

規則:純客觀數據判讀、不報明牌、不指示買賣。直接給結論,不要開場白或結尾贅述。
"""
                from datetime import date as _dt_mg
                cache_k_multi = f"multi:{_dt_mg.today().isoformat()}"
                render_ai_section(
                    prompt_base=multi_prompt,
                    cache_key=cache_k_multi,
                    ss_prefix="ai_multi",
                    button_label="🔍 查看今日多市場連動分析",
                )
            else:
                st.info("💡 去「❓ 關於」加智能 key 即可一鍵分析")

    # ────── Tab 4: 排行榜 ──────
    with tab_rank:
        st.subheader("🏆 個股排行榜")

        # 抓所有 cache 的 ticker
        cache_files = list(TW_OHLCV_CACHE.glob("*.parquet"))
        # TICKER_UNIVERSE_FALLBACK 已搬到模組頂層(策略掃描共用)
        if not cache_files:
            st.caption("📡 即時模式(雲端):取 ~80 檔熱門權值/ETF + 你的觀察清單")
            wl_book = load_json("watchlist", {"tickers": []})
            wl_tickers = [t["ticker"] for t in wl_book.get("tickers", []) if t.get("type") in TW_TYPES]
            universe = list(dict.fromkeys(wl_tickers + TICKER_UNIVERSE_FALLBACK))
            with st.spinner(f"batch 抓 {len(universe)} 檔..."):
                results = _ranking_batch_fetch(tuple(universe), _time_bucket())
            # batch 失敗或太少 → per-ticker fallback
            if len(results) < 5:
                st.warning(f"batch 只拿到 {len(results)} 檔,改用單筆 fetch(慢但穩,~30 秒)")
                results = []
                progress = st.progress(0.0, text="抓取中...")
                for i_u, tk in enumerate(universe):
                    progress.progress((i_u + 1) / len(universe),
                                        text=f"抓 {tk} ({i_u+1}/{len(universe)})")
                    if tk not in ticker_map:
                        continue
                    q = fetch_yfinance_quote(tk)
                    if not q or not q.get("prev_close"):
                        continue
                    chg_pct = (q["price"] / q["prev_close"] - 1) * 100 if q["prev_close"] > 0 else 0
                    results.append({
                        "代號": tk,
                        "收盤": q["price"],
                        "漲跌%": chg_pct,
                        "成交量": int(q.get("volume", 0)),
                    })
                progress.empty()
            # 補產業欄 + 名稱
            for r in results:
                r["產業"] = ticker_map.get(r["代號"], {}).get("industry", "—")
                r["名稱"] = ticker_map.get(r["代號"], {}).get("name", r["代號"])
            if results:
                st.caption(f"✅ 拿到 {len(results)} 檔資料")
        else:
            st.caption(f"📁 本機 cache 模式 · {len(cache_files)} 檔")
            # 計算當日漲跌(限定到 500 檔以免太慢)
            with st.spinner("計算中..."):
                results = []
                for f in cache_files[:500]:
                    tk = f.stem
                    if tk not in ticker_map:
                        continue
                    df_t = load_local_ohlcv(tk, 5)
                    if df_t is None or len(df_t) < 2:
                        continue
                    last = df_t.iloc[-1]
                    prev = df_t.iloc[-2]
                    if last["close"] == 0 or prev["close"] == 0:
                        continue
                    chg_pct = (last["close"] / prev["close"] - 1) * 100
                    results.append({
                        "代號": tk,
                        "名稱": ticker_map[tk]["name"],
                        "收盤": last["close"],
                        "漲跌%": chg_pct,
                        "成交量": int(last["volume"]) if "volume" in last else 0,
                        "產業": ticker_map[tk]["industry"],
                    })

        if results:
            rdf = pd.DataFrame(results)

            def render_rank_cards(df_rank, prefix):
                """直排卡牌風排行榜 — 1 行 1 張卡 + 概述,純數字排名."""
                rows_list = list(df_rank.iterrows())
                for idx, (_, row) in enumerate(rows_list):
                    tk_r = row["代號"]
                    if tk_r not in ticker_map:
                        continue
                    info_r = ticker_map[tk_r]
                    rank_num = idx + 1
                    medal = str(rank_num)  # 純數字 1, 2, 3, 4, ...

                    clicked = render_stock_row(tk_r, info_r, f"rk_{prefix}",
                                                 idx, rank_medal=medal,
                                                 button_label="⭐ 加入觀察清單")
                    if clicked:
                        wl = load_json("watchlist", {"tickers": []})
                        existing = {t["ticker"] for t in wl.get("tickers", [])}
                        if tk_r in existing:
                            st.toast(f"{tk_r} 已在觀察清單", icon="⚠️")
                        else:
                            wl.setdefault("tickers", []).append({
                                "ticker": tk_r, "type": info_r["type"], "note": "",
                            })
                            save_json("watchlist", wl)
                            st.toast(f"已加入觀察清單: {tk_r}", icon="⭐")
                    st.divider()

            tab_up, tab_down, tab_vol, tab_health = st.tabs([
                "🔴 漲幅前 10", "🟢 跌幅前 10", "📊 成交量前 10",
                "🩺 健檢分數前 10"
            ])
            with tab_health:
                st.markdown("##### 🩺 健檢分數排行 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                              unsafe_allow_html=True)
                if pro_gate("健檢分數排行 (Top 10 體質最佳)"):
                 top_liquid = rdf[~rdf["代號"].str.startswith("00")].sort_values("成交量", ascending=False).head(100)
                 st.caption("⚠️ ETF 不適用本健檢模型(無 PER / 月營收),已排除")
                 with st.spinner("計算健檢分數中(僅算成交量前 100 檔,~10 秒)..."):
                    health_rows = []
                    for _, row in top_liquid.iterrows():
                        tk_h = row["代號"]
                        try:
                            ohlcv_h = load_local_ohlcv(tk_h, 250)
                            if ohlcv_h is None or len(ohlcv_h) < 20:
                                continue
                            indi = calc_technical_indicators(ohlcv_h)
                            lt = indi.iloc[-1]
                            tech_d = {
                                "price": float(lt["close"]),
                                "ma5": float(lt["ma5"]) if not pd.isna(lt["ma5"]) else 0,
                                "ma20": float(lt["ma20"]) if not pd.isna(lt["ma20"]) else 0,
                                "ma60": float(lt["ma60"]) if not pd.isna(lt["ma60"]) else 0,
                                "ma200": float(lt["ma200"]) if not pd.isna(lt["ma200"]) else 0,
                                "rsi": float(lt["rsi"]) if not pd.isna(lt["rsi"]) else 50,
                                "k": float(lt["k"]) if not pd.isna(lt["k"]) else 50,
                                "d": float(lt["d"]) if not pd.isna(lt["d"]) else 50,
                            }
                            chip_d = None
                            inst_h = load_finmind_for_ticker(tk_h, "TaiwanStockInstitutionalInvestorsBuySell")
                            if inst_h is not None and not inst_h.empty:
                                i2 = inst_h.copy()
                                i2["date"] = pd.to_datetime(i2["date"])
                                i2 = i2.sort_values("date").tail(40)
                                last20d = i2["date"].unique()[-20:]
                                i2["net"] = i2["buy"] - i2["sell"]
                                sub20 = i2[i2["date"].isin(last20d)]
                                agg = sub20.groupby("name")["net"].sum() / 1000
                                chip_d = {
                                    "foreign_20d": int(agg.get("Foreign_Investor", 0)),
                                    "invtrust_20d": int(agg.get("Investment_Trust", 0)),
                                    "dealer_20d": int(agg.get("Dealer_self", 0)),
                                }
                            funda_d = {}
                            per_dh = load_finmind_for_ticker(tk_h, "TaiwanStockPER")
                            if per_dh is not None and not per_dh.empty:
                                per_dh["date"] = pd.to_datetime(per_dh["date"])
                                lph = per_dh.sort_values("date").iloc[-1]
                                funda_d["per"] = float(lph.get("PER", 0))
                                funda_d["pbr"] = float(lph.get("PBR", 0))
                                funda_d["yield"] = float(lph.get("dividend_yield", 0))
                            rev_dh = load_finmind_for_ticker(tk_h, "TaiwanStockMonthRevenue")
                            if rev_dh is not None and not rev_dh.empty:
                                rev_dh["date"] = pd.to_datetime(rev_dh["date"])
                                rev_dh = rev_dh.sort_values("date")
                                yoy_v = (rev_dh["revenue"].pct_change(12) * 100).iloc[-1]
                                if not pd.isna(yoy_v):
                                    funda_d["rev_yoy"] = float(yoy_v)
                            comp, _sub = calc_composite_score(tech_d, chip_d, funda_d)
                            health_rows.append({
                                "代號": tk_h, "名稱": row["名稱"],
                                "產業": row["產業"], "收盤": row["收盤"],
                                "漲跌%": row["漲跌%"], "成交量": row["成交量"],
                                "健檢分數": comp,
                            })
                        except Exception:
                            continue
                if health_rows:
                    df_all = pd.DataFrame(health_rows).sort_values("健檢分數", ascending=False)
                    df_h = df_all[df_all["健檢分數"] >= 70].head(10)
                    n_high = len(df_h)
                    st.markdown(
                        f"<div style='background:linear-gradient(135deg, #0f766e 0%, #1a1f27 100%);"
                        f"padding:10px 14px; border-radius:8px; margin-bottom:10px;"
                        f"border-left:3px solid #14b8a6'>"
                        f"<div style='color:#5eead4; font-size:0.78rem; font-weight:600'>📊 中期 60 日視角 · 歷史驗證</div>"
                        f"<div style='color:#fff; font-size:0.82rem; margin-top:4px'>"
                        f"70+ 體質股 60 日平均 <b>+10.93%</b> / win <b>60.3%</b> / "
                        f"vs 0050 <b>alpha +4.57pp</b>(n=194, 2020-26)。"
                        f"OOS 2023-26:<b>+19.23% / win 85.7% / alpha +9.91pp</b>(n=56)"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
                    if n_high == 0:
                        st.info("⚪ 今日成交量前 100 檔無人過 70 分(歷史平均每月 2-3 檔過關,訊號稀少屬常態)。"
                                "可改看「異常量能」或「策略市集」其他訊號。")
                    else:
                        st.caption(f"✅ 今日 {n_high} 檔過 70 分。50-69 中等股已過濾(歷史 alpha -1.34pp,不推薦)。")
                    # 直接渲染 (用 render_stock_row + 健檢分數當 medal)
                    for idx, (_, r) in enumerate(df_h.iterrows()):
                        tk_h = r["代號"]
                        if tk_h not in ticker_map: continue
                        info_h = ticker_map[tk_h]
                        score = int(r["健檢分數"])
                        medal = f"{score}分"
                        clicked = render_stock_row(tk_h, info_h, "rk_health",
                                                     idx, rank_medal=medal,
                                                     button_label="⭐ 加入觀察清單")
                        if clicked:
                            wl = load_json("watchlist", {"tickers": []})
                            existing = {t["ticker"] for t in wl.get("tickers", [])}
                            if tk_h in existing:
                                st.toast(f"{tk_h} 已在觀察清單", icon="⚠️")
                            else:
                                wl.setdefault("tickers", []).append({
                                    "ticker": tk_h, "type": info_h["type"], "note": "",
                                })
                                save_json("watchlist", wl)
                                st.toast(f"已加入觀察清單: {tk_h}", icon="⭐")
                        st.divider()
                else:
                    st.info("沒有可用資料")
            with tab_up:
                render_rank_cards(rdf.sort_values("漲跌%", ascending=False).head(10), "up")
            with tab_down:
                render_rank_cards(rdf.sort_values("漲跌%").head(10), "dn")
            with tab_vol:
                render_rank_cards(rdf.sort_values("成交量", ascending=False).head(10), "vol")
        else:
            st.info("沒有可用資料(可能本機 cache 過時)")

    # ────── Tab 策略(從排行榜搬出來) ──────
    with tab_strat_top:
        st.markdown("### 🔬 策略市集 <span style='font-size:0.65rem; background:#f59e0b; color:#16181d; padding:2px 8px; border-radius:6px; letter-spacing:1px; vertical-align:middle; font-weight:700; margin-left:8px'>PRO</span>",
                      unsafe_allow_html=True)
        st.caption("基於 1 年量化研究驗證過的策略 · 每天 6 點 cache,所有用戶共享結果 · 純條件偵測,非投資建議")

        if pro_gate("策略市集 (預設策略 + backtest 數據)"):
            # 正向策略(觸發 = 體質好 / 有 alpha 機會)
            POSITIVE_STRATEGIES = [
                {
                    "id": "rev_yoy",
                    "name": "📈 月營收 YoY 真 alpha",
                    "help_key": "月營收 YoY 真 alpha",
                    "desc": "條件:月營收 YoY > 30% + 20日成交額 > 1 億",
                    "backtest": "歷史 60d 平均 +3.95% / t=24.19 / n=24K · OOS 2020-25 robust",
                    "scan_fn": lambda: scan_revenue_yoy_signals(top_n=10),
                    "key_field": "yoy",
                    "key_fmt": "YoY +{:.1f}%",
                    "border_color": "#14b8a6",
                },
                {
                    "id": "ab_consensus",
                    "name": "👥 外資+投信 AB 雙重共識",
                    "help_key": "AB 雙重共識",
                    "desc": "條件:20日 外資淨買 > 5000張 AND 投信淨買 > 500張",
                    "backtest": "歷史 60d alpha +8.78% / t=+3.83 / n=126 · OOS+MCPT PASS",
                    "scan_fn": lambda: scan_ab_consensus(top_n=10),
                    "key_field": "f20",
                    "key_fmt": "外資 +{:,} 張",
                    "border_color": "#5eead4",
                },
                {
                    "id": "quiet_limitup",
                    "name": "🎯 量縮漲停",
                    "help_key": "量縮漲停",
                    "desc": "條件:單日漲幅 ≥ 9.5% + 量縮(VR < 0.8)",
                    "backtest": "歷史 20d 平均 +4.83% / n=5437 · post-2020 robust",
                    "scan_fn": lambda: scan_quiet_limitup(top_n=10),
                    "key_field": "chg",
                    "key_fmt": "漲 +{:.1f}%",
                    "border_color": "#ef4444",
                },
                {
                    "id": "quiet_limitdown",
                    "name": "📉 量縮跌停反彈",
                    "help_key": "量縮跌停反彈",
                    "desc": "條件:單日跌幅 ≤ -9.5% + 量縮(VR < 0.8)",
                    "backtest": "歷史 20d 平均 +7.99% / 5d +4.27% / n=4733 · OOS robust",
                    "scan_fn": lambda: scan_quiet_limitdown_bounce(top_n=10),
                    "key_field": "chg",
                    "key_fmt": "跌 {:.1f}%",
                    "border_color": "#10b981",
                },
                {
                    "id": "low_retail",
                    "name": "🧠 散戶最少(法人主導)",
                    "help_key": "散戶最少",
                    "desc": "條件:散戶(<50張)持股比例 最低 + 流動性 > 1 億",
                    "backtest": "memory: 散戶比例極端區 lift +11.3pp / p<0.001 / weekly n=9991 真 alpha",
                    "scan_fn": lambda: scan_low_retail_concentration(top_n=10),
                    "key_field": "retail_pct",
                    "key_fmt": "散戶 {:.1f}%",
                    "border_color": "#a78bfa",
                },
            ]
            # 反向策略(觸發 = 警示,後續易弱)
            REVERSE_STRATEGIES = [
                {
                    "id": "govbank_reverse",
                    "name": "🏦 行庫共識度反向(警示)",
                    "help_key": "行庫共識度反向",
                    "desc": "條件:5+ 家行庫同買 — 反向訊號",
                    "backtest": "歷史 60d alpha -1.62% / t=-28.46 / n=161K · 「政府護盤股後續弱勢」反直覺真 alpha",
                    "scan_fn": lambda: scan_govbank_reverse(top_n=10),
                    "key_field": "bank_count",
                    "key_fmt": "{} 家行庫",
                    "border_color": "#dc2626",
                },
                {
                    "id": "high_retail",
                    "name": "🥬 韭菜聚集警示",
                    "help_key": "韭菜聚集警示",
                    "desc": "條件:散戶持股比例 ≥ 60%(韭菜密集 = 後續易倒貨)",
                    "backtest": "memory: 散戶比例極端區 lift +11.3pp 雙向真 alpha",
                    "scan_fn": lambda: scan_high_retail_warning(top_n=10),
                    "key_field": "retail_pct",
                    "key_fmt": "散戶 {:.1f}%",
                    "border_color": "#f43f5e",
                },
            ]

            def _render_strategy(strat):
                # 標題 + ❓ 解釋
                title_with_help(
                    f"##### {strat['name']}",
                    strat.get("help_key", strat["name"]),
                    key_suffix=strat["id"],
                )
                with st.expander(f"📋 {strat['desc']}", expanded=False):
                    st.markdown(
                        f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                        f"padding:10px 14px; border-radius:8px;"
                        f"border-left:3px solid {strat['border_color']}; margin-bottom:10px'>"
                        f"<div style='color:#94a3b8; font-size:0.78rem'>📊 歷史回測</div>"
                        f"<div style='color:#fff; font-size:0.88rem; margin-top:3px'>{strat['backtest']}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    with st.spinner(f"掃描..."):
                        s_hits = strat["scan_fn"]()
                    if not s_hits:
                        # 7 個 scanner 都已加雲端 fallback,真的回 0 = 今日無觸發
                        if not TW_OHLCV_CACHE.exists() or not list(TW_OHLCV_CACHE.glob("*.parquet")):
                            st.info(
                                "⚪ 今日 universe(觀察清單 + ~80 熱門)無觸發此策略。\n"
                                "本機版掃全市場 ~2000 檔,命中率較高。"
                            )
                        else:
                            st.info("⚪ 今日無觸發此策略")
                    else:
                        st.caption(f"✅ 今日觸發 {len(s_hits)} 檔")
                        for i_h, h in enumerate(s_hits):
                            if h["tk"] not in ticker_map: continue
                            info_h = ticker_map[h["tk"]]
                            c1_h, c2_h = st.columns([4, 1])
                            key_val = strat["key_fmt"].format(h[strat["key_field"]])
                            with c1_h:
                                st.markdown(
                                    f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                                    f"padding:9px 12px; border-radius:6px;"
                                    f"border-left:2px solid {strat['border_color']};"
                                    f"display:flex; justify-content:space-between; align-items:center'>"
                                    f"<div><b style='color:#fff'>{h['tk']}</b> "
                                    f"<span style='color:#cbd5e1'>{info_h['name']}</span>"
                                    f"<span style='color:#94a3b8; font-size:0.7rem; margin-left:6px'>"
                                    f"{info_h.get('industry', '')}</span></div>"
                                    f"<div style='color:{strat['border_color']}; font-weight:700; font-size:0.85rem'>"
                                    f"{key_val}</div></div>",
                                    unsafe_allow_html=True,
                                )
                            with c2_h:
                                if st.button("⭐",
                                               key=f"strat_add_{strat['id']}_{i_h}_{h['tk']}",
                                               use_container_width=True,
                                               help="加入觀察清單"):
                                    wl = load_json("watchlist", {"tickers": []})
                                    existing = {t["ticker"] for t in wl.get("tickers", [])}
                                    if h["tk"] in existing:
                                        st.toast(f"{h['tk']} 已在觀察清單", icon="⚠️")
                                    else:
                                        wl.setdefault("tickers", []).append({
                                            "ticker": h["tk"],
                                            "type": info_h["type"], "note": f"觸發 {strat['name']}",
                                        })
                                        save_json("watchlist", wl)
                                        st.toast(f"已加入 {h['tk']}", icon="⭐")

            st.markdown("#### 🟢 正向策略 — 觸發 = 體質好 / 有 alpha 機會")
            for strat in POSITIVE_STRATEGIES:
                _render_strategy(strat)

            st.divider()

            st.markdown("#### 🔴 反向策略 — 觸發 = 警示 / 後續易弱")
            for strat in REVERSE_STRATEGIES:
                _render_strategy(strat)

    # ────── Tab 關於 (合併原使用說明) ──────
    # ────── Tab 韭菜病自檢 ──────
    with tab_quiz:
        st.markdown("### 🥬 韭菜病自檢")
        st.caption("7 題了解你的投資行為偏差。教育性質 · 不構成醫療或投資建議。")

        QUIZ_QUESTIONS = [
            {
                "q": "看到一檔股票連 5 天漲停 + 群組老師力推,你會...?",
                "tag": "🚀 FOMO 追高",
                "opts": [
                    ("立刻跟進,不能錯過", 10),
                    ("買一點點試水溫", 7),
                    ("先觀察,等消息明朗", 3),
                    ("已過熱,絕對不買", 0),
                ],
            },
            {
                "q": "持股賠 20%,你會...?",
                "tag": "📉 損失趨避",
                "opts": [
                    ("再加碼攤平,降低成本", 10),
                    ("繼續抱,反正帳面虧不算虧", 8),
                    ("檢視當初買的理由,理由還在就抱", 3),
                    ("觸發停損點就賣", 0),
                ],
            },
            {
                "q": "最近一筆讓你最得意的交易,你覺得是...?",
                "tag": "🦚 過度自信",
                "opts": [
                    ("我看懂市場了,我有 sense", 10),
                    ("運氣 + 一點本事", 7),
                    ("可能是運氣,但策略也有貢獻", 3),
                    ("純粹運氣", 0),
                ],
            },
            {
                "q": "一週內你會交易幾次?(以「換股」為單位)",
                "tag": "🔄 過度交易",
                "opts": [
                    ("5 次以上,看到機會就動", 10),
                    ("2-4 次", 7),
                    ("1 次左右", 3),
                    ("幾乎不換,長期持有", 0),
                ],
            },
            {
                "q": "朋友 / KOL / 群組推薦的股票,你會...?",
                "tag": "🐑 群眾從眾",
                "opts": [
                    ("立刻跟單,他們專業", 10),
                    ("買一點看看", 7),
                    ("聽聽就好,自己研究再決定", 3),
                    ("從不跟單", 0),
                ],
            },
            {
                "q": "連續 3 筆交易都賺錢,下一筆你會...?",
                "tag": "🔥 熱手效應",
                "opts": [
                    ("加大部位,我目前在 winning streak", 10),
                    ("維持相同部位", 5),
                    ("反而縮減部位,均值回歸要小心", 0),
                    ("不知道,看那一筆值不值得", 3),
                ],
            },
            {
                "q": "你的目標價 / 停損點,通常是...?",
                "tag": "🎯 錨定 / 規劃",
                "opts": [
                    ("不設,看心情", 10),
                    ("設了但常常下修目標 / 上修停損", 8),
                    ("看老師喊的目標價", 5),
                    ("根據策略客觀設定,嚴格執行", 0),
                ],
            },
        ]

        if "quiz_submitted" not in st.session_state:
            st.session_state.quiz_submitted = False
            st.session_state.quiz_answers = {}

        if not st.session_state.quiz_submitted:
            # 答題表單
            st.markdown(f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%); padding:14px 18px; border-radius:10px; border-left:3px solid #14b8a6; margin:10px 0'>"
                          f"<div style='color:#fff; font-weight:600'>🎯 7 題簡單問答,~3 分鐘</div>"
                          f"<div style='color:#94a3b8; font-size:0.85rem; margin-top:4px'>"
                          f"沒有對錯,憑直覺選 — 越誠實結果越準</div>"
                          f"</div>", unsafe_allow_html=True)

            with st.form("quiz_form"):
                answers = {}
                tags_hit = {}
                for i, q in enumerate(QUIZ_QUESTIONS):
                    st.markdown(f"**Q{i+1}. {q['q']}**")
                    st.caption(q["tag"])
                    choice = st.radio(
                        f"Q{i+1}",
                        options=list(range(len(q["opts"]))),
                        format_func=lambda x, qq=q: qq["opts"][x][0],
                        key=f"quiz_radio_{i}",
                        label_visibility="collapsed",
                    )
                    answers[i] = q["opts"][choice][1]
                    tags_hit[q["tag"]] = q["opts"][choice][1]
                    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

                submit = st.form_submit_button("📊 看我的韭菜病指數",
                                                  type="primary",
                                                  use_container_width=True)
                if submit:
                    st.session_state.quiz_submitted = True
                    st.session_state.quiz_answers = answers
                    st.session_state.quiz_tags = tags_hit
                    st.rerun()

        else:
            # 結果頁
            answers = st.session_state.quiz_answers
            tags = st.session_state.quiz_tags
            total = sum(answers.values())
            max_score = len(QUIZ_QUESTIONS) * 10
            score = round(total / max_score * 100, 1)

            if score < 25:
                grade = "🌟 老司機"
                grade_color = "#14b8a6"
                msg = "你顯然是冷靜、紀律的投資人。繼續保持。"
            elif score < 50:
                grade = "✅ 健康"
                grade_color = "#5eead4"
                msg = "輕微偏差,大體上能控制情緒。注意特別高分的維度。"
            elif score < 70:
                grade = "🥬 韭菜傾向"
                grade_color = "#f59e0b"
                msg = "有明顯的行為偏差,容易被市場情緒帶走。下方看哪些維度要注意。"
            else:
                grade = "🥬🥬 重度韭菜病"
                grade_color = "#dc2626"
                msg = "需要嚴肅檢視交易行為。多數失敗交易源自下方這些偏差。"

            # 主分數卡
            st.markdown(
                f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
                f"padding:28px 32px; border-radius:14px; border:2px solid {grade_color};"
                f"text-align:center; margin:14px 0;"
                f"box-shadow: 0 0 30px {grade_color}33'>"
                f"<div style='color:#94a3b8; font-size:0.78rem; letter-spacing:2px'>"
                f"YOUR LEEK DISEASE INDEX</div>"
                f"<div style='color:{grade_color}; font-size:4.5rem; font-weight:800; line-height:1; margin:8px 0'>"
                f"{score}<span style='font-size:1.5rem; color:#94a3b8'>/100</span></div>"
                f"<div style='color:{grade_color}; font-size:1.5rem; font-weight:700; margin-top:6px'>"
                f"{grade}</div>"
                f"<div style='color:#cbd5e1; font-size:0.95rem; margin-top:10px'>{msg}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # 維度分項
            st.markdown("#### 📊 各維度得分")
            sorted_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)
            for tag, sc in sorted_tags:
                col_b = ("#dc2626" if sc >= 8 else
                         "#f59e0b" if sc >= 5 else
                         "#14b8a6" if sc >= 3 else
                         "#5eead4")
                bar_pct = sc / 10 * 100
                st.markdown(
                    f"<div style='background:#1a1f27; padding:10px 14px; border-radius:8px; margin-bottom:6px'>"
                    f"<div style='display:flex; justify-content:space-between'>"
                    f"<div style='color:#e4e6eb; font-weight:600'>{tag}</div>"
                    f"<div style='color:{col_b}; font-weight:700'>{sc}/10</div>"
                    f"</div>"
                    f"<div style='background:#0f172a; height:6px; border-radius:3px; margin-top:6px; overflow:hidden'>"
                    f"<div style='background:{col_b}; height:100%; width:{bar_pct}%'></div>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            st.divider()

            # AI 個性化分析
            tags_text = "\n".join(f"  • {t}: {s}/10" for t, s in tags.items())
            quiz_prompt = f"""我做了「韭菜病自檢」問卷,以下是我的維度得分(0 健康 / 10 嚴重):

{tags_text}

總分:{score}/100 ({grade})

請用「韭菜健檢」風格,白話分析(4-6 句):
1. 🩺 你最該注意的 1-2 個維度 — 為什麼這偏差會虧錢(舉行為案例)
2. 💡 改進建議 — 具體可操作的習慣(不指示具體個股,不報明牌)
3. 📚 推薦一本相關投資心理書或概念(免費可查的)

規則:純教育性建議、不指示買賣動作、不報明牌、白話口語。直接給結論,不要開場白或結尾贅述。
"""
            q_cache_key = f"quiz:{hash(tuple(sorted(tags.items())))}"
            render_ai_section(
                prompt_base=quiz_prompt,
                cache_key=q_cache_key,
                ss_prefix="ai_quiz",
                button_label="🤖 智能個性化分析(根據你的答案)",
            )

            st.divider()

            # 分享
            st.markdown("#### 📤 分享給朋友比比看")
            share_text = (f"我的韭菜病指數 {score}/100\n"
                            f"{grade}\n\n"
                            f"你呢?來測測 → 韭菜健檢 app")
            st.code(share_text, language=None)
            st.caption("💡 按 code block 右上角 📋 一鍵複製,丟到 LINE / IG / Threads")

            st.divider()

            # 重做
            if st.button("🔄 重做一次", use_container_width=True):
                st.session_state.quiz_submitted = False
                st.session_state.quiz_answers = {}
                st.rerun()

    with tab_about:

        st.markdown("""
### 🩺 韭菜健檢 是什麼?

一個 **看股票體質的工具** — 把每檔股票放上 X 光台,從**技術 / 籌碼 / 基本 / 新聞** 4 面照給你看。
**避免你成為韭菜的健診儀。**

---

### ✅ 做什麼

- 🌡️ **大盤現況** — TAIEX、VIX、三大法人、距 200 日均線
- ⭐ **觀察清單** — 加進自己想追的股,異常一目了然
- 🔍 **搜尋** — 找個股 / 看熱門 / 看大盤新聞
- 🏆 **排行榜** — 漲幅 / 跌幅 / 量爆 / 健檢分前 10
- 🩺 **翻開健檢** — 點任何卡片進去看 4 面分析 + AI prompt

---

### ❌ 不做什麼

- ❌ **不報明牌** — 永遠不會說「買這檔」
- ❌ **不喊飆股** — 統計事實而已,你決定怎麼用
- ❌ **不下單** — 純看盤分析
- ❌ **不存你資料雲端** — 全部本機 `data/`

---

### 🧠 健檢分數怎麼算?

| 維度 | 權重 | 看什麼 |
|---|---|---|
| 📈 技術面 | 40% | KD / RSI / 均線排列 / 布林位置 |
| 📊 籌碼面 | 30% | 外資 / 投信 / 自營 20 日淨買賣 |
| 💰 基本面 | 30% | PER / 殖利率 / 月營收 YoY |

- **70+** = 體質很好(綠)
- **50-69** = 普通(琥珀)
- **< 50** = 體質不好(紅)

#### 📊 歷史驗證(2026-06 backtest, 6.4 年實證)

| 時間框架 | HIGH(≥70) mean | win% | vs 0050 alpha |
|---|---|---|---|
| 短線 20 日 | +2.69% | 49.0% | +0.92pp(微弱) |
| **中期 60 日** ✅ | **+10.93%** | **60.3%** | **+4.57pp** |
| 長線 120 日 | +14.27% | 59.6% | +1.03pp(衰退) |

**OOS 2023-26**:HIGH 60 日 **+19.23% / win 85.7% / alpha +9.91pp**(n=56)。

⚠️ **健檢分數適用「中期 60 日」視角** — 短線進場 / 長線定存效果差。
⚠️ 2022 熊年小輸 0.34pp,regime 敏感。
⚠️ 平均每月只 2-3 檔過 70 分,訊號稀少屬常態。

---

### 🎴 稀有度怎麼決定?

用 **20 日平均成交額**(完全客觀,跟健檢分數獨立):

| 稀有度 | 規則 |
|---|---|
| 🟡 **LEGENDARY** | 50 億 / 日以上(台積電、0050) |
| 🟣 **EPIC** | 10 - 50 億 |
| 🔵 **RARE** | 3 - 10 億 |
| 🟢 **UNCOMMON** | 0.5 - 3 億 |
| ⚪ **COMMON** | < 0.5 億 |

→ LEGENDARY = 全市場都在交易、最熱、流動性最好
→ COMMON = 冷門,警示小心

---

### 🤖 智能健檢報告

翻開任何個股 → 自動產出 4 面白話健檢報告(技術 / 籌碼 / 基本 / 新聞)。
AI 用「韭菜健檢」風格判讀,純客觀數據,不報明牌。

---

### 📰 每日晨報

每天打開 app → 自動更新「今日市場概況」:
- 大盤一句話(熱 / 平 / 冷)
- 觀察清單異常巡禮
- 全市場 Top 5 漲跌幅
- 大盤新聞 5 則

PRO 用戶可開啟 **08:30 推播通知**(開盤前 5 分鐘)。

---

### 🌙 資料時效說明(本 app 適合盤後使用)

| 資料來源 | 時效 | 適用 |
|---|---|---|
| 個股報價(yfinance) | **~15 分鐘延遲** | 盤後參考 |
| 法人籌碼(FinMind / TWSE) | **T+0 盤後**(15:30 後公布) | 隔日參考 |
| 月營收 | **每月 10 號前公布** | 月初更新 |
| 財報(資產負債 / 損益) | **每季公布** | 季初更新 |
| 大盤新聞(Google News) | **30 分鐘快取** | 接近即時 |
| 國際市場(yfinance) | **15 分鐘延遲** | 盤後參考 |

⚠️ **本 app 設計給「開盤前 5 分鐘看一下」「下班後做功課」用,不適合盤中即時下單**。

---

### ⚖️ 法律免責

所有數據來自 yfinance / FinMind 公開資料,App 提供之資訊僅供統計觀察,**不構成任何投資建議**。投資具有風險,自行判斷、自負損益。
        """)
        disclaimer()

    disclaimer()


def page_help():
    st.title("❓ 使用說明")

    st.markdown("""
    ### 這是什麼?
    一個 **本機的投資記帳工具**,幫你記錄手上的股票和加密貨幣,看資產分布。

    ### 不是什麼?
    - ❌ 不是投資建議系統,不會告訴你買什麼賣什麼
    - ❌ 不會自動抓即時股價(避免侵權)
    - ❌ 不會幫你下單
    - ❌ 不會把資料傳到別的地方(全部存本機)

    ### 怎麼用?
    1. **TW 股票** — 把你買的台股代號跟股數填進去
    2. **加密貨幣** — 把 BTC 數量、USDT 餘額等填進去
    3. **更新價格** — 看完盤後手動輸入收盤價
    4. **首頁** — 看總值和配置圓餅圖

    ### 資料安全
    - 所有資料存在 `app/user_data/` 資料夾
    - 不上傳、不雲端、不分享
    - 想備份就複製整個資料夾
    - 想清空就刪除資料夾下的 .json 檔

    ### 為什麼要手動輸入價格?
    - 自動抓網站價格在某些國家可能違反 ToS
    - 手動輸入最安全、最不會出包
    - 反正一天看一次盤就好,不會花很多時間
    """)

    disclaimer()


# ───────────────────────────────────────────────────────
def page_tools():
    """設定 + 市場資訊 + 說明 (放一起,平常少用)"""
    tab1, tab2, tab3 = st.tabs(["🔧 AI 設定", "📰 市場資訊", "❓ 使用說明"])
    with tab1:
        page_settings()
    with tab2:
        page_market()
    with tab3:
        page_help()


# 韭菜健檢 — 單頁設計,設定/說明都已整合到主頁 tab
PAGES = {"🩺 韭菜健檢": page_tw_stock_center}

with st.sidebar:
    st.markdown("""
    <div style='padding:6px 0 2px 0'>
      <div style='font-size:1.6rem; font-weight:800; color:#fff; line-height:1'>
        🩺 韭菜健檢
      </div>
      <div style='font-size:0.7rem; color:#5eead4; letter-spacing:1.5px; margin-top:4px;
                  font-weight:600'>
        LEEK CHECK
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.caption(f"📅 {datetime.now(TW).strftime('%m/%d %H:%M')}")
    st.divider()
    st.caption("💡 **避免被收割的 4 道防線**")
    st.caption("🩺 4 面健檢透視個股")
    st.caption("🌡️ 大盤溫度計")
    st.caption("📰 開盤前 5 分鐘晨報")
    st.caption("🎴 卡牌式追蹤觀察清單")
    st.divider()
    st.caption("⚖️ 不報明牌、不喊飆股")
    st.caption("🔒 全本機運算 · 資料不外傳")
    st.caption("🌱 韭菜不是命,是健檢不夠勤")

# 單頁直接呼叫
page_tw_stock_center()

# ── PWA 注入(放最底,避免影響版面) ──
st.components.v1.html(
    """
    <script>
    (function () {
      const head = window.parent.document.head;
      const addTag = (tag, attrs) => {
        const sel = Object.entries(attrs).map(([k, v]) => `[${k}="${v}"]`).join("");
        if (head.querySelector(`${tag}${sel}`)) return;
        const el = window.parent.document.createElement(tag);
        Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
        head.appendChild(el);
      };
      addTag("link", {rel: "manifest", href: "/app/static/manifest.json"});
      addTag("meta", {name: "theme-color", content: "#0f766e"});
      addTag("meta", {name: "apple-mobile-web-app-capable", content: "yes"});
      addTag("meta", {name: "apple-mobile-web-app-status-bar-style", content: "black-translucent"});
      addTag("meta", {name: "apple-mobile-web-app-title", content: "韭菜健檢"});
      addTag("link", {rel: "apple-touch-icon", href: "/app/static/icon-192.png"});
      addTag("link", {rel: "icon", type: "image/png", sizes: "192x192", href: "/app/static/icon-192.png"});
      addTag("link", {rel: "icon", type: "image/png", sizes: "512x512", href: "/app/static/icon-512.png"});
      if ("serviceWorker" in window.parent.navigator) {
        window.parent.navigator.serviceWorker
          .register("/app/static/sw.js", {scope: "/"})
          .catch((e) => console.warn("SW register failed:", e));
      }
    })();
    </script>
    """,
    height=0,
)
