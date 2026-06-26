"""TW Tier 1 sprint: IPO honeymoon + TX z>+2 SHORT (skip ETF rebalance - no holdings cache).

T1.1 IPO honeymoon
  - 910 IPOs (2020-2026), 測 30/60/90 d post-listing return
  - vs same-period 0050 baseline (alpha)
  - 切 industry / type (twse vs tpex)
  - 切 recent N years 看 decay

T1.2 TX OI z>+2 SHORT
  - Memory: z<-2 LONG +1.43% (foreign net OI extreme negative)
  - 對稱測 z>+2 SHORT (foreign over-long → reversal?)
  - hold 5/10/20 d
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
IPO_DF = pd.read_parquet(ROOT / "data" / "cache" / "finmind" / "ipo" / "ipo_list.parquet")
TX_INST = pd.read_parquet(ROOT / "data" / "cache" / "finmind" / "extras" / "futures_institutional.parquet")


def t_stat(arr):
    arr = np.asarray(arr, dtype=float)
    if len(arr) < 2 or arr.std(ddof=1) == 0:
        return 0.0
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
    icon = "✅" if (n >= 30 and t > 2 and mu > 0) else "⚠️" if (mu > 0 and n >= 20) else "❌"
    print(f"  {name}: n={n} mean={mu:+.3f}% t={t:+.2f} WR={wr:.0f}% {icon}")


# ════════════════════════════════════════════════════════════
# T1.1 IPO honeymoon
# ════════════════════════════════════════════════════════════
def test_ipo_honeymoon():
    print("\n" + "═"*70)
    print("T1.1 IPO honeymoon period")
    print("═"*70)
    df = IPO_DF.copy()
    df["date"] = pd.to_datetime(df["date"])
    print(f"  Total IPOs (cache): {len(df)}")
    print(f"  Type: TWSE={sum(df['type']=='twse')}, TPEX={sum(df['type']=='tpex')}")

    # Load 0050 for baseline
    etf = pd.read_parquet(CACHE / "0050.parquet")
    etf["date"] = pd.to_datetime(etf["date"])
    etf = etf.sort_values("date").reset_index(drop=True)
    etf_idx = etf.set_index("date")

    all_events = []
    skip_count = 0
    for _, ipo in df.iterrows():
        tk = str(ipo["stock_id"])
        # Skip ETFs (00xxxx) and special tickers
        if tk.startswith("00") or len(tk) != 4:
            continue
        p = CACHE / f"{tk}.parquet"
        if not p.exists():
            skip_count += 1
            continue
        try:
            stock = pd.read_parquet(p)
            stock["date"] = pd.to_datetime(stock["date"])
            stock = stock.sort_values("date").reset_index(drop=True)
            # Entry: first available date in stock cache >= ipo date
            ipo_dt = ipo["date"]
            entry = stock[stock["date"] >= ipo_dt].head(1)
            if entry.empty:
                continue
            entry_idx = entry.index[0]
            entry_px = float(entry["open"].iloc[0])
            entry_date = entry["date"].iloc[0]
            # For each hold period, compute pct return
            row = {"ticker": tk, "industry": ipo["industry_category"], "type": ipo["type"],
                   "ipo_date": ipo_dt, "entry_date": entry_date, "entry_px": entry_px}
            for h in [5, 10, 20, 30, 60, 90]:
                tgt_idx = entry_idx + h
                if tgt_idx >= len(stock):
                    row[f"ret_{h}d"] = np.nan
                    continue
                exit_px = float(stock["close"].iloc[tgt_idx])
                exit_date = stock["date"].iloc[tgt_idx]
                stock_ret = (exit_px / entry_px - 1) * 100
                # 0050 same period baseline
                try:
                    etf_e = etf_idx.iloc[etf_idx.index.searchsorted(entry_date)]
                    etf_x = etf_idx.iloc[min(etf_idx.index.searchsorted(exit_date), len(etf_idx)-1)]
                    etf_ret = (etf_x["close"] / etf_e["close"] - 1) * 100
                except Exception:
                    etf_ret = 0.0
                row[f"ret_{h}d"] = stock_ret
                row[f"alpha_{h}d"] = stock_ret - etf_ret
            all_events.append(row)
        except Exception:
            continue

    print(f"  IPOs with price data: {len(all_events)} (skipped {skip_count} - no price cache)")
    if not all_events:
        return
    edf = pd.DataFrame(all_events)

    for h in [5, 10, 20, 30, 60, 90]:
        col_ret = f"ret_{h}d"
        col_alpha = f"alpha_{h}d"
        sub = edf[~edf[col_ret].isna()]
        if len(sub) < 30:
            continue
        print(f"\n  Hold {h}d:")
        report(f"    Raw return", sub[col_ret].values)
        report(f"    Alpha vs 0050", sub[col_alpha].values)

    # Recent vs old (decay check)
    print("\n  Decay check (60d alpha by IPO year):")
    edf["year"] = edf["ipo_date"].dt.year
    for yr in sorted(edf["year"].unique()):
        sub = edf[(edf["year"] == yr) & ~edf["alpha_60d"].isna()]
        if len(sub) < 5:
            continue
        a = sub["alpha_60d"].mean()
        n = len(sub)
        print(f"    {yr}: n={n:>3} alpha={a:+.2f}% t={t_stat(sub['alpha_60d'].values):+.2f}")

    # By type (TWSE vs TPEX)
    print("\n  By type (60d alpha):")
    for typ in ["twse", "tpex"]:
        sub = edf[(edf["type"] == typ) & ~edf["alpha_60d"].isna()]
        if len(sub) >= 30:
            report(f"    {typ.upper()}", sub["alpha_60d"].values)


# ════════════════════════════════════════════════════════════
# T1.2 TX OI z>+2 SHORT
# ════════════════════════════════════════════════════════════
def test_tx_oi_extreme_short():
    print("\n" + "═"*70)
    print("T1.2 TX OI z>+2 SHORT (對稱 memory z<-2 LONG)")
    print("═"*70)
    df = TX_INST.copy()
    print(f"  TX inst cols: {list(df.columns)[:6]}")
    print(f"  total rows: {len(df)}")

    # Filter to TX foreign
    tx = df[(df["futures_id"] == "TX") & (df["institutional_investors"] == "外資")].copy()

    if tx.empty:
        print("  TX foreign 抓不到,skip")
        return
    print(f"  TX foreign rows: {len(tx)}")
    tx = tx.copy()
    tx["date"] = pd.to_datetime(tx["date"])
    tx = tx.sort_values("date").reset_index(drop=True)

    # Calculate net OI
    long_col = "long_open_interest_balance_volume"
    short_col = "short_open_interest_balance_volume"
    if long_col not in tx.columns or short_col not in tx.columns:
        print(f"  required cols missing")
        return
    print(f"  using {long_col} - {short_col}")
    tx["net_oi"] = tx[long_col].astype(float) - tx[short_col].astype(float)
    tx["net_oi_z"] = (tx["net_oi"] - tx["net_oi"].rolling(60).mean()) / tx["net_oi"].rolling(60).std()

    # Load TAIEX for return calc
    twii = CACHE / "^TWII.parquet"
    if not twii.exists():
        twii = CACHE / "0050.parquet"
    px = pd.read_parquet(twii)
    px["date"] = pd.to_datetime(px["date"])
    px = px.sort_values("date").reset_index(drop=True)
    px_idx = px.set_index("date")

    # Find z > +2 events
    extreme_high = tx[tx["net_oi_z"] > 2.0].copy()
    extreme_low = tx[tx["net_oi_z"] < -2.0].copy()
    print(f"\n  z > +2 events: {len(extreme_high)}")
    print(f"  z < -2 events: {len(extreme_low)} (memory baseline LONG)")

    # Dedupe (skip if within 5d of prior)
    def dedupe(df_sigs, gap=5):
        if df_sigs.empty:
            return df_sigs
        df_sigs = df_sigs.sort_values("date").reset_index(drop=True)
        keep = [True]
        for i in range(1, len(df_sigs)):
            gap_d = (df_sigs["date"].iloc[i] - df_sigs["date"].iloc[i-1]).days
            keep.append(gap_d >= gap)
        return df_sigs[keep]

    extreme_high = dedupe(extreme_high)
    extreme_low = dedupe(extreme_low)
    print(f"  After dedupe: high={len(extreme_high)}, low={len(extreme_low)}")

    for hold in [5, 10, 20]:
        print(f"\n  Hold {hold}d:")
        # SHORT at z > +2
        pnls = []
        for _, ev in extreme_high.iterrows():
            ev_date = ev["date"]
            try:
                e_idx = px.index[px["date"] >= ev_date][0]
                if e_idx + hold >= len(px):
                    continue
                e_px = float(px["close"].iloc[e_idx + 1])   # enter next day
                x_px = float(px["close"].iloc[e_idx + hold])
                pnl = (e_px - x_px) / e_px * 100   # SHORT
                pnls.append(pnl)
            except Exception:
                continue
        report(f"    z>+2 SHORT (新測)", pnls)

        # LONG at z < -2 (memory baseline check)
        pnls = []
        for _, ev in extreme_low.iterrows():
            ev_date = ev["date"]
            try:
                e_idx = px.index[px["date"] >= ev_date][0]
                if e_idx + hold >= len(px):
                    continue
                e_px = float(px["close"].iloc[e_idx + 1])
                x_px = float(px["close"].iloc[e_idx + hold])
                pnl = (x_px - e_px) / e_px * 100   # LONG
                pnls.append(pnl)
            except Exception:
                continue
        report(f"    z<-2 LONG (memory baseline)", pnls)


if __name__ == "__main__":
    print(f"TW Tier 1 — {datetime.now().strftime('%H:%M')}")
    test_ipo_honeymoon()
    test_tx_oi_extreme_short()
