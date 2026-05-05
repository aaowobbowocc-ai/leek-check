"""
TX 期貨基差 (premium/discount) → TWII 擇時 Backtest

假設:
  期貨價 = 現貨指數 + 基差
  正基差 (期貨 > 現貨): 市場樂觀，套利者期貨溢價
  逆基差 (期貨 < 現貨): 市場悲觀，套利者期貨折價

  極端基差 (deep discount/premium) → mean reversion 訊號

  特別: deep discount (基差 < -X 點) 通常出現在恐慌底
        deep premium (基差 > +X 點) 通常 euphoria 頂

訊號:
  basis = TX_close - TWII_close
  basis_z = rolling 60 day z-score

  Test:
    basis_z < -2 → buy TWII fwd 5/20 day
    basis_z > +2 → defensive

Cost: 0 (TWII timing 用 0050 替代執行 cost ~0.78%)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
FUTURES_PATH = ROOT / "data" / "cache" / "finmind" / "extras" / "futures_daily.parquet"
TWII_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "^TWII.parquet"


def main():
    print("=" * 80)
    print("  TX 期貨基差 → TWII 擇時 Backtest")
    print("=" * 80)

    # Load TX futures (近月)
    fut = pd.read_parquet(FUTURES_PATH)
    print(f"  Futures cols: {list(fut.columns)}")
    print(f"  Contract types: {fut['futures_id'].unique() if 'futures_id' in fut.columns else 'N/A'}")

    # Filter for TX (台指期 大台)
    tx = fut[fut["futures_id"] == "TX"].copy()
    if tx.empty:
        print("  ❌ 無 TX 資料")
        return
    tx["date"] = pd.to_datetime(tx["date"])

    # 期貨可能多個合約月，取近月（最先到期）
    if "contract_date" in tx.columns:
        tx = tx.sort_values(["date", "contract_date"]).groupby("date").first().reset_index()

    print(f"\n  TX rows: {len(tx):,}")
    print(f"  Date range: {tx['date'].min().date()} ~ {tx['date'].max().date()}")
    print(f"  Sample cols: {list(tx.columns)[:8]}")

    # Use 'close' as TX close
    if "close" not in tx.columns:
        print(f"  ⚠️ no 'close' column, available: {list(tx.columns)}")
        return
    tx_close = tx[["date", "close"]].rename(columns={"close": "tx_close"})

    # Load TWII
    twii = pd.read_parquet(TWII_PATH)
    twii["date"] = pd.to_datetime(twii["date"])
    twii = twii[["date", "close"]].rename(columns={"close": "twii_close"})

    df = tx_close.merge(twii, on="date").sort_values("date").reset_index(drop=True)
    if df.empty:
        print("  ❌ merge 失敗")
        return

    df["basis"] = df["tx_close"] - df["twii_close"]
    df["basis_pct"] = df["basis"] / df["twii_close"] * 100  # 點數轉 %

    # Rolling z-score
    for win in [60, 252]:
        df[f"basis_z_{win}"] = (
            (df["basis"] - df["basis"].rolling(win).mean())
            / df["basis"].rolling(win).std()
        )

    # Forward returns
    for h in [5, 10, 20, 60]:
        df[f"fwd_{h}d"] = (df["twii_close"].shift(-h) / df["twii_close"] - 1) * 100

    df = df.dropna(subset=["basis_z_60", "fwd_20d"])
    print(f"\n  Backtest observations: {len(df):,}")
    print(f"  basis stats: mean {df['basis'].mean():.1f} pts, std {df['basis'].std():.1f}, "
          f"range [{df['basis'].min():.0f}, {df['basis'].max():.0f}]")
    print(f"  basis_pct stats: mean {df['basis_pct'].mean():.3f}%, std {df['basis_pct'].std():.3f}%")

    # Bucket by basis_z
    print(f"\n  === TWII fwd return by basis_z (60d) bucket ===")
    bucks = [
        ("Deep discount (z < -2)", df["basis_z_60"] < -2),
        ("Mild discount (-2 ~ -1)", (df["basis_z_60"] >= -2) & (df["basis_z_60"] < -1)),
        ("Normal (-1 ~ +1)", (df["basis_z_60"] >= -1) & (df["basis_z_60"] < 1)),
        ("Mild premium (+1 ~ +2)", (df["basis_z_60"] >= 1) & (df["basis_z_60"] < 2)),
        ("Deep premium (z > +2)", df["basis_z_60"] >= 2),
    ]

    for hold in [5, 20, 60]:
        col = f"fwd_{hold}d"
        print(f"\n  fwd {hold}d:")
        print(f"  {'Bucket':<28} {'n':>5} {'mean':>8} {'win%':>6} {'t':>6}")
        for label, mask in bucks:
            sub = df.loc[mask, col].dropna()
            if len(sub) < 5:
                print(f"  {label:<28} n={len(sub)} (太少)")
                continue
            t, p = stats.ttest_1samp(sub, 0, alternative="two-sided")
            sig = "✅" if abs(t) > 2 else ""
            print(f"  {label:<28} {len(sub):>5} {sub.mean():>+7.2f}% "
                  f"{(sub>0).mean()*100:>5.1f}% {t:>+5.2f}{sig}")

    # Time series of extreme events
    extreme_disc = df[df["basis_z_60"] < -2]
    extreme_prem = df[df["basis_z_60"] > 2]
    print(f"\n  === Extreme Events ===")
    print(f"  Deep discount (z < -2): {len(extreme_disc)} 天")
    print(f"  Deep premium (z > +2):  {len(extreme_prem)} 天")
    if not extreme_disc.empty:
        print(f"  Recent deep discount samples:")
        for _, row in extreme_disc.tail(5).iterrows():
            print(f"    {row['date'].date()}: basis {row['basis']:.0f}pts, z {row['basis_z_60']:.2f}")
    if not extreme_prem.empty:
        print(f"  Recent deep premium samples:")
        for _, row in extreme_prem.tail(5).iterrows():
            print(f"    {row['date'].date()}: basis +{row['basis']:.0f}pts, z {row['basis_z_60']:.2f}")

    # Year breakdown for deep discount → fwd 20d
    print(f"\n  === Deep Discount fwd 20d by year ===")
    extreme_disc["year"] = extreme_disc["date"].dt.year
    print(f"  {'Year':<6} {'n':>4} {'mean':>8} {'win%':>6}")
    for yr in sorted(extreme_disc["year"].unique()):
        sub = extreme_disc[extreme_disc["year"] == yr]["fwd_20d"].dropna()
        if len(sub) < 1:
            continue
        print(f"  {yr:<6} {len(sub):>4} {sub.mean():>+7.2f}% {(sub>0).mean()*100:>5.1f}%")

    # Save
    out = ROOT / "logs" / "tx_basis_signal.csv"
    out.parent.mkdir(exist_ok=True)
    df[["date", "tx_close", "twii_close", "basis", "basis_pct", "basis_z_60",
        "fwd_5d", "fwd_20d", "fwd_60d"]].to_csv(out, index=False)
    print(f"\n  ✅ Saved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
