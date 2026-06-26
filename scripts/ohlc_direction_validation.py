"""
OHLC 方向 proxy 驗證 — 兩部分：

A. 預測力測試（真不真有 alpha）：
   - 100 ticker × 6 年 = 數十萬日
   - 對每天計算 OHLC pos 與 direction
   - 比較 buying / selling / unknown 的 T+5 / T+30 / T+60 forward return
   - 若 buying 顯著 > selling → 方向有預測力

B. 高信心 cohort 比較（加 filter 是否提升 hit rate）：
   - v2 觸發樣本 = z 3.0-3.5 + 200MA + score >= 70
   - 拆 buying / selling / unknown 三桶看 T+60d 分布
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
sys.path.insert(0, str(ROOT))
TW = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def ohlc_direction(open_, close, high, low):
    """純算 — 與 detect_direction 邏輯一致（無內盤比版本）"""
    if high <= low: return "unknown"
    pos = (close - low) / (high - low)
    if pos > 0.65 and close >= open_ * 0.98:
        return "buying"
    if pos < 0.35:
        return "selling"
    return "unknown"


def annotate_one(tk):
    p = TW / f"{tk}.parquet"
    if not p.exists() or p.stat().st_size < 500: return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty or len(df) < 252: return None
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)

    # OHLC pos + direction
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["pos"] = (df["close"] - df["low"]) / rng
    df["direction"] = "unknown"
    buy_mask = (df["pos"] > 0.65) & (df["close"] >= df["open"] * 0.98)
    sell_mask = df["pos"] < 0.35
    df.loc[buy_mask, "direction"] = "buying"
    df.loc[sell_mask, "direction"] = "selling"

    # vol z
    df["vol_ma60"] = df["volume"].rolling(60).mean()
    df["vol_std60"] = df["volume"].rolling(60).std()
    df["vol_z"] = (df["volume"] - df["vol_ma60"]) / df["vol_std60"]

    # 200MA filter
    df["ma200"] = df["close"].rolling(200).mean()
    df["above_ma200"] = df["close"] > df["ma200"]

    # forward returns
    for h in [5, 30, 60]:
        df[f"fwd_{h}"] = (df["close"].shift(-h) / df["close"] - 1) * 100

    return df


def part_a_predictive_power(tickers):
    print("=" * 90)
    print("A. OHLC 方向 proxy 預測力（無篩選，全市場全日子）")
    print("=" * 90)

    aggregated = {dir: {h: [] for h in [5, 30, 60]} for dir in ["buying", "selling", "unknown"]}
    for tk in tickers:
        df = annotate_one(tk)
        if df is None: continue
        for dir in ["buying", "selling", "unknown"]:
            sub = df[df["direction"] == dir]
            for h in [5, 30, 60]:
                vals = sub[f"fwd_{h}"].dropna().tolist()
                aggregated[dir][h].extend(vals)

    print(f"\n  {'方向':<10} {'hold':>5} {'n':>8} {'mean%':>8} {'median':>8} "
          f"{'win%':>6} {'std':>6}")
    print(f"  {'-'*10} {'-'*5} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*6}")

    for dir in ["buying", "unknown", "selling"]:
        for h in [5, 30, 60]:
            arr = np.array(aggregated[dir][h])
            if len(arr) < 50: continue
            print(f"  {dir:<10} {h:>4}d {len(arr):>8} "
                  f"{arr.mean():>+7.2f} {np.median(arr):>+7.2f} "
                  f"{(arr>0).mean()*100:>5.1f}% {arr.std():>5.1f}")
        print()

    # alpha gap: buying - selling
    print(f"\n  📊 buying − selling alpha gap:")
    for h in [5, 30, 60]:
        b = np.array(aggregated["buying"][h])
        s = np.array(aggregated["selling"][h])
        if len(b) > 50 and len(s) > 50:
            gap = b.mean() - s.mean()
            # t-stat
            t = (b.mean() - s.mean()) / np.sqrt(b.var()/len(b) + s.var()/len(s))
            sig = "⭐⭐⭐" if abs(t) > 5 else ("⭐⭐" if abs(t) > 3 else ("⭐" if abs(t) > 2 else ""))
            print(f"    {h}d: buying {b.mean():+.2f}% − selling {s.mean():+.2f}% "
                  f"= {gap:+.2f}pp  (t={t:+.1f}) {sig}")


def part_b_high_confidence_cohort(tickers):
    print("\n\n" + "=" * 90)
    print("B. v2 cohort 拆桶（z 3.0-3.5 + 200MA + 加 OHLC 方向 filter）")
    print("=" * 90)

    cohort = []
    for tk in tickers:
        df = annotate_one(tk)
        if df is None: continue
        # v2 cohort: z 3.0-3.5 + 200MA
        mask = (df["vol_z"] >= 3.0) & (df["vol_z"] < 3.5) & (df["above_ma200"] == True)
        sub = df[mask & df["fwd_60"].notna()]
        for _, row in sub.iterrows():
            cohort.append({
                "ticker": tk,
                "vol_z": row["vol_z"],
                "direction": row["direction"],
                "fwd_5": row["fwd_5"],
                "fwd_30": row["fwd_30"],
                "fwd_60": row["fwd_60"],
            })

    if len(cohort) < 30:
        print(f"  ⚠️ cohort 太少 (n={len(cohort)})")
        return

    df = pd.DataFrame(cohort)
    print(f"\n  cohort total n: {len(df)}")
    print(f"\n  {'tier':<25} {'n':>5} {'fwd60_mean':>11} {'median':>8} "
          f"{'win>15%':>9} {'fail<-15%':>11}")
    print(f"  {'-'*25} {'-'*5} {'-'*11} {'-'*8} {'-'*9} {'-'*11}")

    # all (v2 baseline)
    arr = df["fwd_60"].dropna().values
    print(f"  {'all v2 (z3.0-3.5+MA)':<25} {len(arr):>5} "
          f"{arr.mean():>+10.2f}% {np.median(arr):>+7.2f}% "
          f"{(arr>15).mean()*100:>7.1f}% {(arr<-15).mean()*100:>9.1f}%")

    # by direction
    for dir, label in [("buying", "🟢 high (buying)"),
                        ("unknown", "🟡 medium (unknown)"),
                        ("selling", "❌ would-reject (selling)")]:
        sub = df[df["direction"] == dir]["fwd_60"].dropna().values
        if len(sub) < 5: continue
        print(f"  {label:<25} {len(sub):>5} "
              f"{sub.mean():>+10.2f}% {np.median(sub):>+7.2f}% "
              f"{(sub>15).mean()*100:>7.1f}% {(sub<-15).mean()*100:>9.1f}%")

    # alpha gap
    b = df[df["direction"] == "buying"]["fwd_60"].dropna().values
    s = df[df["direction"] == "selling"]["fwd_60"].dropna().values
    if len(b) > 5 and len(s) > 5:
        gap = b.mean() - s.mean()
        t = (b.mean() - s.mean()) / np.sqrt(b.var()/len(b) + s.var()/len(s))
        print(f"\n  buying vs selling 60d gap: {gap:+.2f}pp  t={t:+.1f}")
        print(f"  → 加 OHLC=buying filter 是否提升？")
        all_mean = df["fwd_60"].mean()
        if b.mean() > all_mean:
            print(f"     ✅ buying mean {b.mean():+.2f}% > all {all_mean:+.2f}% (improve {b.mean()-all_mean:+.2f}pp)")
        else:
            print(f"     ❌ buying mean {b.mean():+.2f}% ≤ all {all_mean:+.2f}%")


def main():
    print("OHLC 方向 proxy 驗證")
    print("baseline: 同 ticker random window")

    # 抽 200 ticker
    all_tks = sorted([p.stem for p in TW.glob("*.parquet")
                       if not p.stem.startswith("00") and p.stem.isdigit()
                       and len(p.stem) == 4])
    rng = np.random.default_rng(42)
    sample = list(rng.choice(all_tks, size=min(200, len(all_tks)), replace=False))
    print(f"sample {len(sample)} ticker × 9y\n")

    part_a_predictive_power(sample)
    part_b_high_confidence_cohort(sample)


if __name__ == "__main__":
    main()
