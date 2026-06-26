"""TW Tier 1B sprint:
  A1. 除權息日反轉 (Dividend ex-day reversal)
  A2. 外資+投信 divergence
  A3. TW IPO SHORT (限定可借券 universe)
"""
from __future__ import annotations
import sys, io, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
FC = ROOT / "data" / "cache" / "finmind" / "finmind"
DIV_CACHE = ROOT / "data" / "cache" / "finmind" / "dividend"
MARGIN_CACHE = ROOT / "data" / "cache" / "finmind"


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
    icon = "✅" if (n>=30 and t>2 and mu>0) else "⚠️" if (mu>0 and n>=20) else "❌"
    print(f"  {name}: n={n} mean={mu:+.3f}% t={t:+.2f} WR={wr:.0f}% {icon}")


# ════════════════════════════════════════════════════════════
# A1. 除權息日反轉
# ════════════════════════════════════════════════════════════
def test_a1_dividend_reversal():
    print("\n" + "═"*70)
    print("A1. 除權息日反轉")
    print("═"*70)
    print("除權息日股價自動扣除股息金額 → 常見情緒過度賣壓 → 5-10 日反彈")

    # Find dividend cache
    div_files = list(DIV_CACHE.glob("*.parquet"))
    print(f"  Dividend parquet files: {len(div_files)}")
    if not div_files:
        # Try alternative
        for sub in [FC, ROOT / "data" / "cache" / "finmind"]:
            div_files = list(sub.glob("*Dividend*.parquet"))
            if div_files: break

    if not div_files:
        print("  ❌ no dividend cache - skip")
        return

    # Use main dividend file
    main = div_files[0]
    print(f"  Loading {main.name}")
    df_div = pd.read_parquet(main)
    print(f"  cols: {list(df_div.columns)[:8]}")
    print(f"  rows: {len(df_div)}")

    if "CashEarningsDistribution" not in df_div.columns and "cash_dividend" not in df_div.columns:
        # Pick first cash-like col
        cash_col = next((c for c in df_div.columns if "ash" in c.lower() and "div" in c.lower()), None)
    else:
        cash_col = "CashEarningsDistribution" if "CashEarningsDistribution" in df_div.columns else "cash_dividend"

    print(f"  Cash dividend column: {cash_col}")
    if not cash_col:
        return

    # Date column
    date_col = None
    for c in df_div.columns:
        if "ex" in c.lower() and ("date" in c.lower() or "day" in c.lower()):
            date_col = c
            break
    if not date_col:
        date_col = next((c for c in df_div.columns if "date" in c.lower()), None)
    print(f"  Date column: {date_col}")
    if not date_col:
        return

    df_div = df_div.copy()
    df_div[date_col] = pd.to_datetime(df_div[date_col], errors='coerce')
    df_div = df_div[df_div[date_col].notna()]
    df_div = df_div[df_div[cash_col].astype(float) > 0]   # 只看有除現金
    print(f"  After filter (cash > 0, date valid): {len(df_div)}")

    # Find ticker column
    tk_col = next((c for c in df_div.columns if "stock_id" in c.lower() or c.lower() == "id"), None)
    print(f"  Ticker column: {tk_col}")

    # For each event, get N-day forward return
    events = []
    for _, ev in df_div.iterrows():
        tk = str(ev[tk_col])
        ex_date = ev[date_col]
        cash_div = float(ev[cash_col])
        p_stock = CACHE / f"{tk}.parquet"
        if not p_stock.exists():
            continue
        try:
            stock = pd.read_parquet(p_stock)
            stock["date"] = pd.to_datetime(stock["date"])
            stock = stock.sort_values("date").reset_index(drop=True)
            ex_idx = stock.index[stock["date"] >= ex_date]
            if len(ex_idx) == 0: continue
            ex_idx = ex_idx[0]
            if ex_idx + 10 >= len(stock): continue

            ex_open = float(stock["open"].iloc[ex_idx])
            ex_close = float(stock["close"].iloc[ex_idx])
            prev_close = float(stock["close"].iloc[ex_idx - 1]) if ex_idx > 0 else ex_open
            if prev_close <= 0: continue
            div_yield_pct = cash_div / prev_close * 100

            # 5d / 10d forward from ex-day open
            for h in [3, 5, 10, 20]:
                if ex_idx + h >= len(stock): break
                exit_px = float(stock["close"].iloc[ex_idx + h])
                ret_pct = (exit_px / ex_open - 1) * 100 - 0.585
                events.append({
                    "ticker": tk, "ex_date": ex_date, "cash_div": cash_div,
                    "div_yield_pct": div_yield_pct, "hold_d": h, "ret": ret_pct
                })
        except Exception:
            continue
    if not events:
        print("  ❌ no events processed")
        return
    edf = pd.DataFrame(events)
    print(f"\n  Events processed: {len(edf['ticker'].unique())} unique tickers, {len(edf)//4} unique events")

    for h in [3, 5, 10, 20]:
        sub = edf[edf["hold_d"] == h]
        if len(sub) >= 30:
            print(f"\n  Hold {h}d:")
            report(f"    All events", sub["ret"].values)
            # By yield quintile
            sub2 = sub.copy()
            sub2["q"] = pd.qcut(sub2["div_yield_pct"], q=4, labels=["Q1 low","Q2","Q3","Q4 high"])
            for q, grp in sub2.groupby("q", observed=True):
                if len(grp) >= 20:
                    report(f"    yield {q}", grp["ret"].values)


