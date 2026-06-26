"""
量縮漲停 × 法人加碼 combo backtest

Hypothesis:
  量縮漲停 (vr<0.8) alpha +5.22%/20d 已驗證
  法人加碼（外資/投信至少一方淨買 >= 1000 張）= 主力 confirmation
  量縮 + 法人買 → 期望 super-additive

Trigger:
  個股當日 ≥ +9.5% AND vol_ratio < 0.8 AND
  (foreign_net >= 1000000 OR trust_net >= 1000000)  # 1000 張 = 1M 股

OOS + MCPT
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
HOLD_DAYS = 20  # 用 quiet_limitup 已驗證 sweet spot
INST_THRESHOLD = 1_000_000  # 1000 張
N_PERMUTE = 1000


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def collect_events(universe):
    print("  收集量縮漲停 + 法人 events...")
    quiet_only = []   # 量縮漲停 only
    combo = []        # 量縮漲停 + 法人加碼

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

        # Triggers
        triggers = px[(px["pct"] >= 9.5) & (px["vol_ratio"] < 0.8) & px["vol_ratio"].notna()]
        if triggers.empty: continue

        # 法人資料
        inst_p = CACHE / f"TaiwanStockInstitutionalInvestorsBuySell_{tk}.parquet"
        inst_df = pd.DataFrame()
        if inst_p.exists():
            try:
                inst_df = pd.read_parquet(inst_p)
                inst_df["date"] = pd.to_datetime(inst_df["date"])
                inst_df["net"] = inst_df["buy"] - inst_df["sell"]
                # daily aggregate by name
                inst_pivot = inst_df.pivot_table(index="date", columns="name",
                                                  values="net", aggfunc="sum").fillna(0)
            except:
                inst_pivot = pd.DataFrame()
        else:
            inst_pivot = pd.DataFrame()

        # baseline
        if len(px) < HOLD_DAYS + 60: continue
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(px) - HOLD_DAYS - 60)
        if n_base <= 0: continue
        bidx = rng.choice(range(60, len(px) - HOLD_DAYS), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            entry = px["close"].iloc[j]
            if entry > 0:
                baseline.append((px["close"].iloc[j+HOLD_DAYS]/entry-1)*100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        for _, row in triggers.iterrows():
            sd = row["date"]
            # forward return
            future = px[px["date"] > sd]
            if len(future) <= HOLD_DAYS: continue
            entry = future["close"].iloc[0]
            if entry <= 0: continue
            fwd = (future["close"].iloc[HOLD_DAYS]/entry-1)*100

            # 法人 confirmation: 當日或前一日法人買 >= 1000 張
            inst_buy = False
            if not inst_pivot.empty:
                # window: 當日 ± 1 日
                window = inst_pivot[(inst_pivot.index >= sd - pd.Timedelta(days=1)) &
                                    (inst_pivot.index <= sd)]
                if not window.empty:
                    foreign_max = window.get("Foreign_Investor", pd.Series([0])).max()
                    trust_max = window.get("Investment_Trust", pd.Series([0])).max()
                    if foreign_max >= INST_THRESHOLD or trust_max >= INST_THRESHOLD:
                        inst_buy = True

            event = {
                "ticker": tk, "signal_date": sd, "fwd_20d": fwd,
                "baseline_mean": bm, "baseline_std": bs,
                "year": sd.year, "vol_ratio": row["vol_ratio"],
            }
            quiet_only.append(event)
            if inst_buy:
                combo.append(event)

        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(universe)}] quiet={len(quiet_only)}, combo={len(combo)}")

    return pd.DataFrame(quiet_only), pd.DataFrame(combo)


def event_summary(df, label):
    if df.empty or len(df)<30:
        print(f"  {label}: n={len(df)} (太少)")
        return None
    n = len(df)
    sig = df["fwd_20d"].mean()
    bm = df["baseline_mean"].mean()
    bs = df["baseline_std"].mean()
    alpha = sig - bm
    win = (df["fwd_20d"]>0).mean()*100
    t = alpha / (bs/np.sqrt(n)) if bs>0 else None
    t_str = f"{t:+.2f}" if t else "n/a"
    print(f"  {label}: n={n}, signal={sig:+.2f}%, baseline={bm:+.2f}%, alpha={alpha:+.2f}%, win={win:.0f}%, t={t_str}")
    return {"n":n, "alpha":alpha, "t":t}


def run_oos(events, label):
    print(f"\n  📅 {label} OOS:")
    for plabel, sub in [
        ("2017-2019", events[events["year"]<=2019]),
        ("2020-2022", events[(events["year"]>=2020)&(events["year"]<=2022)]),
        ("2023-2025", events[events["year"]>=2023]),
    ]:
        event_summary(sub, plabel)


def mcpt_test(events, full_pool, label):
    """MCPT: combo events 的 alpha 是否顯著高於 quiet_only random subset"""
    if events.empty or full_pool.empty: return None
    n_combo = len(events)
    if n_combo > len(full_pool): return None
    rng = np.random.RandomState(42)
    real = events["fwd_20d"].mean() - events["baseline_mean"].mean()
    fwd = full_pool["fwd_20d"].values
    base = full_pool["baseline_mean"].values
    fakes = []
    n_total = len(full_pool)
    for _ in range(N_PERMUTE):
        idx = rng.choice(n_total, size=n_combo, replace=False)
        fake = fwd[idx] - base[idx]
        fakes.append(fake.mean())
    fakes = np.array(fakes)
    p = (fakes >= real).sum() / N_PERMUTE
    print(f"\n  🎲 MCPT {label} (vs random subset of quiet_only): real={real:+.2f}%, p={p:.4f} {'✅' if p<0.05 else '❌'}")
    return p


def main():
    print("="*80)
    print(f"  量縮漲停 × 法人加碼 combo (hold {HOLD_DAYS}d, 法人門檻 {INST_THRESHOLD/1e6:.0f}M 股)")
    print("="*80)
    universe = load_universe()
    print(f"  Universe: {len(universe)}")
    quiet_only, combo = collect_events(universe)

    print("\n" + "="*80)
    print("  📊 Full sample")
    print("="*80)
    event_summary(quiet_only, "Quiet Limitup only")
    event_summary(combo, "Quiet + 法人加碼 (combo)")

    run_oos(quiet_only, "Quiet only")
    run_oos(combo, "Combo")

    print("\n" + "="*80)
    print("  🎲 MCPT")
    print("="*80)
    # combo 是否真的比 quiet_only random subset 強
    mcpt_test(combo, quiet_only, "Combo vs Quiet random")


if __name__ == "__main__":
    main()
