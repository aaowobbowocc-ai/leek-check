"""A2 法人 divergence Round 2 — 扣 0050 baseline 確認真 alpha.

Round 1: 4 類別 (consensus L/S + divergence ±) 全部 20d 正報酬
→ 暗示 baseline 為正 (5y bull),需要扣掉
→ Round 2: 每筆 event 扣同期 0050 return 算 alpha vs 0050
"""
from __future__ import annotations
import sys, io, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
FC = ROOT / "data" / "cache" / "finmind" / "finmind"


def t_stat(arr):
    arr = np.asarray(arr, dtype=float)
    if len(arr) < 2 or arr.std(ddof=1) == 0: return 0.0
    return arr.mean() / (arr.std(ddof=1) / math.sqrt(len(arr)))


def report(name, arr):
    arr = np.array(arr)
    n = len(arr)
    if n == 0:
        print(f"  {name}: n=0 ❌")
        return
    mu = arr.mean()
    t = t_stat(arr)
    wr = (arr > 0).mean() * 100
    icon = "✅" if (n>=30 and t>2 and mu>0.5) else "⚠️" if (mu>0 and n>=20) else "❌"
    print(f"  {name}: n={n} mean={mu:+.3f}% t={t:+.2f} WR={wr:.0f}% {icon}")


print("A2 Round 2 — alpha vs 0050 baseline")
print("="*70)

# 0050 baseline
etf = pd.read_parquet(CACHE / "0050.parquet")
etf["date"] = pd.to_datetime(etf["date"])
etf = etf.sort_values("date").reset_index(drop=True)
etf_idx = etf.set_index("date")

inst_files = list(FC.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))[:500]
print(f"Loading {len(inst_files)} inst caches...")

events = {"consensus_L": [], "consensus_S": [], "div_F_buy_IT_sell": [], "div_F_sell_IT_buy": []}
alpha_events = {k: [] for k in events.keys()}

for p in inst_files:
    tk = p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
    try:
        df = pd.read_parquet(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        stock_p = CACHE / f"{tk}.parquet"
        if not stock_p.exists(): continue
        stock = pd.read_parquet(stock_p)
        stock["date"] = pd.to_datetime(stock["date"])
        stock = stock.sort_values("date").reset_index(drop=True)
        if len(stock) < 100: continue
        stock_idx = stock.set_index("date")

        df["net"] = df["buy"] - df["sell"]
        piv = df.pivot_table(index="date", columns="name", values="net", aggfunc="sum").fillna(0)
        if "Foreign_Investor" not in piv.columns or "Investment_Trust" not in piv.columns:
            continue

        # 5d rolling
        piv["f_5d"] = piv["Foreign_Investor"].rolling(5).sum()
        piv["it_5d"] = piv["Investment_Trust"].rolling(5).sum()
        piv["f_pct"] = piv["f_5d"].rank(pct=True)
        piv["it_pct"] = piv["it_5d"].rank(pct=True)

        for d in piv.index[60:]:
            f_pct = piv.loc[d, "f_pct"]
            it_pct = piv.loc[d, "it_pct"]
            if pd.isna(f_pct) or pd.isna(it_pct): continue

            # Forward 20d return
            try:
                d_pos = stock_idx.index.searchsorted(d)
                if d_pos + 20 >= len(stock_idx): continue
                e_px = float(stock_idx.iloc[d_pos]["close"])
                x_px = float(stock_idx.iloc[d_pos + 20]["close"])
                stock_ret = (x_px / e_px - 1) * 100

                # Baseline: 0050 same period
                etf_e_pos = etf_idx.index.searchsorted(stock_idx.index[d_pos])
                etf_x_pos = etf_idx.index.searchsorted(stock_idx.index[d_pos + 20])
                if etf_e_pos >= len(etf_idx) or etf_x_pos >= len(etf_idx): continue
                e_etf = float(etf_idx.iloc[etf_e_pos]["close"])
                x_etf = float(etf_idx.iloc[etf_x_pos]["close"])
                etf_ret = (x_etf / e_etf - 1) * 100
                alpha = stock_ret - etf_ret
            except Exception:
                continue

            # Categorize
            if f_pct > 0.9 and it_pct > 0.9:
                events["consensus_L"].append(stock_ret)
                alpha_events["consensus_L"].append(alpha)
            elif f_pct < 0.1 and it_pct < 0.1:
                events["consensus_S"].append(stock_ret)
                alpha_events["consensus_S"].append(alpha)
            elif f_pct > 0.9 and it_pct < 0.1:
                events["div_F_buy_IT_sell"].append(stock_ret)
                alpha_events["div_F_buy_IT_sell"].append(alpha)
            elif f_pct < 0.1 and it_pct > 0.9:
                events["div_F_sell_IT_buy"].append(stock_ret)
                alpha_events["div_F_sell_IT_buy"].append(alpha)
    except Exception:
        continue

print()
print("=== Raw return (Round 1 數據確認) ===")
for k, arr in events.items():
    report(f"  {k}", arr)

print()
print("=== Alpha vs 0050 (扣 baseline) ===")
for k, arr in alpha_events.items():
    report(f"  {k}", arr)

# 計算各類 alpha 差異
print()
print("=== Alpha 差距分析 ===")
if alpha_events["div_F_buy_IT_sell"] and alpha_events["consensus_L"]:
    div = np.array(alpha_events["div_F_buy_IT_sell"])
    con = np.array(alpha_events["consensus_L"])
    print(f"  Divergence (外資買+投信賣) alpha: {div.mean():+.3f}%")
    print(f"  Consensus LONG alpha:          {con.mean():+.3f}%")
    print(f"  差距 (divergence 優勢): {div.mean() - con.mean():+.3f}pp")
    print()
    print(f"  Divergence (外資賣+投信買) alpha: {np.array(alpha_events['div_F_sell_IT_buy']).mean():+.3f}%")
    print(f"  Consensus SHORT alpha:         {np.array(alpha_events['consensus_S']).mean():+.3f}%")
