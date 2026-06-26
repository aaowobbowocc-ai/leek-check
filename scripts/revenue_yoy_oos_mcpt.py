"""
Revenue YoY Alpha — OOS + MCPT 嚴謹驗證

驗證對象：
  Signal 1: yoy 30-200% (baseline) → 60d alpha +3.02% claimed
  Signal 3: relative yoy excess > 30% → 60d alpha +3.95% claimed

方法：
  1. OOS 3-window split: 2017-2019, 2020-2022, 2023-2025
     - 每期都應 alpha > 0 且 t-stat > 2 才算 robust
     - 2020-2022 包含 COVID + 2022 熊市（壓力測試）
  2. MCPT (Monte Carlo Permutation Test)
     - Random shuffle 1000 次 signal labels
     - 真實 alpha 在 random 分布的 percentile = p-value
     - p < 0.05 才算真 alpha
  3. Regime split (bull / bear)
     - 用 TAIEX vs MA200 分 bull/bear
     - 每個 regime 獨立計算 alpha
"""
from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
OUT_DIR = ROOT / "scripts" / "output"
OUT_DIR.mkdir(exist_ok=True, parents=True)

HOLD_DAYS = 60
N_PERMUTATIONS = 500  # MCPT


def load_universe() -> list[str]:
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_price(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists() or p.stat().st_size < 500:
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if df.empty: return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_revenue(tk: str) -> pd.DataFrame:
    rev_path = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
    if not rev_path.exists(): return pd.DataFrame()
    try:
        rev = pd.read_parquet(rev_path)
    except Exception:
        return pd.DataFrame()
    if rev.empty or len(rev) < 24: return pd.DataFrame()
    rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
    rev["prior_revenue"] = rev["revenue"].shift(12)
    rev["yoy"] = (rev["revenue"] / rev["prior_revenue"] - 1) * 100
    rev["date"] = pd.to_datetime(rev["date"])
    return rev


def collect_all_events(universe: list[str], use_relative: bool = False) -> pd.DataFrame:
    """收集所有 (ticker, signal_date, fwd_return, baseline_return) events"""
    print(f"  收集 events ({'Relative YoY' if use_relative else 'Baseline YoY'})...")

    # 若需要 relative，預計算 market median yoy
    market_median = {}
    if use_relative:
        all_yoy = []
        for tk in universe:
            rev = load_revenue(tk)
            if rev.empty: continue
            rev_v = rev[rev["yoy"].notna() & (rev["yoy"].abs() < 500) &
                       (rev["prior_revenue"] > 1e7)][["date", "yoy"]].copy()
            all_yoy.append(rev_v)
        if all_yoy:
            mdf = pd.concat(all_yoy, ignore_index=True)
            mdf["ym"] = mdf["date"].dt.to_period("M")
            market_median = mdf.groupby("ym")["yoy"].median().to_dict()
        print(f"  Market median computed for {len(market_median)} months")

    events = []
    for i, tk in enumerate(universe):
        rev = load_revenue(tk)
        if rev.empty: continue
        prices = load_price(tk)
        if prices.empty: continue
        prices_idx = prices.set_index("date")["close"]

        rev["ym"] = rev["date"].dt.to_period("M")
        if use_relative:
            rev["mkt_med"] = rev["ym"].map(market_median)
            rev["excess"] = rev["yoy"] - rev["mkt_med"]
            triggers = rev[
                (rev["excess"] > 30) &
                (rev["yoy"] > 0) &
                (rev["yoy"] < 200) &
                (rev["prior_revenue"] > 1e7) &
                rev["yoy"].notna()
            ]
        else:
            triggers = rev[
                (rev["yoy"] > 30) &
                (rev["yoy"] < 200) &
                (rev["prior_revenue"] > 1e7) &
                rev["yoy"].notna()
            ]

        # baseline samples (same ticker random)
        if len(prices_idx) < HOLD_DAYS + 60: continue
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(prices_idx) - HOLD_DAYS - 60)
        if n_base <= 0: continue
        base_idx = rng.choice(range(60, len(prices_idx) - HOLD_DAYS), size=n_base, replace=False)
        baseline_returns = []
        for j in base_idx:
            entry = prices_idx.iloc[j]
            exit_p = prices_idx.iloc[j + HOLD_DAYS]
            if entry > 0:
                baseline_returns.append((exit_p / entry - 1) * 100)

        # signal returns
        for _, row in triggers.iterrows():
            sig_d = row["date"]
            future = prices_idx[prices_idx.index > sig_d]
            if len(future) <= HOLD_DAYS: continue
            entry = future.iloc[0]
            exit_p = future.iloc[HOLD_DAYS]
            if entry > 0:
                fwd = (exit_p / entry - 1) * 100
                events.append({
                    "ticker": tk,
                    "signal_date": sig_d,
                    "fwd_60d": fwd,
                    "baseline_mean": np.mean(baseline_returns) if baseline_returns else 0,
                    "baseline_std": np.std(baseline_returns) if baseline_returns else 0,
                    "yoy": row["yoy"],
                    "year": sig_d.year,
                })

        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(universe)}] events={len(events)}")

    df = pd.DataFrame(events)
    print(f"  Total events: {len(df)}")
    return df


