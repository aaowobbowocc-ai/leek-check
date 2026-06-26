"""
多因子 Portfolio Walk-Forward Optimization。

問題：
  0050 + 00881 + 00947 + EWY + EWZ 5 個資產的最佳組合？
  比 100% 0050 好多少？

方法：
  1. Train (2015-2021): 用 efficient frontier 找最大 Sharpe portfolio
  2. Test (2022-2026): 套用 train 找的權重，量化 OOS 績效
  3. 對比：100% 0050 / 等權重 / Train-optimal / 用戶手動配置

驗證：避免 in-sample overfit，看 OOS 是否仍贏 0050
"""
from __future__ import annotations

import io
import sys
from datetime import date
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "cache" / "yfinance" / "global"
ETF_CACHE = ROOT / "data" / "cache" / "yfinance" / "etf_audit"

# 5 個資產（已 cached from 之前 audit）
ASSETS = [
    ("0050.TW",  "元大台 50",       "TW"),
    ("00881.TW", "國泰 5G+",         "TW"),
    ("00947.TW", "中信成長關鍵半導體", "TW"),
    ("EWY",      "韓國 MSCI",        "韓國"),
    ("EWZ",      "巴西 MSCI",        "巴西"),
]


def load(ticker: str) -> pd.DataFrame:
    # 先試 etf_audit cache，再 global cache
    for cache_dir in (ETF_CACHE, CACHE):
        cache_p = cache_dir / f"{ticker.replace('.', '_')}.parquet"
        if cache_p.exists():
            df = pd.read_parquet(cache_p)
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df
    # fallback yfinance
    import yfinance as yf
    df = yf.download(ticker, start="2015-01-01", end=date.today().isoformat(),
                     auto_adjust=True, progress=False)
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def portfolio_metrics(returns: pd.Series) -> dict:
    """計算 portfolio CAGR / Sharpe / MDD / Vol。"""
    n_years = len(returns) / 252
    if n_years <= 0:
        return {"cagr": 0, "sharpe": 0, "mdd": 0, "vol": 0}
    cum = (1 + returns).cumprod()
    cagr = (cum.iloc[-1] ** (1 / n_years) - 1) * 100
    sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
    peak = cum.cummax()
    mdd = ((cum - peak) / peak).min() * 100
    vol = returns.std() * np.sqrt(252) * 100
    return {"cagr": cagr, "sharpe": sharpe, "mdd": mdd, "vol": vol}


def sweep_portfolios(daily_rets: pd.DataFrame, step: float = 0.05) -> pd.DataFrame:
    """
    暴力 sweep 所有 5-asset 權重組合（step=0.05 → 約 53k 組合，可接受）。
    返回每組合的 CAGR / Sharpe / vol。
    """
    n = len(daily_rets.columns)
    weights_grid = []
    # Generate all combos summing to 1.0 with step
    n_steps = int(1.0 / step) + 1
    for combo in product(range(n_steps), repeat=n):
        if sum(combo) * step != 1.0:
            continue
        w = np.array(combo) * step
        weights_grid.append(w)

    print(f"  測試 {len(weights_grid)} 種權重組合...")
    results = []
    for w in weights_grid:
        port_rets = (daily_rets * w).sum(axis=1)
        m = portfolio_metrics(port_rets)
        result = {
            "weights": tuple(round(x, 2) for x in w),
            **m,
        }
        for i, col in enumerate(daily_rets.columns):
            result[col] = w[i]
        results.append(result)

    return pd.DataFrame(results)


