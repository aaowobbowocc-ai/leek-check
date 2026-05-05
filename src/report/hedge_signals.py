"""
Hedge Signals — Crash Hedge Layer (overlays barbell allocation)

Four hedge signals from independent data sources:

1. Foreign TX OI z-score (memory: project_foreign_tx_oi_alpha.md)
   - Source: futures_institutional (FinMind, TX 台指期)
   - Trigger: 外資台指期 net OI z-score < -2.0 (rolling 252d)
   - Empirical: 10d TAIEX alpha +1.43% (t=4.09, n=123, OOS 3/3 robust)
   - Use: regime-independent crash hedge; reduces position when risk-off

2. VIX threshold (existing in concentration_advisor.py)
   - Trigger: VIX > 30
   - Use: market panic indicator

3. VIX/VIX3M ratio (added 2026-05-04 post 3-AI critique)
   - Source: ^VIX 與 ^VIX3M (yfinance)
   - Trigger: ratio > 1.05 (term structure flattening = elevated risk)
   - 重要: 3-AI 一致警告「moderate vs deep backwardation 是 hindsight bias」
           因此 used as RISK INDICATOR (cash tilt), NOT as entry signal
   - 實證: ratio > 1.05 是 panic 接近 / risk-off; >1.10 = 已在 crash 中
   - Use: 增加 cash tilt 5-10pp，避免在 panic 中段加碼

4. TX basis vs TWII spot (added 2026-05-05 post strategy exploration)
   - Source: futures_daily (TX 期貨 close) vs ^TWII (現貨 close)
   - basis = TX_close - TWII_close (點數)
   - basis_z_60 = 60 日 rolling z-score
   - Backtest: deep premium (z > +2) fwd 20d +3.38%, deep discount (z < -2) fwd 20d +2.28%
   - 但 OOS 1/3 期 robust，跨期翻轉 → INFORMATIONAL only
   - Use: 顯示當前 basis 結構作 awareness，極端值不疊加 cash tilt（避免 over-fit）

Output: hedge_active flag + recommended cash tilt (+0% normal / +5-25% on hedge)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TX_INST_PATH = ROOT / "data" / "cache" / "finmind" / "extras" / "futures_institutional.parquet"
TX_DAILY_PATH = ROOT / "data" / "cache" / "finmind" / "extras" / "futures_daily.parquet"
TWII_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "^TWII.parquet"
VIX_PATH = ROOT / "data" / "cache" / "yfinance" / "global" / "VIX_full.parquet"
VIX3M_PATH = ROOT / "data" / "cache" / "yfinance" / "global" / "VIX3M.parquet"


@dataclass
class HedgeReading:
    foreign_tx_z: float          # Z-score of foreign net OI on TX
    foreign_tx_signal: bool      # True if z < -2.0
    vix_current: float           # Latest VIX close
    vix_signal: bool             # True if VIX > 30
    vix_ratio: float             # VIX / VIX3M (term structure)
    vix_ratio_signal: bool       # True if ratio > 1.05
    tx_basis_pts: float          # TX - TWII basis (點數)
    tx_basis_z: float            # 60d z-score of basis
    tx_basis_extreme: bool       # |z| > 2.0 (informational, not actioned)
    cash_tilt_pp: int            # Recommended cash tilt over baseline (+0 / +5-25)
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
    # Try VIX_full first (added 2026-05-04), fallback to old VIX.parquet
    candidates = [VIX_PATH, ROOT / "data" / "cache" / "yfinance" / "global" / "VIX.parquet"]
    for path in candidates:
        if path.exists():
            try:
                df = pd.read_parquet(path)
                if "close" in df.columns and len(df) > 0:
                    return float(df["close"].iloc[-1])
            except Exception:
                continue
    return float("nan")


def get_tx_basis() -> tuple[float, float]:
    """Compute current TX basis (期貨 - 現貨) and 60d z-score.

    Returns: (basis_pts, basis_z_60d) — informational only, not actioned.
    """
    if not (TX_DAILY_PATH.exists() and TWII_PATH.exists()):
        return float("nan"), float("nan")
    try:
        fut = pd.read_parquet(TX_DAILY_PATH)
        fut = fut[fut["futures_id"] == "TX"]
        if fut.empty:
            return float("nan"), float("nan")
        fut["date"] = pd.to_datetime(fut["date"])
        # 取近月（每日 contract_date 最早）
        if "contract_date" in fut.columns:
            fut = fut.sort_values(["date", "contract_date"]).groupby("date").first().reset_index()

        twii = pd.read_parquet(TWII_PATH)
        twii["date"] = pd.to_datetime(twii["date"])
        twii = twii[["date", "close"]].rename(columns={"close": "twii_close"})

        df = fut[["date", "close"]].rename(columns={"close": "tx_close"}).merge(
            twii, on="date"
        ).sort_values("date").reset_index(drop=True)
        if df.empty or len(df) < 60:
            return float("nan"), float("nan")
        df["basis"] = df["tx_close"] - df["twii_close"]
        df["basis_z"] = (
            (df["basis"] - df["basis"].rolling(60).mean())
            / df["basis"].rolling(60).std()
        )
        last = df.iloc[-1]
        return float(last["basis"]), float(last["basis_z"])
    except Exception:
        return float("nan"), float("nan")


def get_vix_ratio() -> float:
    """Compute current VIX / VIX3M ratio (term structure proxy).

    > 1.0 = backwardation (panic), > 1.05 = elevated risk (defensive)
    < 1.0 = contango (calm)
    """
    if not (VIX_PATH.exists() and VIX3M_PATH.exists()):
        return float("nan")
    try:
        v = pd.read_parquet(VIX_PATH)
        v3 = pd.read_parquet(VIX3M_PATH)
        # Match latest dates
        v["date"] = pd.to_datetime(v["date"])
        v3["date"] = pd.to_datetime(v3["date"])
        merged = v[["date", "close"]].merge(v3[["date", "close"]], on="date", suffixes=("_vix", "_3m"))
        if merged.empty:
            return float("nan")
        last = merged.sort_values("date").iloc[-1]
        return float(last["close_vix"] / last["close_3m"])
    except Exception:
        return float("nan")


def compute_hedge_reading() -> HedgeReading:
    z, _ = compute_foreign_tx_oi_z()
    vix = get_current_vix()
    vix_ratio = get_vix_ratio()
    tx_basis, tx_basis_z = get_tx_basis()
    notes = []

    foreign_signal = (not np.isnan(z)) and z < -2.0
    vix_signal = (not np.isnan(vix)) and vix > 30
    # VIX/VIX3M ratio > 1.05 = term structure flattening = elevated risk
    # 3-AI 共識 (2026-05-04): 用作 risk indicator (cash tilt)，不作 entry signal
    vix_ratio_signal = (not np.isnan(vix_ratio)) and vix_ratio > 1.05
    # TX basis |z| > 2 = extreme structure (informational only, OOS 1/3 robust)
    tx_basis_extreme = (not np.isnan(tx_basis_z)) and abs(tx_basis_z) > 2

    cash_tilt = 0
    # Stack tilts (each signal adds independently)
    if foreign_signal:
        cash_tilt += 10
        notes.append(f"⚠️ Foreign TX OI z={z:.2f} < -2.0 (10d alpha +1.43%) → +10pp 現金")
    if vix_signal:
        cash_tilt += 10
        notes.append(f"⚠️ VIX {vix:.1f} > 30（panic）→ +10pp 現金")
    if vix_ratio_signal:
        # Tighter tilt for ratio bucket
        if vix_ratio > 1.10:
            cash_tilt += 10
            notes.append(f"🚨 VIX/VIX3M = {vix_ratio:.3f} > 1.10 (deep backwardation, crash 中段) → +10pp 現金")
        else:
            cash_tilt += 5
            notes.append(f"⚠️ VIX/VIX3M = {vix_ratio:.3f} > 1.05 (term structure 警戒) → +5pp 現金")

    # TX basis: informational only (不影響 cash_tilt)
    if tx_basis_extreme:
        direction = "premium" if tx_basis_z > 0 else "discount"
        notes.append(f"ℹ️ TX 基差 {tx_basis:+.0f}pts (z={tx_basis_z:+.2f}) extreme {direction} — informational only")

    if cash_tilt == 0:
        if not np.isnan(z):
            notes.append(f"✅ Foreign TX OI z={z:+.2f}（正常區間）")
        if not np.isnan(vix):
            notes.append(f"✅ VIX {vix:.1f}（正常區間）")
        if not np.isnan(vix_ratio):
            notes.append(f"✅ VIX/VIX3M = {vix_ratio:.3f}（contango，正常）")
        if not np.isnan(tx_basis):
            notes.append(f"✅ TX 基差 {tx_basis:+.0f}pts (z={tx_basis_z:+.2f}, 正常結構)")
    elif foreign_signal and vix_signal:
        notes.insert(0, f"🚨 多重 hedge 觸發 (Foreign TX + VIX{'+ ratio' if vix_ratio_signal else ''}) → 總 cash tilt +{cash_tilt}pp")

    # Cap at 25pp to avoid over-defensive
    cash_tilt = min(cash_tilt, 25)

    return HedgeReading(
        foreign_tx_z=z if not np.isnan(z) else 0.0,
        foreign_tx_signal=foreign_signal,
        vix_current=vix if not np.isnan(vix) else 0.0,
        vix_signal=vix_signal,
        vix_ratio=vix_ratio if not np.isnan(vix_ratio) else 0.0,
        vix_ratio_signal=vix_ratio_signal,
        tx_basis_pts=tx_basis if not np.isnan(tx_basis) else 0.0,
        tx_basis_z=tx_basis_z if not np.isnan(tx_basis_z) else 0.0,
        tx_basis_extreme=tx_basis_extreme,
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
        f"| VIX/VIX3M ratio | **{r.vix_ratio:.3f}** | > 1.05 | "
        f"{'🚨 YES' if r.vix_ratio_signal else '✅ no'} |",
        f"| TX basis (60d z) | **{r.tx_basis_pts:+.0f}pts (z={r.tx_basis_z:+.2f})** | \\|z\\|>2 | "
        f"{'ℹ️ extreme' if r.tx_basis_extreme else '✅ normal'} |",
        "",
        f"**Cash tilt 建議**: {'+' + str(r.cash_tilt_pp) if r.cash_tilt_pp > 0 else '0'}pp 超出 barbell baseline",
        "",
    ]
    for note in r.notes:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("_TX OI 實證: 10d TAIEX alpha +1.43% (t=4.09, OOS 3/3)。VIX > 30 為 panic 指標。_")
    lines.append("_VIX/VIX3M 為 risk indicator (3-AI 共識 2026-05-04 建議不作 entry signal — hindsight bias)。_")
    lines.append("_TX 基差 informational only — full +3.38% deep premium 但 OOS 1/3 期 robust，僅顯示結構不疊加 tilt。_")
    lines.append("")
    return "\n".join(lines)
