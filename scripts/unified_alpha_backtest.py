"""
Unified Alpha Backtest — 3 個未測訊號平行驗證

訊號：
  1. 月營收 YoY surprise → 個股 60d forward return
     - YoY > +30% (filter) → vs same-ticker random baseline
  2. 融資餘額過熱 → 個股 30d forward return
     - 融資餘額 60d z > +2.0 → 預期負 alpha
  3. 外資 daily 加碼速度 → 個股 20d forward return
     - 外資持股 5d 變化 z > +2.0 → 短期 momentum

評估：
  - n: trigger 數
  - mean forward return
  - vs baseline (same ticker random window)
  - alpha = signal_mean - baseline_mean
  - t-stat (rough)

Universe: institutional cache 已驗證有資料的 1988 檔
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
OUT_DIR = ROOT / "scripts" / "output"
OUT_DIR.mkdir(exist_ok=True, parents=True)

CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


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


def compute_forward_return(prices: pd.DataFrame, signal_dates: list, hold: int) -> list[float]:
    """對每個 signal_date 找下個交易日進場，hold N 日後出場，回傳 % 報酬"""
    if prices.empty: return []
    prices = prices.set_index("date")
    closes = prices["close"]
    rets = []
    for sig_d in signal_dates:
        # next trading day
        future = closes[closes.index > pd.Timestamp(sig_d)]
        if len(future) <= hold: continue
        entry = future.iloc[0]
        exit_ = future.iloc[hold]
        if entry > 0:
            rets.append((exit_ / entry - 1) * 100)
    return rets


def baseline_random(prices: pd.DataFrame, hold: int, n_samples: int = 100) -> list[float]:
    """Same ticker random baseline"""
    if prices.empty or len(prices) < hold + 60: return []
    closes = prices["close"].values
    n = len(closes)
    idx = np.random.RandomState(42).choice(range(60, n - hold), size=min(n_samples, n - hold - 60), replace=False)
    rets = []
    for i in idx:
        if closes[i] > 0:
            rets.append((closes[i + hold] / closes[i] - 1) * 100)
    return rets


# ─── Signal 1: 月營收 YoY surprise ───
def signal_1_revenue_yoy(universe: list[str], yoy_threshold: float = 30.0,
                          hold_periods: list = [20, 60]) -> dict:
    print(f"\n{'=' * 80}")
    print(f"  ▶ Signal 1: 月營收 YoY surprise (YoY > +{yoy_threshold}%)")
    print(f"{'=' * 80}")

    all_rets = {h: [] for h in hold_periods}
    all_baseline = {h: [] for h in hold_periods}
    n_triggers = 0
    n_tickers_with_trigger = 0

    for i, tk in enumerate(universe):
        rev_path = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
        if not rev_path.exists(): continue
        try:
            rev = pd.read_parquet(rev_path)
        except Exception:
            continue
        if rev.empty or len(rev) < 24: continue

        rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
        rev["prior_revenue"] = rev["revenue"].shift(12)
        rev["yoy"] = (rev["revenue"] / rev["prior_revenue"] - 1) * 100
        # FIX 2026-05-04 #8 + Claude#2-1: 用 estimated announce date (date + 14 days)
        # 條件邏輯修正: create_dt 必須 > date AND <= date+25 才視為 valid 公告日
        rev["date"] = pd.to_datetime(rev["date"])
        rev["announce_date"] = rev["date"] + pd.Timedelta(days=14)  # default fallback
        if "create_time" in rev.columns:
            create_dt = pd.to_datetime(rev["create_time"], errors="coerce")
            valid_create = (
                create_dt.notna() &
                (create_dt > rev["date"]) &  # 公告日必在 period 之後
                (create_dt <= rev["date"] + pd.Timedelta(days=25))  # 最遲 25 天 (法定 21 + buffer)
            )
            rev.loc[valid_create, "announce_date"] = create_dt[valid_create]
        # G-3 FIX: 公告通常在盤後 (13:30+)，所以「次一交易日 open」進場 = +1 trading day
        # 簡化用 +1 calendar day（後續 forward return 從 next trading day 起算）
        rev["entry_date"] = rev["announce_date"] + pd.Timedelta(days=1)
        # 過濾異常
        triggers = rev[
            (rev["yoy"] > yoy_threshold) &
            (rev["yoy"] < 200) &
            (rev["prior_revenue"] > 1e7) &
            rev["yoy"].notna()
        ]
        if triggers.empty: continue

        prices = load_price(tk)
        if prices.empty: continue

        # FIX #8 + G-3: 用 entry_date (announce + 1) 模擬次日 open 進場
        sig_dates = triggers["entry_date"].tolist()
        ticker_has = False
        for h in hold_periods:
            rets = compute_forward_return(prices, sig_dates, h)
            if rets:
                all_rets[h].extend(rets)
                ticker_has = True
            base = baseline_random(prices, h, n_samples=50)
            all_baseline[h].extend(base)
        if ticker_has:
            n_tickers_with_trigger += 1
            n_triggers += len(sig_dates)

        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(universe)}] tickers={n_tickers_with_trigger}, triggers={n_triggers}")

    print(f"\n  Total: {n_triggers} triggers across {n_tickers_with_trigger} tickers")
    return _summarize(all_rets, all_baseline, hold_periods, "Revenue YoY")


# ─── Signal 2: 融資餘額過熱 ───
def signal_2_margin_overheat(universe: list[str], z_threshold: float = 2.0,
                              hold_periods: list = [20, 60]) -> dict:
    print(f"\n{'=' * 80}")
    print(f"  ▶ Signal 2: 融資餘額過熱 (z > +{z_threshold})")
    print(f"{'=' * 80}")

    all_rets = {h: [] for h in hold_periods}
    all_baseline = {h: [] for h in hold_periods}
    n_triggers = 0
    n_tickers_with_trigger = 0

    for i, tk in enumerate(universe):
        m_path = CACHE / f"TaiwanStockMarginPurchaseShortSale_{tk}.parquet"
        if not m_path.exists(): continue
        try:
            m = pd.read_parquet(m_path)
        except Exception:
            continue
        if m.empty or len(m) < 120: continue

        m = m.sort_values("date").reset_index(drop=True)
        m["bal"] = m["MarginPurchaseTodayBalance"]
        m["ma"] = m["bal"].rolling(60).mean()
        m["std"] = m["bal"].rolling(60).std()
        m["z"] = (m["bal"] - m["ma"]) / m["std"]
        triggers = m[m["z"] > z_threshold].copy()
        if triggers.empty: continue

        # 避免連續觸發：每 30 日只取第一個 trigger
        triggers["date"] = pd.to_datetime(triggers["date"])
        triggers = triggers.sort_values("date")
        last_dt = pd.Timestamp("2000-01-01")
        keep = []
        for _, row in triggers.iterrows():
            if (row["date"] - last_dt).days >= 30:
                keep.append(row)
                last_dt = row["date"]
        if not keep: continue
        triggers = pd.DataFrame(keep)

        prices = load_price(tk)
        if prices.empty: continue

        sig_dates = triggers["date"].tolist()
        ticker_has = False
        for h in hold_periods:
            rets = compute_forward_return(prices, sig_dates, h)
            if rets:
                all_rets[h].extend(rets)
                ticker_has = True
            base = baseline_random(prices, h, n_samples=50)
            all_baseline[h].extend(base)
        if ticker_has:
            n_tickers_with_trigger += 1
            n_triggers += len(sig_dates)

        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(universe)}] tickers={n_tickers_with_trigger}, triggers={n_triggers}")

    print(f"\n  Total: {n_triggers} triggers across {n_tickers_with_trigger} tickers")
    return _summarize(all_rets, all_baseline, hold_periods, "Margin Overheat")


# ─── Signal 3: 外資 daily 加碼速度 ───
def signal_3_foreign_velocity(universe: list[str], days_chg: int = 5,
                                z_threshold: float = 2.0,
                                hold_periods: list = [10, 20, 60]) -> dict:
    print(f"\n{'=' * 80}")
    print(f"  ▶ Signal 3: 外資 daily 加碼速度 ({days_chg}d 變化 z > +{z_threshold})")
    print(f"{'=' * 80}")

    all_rets = {h: [] for h in hold_periods}
    all_baseline = {h: [] for h in hold_periods}
    n_triggers = 0
    n_tickers_with_trigger = 0

    for i, tk in enumerate(universe):
        s_path = CACHE / f"TaiwanStockShareholding_{tk}.parquet"
        if not s_path.exists(): continue
        try:
            s = pd.read_parquet(s_path)
        except Exception:
            continue
        if s.empty or len(s) < 120: continue

        s = s.sort_values("date").reset_index(drop=True)
        s["foreign_pct"] = s["ForeignInvestmentSharesRatio"]
        s["chg"] = s["foreign_pct"] - s["foreign_pct"].shift(days_chg)
        s["ma"] = s["chg"].rolling(60).mean()
        s["std"] = s["chg"].rolling(60).std()
        s["z"] = (s["chg"] - s["ma"]) / s["std"]
        triggers = s[s["z"] > z_threshold].copy()
        if triggers.empty: continue

        # 避免連續觸發
        triggers["date"] = pd.to_datetime(triggers["date"])
        triggers = triggers.sort_values("date")
        last_dt = pd.Timestamp("2000-01-01")
        keep = []
        for _, row in triggers.iterrows():
            if (row["date"] - last_dt).days >= 20:
                keep.append(row)
                last_dt = row["date"]
        if not keep: continue
        triggers = pd.DataFrame(keep)

        prices = load_price(tk)
        if prices.empty: continue

        sig_dates = triggers["date"].tolist()
        ticker_has = False
        for h in hold_periods:
            rets = compute_forward_return(prices, sig_dates, h)
            if rets:
                all_rets[h].extend(rets)
                ticker_has = True
            base = baseline_random(prices, h, n_samples=50)
            all_baseline[h].extend(base)
        if ticker_has:
            n_tickers_with_trigger += 1
            n_triggers += len(sig_dates)

        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(universe)}] tickers={n_tickers_with_trigger}, triggers={n_triggers}")

    print(f"\n  Total: {n_triggers} triggers across {n_tickers_with_trigger} tickers")
    return _summarize(all_rets, all_baseline, hold_periods, "Foreign Velocity")


def _summarize(all_rets: dict, all_baseline: dict, hold_periods: list, name: str) -> dict:
    """彙整結果"""
    print(f"\n  📊 {name} Results:")
    print(f"  {'hold':<6} {'n':<8} {'mean':<10} {'baseline':<10} {'alpha':<10} {'win%':<8} {'t':<8}")
    print(f"  {'-'*60}")
    out = {"name": name, "by_hold": {}}
    from scipy import stats as scipy_stats
    for h in hold_periods:
        rets = all_rets[h]
        base = all_baseline[h]
        if len(rets) < 30:
            print(f"  {h:<6} n={len(rets)} (太少)")
            continue
        n = len(rets)
        mean_ret = np.mean(rets)
        base_mean = np.mean(base) if base else 0
        alpha = mean_ret - base_mean
        win = sum(1 for r in rets if r > 0) / n * 100
        # FIX C2-2: Welch's t-test (proper) instead of wrong formula alpha/(base_std/sqrt(n))
        if len(base) >= 30:
            t_stat, p_val = scipy_stats.ttest_ind(rets, base, equal_var=False, alternative="greater")
        else:
            t_stat, p_val = None, None
        t_str = f"{t_stat:+.2f}" if t_stat is not None else "n/a"
        p_str = f"{p_val:.4f}" if p_val is not None else "n/a"
        print(f"  {h:<6} {n:<8} {mean_ret:+.2f}%    {base_mean:+.2f}%    "
              f"{alpha:+.2f}%    {win:.1f}%    t={t_str} p={p_str}")
        out["by_hold"][h] = {
            "n": n, "mean": mean_ret, "baseline": base_mean,
            "alpha": alpha, "win_pct": win, "t_stat": t_stat, "p_value": p_val,
        }
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tickers", type=int, default=0, help="0 = all")
    ap.add_argument("--only", choices=["1", "2", "3"], help="只跑某個 signal")
    args = ap.parse_args()

    print("=" * 80)
    print("  Unified Alpha Backtest — 3 個未測訊號")
    print("=" * 80)
    universe = load_universe()
    if args.max_tickers > 0:
        universe = universe[:args.max_tickers]
    print(f"\n  Universe: {len(universe)} tickers")

    results = {}
    if args.only is None or args.only == "1":
        results["revenue"] = signal_1_revenue_yoy(universe)
    if args.only is None or args.only == "2":
        results["margin"] = signal_2_margin_overheat(universe)
    if args.only is None or args.only == "3":
        results["foreign"] = signal_3_foreign_velocity(universe)

    # ── Final summary ──
    print("\n" + "=" * 80)
    print("  🎯 Final Summary")
    print("=" * 80)
    for name, r in results.items():
        print(f"\n  {r['name']}:")
        for h, stats in r["by_hold"].items():
            t_val = stats.get("t_stat")
            t_str = f"{t_val:.2f}" if t_val is not None else "n/a"
            verdict = "✅" if abs(stats.get("alpha", 0)) > 1.0 and abs(t_val or 0) > 2.0 else "⚠️"
            print(f"    hold={h}d: n={stats['n']}, alpha={stats['alpha']:+.2f}%, "
                  f"win={stats['win_pct']:.1f}%, t={t_str} {verdict}")


if __name__ == "__main__":
    main()
