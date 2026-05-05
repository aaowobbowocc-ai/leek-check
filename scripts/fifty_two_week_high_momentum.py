"""
52-Week High Momentum Backtest (George-Hwang 2004)

假設:
  接近 52 週高的股票 = 散戶 anchor bias (覺得歷史高貴, 不敢買)
  → fundamentally strong 卻被 anchor pricing 壓抑
  → fwd N 日 momentum continuation

訊號:
  proximity = current_close / max(close, last 252 days)
  Trigger: proximity >= 0.95 (within 5% of 52w high)
  Hold: 20 / 60 / 120 days

學術: George & Hwang 2004 "The 52-Week High and Momentum Investing"
  US market: 接近 52w high stocks fwd 6m alpha 占整體 momentum 70%

TW 驗證:
  - Full sample alpha
  - OOS 3-period split
  - Liquidity filter (L4 > 10億/日)
  - 跟 0050 baseline 對比
  - Portfolio simulation max=20

Cost: 0.78%
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
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
COST = 0.78
HOLDS = [20, 60, 120]
PROXIMITY = 0.95  # within 5% of 52w high
MIN_LIQUIDITY_YI = 1.0  # 1 億/日 minimum


def load_px(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    if len(df) < 280:
        return pd.DataFrame()
    df["high_252"] = df["close"].rolling(252).max()
    df["proximity"] = df["close"] / df["high_252"]
    df["dv"] = df["close"] * df["volume"]
    df["dv_60d"] = df["dv"].rolling(60).mean()
    return df


def detect_events(tk: str) -> list[dict]:
    px = load_px(tk)
    if px.empty:
        return []

    events = []
    in_signal = False  # to avoid daily duplicate triggers
    for idx in range(252, len(px) - max(HOLDS) - 1):
        row = px.iloc[idx]
        if pd.isna(row["proximity"]) or pd.isna(row["dv_60d"]):
            continue
        if row["dv_60d"] < MIN_LIQUIDITY_YI * 1e8:
            continue

        is_signal = row["proximity"] >= PROXIMITY
        if is_signal and not in_signal:
            in_signal = True
            entry = px.iloc[idx + 1]["open"]
            if entry <= 0:
                continue
            rec = {
                "ticker": tk,
                "date": row["date"],
                "proximity": float(row["proximity"]),
                "dv_60d_yi": float(row["dv_60d"] / 1e8),
                "entry": entry,
            }
            for hold in HOLDS:
                exit_p = px.iloc[idx + hold]["close"]
                rec[f"fwd_{hold}d"] = (exit_p / entry - 1) * 100 - COST
            events.append(rec)
        elif not is_signal:
            in_signal = False

    return events


def main():
    print("=" * 80)
    print("  52-Week High Momentum Backtest (George-Hwang 2004)")
    print(f"  Trigger: proximity >= {PROXIMITY} (within {(1-PROXIMITY)*100:.0f}% of 52w high)")
    print(f"  Hold {HOLDS}d, COST {COST}%, liquidity > {MIN_LIQUIDITY_YI}億/日")
    print("=" * 80)

    universe = sorted([
        p.stem for p in TW_CACHE.glob("*.parquet")
        if p.stem.isdigit() and len(p.stem) == 4 and not p.stem.startswith("00")
    ])
    print(f"\n  Universe: {len(universe)} tickers")

    print(f"\n  Scanning events (first occurrence per signal cluster)...")
    all_events = []
    for i, tk in enumerate(universe):
        try:
            events = detect_events(tk)
            all_events.extend(events)
        except Exception:
            continue
        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(universe)}] events: {len(all_events):,}")

    df = pd.DataFrame(all_events)
    if df.empty:
        print("  ❌ 無事件")
        return

    df["date"] = pd.to_datetime(df["date"])
    print(f"\n  Total events: {len(df):,}")

    # Full sample
    for hold in HOLDS:
        col = f"fwd_{hold}d"
        sub = df[col].dropna()
        if len(sub) < 5: continue
        t, p = stats.ttest_1samp(sub, 0, alternative="two-sided")
        win = (sub > 0).mean() * 100
        print(f"\n  === fwd {hold}d ===")
        print(f"    n={len(sub):,}  mean={sub.mean():+.2f}%  median={sub.median():+.2f}%")
        print(f"    t={t:+.2f}  p={p:.5f}  win={win:.1f}%")

    # OOS Walk-Forward
    print(f"\n  === OOS Walk-Forward (fwd 60d) ===")
    df["year"] = df["date"].dt.year
    splits = [
        ("Period A 2017-2019", 2017, 2019),
        ("Period B 2020-2022", 2020, 2022),
        ("Period C 2023-2025", 2023, 2025),
    ]
    print(f"  {'Period':<22} {'n':>6} {'mean':>8} {'win%':>6} {'t':>6}")
    for label, ys, ye in splits:
        sub = df[(df["year"] >= ys) & (df["year"] <= ye)]["fwd_60d"].dropna()
        if len(sub) < 50: continue
        t, _ = stats.ttest_1samp(sub, 0, alternative="two-sided")
        sig = "✅" if abs(t) > 2 else "❌"
        print(f"  {label:<22} {len(sub):>6} {sub.mean():>+7.2f}% "
              f"{(sub>0).mean()*100:>5.1f}% {t:>+5.2f}{sig}")

    # Liquidity filter sweep
    print(f"\n  === Liquidity Filter Sweep (fwd 60d) ===")
    print(f"  {'Threshold':<16} {'n':>6} {'mean':>8} {'win%':>6}")
    for liq in [1, 5, 10, 20]:
        sub = df[df["dv_60d_yi"] >= liq]["fwd_60d"].dropna()
        if len(sub) < 30: continue
        print(f"  > {liq:>3} 億/日       {len(sub):>6} {sub.mean():>+7.2f}% {(sub>0).mean()*100:>5.1f}%")

    # vs same-period 0050 baseline
    etf_path = TW_CACHE / "0050.parquet"
    if etf_path.exists():
        etf = pd.read_parquet(etf_path)
        etf["date"] = pd.to_datetime(etf["date"])
        etf = etf.sort_values("date").reset_index(drop=True)
        print(f"\n  === vs 0050 baseline (fwd 60d) ===")
        # For each event, compute 0050 fwd 60d return at same start date
        df_l4 = df[df["dv_60d_yi"] >= 10]  # L4 filter
        baseline_rets = []
        strategy_rets = []
        for _, row in df_l4.iterrows():
            etf_sub = etf[etf["date"] >= row["date"]].head(61)
            if len(etf_sub) < 61:
                continue
            etf_ret = (etf_sub.iloc[-1]["close"] / etf_sub.iloc[0]["open"] - 1) * 100 - COST
            baseline_rets.append(etf_ret)
            strategy_rets.append(row["fwd_60d"])
        if strategy_rets:
            arr_s = np.array(strategy_rets)
            arr_b = np.array(baseline_rets)
            print(f"  L4 strategy: n={len(arr_s)}, mean {arr_s.mean():+.2f}%")
            print(f"  0050 same-period: mean {arr_b.mean():+.2f}%")
            print(f"  Excess vs 0050: {(arr_s - arr_b).mean():+.2f}pp")
            t, p = stats.ttest_rel(arr_s, arr_b)
            print(f"  Paired t-test: t={t:+.2f}, p={p:.4f}")

    # Save
    out = ROOT / "logs" / "52wk_high_momentum_events.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n  ✅ Saved {len(df)} events to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
