"""
ORB 跨牛熊 9 年驗證 (2017-2026)

對 2408 / 2485 重跑 ORB backtest 並分時段驗證：
  - Period A: 2017-2019 (混合熊+多)
  - Period B: 2020 (covid 巨震)
  - Period C: 2021-2022 (2021 牛 + 2022 熊 -22%)
  - Period D: 2023-2026 (大牛市)

看每個 period 真 alpha 是否持續。
"""
from __future__ import annotations
import io, sys
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


def load_minute(ticker: str) -> pd.DataFrame:
    files = sorted(CACHE.glob(f"{ticker}_*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date_only"] = df["dt"].dt.date
    df["minute_str"] = df["dt"].dt.strftime("%H:%M")
    return df.sort_values("dt").reset_index(drop=True)


def detect_orb(day_df, prev_vol, entry_time, vol_thresh, ref):
    if day_df.empty or prev_vol <= 0:
        return None
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
    if vol_ratio < vol_thresh or entry <= ref_high:
        return None
    # exit 13:20
    exit_p = None
    for tt in ["13:20", "13:19", "13:21", "13:25", "13:30"]:
        b = day_df[day_df["minute_str"] == tt]
        if not b.empty:
            exit_p = float(b["close"].iloc[0]); break
    if exit_p is None:
        exit_p = float(day_df.iloc[-1]["close"])
    return {"entry_price": entry, "exit_price": exit_p,
            "gross_pct": (exit_p / entry - 1) * 100}


def scan(df: pd.DataFrame, entry_time: str, vol_thresh: float, ref: str):
    daily_vol = df.groupby("date_only")["volume"].sum().to_dict()
    days = sorted(df["date_only"].unique())
    rows = []
    for i, d in enumerate(days):
        if i == 0: continue
        prev = daily_vol.get(days[i-1], 0)
        sig = detect_orb(df[df["date_only"] == d], prev, entry_time, vol_thresh, ref)
        if sig:
            rows.append({"date": pd.Timestamp(d), **sig})
    return pd.DataFrame(rows)


def random_window_returns(df: pd.DataFrame) -> tuple:
    """同 ticker 每天 09:15 進場、13:20 出場 (跟 ORB 同 hold 期) 的 random baseline"""
    days = sorted(df["date_only"].unique())
    rets = []
    valid_days = []
    for d in days:
        day_df = df[df["date_only"] == d]
        b915 = day_df[day_df["minute_str"] == "09:15"]
        b1320 = day_df[day_df["minute_str"] == "13:20"]
        if not b915.empty and not b1320.empty:
            entry = float(b915["close"].iloc[0])
            exit_p = float(b1320["close"].iloc[0])
            rets.append((exit_p / entry - 1) * 100)
            valid_days.append(d)
    return np.array(rets), valid_days


def stratify(df_sigs, rand_rets, all_days, periods):
    """各時段分別算 sigma."""
    results = {}
    for label, start, end in periods:
        sig_p = df_sigs[(df_sigs["date"] >= pd.Timestamp(start)) &
                        (df_sigs["date"] <= pd.Timestamp(end))]
        sig_net = (sig_p["gross_pct"] - COST).values
        rand_p_idx = [i for i, d in enumerate(all_days) if start <= d <= end]
        if not rand_p_idx or len(sig_net) < 2:
            results[label] = None; continue
        rand_p = rand_rets[rand_p_idx] - COST
        sig_mean = sig_net.mean()
        rand_mean = rand_p.mean()
        rand_std = rand_p.std()
        alpha = sig_mean - rand_mean
        sigma = (alpha / (rand_std / np.sqrt(len(sig_net)))) if rand_std > 0 else 0
        win = (sig_net > 0).mean() * 100
        results[label] = {
            "n_sig": len(sig_net), "sig_mean": sig_mean,
            "rand_mean": rand_mean, "alpha": alpha,
            "sigma": sigma, "win": win,
            "n_rand": len(rand_p),
        }
    return results


def main():
    from datetime import date
    periods = [
        ("A 2017-2019", date(2017, 1, 1), date(2019, 12, 31)),
        ("B 2020 covid", date(2020, 1, 1), date(2020, 12, 31)),
        ("C 2021-2022", date(2021, 1, 1), date(2022, 12, 31)),
        ("D 2023-2026 牛市", date(2023, 1, 1), date(2026, 4, 30)),
    ]

    # 規則：用 2408 / 2485 各自 best variant
    cases = [
        ("2408", "09:15", 0.30, "open5",  "南亞科 09:15/30%/open5"),
        ("2408", "09:15", 0.25, "open5",  "南亞科 09:15/25%/open5"),
        ("2485", "09:45", 0.30, "open15", "兆赫 09:45/30%/open15"),
    ]

    print("=" * 110)
    print("ORB 跨牛熊 9 年驗證 (2017-2026)")
    print("=" * 110)

    for tk, et, vt, ref, label in cases:
        df = load_minute(tk)
        if df.empty:
            print(f"\n❌ {tk} 無資料"); continue
        rand_rets, all_days = random_window_returns(df)
        sigs = scan(df, et, vt, ref)
        if sigs.empty:
            print(f"\n❌ {tk} 無訊號"); continue

        print(f"\n=== {label} (全期 {len(sigs)} 訊號) ===")
        print(f"{'period':<22} {'n_sig':>5} {'sig':>8} {'rand':>8} {'alpha':>8} "
              f"{'sigma':>7} {'win':>5} {'verdict':>10}")
        results = stratify(sigs, rand_rets, all_days, periods)
        for p_label, r in results.items():
            if r is None:
                print(f"  {p_label:<20}  無訊號 / sample 不夠"); continue
            v = "✅ robust" if r["sigma"] > 1.96 and r["alpha"] > 0 else (
                "⚠️ 弱" if r["alpha"] > 0 else "❌ 假")
            print(f"  {p_label:<20} {r['n_sig']:>5} {r['sig_mean']:>+6.2f}% "
                  f"{r['rand_mean']:>+6.2f}% {r['alpha']:>+6.2f}% "
                  f"{r['sigma']:>+6.2f} {r['win']:>4.0f}% {v:>10}")


if __name__ == "__main__":
    main()
