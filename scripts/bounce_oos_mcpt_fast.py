"""Fast OOS + MCPT — preloads ALL parquet data once, then runs scan in-memory.

~50-100x faster than naive version which reads parquet per (ticker, day).
"""
from __future__ import annotations
import sys, io, time
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np

TW = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
OUT = ROOT / "docs" / "bounce_oos_mcpt.md"
OUT.parent.mkdir(exist_ok=True)

# Filter params (must match bounce_candidate_scanner)
RET_5D_MIN, RET_5D_MAX   = -25.0, -8.0
RET_10D_MIN, RET_10D_MAX = -25.0, -12.0
RSI_THRESHOLD            = 35.0
MIN_AVG_DOLLAR_VOL       = 5_000_000
MAX_DRAWDOWN_60D         = -35.0

WINDOW_DAYS    = 90
HOLD_DAYS      = 5
COST_RT        = 0.78
N_PERMUTATIONS = 5000


def preload() -> dict[str, pd.DataFrame]:
    print(f"Preloading parquets...")
    t0 = time.time()
    cache = {}
    files = list(TW.glob("*.parquet"))
    files = [f for f in files if f.stem.isdigit() and len(f.stem) == 4 and not f.stem.startswith("00")]
    for i, f in enumerate(files):
        if i % 500 == 0:
            print(f"  {i}/{len(files)}")
        try:
            df = pd.read_parquet(f)
            if df.empty or len(df) < 70:
                continue
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df.sort_values("date").reset_index(drop=True)
            df["close"] = df["close"].astype(float)
            df["volume"] = df["volume"].astype(float)
            # Precompute RSI, MA20, MA60, vol stats
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            df["rsi14"] = 100 - 100 / (1 + rs)
            df["ma20"] = df["close"].rolling(20).mean()
            df["ma60"] = df["close"].rolling(60).mean()
            df["dollar_vol"] = df["close"] * df["volume"]
            cache[f.stem] = df
        except Exception:
            continue
    print(f"  {len(cache)} tickers loaded in {time.time()-t0:.1f}s")
    return cache


def collect_trades(cache: dict, end_date: date, n_days: int) -> pd.DataFrame:
    print(f"Collecting trades over {n_days} days ending {end_date}...")
    t0 = time.time()
    rows = []
    days_examined = 0
    for i in range(n_days, HOLD_DAYS, -1):
        d = end_date - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        days_examined += 1
        # Find candidates for date d
        for tk, df in cache.items():
            try:
                idx = df.index[df["date"] == d]
                if len(idx) == 0:
                    continue
                i_idx = idx[0]
                if i_idx < 65 or i_idx + HOLD_DAYS + 1 >= len(df):
                    continue
                close_today = float(df["close"].iloc[i_idx])
                ma20 = float(df["ma20"].iloc[i_idx])
                ma60 = float(df["ma60"].iloc[i_idx])
                rsi = float(df["rsi14"].iloc[i_idx])
                if pd.isna(rsi) or rsi >= RSI_THRESHOLD:
                    continue
                if close_today >= ma20:
                    continue
                if close_today <= ma60:
                    continue

                # Returns
                ret_5d  = (close_today / float(df["close"].iloc[i_idx - 5])  - 1) * 100
                ret_10d = (close_today / float(df["close"].iloc[i_idx - 10]) - 1) * 100

                # Filter on returns
                cond_5d  = RET_5D_MIN  <= ret_5d  <= RET_5D_MAX
                cond_10d = RET_10D_MIN <= ret_10d <= RET_10D_MAX
                if not (cond_5d or cond_10d):
                    continue

                # Liquidity
                avg_dv = float(df["dollar_vol"].iloc[i_idx - 59 : i_idx + 1].mean())
                if avg_dv < MIN_AVG_DOLLAR_VOL:
                    continue

                # Max drawdown 60d
                window = df["close"].iloc[i_idx - 59 : i_idx + 1]
                running_max = window.cummax()
                dd = ((window - running_max) / running_max * 100).min()
                if dd < MAX_DRAWDOWN_60D:
                    continue

                # Score
                rsi_score = max(0, (35 - rsi)) * 2.0
                drop_score = min(50, abs(min(ret_5d, ret_10d)) * 1.5)
                ma60_dist = (close_today / ma60 - 1) * 100
                ma60_score = min(20, max(0, ma60_dist))
                score = rsi_score + drop_score + ma60_score

                # Forward 5d return
                entry_price = close_today * 1.005
                exit_price = float(df["close"].iloc[i_idx + 1 + HOLD_DAYS])
                pnl = (exit_price / entry_price - 1) * 100 - COST_RT

                rows.append({
                    "trade_date": d,
                    "ticker":     tk,
                    "entry":      entry_price,
                    "exit":       exit_price,
                    "pnl":        pnl,
                    "rsi":        rsi,
                    "ret_5d":     ret_5d,
                    "ret_10d":    ret_10d,
                    "score":      score,
                    "ma60_dist":  ma60_dist,
                })
            except Exception:
                continue
    print(f"  {days_examined} days scanned in {time.time()-t0:.1f}s, {len(rows)} trades")
    return pd.DataFrame(rows)


