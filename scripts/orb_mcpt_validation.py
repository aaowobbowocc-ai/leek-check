"""
ORB MCPT 嚴格驗證 — 在牛市段對 2408/2485 跑 1000 次 permutation。

驗收：
  bull 期 actual_mean vs shuffle 1000 次 mean 分佈
  p < 0.01 → 牛市時真 alpha 可實單
  p > 0.05 → 永遠 paper trade only
"""
from __future__ import annotations
import io, sys
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "finmind" / "minute"
COST = 0.34
N_PERM = 1000
SEED = 42


def load_minute(tk):
    files = sorted(CACHE.glob(f"{tk}_*.parquet"))
    if not files: return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date_only"] = df["dt"].dt.date
    df["minute_str"] = df["dt"].dt.strftime("%H:%M")
    return df.sort_values("dt").reset_index(drop=True)


def detect_orb(day_df, prev_vol, entry_time, vol_thresh, ref):
    if day_df.empty or prev_vol <= 0: return None
    if ref == "open5":
        ref_w = day_df[day_df["minute_str"] <= "09:04"]
    else:
        ref_w = day_df[day_df["minute_str"] <= "09:14"]
    if ref_w.empty: return None
    ref_high = float(ref_w["high"].max())
    cum_w = day_df[day_df["minute_str"] < entry_time]
    if cum_w.empty: return None
    vol_ratio = float(cum_w["volume"].sum()) / prev_vol
    bar = day_df[day_df["minute_str"] == entry_time]
    if bar.empty: return None
    entry = float(bar["close"].iloc[0])
    if vol_ratio < vol_thresh or entry <= ref_high: return None
    exit_p = None
    for tt in ["13:20", "13:19", "13:21", "13:25", "13:30"]:
        b = day_df[day_df["minute_str"] == tt]
        if not b.empty:
            exit_p = float(b["close"].iloc[0]); break
    if exit_p is None:
        exit_p = float(day_df.iloc[-1]["close"])
    return (entry_p_ret := (exit_p / entry - 1) * 100)


def random_open_close_returns(df, days_filter):
    """全市場某段內，每天 09:15 → 13:20 random window 報酬"""
    rets = []
    for d in days_filter:
        day_df = df[df["date_only"] == d]
        b915 = day_df[day_df["minute_str"] == "09:15"]
        b1320 = day_df[day_df["minute_str"] == "13:20"]
        if not b915.empty and not b1320.empty:
            entry = float(b915["close"].iloc[0])
            exit_p = float(b1320["close"].iloc[0])
            rets.append((exit_p / entry - 1) * 100)
    return np.array(rets)


def mcpt_orb(ticker, entry_time, vol_thresh, ref, period_label, period_start, period_end):
    df = load_minute(ticker)
    if df.empty:
        print(f"  ❌ {ticker} 無資料"); return

    daily_vol = df.groupby("date_only")["volume"].sum().to_dict()
    days = sorted([d for d in df["date_only"].unique()
                   if period_start <= d <= period_end])
    if len(days) < 30:
        print(f"  ❌ {ticker} {period_label} sample 不足"); return

    # 實際訊號
    actual_rets = []
    for i, d in enumerate(days):
        if i == 0: continue
        prev = daily_vol.get(days[i-1], 0)
        ret = detect_orb(df[df["date_only"] == d], prev, entry_time, vol_thresh, ref)
        if ret is not None:
            actual_rets.append(ret - COST)
    if len(actual_rets) < 5:
        print(f"  ❌ {ticker} {period_label} 訊號太少 (n={len(actual_rets)})"); return

    actual_mean = np.mean(actual_rets)
    n = len(actual_rets)

    # MCPT - shuffle 隨機選 N 個交易日當「假訊號」
    rng = np.random.default_rng(SEED)
    rand_window_rets = random_open_close_returns(df, days)
    rand_window_rets = rand_window_rets - COST  # 扣同樣 cost

    if len(rand_window_rets) < n * 2:
        print(f"  ❌ {ticker} {period_label} random sample 不足"); return

    shuffle_means = []
    for _ in range(N_PERM):
        sample = rng.choice(rand_window_rets, size=n, replace=False)
        shuffle_means.append(sample.mean())
    shuffle_means = np.array(shuffle_means)
    p = (shuffle_means >= actual_mean).sum() / N_PERM

    print(f"\n=== {ticker} {entry_time}/{vol_thresh:.0%}/{ref} | {period_label} ===")
    print(f"  訊號 n: {n}")
    print(f"  實際 mean: {actual_mean:+.3f}%")
    print(f"  Random 中位: {np.median(shuffle_means):+.3f}%")
    print(f"  Random 95% CI: [{np.percentile(shuffle_means, 5):+.3f}%, "
          f"{np.percentile(shuffle_means, 95):+.3f}%]")
    print(f"  P-value: {p:.4f}")
    if p < 0.001:
        print(f"  → ⭐⭐⭐ 極度顯著 (p<0.001)")
    elif p < 0.01:
        print(f"  → ⭐⭐ 顯著 (p<0.01)")
    elif p < 0.05:
        print(f"  → ⭐ 邊緣 (p<0.05)")
    else:
        print(f"  → ❌ 未過")


def main():
    print("=" * 80)
    print("ORB MCPT 嚴格驗證")
    print("=" * 80)

    cases = [
        # (ticker, entry, vol, ref)
        ("2408", "09:15", 0.30, "open5"),
        ("2485", "09:45", 0.30, "open15"),
    ]
    periods = [
        ("Full 9 years", date(2017, 1, 1), date(2026, 4, 30)),
        ("Bull (2017-2020)", date(2017, 1, 1), date(2020, 12, 31)),
        ("Bull-D (2023-2026)", date(2023, 1, 1), date(2026, 4, 30)),
        ("Bear (2021-2022)", date(2021, 1, 1), date(2022, 12, 31)),
    ]
    for tk, et, vt, ref in cases:
        for label, start, end in periods:
            mcpt_orb(tk, et, vt, ref, label, start, end)


if __name__ == "__main__":
    main()
