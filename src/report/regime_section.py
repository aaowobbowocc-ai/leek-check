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
    "CRASH": "🚨 CRASH - 鑽石買點！分批 5 日進場 0050 + 30% 00631L (Period B/C 100% win, +9.75-12.31%)",
    "BEAR": "🟠 BEAR - hold 0050 不動，不加 00631L (decay)；可考慮 30-40% 現金等 CRASH 訊號",
    "SIDEWAYS": "🟡 SIDEWAYS - 0050 + Revenue YoY 衛星 25%（跨 3 期都 +0.24~1.95% 穩定區）",
    "BULL_TREND": "🟢 BULL_TREND - 0050 + 20-25% 00631L 吃趨勢（2020 後 +1.84~3.43% 穩定）",
    "STRONG_BULL": (
        "🔴 STRONG_BULL - 停 DCA + 累積現金。⚠️ 跨期不穩定: "
        "2020-22 +0.31% (延續) / 2023-25 -2.13% (mean reversion)。"
        "目前環境傾向後者，建議減倉 20%；激進派可 hold 不動但不加碼。"
    ),
}

EXPECTED_FWD = {
    "CRASH": "0050 +9.75% / 00631L +22.71% (100% win, n=34)",
    "BEAR": "0050 +0.45% / 00631L +1.62% (47-51% win)",
    "SIDEWAYS": "0050 +1.27% / 00631L +2.43% (66% win, 三期一致)",
    "BULL_TREND": "0050 +2.33% / 00631L +3.83% (64% win)",
    "STRONG_BULL": "0050 -0.62% (full) / 跨期 +0.31% vs -2.13% ⚠️ 不穩定",
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
        "_分類規則 V2 (mutually exclusive，2026-05-04 修正):_",
        "- CRASH: 60d ret < -15% AND vol30 > 25%",
        "- BEAR: dist MA200 < -5% AND ret_60d < 0",
        "- SIDEWAYS: |dist MA200| < 5%",
        "- BULL_TREND: dist MA200 在 0-20%",
        "- STRONG_BULL: dist MA200 > +20%（vol gate 移除）",
        "",
        "_實證 9 年 TAIEX (2017-2025) + Period A/B/C walk-forward：CRASH 跨期 100% win（鑽石買點）；STRONG_BULL 跨期不穩定（+0.31% vs -2.13%）_",
        "",
    ]
    return "\n".join(lines)