def oos_walkforward(df: pd.DataFrame, n_splits: int = 3) -> list[dict]:
    if df.empty or len(df) < n_splits * 5:
        return []
    df_s = df.sort_values("trade_date").reset_index(drop=True)
    n = len(df_s)
    boundaries = [int(n * i / n_splits) for i in range(n_splits + 1)]
    out = []
    for i in range(n_splits):
        c = df_s.iloc[boundaries[i]:boundaries[i+1]]
        if c.empty:
            continue
        arr = c["pnl"].values
        out.append({
            "period":  f"{c['trade_date'].min()} ~ {c['trade_date'].max()}",
            "n":       len(arr),
            "mean":    float(arr.mean()),
            "median":  float(np.median(arr)),
            "wr":      float((arr > 0).mean() * 100),
            "std":     float(arr.std()),
            "t_stat":  float(arr.mean() / (arr.std() / max(len(arr), 1) ** 0.5)) if arr.std() > 0 else 0,
        })
    return out


def mcpt(arr: np.ndarray, n_perm: int = 5000) -> dict:
    rng = np.random.default_rng(42)
    n = len(arr)
    actual = float(arr.mean())
    null_means = np.empty(n_perm)
    for k in range(n_perm):
        signs = rng.choice([-1, 1], size=n)
        null_means[k] = (arr * signs).mean()
    return {
        "actual":   actual,
        "null_mu":  float(null_means.mean()),
        "null_std": float(null_means.std()),
        "p_value":  float((null_means >= actual).mean()),
        "z":        float((actual - null_means.mean()) / null_means.std()) if null_means.std() > 0 else 0,
        "n_perm":   n_perm,
    }


def by_quintile(df: pd.DataFrame) -> list[dict]:
    if len(df) < 25:
        return []
    df_s = df.sort_values("score", ascending=False).reset_index(drop=True)
    n = len(df_s)
    boundaries = [int(n * i / 5) for i in range(6)]
    out = []
    for i in range(5):
        c = df_s.iloc[boundaries[i]:boundaries[i+1]]
        arr = c["pnl"].values
        out.append({
            "q":     f"Q{i+1}",
            "n":     len(arr),
            "mean":  float(arr.mean()),
            "wr":    float((arr > 0).mean() * 100),
            "smin":  float(c["score"].min()),
            "smax":  float(c["score"].max()),
        })
    return out