# ════════════════════════════════════════════════════════════
# A2. 法人 divergence (外資強買 + 投信反向)
# ════════════════════════════════════════════════════════════
def test_a2_inst_divergence():
    print("\n" + "═"*70)
    print("A2. 法人 divergence (外資 strong buy + 投信 strong sell)")
    print("═"*70)
    print("Hypothesis: 外資+投信 同方向 = strong consensus,反方向 = 籌碼移轉")

    print("  Loading institutional caches...")
    inst_files = list(FC.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))[:500]   # cap
    print(f"  Found {len(inst_files)} ticker caches")
    if not inst_files:
        print("  ❌ no inst cache")
        return

    all_events_consensus_long = []   # 外資 buy + 投信 buy
    all_events_consensus_short = []  # 外資 sell + 投信 sell
    all_events_divergence_pos = []   # 外資 buy + 投信 sell
    all_events_divergence_neg = []   # 外資 sell + 投信 buy

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

            # Compute net buy by investor
            df["net"] = df["buy"] - df["sell"]
            piv = df.pivot_table(index="date", columns="name", values="net", aggfunc="sum").fillna(0)
            if "Foreign_Investor" not in piv.columns or "Investment_Trust" not in piv.columns:
                continue
            piv["f_net"] = piv["Foreign_Investor"]
            piv["it_net"] = piv["Investment_Trust"]

            # 5d rolling
            piv["f_5d"] = piv["f_net"].rolling(5).sum()
            piv["it_5d"] = piv["it_net"].rolling(5).sum()

            # Use percentile within ticker history
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
                    ret_pct = (x_px / e_px - 1) * 100
                except Exception:
                    continue

                # Categorize
                if f_pct > 0.9 and it_pct > 0.9:
                    all_events_consensus_long.append(ret_pct)
                elif f_pct < 0.1 and it_pct < 0.1:
                    all_events_consensus_short.append(ret_pct)
                elif f_pct > 0.9 and it_pct < 0.1:
                    all_events_divergence_pos.append(ret_pct)
                elif f_pct < 0.1 and it_pct > 0.9:
                    all_events_divergence_neg.append(ret_pct)
        except Exception:
            continue

    print(f"\n  Consensus LONG (外資 + 投信 都強買): n={len(all_events_consensus_long)}")
    if all_events_consensus_long:
        report("    20d return", all_events_consensus_long)
    print(f"\n  Consensus SHORT (外資 + 投信 都強賣): n={len(all_events_consensus_short)}")
    if all_events_consensus_short:
        report("    20d return", all_events_consensus_short)
    print(f"\n  Divergence (外資買 + 投信賣) — 跟外資: n={len(all_events_divergence_pos)}")
    if all_events_divergence_pos:
        report("    20d return", all_events_divergence_pos)
    print(f"\n  Divergence (外資賣 + 投信買) — 跟投信: n={len(all_events_divergence_neg)}")
    if all_events_divergence_neg:
        report("    20d return", all_events_divergence_neg)


