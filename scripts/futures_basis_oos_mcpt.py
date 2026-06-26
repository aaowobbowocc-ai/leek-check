"""
期貨升水 (basis z > +2.0) OOS + MCPT 驗證

Hypothesis: TX 期貨 vs TAIEX 現貨 basis z > +2.0 → 20d alpha +2.36% (n=43, t=2.85)
但 n=43 太少，需 OOS 驗證才敢整合 INVEST

OOS：split 3 期 (2018-2020, 2021-2023, 2024-2026)
MCPT：1000 次 random shuffle
"""
from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = ROOT / "data" / "cache" / "finmind" / "extras"
HOLD_DAYS = 20
ENTRY_Z = 2.0
N_PERMUTE = 1000


def prepare_data():
    """合併期貨 + TAIEX，計算 basis_z + forward returns"""
    fut = pd.read_parquet(EXTRAS / "futures_daily.parquet")
    fut = fut[fut["futures_id"] == "TX"].copy()
    fut["date"] = pd.to_datetime(fut["date"])
    fut = fut.sort_values(["date", "contract_date"])
    fut_near = fut.groupby("date").first().reset_index()

    import yfinance as yf
    h = yf.Ticker("^TWII").history(period="3000d", auto_adjust=False)
    spot = pd.DataFrame({
        "date": pd.to_datetime(h.index).tz_localize(None),
        "spot_close": h["Close"].values,
    })

    df = fut_near.merge(spot, on="date", how="inner")
    df["basis"] = df["close"] - df["spot_close"]
    df["basis_pct"] = df["basis"] / df["spot_close"] * 100
    df["basis_ma"] = df["basis_pct"].rolling(60).mean()
    df["basis_std"] = df["basis_pct"].rolling(60).std()
    df["basis_z"] = (df["basis_pct"] - df["basis_ma"]) / df["basis_std"]
    df[f"fwd_{HOLD_DAYS}d"] = (df["spot_close"].shift(-HOLD_DAYS) / df["spot_close"] - 1) * 100
    df = df.dropna(subset=["basis_z", f"fwd_{HOLD_DAYS}d"]).copy()
    df["year"] = df["date"].dt.year
    return df


def event_study(df: pd.DataFrame, label: str) -> dict:
    fwd = f"fwd_{HOLD_DAYS}d"
    base_mean = df[fwd].mean()
    base_std = df[fwd].std()
    triggers = df[df["basis_z"] > ENTRY_Z]
    n = len(triggers)
    if n < 10: return {"label": label, "n": n, "alpha": None, "t": None}
    sig_mean = triggers[fwd].mean()
    alpha = sig_mean - base_mean
    t = alpha / (base_std / np.sqrt(n))
    win = (triggers[fwd] > 0).mean() * 100
    print(f"    {label}: n={n}, signal_mean={sig_mean:+.2f}%, baseline={base_mean:+.2f}%, "
          f"alpha={alpha:+.2f}%, win={win:.0f}%, t={t:+.2f}")
    return {"label": label, "n": n, "alpha": alpha, "t": t, "win": win}


def oos_split(df: pd.DataFrame):
    print("\n  📅 OOS 3-window split:")
    splits = [
        ("2018-2020", df[df["year"] <= 2020]),
        ("2021-2023", df[(df["year"] >= 2021) & (df["year"] <= 2023)]),
        ("2024-2026", df[df["year"] >= 2024]),
    ]
    for label, sub in splits:
        if len(sub) < 50: continue
        event_study(sub, label)


def mcpt(df: pd.DataFrame, n_permute: int = N_PERMUTE):
    print(f"\n  🎲 MCPT (n_permute={n_permute}):")
    fwd = f"fwd_{HOLD_DAYS}d"
    real_alpha = event_study(df, "Full")["alpha"]
    if real_alpha is None: return

    # Random shuffle: 隨機抽 n_signals 個 days，計算 alpha
    n_signals = (df["basis_z"] > ENTRY_Z).sum()
    rng = np.random.RandomState(42)
    fake_alphas = []
    n_total = len(df)
    base_mean = df[fwd].mean()
    fwd_array = df[fwd].values

    for _ in range(n_permute):
        idx = rng.choice(n_total, size=n_signals, replace=False)
        fake_signals = fwd_array[idx]
        fake_alpha = fake_signals.mean() - base_mean
        fake_alphas.append(fake_alpha)

    fake_alphas = np.array(fake_alphas)
    p_value = (fake_alphas >= real_alpha).sum() / n_permute
    print(f"    Real alpha: {real_alpha:+.3f}%")
    print(f"    Random mean: {fake_alphas.mean():+.3f}%, std: {fake_alphas.std():.3f}")
    print(f"    p-value: {p_value:.4f} {'✅ < 0.05' if p_value < 0.05 else '❌'}")
    return p_value


def main():
    print("=" * 80)
    print(f"  期貨升水 (basis z > +{ENTRY_Z}) OOS + MCPT 驗證")
    print(f"  Hold: {HOLD_DAYS}d / N permute: {N_PERMUTE}")
    print("=" * 80)

    df = prepare_data()
    print(f"\n  資料: {df['date'].min().date()} ~ {df['date'].max().date()}, n={len(df)}")
    print(f"  basis z 觸發 z>+{ENTRY_Z} 的天數: {(df['basis_z'] > ENTRY_Z).sum()}")

    print("\n  ▶ Full sample:")
    full = event_study(df, "Full")

    oos_split(df)
    p = mcpt(df)

    print("\n" + "=" * 80)
    print("  🎯 結論")
    print("=" * 80)
    if full and full.get("alpha") and abs(full["t"]) > 2 and (p or 1) < 0.05:
        print("  ✅ Full sample 顯著且 MCPT 通過")
        print("  → 但需 3 期 OOS 都 robust 才能整合 INVEST")
    else:
        print("  ⚠️ 需更謹慎評估")


if __name__ == "__main__":
    main()