def main():
    cache = preload()
    end_date = date(2026, 5, 6)
    df = collect_trades(cache, end_date, WINDOW_DAYS)
    print(f"\nTotal trades: {len(df)}")
    if df.empty:
        return

    arr = df["pnl"].values
    overall = {
        "n":      len(arr),
        "mean":   float(arr.mean()),
        "median": float(np.median(arr)),
        "wr":     float((arr > 0).mean() * 100),
        "std":    float(arr.std()),
        "t":      float(arr.mean() / (arr.std() / len(arr) ** 0.5)) if arr.std() > 0 else 0,
    }

    print(f"[1/3] OOS walk-forward...")
    oos = oos_walkforward(df, n_splits=3)
    print(f"[2/3] MCPT n={N_PERMUTATIONS}...")
    m = mcpt(arr, N_PERMUTATIONS)
    print(f"[3/3] Quintile analysis...")
    q = by_quintile(df)

    md = ["# Bounce Strategy — OOS + MCPT Validation", ""]
    md.append(f"Window: last {WINDOW_DAYS} days ending {end_date}")
    md.append(f"Hold: {HOLD_DAYS} day, Cost: {COST_RT}% RT")
    md.append(f"Universe: {len(cache):,} TW stocks (4-digit ex-ETFs)")
    md.append("")
    md.append("## Overall")
    md.append("| Metric | Value |")
    md.append("|---|---:|")
    md.append(f"| n trades | **{overall['n']}** |")
    md.append(f"| Mean | **{overall['mean']:+.2f}%** |")
    md.append(f"| Median | {overall['median']:+.2f}% |")
    md.append(f"| WR | {overall['wr']:.0f}% |")
    md.append(f"| Std | {overall['std']:.2f}% |")
    md.append(f"| t-stat | **{overall['t']:+.2f}** |")
    md.append("")

    md.append("## OOS Walk-Forward (3 splits)")
    md.append("| Period | n | Mean | Median | WR | t-stat |")
    md.append("|---|---:|---:|---:|---:|---:|")
    pass_count = 0
    for p in oos:
        ok = p["mean"] > 0 and p["wr"] >= 50
        if ok:
            pass_count += 1
        md.append(f"| {p['period']} | {p['n']} | "
                  f"{'**' if ok else ''}{p['mean']:+.2f}%{'**' if ok else ''} | "
                  f"{p['median']:+.2f}% | {p['wr']:.0f}% | {p['t_stat']:+.2f} |")
    md.append("")
    md.append(f"**OOS Verdict**: {pass_count}/{len(oos)} pass")
    md.append("")

    md.append(f"## MCPT (n={N_PERMUTATIONS:,} permutations)")
    md.append("| Metric | Value |")
    md.append("|---|---:|")
    md.append(f"| Actual mean | **{m['actual']:+.3f}%** |")
    md.append(f"| Null mean | {m['null_mu']:+.3f}% |")
    md.append(f"| Null std | {m['null_std']:.3f}% |")
    md.append(f"| Z-score | **{m['z']:+.2f}** |")
    md.append(f"| **p-value** | **{m['p_value']:.4f}** |")
    md.append("")
    if m["p_value"] < 0.001:
        md.append("✅ **p < 0.001** — Highly significant")
    elif m["p_value"] < 0.01:
        md.append("✅ **p < 0.01** — Significant")
    elif m["p_value"] < 0.05:
        md.append("🟡 **p < 0.05** — Marginal")
    else:
        md.append("🔴 **p ≥ 0.05** — NOT significant")
    md.append("")

    md.append("## Score Quintile (Q1=top score)")
    md.append("| Quintile | n | Mean | WR | Score range |")
    md.append("|---|---:|---:|---:|---:|")
    for x in q:
        md.append(f"| {x['q']} | {x['n']} | {x['mean']:+.2f}% | {x['wr']:.0f}% | "
                  f"{x['smin']:.0f}-{x['smax']:.0f} |")
    md.append("")
    if q and len(q) >= 2:
        spread = q[0]["mean"] - q[-1]["mean"]
        if spread > 2:
            md.append(f"✅ Score system valid: Q1-Q5 spread {spread:+.1f}pp")
        else:
            md.append(f"🟡 Score weak: Q1-Q5 spread {spread:+.1f}pp")
    md.append("")

    OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"\nReport: {OUT}")
    print(f"  n={overall['n']}, mean={overall['mean']:+.2f}%, t={overall['t']:+.2f}")
    print(f"  OOS: {pass_count}/{len(oos)} pass")
    print(f"  MCPT p={m['p_value']:.4f}")


if __name__ == "__main__":
    main()