# ════════════════════════════════════════════════════════════
# A3. TW IPO SHORT 限定可借券 universe
# ════════════════════════════════════════════════════════════
def test_a3_ipo_short_shortable():
    print("\n" + "═"*70)
    print("A3. TW IPO SHORT — 限定有融券餘額 universe")
    print("═"*70)
    print("Memory T1.1: 全 IPO 60d alpha -6.87% t=-4.72")
    print("這次限定有融券記錄 (代表可借券) 的 IPO")

    ipo_df = pd.read_parquet(ROOT / "data" / "cache" / "finmind" / "ipo" / "ipo_list.parquet")
    ipo_df["date"] = pd.to_datetime(ipo_df["date"])
    print(f"  Total IPOs: {len(ipo_df)}")

    # Find margin cache
    margin_files = list((ROOT / "data" / "cache" / "finmind" / "margin").glob("*.parquet"))
    print(f"  Margin parquet files: {len(margin_files)}")
    if not margin_files:
        # Try in finmind folder
        margin_files = list((FC).glob("*MarginPurchase*.parquet"))[:300]
        print(f"  Found in finmind: {len(margin_files)}")

    if not margin_files:
        print("  ⚠️ no margin cache, fallback: 假設所有 TWSE IPO 都可借券 (TPEX 多數不可)")
        sub = ipo_df[ipo_df["type"] == "twse"]
    else:
        # Get tickers that have margin data
        shortable_tickers = set()
        for p in margin_files:
            tk = p.stem.split("_")[-1]
            if tk.isdigit():
                shortable_tickers.add(tk)
        print(f"  Shortable tickers (margin data exist): {len(shortable_tickers)}")
        sub = ipo_df[ipo_df["stock_id"].astype(str).isin(shortable_tickers)]

    print(f"  IPO universe (shortable): {len(sub)}")

    # Run SHORT 60d backtest
    etf = pd.read_parquet(CACHE / "0050.parquet")
    etf["date"] = pd.to_datetime(etf["date"])
    etf = etf.sort_values("date").reset_index(drop=True)
    etf_idx = etf.set_index("date")

    all_short_returns = []
    all_alpha = []
    skip = 0
    for _, ipo in sub.iterrows():
        tk = str(ipo["stock_id"])
        if not tk.isdigit() or len(tk) != 4: continue
        p = CACHE / f"{tk}.parquet"
        if not p.exists():
            skip += 1
            continue
        try:
            stock = pd.read_parquet(p)
            stock["date"] = pd.to_datetime(stock["date"])
            stock = stock.sort_values("date").reset_index(drop=True)
            ipo_dt = ipo["date"]
            # Entry: 30 days after IPO (avoid initial volatility)
            entry = stock[stock["date"] >= ipo_dt + pd.Timedelta(days=30)].head(1)
            if entry.empty: continue
            entry_idx = entry.index[0]
            entry_px = float(entry["open"].iloc[0])
            entry_date = entry["date"].iloc[0]
            tgt_idx = entry_idx + 60   # 60d after entry
            if tgt_idx >= len(stock): continue
            exit_px = float(stock["close"].iloc[tgt_idx])
            exit_date = stock["date"].iloc[tgt_idx]
            # SHORT pnl
            stock_ret = (exit_px / entry_px - 1) * 100
            short_pnl = -stock_ret - 0.585 * 2   # short cost double
            # 0050 baseline
            try:
                etf_e = etf_idx.iloc[etf_idx.index.searchsorted(entry_date)]
                etf_x = etf_idx.iloc[min(etf_idx.index.searchsorted(exit_date), len(etf_idx)-1)]
                etf_ret = (etf_x["close"] / etf_e["close"] - 1) * 100
                alpha = short_pnl - (-etf_ret)  # SHORT alpha: did we outperform shorting 0050?
            except Exception:
                alpha = 0
            all_short_returns.append(short_pnl)
            all_alpha.append(alpha)
        except Exception:
            continue

    print(f"  Processed: {len(all_short_returns)} (skipped {skip})")
    if all_short_returns:
        print(f"\n  IPO SHORT 60d (entry 30d post IPO):")
        report("    Net SHORT pnl", all_short_returns)
        report("    Alpha vs SHORT 0050", all_alpha)


if __name__ == "__main__":
    print(f"TW Tier 1B — {datetime.now().strftime('%H:%M')}")
    test_a1_dividend_reversal()
    test_a2_inst_divergence()
    test_a3_ipo_short_shortable()
