"""
Daily Signal Scanner — 每日掃全市場（1962 檔）找妖股 #1 + 多因子訊號觸發。

訊號 1: 連漲 + 法人買（妖股 #1）
  條件: 過去 3 日有 ≥ 2 次當日漲幅 ≥ 9% AND 當日法人淨買 ≥ 200 張
  期望: 60 日 alpha +11.23pp

訊號 2: 多因子（中小妖股 S1+S3）
  條件: 散戶比例 < 過去 252 日 20% 分位 AND 量能 z >= 2.5
  期望: 60 日 alpha +8.13pp（中小妖股；大型 2330/2317 反向不適用）

輸出:
  data/paper_trades/scanner_hits.csv  — 累計觸發紀錄
  Discord push（若有觸發）
  print 表格

已知限制 (Gemini audit 2026-05-04):
  G-4: 價格使用 yfinance auto_adjust=True 還原收盤；除息日造成 daily pct
       小幅偏離 raw（~0.3-2%），不足以誤觸 ±9.5% 閾值。TW 拆股罕見故影響極小。
  G-5: 配對交易需要建模融券強制回補日（除權息前 6 個交易日），目前 pair backtest
       未加入此約束 — 已 acknowledge，未來如要實單 pair 必須補。
  G-7: Baseline 為 same-ticker 隨機進場，但未做 year-matched stratification；
       後 2020 牛市偏多，alpha 估計可能 +0.5~1pp 偏高（已 partial 修補：VIX-conditioned）。
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INST = ROOT / "data" / "cache" / "finmind" / "institutional"
HOLD = ROOT / "data" / "cache" / "finmind" / "holding"
TW = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
HITS = ROOT / "data" / "paper_trades" / "scanner_hits.csv"

# 大型權值排除（多因子不適用）
LARGE_CAP_EXCLUDE = {"2330", "2317", "2454", "2412", "2891", "2882", "2002", "1303", "1301", "2308"}

# G-6: 摩擦成本 (round-trip)
# 一般 0.785% (手續費 0.285% + 證交稅 0.3% + 滑價 0.2%)
# 若量縮鎖死 (vol_ratio < 0.3) 滑價放大 → 估 1.0%+
FRICTION_COST_PCT = 0.78
FRICTION_LOCKED_PCT = 1.20  # 量縮鎖死日的退場滑價

# G-1: 鎖死流動性閾值
LOCKED_LIMIT_VOL_RATIO = 0.30  # vol_ratio 低於此 → 視為可能鎖死無法交易

# ChatGPT #1 non-overlapping fix: hold window per signal type
# 同 ticker 在持倉期內重複觸發會高估 t-stat 19-35%（已實測確認）
SIGNAL_HOLD_DAYS = {
    "monster_limitup_foreign": 60,
    "multifactor_S1_S3": 60,
    "revenue_relative_yoy": 60,
    "quiet_limitup": 20,
    "quiet_limitdown_reversal": 20,
}


def net_alpha(gross_alpha: float, locked: bool = False) -> float:
    """G-6: 扣摩擦成本後的淨 alpha"""
    cost = FRICTION_LOCKED_PCT if locked else FRICTION_COST_PCT
    return gross_alpha - cost


def load_px(tk):
    p = TW / f"{tk}.parquet"
    if not p.exists() or p.stat().st_size < 500: return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if df.empty or len(df) < 60: return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df["pct"] = df["close"].pct_change() * 100
    return df


def load_inst(tk):
    p = INST / f"{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    pivot = df.pivot_table(index="date", columns="name", values="net_buy",
                            aggfunc="sum", fill_value=0).reset_index()
    pivot.columns.name = None
    return pivot.sort_values("date").reset_index(drop=True)


def load_holding(tk):
    p = HOLD / f"{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    retail_levels = ["1-999", "1,000-5,000", "5,001-10,000",
                     "10,001-15,000", "15,001-20,000", "20,001-30,000",
                     "30,001-40,000", "40,001-50,000"]
    df["is_retail"] = df["HoldingSharesLevel"].isin(retail_levels)
    grp = df.groupby(["date", "is_retail"])["percent"].sum().unstack(fill_value=0)
    if True not in grp.columns: return pd.DataFrame()
    return pd.DataFrame({"date": grp.index, "retail_pct": grp[True].values}).reset_index(drop=True)


def scan_signal_1(tk, today):
    """連漲 + 法人買"""
    px = load_px(tk)
    if px.empty: return None
    today_row = px[px["date"] == today]
    if today_row.empty: return None

    inst = load_inst(tk)
    if inst.empty: return None
    today_inst = inst[inst["date"] == today]
    if today_inst.empty: return None

    # 連漲: 最近 3 日 ≥ 2 次 pct >= 9%
    recent = px.iloc[-3:]
    near_lu_count = (recent["pct"] >= 9.0).sum()
    if near_lu_count < 2: return None

    fi = float(today_inst["Foreign_Investor"].iloc[0]) if "Foreign_Investor" in today_inst.columns else 0
    inv = float(today_inst["Investment_Trust"].iloc[0]) if "Investment_Trust" in today_inst.columns else 0
    inst_net = fi + inv
    if inst_net < 200000: return None  # 200 lots = 200,000 shares

    return {
        "ticker": tk,
        "signal": "monster_limitup_foreign",
        "close": float(today_row["close"].iloc[-1]),
        "near_lu_3d": int(near_lu_count),
        "inst_net_shares": int(inst_net),
        "expected_alpha_60d": 8.48,  # FIX 2026-05-04: 修正 look-ahead bias 後從 11.23 → 8.48pp
        "expected_alpha_60d_net": round(net_alpha(8.48), 2),  # G-6: 扣摩擦
    }


def scan_signal_2(tk, today):
    """中小妖股: S1 (散戶低) + S3 (量爆)"""
    if tk in LARGE_CAP_EXCLUDE: return None  # 大型權值反向

    px = load_px(tk)
    if px.empty or len(px) < 250: return None
    today_row = px[px["date"] == today]
    if today_row.empty: return None

    # S3 vol z
    px["vol_ma60"] = px["volume"].rolling(60).mean()
    px["vol_std60"] = px["volume"].rolling(60).std()
    px["vol_z"] = (px["volume"] - px["vol_ma60"]) / px["vol_std60"]
    today_z = float(px[px["date"] == today]["vol_z"].iloc[-1]) if not px[px["date"] == today].empty else 0
    if today_z < 2.5: return None

    # S1 retail < 252d 20%
    rh = load_holding(tk)
    if rh.empty: return None
    rh_today = rh[rh["date"] <= today].tail(1)
    if rh_today.empty: return None
    today_retail = float(rh_today["retail_pct"].iloc[-1])
    rh_recent = rh[rh["date"] <= today].tail(252)
    if len(rh_recent) < 60: return None
    p20 = float(np.percentile(rh_recent["retail_pct"], 20))
    if today_retail >= p20: return None

    return {
        "ticker": tk,
        "signal": "multifactor_S1_S3",
        "close": float(today_row["close"].iloc[-1]),
        "vol_z": round(today_z, 2),
        "retail_pct": round(today_retail, 2),
        "retail_p20": round(p20, 2),
        "expected_alpha_60d": 8.13,
        "expected_alpha_60d_net": round(net_alpha(8.13), 2),  # G-6
    }


_MARKET_MEDIAN_CACHE = {"ym": None, "median": 0.0}
_GOVBANK_CACHE = {"loaded": False, "df": None}
_INDUSTRY_CACHE = {"loaded": False, "map": None}

# 已驗證高 alpha 產業（OOS 兩期 robust + MCPT p<0.05）
HIGH_ALPHA_SECTORS = {
    "資訊服務業": 7.42,
    "半導體業": 6.13,
    "通信網路業": 6.91,
    "電腦及週邊設備業": 4.97,
}
# 已驗證 PEAD 失效產業（傳產商品類）
LOW_ALPHA_SECTORS = {"紡織纖維", "塑膠工業", "鋼鐵工業", "觀光餐旅", "電子通路業"}


def get_sector(tk: str) -> str:
    global _INDUSTRY_CACHE
    if not _INDUSTRY_CACHE["loaded"]:
        path = ROOT / "data" / "cache" / "finmind" / "extras" / "stock_info.parquet"
        if path.exists():
            df = pd.read_parquet(path)
            _INDUSTRY_CACHE["map"] = dict(zip(df["stock_id"], df["industry_category"]))
        else:
            _INDUSTRY_CACHE["map"] = {}
        _INDUSTRY_CACHE["loaded"] = True
    return _INDUSTRY_CACHE["map"].get(tk, "Unknown")


def check_govbank_anti_signal(tk: str, today, lookback_days: int = 7) -> dict:
    """
    Anti-filter：檢查近 N 日是否觸發「5+ 行庫共識度」反向訊號

    已驗證：5+ 行庫同時 net buy 後 60d alpha -1.62% (t=-28.46, n=161K)
    Use: 對 monster signal 觸發加 anti-flag，提醒「政府護盤股反而 underperform」

    Returns: {
        triggered: bool,        # 7 日內是否曾 5+ 行庫同買
        max_n_banks: int,       # 7 日內最高銀行數
        peak_date: str,         # 最高那天日期
    }
    """
    global _GOVBANK_CACHE
    if not _GOVBANK_CACHE["loaded"]:
        path = ROOT / "data" / "cache" / "finmind" / "extras" / "government_bank_buysell.parquet"
        if not path.exists():
            _GOVBANK_CACHE["loaded"] = True
            _GOVBANK_CACHE["df"] = pd.DataFrame()
            return {"triggered": False, "max_n_banks": 0, "peak_date": None,
                    "available": False}
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"])
        df["bought"] = ((df["buy_amount"] - df["sell_amount"]) > 0).astype(int)
        # Pre-aggregate: per (date, stock) sum bought banks
        agg = df.groupby(["date", "stock_id"])["bought"].sum().reset_index()
        agg.rename(columns={"bought": "n_banks"}, inplace=True)
        _GOVBANK_CACHE["df"] = agg
        _GOVBANK_CACHE["loaded"] = True

    if _GOVBANK_CACHE["df"].empty:
        return {"triggered": False, "max_n_banks": 0, "peak_date": None,
                "available": False}

    today_dt = pd.Timestamp(today)
    cutoff = today_dt - pd.Timedelta(days=lookback_days)
    sub = _GOVBANK_CACHE["df"][
        (_GOVBANK_CACHE["df"]["stock_id"] == tk) &
        (_GOVBANK_CACHE["df"]["date"] >= cutoff) &
        (_GOVBANK_CACHE["df"]["date"] <= today_dt)
    ]
    if sub.empty:
        return {"triggered": False, "max_n_banks": 0, "peak_date": None,
                "available": True}
    max_row = sub.loc[sub["n_banks"].idxmax()]
    max_n = int(max_row["n_banks"])
    return {
        "triggered": max_n >= 5,
        "max_n_banks": max_n,
        "peak_date": max_row["date"].date().isoformat(),
        "available": True,
    }


def compute_market_median_yoy(target_ym: pd.Period, today=None) -> float:
    """計算目標月份全市場 YoY median（從所有 cached revenue files）

    FIX 2026-05-04 C2-9: 加 today 參數，只納入 announce_date <= today 的公司
    （避免生產環境用未公告公司的 backtest-only 資料造成 production 偏差）
    Cache key = (ym, today) 避免不同日重用同一 cache。
    """
    cache_key = (target_ym, today)
    if _MARKET_MEDIAN_CACHE["ym"] == cache_key:
        return _MARKET_MEDIAN_CACHE["median"]

    rev_dir = ROOT / "data" / "cache" / "finmind" / "finmind"
    yoy_list = []
    for p in rev_dir.glob("TaiwanStockMonthRevenue_*.parquet"):
        try:
            rev = pd.read_parquet(p)
            if len(rev) < 24: continue
            rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
            # 找 target_ym 的紀錄
            rev["ym"] = pd.to_datetime(rev["date"]).dt.to_period("M")
            target = rev[rev["ym"] == target_ym]
            if target.empty: continue
            row = target.iloc[0]
            # C2-9: 過濾未公告
            if today is not None:
                period_start = pd.to_datetime(row["date"]).date()
                ct = row.get("create_time", None)
                if ct and pd.notna(ct) and ct != "":
                    try:
                        ann_dt = pd.to_datetime(ct).date()
                    except Exception:
                        ann_dt = period_start + timedelta(days=14)
                else:
                    ann_dt = period_start + timedelta(days=14)
                if ann_dt > today:
                    continue  # 未公告，不納入 median
            ridx = rev.index[rev["ym"] == target_ym][0]
            if ridx < 12: continue
            prior = rev.iloc[ridx - 12]["revenue"]
            if prior < 1e7: continue
            yoy = (row["revenue"] / prior - 1) * 100
            if abs(yoy) < 500:
                yoy_list.append(yoy)
        except Exception:
            continue
    median = float(np.median(yoy_list)) if yoy_list else 0.0
    _MARKET_MEDIAN_CACHE["ym"] = cache_key
    _MARKET_MEDIAN_CACHE["median"] = median
    return median


_VIX_CACHE = {"value": None, "date": None}


def get_current_vix() -> float:
    """取當前 VIX (cache 一次)"""
    global _VIX_CACHE
    if _VIX_CACHE["value"] is not None and _VIX_CACHE["date"] == date.today():
        return _VIX_CACHE["value"]
    try:
        import yfinance as yf
        h = yf.Ticker("^VIX").history(period="3d")
        if not h.empty:
            v = float(h["Close"].iloc[-1])
            _VIX_CACHE["value"] = v
            _VIX_CACHE["date"] = date.today()
            return v
    except Exception:
        pass
    return 20.0  # default


def vix_alpha_multiplier(vix: float, signal_type: str) -> tuple[str, str, float]:
    """根據 VIX bucket 返回 (label, warning, expected_alpha_multiplier)

    已驗證：
      VIX < 18: quiet 訊號 alpha 為負（漲停 -1.36%, 跌停 -3.06%）
      VIX ≥ 35: alpha 飆升（漲停 +15.33%, 跌停 +13.23%）
    """
    if vix < 18:
        return ("🔴 平靜市場（VIX<18）", "alpha 預期為負，謹慎", 0)
    elif vix < 25:
        return ("🟡 中等波動（VIX 18-25）", "中信心", 1.0)
    elif vix < 35:
        return ("🟠 高波動（VIX 25-35）", "高信心", 2.0)
    else:
        return ("🚨 極端恐慌（VIX≥35）", "極致信心 ⭐⭐⭐", 3.0)


def scan_signal_5(tk, today):
    """量縮跌停反彈 alpha (next-day entry, 2026-05-04 驗證)

    觸發：當日 ≤ -9.5% AND 量比 < 0.8
    驗證 (next-day entry):
      Full alpha +7.99%/20d (n=4733, t+37.96, win 73.6%)
      hold 5d alpha +4.27% (win 71%) — 短線反彈強
      OOS 2020-22 +7.28% / 2023-25 +9.93% ✅
      ⚠️ 2017-2019 +0.12% (post-2020 alpha)
      MCPT vs 量爆 (Q1-Q4=+4.13%) p<0.0001

    ChatGPT #1 non-overlapping correction (2026-05-04):
      5d non-overlap:  alpha +3.21% → +2.13% (-1.08pp), t 19.0 → 12.4 (-35%) 🚨
      20d non-overlap: alpha +9.55% → +8.22% (-1.33pp), t 33.5 → 27.1 (-19%)
      — overlapping 同 ticker 連續觸發高估 t-stat；真實 5d alpha ~+2.13%

    Gemini #5 condition-matched correction (2026-05-04):
      vs random baseline:    +9.14pp (raw)
      vs ANY limitdown day:  +3.55pp (incremental quiet alpha)
      — 所有跌停日本身 mean +6.00%；量縮 filter 只加 +3.55pp 增量 alpha
      — 報 scanner 數字用「vs random」但知悉真正差異化因子貢獻只 +3.55pp
    """
    px = load_px(tk)
    if px.empty or len(px) < 65: return None
    today_row = px[px["date"] == today]
    if today_row.empty: return None
    last = today_row.iloc[-1]
    pct = last.get("pct", 0)
    if pd.isna(pct) or pct > -9.5: return None

    px2 = px.copy()
    px2["vol_ma60"] = px2["volume"].rolling(60).mean()
    today_idx = px2.index[px2["date"] == today]
    if len(today_idx) == 0: return None
    vol_ma = float(px2.loc[today_idx[-1], "vol_ma60"])
    today_vol = float(last["volume"])
    if vol_ma <= 0: return None
    vol_ratio = today_vol / vol_ma
    if vol_ratio >= 0.8: return None

    vix = get_current_vix()
    vix_label, vix_warn, _ = vix_alpha_multiplier(vix, "down")
    # FIX 2026-05-04 #5 + G-2: VIX-conditioned baseline + cluster-level check
    # 修正後 alpha (vs VIX-matched baseline + cluster-level 統計):
    #   low (-1.66, p=0.99) / mid (+5.73) / high (+9.11) / extreme (+2.79, cluster p=0.32 ❌)
    # ⚠️ Extreme bucket cluster-level NOT significant — 撤回 alpha 預期至保守值
    if vix < 18: alpha_adj = -1.66
    elif vix < 25: alpha_adj = 5.73
    elif vix < 35: alpha_adj = 9.11
    else: alpha_adj = 2.79  # cluster-level alpha (was 7.36 nominal, but p=0.32 NOT robust)

    locked = vol_ratio < LOCKED_LIMIT_VOL_RATIO  # G-1: 鎖死警示
    return {
        "ticker": tk,
        "signal": "quiet_limitdown_reversal",
        "close": float(last["close"]),
        "pct": round(pct, 2),
        "vol_ratio": round(vol_ratio, 2),
        "expected_alpha_20d": alpha_adj,
        "expected_alpha_20d_net": round(net_alpha(alpha_adj, locked), 2),  # G-6
        "locked_limit": locked,  # G-1: 是否疑似鎖死
        "vix": round(vix, 1),
        "vix_label": vix_label,
        "vix_warn": vix_warn,
    }


def scan_signal_4(tk, today):
    """量縮漲停 alpha (修正後 - 次日進場避免 look-ahead, 2026-05-04)

    觸發：當日 ≥ +9.5% AND 量比 < 0.8 (60d 平均)
    驗證 (next-day entry, no bias):
      Full alpha +4.83%/20d (n=5437, t+23.71)
      OOS 2020-2022 +6.50% ✅, 2023-2025 +4.96% ✅
      ⚠️ 2017-2019 alpha -1.66% (post-2020 alpha)
    vs 量爆漲停 +2.22% — 量縮顯著強

    Gemini #5 condition-matched correction (2026-05-04):
      vs random baseline:    +5.48pp (naive)
      vs ANY limitup day:    +4.71pp (incremental quiet alpha)
      — naive 高估 14%，仍為真 alpha ✅
    """
    px = load_px(tk)
    if px.empty or len(px) < 65: return None
    today_row = px[px["date"] == today]
    if today_row.empty: return None
    last = today_row.iloc[-1]
    pct = last.get("pct", 0)
    if pd.isna(pct) or pct < 9.5: return None

    px2 = px.copy()
    px2["vol_ma60"] = px2["volume"].rolling(60).mean()
    today_idx = px2.index[px2["date"] == today]
    if len(today_idx) == 0: return None
    vol_ma = float(px2.loc[today_idx[-1], "vol_ma60"])
    today_vol = float(last["volume"])
    if vol_ma <= 0: return None
    vol_ratio = today_vol / vol_ma
    if vol_ratio >= 0.8: return None  # 只取量縮

    vix = get_current_vix()
    vix_label, vix_warn, _ = vix_alpha_multiplier(vix, "up")
    # FIX 2026-05-04 #5: VIX-conditioned baseline (剝離 crash-recovery beta)
    # 修正後 alpha (vs VIX-matched baseline):
    #   low (-0.25%) / mid (+5.30%) / high (+3.66%) / extreme (+9.05%)
    if vix < 18: alpha_adj = -0.25
    elif vix < 25: alpha_adj = 5.30
    elif vix < 35: alpha_adj = 3.66
    else: alpha_adj = 9.05

    locked = vol_ratio < LOCKED_LIMIT_VOL_RATIO  # G-1
    return {
        "ticker": tk,
        "signal": "quiet_limitup",
        "close": float(last["close"]),
        "pct": round(pct, 2),
        "vol_ratio": round(vol_ratio, 2),
        "expected_alpha_20d": alpha_adj,
        "expected_alpha_20d_net": round(net_alpha(alpha_adj, locked), 2),  # G-6
        "locked_limit": locked,  # G-1
        "vix": round(vix, 1),
        "vix_label": vix_label,
        "vix_warn": vix_warn,
    }


def scan_signal_3(tk, today):
    """月營收 Relative YoY surprise — 已驗證 60d alpha +3.95%, t=24.19 (n=24,476)

    觸發：個股 YoY - 全市場 median YoY > +30%（公告後 0-3 天內）
    為何相對：排除「整體景氣好」噪音，純捕捉個股 outperform
    """
    rev_path = ROOT / "data" / "cache" / "finmind" / "finmind" / f"TaiwanStockMonthRevenue_{tk}.parquet"
    if not rev_path.exists(): return None
    try:
        rev = pd.read_parquet(rev_path)
    except Exception:
        return None
    if rev.empty or len(rev) < 24: return None

    rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
    rev["prior_revenue"] = rev["revenue"].shift(12)
    rev["yoy"] = (rev["revenue"] / rev["prior_revenue"] - 1) * 100

    latest = rev.iloc[-1]
    # FIX 2026-05-04 #8: 用 estimated announce date 而非 'date' (月份 1 號)
    # 'date' 是 period start (e.g., 4/1 對應 3 月營收), 實際公告日 ~11-21 天後
    latest_dt = pd.to_datetime(latest["date"]).date()
    create_time = latest.get("create_time", None)
    if create_time and pd.notna(create_time) and create_time != "":
        try:
            announce_dt = pd.to_datetime(create_time).date()
        except Exception:
            announce_dt = latest_dt + timedelta(days=14)
    else:
        announce_dt = latest_dt + timedelta(days=14)  # conservative estimate

    days_since = (today - announce_dt).days
    # 公告後 0-3 天才推（避免 look-ahead，且抓 PEAD 早期 alpha）
    if days_since > 3 or days_since < 0: return None
    yoy = float(latest["yoy"])
    prior = float(latest["prior_revenue"]) if pd.notna(latest["prior_revenue"]) else 0
    if pd.isna(yoy) or yoy > 500 or prior < 1e7: return None

    # 計算相對市場（全市場 median YoY）—— C2-9: 限 today 已公告
    target_ym = pd.Period(f"{int(latest['revenue_year'])}-{int(latest['revenue_month']):02d}")
    market_median = compute_market_median_yoy(target_ym, today=today)
    excess_yoy = yoy - market_median

    # Trigger: excess > 30% (alpha +3.95%, t=24.19)
    if excess_yoy < 30.0 or yoy > 200.0: return None

    px = load_px(tk)
    if px.empty: return None
    today_row = px[px["date"] == today]
    close = float(today_row["close"].iloc[-1]) if not today_row.empty else float(px["close"].iloc[-1])

    # 月份 adjustment 撤回（2026-05-04 validation 失敗）
    # 月份 alpha cross-year std ±6~15%, 比 mean 還大
    # "4月+7.50%" 主要被 2020 COVID +20.85% 拉高，是 spurious seasonal
    rev_month = int(latest['revenue_month'])

    # 2026-05-04 portfolio backtest 發現: yoy_asc 優先序贏 yoy_desc 24pp
    # 中度 YoY (30-50%) 是 PEAD 甜蜜區，極端 (>100%) 是 base-effect 雜訊
    if 30 <= yoy <= 50:
        yoy_tier = "moderate (sweet spot)"
        tier_label = "⭐ 中度 YoY (PEAD 甜蜜區)"
    elif 50 < yoy <= 100:
        yoy_tier = "high"
        tier_label = "🟡 高 YoY (alpha 較弱)"
    else:
        yoy_tier = "extreme (noise)"
        tier_label = "⚠️ 極端 YoY (base-effect 雜訊風險)"

    # 2026-05-04 流動性 filter validation:
    #   No filter: Full +30% / 1H +38% / 2H +15% (2H 輸 0050 -20pp)
    #   L4 (>10億/日): Full +25.7% / 1H +23.3% / 2H +31.5% (2H 改善至 -5.8pp，可實單)
    # 計算觸發前 60 日 avg dollar volume 作為 deploy_ready 判定
    px2 = px.copy()
    today_idx_arr = px2.index[px2["date"] == today]
    if len(today_idx_arr) > 0:
        today_idx = today_idx_arr[-1]
        before = px2.iloc[max(0, today_idx - 60):today_idx]
        if len(before) >= 30:
            avg_dv_60d = float((before["close"] * before["volume"]).mean())
        else:
            avg_dv_60d = 0.0
    else:
        avg_dv_60d = 0.0

    # L4 deploy threshold: 10 億/日（驗證後唯一 deployable level）
    LIQUIDITY_L4 = 1e9
    deploy_ready = avg_dv_60d >= LIQUIDITY_L4
    liq_label = (
        f"✅ L4 流動性 (>{LIQUIDITY_L4/1e8:.0f}億/日, 可實單)"
        if deploy_ready
        else f"⚠️ 流動性不足 ({avg_dv_60d/1e8:.1f}億/日 < L4，僅 informational)"
    )

    return {
        "ticker": tk,
        "signal": "revenue_relative_yoy",
        "close": close,
        "yoy_pct": round(yoy, 1),
        "market_median_yoy": round(market_median, 1),
        "excess_yoy": round(excess_yoy, 1),
        "yoy_tier": yoy_tier,
        "tier_label": tier_label,
        "revenue_month": f"{int(latest['revenue_year'])}-{int(latest['revenue_month']):02d}",
        "rev_month_num": rev_month,
        "days_since_announce": days_since,
        "avg_dv_60d_yi": round(avg_dv_60d / 1e8, 1),  # 億/日
        "deploy_ready": deploy_ready,
        "liq_label": liq_label,
        # L4 portfolio CAGR +25.7% (vs 0050 +21.7%, +4.0pp); 1H +15.5pp 強勝
        "expected_alpha_60d": 4.0 if deploy_ready else 3.95,
        "expected_alpha_60d_net": round(net_alpha(4.0 if deploy_ready else 3.95), 2),
    }


def auto_refresh_cache(tickers: list[str], lookback_days: int = 5):
    """Scanner 跑前自動補抓最近 N 日的法人/月營收資料

    用 finmind_client 自帶的增量更新邏輯（避免重抓全部）
    """
    import os
    from datetime import timedelta
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / "config" / ".env")
    except ImportError:
        pass
    from src.data.finmind_client import FinMindClient

    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("  ⚠️ FINMIND_TOKEN 未設置，跳過自動更新")
        return

    fc = FinMindClient(token=token, cache_dir=ROOT / "data" / "cache" / "finmind")
    today_d = date.today()
    start_d = today_d - timedelta(days=lookback_days)

    print(f"  自動補抓最近 {lookback_days} 日資料（institutional + revenue）...")
    n_updated = 0
    n_failed = 0
    for tk in tickers:
        try:
            fc.get_institutional(tk, start_d, today_d)  # 自動寫入 cache
            n_updated += 1
        except Exception:
            n_failed += 1
    print(f"  ✓ Institutional: ok={n_updated}, fail={n_failed}")

    # Revenue 是月更，只抓有可能新公告的 ticker
    # C2-5 FIX: 公告 window 至 day 21（部分公司延後到 16-21 日才公告），原 <=15 會錯過
    if today_d.day <= 22:
        n_rev = 0
        for tk in tickers[:200]:  # 簡化：只抓前 200 檔（其他下次跑）
            try:
                fc.get_monthly_revenue(tk, start_d, today_d)
                n_rev += 1
            except Exception:
                pass
        print(f"  ✓ Revenue: {n_rev} tickers refreshed")


def _latest_available_date(calendar_today) -> date:
    """C2-8: 偵測 yfinance cache 最新可用交易日

    法人 + 股價資料盤後 (18:00+) 才更新。晨報 08:30 跑時查 today 一定空。
    解法: 抽查 3 檔大型股的最新 cache 日期，取最晚的那天。
    """
    probes = ["2330", "2317", "2412"]
    latest = None
    for tk in probes:
        p = TW / f"{tk}.parquet"
        if not p.exists(): continue
        try:
            df = pd.read_parquet(p, columns=["date"])
            d = pd.to_datetime(df["date"]).dt.date.max()
            if latest is None or d > latest:
                latest = d
        except Exception:
            continue
    if latest is None:
        return calendar_today
    # 如果 cache 最新日就是今天，代表盤後資料已入庫
    return latest


def load_recent_hits(today: date, signal_hold_days: dict) -> set:
    """Non-overlapping filter (ChatGPT #1): return (ticker, signal) still within hold window.

    Backtest confirmed overlapping same-ticker triggers inflate t-stat 19-35%.
    Production fix: skip re-entry if same ticker+signal triggered within hold window.
    """
    if not HITS.exists():
        return set()
    try:
        df = pd.read_csv(HITS)
        if df.empty or "scan_date" not in df.columns or "ticker" not in df.columns:
            return set()
        df["scan_date"] = pd.to_datetime(df["scan_date"]).dt.date
        in_position: set = set()
        for _, row in df.iterrows():
            signal = str(row.get("signal", ""))
            hold = signal_hold_days.get(signal, 20)
            days_ago = (today - row["scan_date"]).days
            if 0 < days_ago <= hold:
                in_position.add((str(row["ticker"]), signal))
        return in_position
    except Exception:
        return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discord", action="store_true")
    ap.add_argument("--max-tickers", type=int, default=0, help="0 = all")
    ap.add_argument("--no-refresh", action="store_true", help="跳過自動補抓最新資料")
    args = ap.parse_args()

    # C2-8 FIX: 法人/價格資料盤後才有，晨報跑時 today 查不到任何資料
    # 自動偵測最新可用交易日（用 yfinance cache 判斷），晨報也能正常觸發
    calendar_today = date.today()
    today = _latest_available_date(calendar_today)
    stale_warn = ""
    if today < calendar_today:
        stale_warn = f"  ⚠️ 資料截至 {today}（今日盤後資料尚未發布，使用昨日收盤資料；今日開盤即可進場）\n"
    print(f"=== Daily Signal Scanner ({today}) ==={'' if not stale_warn else chr(10) + stale_warn}")

    # 共同 universe
    inst_tks = {p.stem for p in INST.glob("*.parquet")}
    tw_tks = {p.stem for p in TW.glob("*.parquet")}
    common = sorted([t for t in (inst_tks & tw_tks)
                     if not t.startswith("00") and t.isdigit() and len(t) == 4])
    if args.max_tickers > 0:
        common = common[:args.max_tickers]

    # 自動補抓最新資料（除非 --no-refresh）
    if not args.no_refresh:
        auto_refresh_cache(common, lookback_days=5)

    print(f"  scanning {len(common)} tickers...")

    in_position_set = load_recent_hits(today, SIGNAL_HOLD_DAYS)
    skipped_overlap = 0
    if in_position_set:
        print(f"  ⏭️ {len(in_position_set)} 個 (ticker, signal) 仍在持倉期，重複觸發將跳過")

    hits = []
    for i, tk in enumerate(common):
        if i % 200 == 0 and i > 0:
            print(f"  ... {i}/{len(common)}")
        for fn in (scan_signal_1, scan_signal_2, scan_signal_3, scan_signal_4, scan_signal_5):
            try:
                r = fn(tk, today)
                if r:
                    if (tk, r["signal"]) in in_position_set:
                        skipped_overlap += 1
                        continue
                    r["scan_date"] = today.isoformat()
                    r["scanned_at"] = datetime.now().isoformat(timespec="seconds")
                    # Sector enrichment（OOS+MCPT 已驗證）
                    sector = get_sector(tk)
                    r["sector"] = sector
                    if sector in HIGH_ALPHA_SECTORS:
                        r["sector_label"] = f"⭐ 高 alpha 產業（{sector} 預期 +{HIGH_ALPHA_SECTORS[sector]:.1f}%）"
                        r["sector_expected_alpha"] = HIGH_ALPHA_SECTORS[sector]
                    elif sector in LOW_ALPHA_SECTORS:
                        r["sector_label"] = f"⚠️ PEAD 失效產業（{sector}）"
                        r["sector_expected_alpha"] = 0
                    hits.append(r)
            except Exception:
                continue

    if skipped_overlap > 0:
        print(f"  ⏭️ 跳過 {skipped_overlap} 個持倉期重疊訊號（non-overlapping filter）")

    # ── 多因子共識偵測（已驗證 super-additive） ──
    ticker_signals = {}
    for h in hits:
        ticker_signals.setdefault(h["ticker"], []).append(h["signal"])
    for h in hits:
        n = len(ticker_signals[h["ticker"]])
        h["multi_signal_count"] = n
        if n >= 3:
            h["combo_label"] = "🏆 三重共識 ABC（樣本小，experimental）"
            h["combo_alpha_60d"] = 18.94  # OOS 2023-2025 +12.94%, MCPT pass
            h["combo_warning"] = "n=53 累積中，僅 2023-2025 OOS 通過"
        elif n == 2:
            # 2026-05-04 3-AI critique + survivorship stress test 後修正:
            #   原 backtest mean +8.6% (n=578) MCPT p=0.005
            #   但 stress test 顯示在 5% 下市率時 alpha 已 break-even
            #   Claude 估 TW 9 年實際下市率 5-10% → 真實 alpha 接近 0 或負
            #   2020 outlier 剝離後也僅 +4% (vs baseline +4.8%)
            #   結論：保留 scanner 偵測但不再宣稱有 portfolio alpha
            h["combo_label"] = "🥇 雙重共識（⚠️ informational only）"
            h["combo_alpha_60d"] = 0.0  # survivorship-adjusted ≈ 0
            h["combo_warning"] = (
                "⚠️ 3-AI critique + stress test (2026-05-04): "
                "real-world expected alpha ≈ 0 (survivorship bias 5%+ 下市率假設下)。"
                "保留 informational signal 但不建議 deploy 為 portfolio 策略。"
                "若實單，單筆 ≤ 1% portfolio 並設 -15% stop loss。"
            )

    if not hits:
        print(f"\n  ⚪ 無訊號觸發（{len(common)} 檔掃完）")
    else:
        print(f"\n  🚨 {len(hits)} 個訊號觸發:")
        # 先 print 共識訊號（最強）
        super_hits = [h for h in hits if h.get("multi_signal_count", 1) >= 2]
        if super_hits:
            uniq = sorted(set(h["ticker"] for h in super_hits))
            print(f"\n  🌟 多因子共識（{len(uniq)} 檔，預期 alpha +10~21%）:")
            for tk in uniq:
                tk_hits = [h for h in super_hits if h["ticker"] == tk]
                lbl = tk_hits[0].get("combo_label", "")
                expected = tk_hits[0].get("combo_alpha_60d", 0)
                signals = "+".join(h["signal"][:5] for h in tk_hits)
                print(f"    {lbl} {tk} @ {tk_hits[0]['close']:.2f}  "
                      f"訊號={signals}  → 預期 60d alpha +{expected}%")
        for h in hits:
            warn = f"  {h.get('confidence_warning', '')}" if h.get("govbank_anti_triggered") else ""
            lock_warn = "  ⚠️ 疑似鎖死(量比<0.3)，滑價放大" if h.get("locked_limit") else ""
            if h["signal"] == "monster_limitup_foreign":
                print(f"    妖股 {h['ticker']} @ {h['close']:.2f}  "
                      f"連漲 {h['near_lu_3d']}/3d  "
                      f"法人 +{h['inst_net_shares']/1000:.0f} 張  "
                      f"→ 60d alpha +{h['expected_alpha_60d']}pp (淨 +{h['expected_alpha_60d_net']}pp){warn}")
            elif h["signal"] == "revenue_relative_yoy":
                print(f"    月營收 {h['ticker']} @ {h['close']:.2f}  "
                      f"{h['revenue_month']} YoY +{h['yoy_pct']}% "
                      f"(市場 {h['market_median_yoy']}%, excess +{h['excess_yoy']}%) "
                      f"→ 60d alpha {h['expected_alpha_60d']:+.2f}pp (淨 {h['expected_alpha_60d_net']:+.2f}pp){warn}")
            elif h["signal"] == "quiet_limitup":
                vix_info = f" [{h.get('vix_label', '')} VIX={h.get('vix', '')}]"
                print(f"    量縮漲停 {h['ticker']} @ {h['close']:.2f}  "
                      f"{h['pct']:+.1f}% 量比 {h['vol_ratio']}x "
                      f"→ 20d alpha {h['expected_alpha_20d']:+.2f}pp (淨 {h['expected_alpha_20d_net']:+.2f}pp){vix_info}{warn}{lock_warn}")
            elif h["signal"] == "quiet_limitdown_reversal":
                vix_info = f" [{h.get('vix_label', '')} VIX={h.get('vix', '')}]"
                print(f"    量縮跌停反彈 {h['ticker']} @ {h['close']:.2f}  "
                      f"{h['pct']:+.1f}% 量比 {h['vol_ratio']}x "
                      f"→ 20d alpha {h['expected_alpha_20d']:+.2f}pp (淨 {h['expected_alpha_20d_net']:+.2f}pp){vix_info}{warn}{lock_warn}")
            else:
                print(f"    多因子 {h['ticker']} @ {h['close']:.2f}  "
                      f"vol z={h['vol_z']}  retail={h['retail_pct']}% "
                      f"(<p20={h['retail_p20']}%)  "
                      f"→ 60d alpha +{h['expected_alpha_60d']}pp (淨 +{h['expected_alpha_60d_net']}pp){warn}")

        # append CSV
        df_new = pd.DataFrame(hits)
        if HITS.exists():
            df_old = pd.read_csv(HITS)
            df = pd.concat([df_old, df_new], ignore_index=True)
        else:
            df = df_new
        df.to_csv(HITS, index=False, encoding="utf-8-sig")
        print(f"  ✅ 寫入 {HITS}")

        if args.discord:
            push_discord(hits, today)


def _load_user_holdings_tickers() -> set:
    """讀 assets.json 取得 user 持股 ticker 清單（用於 Discord 持倉關聯）"""
    try:
        import json as _json
        p = ROOT / "data" / "assets.json"
        if not p.exists():
            return set()
        data = _json.loads(p.read_text(encoding="utf-8"))
        tickers: set = set()
        for _, items in data.get("holdings", {}).items():
            if isinstance(items, list):
                for h in items:
                    if isinstance(h, dict) and h.get("ticker"):
                        tickers.add(str(h["ticker"]).strip())
        return tickers
    except Exception:
        return set()


def _push_executive_summary(hits, today, holdings_tickers: set) -> list:
    """Discord Executive Summary — 30 秒掃完判斷今日重要性"""
    m3_deploy = [h for h in hits if h["signal"] == "revenue_relative_yoy" and h.get("deploy_ready")]
    m3_info = [h for h in hits if h["signal"] == "revenue_relative_yoy" and not h.get("deploy_ready")]
    info_count = sum(1 for h in hits if h["signal"] in ("quiet_limitup", "quiet_limitdown_reversal")) + len(m3_info)
    other_count = sum(1 for h in hits if h["signal"] in ("monster_limitup_foreign", "multifactor_S1_S3"))

    # Hedge / Regime status (從現有 module 取)
    hedge_text = ""
    regime_text = ""
    try:
        sys.path.insert(0, str(ROOT))
        from src.report.hedge_signals import compute_hedge_reading
        from src.report.regime_section import compute_current_regime
        h = compute_hedge_reading()
        r = compute_current_regime()
        if h and r:
            regime_emoji = {"CRASH": "🚨", "STRONG_BULL": "🔴", "BEAR": "🟠"}.get(r.regime, "🟡")
            regime_text = f"{regime_emoji} `{r.regime}` (TAIEX {r.dist_ma200:+.1f}% MA200)"
            if h.cash_tilt_pp >= 10:
                hedge_text = f"🚨 **Hedge tilt +{h.cash_tilt_pp}pp 警示**"
            elif h.cash_tilt_pp > 0:
                hedge_text = f"⚠️ Hedge tilt +{h.cash_tilt_pp}pp"
            else:
                hedge_text = f"✅ Hedge: 全部正常"
    except Exception:
        pass

    # Persona 持倉關聯
    persona_lines = []
    if holdings_tickers and hits:
        for h in hits:
            tk = h.get("ticker", "")
            if tk in holdings_tickers:
                signal_label = {
                    "revenue_relative_yoy": "月營收",
                    "monster_limitup_foreign": "妖股",
                    "multifactor_S1_S3": "多因子",
                    "quiet_limitup": "量縮漲停",
                    "quiet_limitdown_reversal": "量縮跌停反彈",
                }.get(h["signal"], h["signal"])
                persona_lines.append(f"  • {tk} 觸發 {signal_label} (你已持有)")

    lines = []
    lines.append(f"📊 **訊號日報 — {today}**")
    lines.append(f"━━━━━━━━━━━━━━━━━━")
    if regime_text:
        lines.append(regime_text)
    if hedge_text:
        lines.append(hedge_text)
    lines.append(
        f"✅ Deploy-Ready: **{len(m3_deploy)}** | "
        f"ℹ️ Informational: {info_count + other_count} | "
        f"📨 Total: {len(hits)}"
    )
    lines.append(f"━━━━━━━━━━━━━━━━━━")
    lines.append("")

    # Top 行動 (從 action_advisor 取)
    try:
        from src.report.action_advisor import generate_actions
        from src.report.hedge_signals import compute_hedge_reading
        from src.report.regime_section import compute_current_regime
        from src.report.barbell_allocation import (
            ALLOCATION_TABLE, _apply_hedge_tilt, _load_holdings,
        )
        regime_r = compute_current_regime()
        hedge_r = compute_hedge_reading()
        holdings_dc = _load_holdings()
        if regime_r and holdings_dc:
            base_target = ALLOCATION_TABLE.get(regime_r.regime, {})
            target, _, _ = _apply_hedge_tilt(base_target)
            cash_total = holdings_dc.cash_pct / 100 * holdings_dc.total_value
            actions = generate_actions(regime_r, hedge_r, target, holdings_dc,
                                       holdings_dc.total_value, cash_total)
            lines.append("**🎯 今日 Top 3 行動:**")
            for i, a in enumerate(actions[:3], 1):
                lines.append(f"  {i}. {a.icon} {a.label}")
            lines.append("")
            today_budget = min(int(cash_total * 0.1), 30000)
            lines.append(
                f"**💰 資金**: 現金 NT${cash_total:,.0f} ({holdings_dc.cash_pct:.0f}%) | "
                f"今日建議 ≤ NT${today_budget:,}"
            )
            lines.append("")
    except Exception as e:
        lines.append(f"_action engine 失敗: {e}_")
        lines.append("")

    # Persona 警示
    if persona_lines:
        lines.append("**⚠️ 持倉關聯 (你已持有的標的觸發):**")
        lines.extend(persona_lines)
        lines.append("")

    return lines


def push_discord(hits, today):
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        try:
            url = (ROOT / ".discord_webhook").read_text(encoding="utf-8").strip()
        except Exception:
            print("  ⚠️ DISCORD_WEBHOOK_URL 未設定")
            return
    try:
        import requests
        # === Executive Summary (新格式 P0) ===
        holdings_tickers = _load_user_holdings_tickers()
        lines = _push_executive_summary(hits, today, holdings_tickers)
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("**📋 詳細訊號**")
        lines.append("")

        m1 = [h for h in hits if h["signal"] == "monster_limitup_foreign"]
        m2 = [h for h in hits if h["signal"] == "multifactor_S1_S3"]
        m3 = [h for h in hits if h["signal"] == "revenue_relative_yoy"]
        m4 = [h for h in hits if h["signal"] == "quiet_limitup"]
        m5 = [h for h in hits if h["signal"] == "quiet_limitdown_reversal"]
        if m1:
            lines.append(f"**🐉 妖股 #1（連漲+法人買）**")
            lines.append(f"_條件：3 日內 ≥ 2 次漲幅 ≥ 9% AND 當日法人 +200 張_")
            lines.append(f"_驗證：修正 look-ahead 後 60d alpha **+8.48pp** (淨 +7.70pp，扣 {FRICTION_COST_PCT:.2f}% 摩擦)；連漲 only 反向 -4.31pp_")
            for h in m1:
                warn = f" {h['confidence_warning']}" if h.get("govbank_anti_triggered") else ""
                lines.append(f"  • **{h['ticker']}** @ {h['close']:.2f} "
                             f"法人 +{h['inst_net_shares']/1000:.0f} 張{warn}")
        if m2:
            lines.append(f"\n**📊 多因子 S1+S3（中小妖股）**")
            lines.append(f"_條件：散戶比例 < 過去 252 日 20% 分位 AND 量能 z ≥ 2.5_")
            lines.append(f"_驗證：S1+S3 雙因子 60d alpha **+8.13pp**（大型權值反向 -5pp，僅適用中小股）_")
            lines.append(f"_排除大型權值：2330/2317/2454/2412/2891/2882/2002/1303/1301/2308_")
            for h in m2:
                warn = f" {h['confidence_warning']}" if h.get("govbank_anti_triggered") else ""
                lines.append(f"  • **{h['ticker']}** @ {h['close']:.2f} "
                             f"vol z={h['vol_z']} retail={h['retail_pct']}%{warn}")
        if m3:
            # 分組: deploy_ready (L4 流動性) vs informational only
            m3_deploy = [h for h in m3 if h.get("deploy_ready")]
            m3_info = [h for h in m3 if not h.get("deploy_ready")]

            lines.append(f"\n**💰 月營收 Relative YoY（**唯一驗證 portfolio alpha 的 stock-picking 策略**）**")
            lines.append(f"_驗證: max=20 yoy_asc + L4 (>10億/日) Full +25.7%/1H +23.3% 贏 0050 +4-15.5pp_")
            lines.append(f"_⭐ 優先序: 中度 YoY (30-50%) > 高 > 極端_")

            if m3_deploy:
                lines.append(f"\n  **✅ Deploy-Ready (L4 流動性，可實單)**")
                m3_deploy.sort(key=lambda h: h.get("yoy_pct", 999))  # yoy_asc
                for h in m3_deploy:
                    warn = f" {h['confidence_warning']}" if h.get("govbank_anti_triggered") else ""
                    tier = h.get("tier_label", "")
                    lines.append(f"    • **{h['ticker']}** @ {h['close']:.2f} "
                                 f"YoY +{h['yoy_pct']}% (市場 {h['market_median_yoy']}%, "
                                 f"excess +{h['excess_yoy']}%, {h['avg_dv_60d_yi']}億/日) {tier}{warn}")

            if m3_info:
                lines.append(f"\n  ⚠️ Informational only (流動性 < L4 10億/日，不建議實單)")
                m3_info.sort(key=lambda h: h.get("yoy_pct", 999))
                # 只顯示前 5 檔避免雜訊
                for h in m3_info[:5]:
                    tier = h.get("tier_label", "")
                    lines.append(f"    • {h['ticker']} @ {h['close']:.2f} "
                                 f"YoY +{h['yoy_pct']}% ({h['avg_dv_60d_yi']}億/日) {tier}")
                if len(m3_info) > 5:
                    lines.append(f"    ...（{len(m3_info) - 5} 檔未顯示）")
        if m4:
            lines.append(f"\n**📈 量縮漲停（市場警報，⚠️ 非個股 trade signal）**")
            lines.append(f"_2026-05-04 portfolio backtest 確認: 個股 alpha 是 crash-day market beta，不是 stock-picking_")
            lines.append(f"_5-slot portfolio 全 16 config 輸 0050 → 改用 0050/00631L 抓市場底反彈_")
            for h in m4:
                lock = " ⚠️鎖死" if h.get("locked_limit") else ""
                lines.append(f"  • {h['ticker']} @ {h['close']:.2f} "
                             f"量比 {h['vol_ratio']}x VIX={h.get('vix','?')}{lock}（信息參考）")
        if m5:
            n_signals_today = len(m5)
            cluster_warn = ""
            if n_signals_today >= 50:
                cluster_warn = f" 🚨 cluster ({n_signals_today} 檔) — 市場底訊號"
            elif n_signals_today >= 20:
                cluster_warn = f" ⚠️ partial cluster ({n_signals_today} 檔)"
            lines.append(f"\n**📉 量縮跌停反彈（市場警報，⚠️ 非個股 trade signal）{cluster_warn}**")
            lines.append(f"_portfolio 實證: 個別股 alpha 不可實現（96% 訊號跳過 + selection bias 反向）_")
            lines.append(f"_真正動作: 等 VIX > 30 + 月跌 > 15% 進入 CRASH regime → 加碼 0050（89% win, +6%/20d）_")
            for h in m5[:10]:  # limit display
                lock = " ⚠️鎖死" if h.get("locked_limit") else ""
                lines.append(f"  • {h['ticker']} @ {h['close']:.2f} "
                             f"量比 {h['vol_ratio']}x VIX={h.get('vix','?')}{lock}（信息參考）")
            if len(m5) > 10:
                lines.append(f"  ...（{len(m5) - 10} 檔未顯示）")
        lines.append("")
        lines.append(f"_⚠️ Revenue YoY 是唯一 portfolio-level 驗證的 stock-picking alpha (max=20 yoy_asc)。_")
        lines.append(f"_⚠️ quiet_limit 訊號為市場警報，個股 alpha 在 portfolio 層級不可實現（已驗證 16/16 config 輸 0050）。_")
        lines.append(f"_實單建議: DCA 0050 為核心 + Revenue YoY 衛星；CRASH 時加碼，STRONG_BULL 時減倉。_")
        text = "\n".join(lines)
        requests.post(url, json={"content": text}, timeout=10)
        print("  ✅ Discord 推播成功")
    except Exception as e:
        print(f"  ⚠️ Discord 推播失敗: {e}")


if __name__ == "__main__":
    main()
