"""
三大法人連賣 Reversal Backtest

假設:
  外資 + 投信 + 自營商 (三大法人) 連續賣超 N 日 → 賣壓接近尾聲
  → fwd N 日反彈 (mean reversion)

訊號:
  inst_net = Foreign_Investor + Investment_Trust + Dealer_self
  consec_sell = 連續 inst_net < 0 的天數
  Trigger: consec_sell >= 5 (或 7)

Hold: 5/10/20 日
Universe: 1977 個股 institutional cache
Cost: 0.78%

注意: 跟 govbank consensus anti-signal (-1.62%) 不同
       govbank 是 8 大行庫共識
       這裡是三大法人總和連賣
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
INST_CACHE = ROOT / "data" / "cache" / "finmind" / "institutional"
COST = 0.78
HOLDS = [5, 10, 20]

# Liquidity filter: avoid 小型股雜訊
MIN_LIQUIDITY_YI = 1.0  # 1 億/日


def load_px(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["dv"] = df["close"] * df["volume"]
    df["dv_60d"] = df["dv"].rolling(60).mean()
    return df


def load_inst(tk: str) -> pd.DataFrame:
    p = INST_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    pivot = df.pivot_table(
        index="date", columns="name", values="net_buy",
        aggfunc="sum", fill_value=0,
    ).reset_index()
    pivot.columns.name = None
    return pivot.sort_values("date").reset_index(drop=True)


def detect_events(tk: str, n_consec: int = 5) -> list[dict]:
    px = load_px(tk)
    inst = load_inst(tk)
    if px.empty or inst.empty or len(px) < 100:
        return []

    # Sum 三大法人
    fi = "Foreign_Investor"
    inv = "Investment_Trust"
    dl = "Dealer_self"
    for col in [fi, inv, dl]:
        if col not in inst.columns:
            inst[col] = 0
    inst["inst_net"] = inst[fi] + inst[inv] + inst[dl]

    df = px.merge(inst[["date", "inst_net"]], on="date", how="left")
    df["inst_net"] = df["inst_net"].fillna(0)

    # 連續賣超天數
    df["is_sell"] = df["inst_net"] < 0
    # rolling sum: how many of last N days were 連續賣
    # We want: today AND yesterday AND ... N-1 days ago all 賣超
    df["consec_sell"] = 0
    consec = 0
    for i, sell in enumerate(df["is_sell"].values):
        consec = consec + 1 if sell else 0
        df.at[i, "consec_sell"] = consec

    events = []
    for idx in range(60, len(df) - max(HOLDS) - 1):
        row = df.iloc[idx]
        if row["consec_sell"] < n_consec:
            continue
        # Liquidity filter
        if pd.isna(row["dv_60d"]) or row["dv_60d"] < MIN_LIQUIDITY_YI * 1e8:
            continue
        # Only first day of consec_sell >= n (avoid duplicate triggers within window)
        if idx > 0 and df.iloc[idx - 1]["consec_sell"] >= n_consec:
            continue

        entry = df.iloc[idx + 1]["open"] if "open" in df.columns else None
        if entry is None or entry <= 0:
            continue

        rec = {
            "ticker": tk,
            "date": row["date"],
            "consec_sell": int(row["consec_sell"]),
            "dv_60d_yi": float(row["dv_60d"] / 1e8),
            "entry": entry,
        }
        for hold in HOLDS:
            exit_p = df.iloc[idx + hold]["close"]
            rec[f"fwd_{hold}d"] = (exit_p / entry - 1) * 100 - COST
        events.append(rec)
    return events


def main():
    print("=" * 80)
    print("  三大法人連賣 Reversal Backtest")
    print(f"  Trigger: consec_sell >= 5 (first occurrence), liquidity > {MIN_LIQUIDITY_YI}億/日")
    print("=" * 80)

    universe = sorted([
        p.stem for p in INST_CACHE.glob("*.parquet")
        if p.stem.isdigit() and len(p.stem) == 4 and not p.stem.startswith("00")
    ])
    print(f"\n  Universe: {len(universe)} tickers")

    print(f"\n  Scanning events (n_consec=5)...")
    all_events = []
    for i, tk in enumerate(universe):
        try:
            events = detect_events(tk, n_consec=5)
            all_events.extend(events)
        except Exception:
            continue
        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(universe)}] events: {len(all_events):,}")

    df = pd.DataFrame(all_events)
    if df.empty:
        print("  ❌ 無事件")
        return

    df["date"] = pd.to_datetime(df["date"])
    print(f"\n  Total events: {len(df):,}")

    for hold in HOLDS:
        col = f"fwd_{hold}d"
        sub = df[col].dropna()
        if len(sub) < 5: continue
        t, p = stats.ttest_1samp(sub, 0, alternative="two-sided")
        win = (sub > 0).mean() * 100
        print(f"\n  === fwd {hold}d ===")
        print(f"    n={len(sub):,}  mean={sub.mean():+.2f}%  median={sub.median():+.2f}%")
        print(f"    t={t:+.2f}  p={p:.5f}  win={win:.1f}%")

    # By consec_sell length
    print(f"\n  === By consec_sell length (fwd 20d) ===")
    print(f"  {'consec_sell':<14} {'n':>6} {'mean':>8} {'win%':>6} {'t':>6}")
    df["bucket"] = pd.cut(df["consec_sell"], bins=[4, 5, 7, 10, 100],
                           labels=["5d", "6-7d", "8-10d", ">10d"])
    for bucket, sub in df.groupby("bucket", observed=True):
        ret = sub["fwd_20d"].dropna()
        if len(ret) < 30: continue
        t, _ = stats.ttest_1samp(ret, 0, alternative="two-sided")
        print(f"  {str(bucket):<14} {len(ret):>6} {ret.mean():>+7.2f}% "
              f"{(ret>0).mean()*100:>5.1f}% {t:>+5.2f}")

    # OOS Walk-Forward
    print(f"\n  === OOS Walk-Forward (fwd 20d) ===")
    df["year"] = df["date"].dt.year
    splits = [
        ("Period A 2017-2019", 2017, 2019),
        ("Period B 2020-2022", 2020, 2022),
        ("Period C 2023-2025", 2023, 2025),
    ]
    print(f"  {'Period':<22} {'n':>6} {'mean':>8} {'win%':>6} {'t':>6}")
    for label, ys, ye in splits:
        sub = df[(df["year"] >= ys) & (df["year"] <= ye)]["fwd_20d"].dropna()
        if len(sub) < 50: continue
        t, _ = stats.ttest_1samp(sub, 0, alternative="two-sided")
        sig = "✅" if abs(t) > 2 else "❌"
        print(f"  {label:<22} {len(sub):>6} {sub.mean():>+7.2f}% "
              f"{(sub>0).mean()*100:>5.1f}% {t:>+5.2f}{sig}")

    # Liquidity filter sweep
    print(f"\n  === Liquidity Filter Sweep (fwd 20d) ===")
    print(f"  {'Threshold':<16} {'n':>6} {'mean':>8} {'win%':>6}")
    for liq in [0, 1, 5, 10]:
        sub = df[df["dv_60d_yi"] >= liq]["fwd_20d"].dropna()
        if len(sub) < 30: continue
        print(f"  > {liq:>3} 億/日       {len(sub):>6} {sub.mean():>+7.2f}% {(sub>0).mean()*100:>5.1f}%")

    out = ROOT / "logs" / "inst_consec_sell_events.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n  ✅ Saved {len(df)} events to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
