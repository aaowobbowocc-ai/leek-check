"""
Market Regime Section for Morning Briefing.

5-regime classification based on TAIEX (^TWII):
  CRASH        : 60d ret < -15% OR vol30 > 30%
  BEAR         : TAIEX < MA200 - 5%, NOT crash
  SIDEWAYS     : |TAIEX vs MA200| < 5%
  BULL_TREND   : TAIEX > MA200, dist <= +20%
  STRONG_BULL  : dist MA200 > +20% AND vol30 < 18%

Each regime maps to a recommended strategy. Empirical evidence from 9-year
TAIEX backtest (2017-2025): see logs/regime_strategy_mapping.csv.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TWII_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "^TWII.parquet"


@dataclass
class RegimeReading:
    regime: str
    label: str
    dist_ma200: float
    vol_30d: float
    ret_60d: float
    recommendation: str
    expected_fwd_20d: str


REGIME_RULES = {
    "CRASH": "🚨 CRASH - 全力部署現金，等 VIX 高點 30% 回落 → 加倍買 0050（89% win, fwd 20d +6%）",
    "BEAR": "🟠 BEAR - hold 0050 不動，不加 00631L（daily reset decay）",
    "SIDEWAYS": "🟡 SIDEWAYS - 0050 + Revenue YoY 衛星 20-30%（廣度因子適用區）",
    "BULL_TREND": "🟢 BULL_TREND - 0050 加 20-30% 00631L 吃趨勢 (fwd 20d +2.32%)",
    "STRONG_BULL": "🔴 STRONG_BULL - 減倉 20-30%，停 DCA，累積現金（fwd 20d -1.21% 是賣點）",
}

EXPECTED_FWD = {
    "CRASH": "0050 +6.22% / 00631L +13.06% (89% win)",
    "BEAR": "0050 +0.15% / 00631L +0.98% (44-48% win)",
    "SIDEWAYS": "0050 +0.99% / 00631L +1.91% (65% win)",
    "BULL_TREND": "0050 +2.32% / 00631L +3.96% (64% win)",
    "STRONG_BULL": "0050 -1.21% / 00631L -3.14% (32-38% win) ⚠️",
}


def classify(dist_ma200: float, vol_30d: float, ret_60d: float) -> str:
    """5-regime classifier (mutually exclusive).

    Order matters:
    1. CRASH first: ret_60d < -15% AND vol30 > 25% (both required, prevents
       post-crash V-shaped recovery from being mis-classified)
    2. BEAR: significantly below MA200 with weak momentum
    3. STRONG_BULL: well above MA200 (vol gate relaxed — post-crash bulls qualify)
    4. SIDEWAYS: near MA200
    5. BULL_TREND: above MA200 but not euphoric
    """
    if pd.isna(dist_ma200) or pd.isna(vol_30d) or pd.isna(ret_60d):
        return "UNKNOWN"
    if ret_60d < -15 and vol_30d > 25:
        return "CRASH"
    if dist_ma200 < -5 and ret_60d < 0:
        return "BEAR"
    if dist_ma200 > 20:
        return "STRONG_BULL"
    if abs(dist_ma200) < 5:
        return "SIDEWAYS"
    if dist_ma200 > 0:
        return "BULL_TREND"
    return "SIDEWAYS"


def compute_current_regime() -> RegimeReading | None:
    if not TWII_PATH.exists():
        return None
    try:
        df = pd.read_parquet(TWII_PATH)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        if len(df) < 220:
            return None
        df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
        df["ret_60d"] = df["close"].pct_change(60) * 100
        df["ma200"] = df["close"].rolling(200).mean()
        df["dist_ma200"] = (df["close"] / df["ma200"] - 1) * 100
        df["vol_30d"] = df["log_ret"].rolling(30).std() * np.sqrt(252) * 100

        last = df.iloc[-1]
        regime = classify(last["dist_ma200"], last["vol_30d"], last["ret_60d"])
        return RegimeReading(
            regime=regime,
            label=regime,
            dist_ma200=float(last["dist_ma200"]),
            vol_30d=float(last["vol_30d"]),
            ret_60d=float(last["ret_60d"]),
            recommendation=REGIME_RULES.get(regime, "—"),
            expected_fwd_20d=EXPECTED_FWD.get(regime, "—"),
        )
    except Exception:
        return None


def render_regime_section() -> str:
    reading = compute_current_regime()
    if reading is None:
        return ""

    lines = [
        "## 🎯 市場 Regime（每日策略導向）",
        "",
        f"**當前 regime: `{reading.regime}`**",
        "",
        f"- TAIEX 距 MA200: **{reading.dist_ma200:+.1f}%**",
        f"- 30d 年化波動: **{reading.vol_30d:.1f}%**",
        f"- 60d 報酬: **{reading.ret_60d:+.1f}%**",
        "",
        f"**推薦動作**：{reading.recommendation}",
        "",
        f"_歷史 fwd 20d: {reading.expected_fwd_20d}_",
        "",
        "_分類規則 (5-regime mutually exclusive):_",
        "- CRASH: 60d ret < -15% OR vol30 > 30%",
        "- BEAR: dist MA200 < -5%（非 crash）",
        "- SIDEWAYS: |dist MA200| < 5%",
        "- BULL_TREND: dist MA200 在 0-20%",
        "- STRONG_BULL: dist MA200 > +20% AND vol30 < 18%",
        "",
        "_實證 9 年 TAIEX (2017-2025), STRONG_BULL fwd 20d -1.21% 是 mean reversion 賣點_",
        "",
    ]
    return "\n".join(lines)