def main() -> None:
    print("=" * 95)
    print("Multi-Factor Portfolio Walk-Forward Optimization")
    print("=" * 95)

    # 1. Load
    print("\n[1/4] 載入...")
    data = {}
    for tk, name, region in ASSETS:
        df = load(tk)
        if df.empty:
            print(f"  ❌ {tk}")
            continue
        data[tk] = {"name": name, "region": region, "df": df}
        print(f"  ✅ {tk} {name}: {df['date'].min()} ~ {df['date'].max()}")

    # 2. 對齊
    common_start = max(d["df"]["date"].min() for d in data.values())
    common_end = min(d["df"]["date"].max() for d in data.values())
    print(f"\n共同期間: {common_start} ~ {common_end}")

    # 建 daily returns matrix
    rets_df = pd.DataFrame()
    for tk, info in data.items():
        df = info["df"][(info["df"]["date"] >= common_start)
                       & (info["df"]["date"] <= common_end)].copy()
        df["ret"] = df["close"].pct_change()
        rets_df[tk] = df.set_index("date")["ret"]
    rets_df = rets_df.dropna()
    print(f"對齊後 daily returns: {rets_df.shape}")

    # Train / Test split
    split_idx = int(len(rets_df) * 0.6)   # 60% train
    train = rets_df.iloc[:split_idx]
    test = rets_df.iloc[split_idx:]
    print(f"  Train: {train.index[0]} ~ {train.index[-1]} ({len(train)} days)")
    print(f"  Test : {test.index[0]} ~ {test.index[-1]} ({len(test)} days)")

    # 3. Train: 找最大 Sharpe 組合
    print(f"\n[2/4] Train 期暴力搜尋...")
    train_results = sweep_portfolios(train, step=0.05)
    train_results = train_results.sort_values("sharpe", ascending=False)

    print(f"\nTrain top 5 by Sharpe:")
    print(f"  {'weights':<35} {'CAGR':>8} {'Sharpe':>7} {'MDD':>7} {'Vol':>6}")
    for _, r in train_results.head(5).iterrows():
        wstr = " / ".join([f"{tk[:5]}={r[tk]:.0%}" for tk in rets_df.columns if r[tk] > 0])
        print(f"  {wstr:<35s} {r['cagr']:>+7.2f}% {r['sharpe']:>7.2f} "
              f"{r['mdd']:>+6.1f}% {r['vol']:>5.1f}%")

    # 找最大 CAGR 組合
    train_cagr_top = train_results.sort_values("cagr", ascending=False).head(3)
    print(f"\nTrain top 3 by CAGR:")
    for _, r in train_cagr_top.iterrows():
        wstr = " / ".join([f"{tk[:5]}={r[tk]:.0%}" for tk in rets_df.columns if r[tk] > 0])
        print(f"  {wstr:<35s} {r['cagr']:>+7.2f}% {r['sharpe']:>7.2f}")

    # 4. Test: 套用 train 最佳權重 + baseline
    print(f"\n[3/4] OOS Test 期評估")
    print(f"  {'方案':<40} {'CAGR':>8} {'Sharpe':>7} {'MDD':>7} {'Vol':>6}")
    print("  " + "-" * 80)

    # Baseline: 100% 0050
    if "0050.TW" in test.columns:
        test_rets_50 = test["0050.TW"]
        m = portfolio_metrics(test_rets_50)
        print(f"  {'100% 0050':<40s} {m['cagr']:>+7.2f}% {m['sharpe']:>7.2f} "
              f"{m['mdd']:>+6.1f}% {m['vol']:>5.1f}%")

    # 等權重
    eq_w = np.ones(len(rets_df.columns)) / len(rets_df.columns)
    eq_rets = (test * eq_w).sum(axis=1)
    m = portfolio_metrics(eq_rets)
    print(f"  {'等權重 5 資產 (各 20%)':<40s} {m['cagr']:>+7.2f}% {m['sharpe']:>7.2f} "
          f"{m['mdd']:>+6.1f}% {m['vol']:>5.1f}%")

    # Train top Sharpe
    top_w = train_results.iloc[0]
    w_arr = np.array([top_w[tk] for tk in rets_df.columns])
    test_rets = (test * w_arr).sum(axis=1)
    m = portfolio_metrics(test_rets)
    wstr = " / ".join([f"{tk[:5]}={top_w[tk]:.0%}" for tk in rets_df.columns if top_w[tk] > 0])
    print(f"  {'Train Top Sharpe → ' + wstr:<40s} {m['cagr']:>+7.2f}% {m['sharpe']:>7.2f} "
          f"{m['mdd']:>+6.1f}% {m['vol']:>5.1f}%")

    # Train top CAGR
    top_w = train_cagr_top.iloc[0]
    w_arr = np.array([top_w[tk] for tk in rets_df.columns])
    test_rets = (test * w_arr).sum(axis=1)
    m = portfolio_metrics(test_rets)
    wstr = " / ".join([f"{tk[:5]}={top_w[tk]:.0%}" for tk in rets_df.columns if top_w[tk] > 0])
    print(f"  {'Train Top CAGR → ' + wstr:<40s} {m['cagr']:>+7.2f}% {m['sharpe']:>7.2f} "
          f"{m['mdd']:>+6.1f}% {m['vol']:>5.1f}%")

    # 我推薦的配置：50% 0050 + 25% 00881 + 10% 00947 + 10% EWY + 5% EWZ
    rec_weights = {
        "0050.TW": 0.50, "00881.TW": 0.25, "00947.TW": 0.10,
        "EWY": 0.10, "EWZ": 0.05,
    }
    w_arr = np.array([rec_weights.get(tk, 0) for tk in rets_df.columns])
    test_rets = (test * w_arr).sum(axis=1)
    m = portfolio_metrics(test_rets)
    print(f"  {'我推薦 50/25/10/10/5':<40s} {m['cagr']:>+7.2f}% {m['sharpe']:>7.2f} "
          f"{m['mdd']:>+6.1f}% {m['vol']:>5.1f}%")

    # 你 memory 上的舊配置 (TW 28% / US 18% / DXJ 8% / India 8% / VNM 4%) 對應 0050+00881
    old_weights = {"0050.TW": 0.50, "00881.TW": 0.50}    # 簡化 TW only
    w_arr = np.array([old_weights.get(tk, 0) for tk in rets_df.columns])
    test_rets = (test * w_arr).sum(axis=1)
    m = portfolio_metrics(test_rets)
    print(f"  {'舊配置 TW only (50/50)':<40s} {m['cagr']:>+7.2f}% {m['sharpe']:>7.2f} "
          f"{m['mdd']:>+6.1f}% {m['vol']:>5.1f}%")

    # 100% 00881
    w_88 = {"00881.TW": 1.0}
    w_arr = np.array([w_88.get(tk, 0) for tk in rets_df.columns])
    test_rets = (test * w_arr).sum(axis=1)
    m = portfolio_metrics(test_rets)
    print(f"  {'100% 00881':<40s} {m['cagr']:>+7.2f}% {m['sharpe']:>7.2f} "
          f"{m['mdd']:>+6.1f}% {m['vol']:>5.1f}%")

    # 4. 全期 (整段) 分析
    print(f"\n[4/4] 全期（含 train+test）對比")
    print(f"  {'方案':<40} {'CAGR':>8} {'Sharpe':>7} {'MDD':>7}")
    print("  " + "-" * 72)
    for label, weights in [
        ("100% 0050", {"0050.TW": 1.0}),
        ("100% 00881", {"00881.TW": 1.0}),
        ("0050+00881 50/50", {"0050.TW": 0.5, "00881.TW": 0.5}),
        ("我推薦 50/25/10/10/5", rec_weights),
        ("等權重 5 資產", {tk: 0.2 for tk in rets_df.columns}),
    ]:
        w_arr = np.array([weights.get(tk, 0) for tk in rets_df.columns])
        port = (rets_df * w_arr).sum(axis=1)
        m = portfolio_metrics(port)
        print(f"  {label:<40s} {m['cagr']:>+7.2f}% {m['sharpe']:>7.2f} {m['mdd']:>+6.1f}%")


if __name__ == "__main__":
    main()
