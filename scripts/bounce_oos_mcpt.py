"""Bounce strategy OOS + MCPT validation.

Two tests:

1. OOS walk-forward: split sample into N sub-periods, check each sub-period
   independently shows positive mean alpha.

2. MCPT (sign permutation): for the 110 trades, randomly flip return signs
   N=1000 times, compare actual mean to null distribution.

Run:
  python -m scripts.bounce_oos_mcpt
"""
from __future__ import annotations
import sys, io
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
from scripts.bounce_candidate_scanner import scan, TW

OUT = ROOT / "docs" / "bounce_oos_mcpt.md"
OUT.parent.mkdir(exist_ok=True)

# Backtest config
WINDOW_DAYS    = 90      # extend to 90 days for more sample
HOLD_DAYS      = 5
COST_RT        = 0.78
N_PERMUTATIONS = 5000


def collect_trades(end_date: date, n_days: int) -> pd.DataFrame:
    """Run scanner each day in window, compute 5d-forward PnL for each candidate."""
    print(f"Collecting trades over {n_days} days ending {end_date}...")
    rows = []
    skipped_days = 0
    for i in range(n_days, HOLD_DAYS, -1):  # leave HOLD_DAYS buffer
        d = end_date - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        df = scan(d)
        if df.empty:
            skipped_days += 1
            continue
        for _, r in df.iterrows():
            tk = str(r["ticker"])
            entry_price = r["close"] * 1.005
            try:
                p = pd.read_parquet(TW / f"{tk}.parquet")
            except Exception:
                continue
            p["date"] = pd.to_datetime(p["date"]).dt.date
            p = p.sort_values("date").reset_index(drop=True)
            sig_idx = p.index[p["date"] == d]
            if len(sig_idx) == 0:
                continue
            exit_idx = sig_idx[0] + 1 + HOLD_DAYS  # T+1 entry + HOLD_DAYS
            if exit_idx >= len(p):
                continue
            exit_price = float(p["close"].iloc[exit_idx])
            pnl = (exit_price / entry_price - 1) * 100 - COST_RT
            rows.append({
                "trade_date": d,
                "ticker":     tk,
                "entry":      entry_price,
                "exit":       exit_price,
                "pnl":        pnl,
                "rsi":        r["rsi14"],
                "ret_5d":     r["ret_5d"],
                "score":      r["score"],
                "ma60_dist":  r["ma60_dist"],
            })
    return pd.DataFrame(rows)


def oos_walkforward(df: pd.DataFrame, n_splits: int = 3) -> list[dict]:
    """Split sample into N non-overlapping sub-periods, compute stats per period."""
    if df.empty or len(df) < n_splits * 5:
        return []
    df_s = df.sort_values("trade_date").reset_index(drop=True)
    chunks = np.array_split(df_s, n_splits)
    out = []
    for i, c in enumerate(chunks):
        if c.empty:
            continue
        arr = c["pnl"].values
        d_start = c["trade_date"].min()
        d_end   = c["trade_date"].max()
        out.append({
            "period":   f"{d_start} ~ {d_end}",
            "n":        len(arr),
            "mean":     float(arr.mean()),
            "median":   float(np.median(arr)),
            "wr":       float((arr > 0).mean() * 100),
            "std":      float(arr.std()),
            "t_stat":   float(arr.mean() / (arr.std() / max(len(arr), 1) ** 0.5)) if arr.std() > 0 else 0,
            "min":      float(arr.min()),
            "max":      float(arr.max()),
        })
    return out


def mcpt_sign_permutation(arr: np.ndarray, n_permutations: int = 5000) -> dict:
    """Sign-permutation MCPT.
    H0: trade returns are random (mean 0).
    For each permutation: randomly flip signs of returns. Compute mean.
    p-value = P(permuted_mean >= actual_mean)
    """
    rng = np.random.default_rng(42)
    n = len(arr)
    actual_mean = float(arr.mean())
    null_means = np.empty(n_permutations)
    for k in range(n_permutations):
        signs = rng.choice([-1, 1], size=n)
        null_means[k] = (arr * signs).mean()
    p_value = float((null_means >= actual_mean).mean())
    return {
        "actual_mean":        actual_mean,
        "null_mean":          float(null_means.mean()),
        "null_std":           float(null_means.std()),
        "p_value":            p_value,
        "n_permutations":     n_permutations,
        "z_score":            (actual_mean - null_means.mean()) / null_means.std() if null_means.std() > 0 else 0,
    }


def by_score_decile(df: pd.DataFrame) -> list[dict]:
    """Show how alpha varies with score quintiles."""
    df_s = df.sort_values("score", ascending=False).reset_index(drop=True)
    n = len(df_s)
    if n < 25:
        return []
    chunks = np.array_split(df_s, 5)  # quintiles
    out = []
    for i, c in enumerate(chunks):
        arr = c["pnl"].values
        out.append({
            "quintile": f"Q{i+1} (top→bot)",
            "n":        len(arr),
            "mean":     float(arr.mean()),
            "wr":       float((arr > 0).mean() * 100),
            "score_min": float(c["score"].min()),
            "score_max": float(c["score"].max()),
        })
    return out


