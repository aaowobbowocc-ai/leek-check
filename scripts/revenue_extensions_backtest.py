"""
月營收 YoY 延伸研究 — 3 個派生訊號

baseline: yoy 30-200% 後 60d alpha +3.02%, t=19.84

延伸假設：
  Signal 4: YoY 加速度
    Trigger: 連 3 個月 YoY > +30% AND 遞增
    Hypothesis: 趨勢加速 → 獲利持續，alpha 應 > +3%

  Signal 5: 相對市場 YoY
    Trigger: ticker_yoy - market_median_yoy > +30
    Hypothesis: 排除產業性整體好景氣，純個股 outperform → alpha 應更穩

  Signal 6: 月營收 + 量爆組合（簡化版多因子）
    Trigger: yoy > +30% AND 公告後 5 日內出現 vol z > 2.5
    Hypothesis: 月營收公告 + 量能爆發 = 法人吃貨 confirmation

評估：vs same-ticker random baseline，看 alpha + t-stat

注意：先過濾異常 (yoy 30-200%, prior > 1000 萬)
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

HOLD_PERIODS = [20, 60]


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


def load_revenue_with_yoy(tk: str) -> pd.DataFrame:
    """載入月營收 + 計算 YoY（過濾異常）"""
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
    # 標記合法 YoY
    rev["yoy_valid"] = (
        rev["yoy"].notna() &
        (rev["yoy"].abs() < 500) &  # 過濾極端
        (rev["prior_revenue"] > 1e7)
    )
    return rev


def compute_forward_return(prices: pd.DataFrame, signal_dates: list, hold: int) -> list[float]:
    if prices.empty: return []
    prices = prices.set_index("date")
    closes = prices["close"]
    rets = []
    for sig_d in signal_dates:
        future = closes[closes.index > pd.Timestamp(sig_d)]
        if len(future) <= hold: continue
        entry = future.iloc[0]
        exit_ = future.iloc[hold]
        if entry > 0:
            rets.append((exit_ / entry - 1) * 100)
    return rets


def baseline_random(prices: pd.DataFrame, hold: int, n_samples: int = 50) -> list[float]:
    if prices.empty or len(prices) < hold + 60: return []
    closes = prices["close"].values
    n = len(closes)
    idx = np.random.RandomState(42).choice(
        range(60, n - hold), size=min(n_samples, n - hold - 60), replace=False
    )
    return [(closes[i + hold] / closes[i] - 1) * 100 for i in idx if closes[i] > 0]


# ─── Signal 4: YoY 加速度 ───
def signal_4_yoy_acceleration(universe: list[str]) -> dict:
    """連 3 個月 YoY > +30% AND 遞增"""
    print(f"\n{'=' * 80}")
    print(f"  ▶ Signal 4: YoY 加速度（連 3 個月 YoY > +30% AND 遞增）")
    print(f"{'=' * 80}")

    all_rets = {h: [] for h in HOLD_PERIODS}
    all_baseline = {h: [] for h in HOLD_PERIODS}
    n_triggers = 0
    n_tickers = 0

    for i, tk in enumerate(universe):
        rev = load_revenue_with_yoy(tk)
        if rev.empty: continue

        rev = rev[rev["yoy_valid"]].copy()
        if len(rev) < 4: continue

        # Trigger: 當前 month 與前 2 month 都 yoy > 30%, 且遞增
        triggers = []
        for j in range(2, len(rev)):
            yoy_now = rev.iloc[j]["yoy"]
            yoy_p1 = rev.iloc[j-1]["yoy"]
            yoy_p2 = rev.iloc[j-2]["yoy"]
            if yoy_now > 30 and yoy_p1 > 30 and yoy_p2 > 30 and \
               yoy_now > yoy_p1 > yoy_p2 and yoy_now < 200:
                triggers.append(rev.iloc[j]["date"])

        if not triggers: continue

        prices = load_price(tk)
        if prices.empty: continue

        ticker_has = False
        for h in HOLD_PERIODS:
            rets = compute_forward_return(prices, triggers, h)
            if rets:
                all_rets[h].extend(rets)
                ticker_has = True
            base = baseline_random(prices, h)
            all_baseline[h].extend(base)
        if ticker_has:
            n_tickers += 1
            n_triggers += len(triggers)

        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(universe)}] tickers={n_tickers}, triggers={n_triggers}")

    print(f"\n  Total: {n_triggers} triggers across {n_tickers} tickers")
    return _summarize(all_rets, all_baseline, "YoY Acceleration")


# ─── Signal 5: 相對市場 YoY ───
def signal_5_relative_yoy(universe: list[str], excess: float = 30.0) -> dict:
    """ticker yoy - market median yoy > +excess"""
    print(f"\n{'=' * 80}")
    print(f"  ▶ Signal 5: 相對市場 YoY（excess > +{excess}%）")
    print(f"{'=' * 80}")

    # Step 1: 全市場 monthly median yoy
    print(f"  載入全市場 YoY...")
    all_yoy = []
    for tk in universe:
        rev = load_revenue_with_yoy(tk)
        if rev.empty: continue
        rev_valid = rev[rev["yoy_valid"]][["date", "yoy"]].copy()
        rev_valid["ticker"] = tk
        all_yoy.append(rev_valid)
    if not all_yoy:
        return {"name": "Relative YoY", "by_hold": {}}
    market_df = pd.concat(all_yoy, ignore_index=True)
    market_df["ym"] = market_df["date"].dt.to_period("M")
    market_median = market_df.groupby("ym")["yoy"].median().to_dict()
    print(f"  市場月份 median 計算完成 ({len(market_median)} months)")

    # Step 2: 對每個 ticker 找相對 trigger
    all_rets = {h: [] for h in HOLD_PERIODS}
    all_baseline = {h: [] for h in HOLD_PERIODS}
    n_triggers = 0
    n_tickers = 0

    for i, tk in enumerate(universe):
        rev = load_revenue_with_yoy(tk)
        if rev.empty: continue
        rev = rev[rev["yoy_valid"]].copy()
        rev["ym"] = rev["date"].dt.to_period("M")
        rev["market_median"] = rev["ym"].map(market_median)
        rev["excess_yoy"] = rev["yoy"] - rev["market_median"]
        triggers_df = rev[(rev["excess_yoy"] > excess) & (rev["yoy"] < 200)]
        if triggers_df.empty: continue

        prices = load_price(tk)
        if prices.empty: continue

        sig_dates = triggers_df["date"].tolist()
        ticker_has = False
        for h in HOLD_PERIODS:
            rets = compute_forward_return(prices, sig_dates, h)
            if rets:
                all_rets[h].extend(rets)
                ticker_has = True
            base = baseline_random(prices, h)
            all_baseline[h].extend(base)
        if ticker_has:
            n_tickers += 1
            n_triggers += len(sig_dates)

        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(universe)}] tickers={n_tickers}, triggers={n_triggers}")

    print(f"\n  Total: {n_triggers} triggers across {n_tickers} tickers")
    return _summarize(all_rets, all_baseline, "Relative YoY")


# ─── Signal 6: YoY + 量爆 組合 ───
def signal_6_yoy_plus_volume(universe: list[str]) -> dict:
    """yoy > +30% AND 公告後 5 日內出現 vol z > 2.5"""
    print(f"\n{'=' * 80}")
    print(f"  ▶ Signal 6: 月營收 + 量爆組合（yoy>+30% AND 公告後 5d 內 vol z>2.5）")
    print(f"{'=' * 80}")

    all_rets = {h: [] for h in HOLD_PERIODS}
    all_baseline = {h: [] for h in HOLD_PERIODS}
    n_triggers = 0
    n_tickers = 0

    for i, tk in enumerate(universe):
        rev = load_revenue_with_yoy(tk)
        if rev.empty: continue
        rev = rev[rev["yoy_valid"] & (rev["yoy"] > 30) & (rev["yoy"] < 200)]
        if rev.empty: continue

        prices = load_price(tk)
        if prices.empty: continue

        # 計算 vol z
        prices = prices.copy()
        prices["vol_ma"] = prices["volume"].rolling(60).mean()
        prices["vol_std"] = prices["volume"].rolling(60).std()
        prices["vol_z"] = (prices["volume"] - prices["vol_ma"]) / prices["vol_std"]

        # 對每個 yoy trigger，看公告後 5 日內是否有 vol z > 2.5
        ticker_triggers = []
        for _, row in rev.iterrows():
            ann_date = row["date"]
            window = prices[(prices["date"] >= ann_date) &
                           (prices["date"] <= ann_date + pd.Timedelta(days=10))]
            high_vol = window[window["vol_z"] > 2.5]
            if not high_vol.empty:
                # Trigger 在 vol 爆出當日
                ticker_triggers.append(high_vol.iloc[0]["date"])

        if not ticker_triggers: continue

        ticker_has = False
        for h in HOLD_PERIODS:
            rets = compute_forward_return(prices, ticker_triggers, h)
            if rets:
                all_rets[h].extend(rets)
                ticker_has = True
            base = baseline_random(prices, h)
            all_baseline[h].extend(base)
        if ticker_has:
            n_tickers += 1
            n_triggers += len(ticker_triggers)

        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(universe)}] tickers={n_tickers}, triggers={n_triggers}")

    print(f"\n  Total: {n_triggers} triggers across {n_tickers} tickers")
    return _summarize(all_rets, all_baseline, "YoY + Volume")


def _summarize(all_rets: dict, all_baseline: dict, name: str) -> dict:
    print(f"\n  📊 {name} Results:")
    print(f"  {'hold':<6} {'n':<8} {'mean':<10} {'baseline':<10} {'alpha':<10} {'win%':<8} {'t':<8}")
    print(f"  {'-'*60}")
    out = {"name": name, "by_hold": {}}
    for h in HOLD_PERIODS:
        rets = all_rets[h]
        base = all_baseline[h]
        if len(rets) < 30:
            print(f"  {h:<6} n={len(rets)} (太少)")
            continue
        n = len(rets)
        mean_ret = np.mean(rets)
        base_mean = np.mean(base) if base else 0
        base_std = np.std(base) if base else 0
        alpha = mean_ret - base_mean
        win = sum(1 for r in rets if r > 0) / n * 100
        t_stat = alpha / (base_std / np.sqrt(n)) if base_std > 0 else None
        t_str = f"{t_stat:+.2f}" if t_stat is not None else "n/a"
        print(f"  {h:<6} {n:<8} {mean_ret:+.2f}%    {base_mean:+.2f}%    "
              f"{alpha:+.2f}%    {win:.1f}%    {t_str}")
        out["by_hold"][h] = {
            "n": n, "mean": mean_ret, "baseline": base_mean,
            "alpha": alpha, "win_pct": win, "t_stat": t_stat,
        }
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tickers", type=int, default=0, help="0 = all")
    ap.add_argument("--only", choices=["4", "5", "6"], help="只跑某個 signal")
    args = ap.parse_args()

    print("=" * 80)
    print("  月營收 YoY 延伸研究 — 3 個派生訊號")
    print(f"  Baseline: yoy 30-200% 後 60d alpha +3.02%, t=19.84")
    print("=" * 80)

    universe = load_universe()
    if args.max_tickers > 0:
        universe = universe[:args.max_tickers]
    print(f"\n  Universe: {len(universe)} tickers")

    results = {}
    if args.only is None or args.only == "4":
        results["accel"] = signal_4_yoy_acceleration(universe)
    if args.only is None or args.only == "5":
        results["relative"] = signal_5_relative_yoy(universe)
    if args.only is None or args.only == "6":
        results["combined"] = signal_6_yoy_plus_volume(universe)

    # ── Final summary ──
    print("\n" + "=" * 80)
    print("  🎯 Final Summary (vs Baseline +3.02%/60d)")
    print("=" * 80)
    for name, r in results.items():
        print(f"\n  {r['name']}:")
        for h, stats in r["by_hold"].items():
            t_val = stats.get("t_stat")
            t_str = f"{t_val:+.2f}" if t_val is not None else "n/a"
            verdict = "✅" if abs(stats.get("alpha", 0)) > 3.0 and abs(t_val or 0) > 2.0 else "⚠️"
            print(f"    hold={h}d: n={stats['n']}, alpha={stats['alpha']:+.2f}%, "
                  f"win={stats['win_pct']:.1f}%, t={t_str} {verdict}")


if __name__ == "__main__":
    main()
