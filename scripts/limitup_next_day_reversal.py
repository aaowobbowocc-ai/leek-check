"""
漲停隔日反轉 alpha (1d) — 從未測過

Hypothesis:
  量縮漲停 隔日延續（量縮繼續，主力未派發）
  量爆漲停 隔日反轉（散戶推升，法人派發）

Setup:
  Trigger: D 日漲停 (pct >= 9.5)
  Entry: D+1 open
  Exit: D+1 close
  Alpha: D+1 close / D+1 open - 1
  比較 vs same-ticker random 1d intraday

或者 D+1 close vs D close（包含跳空）

分 vol_ratio + VIX bucket
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
    print("  收集漲停 events for 1d / overnight 反轉...")
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

        triggers = px[(px["pct"] >= 9.5) & px["vol_ratio"].notna()]

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

            # 隔日 alpha
            overnight_gap = (next_open / d_close - 1) * 100  # 跳空
            intraday = (next_close / next_open - 1) * 100   # 隔日盤中
            d_to_d1 = (next_close / d_close - 1) * 100       # 完整隔日 (D->D+1)

            vix = None
            for offset in range(7):
                d_check = sd - pd.Timedelta(days=offset)
                if d_check in vix_map:
                    vix = vix_map[d_check]; break
            if vix is None: continue

            events.append({
                "ticker": tk, "date": sd,
                "vol_ratio": row["vol_ratio"], "vix": vix,
                "overnight_gap_pct": overnight_gap,
                "intraday_pct": intraday,
                "d_to_d1_pct": d_to_d1,
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
    for label, sub in buckets:
        if len(sub) < 100: continue
        n = len(sub)
        gap = sub["overnight_gap_pct"].mean()
        intra = sub["intraday_pct"].mean()
        full = sub["d_to_d1_pct"].mean()
        gap_pos = (sub["overnight_gap_pct"] > 0).mean() * 100
        full_pos = (sub["d_to_d1_pct"] > 0).mean() * 100
        print(f"  {label:<25} n={n:<5} gap={gap:+.2f}%(win {gap_pos:.0f}%) "
              f"intra={intra:+.2f}% full={full:+.2f}%(win {full_pos:.0f}%)")

    # VIX × vol_ratio cross-section
    print(f"\n  📊 VIX × vol_ratio (intraday alpha D+1 close vs open):")
    for vlabel, vsub in [
        ("vix<18", events[events["vix"] < 18]),
        ("vix 18-25", events[(events["vix"] >= 18) & (events["vix"] < 25)]),
        ("vix≥25", events[events["vix"] >= 25]),
    ]:
        for blabel, sub in [
            ("Q1 量縮", vsub[vsub["vol_ratio"] < 0.8]),
            ("Q4 量爆", vsub[vsub["vol_ratio"] >= 2.0]),
        ]:
            if len(sub) < 50: continue
            n = len(sub)
            mean = sub["intraday_pct"].mean()
            full = sub["d_to_d1_pct"].mean()
            print(f"  {vlabel} × {blabel}: n={n}, intraday={mean:+.2f}%, D->D+1={full:+.2f}%")


def oos_check(events):
    print(f"\n  📅 OOS for Q4 量爆 + vix<18 隔日 intraday:")
    sub_all = events[(events["vol_ratio"] >= 2.0) & (events["vix"] < 18)]
    for plabel, sub in [
        ("2017-2019", sub_all[sub_all["year"] <= 2019]),
        ("2020-2022", sub_all[(sub_all["year"] >= 2020) & (sub_all["year"] <= 2022)]),
        ("2023-2025", sub_all[sub_all["year"] >= 2023]),
    ]:
        if len(sub) < 50: continue
        n = len(sub)
        gap = sub["overnight_gap_pct"].mean()
        intra = sub["intraday_pct"].mean()
        gap_pos = (sub["overnight_gap_pct"] > 0).mean() * 100
        intra_neg = (sub["intraday_pct"] < 0).mean() * 100
        print(f"    {plabel}: n={n}, gap={gap:+.2f}%(win {gap_pos:.0f}%), intraday={intra:+.2f}%(neg pct {intra_neg:.0f}%)")


def main():
    print("=" * 80)
    print("  漲停隔日反轉 alpha (1d intraday + overnight gap)")
    print("=" * 80)
    universe = load_universe()
    vix_map = load_vix()
    events = collect_events(universe, vix_map)
    analyze(events)
    oos_check(events)


if __name__ == "__main__":
    main()
