"""
Hedge Signals — Crash Hedge Layer (overlays barbell allocation)

Two hedge signals from independent data sources:

1. Foreign TX OI z-score (memory: project_foreign_tx_oi_alpha.md)
   - Source: futures_institutional (FinMind, TX 台指期)
   - Trigger: 外資台指期 net OI z-score < -2.0 (rolling 252d)
   - Empirical: 10d TAIEX alpha +1.43% (t=4.09, n=123, OOS 3/3 robust)
   - Use: regime-independent crash hedge; reduces position when risk-off

2. VIX threshold (existing in concentration_advisor.py)
   - Trigger: VIX > 30
   - Use: market panic indicator

Output: hedge_active flag + recommended cash tilt (+0% normal / +10-20% on hedge)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TX_INST_PATH = ROOT / "data" / "cache" / "finmind" / "extras" / "futures_institutional.parquet"
VIX_PATH = ROOT / "data" / "cache" / "yfinance" / "global" / "VIX.parquet"


@dataclass
class HedgeReading:
    foreign_tx_z: float          # Z-score of foreign net OI on TX
    foreign_tx_signal: bool      # True if z < -2.0
    vix_current: float           # Latest VIX close
    vix_signal: bool             # True if VIX > 30
    cash_tilt_pp: int            # Recommended cash tilt over baseline (+0 / +10 / +20)
    notes: list[str]             # Human-readable explanations


def compute_foreign_tx_oi_z(window: int = 252) -> tuple[float, dict]:
    """Compute current Foreign Investor net OI z-score on TX."""
    if not TX_INST_PATH.exists():
        return float("nan"), {}
    try:
        df = pd.read_parquet(TX_INST_PATH)
    except Exception:
        return float("nan"), {}
    df = df[df["futures_id"] == "TX"]
    df = df[df["institutional_investors"] == "外資"]
    if df.empty:
        return float("nan"), {}
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    # Net OI = long balance - short balance
    df["net_oi"] = (
        df["long_open_interest_balance_volume"]
        - df["short_open_interest_balance_volume"]
    )
    if len(df) < window + 1:
        return float("nan"), {}
    rolling = df["net_oi"].rolling(window)
    df["mean"] = rolling.mean()
    df["std"] = rolling.std()
    df["z"] = (df["net_oi"] - df["mean"]) / df["std"]
    last = df.iloc[-1]
    return float(last["z"]), {
        "date": last["date"].date().isoformat(),
        "net_oi": int(last["net_oi"]),
        "mean": int(last["mean"]) if pd.notna(last["mean"]) else 0,
        "std": int(last["std"]) if pd.notna(last["std"]) else 0,
    }


def get_current_vix() -> float:
    if not VIX_PATH.exists():
        return float("nan")
    try:
        df = pd.read_parquet(VIX_PATH)
        return float(df["close"].iloc[-1])
    except Exception:
        return float("nan")


def compute_hedge_reading() -> HedgeReading:
    z, _ = compute_foreign_tx_oi_z()
    vix = get_current_vix()
    notes = []

    foreign_signal = (not np.isnan(z)) and z < -2.0
    vix_signal = (not np.isnan(vix)) and vix > 30

    cash_tilt = 0
    if foreign_signal and vix_signal:
        cash_tilt = 20
        notes.append(f"🚨 雙重 hedge: Foreign TX OI z={z:.2f} AND VIX={vix:.1f} → +20pp 現金")
    elif foreign_signal:
        cash_tilt = 10
        notes.append(f"⚠️ Foreign TX OI z={z:.2f} < -2.0 (10d alpha +1.43%) → +10pp 現金")
    elif vix_signal:
        cash_tilt = 10
        notes.append(f"⚠️ VIX {vix:.1f} > 30（panic）→ +10pp 現金")
    else:
        if not np.isnan(z):
            notes.append(f"✅ Foreign TX OI z={z:+.2f}（正常區間）")
        if not np.isnan(vix):
            notes.append(f"✅ VIX {vix:.1f}（正常區間）")

    return HedgeReading(
        foreign_tx_z=z if not np.isnan(z) else 0.0,
        foreign_tx_signal=foreign_signal,
        vix_current=vix if not np.isnan(vix) else 0.0,
        vix_signal=vix_signal,
        cash_tilt_pp=cash_tilt,
        notes=notes,
    )


def render_hedge_section() -> str:
    r = compute_hedge_reading()
    lines = [
        "## 🛡️ Hedge Signals（regime-independent crash overlay）",
        "",
        "| Signal | Value | Threshold | Active |",
        "|--------|-------|-----------|--------|",
        f"| Foreign TX OI z (252d) | **{r.foreign_tx_z:+.2f}** | < -2.0 | "
        f"{'🚨 YES' if r.foreign_tx_signal else '✅ no'} |",
        f"| VIX | **{r.vix_current:.1f}** | > 30 | "
        f"{'🚨 YES' if r.vix_signal else '✅ no'} |",
        "",
        f"**Cash tilt 建議**: {'+' + str(r.cash_tilt_pp) if r.cash_tilt_pp > 0 else '0'}pp 超出 barbell baseline",
        "",
    ]
    for note in r.notes:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("_TX OI 訊號實證 (memory): 10d TAIEX alpha +1.43% (t=4.09, n=123, OOS 3/3)。"
                 "VIX 為 panic 指標。兩訊號獨立驗證、可疊加。_")
    lines.append("")
    return "\n".join(lines)
