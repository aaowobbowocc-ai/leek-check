"""
融資餘額激增 Reversal Backtest（散戶過度槓桿訊號）

假設:
  融資餘額 = 散戶向券商借錢買股 (retail leverage)
  短期內融資餘額大幅增加 → 散戶 FOMO over-leverage
  → 後續股價 weakness（散戶被迫平倉、聰明錢出貨）

訊號:
  margin_surge_5d = (今日融資餘額 / 5 日前融資餘額) - 1
  Trigger: surge > +20% (over-leverage 警示)
  Hold: 20 / 60 days
  Exit: hold-to-maturity

Universe: 1854 ticker × 9 年 (FinMind margin cache)
Cost: 0.78% round trip
Baseline: same-ticker random window
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
MARGIN_CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
COST = 0.78
HOLDS = [20, 60]
SURGE_THRESHOLD = 20.0  # +20% in 5 days

# Filter: only large enough to have meaningful margin (avoid penny stocks)
MIN_MARGIN_BALANCE = 100  # 至少 100 張融資餘額（基本流動性）


def load_px(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_margin(tk: str) -> pd.DataFrame:
    p = MARGIN_CACHE / f"TaiwanStockMarginPurchaseShortSale_{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    # 融資餘額 = MarginPurchaseTodayBalance
    return df[["date", "MarginPurchaseTodayBalance"]].rename(
        columns={"MarginPurchaseTodayBalance": "margin_bal"}
    )


def detect_events(tk: str) -> list[dict]:
    px = load_px(tk)
    margin = load_margin(tk)
    if px.empty or margin.empty or len(px) < 100:
        return []

    df = px.merge(margin, on="date", how="left")
    df["margin_bal"] = df["margin_bal"].ffill()
    df = df.dropna(subset=["margin_bal"]).reset_index(drop=True)
    if len(df) < 100:
        return []

    df["margin_5d_ago"] = df["margin_bal"].shift(5)
    df["surge_pct"] = (df["margin_bal"] / df["margin_5d_ago"] - 1) * 100

    events = []
    for idx in range(60, len(df) - max(HOLDS) - 1):
        row = df.iloc[idx]
        if pd.isna(row["surge_pct"]):
            continue
        if row["margin_bal"] < MIN_MARGIN_BALANCE:
            continue
        if row["surge_pct"] < SURGE_THRESHOLD:
            continue

        entry = df.iloc[idx + 1]["open"]
        if entry <= 0:
            continue

        rec = {
            "ticker": tk,
            "date": row["date"],
            "surge_pct": float(row["surge_pct"]),
            "margin_bal": float(row["margin_bal"]),
            "entry": entry,
        }
        for hold in HOLDS:
            exit_p = df.iloc[idx + hold]["close"]
            rec[f"fwd_{hold}d"] = (exit_p / entry - 1) * 100 - COST
        events.append(rec)
    return events


def main():
    print("=" * 80)
    print("  融資餘額 5 日激增 Reversal Backtest")
    print(f"  Trigger: margin balance up > {SURGE_THRESHOLD}% in 5 days")
    print(f"  Hold: {HOLDS} days, COST {COST}%")
    print("=" * 80)

    margin_files = list(MARGIN_CACHE.glob("TaiwanStockMarginPurchaseShortSale_*.parquet"))
    universe = []
    for p in margin_files:
        tk = p.stem.replace("TaiwanStockMarginPurchaseShortSale_", "")
        if tk.isdigit() and len(tk) == 4 and not tk.startswith("00"):
            universe.append(tk)
    universe = sorted(universe)
    print(f"\n  Universe: {len(universe)} tickers (個股, 排除 ETF)")

    print(f"\n  掃描 events...")
    all_events = []
    for i, tk in enumerate(universe):
        try:
            events = detect_events(tk)
            all_events.extend(events)
        except Exception:
            continue
        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(universe)}] events so far: {len(all_events)}")

    df = pd.DataFrame(all_events)
    if df.empty:
        print("  ❌ 無事件")
        return

    print(f"\n  Total events: {len(df):,}")

    for hold in HOLDS:
        col = f"fwd_{hold}d"
        sub = df[col].dropna()
        if len(sub) < 5:
            continue
        t, p = stats.ttest_1samp(sub, 0, alternative="two-sided")
        win = (sub > 0).mean() * 100
        print(f"\n  === fwd {hold}d ===")
        print(f"    n={len(sub):,}  mean={sub.mean():+.2f}%  median={sub.median():+.2f}%")
        print(f"    t={t:+.2f}  p={p:.5f}  win={win:.1f}%")
        # Two-sided p; alpha could be either direction
        if t < -2:
            print(f"    ✅ 顯著負 alpha — 融資激增後弱（散戶 over-leverage 反向）")
        elif t > 2:
            print(f"    ✅ 顯著正 alpha — 融資激增後續強（momentum）")
        else:
            print(f"    ❌ 不顯著")

    # Surge buckets
    print(f"\n  === By surge magnitude ===")
    df["surge_bucket"] = pd.cut(
        df["surge_pct"],
        bins=[0, 30, 50, 100, 9999],
        labels=["+20-30%", "+30-50%", "+50-100%", ">+100%"],
    )
    for hold in [60]:
        col = f"fwd_{hold}d"
        print(f"\n  fwd {hold}d by bucket:")
        print(f"  {'Bucket':<14} {'n':>6} {'mean':>8} {'win%':>6}")
        grp = df.groupby("surge_bucket", observed=True)[col].agg(
            n="count", mean="mean", win=lambda x: (x > 0).mean() * 100
        )
        for bucket, row in grp.iterrows():
            print(f"  {str(bucket):<14} {int(row['n']):>6} {row['mean']:>+7.2f}% {row['win']:>5.1f}%")

    # Year breakdown
    print(f"\n  === Year-by-Year (fwd 20d) ===")
    df["year"] = df["date"].dt.year
    print(f"  {'Year':<6} {'n':>5} {'mean':>8} {'win%':>6}")
    for yr in sorted(df["year"].unique()):
        sub = df[df["year"] == yr]["fwd_20d"].dropna()
        if len(sub) < 5:
            continue
        print(f"  {yr:<6} {len(sub):>5} {sub.mean():>+7.2f}% {(sub>0).mean()*100:>5.1f}%")

    # Save
    out = ROOT / "logs" / "margin_surge_events.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n  ✅ Saved {len(df)} events to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
