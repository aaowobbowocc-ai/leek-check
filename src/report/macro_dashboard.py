"""
全球宏觀儀表板 + ETF 溢價警報（Phase 17c）。

用既有的 yfinance 快取資料計算「跨市場風險指標」，幫使用者在每日晨報
看清「TW 是否被全球同質化拖下水」+「TW 投信版海外 ETF 該不該買」。

三大功能：
  1. **TAIEX vs S&P 500 滾動相關性** — 60 日 rolling
     - > 0.85: 紅燈，全球同質化嚴重，分散失效
     - 0.70 - 0.85: 黃燈，正常牛市相關性
     - < 0.70: 綠燈，真分散有效

  2. **VIX 恐慌指數** — 美股波動代理（風險偏好）
     - > 30: 恐慌，買入機會
     - 20 - 30: 警戒
     - < 20: 平穩
     - < 12: 過度樂觀（反轉風險）

  3. **TW 投信版海外 ETF 折溢價估算**
     - 00646 (S&P 500): 比對 SPY × USD/TWD 隱含 NAV
     - 00643 (印度): 比對 INDA × USD/TWD
     - 00885 (越南): 比對 VNM × USD/TWD
     - 折溢價 > 2% 警告

純函式設計：吃 yfinance 快取 DataFrame，不打 API。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Premium 對照表：TW ETF → 美股對應 ticker（用於估算 NAV）
ETF_PREMIUM_REFERENCES: dict[str, dict[str, str]] = {
    "00646": {"name": "元大標普 500", "ref": "SPY", "ref_name": "美股 S&P 500"},
    "00643": {"name": "FH 印度", "ref": "INDA", "ref_name": "iShares 印度"},
    "00885": {"name": "富邦越南", "ref": "VNM", "ref_name": "VanEck 越南"},
    "00662": {"name": "富邦 NASDAQ", "ref": "QQQ", "ref_name": "美股 NASDAQ-100"},
    "00684R": {"name": "期日經反 1", "ref": "EWJ", "ref_name": "iShares 日本"},
    "00657": {"name": "國泰美中台", "ref": "VOO", "ref_name": "美股大盤"},
}

# 全球建議配置 — 含日本價值（DXJ 10 年贏 EWJ 240pp，真正能 alpha 的市場）
GLOBAL_ALLOCATION_TARGETS = {
    "🇹🇼 TW 留存": {"target_pct": 28, "tickers": ["0050", "00878"]},
    "🇺🇸 美股大盤": {"target_pct": 18, "tickers": ["VOO", "00646"]},
    "🇯🇵 日本價值": {"target_pct": 8, "tickers": ["DXJ"]},  # 新增
    "🇮🇳 印度": {"target_pct": 8, "tickers": ["INDA", "00643"]},
    "🇻🇳 越南": {"target_pct": 4, "tickers": ["VNM", "00885"]},
    "🛡️ 黃金": {"target_pct": 5, "tickers": ["GLD", "00635U"]},
    "🎰 樂透小注": {"target_pct": 3, "tickers": []},  # 妖股雷達 + 早期 hunter
    "💰 現金 buffer": {"target_pct": 26, "tickers": []},
}


# ─────────────────────────────────────────
# 1. TAIEX vs S&P 500 相關性
# ─────────────────────────────────────────
@dataclass(frozen=True)
class CorrelationStatus:
    correlation: float
    level: str        # "diversified" | "normal" | "concentrated"
    description: str


def compute_taiex_sp500_correlation(
    taiex_ohlcv: pd.DataFrame,
    sp500_ohlcv: pd.DataFrame,
    window_days: int = 60,
) -> CorrelationStatus | None:
    """
    計算最近 N 個交易日 TAIEX 與 S&P 500 的日報酬率相關性。
    兩者需都有資料；用 inner join 對齊日期。
    """
    if taiex_ohlcv is None or sp500_ohlcv is None:
        return None
    if taiex_ohlcv.empty or sp500_ohlcv.empty:
        return None

    tw = taiex_ohlcv[["date", "close"]].copy()
    us = sp500_ohlcv[["date", "close"]].copy()
    tw["date"] = pd.to_datetime(tw["date"]).dt.date
    us["date"] = pd.to_datetime(us["date"]).dt.date

    merged = pd.merge(tw, us, on="date", suffixes=("_tw", "_us")).sort_values("date")
    if len(merged) < window_days + 2:
        return None

    merged["tw_ret"] = merged["close_tw"].pct_change()
    merged["us_ret"] = merged["close_us"].pct_change()
    recent = merged.dropna().tail(window_days)
    if len(recent) < window_days // 2:
        return None

    corr = float(recent["tw_ret"].corr(recent["us_ret"]))

    if corr > 0.85:
        level = "concentrated"
        desc = f"🔴 TAIEX/SP500 {window_days}D 相關性 {corr:.2f} > 0.85 — 全球同質化嚴重，海外配置防禦失效"
    elif corr > 0.70:
        level = "normal"
        desc = f"🟡 TAIEX/SP500 {window_days}D 相關性 {corr:.2f} — 正常牛市相關"
    else:
        level = "diversified"
        desc = f"🟢 TAIEX/SP500 {window_days}D 相關性 {corr:.2f} — 真分散有效"

    return CorrelationStatus(correlation=corr, level=level, description=desc)


# ─────────────────────────────────────────
# 2. VIX 恐慌指數
# ─────────────────────────────────────────
@dataclass(frozen=True)
class VIXStatus:
    value: float
    level: str        # "panic" | "alert" | "calm" | "complacent"
    description: str


def vix_status(vix_value: float) -> VIXStatus:
    if vix_value >= 30:
        return VIXStatus(vix_value, "panic", f"⚫ VIX {vix_value:.1f} — 恐慌期，可分批加碼買進機會")
    if vix_value >= 20:
        return VIXStatus(vix_value, "alert", f"🟠 VIX {vix_value:.1f} — 警戒，留意大盤波動")
    if vix_value >= 12:
        return VIXStatus(vix_value, "calm", f"🟢 VIX {vix_value:.1f} — 平穩")
    return VIXStatus(vix_value, "complacent", f"🟡 VIX {vix_value:.1f} — 過度樂觀，反轉風險升高")


# ─────────────────────────────────────────
# 3. ETF 折溢價估算
# ─────────────────────────────────────────
@dataclass(frozen=True)
class ETFPremiumCheck:
    tw_ticker: str
    tw_name: str
    tw_price: float
    ref_ticker: str
    ref_name: str
    ref_price: float
    usd_twd: float
    estimated_premium_pct: float
    level: str        # "ok" | "warn" | "danger"
    suggestion: str


def estimate_etf_premium(
    tw_ticker: str,
    tw_ohlcv: pd.DataFrame,
    ref_ohlcv: pd.DataFrame,
    usd_twd_rate: float,
    baseline_ratio: float | None = None,
) -> ETFPremiumCheck | None:
    """
    粗估折溢價：(TW ETF 收盤 / 美股對應 × 匯率) 的歷史比例 vs 當前比例。

    baseline_ratio: 若提供，用此為「正常」比例；否則用 60 日歷史中位數估計。
    這個方法是近似值，正式 NAV 仍需從投信公司或集保中心抓。
    """
    info = ETF_PREMIUM_REFERENCES.get(tw_ticker)
    if info is None:
        return None
    if tw_ohlcv is None or tw_ohlcv.empty or ref_ohlcv is None or ref_ohlcv.empty:
        return None
    if usd_twd_rate <= 0:
        return None

    tw_close = float(tw_ohlcv.sort_values("date").iloc[-1]["close"])
    ref_close = float(ref_ohlcv.sort_values("date").iloc[-1]["close"])
    if ref_close <= 0:
        return None

    # 當前比例
    current_ratio = tw_close / (ref_close * usd_twd_rate)

    # 歷史比例：用過去 60 日中位數作為「合理」基準
    if baseline_ratio is None:
        tw = tw_ohlcv[["date", "close"]].copy()
        rf = ref_ohlcv[["date", "close"]].copy()
        tw["date"] = pd.to_datetime(tw["date"]).dt.date
        rf["date"] = pd.to_datetime(rf["date"]).dt.date
        merged = pd.merge(tw, rf, on="date", suffixes=("_tw", "_ref"))
        if len(merged) < 30:
            return None
        recent = merged.sort_values("date").tail(60)
        ratios = recent["close_tw"] / (recent["close_ref"] * usd_twd_rate)
        baseline_ratio = float(ratios.median())

    premium_pct = (current_ratio / baseline_ratio - 1.0) * 100.0

    if abs(premium_pct) > 3:
        level = "danger"
        suggestion = f"⚠️ {info['name']} 偏離 {premium_pct:+.1f}% — 強烈建議延後或改 IB 直接買 {info['ref']}"
    elif abs(premium_pct) > 1.5:
        level = "warn"
        suggestion = f"🟡 {info['name']} 偏離 {premium_pct:+.1f}% — 留意，等回正再買"
    else:
        level = "ok"
        suggestion = f"🟢 {info['name']} 偏離 {premium_pct:+.1f}% — 正常範圍"

    return ETFPremiumCheck(
        tw_ticker=tw_ticker, tw_name=info["name"], tw_price=tw_close,
        ref_ticker=info["ref"], ref_name=info["ref_name"], ref_price=ref_close,
        usd_twd=usd_twd_rate,
        estimated_premium_pct=round(premium_pct, 2),
        level=level, suggestion=suggestion,
    )


# ─────────────────────────────────────────
# 渲染 Markdown
# ─────────────────────────────────────────
def render_macro_section(
    correlation: CorrelationStatus | None,
    vix: VIXStatus | None,
    etf_premiums: list[ETFPremiumCheck],
) -> str:
    lines = ["## 🌍 全球宏觀儀表板"]

    # 相關性
    lines.append("\n### TAIEX vs 全球")
    if correlation is not None:
        lines.append(f"- {correlation.description}")
    else:
        lines.append("- 相關性資料不足（需 60 日重疊資料）")

    # VIX
    if vix is not None:
        lines.append(f"- {vix.description}")

    # ETF 溢價
    lines.append("\n### TW 投信版海外 ETF 折溢價")
    if etf_premiums:
        for p in etf_premiums:
            lines.append(f"- {p.suggestion}")
    else:
        lines.append("- 無資料")

    return "\n".join(lines) + "\n"
