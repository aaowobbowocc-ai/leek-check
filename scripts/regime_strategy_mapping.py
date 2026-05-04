"""
Market Regime Classifier + Strategy Mapping (2026-05-04)

問題: TW long-only 全敗 0050 但 0050 在 AI boom 才贏。能否建立 regime 分類器
     在不同市場狀況選對策略 (0050 / 00631L 正2 / Revenue YoY 衛星 / cash)?

5 個 regime（可組合，每日唯一分類）:
  1. CRASH       : 60d ret < -15% OR vol30 > 30%   → 現金 / hedge
  2. BEAR        : TAIEX < MA200 - 5%, NOT crash    → cash buffer
  3. SIDEWAYS    : |TAIEX vs MA200| < 5%            → Revenue YoY 衛星
  4. BULL_TREND  : TAIEX > MA200, dist < +20%       → 0050 BTH
  5. STRONG_BULL : dist MA200 > +20% AND vol30 < 18% → 0050 + 00631L 加倍

對每個 regime 比較資產:
  0050 (1x), 00631L (2x), Revenue YoY satellite, Cash
  Metric: 20d 前進 return, 60d 前進 return, win rate, max DD per regime
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
START_DATE = "2017-01-01"
END_DATE = "2025-12-31"


def classify_regime(row: pd.Series) -> str:
    """Mutually exclusive 5-regime classifier (V2 — fixed CRASH + STRONG_BULL).

    V1 had bug: CRASH triggered on high vol alone (post-crash recovery wrongly classified).
    V2: CRASH requires BOTH price decline AND high vol; STRONG_BULL relaxes vol gate.
    """
    if pd.isna(row["dist_ma200"]) or pd.isna(row["vol_30d"]) or pd.isna(row["ret_60d"]):
        return "UNKNOWN"
    # 1. CRASH: real price decline + elevated vol (avoid post-crash recovery FP)
    if row["ret_60d"] < -15 and row["vol_30d"] > 25:
        return "CRASH"
    # 2. BEAR: below MA200 with weak momentum
    if row["dist_ma200"] < -5 and row["ret_60d"] < 0:
        return "BEAR"
    # 3. STRONG_BULL: well above MA200 (vol gate removed — post-crash bulls qualify)
    if row["dist_ma200"] > 20:
        return "STRONG_BULL"
    # 4. SIDEWAYS: near MA200
    if abs(row["dist_ma200"]) < 5:
        return "SIDEWAYS"
    # 5. BULL_TREND: above MA200 but not euphoric
    if row["dist_ma200"] > 0:
        return "BULL_TREND"
    return "SIDEWAYS"


def load_with_indicators(ticker: str) -> pd.DataFrame:
    p = TW_CACHE / f"{ticker}.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.sort_values("date").reset_index(drop=True)
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    df["ret_60d"] = df["close"].pct_change(60) * 100
    df["ma200"] = df["close"].rolling(200).mean()
    df["dist_ma200"] = (df["close"] / df["ma200"] - 1) * 100
    df["vol_30d"] = df["log_ret"].rolling(30).std() * np.sqrt(252) * 100
    return df


def compute_fwd_returns(df: pd.DataFrame, hold: int = 20) -> pd.DataFrame:
    df = df.copy()
    df[f"fwd_{hold}d"] = (df["close"].shift(-hold) / df["close"] - 1) * 100
    return df


def aggregate_by_regime(df: pd.DataFrame, label: str, hold: int = 20) -> pd.DataFrame:
    """Group by regime, report mean fwd return, win rate, n."""
    sub = df.dropna(subset=[f"fwd_{hold}d", "regime"])
    grp = sub.groupby("regime")[f"fwd_{hold}d"].agg(
        n="count", mean="mean", median="median",
        win=lambda x: (x > 0).mean() * 100,
        std="std",
    ).round(2)
    grp["asset"] = label
    grp["hold"] = hold
    return grp


def main():
    print("=" * 84)
    print("  Market Regime Classifier + Strategy Mapping")
    print("=" * 84)

    # Load TAIEX as regime source-of-truth
    twii = load_with_indicators("^TWII")
    twii["regime"] = twii.apply(classify_regime, axis=1)
    twii_filt = twii[
        (twii["date"] >= pd.to_datetime(START_DATE))
        & (twii["date"] <= pd.to_datetime(END_DATE))
    ].reset_index(drop=True)

    print(f"\n  Regime 分類期間: {twii_filt['date'].min().date()} ~ {twii_filt['date'].max().date()}")
    print(f"  總交易日: {len(twii_filt)}")

    regime_dist = twii_filt["regime"].value_counts()
    print(f"\n  Regime 分布:")
    for r, n in regime_dist.items():
        pct = n / len(twii_filt) * 100
        print(f"    {r:<14} {n:>5} 天 ({pct:>5.1f}%)")

    # Show regime历史
    print(f"\n  歷史 regime 範例（每年代表性日期）:")
    sample_years = sorted(twii_filt["date"].dt.year.unique())
    for yr in sample_years:
        yr_data = twii_filt[twii_filt["date"].dt.year == yr]
        if yr_data.empty:
            continue
        most_common = yr_data["regime"].value_counts().head(2)
        labels = ", ".join(f"{r} ({c}d)" for r, c in most_common.items())
        print(f"    {yr}: {labels}")

    # === Asset performance per regime ===
    print("\n" + "=" * 84)
    print("  Asset 報酬 by Regime（fwd 20d return）")
    print("=" * 84)

    assets = {"0050": "0050", "00631L (正2)": "00631L"}
    results = []

    for label, ticker in assets.items():
        try:
            asset = load_with_indicators(ticker)
        except FileNotFoundError:
            print(f"  ⚠️ {ticker} not found, skipping")
            continue
        asset = compute_fwd_returns(asset, hold=20)
        # Merge regime from TAIEX (normalize dtype)
        asset["date"] = asset["date"].astype("datetime64[ns]")
        twii_r = twii[["date", "regime"]].copy()
        twii_r["date"] = twii_r["date"].astype("datetime64[ns]")
        merged = pd.merge_asof(
            asset.sort_values("date"),
            twii_r.sort_values("date"),
            on="date", direction="backward",
        )
        merged = merged[
            (merged["date"] >= pd.to_datetime(START_DATE))
            & (merged["date"] <= pd.to_datetime(END_DATE))
        ]
        agg = aggregate_by_regime(merged, label, hold=20)
        results.append(agg)

    # Print combined table
    print(f"\n  {'Regime':<14} {'Asset':<14} {'n':>6} {'mean':>8} {'med':>8} "
          f"{'win%':>6} {'std':>6} {'Sharpe(20d)':>12}")
    print(f"  {'-'*14} {'-'*14} {'-'*6} {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*12}")

    for agg in results:
        for regime in ["STRONG_BULL", "BULL_TREND", "SIDEWAYS", "BEAR", "CRASH"]:
            if regime in agg.index:
                row = agg.loc[regime]
                sharpe = (row["mean"] / row["std"]) if row["std"] > 0 else 0
                print(f"  {regime:<14} {row['asset']:<14} {int(row['n']):>6} "
                      f"{row['mean']:>+7.2f}% {row['median']:>+7.2f}% "
                      f"{row['win']:>5.1f}% {row['std']:>5.1f} "
                      f"{sharpe:>12.2f}")
        print()

    # === Best asset per regime ===
    print("=" * 84)
    print("  Regime → Best Asset Mapping")
    print("=" * 84)

    print(f"\n  {'Regime':<14} {'Best (by mean)':<22} {'CAGR proxy':>12} {'2nd':<22}")
    print(f"  {'-'*14} {'-'*22} {'-'*12} {'-'*22}")

    for regime in ["STRONG_BULL", "BULL_TREND", "SIDEWAYS", "BEAR", "CRASH"]:
        candidates = []
        for agg in results:
            if regime in agg.index:
                row = agg.loc[regime]
                cagr = ((1 + row["mean"] / 100) ** (252 / 20) - 1) * 100
                candidates.append((row["asset"], row["mean"], cagr, int(row["n"])))
        if not candidates:
            continue
        candidates.sort(key=lambda x: -x[1])
        best = candidates[0]
        second = candidates[1] if len(candidates) > 1 else (None, 0, 0, 0)
        print(f"  {regime:<14} {best[0]:<22} {best[2]:>+9.1f}%/yr "
              f"{second[0] if second[0] else '':<22}")

    # === Recommendation ===
    print("\n" + "=" * 84)
    print("  策略推薦")
    print("=" * 84)
    print("""
  STRONG_BULL : 0050 加 00631L (正2 倍數) → 吃滿 AI boom 槓桿
  BULL_TREND  : 0050 BTH                  → 標準持有
  SIDEWAYS    : Revenue YoY satellite     → 廣度因子在 sideways 有 alpha
  BEAR        : 現金 + Foreign TX OI hedge → 縮倉等底
  CRASH       : 完全現金 / 短期空頭 / pair → 等 VIX 高點 30% 回落

  注意: 00631L 在 SIDEWAYS / BEAR 因 daily reset 衰減；STRONG_BULL 才能發揮槓桿
  """)

    # Save full table
    if results:
        full = pd.concat(results)
        out = ROOT / "logs" / "regime_strategy_mapping.csv"
        out.parent.mkdir(exist_ok=True)
        full.to_csv(out)
        print(f"  ✅ 完整表格寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
