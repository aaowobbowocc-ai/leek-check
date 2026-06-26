"""
量縮漲停 × Revenue YoY combo backtest

Hypothesis:
  量縮漲停 alpha +5.22%/20d (已驗證)
  Revenue YoY +3.95%/60d (已驗證)
  同 ticker 30 日內兩個訊號都觸發 → 期望 super-additive

OOS + MCPT 驗證
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
HOLD_DAYS = 60
COMBO_WINDOW = 30
N_PERMUTE = 1000


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


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


def find_revenue_triggers(tk, market_median):
    rp = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
    if not rp.exists(): return []
    try: rev = pd.read_parquet(rp)
    except: return []
    if rev.empty or len(rev) < 24: return []
    rev = rev.sort_values(["revenue_year","revenue_month"]).reset_index(drop=True)
    rev["prior"] = rev["revenue"].shift(12)
    rev["yoy"] = (rev["revenue"]/rev["prior"]-1)*100
    rev["date"] = pd.to_datetime(rev["date"])
    rev["ym"] = rev["date"].dt.to_period("M")
    rev["mkt_med"] = rev["ym"].map(market_median)
    rev["excess"] = rev["yoy"] - rev["mkt_med"]
    return rev[(rev["excess"]>30) & (rev["yoy"]<200) & rev["yoy"].notna()
               & (rev["prior"]>1e7)]["date"].tolist()


def find_quiet_limitup_triggers(tk):
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists() or p.stat().st_size < 500: return []
    try: px = pd.read_parquet(p)
    except: return []
    if px.empty or len(px) < 100: return []
    px["date"] = pd.to_datetime(px["date"])
    px = px.sort_values("date").reset_index(drop=True)
    px["pct"] = px["close"].pct_change() * 100
    px["vol_ma"] = px["volume"].rolling(60).mean()
    px["vol_ratio"] = px["volume"] / px["vol_ma"]
    triggers = px[(px["pct"] >= 9.5) & (px["vol_ratio"] < 0.8)]
    return triggers["date"].tolist()


def collect_combo_events(universe, market_median):
    """A = Revenue YoY trigger, B = 量縮漲停 trigger
    若 A 觸發後 30 日內 B 也觸發 → AB combo
    若 B 觸發後 30 日內 A 也觸發 → 同樣 AB combo
    取最早的 second trigger 作為 entry date
    """
    events_a_only = []
    events_b_only = []
    events_ab = []

    for i, tk in enumerate(universe):
        a_dates = find_revenue_triggers(tk, market_median)
        b_dates = find_quiet_limitup_triggers(tk)

        p = TW_CACHE / f"{tk}.parquet"
        if not p.exists() or p.stat().st_size < 500: continue
        try: px = pd.read_parquet(p)
        except: continue
        if px.empty or len(px) < HOLD_DAYS + 60: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        # baseline
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px_idx) - HOLD_DAYS - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px_idx) - HOLD_DAYS), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            if px_idx.iloc[j] > 0:
                baseline.append((px_idx.iloc[j+HOLD_DAYS]/px_idx.iloc[j]-1)*100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        a_set = sorted([pd.Timestamp(d) for d in a_dates])
        b_set = sorted([pd.Timestamp(d) for d in b_dates])

        # Combo: A 觸發後 30 日內 B 也觸發
        ab_dates = []
        for a in a_set:
            end = a + pd.Timedelta(days=COMBO_WINDOW)
            b_in = [b for b in b_set if a < b <= end]
            if b_in: ab_dates.append(min(b_in))
        # B 觸發後 30 日內 A 也觸發
        for b in b_set:
            end = b + pd.Timedelta(days=COMBO_WINDOW)
            a_in = [a for a in a_set if b < a <= end]
            if a_in: ab_dates.append(min(a_in))
        ab_dates = sorted(set(ab_dates))

        # 計算 forward return (用 entry date)
        for combo_name, dates in [("A_only", a_set), ("B_only", b_set), ("AB", ab_dates)]:
            target = combo_name + "_evts"
            evt_list = events_a_only if combo_name == "A_only" else \
                       events_b_only if combo_name == "B_only" else events_ab
            for sd in dates:
                future = px_idx[px_idx.index > sd]
                if len(future) <= HOLD_DAYS: continue
                entry = future.iloc[0]
                if entry > 0:
                    fwd = (future.iloc[HOLD_DAYS]/entry-1)*100
                    evt_list.append({
                        "ticker": tk, "signal_date": sd,
                        "fwd_60d": fwd, "baseline_mean": bm, "baseline_std": bs,
                        "year": sd.year,
                    })
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] A={len(events_a_only)}, B={len(events_b_only)}, AB={len(events_ab)}")

    return (pd.DataFrame(events_a_only), pd.DataFrame(events_b_only), pd.DataFrame(events_ab))


def event_summary(df, label):
    if df.empty or len(df)<30:
        print(f"  {label}: n={len(df)} (太少)")
        return None
    n = len(df)
    sig = df["fwd_60d"].mean()
    bm = df["baseline_mean"].mean()
    bs = df["baseline_std"].mean()
    alpha = sig - bm
    win = (df["fwd_60d"]>0).mean()*100
    t = alpha / (bs/np.sqrt(n)) if bs>0 else None
    t_str = f"{t:+.2f}" if t else "n/a"
    print(f"  {label}: n={n}, signal={sig:+.2f}%, baseline={bm:+.2f}%, alpha={alpha:+.2f}%, win={win:.0f}%, t={t_str}")
    return {"n":n, "alpha":alpha, "t":t}


def mcpt_test(events, label):
    if events.empty or len(events) < 30: return None
    rng = np.random.RandomState(42)
    n_events = len(events)
    real = events["fwd_60d"].mean() - events["baseline_mean"].mean()
    fwd = events["fwd_60d"].values
    base = events["baseline_mean"].values
    fakes = []
    for _ in range(N_PERMUTE):
        perm = rng.permutation(n_events)
        fake = fwd - base[perm]
        fakes.append(fake.mean())
    fakes = np.array(fakes)
    p = (fakes >= real).sum() / N_PERMUTE
    print(f"\n  🎲 MCPT {label}: real={real:+.2f}%, p={p:.4f} {'✅' if p<0.05 else '❌'}")
    return p


def run_oos(events, label):
    print(f"\n  📅 {label} OOS:")
    for plabel, sub in [
        ("2017-2019", events[events["year"]<=2019]),
        ("2020-2022", events[(events["year"]>=2020)&(events["year"]<=2022)]),
        ("2023-2025", events[events["year"]>=2023]),
    ]:
        event_summary(sub, plabel)


def main():
    print("="*80)
    print("  量縮漲停 × Revenue YoY combo backtest")
    print("="*80)
    universe = load_universe()
    print(f"  Universe: {len(universe)}")
    market_median = compute_market_median()

    a, b, ab = collect_combo_events(universe, market_median)

    print("\n" + "="*80)
    print("  📊 Full sample summary")
    print("="*80)
    event_summary(a, "A_only (Revenue YoY)")
    event_summary(b, "B_only (Quiet Limitup)")
    event_summary(ab, "AB combo")

    print("\n" + "="*80)
    print("  📅 OOS validation")
    print("="*80)
    run_oos(a, "A_only")
    run_oos(b, "B_only")
    run_oos(ab, "AB")

    print("\n" + "="*80)
    print("  🎲 MCPT")
    print("="*80)
    for evts, label in [(a, "A_only"), (b, "B_only"), (ab, "AB")]:
        mcpt_test(evts, label)


if __name__ == "__main__":
    main()
