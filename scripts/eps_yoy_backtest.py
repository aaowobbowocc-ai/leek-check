"""
EPS YoY surprise alpha backtest

Hypothesis: 季 EPS YoY > +30% (vs same Q last year) → 60d forward alpha

學術 PEAD 原始研究用 EPS（非 revenue）。預期 EPS 訊號比 revenue 強或互補。

避免 look-ahead bias：
  Quarter end (Q1=3/31) → 公告日 ≈ Q1 結束 + 50 天 (5/20)
  Forward return 從公告日開始算

過濾：
  prior_eps > 0.5 (避免 base effect)
  yoy 在 -100 ~ +500%
"""
from __future__ import annotations
import io, sys
from pathlib import Path
from datetime import timedelta
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
HOLD_DAYS = 60
ANNOUNCE_LAG_DAYS = 50  # quarter end + 50 days ≈ 公告日


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_eps(tk):
    p = CACHE / f"TaiwanStockFinancialStatements_{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    try: df = pd.read_parquet(p)
    except: return pd.DataFrame()
    if df.empty: return pd.DataFrame()
    df = df[df["type"] == "EPS"].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["eps"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    return df


def compute_market_median_eps_yoy():
    """全市場 quarter 級 median EPS YoY"""
    print("  計算市場 EPS YoY median...")
    all_yoy = []
    for p in CACHE.glob("TaiwanStockFinancialStatements_*.parquet"):
        try:
            df = pd.read_parquet(p)
            df = df[df["type"] == "EPS"].copy()
            if len(df) < 8: continue
            df["date"] = pd.to_datetime(df["date"])
            df["eps"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.sort_values("date").reset_index(drop=True)
            # 4 季前的同 quarter EPS
            df["prior_eps"] = df["eps"].shift(4)
            df["yoy"] = (df["eps"] - df["prior_eps"]) / df["prior_eps"].abs() * 100
            df = df[(df["prior_eps"] > 0.5) & (df["yoy"].abs() < 500)]
            if df.empty: continue
            all_yoy.append(df[["date", "yoy"]])
        except: continue
    if not all_yoy: return {}
    df = pd.concat(all_yoy, ignore_index=True)
    df["q"] = df["date"].dt.to_period("Q")
    return df.groupby("q")["yoy"].median().to_dict()


def collect_events(universe, market_median):
    events = []
    for i, tk in enumerate(universe):
        eps = load_eps(tk)
        if eps.empty or len(eps) < 8: continue
        eps["prior_eps"] = eps["eps"].shift(4)
        eps["yoy"] = (eps["eps"] - eps["prior_eps"]) / eps["prior_eps"].abs() * 100
        eps["q"] = eps["date"].dt.to_period("Q")
        eps["mkt_med"] = eps["q"].map(market_median)
        eps["excess"] = eps["yoy"] - eps["mkt_med"]
        triggers = eps[
            (eps["excess"] > 30) &
            (eps["yoy"] > 0) & (eps["yoy"] < 500) &
            (eps["prior_eps"] > 0.5) &
            eps["yoy"].notna()
        ]
        if triggers.empty: continue

        # 公告日 = quarter_end + 50 days
        triggers = triggers.copy()
        triggers["announce_date"] = triggers["date"] + pd.Timedelta(days=ANNOUNCE_LAG_DAYS)

        pp = TW_CACHE / f"{tk}.parquet"
        if not pp.exists() or pp.stat().st_size < 500: continue
        try: px = pd.read_parquet(pp)
        except: continue
        if px.empty or len(px) < HOLD_DAYS + 60: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px_idx) - HOLD_DAYS - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px_idx) - HOLD_DAYS), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            if px_idx.iloc[j] > 0:
                baseline.append((px_idx.iloc[j + HOLD_DAYS] / px_idx.iloc[j] - 1) * 100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        for _, row in triggers.iterrows():
            ann = row["announce_date"]
            future = px_idx[px_idx.index >= ann]
            if len(future) <= HOLD_DAYS: continue
            entry = future.iloc[0]
            if entry > 0:
                fwd = (future.iloc[HOLD_DAYS] / entry - 1) * 100
                events.append({
                    "ticker": tk, "announce_date": ann,
                    "fwd_60d": fwd, "baseline_mean": bm, "baseline_std": bs,
                    "yoy": row["yoy"], "excess": row["excess"],
                    "year": ann.year,
                })
        if (i + 1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def event_summary(df, label):
    if df.empty or len(df) < 30:
        print(f"  {label}: n={len(df)} (太少)")
        return None
    n = len(df)
    sig = df["fwd_60d"].mean()
    bm = df["baseline_mean"].mean()
    bs = df["baseline_std"].mean()
    alpha = sig - bm
    win = (df["fwd_60d"] > 0).mean() * 100
    t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
    t_str = f"{t:+.2f}" if t else "n/a"
    verdict = "✅" if alpha > 1.5 and (t or 0) > 2 else "⚠️"
    print(f"  {label}: n={n}, signal={sig:+.2f}%, baseline={bm:+.2f}%, "
          f"alpha={alpha:+.2f}%, win={win:.0f}%, t={t_str} {verdict}")
    return {"n": n, "alpha": alpha, "t": t}


def mcpt(events):
    if events.empty: return None
    n_events = len(events)
    rng = np.random.RandomState(42)
    real = events["fwd_60d"].mean() - events["baseline_mean"].mean()
    fwd = events["fwd_60d"].values
    base = events["baseline_mean"].values
    fakes = []
    n_total = len(fwd)
    for _ in range(1000):
        idx = rng.choice(n_total, size=n_events, replace=False)
        # Resample: 把 fwd & base 同步打亂（label permutation）
        perm = rng.permutation(n_total)
        fake = fwd - base[perm]  # break correlation
        fakes.append(fake.mean())
    fakes = np.array(fakes)
    p = (fakes >= real).sum() / 1000
    print(f"\n  🎲 MCPT: real_alpha={real:+.3f}%, p={p:.4f} {'✅' if p<0.05 else '❌'}")
    return p


def main():
    print("=" * 80)
    print(f"  EPS YoY surprise alpha backtest")
    print(f"  Excess > +30% (vs market median Q YoY), hold {HOLD_DAYS}d")
    print("=" * 80)
    universe = load_universe()
    print(f"  Universe: {len(universe)}")
    market_median = compute_market_median_eps_yoy()
    print(f"  Market median quarters: {len(market_median)}")

    events = collect_events(universe, market_median)
    print(f"\n  Total events: {len(events)}")

    print("\n  ▶ Full sample:")
    full = event_summary(events, "Full")

    print("\n  📅 OOS split:")
    splits = [
        ("2017-2019", events[events["year"] <= 2019]),
        ("2020-2022", events[(events["year"] >= 2020) & (events["year"] <= 2022)]),
        ("2023-2025", events[events["year"] >= 2023]),
    ]
    for label, sub in splits:
        event_summary(sub, label)

    mcpt(events)


if __name__ == "__main__":
    main()
