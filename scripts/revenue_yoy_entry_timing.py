"""
Revenue YoY Entry Timing 分析 — PEAD drift 形狀

Hypothesis:
  公告當日 entry vs +1, +3, +7, +14 天 entry，alpha 差異 = drift timing

如果 drift 集中在 early days → 必須立刻進場
如果 drift 平緩 → 可延遲進場（規避雜訊）

輸出最佳 entry_offset 給 scanner 使用
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
ENTRY_OFFSETS = [0, 1, 3, 7, 14]  # 公告後 N 天進場


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


def collect_events_with_offsets(universe, market_median):
    """每個 trigger 計算多個 offset 的 forward return"""
    events = []
    for i, tk in enumerate(universe):
        rp = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
        if not rp.exists(): continue
        try: rev = pd.read_parquet(rp)
        except: continue
        if rev.empty or len(rev) < 24: continue
        rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
        rev["prior"] = rev["revenue"].shift(12)
        rev["yoy"] = (rev["revenue"] / rev["prior"] - 1) * 100
        rev["date"] = pd.to_datetime(rev["date"])
        rev["ym"] = rev["date"].dt.to_period("M")
        rev["mkt_med"] = rev["ym"].map(market_median)
        rev["excess"] = rev["yoy"] - rev["mkt_med"]
        triggers = rev[
            (rev["excess"] > 30) &
            (rev["yoy"] > 0) &
            (rev["yoy"] < 200) &
            rev["yoy"].notna() &
            (rev["prior"] > 1e7)
        ]
        if triggers.empty: continue

        pp = TW_CACHE / f"{tk}.parquet"
        if not pp.exists() or pp.stat().st_size < 500: continue
        try: px = pd.read_parquet(pp)
        except: continue
        if px.empty or len(px) < HOLD_DAYS + 60: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        for _, row in triggers.iterrows():
            sd = row["date"]
            future = px_idx[px_idx.index > sd]
            if len(future) <= max(ENTRY_OFFSETS) + HOLD_DAYS: continue
            event = {"ticker": tk, "signal_date": sd, "year": sd.year}
            for off in ENTRY_OFFSETS:
                if off + HOLD_DAYS >= len(future): continue
                entry = future.iloc[off]
                exit_ = future.iloc[off + HOLD_DAYS]
                if entry > 0:
                    event[f"fwd_off{off}"] = (exit_ / entry - 1) * 100
            if all(f"fwd_off{o}" in event for o in ENTRY_OFFSETS):
                events.append(event)
        if (i + 1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events)


def analyze_offsets(events):
    print(f"\n  Total events (with all offsets): {len(events)}")
    print(f"\n  📊 Alpha by entry offset (vs offset=0 mean):")
    print(f"  {'offset':<8} {'n':<7} {'mean':<8} {'vs_off0':<8} {'win%':<6}")
    base_off0_mean = events["fwd_off0"].mean()
    for off in ENTRY_OFFSETS:
        col = f"fwd_off{off}"
        sub = events[col].dropna()
        n = len(sub)
        mean = sub.mean()
        diff = mean - base_off0_mean
        win = (sub > 0).mean() * 100
        print(f"  +{off}d:    {n:<7} {mean:+.2f}%  {diff:+.2f}%   {win:.1f}%")

    # OOS
    print(f"\n  📅 OOS (2020-2022 vs 2023-2025) for each offset:")
    for off in ENTRY_OFFSETS:
        col = f"fwd_off{off}"
        for plabel, period_sub in [
            ("2020-22", events[(events["year"] >= 2020) & (events["year"] <= 2022)]),
            ("2023-25", events[events["year"] >= 2023]),
        ]:
            if len(period_sub) < 100: continue
            n = len(period_sub)
            mean = period_sub[col].mean()
            print(f"    +{off}d {plabel}: n={n}, mean={mean:+.2f}%")


def main():
    print("=" * 80)
    print("  Revenue YoY Entry Timing 分析")
    print("=" * 80)
    universe = load_universe()
    market_median = compute_market_median()
    events = collect_events_with_offsets(universe, market_median)
    analyze_offsets(events)


if __name__ == "__main__":
    main()
