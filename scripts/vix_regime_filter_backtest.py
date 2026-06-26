"""
VIX regime filter for quiet limit-up / limit-down 訊號

Hypothesis:
  高 VIX (>25) = 全球 risk-off → mean reversion alpha 增強
  低 VIX (<18) = 平靜市場 → 個股 idiosyncratic 反彈弱

實作：
  對量縮漲停 (vr<0.8, pct>=9.5) 和量縮跌停 (vr<0.8, pct<=-9.5) 兩個 trigger
  按當日 VIX 分 bucket: low (<18), mid (18-25), high (>25), extreme (>35)
  比較各 regime 的 forward 20d alpha
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
HOLD = 20


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_vix():
    """yfinance ^VIX history"""
    import yfinance as yf
    h = yf.Ticker("^VIX").history(period="3000d", auto_adjust=False)
    df = pd.DataFrame({"date": pd.to_datetime(h.index).tz_localize(None),
                       "vix": h["Close"].values})
    return df.set_index("date")["vix"].to_dict()


def collect_events(universe, vix_map, direction="up"):
    print(f"  收集量縮{'漲停' if direction == 'up' else '跌停'} events...")
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

        if direction == "up":
            triggers = px[(px["pct"] >= 9.5) & (px["vol_ratio"] < 0.8) & px["vol_ratio"].notna()]
        else:
            triggers = px[(px["pct"] <= -9.5) & (px["vol_ratio"] < 0.8) & px["vol_ratio"].notna()]
        if triggers.empty: continue

        if len(px) < HOLD + 60: continue
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px) - HOLD - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px) - HOLD), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            entry = px["close"].iloc[j]
            if entry > 0:
                baseline.append((px["close"].iloc[j+HOLD]/entry-1)*100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        for _, row in triggers.iterrows():
            sd = row["date"]
            future = px[px["date"] > sd]
            if len(future) <= HOLD: continue
            entry = future["close"].iloc[0]
            if entry <= 0: continue
            fwd = (future["close"].iloc[HOLD]/entry-1)*100
            vix = vix_map.get(sd, np.nan)
            events.append({
                "ticker": tk, "date": sd,
                "fwd_20d": fwd, "baseline_mean": bm, "baseline_std": bs,
                "vix": vix, "year": sd.year,
            })
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")
    return pd.DataFrame(events).dropna(subset=["vix"])


def analyze_vix_buckets(events, label):
    print(f"\n  📊 {label} by VIX bucket:")
    print(f"  {'bucket':<20} {'n':<7} {'alpha':<8} {'win%':<6} {'t':<7}")
    buckets = [
        ("low (vix<18)", events[events["vix"] < 18]),
        ("mid (18-25)", events[(events["vix"] >= 18) & (events["vix"] < 25)]),
        ("high (25-35)", events[(events["vix"] >= 25) & (events["vix"] < 35)]),
        ("extreme (≥35)", events[events["vix"] >= 35]),
    ]
    for blabel, sub in buckets:
        if len(sub) < 30: continue
        n = len(sub)
        alpha = sub["fwd_20d"].mean() - sub["baseline_mean"].mean()
        bs = sub["baseline_std"].mean()
        win = (sub["fwd_20d"] > 0).mean() * 100
        t = alpha / (bs/np.sqrt(n)) if bs > 0 else None
        t_str = f"{t:+.2f}" if t else "n/a"
        verdict = "⭐" if alpha > 8 and (t or 0) > 5 else ""
        print(f"  {blabel:<20} {n:<7} {alpha:+.2f}%  {win:.1f}%  {t_str}  {verdict}")


def oos_check_vix(events, label):
    """對 vix>35 和 vix<18 跑 OOS"""
    print(f"\n  📅 {label} OOS:")
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
            alpha = sub["fwd_20d"].mean() - sub["baseline_mean"].mean()
            bs = sub["baseline_std"].mean()
            t = alpha / (bs / np.sqrt(n)) if bs > 0 else None
            t_str = f"{t:+.2f}" if t else "n/a"
            verdict = "✅" if abs(alpha) > 1 and abs(t or 0) > 2 else "⚠️"
            print(f"    {plabel}: n={n}, alpha={alpha:+.2f}%, t={t_str} {verdict}")


def mcpt(events, label):
    """MCPT: vix>=35 vs full pool"""
    extreme = events[events["vix"] >= 35]
    if len(extreme) < 30: return
    real = extreme["fwd_20d"].mean() - extreme["baseline_mean"].mean()
    fwd = events["fwd_20d"].values
    base = events["baseline_mean"].values
    rng = np.random.RandomState(42)
    fakes = []
    n_ext = len(extreme)
    for _ in range(1000):
        idx = rng.choice(len(events), size=n_ext, replace=False)
        fa = fwd[idx].mean() - base[idx].mean()
        fakes.append(fa)
    fakes = np.array(fakes)
    p = (fakes >= real).sum() / 1000
    print(f"\n  🎲 MCPT {label} vix≥35: real={real:+.2f}%, p={p:.4f} {'✅' if p<0.05 else '❌'}")


def main():
    print("=" * 80)
    print("  VIX regime filter for quiet 訊號")
    print("=" * 80)
    universe = load_universe()
    vix_map = load_vix()
    print(f"  Universe: {len(universe)}, VIX days: {len(vix_map)}")

    print("\n--- 量縮漲停 ---")
    events_up = collect_events(universe, vix_map, "up")
    analyze_vix_buckets(events_up, "Quiet Limitup")
    oos_check_vix(events_up, "Quiet Limitup")
    mcpt(events_up, "Quiet Limitup")

    print("\n--- 量縮跌停 ---")
    events_down = collect_events(universe, vix_map, "down")
    analyze_vix_buckets(events_down, "Quiet Limitdown Reversal")
    oos_check_vix(events_down, "Quiet Limitdown")
    mcpt(events_down, "Quiet Limitdown")


if __name__ == "__main__":
    main()