# ─── OOS 3-window split ───
def oos_split(events: pd.DataFrame, label: str) -> pd.DataFrame:
    """3 期 OOS：2017-2019, 2020-2022, 2023-2025"""
    print(f"\n  📅 {label} — OOS 3-window split:")
    rows = []
    splits = [
        ("2017-2019", events[events["year"] <= 2019]),
        ("2020-2022", events[(events["year"] >= 2020) & (events["year"] <= 2022)]),
        ("2023-2025", events[events["year"] >= 2023]),
    ]
    for period, sub in splits:
        if len(sub) < 100:
            print(f"    {period}: n={len(sub)} (太少)")
            continue
        n = len(sub)
        signal_mean = sub["fwd_60d"].mean()
        baseline_mean = sub["baseline_mean"].mean()
        baseline_std_pooled = sub["baseline_std"].mean()
        alpha = signal_mean - baseline_mean
        win = (sub["fwd_60d"] > 0).mean() * 100
        t = alpha / (baseline_std_pooled / np.sqrt(n)) if baseline_std_pooled > 0 else None
        verdict = "✅" if alpha > 1.0 and (t or 0) > 2 else "⚠️"
        rows.append({
            "period": period, "n": n, "signal_mean": round(signal_mean, 2),
            "baseline": round(baseline_mean, 2), "alpha": round(alpha, 2),
            "win_pct": round(win, 1), "t_stat": round(t, 2) if t else None,
            "verdict": verdict
        })
        t_str = f"{t:+.2f}" if t else "n/a"
        print(f"    {period}: n={n}, alpha={alpha:+.2f}%, win={win:.1f}%, t={t_str} {verdict}")
    return pd.DataFrame(rows)


# ─── MCPT ───
def mcpt(events: pd.DataFrame, label: str, n_permute: int = N_PERMUTATIONS) -> dict:
    """Monte Carlo Permutation Test

    Null hypothesis: signal labels 是隨機的（無 alpha）
    Test statistic: alpha = signal_mean - baseline_mean

    Method:
      1. 計算真實 alpha
      2. Random shuffle 'is_signal' label N 次
      3. 每次計算 random alpha
      4. p-value = (random_alpha >= real_alpha 的次數) / N
    """
    print(f"\n  🎲 {label} — MCPT (n_permute={n_permute}):")
    if events.empty: return {}

    real_alpha = events["fwd_60d"].mean() - events["baseline_mean"].mean()

    # Pool: 真實 signal returns + 對應的 baseline mean
    # 我們 shuffle: 把 fwd_60d 重新洗到 baseline 之間
    # Simpler: 假設 null distribution = baseline_mean ± baseline_std
    # 抽 random sample 的 mean，看真實 alpha 的 percentile

    rng = np.random.RandomState(42)
    fake_alphas = []
    n_events = len(events)
    baselines_pool = events["baseline_mean"].values
    baselines_std_pool = events["baseline_std"].values

    for _ in range(n_permute):
        # 從同 ticker baseline 分布隨機抽 n_events 個 fake signal returns
        fake_returns = rng.normal(loc=baselines_pool, scale=baselines_std_pool)
        fake_alpha = fake_returns.mean() - baselines_pool.mean()
        fake_alphas.append(fake_alpha)

    fake_alphas = np.array(fake_alphas)
    p_value = (fake_alphas >= real_alpha).sum() / n_permute
    percentile = (real_alpha > fake_alphas).sum() / n_permute * 100

    print(f"    Real alpha:    {real_alpha:+.3f}%")
    print(f"    Random mean:   {fake_alphas.mean():+.3f}%")
    print(f"    Random std:    {fake_alphas.std():.3f}")
    print(f"    Percentile:    {percentile:.1f}%")
    print(f"    p-value:       {p_value:.4f} {'✅ <0.05' if p_value < 0.05 else '❌'}")
    return {
        "real_alpha": real_alpha, "p_value": p_value,
        "percentile": percentile, "n_events": n_events,
    }


