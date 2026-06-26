"""D direction analysis — 長期配置 vs Active 短線.

D1. BTC DCA timing strategies
  - Daily DCA 固定金額
  - Weekly DCA 固定金額
  - Monthly DCA 固定金額
  - Buy the dip (MA200 -10% 加倍, 否則固定)
  - 純等 dip (MA200 -20% 才買)
  比較 5 年 IRR + max DD + Sharpe

D2. TW core ETF rebalance frequency
  - Buy and Hold (50% 0050 + 50% 00646)
  - 半年 rebalance
  - 年度 rebalance
  - threshold rebalance (差 > 5% 才動)
  比較 CAGR + DD
"""
from __future__ import annotations
import sys, io, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
import pandas as pd
import numpy as np
from pathlib import Path
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def annualize_return(total_ret, years):
    return (1 + total_ret) ** (1/years) - 1


def max_drawdown(equity):
    eq = np.asarray(equity)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return dd.min() * 100


# ════════════════════════════════════════════════════════════
# D1. BTC DCA timing
# ════════════════════════════════════════════════════════════
def test_d1_btc_dca():
    print("\n" + "═"*70)
    print("D1. BTC DCA 時機策略 (5 年)")
    print("═"*70)

    btc = yf.Ticker("BTC-USD").history(period="5y", auto_adjust=False)
    if btc.empty:
        print("  no BTC data"); return
    btc.index = pd.to_datetime(btc.index)
    btc = btc[~btc.index.duplicated()]
    print(f"  BTC bars: {len(btc)}, {btc.index[0].date()} → {btc.index[-1].date()}")

    # Daily DCA: USD 10/day
    btc["ma200"] = btc["Close"].rolling(200).mean()
    btc["dist_ma200"] = (btc["Close"]/btc["ma200"] - 1) * 100

    strategies = {}

    # Daily DCA
    daily_amt = 10
    btc_qty = (daily_amt / btc["Close"]).cumsum()
    total_spent = pd.Series(daily_amt * (np.arange(len(btc)) + 1), index=btc.index)
    portfolio = btc_qty * btc["Close"]
    strategies["Daily $10"] = (portfolio, total_spent)

    # Weekly DCA: USD 70/week
    weekly_mask = btc.index.dayofweek == 0  # Monday
    week_amt = 70
    btc_qty_w = pd.Series(0.0, index=btc.index)
    spent_w = pd.Series(0.0, index=btc.index)
    cum_qty, cum_spent = 0, 0
    for d in btc.index:
        if weekly_mask[btc.index.get_loc(d)]:
            cum_qty += week_amt / btc.loc[d, "Close"]
            cum_spent += week_amt
        btc_qty_w[d] = cum_qty
        spent_w[d] = cum_spent
    strategies["Weekly $70"] = (btc_qty_w * btc["Close"], spent_w)

    # Monthly DCA: USD 300/month
    btc["is_first_of_month"] = btc.index.to_series().dt.is_month_start | \
        (btc.index.to_series().dt.day == 1)
    month_amt = 300
    cum_qty, cum_spent = 0, 0
    btc_qty_m = pd.Series(0.0, index=btc.index)
    spent_m = pd.Series(0.0, index=btc.index)
    last_month = -1
    for d in btc.index:
        if d.month != last_month:
            cum_qty += month_amt / btc.loc[d, "Close"]
            cum_spent += month_amt
            last_month = d.month
        btc_qty_m[d] = cum_qty
        spent_m[d] = cum_spent
    strategies["Monthly $300"] = (btc_qty_m * btc["Close"], spent_m)

    # Buy the dip: USD 10/day baseline + extra USD 50 if dist_ma200 < -10%
    cum_qty, cum_spent = 0, 0
    qty_d, spent_d = [], []
    for d in btc.index:
        price = btc.loc[d, "Close"]
        amt = 10
        if not pd.isna(btc.loc[d, "dist_ma200"]) and btc.loc[d, "dist_ma200"] < -10:
            amt = 60
        cum_qty += amt / price
        cum_spent += amt
        qty_d.append(cum_qty)
        spent_d.append(cum_spent)
    qty_d = pd.Series(qty_d, index=btc.index)
    spent_d = pd.Series(spent_d, index=btc.index)
    strategies["Daily $10 + Dip $50"] = (qty_d * btc["Close"], spent_d)

    # Pure dip buy: only buy when dist_ma200 < -20%
    cum_qty, cum_spent = 0, 0
    qty_p, spent_p = [], []
    for d in btc.index:
        price = btc.loc[d, "Close"]
        amt = 0
        if not pd.isna(btc.loc[d, "dist_ma200"]) and btc.loc[d, "dist_ma200"] < -20:
            amt = 100
        cum_qty += amt / price
        cum_spent += amt
        qty_p.append(cum_qty)
        spent_p.append(cum_spent)
    qty_p = pd.Series(qty_p, index=btc.index)
    spent_p = pd.Series(spent_p, index=btc.index)
    strategies["Pure Dip > -20%"] = (qty_p * btc["Close"], spent_p)

    print(f"\n  {'Strategy':<25} {'Spent':<10} {'Value':<10} {'ROI':<8} {'MaxDD':<8}")
    print("-"*70)
    for name, (portfolio, spent) in strategies.items():
        if spent.iloc[-1] == 0:
            print(f"  {name:<25} $0 spent (no trigger)")
            continue
        final_value = portfolio.iloc[-1]
        final_spent = spent.iloc[-1]
        roi = (final_value / final_spent - 1) * 100
        # DD: compute on portfolio - spent (real PnL)
        pnl = portfolio - spent
        peak = pnl.cummax()
        dd = (pnl - peak)
        max_dd_usd = dd.min()
        max_dd_pct = max_dd_usd / spent.replace(0, np.nan).max() * 100
        print(f"  {name:<25} ${final_spent:>8.0f}  ${final_value:>8.0f}  {roi:>+6.1f}%  {max_dd_pct:>+5.1f}%")