def main():
    end_date = date(2026, 5, 6)
    df = collect_trades(end_date, WINDOW_DAYS)
    print(f"\nTotal trades collected: {len(df)}")
    if df.empty:
        return

    arr = df["pnl"].values
    overall = {
        "n":      len(arr),
        "mean":   float(arr.mean()),
        "median": float(np.median(arr)),
        "wr":     float((arr > 0).mean() * 100),
        "std":    float(arr.std()),
        "t_stat": float(arr.mean() / (arr.std() / max(len(arr), 1) ** 0.5)),
    }

    # OOS
    print(f"\n[1/3] OOS walk-forward (3 splits)...")
    oos = oos_walkforward(df, n_splits=3)

    # MCPT
    print(f"[2/3] MCPT sign permutation (n={N_PERMUTATIONS})...")
    mcpt_all = mcpt_sign_permutation(arr, n_permutations=N_PERMUTATIONS)

    # By quintile
    print(f"[3/3] Score quintile analysis...")
    quint = by_score_decile(df)

    # ── Report ──────────────────────────────────────────────────────────────
    md = ["# Bounce Strategy — OOS + MCPT Validation", ""]
    md.append(f"Window: {WINDOW_DAYS} trading days ending {end_date}")
    md.append(f"Hold: {HOLD_DAYS} days, Cost: {COST_RT}% RT")
    md.append("")

    md.append("## Overall stats")
    md.append("| Metric | Value |")
    md.append("|---|---:|")
    md.append(f"| n trades | **{overall['n']}** |")
    md.append(f"| Mean PnL | **{overall['mean']:+.2f}%** |")
    md.append(f"| Median | {overall['median']:+.2f}% |")
    md.append(f"| Win rate | {overall['wr']:.0f}% |")
    md.append(f"| Std | {overall['std']:.2f}% |")
    md.append(f"| t-stat | **{overall['t_stat']:+.2f}** |")
    md.append("")

    md.append("## OOS walk-forward (3 sub-periods)")
    md.append("| Period | n | Mean | Median | WR | t-stat |")
    md.append("|---|---:|---:|---:|---:|---:|")
    pass_count = 0
    for p in oos:
        passed = p["mean"] > 0 and p["wr"] >= 50
        if passed:
            pass_count += 1
        md.append(f"| {p['period']} | {p['n']} | "
                  f"{'**' if passed else ''}{p['mean']:+.2f}%{'**' if passed else ''} | "
                  f"{p['median']:+.2f}% | {p['wr']:.0f}% | {p['t_stat']:+.2f} |")
    md.append("")
    md.append(f"**OOS Verdict**: {pass_count}/{len(oos)} periods pass (mean > 0 AND WR ≥ 50%)")
    md.append("")

    md.append("## MCPT sign permutation (n={:,} iterations)".format(N_PERMUTATIONS))
    md.append("| Metric | Value |")
    md.append("|---|---:|")
    md.append(f"| Actual mean | **{mcpt_all['actual_mean']:+.3f}%** |")
    md.append(f"| Null mean | {mcpt_all['null_mean']:+.3f}% |")
    md.append(f"| Null std | {mcpt_all['null_std']:.3f}% |")
    md.append(f"| Z-score | **{mcpt_all['z_score']:+.2f}** |")
    md.append(f"| **p-value** | **{mcpt_all['p_value']:.4f}** |")
    md.append("")
    if mcpt_all["p_value"] < 0.001:
        md.append("✅ **p < 0.001** — Strategy alpha is highly statistically significant")
    elif mcpt_all["p_value"] < 0.01:
        md.append("✅ **p < 0.01** — Strategy alpha is statistically significant")
    elif mcpt_all["p_value"] < 0.05:
        md.append("🟡 **p < 0.05** — Marginally significant")
    else:
        md.append("🔴 **p ≥ 0.05** — Not statistically significant")
    md.append("")

    md.append("## Score quintile breakdown (top vs bottom score)")
    md.append("| Quintile | n | Mean | WR | Score range |")
    md.append("|---|---:|---:|---:|---:|")
    for q in quint:
        md.append(f"| {q['quintile']} | {q['n']} | "
                  f"{q['mean']:+.2f}% | {q['wr']:.0f}% | "
                  f"{q['score_min']:.0f}-{q['score_max']:.0f} |")
    md.append("")
    if quint and len(quint) >= 2:
        top_mean = quint[0]["mean"]
        bot_mean = quint[-1]["mean"]
        if top_mean > bot_mean + 2:
            md.append(f"✅ Score system valid: Q1 ({top_mean:+.1f}%) > Q5 ({bot_mean:+.1f}%) by "
                      f"{top_mean - bot_mean:.1f}pp")
        else:
            md.append(f"🟡 Score system weak: Q1-Q5 spread only {top_mean - bot_mean:.1f}pp")
    md.append("")

    OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"\nReport: {OUT}")

    # Console summary
    print(f"\n=== SUMMARY ===")
    print(f"Overall n={overall['n']}, mean={overall['mean']:+.2f}%, WR={overall['wr']:.0f}%, t={overall['t_stat']:+.2f}")
    print(f"OOS: {pass_count}/{len(oos)} periods pass")
    print(f"MCPT p-value: {mcpt_all['p_value']:.4f} (z={mcpt_all['z_score']:+.2f})")


if __name__ == "__main__":
    main()
