"""
ABC 三重共識 + AB 雙重共識 OOS + MCPT 驗證

Original claim:
  ABC: 60d alpha +20.98% (t=+5.90, n=53)
  AB:  60d alpha +10.01% (t=+4.36, n=126)

驗證：
  OOS：split 3 期
  MCPT：1000 次 random shuffle
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
LARGE_CAP_EXCLUDE = {"2330","2317","2454","2412","2891","2882","2002","1303","1301","2308"}
N_PERMUTE = 1000


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_price(tk):
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists() or p.stat().st_size < 500: return pd.DataFrame()
    try: df = pd.read_parquet(p)
    except: return pd.DataFrame()
    if df.empty: return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["pct"] = df["close"].pct_change() * 100
    df["vol_ma"] = df["volume"].rolling(60).mean()
    df["vol_std"] = df["volume"].rolling(60).std()
    df["vol_z"] = (df["volume"] - df["vol_ma"]) / df["vol_std"]
    return df


def find_a(tk, market_median):
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


def find_b(tk, prices):
    if prices.empty or len(prices)<5: return []
    inst_p = CACHE / f"TaiwanStockInstitutionalInvestorsBuySell_{tk}.parquet"
    if not inst_p.exists(): return []
    try: inst = pd.read_parquet(inst_p)
    except: return []
    inst["date"] = pd.to_datetime(inst["date"])
    inst["net"] = inst["buy"] - inst["sell"]
    p2 = prices.copy()
    p2["lu"] = (p2["pct"]>=9.0).astype(int)
    p2["lu_3d"] = p2["lu"].rolling(3).sum()
    inst_d = inst.groupby("date")["net"].sum().reset_index()
    inst_d.rename(columns={"net":"inst_net"}, inplace=True)
    m = p2.merge(inst_d, on="date", how="left").fillna(0)
    return m[(m["lu_3d"]>=2) & (m["inst_net"]>=200000)]["date"].tolist()


def find_c(tk, prices):
    if tk in LARGE_CAP_EXCLUDE or prices.empty: return []
    hp = CACHE / f"TaiwanStockHoldingSharesPer_{tk}.parquet"
    if not hp.exists(): return []
    try: h = pd.read_parquet(hp)
    except: return []
    h["date"] = pd.to_datetime(h["date"])
    retail = ["1-999","1,000-5,000","5,001-10,000","10,001-15,000",
              "15,001-20,000","20,001-30,000","30,001-40,000","40,001-50,000"]
    h["is_r"] = h["HoldingSharesLevel"].isin(retail)
    grp = h.groupby(["date","is_r"])["percent"].sum().unstack(fill_value=0)
    if True not in grp.columns: return []
    rdf = pd.DataFrame({"date":grp.index, "rp":grp[True].values}).sort_values("date")
    rdf["p20"] = rdf["rp"].rolling(60).quantile(0.20)
    rdf["s1"] = (rdf["rp"]<rdf["p20"]).astype(int)
    p2 = prices[["date","vol_z"]].copy()
    m = p2.merge(rdf[["date","s1"]], on="date", how="left")
    m["s1"] = m["s1"].ffill()
    return m[(m["s1"]==1) & (m["vol_z"]>=2.5)]["date"].tolist()


def find_combos(a_dates, b_dates, c_dates):
    a_set = sorted([pd.Timestamp(d) for d in a_dates])
    b_set = sorted([pd.Timestamp(d) for d in b_dates])
    c_set = sorted([pd.Timestamp(d) for d in c_dates])
    ab, ac, abc = [], [], []
    for a in a_set:
        end = a + pd.Timedelta(days=COMBO_WINDOW)
        b_in = [b for b in b_set if a<=b<=end]
        c_in = [c for c in c_set if a<=c<=end]
        if b_in: ab.append(min(b_in))
        if c_in: ac.append(min(c_in))
        if b_in and c_in: abc.append(min(min(b_in), min(c_in)))
    return {"AB":ab, "AC":ac, "ABC":abc}


def compute_market_median():
    print("  計算市場 median YoY...")
    all_yoy = []
    for p in CACHE.glob("TaiwanStockMonthRevenue_*.parquet"):
        try:
            r = pd.read_parquet(p)
            if len(r)<24: continue
            r = r.sort_values(["revenue_year","revenue_month"]).reset_index(drop=True)
            r["prior"] = r["revenue"].shift(12)
            r["yoy"] = (r["revenue"]/r["prior"]-1)*100
            r = r[r["prior"]>1e7]
            if r.empty: continue
            r["date"] = pd.to_datetime(r["date"])
            r2 = r[r["yoy"].abs()<500][["date","yoy"]]
            all_yoy.append(r2)
        except: continue
    df = pd.concat(all_yoy, ignore_index=True)
    df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym")["yoy"].median().to_dict()


def collect_combo_events(combo_name="ABC"):
    universe = load_universe()
    print(f"  Universe: {len(universe)}")
    market_median = compute_market_median()

    events = []  # {ticker, signal_date, fwd_60d, baseline_mean, baseline_std, year}
    n_processed = 0
    for tk in universe:
        prices = load_price(tk)
        if prices.empty or len(prices)<200: continue
        a = find_a(tk, market_median)
        b = find_b(tk, prices)
        c = find_c(tk, prices)
        combos = find_combos(a, b, c)
        target_dates = combos.get(combo_name, [])
        if not target_dates:
            n_processed += 1
            continue

        px_idx = prices.set_index("date")["close"]
        if len(px_idx)<HOLD_DAYS+60: continue

        # baseline
        rng = np.random.RandomState(hash(tk)%(2**32))
        n_base = min(50, len(px_idx)-HOLD_DAYS-60)
        if n_base<=0: continue
        bidx = rng.choice(range(60, len(px_idx)-HOLD_DAYS), size=n_base, replace=False)
        baseline = []
        for j in bidx:
            if px_idx.iloc[j]>0:
                baseline.append((px_idx.iloc[j+HOLD_DAYS]/px_idx.iloc[j]-1)*100)
        if not baseline: continue
        bm = np.mean(baseline); bs = np.std(baseline)

        for sd in target_dates:
            future = px_idx[px_idx.index > sd]
            if len(future)<=HOLD_DAYS: continue
            entry = future.iloc[0]
            if entry>0:
                fwd = (future.iloc[HOLD_DAYS]/entry-1)*100
                events.append({
                    "ticker":tk, "signal_date":sd, "fwd_60d":fwd,
                    "baseline_mean":bm, "baseline_std":bs, "year":sd.year,
                })
        n_processed += 1
        if n_processed % 300 == 0:
            print(f"  [{n_processed}/{len(universe)}] {combo_name} events={len(events)}")

    return pd.DataFrame(events)


def event_stats(df, label):
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
    return {"n":n, "alpha":alpha, "t":t, "win":win}


def mcpt_test(df, label):
    if df.empty: return None
    n_events = len(df)
    rng = np.random.RandomState(42)
    fwd = df["fwd_60d"].values
    base = df["baseline_mean"].values
    real = fwd.mean() - base.mean()
    fakes = []
    for _ in range(N_PERMUTE):
        # Shuffle: 隨機抽取 N events from baseline distribution
        # 用 events 整體的 baseline distribution 做 null
        fake_signal = rng.choice(base, size=n_events, replace=True)
        # 加 noise（baseline std）
        fake_signal = fake_signal + rng.normal(0, df["baseline_std"].mean(), n_events)
        fake_alpha = fake_signal.mean() - base.mean()
        fakes.append(fake_alpha)
    fakes = np.array(fakes)
    p = (fakes >= real).sum() / N_PERMUTE
    print(f"\n  🎲 MCPT {label}: real={real:+.2f}%, p={p:.4f} {'✅' if p<0.05 else '❌'}")
    return p


def run_combo(combo_name):
    print(f"\n{'='*80}")
    print(f"  ▶ {combo_name}")
    print(f"{'='*80}")
    events = collect_combo_events(combo_name)
    print(f"  Total events: {len(events)}")
    if events.empty: return

    print(f"\n  Full sample:")
    full = event_stats(events, "Full")

    print(f"\n  📅 OOS split:")
    splits = [
        ("2017-2019", events[events["year"]<=2019]),
        ("2020-2022", events[(events["year"]>=2020)&(events["year"]<=2022)]),
        ("2023-2025", events[events["year"]>=2023]),
    ]
    oos = []
    for lbl, sub in splits:
        r = event_stats(sub, lbl)
        if r: oos.append((lbl,r))

    p = mcpt_test(events, combo_name)

    n_robust = sum(1 for _,r in oos if r["alpha"]>1 and (r["t"] or 0)>2)
    print(f"\n  🎯 {combo_name} 結論：OOS robust {n_robust}/{len(oos)}, MCPT p={p:.4f}")
    if n_robust == len(oos) and p<0.05:
        print(f"  ✅ 通過驗證")
    else:
        print(f"  ⚠️ 未全部通過")


def main():
    print("="*80)
    print(f"  多因子 Combo OOS + MCPT 驗證 (hold {HOLD_DAYS}d)")
    print("="*80)
    for combo in ["AB", "ABC"]:
        run_combo(combo)


if __name__ == "__main__":
    main()
