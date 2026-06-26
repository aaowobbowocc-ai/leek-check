"""
連 N 日漲停 alpha (next-day entry)

Hypothesis:
  連漲 = 強勢 momentum，但
  連 1 日 = 一般漲停
  連 2 日 = 強勢確認
  連 3 日 = 過熱，可能反轉?
  連 4+ 日 = 極端，必反轉?

驗證：alpha 是否 monotonic 還是 U/inverted-U
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
HOLDS = [5, 20, 60]


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def collect_events(universe):
    print("  收集連 N 日漲停 events...")
    events = []
    max_hold = max(HOLDS)
    for i, tk in enumerate(universe):
        p = TW_CACHE / f"{tk}.parquet"
        if not p.exists() or p.stat().st_size < 500: continue
        try: px = pd.read_parquet(p)
        except: continue
        if px.empty or len(px) < 200: continue
        px["date"] = pd.to_datetime(px["date"])
        px = px.sort_values("date").reset_index(drop=True)
        px["pct"] = px["close"].pct_change() * 100
        px["limit_up"] = (px["pct"] >= 9.0).astype(int)

        # consecutive count
        cnt = 0
        cnts = []
        for v in px["limit_up"]:
            if v == 1: cnt += 1
            else: cnt = 0
            cnts.append(cnt)
        px["consec_lu"] = cnts

        # baseline
        if len(px) < max_hold + 60: continue
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px) - max_hold - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px) - max_hold), size=n_base, replace=False)
        baseline = {h: [] for h in HOLDS}
        for j in bidx:
            entry = px["close"].iloc[j]
            if entry > 0:
                for h in HOLDS:
                    baseline[h].append((px["close"].iloc[j+h]/entry-1)*100)

        # Triggers: 任何漲停日（cnt >= 1）
        triggers = px[px["limit_up"] == 1]
        for _, row in triggers.iterrows():
            sd = row["date"]
            cnt_at = row["consec_lu"]
            future = px[px["date"] > sd]
            if len(future) <= max_hold: continue
            entry = future["close"].iloc[0]
            if entry <= 0: continue
            event = {
                "ticker": tk, "date": sd, "consec": cnt_at,
                "year": sd.year,
            }
            for h in HOLDS:
                event[f"fwd_{h}d"] = (future["close"].iloc[h]/entry-1)*100
                event[f"base_{h}d"] = np.mean(baseline[h]) if baseline[h] else 0
                event[f"base_std_{h}d"] = np.std(baseline[h]) if baseline[h] else 0
            events.append(event)
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def analyze_consecutive(events):
    print(f"\n  Total events: {len(events)}")
    print(f"  Distribution: {events['consec'].value_counts().sort_index().to_dict()}")

    for hold in HOLDS:
        print(f"\n  📊 連 N 日漲停 alpha (hold={hold}d):")
        print(f"  {'consec':<8} {'n':<7} {'mean':<8} {'baseline':<10} {'alpha':<8} {'win%':<6} {'t':<7}")
        for n_consec in [1, 2, 3, 4, 5]:
            if n_consec == 5:
                sub = events[events["consec"] >= 5]
                label = "≥5"
            else:
                sub = events[events["consec"] == n_consec]
                label = str(n_consec)
            if len(sub) < 30: continue
            n = len(sub)
            sig = sub[f"fwd_{hold}d"].mean()
            bm = sub[f"base_{hold}d"].mean()
            bs = sub[f"base_std_{hold}d"].mean()
            alpha = sig - bm
            win = (sub[f"fwd_{hold}d"] > 0).mean() * 100
            t = alpha / (bs/np.sqrt(n)) if bs > 0 else None
            t_str = f"{t:+.2f}" if t else "n/a"
            print(f"  {label:<8} {n:<7} {sig:+.2f}%  {bm:+.2f}%    {alpha:+.2f}%  {win:.1f}%  {t_str}")


def oos_check(events):
    print(f"\n  📅 OOS for consec=2 (連 2 日) hold=20d:")
    sub_all = events[events["consec"] == 2]
    for plabel, sub in [
        ("2017-2019", sub_all[sub_all["year"] <= 2019]),
        ("2020-2022", sub_all[(sub_all["year"] >= 2020) & (sub_all["year"] <= 2022)]),
        ("2023-2025", sub_all[sub_all["year"] >= 2023]),
    ]:
        if len(sub) < 30: continue
        n = len(sub)
        alpha = sub["fwd_20d"].mean() - sub["base_20d"].mean()
        bs = sub["base_std_20d"].mean()
        t = alpha / (bs/np.sqrt(n)) if bs > 0 else None
        t_str = f"{t:+.2f}" if t else "n/a"
        verdict = "✅" if alpha > 1 and (t or 0) > 2 else "⚠️"
        print(f"    {plabel}: n={n}, alpha={alpha:+.2f}%, t={t_str} {verdict}")


def main():
    print("=" * 80)
    print("  連 N 日漲停 alpha")
    print("=" * 80)
    universe = load_universe()
    events = collect_events(universe)
    analyze_consecutive(events)
    oos_check(events)


if __name__ == "__main__":
    main()
