"""
跌停隔日 alpha (對稱於漲停)

Hypothesis:
  量縮跌停: 賣壓力竭 → 隔日反彈 (gap up + intraday up)
  量爆跌停: panic dump → 隔日跳空更低 or 急反彈?
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


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_vix():
    import yfinance as yf
    h = yf.Ticker("^VIX").history(period="3500d", auto_adjust=False)
    df = pd.DataFrame({"date": pd.to_datetime(h.index).tz_localize(None),
                       "vix": h["Close"].values})
    return df.set_index("date")["vix"].to_dict()


def collect_events(universe, vix_map):
    print("  收集跌停 events for 隔日 alpha...")
    events = []
    for i, tk in enumerate(universe):
        p = TW_CACHE / f"{tk}.parquet"
        if not p.exists() or p.stat().st_size < 500: continue
        try: px = pd.read_parquet(p)
        except: continue
        if px.empty or len(px) < 200: continue
        px["date"] = pd.to_datetime(px["date"])
        px = px.sort_values("date").reset_index(drop=True)
        px["pct"] = px["close"].pct_change() * 100
        px["vol_ma60"] = px["volume"].rolling(60).mean()
        px["vol_ratio"] = px["volume"] / px["vol_ma60"]

        triggers = px[(px["pct"] <= -9.5) & px["vol_ratio"].notna()]
        for _, row in triggers.iterrows():
            sd = row["date"]
            cur_idx = px.index[px["date"] == sd]
            if len(cur_idx) == 0: continue
            cur_idx = cur_idx[0]
            if cur_idx + 1 >= len(px): continue
            d_close = px.iloc[cur_idx]["close"]
            next_row = px.iloc[cur_idx + 1]
            next_open = next_row.get("open", next_row["close"])
            next_close = next_row["close"]
            overnight_gap = (next_open / d_close - 1) * 100
            intraday = (next_close / next_open - 1) * 100
            d_to_d1 = (next_close / d_close - 1) * 100

            vix = None
            for offset in range(7):
                d_check = sd - pd.Timedelta(days=offset)
                if d_check in vix_map:
                    vix = vix_map[d_check]; break
            if vix is None: continue
            events.append({
                "ticker": tk, "date": sd,
                "vol_ratio": row["vol_ratio"], "vix": vix,
                "gap": overnight_gap, "intraday": intraday, "d_to_d1": d_to_d1,
                "year": sd.year,
            })
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def analyze(events):
    print(f"\n  Total events: {len(events)}")
    buckets = [
        ("Q1 量縮 (vr<0.8)", events[events["vol_ratio"] < 0.8]),
        ("Q2 (0.8-1.2)", events[(events["vol_ratio"] >= 0.8) & (events["vol_ratio"] < 1.2)]),
        ("Q3 (1.2-2.0)", events[(events["vol_ratio"] >= 1.2) & (events["vol_ratio"] < 2.0)]),
        ("Q4 量爆 (vr≥2.0)", events[events["vol_ratio"] >= 2.0]),
    ]
    print(f"\n  📊 跌停隔日 by 量比:")
    for label, sub in buckets:
        if len(sub) < 50: continue
        n = len(sub)
        gap = sub["gap"].mean()
        intra = sub["intraday"].mean()
        full = sub["d_to_d1"].mean()
        gap_pos = (sub["gap"] > 0).mean() * 100
        intra_pos = (sub["intraday"] > 0).mean() * 100
        full_pos = (sub["d_to_d1"] > 0).mean() * 100
        print(f"  {label:<25} n={n:<5} gap={gap:+.2f}%(win {gap_pos:.0f}%) "
              f"intra={intra:+.2f}%(win {intra_pos:.0f}%) full={full:+.2f}%(win {full_pos:.0f}%)")

    # VIX × Q1 量縮跌停 - 已知核心訊號
    print(f"\n  📊 VIX × 量縮跌停 (Q1):")
    for vlabel, vsub in [
        ("vix<18", events[(events["vol_ratio"] < 0.8) & (events["vix"] < 18)]),
        ("vix 18-25", events[(events["vol_ratio"] < 0.8) & (events["vix"] >= 18) & (events["vix"] < 25)]),
        ("vix≥25", events[(events["vol_ratio"] < 0.8) & (events["vix"] >= 25)]),
    ]:
        if len(vsub) < 30: continue
        n = len(vsub)
        gap = vsub["gap"].mean()
        intra = vsub["intraday"].mean()
        full = vsub["d_to_d1"].mean()
        print(f"  {vlabel}: n={n}, gap={gap:+.2f}%, intra={intra:+.2f}%, full={full:+.2f}%")


def oos_check(events):
    print(f"\n  📅 OOS Q1 量縮跌停 隔日全日 alpha:")
    sub_all = events[events["vol_ratio"] < 0.8]
    for plabel, sub in [
        ("2017-2019", sub_all[sub_all["year"] <= 2019]),
        ("2020-2022", sub_all[(sub_all["year"] >= 2020) & (sub_all["year"] <= 2022)]),
        ("2023-2025", sub_all[sub_all["year"] >= 2023]),
    ]:
        if len(sub) < 30: continue
        n = len(sub)
        gap = sub["gap"].mean()
        intra = sub["intraday"].mean()
        full = sub["d_to_d1"].mean()
        full_pos = (sub["d_to_d1"] > 0).mean() * 100
        print(f"    {plabel}: n={n}, gap={gap:+.2f}%, intra={intra:+.2f}%, full={full:+.2f}% (win {full_pos:.0f}%)")


def main():
    print("=" * 80)
    print("  跌停隔日 alpha (對稱漲停實驗)")
    print("=" * 80)
    universe = load_universe()
    vix_map = load_vix()
    events = collect_events(universe, vix_map)
    analyze(events)
    oos_check(events)


if __name__ == "__main__":
    main()
