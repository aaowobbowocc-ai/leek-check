"""
VIX regime × Revenue YoY 訊號驗證

Hypothesis: VIX 是普遍 alpha multiplier，對 Revenue YoY 也有效
  - VIX < 18 平靜 → PEAD alpha 弱?
  - VIX ≥ 35 極恐慌 → PEAD alpha 強?

驗證所有訊號的 VIX dependency
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
HOLD = 60


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_vix():
    import yfinance as yf
    h = yf.Ticker("^VIX").history(period="3500d", auto_adjust=False)
    df = pd.DataFrame({"date": pd.to_datetime(h.index).tz_localize(None),
                       "vix": h["Close"].values})
    return df.set_index("date")["vix"].to_dict()


def compute_market_median():
    print("  計算市場 median...")
    all_yoy = []
    for p in CACHE.glob("TaiwanStockMonthRevenue_*.parquet"):
        try:
            r = pd.read_parquet(p)
            if len(r) < 24: continue
            r = r.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
            r["prior"] = r["revenue"].shift(12)
            r["yoy"] = (r["revenue"] / r["prior"] - 1) * 100
            r = r[r["prior"] > 1e7]
            if r.empty: continue
            r["date"] = pd.to_datetime(r["date"])
            r2 = r[r["yoy"].abs() < 500][["date", "yoy"]]
            all_yoy.append(r2)
        except: continue
    df = pd.concat(all_yoy, ignore_index=True)
    df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym")["yoy"].median().to_dict()


def collect_events(universe, market_median, vix_map):
    print("  收集 Revenue YoY events with VIX...")
    events = []
    for i, tk in enumerate(universe):
        rp = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
        if not rp.exists(): continue
        try: rev = pd.read_parquet(rp)
        except: continue
        if rev.empty or len(rev) < 24: continue
        rev = rev.sort_values(["revenue_year","revenue_month"]).reset_index(drop=True)
        rev["prior"] = rev["revenue"].shift(12)
        rev["yoy"] = (rev["revenue"]/rev["prior"]-1)*100
        rev["date"] = pd.to_datetime(rev["date"])
        rev["ym"] = rev["date"].dt.to_period("M")
        rev["mkt_med"] = rev["ym"].map(market_median)
        rev["excess"] = rev["yoy"] - rev["mkt_med"]
        triggers = rev[(rev["excess"]>30) & (rev["yoy"]<200) & rev["yoy"].notna()
                       & (rev["prior"]>1e7)]
        if triggers.empty: continue

        pp = TW_CACHE / f"{tk}.parquet"
        if not pp.exists() or pp.stat().st_size < 500: continue
        try: px = pd.read_parquet(pp)
        except: continue
        if px.empty or len(px) < HOLD + 60: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px_idx) - HOLD - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px_idx) - HOLD), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            if px_idx.iloc[j] > 0:
                baseline.append((px_idx.iloc[j+HOLD]/px_idx.iloc[j]-1)*100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        for _, row in triggers.iterrows():
            sd = row["date"]
            future = px_idx[px_idx.index > sd]
            if len(future) <= HOLD: continue
            entry = future.iloc[0]
            if entry > 0:
                fwd = (future.iloc[HOLD]/entry-1)*100
                # VIX at signal date (or nearest before)
                vix = None
                for d_offset in range(0, 7):
                    d_check = sd - pd.Timedelta(days=d_offset)
                    if d_check in vix_map:
                        vix = vix_map[d_check]; break
                if vix is None: continue
                events.append({
                    "ticker": tk, "fwd_60d": fwd,
                    "baseline_mean": bm, "baseline_std": bs,
                    "vix": vix, "year": sd.year,
                })
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def analyze_vix(events):
    print(f"\n  Total events with VIX: {len(events)}")
    print(f"\n  📊 Revenue YoY × VIX bucket (hold 60d):")
    print(f"  {'bucket':<20} {'n':<7} {'alpha':<8} {'baseline':<10} {'win%':<6} {'t':<7}")
    for blabel, sub in [
        ("low (vix<18)", events[events["vix"] < 18]),
        ("mid (18-25)", events[(events["vix"] >= 18) & (events["vix"] < 25)]),
        ("high (25-35)", events[(events["vix"] >= 25) & (events["vix"] < 35)]),
        ("extreme (≥35)", events[events["vix"] >= 35]),
    ]:
        if len(sub) < 100: continue
        n = len(sub)
        alpha = sub["fwd_60d"].mean() - sub["baseline_mean"].mean()
        bm = sub["baseline_mean"].mean()
        bs = sub["baseline_std"].mean()
        win = (sub["fwd_60d"] > 0).mean() * 100
        t = alpha / (bs/np.sqrt(n)) if bs > 0 else None
        t_str = f"{t:+.2f}" if t else "n/a"
        verdict = "⭐" if alpha > 5 and (t or 0) > 5 else ""
        print(f"  {blabel:<20} {n:<7} {alpha:+.2f}%  {bm:+.2f}%    {win:.1f}%  {t_str}  {verdict}")


def oos_check(events):
    print(f"\n  📅 OOS for vix<18 vs vix≥35:")
    for vlabel, vsub in [
        ("vix<18", events[events["vix"] < 18]),
        ("vix≥35", events[events["vix"] >= 35]),
    ]:
        print(f"\n  --- {vlabel} ---")
        for plabel, sub in [
            ("2017-2019", vsub[vsub["year"] <= 2019]),
            ("2020-2022", vsub[(vsub["year"] >= 2020) & (vsub["year"] <= 2022)]),
            ("2023-2025", vsub[vsub["year"] >= 2023]),
        ]:
            if len(sub) < 30: continue
            n = len(sub)
            alpha = sub["fwd_60d"].mean() - sub["baseline_mean"].mean()
            bs = sub["baseline_std"].mean()
            t = alpha / (bs/np.sqrt(n)) if bs > 0 else None
            t_str = f"{t:+.2f}" if t else "n/a"
            verdict = "✅" if abs(alpha) > 1 and abs(t or 0) > 2 else "⚠️"
            print(f"    {plabel}: n={n}, alpha={alpha:+.2f}%, t={t_str} {verdict}")


def main():
    print("=" * 80)
    print("  VIX × Revenue YoY (PEAD) regime test")
    print("=" * 80)
    universe = load_universe()
    market_median = compute_market_median()
    vix_map = load_vix()
    print(f"  Universe: {len(universe)}, VIX days: {len(vix_map)}")
    events = collect_events(universe, market_median, vix_map)
    analyze_vix(events)
    oos_check(events)


if __name__ == "__main__":
    main()
