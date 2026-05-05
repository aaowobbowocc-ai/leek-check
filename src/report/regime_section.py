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
    "CRASH": "🚨 市場崩盤中（鑽石買點！）— 立刻分批買進 0050；歷史 100% 機率 20 天內反彈 +10%",
    "BEAR": "🟠 市場下跌中 — 持有 0050 不動，不要加碼槓桿 ETF；保留 30-40% 現金等真正大跌",
    "SIDEWAYS": "🟡 市場盤整 — 持有 0050 + 25% Revenue YoY 個股組合（這是少數跨期穩賺的策略）",
    "BULL_TREND": "🟢 健康牛市 — 持有 0050 + 可加 20-25% 槓桿 ETF（00631L）吃趨勢",
    "STRONG_BULL": (
        "🔴 市場過熱 — 暫停定期買進，把現金存起來等下一次大跌。"
        "⚠️ 注意：歷史顯示這種狀態有時會繼續漲（2020-22）有時會回檔（2023-25），"
        "目前環境較像 2023-25。保守派可減倉 20%，積極派至少不要再加碼。"
    ),
}

EXPECTED_FWD = {
    "CRASH": "歷史 20 天後: 0050 +9.75% / 00631L +22.71%（100% 都漲）",
    "BEAR": "歷史 20 天後: 0050 +0.45% / 00631L +1.62%（勝率 47-51%）",
    "SIDEWAYS": "歷史 20 天後: 0050 +1.27% / 00631L +2.43%（勝率 66%，三期都穩）",
    "BULL_TREND": "歷史 20 天後: 0050 +2.33% / 00631L +3.83%（勝率 64%）",
    "STRONG_BULL": "歷史 20 天後不穩定: 過去有時 +0.31% 有時 -2.13% ⚠️",
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

    regime_chinese = {
        "CRASH": "市場崩盤中",
        "BEAR": "市場下跌中",
        "SIDEWAYS": "市場盤整",
        "BULL_TREND": "健康牛市",
        "STRONG_BULL": "市場過熱",
    }.get(reading.regime, reading.regime)

    lines = [
        "## 🎯 今天市場狀態",
        "",
        f"**目前狀態: `{reading.regime}`（{regime_chinese}）**",
        "",
        f"- 大盤離 200 日均線: **{reading.dist_ma200:+.1f}%**（>+20% 算過熱）",
        f"- 最近 30 天波動度: **{reading.vol_30d:.1f}%**（一般 15-20%）",
        f"- 最近 60 天漲跌: **{reading.ret_60d:+.1f}%**",
        "",
        f"**👉 該怎麼做**：{reading.recommendation}",
        "",
        f"_{reading.expected_fwd_20d}_",
        "",
        "<details><summary>📖 5 種市場狀態怎麼分（點開）</summary>",
        "",
        "- **市場崩盤中 (CRASH)**: 60 天跌超過 15% 且波動度高 → 鑽石買點",
        "- **市場下跌中 (BEAR)**: 跌破 200 日均線 → 防禦",
        "- **市場盤整 (SIDEWAYS)**: 在 200 日均線附近 5% 內 → 個股策略適用",
        "- **健康牛市 (BULL_TREND)**: 站上 200 日均線 0-20% → 持續持有",
        "- **市場過熱 (STRONG_BULL)**: 大盤超過 200 日均線 20% → 警戒",
        "",
        "</details>",
        "",
        "_資料來源: 9 年台股實證 (2017-2025)。CRASH 是跨期 100% 勝率的買點；STRONG_BULL 跨期表現不穩定。_",
        "",
    ]
    return "\n".join(lines)