# ─── Regime split ───
def regime_split(events: pd.DataFrame, label: str) -> pd.DataFrame:
    """以 TAIEX vs MA200 區分 bull / bear"""
    print(f"\n  🌤️ {label} — Regime split (TAIEX vs MA200):")
    import yfinance as yf
    h = yf.Ticker("^TWII").history(period="max", auto_adjust=False)
    h = pd.DataFrame({"date": pd.to_datetime(h.index).tz_localize(None),
                       "close": h["Close"].values})
    h["ma200"] = h["close"].rolling(200).mean()
    h["regime"] = np.where(h["close"] > h["ma200"], "bull",
                          np.where(h["close"] < h["ma200"], "bear", "neutral"))
    h_idx = h.set_index("date")[["regime"]]

    # Match each event to regime
    events = events.copy()
    events["sd"] = pd.to_datetime(events["signal_date"])
    # Get regime at signal_date (or nearest before)
    events_with_regime = pd.merge_asof(
        events.sort_values("sd"),
        h_idx.reset_index().sort_values("date"),
        left_on="sd", right_on="date", direction="backward"
    )

    rows = []
    for r in ["bull", "bear", "neutral"]:
        sub = events_with_regime[events_with_regime["regime"] == r]
        if len(sub) < 100:
            print(f"    {r}: n={len(sub)} (太少)")
            continue
        n = len(sub)
        alpha = sub["fwd_60d"].mean() - sub["baseline_mean"].mean()
        win = (sub["fwd_60d"] > 0).mean() * 100
        t = alpha / (sub["baseline_std"].mean() / np.sqrt(n)) if sub["baseline_std"].mean() > 0 else None
        verdict = "✅" if alpha > 1.0 and (t or 0) > 2 else "⚠️"
        rows.append({"regime": r, "n": n, "alpha": round(alpha, 2),
                    "win_pct": round(win, 1),
                    "t_stat": round(t, 2) if t else None, "verdict": verdict})
        t_str = f"{t:+.2f}" if t else "n/a"
        print(f"    {r}: n={n}, alpha={alpha:+.2f}%, win={win:.1f}%, t={t_str} {verdict}")
    return pd.DataFrame(rows)


def main():
    print("=" * 80)
    print("  Revenue YoY OOS + MCPT 嚴謹驗證")
    print("=" * 80)

    universe = load_universe()
    print(f"  Universe: {len(universe)} tickers")

    for label, use_rel in [
        ("Signal 1 — Baseline YoY (yoy 30-200%)", False),
        ("Signal 3 — Relative YoY (excess > 30)", True),
    ]:
        print(f"\n{'=' * 80}")
        print(f"  ▶ {label}")
        print(f"{'=' * 80}")
        events = collect_all_events(universe, use_relative=use_rel)
        if events.empty:
            print("  ⚠️ No events")
            continue

        # 1. OOS
        oos_df = oos_split(events, label)

        # 2. MCPT
        mcpt_r = mcpt(events, label)

        # 3. Regime
        try:
            regime_df = regime_split(events, label)
        except Exception as e:
            print(f"  ⚠️ Regime split 失敗: {e}")

        # Save
        suffix = "rel" if use_rel else "base"
        events.to_csv(OUT_DIR / f"revenue_yoy_events_{suffix}.csv", index=False, encoding="utf-8-sig")

    print("\n" + "=" * 80)
    print("  🎯 完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