# ════════════════════════════════════════════════════════════
# D2. TW core ETF allocation
# ════════════════════════════════════════════════════════════
def test_d2_tw_etf_allocation():
    print("\n" + "═"*70)
    print("D2. TW core ETF 配置 + rebalance frequency")
    print("═"*70)

    tickers = {
        "0050": "TW core (大盤)",
        "00646": "美股 S&P500 (TW)",
        "00635U": "黃金 (TW)",
        "00631L": "TW 2x leverage",
    }
    dfs = {}
    for tk in tickers:
        p = CACHE / f"{tk}.parquet"
        if not p.exists(): continue
        df = pd.read_parquet(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        dfs[tk] = df["close"]

    # Align dates
    merged = pd.DataFrame(dfs).dropna()
    if merged.empty:
        print("  no aligned data"); return
    print(f"  Aligned bars: {len(merged)}, {merged.index[0].date()} → {merged.index[-1].date()}")

    # Strategy: 50% 0050 + 50% 00646
    targets = {"0050": 0.5, "00646": 0.5}
    initial = 1000000  # NT$1M (or whatever currency)

    def run_strategy(rebalance_mode="buy_hold"):
        prices = merged[list(targets.keys())]
        qty = pd.Series(0.0, index=targets.keys())
        for tk in targets:
            qty[tk] = (initial * targets[tk]) / prices[tk].iloc[0]
        equity = pd.Series(0.0, index=prices.index)
        last_rb = prices.index[0]
        for d in prices.index:
            cur_val = sum(qty[tk] * prices.loc[d, tk] for tk in targets)
            equity[d] = cur_val
            # Rebalance check
            need_rb = False
            if rebalance_mode == "half_yearly":
                need_rb = (d - last_rb).days >= 182
            elif rebalance_mode == "yearly":
                need_rb = (d - last_rb).days >= 365
            elif rebalance_mode == "threshold":
                cur_alloc = {tk: qty[tk] * prices.loc[d, tk] / cur_val for tk in targets}
                if max(abs(cur_alloc[tk] - targets[tk]) for tk in targets) > 0.05:
                    need_rb = True
            if need_rb:
                for tk in targets:
                    qty[tk] = (cur_val * targets[tk]) / prices.loc[d, tk]
                last_rb = d
        return equity

    print(f"\n  {'Mode':<20} {'Final $':<12} {'CAGR':<8} {'MaxDD':<8}")
    print("-"*55)
    years = (merged.index[-1] - merged.index[0]).days / 365.25
    for mode in ["buy_hold", "half_yearly", "yearly", "threshold"]:
        equity = run_strategy(mode)
        cagr = annualize_return(equity.iloc[-1]/initial - 1, years) * 100
        dd = max_drawdown(equity.values)
        print(f"  {mode:<20} {equity.iloc[-1]:>10.0f}  {cagr:>+5.1f}%  {dd:>+5.1f}%")

    # Add other configs:
    print(f"\n  Other allocations (yearly rebalance):")
    print(f"  {'Allocation':<35} {'Final $':<12} {'CAGR':<8} {'MaxDD':<8}")
    print("-"*70)
    test_configs = [
        ({"0050": 1.0}, "100% 0050"),
        ({"00646": 1.0}, "100% 00646 (S&P)"),
        ({"0050": 0.7, "00646": 0.3}, "70% TW + 30% 美"),
        ({"0050": 0.5, "00646": 0.3, "00635U": 0.2}, "50/30/20 TW+US+黃金"),
        ({"0050": 0.6, "00631L": 0.4}, "60% 0050 + 40% 00631L (槓桿)"),
    ]
    for cfg, name in test_configs:
        if not all(tk in merged.columns for tk in cfg):
            continue
        prices = merged[list(cfg.keys())]
        qty = {tk: (initial * cfg[tk]) / prices[tk].iloc[0] for tk in cfg}
        equity = pd.Series(0.0, index=prices.index)
        last_rb = prices.index[0]
        for d in prices.index:
            cur_val = sum(qty[tk] * prices.loc[d, tk] for tk in cfg)
            equity[d] = cur_val
            if (d - last_rb).days >= 365:
                for tk in cfg:
                    qty[tk] = (cur_val * cfg[tk]) / prices.loc[d, tk]
                last_rb = d
        cagr = annualize_return(equity.iloc[-1]/initial - 1, years) * 100
        dd = max_drawdown(equity.values)
        print(f"  {name:<35} {equity.iloc[-1]:>10.0f}  {cagr:>+5.1f}%  {dd:>+5.1f}%")


if __name__ == "__main__":
    from datetime import datetime
    print(f"D direction — {datetime.now().strftime('%H:%M')}")
    test_d1_btc_dca()
    test_d2_tw_etf_allocation()
