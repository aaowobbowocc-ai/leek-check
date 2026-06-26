"""
行庫共識度 anti-filter OOS + MCPT 驗證

Original claim: 5+ 行庫同買後 60d alpha -1.62% (t=-28.46, n=161K)

驗證：
  OOS：split 2 期（2021H2-2023, 2024-2026，因為資料只到 2021-06）
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
EXTRAS = ROOT / "data" / "cache" / "finmind" / "extras"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"

HOLD_DAYS = 60
THRESHOLD = 5  # 5+ 行庫同買
N_PERMUTE = 1000


def collect_events():
    """收集 (ticker, date, fwd_ret, baseline_mean) events"""
    print("  載入 13M rows 行庫資料...")
    gb = pd.read_parquet(EXTRAS / "government_bank_buysell.parquet")
    gb["date"] = pd.to_datetime(gb["date"])
    gb["bought"] = ((gb["buy_amount"] - gb["sell_amount"]) > 0).astype(int)
    cons = gb.groupby(["date", "stock_id"])["bought"].sum().reset_index()
    cons.rename(columns={"bought": "n_banks"}, inplace=True)
    triggers = cons[cons["n_banks"] >= THRESHOLD].copy()
    print(f"  Trigger events ≥ {THRESHOLD} banks: {len(triggers):,}")

    # 限制到前 500 ticker (處理時間)
    top_tks = triggers["stock_id"].value_counts().head(500).index.tolist()
    triggers = triggers[triggers["stock_id"].isin(top_tks)]

    events = []
    n_processed = 0
    for tk in top_tks:
        tk_trig = triggers[triggers["stock_id"] == tk]["date"].tolist()
        if not tk_trig: continue
        p = TW_CACHE / f"{tk}.parquet"
        if not p.exists() or p.stat().st_size < 500: continue
        try:
            px = pd.read_parquet(p)
        except: continue
        if px.empty or len(px) < 200: continue
        px["date"] = pd.to_datetime(px["date"])
        px_idx = px.set_index("date")["close"]

        # baseline samples
        if len(px_idx) < HOLD_DAYS + 60: continue
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(30, len(px_idx) - HOLD_DAYS - 60)
        base_idx = rng.choice(range(60, len(px_idx) - HOLD_DAYS), size=n_base, replace=False)
        baseline = []
        for j in base_idx:
            if px_idx.iloc[j] > 0:
                baseline.append((px_idx.iloc[j + HOLD_DAYS] / px_idx.iloc[j] - 1) * 100)
        base_mean = np.mean(baseline) if baseline else 0
        base_std = np.std(baseline) if baseline else 0

        for sd in tk_trig:
            future = px_idx[px_idx.index > sd]
            if len(future) <= HOLD_DAYS: continue
            entry = future.iloc[0]
            if entry > 0:
                fwd = (future.iloc[HOLD_DAYS] / entry - 1) * 100
                events.append({
                    "ticker": tk, "signal_date": sd, "fwd_60d": fwd,
                    "baseline_mean": base_mean, "baseline_std": base_std,
                    "year": sd.year,
                })
        n_processed += 1
        if n_processed % 100 == 0:
            print(f"  [{n_processed}/500] events={len(events)}")

    return pd.DataFrame(events)


def event_summary(df: pd.DataFrame, label: str):
    if df.empty or len(df) < 30:
        print(f"  {label}: n={len(df)} (太少)")
        return None
    n = len(df)
    sig_mean = df["fwd_60d"].mean()
    base_mean = df["baseline_mean"].mean()
    base_std = df["baseline_std"].mean()
    alpha = sig_mean - base_mean
    win = (df["fwd_60d"] > 0).mean() * 100
    t = alpha / (base_std / np.sqrt(n)) if base_std > 0 else None
    t_str = f"{t:+.2f}" if t else "n/a"
    print(f"  {label}: n={n}, signal={sig_mean:+.2f}%, baseline={base_mean:+.2f}%, "
          f"alpha={alpha:+.2f}%, win={win:.0f}%, t={t_str}")
    return {"n": n, "alpha": alpha, "t": t, "win": win}


def main():
    print("=" * 80)
    print(f"  行庫 anti-filter OOS + MCPT 驗證 ({THRESHOLD}+ banks, hold {HOLD_DAYS}d)")
    print("=" * 80)
    events = collect_events()
    print(f"\n  Total events: {len(events)}")

    print("\n  ▶ Full sample:")
    full = event_summary(events, "Full")

    print("\n  📅 OOS split:")
    n = len(events)
    splits = [
        ("2021-2022", events[events["year"] <= 2022]),
        ("2023-2024", events[(events["year"] >= 2023) & (events["year"] <= 2024)]),
        ("2025-2026", events[events["year"] >= 2025]),
    ]
    oos_results = []
    for label, sub in splits:
        r = event_summary(sub, label)
        if r: oos_results.append((label, r))

    print(f"\n  🎲 MCPT (n={N_PERMUTE}):")
    if not full:
        print("  No full sample to test")
        return
    real_alpha = full["alpha"]
    n_events = full["n"]

    # Pool: events 的 fwd_60d minus baseline (residual after baseline subtracted)
    rng = np.random.RandomState(42)
    fake_alphas = []
    fwd_arr = events["fwd_60d"].values
    base_arr = events["baseline_mean"].values
    n_total = len(events)

    for _ in range(N_PERMUTE):
        # Random sample n_events 個 trade days，計算 alpha
        idx = rng.choice(n_total, size=n_events, replace=False)
        fake_signal_mean = fwd_arr[idx].mean()
        fake_baseline_mean = base_arr[idx].mean()
        fake_alpha = fake_signal_mean - fake_baseline_mean
        fake_alphas.append(fake_alpha)

    fake_alphas = np.array(fake_alphas)
    p_value = (fake_alphas <= real_alpha).sum() / N_PERMUTE  # 反向：alpha 越負越強
    print(f"    Real alpha: {real_alpha:+.3f}% (negative = anti-signal)")
    print(f"    Random mean: {fake_alphas.mean():+.3f}%, std: {fake_alphas.std():.3f}")
    print(f"    p-value (alpha <= real): {p_value:.4f}")

    print("\n" + "=" * 80)
    print("  🎯 結論")
    print("=" * 80)
    n_robust = sum(1 for _, r in oos_results if r["alpha"] < 0 and (r["t"] or 0) < -2)
    if n_robust == len(oos_results) and p_value < 0.05:
        print(f"  ✅ 全部 {len(oos_results)} OOS 期都 robust 反向 + MCPT p<0.05")
        print("  → 保留 anti-filter 整合")
    else:
        print(f"  ⚠️ OOS robust: {n_robust}/{len(oos_results)} 期, MCPT p={p_value:.4f}")
        print("  → 評估是否撤回整合")


if __name__ == "__main__":
    main()
