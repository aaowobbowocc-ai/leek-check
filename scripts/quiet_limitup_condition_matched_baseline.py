"""
Gemini Finding #5: Condition-matched baseline for quiet limitup/down

Problem: same-ticker random baseline includes boring sideways days.
"Quiet limitup" should compare vs OTHER limitup days (with different vol_ratio),
not random days. This answers: does vol_ratio < 0.8 ADD alpha vs ANY limitup?

Test:
  Group A: quiet limitup  (pct >= 9.5% AND vr < 0.8)  [our signal]
  Group B: normal limitup (pct >= 9.5% AND vr >= 0.8)  [condition-matched]
  Group C: random entry   (any day)                    [old baseline]

A vs B = TRUE extra alpha from "quiet" factor
A vs C = naive alpha (what we've been reporting)
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
HOLD = 20
COST = 0.78


def collect(direction="up"):
    quiet, normal, random_returns = [], [], []
    files = list(TW_CACHE.glob("*.parquet"))
    for i, p in enumerate(files):
        if p.stat().st_size < 500: continue
        try: px = pd.read_parquet(p)
        except: continue
        if len(px) < 80: continue
        px["date"] = pd.to_datetime(px["date"])
        px = px.sort_values("date").reset_index(drop=True)
        px["pct"] = px["close"].pct_change() * 100
        px["vol_ma60"] = px["volume"].rolling(60).mean()
        px["vol_ratio"] = px["volume"] / px["vol_ma60"]

        for idx in range(len(px) - HOLD - 1):
            pct = px.loc[idx, "pct"]
            vr = px.loc[idx, "vol_ratio"]
            if pd.isna(pct) or pd.isna(vr): continue
            entry = px.loc[idx + 1, "open"]
            if entry <= 0: continue
            fwd = (px.loc[idx + HOLD, "close"] / entry - 1) * 100 - COST

            is_limit = (pct >= 9.5) if direction == "up" else (pct <= -9.5)
            is_quiet = vr < 0.8

            if is_limit and is_quiet:
                quiet.append(fwd)
            elif is_limit and not is_quiet:
                normal.append(fwd)
            else:
                random_returns.append(fwd)

        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(files)}] q={len(quiet)} norm={len(normal)}")

    return quiet, normal, random_returns


def report(quiet, normal, random_r, label):
    print(f"\n{'='*80}\n  {label} — condition-matched baseline\n{'='*80}")
    q = np.array(quiet)
    n = np.array(normal)
    r = np.array(random_r[:len(q)*5])  # 只取 5x 樣本避免 t-test 過強

    def fmt(arr, name):
        print(f"\n  {name} (n={len(arr):,}): mean {arr.mean():+.2f}% | "
              f"median {np.median(arr):+.2f}% | win {(arr>0).mean()*100:.1f}%")
    fmt(q, "A: quiet signal")
    fmt(n, "B: normal limitup (condition-matched)")
    fmt(r, "C: random entry   (old baseline)")

    # A vs B: Welch's t
    if len(n) >= 30:
        t_ab, p_ab = stats.ttest_ind(q, n, equal_var=False, alternative="greater")
        diff_ab = q.mean() - n.mean()
        print(f"\n  A vs B (quiet vs normal limitup):")
        print(f"    Δ = {diff_ab:+.2f}pp, t={t_ab:+.2f}, p={p_ab:.4f}")
        if p_ab < 0.05:
            print(f"    ✅ quiet factor 貢獻 {diff_ab:+.2f}pp GENUINE extra alpha vs condition-matched")
        else:
            print(f"    ⚠️ quiet factor 相對 normal limitup 無顯著 alpha (p={p_ab:.3f})")

    # A vs C: old naive alpha
    if len(r) >= 30:
        t_ac, p_ac = stats.ttest_ind(q, r, equal_var=False, alternative="greater")
        diff_ac = q.mean() - r.mean()
        print(f"\n  A vs C (quiet vs random — 舊 baseline):")
        print(f"    Δ = {diff_ac:+.2f}pp, t={t_ac:+.2f}, p={p_ac:.4f}")
        print(f"    (Gemini 懷疑舊 baseline 因包含無聊盤整日而高估 alpha)")

    print(f"\n  📊 Alpha 修正:")
    naive = q.mean() - r.mean()
    real  = q.mean() - n.mean()
    print(f"    naive alpha (vs random):             {naive:+.2f}pp")
    print(f"    condition-matched alpha (vs any limitup): {real:+.2f}pp")
    if abs(naive) > 0:
        over = (naive - real) / abs(naive) * 100
        print(f"    naive 高估了 {over:.0f}%" if over > 0 else f"    naive 低估了 {-over:.0f}%")


def main():
    print("=" * 80)
    print("  Gemini #5: Condition-matched baseline (vs other limitup days)")
    print("=" * 80)

    print("\n[1/2] Quiet Limitup (direction=up)...")
    q, n, r = collect("up")
    report(q, n, r, "Quiet Limitup")

    print("\n[2/2] Quiet Limitdown (direction=down)...")
    q2, n2, r2 = collect("down")
    report(q2, n2, r2, "Quiet Limitdown")


if __name__ == "__main__":
    main()
