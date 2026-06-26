"""
ORB Parameter Sweep — 用既有 31 ticker minute cache 跑多參數變體 + whitelist。

變體（4 × 3 × 2 = 24 組合）:
  entry_time:   09:15 / 09:30 / 09:45 / 10:00
  vol_threshold: 0.25 / 0.30 / 0.40
  breakout_ref: open5_high (09:00-09:05) / open15_high (09:00-09:15)

對每組合 × 31 ticker 跑：
  - n
  - mean net return (扣 0.34%)
  - win rate
  - bootstrap 95% CI
  - train (cutoff 2025-06-01) vs test 一致性

輸出:
  logs/orb_param_sweep.csv         全參數 × ticker 統計
  logs/orb_param_sweep_summary.md  Tier A/B whitelist 推薦
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategy.volume_anomaly_scanner import lookup_ticker_name  # noqa: E402

CACHE = ROOT / "data" / "cache" / "finmind" / "minute"
NEW_COST = 0.34
CUTOFF = pd.Timestamp("2025-06-01")
N_BOOT = 500
SEED = 42

ENTRY_TIMES = ["09:15", "09:30", "09:45", "10:00"]
VOL_THRESHOLDS = [0.25, 0.30, 0.40]
BREAKOUT_REFS = ["open5", "open15"]
EXIT_TIME = "13:20"


def load_ticker_minute(ticker: str) -> pd.DataFrame:
    files = sorted(CACHE.glob(f"{ticker}_*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date_only"] = df["dt"].dt.date
    df["minute_str"] = df["dt"].dt.strftime("%H:%M")
    return df.sort_values("dt").reset_index(drop=True)


def detect_orb(
    day_df: pd.DataFrame,
    prev_day_total_vol: float,
    entry_time: str,
    vol_threshold: float,
    breakout_ref: str,
) -> dict | None:
    if day_df.empty or prev_day_total_vol <= 0:
        return None

    if breakout_ref == "open5":
        ref_window = day_df[day_df["minute_str"] <= "09:04"]
    else:
        ref_window = day_df[day_df["minute_str"] <= "09:14"]
    if ref_window.empty:
        return None
    ref_high = float(ref_window["high"].max())

    cum_window = day_df[day_df["minute_str"] < entry_time]
    if cum_window.empty:
        return None
    cum_vol = float(cum_window["volume"].sum())
    vol_ratio = cum_vol / prev_day_total_vol

    bar_entry = day_df[day_df["minute_str"] == entry_time]
    if bar_entry.empty:
        for delta in [-1, 1, -2, 2]:
            h, m = entry_time.split(":")
            adj = f"{h}:{int(m) + delta:02d}"
            bar_entry = day_df[day_df["minute_str"] == adj]
            if not bar_entry.empty:
                break
        if bar_entry.empty:
            return None
    entry_close = float(bar_entry["close"].iloc[0])

    if vol_ratio < vol_threshold:
        return None
    if entry_close <= ref_high:
        return None

    exit_price = None
    for tt in [EXIT_TIME, "13:19", "13:21", "13:25", "13:30"]:
        bar = day_df[day_df["minute_str"] == tt]
        if not bar.empty:
            exit_price = float(bar["close"].iloc[0])
            break
    if exit_price is None:
        exit_price = float(day_df.iloc[-1]["close"])

    return {
        "entry_price": entry_close,
        "exit_price": exit_price,
        "gross_return_pct": (exit_price / entry_close - 1) * 100,
        "vol_ratio": vol_ratio,
    }


def scan_variant(
    minute_data: dict[str, pd.DataFrame],
    entry_time: str,
    vol_threshold: float,
    breakout_ref: str,
) -> pd.DataFrame:
    rows = []
    for tk, full_df in minute_data.items():
        if full_df.empty:
            continue
        daily_vol = full_df.groupby("date_only")["volume"].sum().to_dict()
        unique_days = sorted(full_df["date_only"].unique())
        for i, d in enumerate(unique_days):
            if i == 0:
                continue
            prev_total = daily_vol.get(unique_days[i - 1], 0)
            day_df = full_df[full_df["date_only"] == d]
            sig = detect_orb(day_df, prev_total, entry_time, vol_threshold, breakout_ref)
            if sig:
                rows.append({"ticker": tk, "date": pd.Timestamp(d), **sig})
    return pd.DataFrame(rows)


def stats_per_ticker(df_signals: pd.DataFrame) -> pd.DataFrame:
    if df_signals.empty:
        return pd.DataFrame()
    df_signals = df_signals.copy()
    df_signals["net"] = df_signals["gross_return_pct"] - NEW_COST

    rng = np.random.default_rng(SEED)
    out = []
    for tk, sub in df_signals.groupby("ticker"):
        n = len(sub)
        if n < 8:
            continue
        rets = sub["net"].values
        train = sub[sub["date"] < CUTOFF]["net"].values
        test = sub[sub["date"] >= CUTOFF]["net"].values
        boot = np.array([rng.choice(rets, size=n, replace=True).mean() for _ in range(N_BOOT)])
        ci_low, ci_high = np.percentile(boot, [2.5, 97.5])
        out.append({
            "ticker": tk,
            "n": n,
            "full_mean": rets.mean(),
            "full_win": (rets > 0).mean() * 100,
            "train_n": len(train),
            "train_mean": train.mean() if len(train) else np.nan,
            "test_n": len(test),
            "test_mean": test.mean() if len(test) else np.nan,
            "test_win": (test > 0).mean() * 100 if len(test) else np.nan,
            "ci_low": ci_low,
            "ci_high": ci_high,
        })
    return pd.DataFrame(out)


def main() -> None:
    tickers = sorted({p.stem.split("_")[0] for p in CACHE.glob("*.parquet")})
    print(f"=== ORB Param Sweep — {len(tickers)} ticker × {len(ENTRY_TIMES)*len(VOL_THRESHOLDS)*len(BREAKOUT_REFS)} variants ===")

    print("\n[1/3] 載入 minute cache...")
    t0 = time.time()
    minute_data: dict[str, pd.DataFrame] = {}
    for tk in tickers:
        df = load_ticker_minute(tk)
        minute_data[tk] = df
        if not df.empty:
            print(f"  {tk}: {len(df):,} rows ({df['date_only'].nunique()} days)")
    print(f"  載入完成 {time.time()-t0:.1f}s")

    print("\n[2/3] 跑參數變體...")
    all_results = []
    variant_id = 0
    for entry_time in ENTRY_TIMES:
        for vol_t in VOL_THRESHOLDS:
            for ref in BREAKOUT_REFS:
                variant_id += 1
                t0 = time.time()
                sigs = scan_variant(minute_data, entry_time, vol_t, ref)
                stats = stats_per_ticker(sigs)
                if not stats.empty:
                    stats["entry_time"] = entry_time
                    stats["vol_threshold"] = vol_t
                    stats["breakout_ref"] = ref
                    all_results.append(stats)
                n_sig = len(sigs)
                n_passers = ((stats["test_mean"] > 0) & (stats["ci_low"] > 0)).sum() if not stats.empty else 0
                print(f"  [{variant_id:>2}/24] entry={entry_time} vol≥{vol_t:.0%} ref={ref:<7} "
                      f"signals={n_sig:>4}, n>=8 ticker={len(stats):>2}, Tier-A passers={n_passers} "
                      f"({time.time()-t0:.1f}s)")

    if not all_results:
        print("❌ 全變體無 signal")
        return
    full = pd.concat(all_results, ignore_index=True)
    full["name"] = full["ticker"].apply(lambda t: lookup_ticker_name(str(t)) or "")

    out_csv = ROOT / "logs" / "orb_param_sweep.csv"
    full.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[3/3] 寫入 {out_csv.relative_to(ROOT)} ({len(full)} rows)")

    # Tier A: test_mean > 0 AND ci_low > 0 AND test_n >= 5
    tier_a = full[(full["test_mean"] > 0) & (full["ci_low"] > 0) & (full["test_n"] >= 5)].copy()
    tier_b = full[(full["test_mean"] > 0) & (full["test_n"] >= 5) & ~full.index.isin(tier_a.index)].copy()

    tier_a = tier_a.sort_values(["ticker", "test_mean"], ascending=[True, False])
    tier_b = tier_b.sort_values("test_mean", ascending=False)

    md = ["# ORB Parameter Sweep — Whitelist\n",
          f"Universe: {len(tickers)} ticker | Variants: 24 | Cost: {NEW_COST}% / 筆\n"]

    md.append(f"\n## Tier A — 統計顯著 + OOS 持續 ({len(tier_a)} 組合)\n")
    md.append("| ticker | name | entry | vol≥ | ref | n | OOS mean/win | full mean/win | 95% CI |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for _, r in tier_a.iterrows():
        md.append(
            f"| {r['ticker']} | {r['name']} | {r['entry_time']} | {r['vol_threshold']:.0%} | "
            f"{r['breakout_ref']} | {r['n']} | {r['test_mean']:+.2f}%/{r['test_win']:.0f}% (n={r['test_n']}) | "
            f"{r['full_mean']:+.2f}%/{r['full_win']:.0f}% | "
            f"[{r['ci_low']:+.2f}, {r['ci_high']:+.2f}] |"
        )

    md.append(f"\n## Tier B — OOS mean>0 但 CI 跨 0 ({len(tier_b)} 組合)\n")
    md.append("| ticker | name | entry | vol≥ | ref | OOS mean/win | full mean/win |")
    md.append("|---|---|---|---|---|---|---|")
    for _, r in tier_b.head(30).iterrows():
        md.append(
            f"| {r['ticker']} | {r['name']} | {r['entry_time']} | {r['vol_threshold']:.0%} | "
            f"{r['breakout_ref']} | {r['test_mean']:+.2f}%/{r['test_win']:.0f}% (n={r['test_n']}) | "
            f"{r['full_mean']:+.2f}%/{r['full_win']:.0f}% |"
        )

    # Best ticker overall (any variant Tier A)
    md.append("\n## 通過 Tier A 的 ticker（去重）\n")
    a_tk = tier_a["ticker"].unique().tolist()
    if a_tk:
        for tk in a_tk:
            sub = tier_a[tier_a["ticker"] == tk].sort_values("test_mean", ascending=False).iloc[0]
            md.append(f"- **{tk} {sub['name']}** — best: entry={sub['entry_time']}, "
                      f"vol≥{sub['vol_threshold']:.0%}, ref={sub['breakout_ref']}, "
                      f"OOS {sub['test_mean']:+.2f}%/{sub['test_win']:.0f}%")
    else:
        md.append("（無）")

    out_md = ROOT / "logs" / "orb_param_sweep_summary.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"寫入 {out_md.relative_to(ROOT)}")

    print(f"\nTier A 組合: {len(tier_a)}")
    print(f"Tier A 不重複 ticker: {len(tier_a['ticker'].unique())}")
    print(f"Tier B 組合: {len(tier_b)}")


if __name__ == "__main__":
    main()
