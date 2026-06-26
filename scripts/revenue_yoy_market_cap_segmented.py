"""
月營收 YoY alpha 在不同市值的 segment 分析

Hypothesis:
  - 小型股 PEAD alpha > 大型股（熱錢追小、information asymmetry 大）
  - 中型股 sweet spot

方法：
  1. 計算每檔最近一日市值 = close × shares
  2. 分 quartile：mega(top 25%), large, mid, small(bottom 25%)
  3. 對每 quartile 跑 Revenue YoY (excess > 30%) backtest
  4. OOS + MCPT 驗證最強的 segment

如果某 segment alpha 顯著強：
  → 升級 scanner 加 segment filter
  → 對該 segment 給更高信心標籤
"""
from __future__ import annotations
import io, sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
HOLD_DAYS = 60
N_PERMUTE = 1000


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def get_market_cap(tk: str) -> float:
    """取最新市值（USD billion 級的不適用台股，用 NT$ million）"""
    sp = CACHE / f"TaiwanStockShareholding_{tk}.parquet"
    pp = TW_CACHE / f"{tk}.parquet"
    if not sp.exists() or not pp.exists(): return 0.0
    try:
        s = pd.read_parquet(sp)
        p = pd.read_parquet(pp)
        if s.empty or p.empty: return 0.0
        s = s.sort_values("date").iloc[-1]
        p = p.sort_values("date").iloc[-1]
        shares = float(s["NumberOfSharesIssued"])
        close = float(p["close"])
        return shares * close / 1e6  # NT$ 百萬
    except: return 0.0


def compute_market_median_yoy():
    print("  計算市場 median YoY...")
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


def collect_events_with_cap(universe: list, market_median: dict):
    """收集 Revenue YoY events 並加 market cap"""
    events = []
    n_skip = 0
    for i, tk in enumerate(universe):
        # market cap
        cap = get_market_cap(tk)
        if cap <= 0:
            n_skip += 1
            continue

        # revenue triggers
        rp = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
        if not rp.exists(): continue
        try:
            rev = pd.read_parquet(rp)
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

        # forward returns
        pp = TW_CACHE / f"{tk}.parquet"
        if not pp.exists() or pp.stat().st_size < 500: continue
        try: px = pd.read_parquet(pp)
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
                baseline.append((px_idx.iloc[j + HOLD_DAYS] / px_idx.iloc[j] - 1) * 100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        for _, row in triggers.iterrows():
            sd = row["date"]
            future = px_idx[px_idx.index > sd]
            if len(future) <= HOLD_DAYS: continue
            entry = future.iloc[0]
            if entry > 0:
                fwd = (future.iloc[HOLD_DAYS] / entry - 1) * 100
                events.append({
                    "ticker": tk, "signal_date": sd, "fwd_60d": fwd,
                    "baseline_mean": bm, "baseline_std": bs,
                    "market_cap_m": cap, "year": sd.year,
                })
        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}, skipped={n_skip}")
    return pd.DataFrame(events)


def segment_analysis(events: pd.DataFrame):
    """按市值 quartile 分析 alpha"""
    print(f"\n  Total events: {len(events)}")
    if events.empty: return

    # Quartile by market cap
    events["cap_q"] = pd.qcut(events["market_cap_m"], q=4,
                              labels=["Q1_small", "Q2_mid", "Q3_large", "Q4_mega"])

    print("\n  📊 By market cap quartile:")
    print(f"  {'segment':<12} {'cap范圍':<25} {'n':<7} {'alpha':<8} {'t':<7} {'win%':<6}")
    print(f"  {'-'*65}")
    for q in ["Q1_small", "Q2_mid", "Q3_large", "Q4_mega"]:
        sub = events[events["cap_q"] == q]
        if len(sub) < 100: continue
        n = len(sub)
        sig = sub["fwd_60d"].mean()
        bm = sub["baseline_mean"].mean()
        bs = sub["baseline_std"].mean()
        alpha = sig - bm
        win = (sub["fwd_60d"] > 0).mean() * 100
        t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
        cap_min = sub["market_cap_m"].min()
        cap_max = sub["market_cap_m"].max()
        cap_range = f"{cap_min:.0f}M-{cap_max:.0f}M"
        t_str = f"{t:+.2f}" if t else "n/a"
        print(f"  {q:<12} {cap_range:<25} {n:<7} {alpha:+.2f}%  {t_str:<7} {win:.1f}%")

    # OOS for best segment
    print("\n  📅 OOS for each segment (2020-2022, 2023-2025):")
    for q in ["Q1_small", "Q2_mid", "Q3_large", "Q4_mega"]:
        sub = events[events["cap_q"] == q]
        if len(sub) < 100: continue
        print(f"\n  --- {q} ---")
        for label, period_sub in [
            ("2020-2022", sub[(sub["year"]>=2020) & (sub["year"]<=2022)]),
            ("2023-2025", sub[sub["year"]>=2023]),
        ]:
            if len(period_sub) < 50: continue
            n = len(period_sub)
            sig = period_sub["fwd_60d"].mean()
            bm = period_sub["baseline_mean"].mean()
            bs = period_sub["baseline_std"].mean()
            alpha = sig - bm
            t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
            t_str = f"{t:+.2f}" if t else "n/a"
            verdict = "✅" if alpha > 1.5 and (t or 0) > 2 else "⚠️"
            print(f"    {label}: n={n}, alpha={alpha:+.2f}%, t={t_str} {verdict}")


def main():
    print("=" * 80)
    print("  Revenue YoY × 市值 Segmentation 分析")
    print("=" * 80)
    universe = load_universe()
    print(f"  Universe: {len(universe)}")
    market_median = compute_market_median_yoy()

    events = collect_events_with_cap(universe, market_median)
    segment_analysis(events)


if __name__ == "__main__":
    main()
