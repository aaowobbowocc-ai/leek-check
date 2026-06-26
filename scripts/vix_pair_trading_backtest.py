"""
VIX × 配對交易 spread z-score

Hypothesis: 高 VIX 時 spread divergence 更激烈，mean reversion alpha 更強

對 6 對 Tier A 配對驗證 VIX dependency
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
ENTRY_Z = 2.5
EXIT_Z = 0.5
TIMEOUT = 20
ROLL = 60

PAIRS = [
    ("DRAM 2408-2344", "2408", "2344"),
    ("重電 1513-1519", "1513", "1519"),
    ("半導體 2330-3711", "2330", "3711"),
    ("半導體 2454-3711", "2454", "3711"),
    ("航運 2609-2615", "2609", "2615"),
    ("塑化 1301-1326", "1301", "1326"),
]


def load(tk):
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_vix():
    import yfinance as yf
    h = yf.Ticker("^VIX").history(period="3500d", auto_adjust=False)
    df = pd.DataFrame({"date": pd.to_datetime(h.index).tz_localize(None),
                       "vix": h["Close"].values})
    return df.set_index("date")["vix"].to_dict()


def simulate_pair(name, a_tk, b_tk, vix_map):
    a = load(a_tk); b = load(b_tk)
    if a.empty or b.empty: return []
    m = pd.merge(a[["date","close"]].rename(columns={"close":"a"}),
                 b[["date","close"]].rename(columns={"close":"b"}),
                 on="date").sort_values("date").reset_index(drop=True)
    if len(m) < ROLL + 60: return []
    m["log_a"] = np.log(m["a"])
    m["log_b"] = np.log(m["b"])
    m["spread"] = m["log_a"] - m["log_b"]
    m["spread_ma"] = m["spread"].rolling(ROLL).mean()
    m["spread_std"] = m["spread"].rolling(ROLL).std()
    m["z"] = (m["spread"] - m["spread_ma"]) / m["spread_std"]

    trades = []
    pos = None; entry_idx = None; entry_spread = None
    for i in range(len(m)):
        z = m["z"].iloc[i]
        if pd.isna(z): continue
        if pos is None:
            if z > ENTRY_Z:
                pos = "short"; entry_idx = i; entry_spread = m["spread"].iloc[i]
            elif z < -ENTRY_Z:
                pos = "long"; entry_idx = i; entry_spread = m["spread"].iloc[i]
            continue
        days_held = i - entry_idx
        if abs(z) < EXIT_Z or days_held >= TIMEOUT:
            exit_spread = m["spread"].iloc[i]
            if pos == "short":
                pnl = (entry_spread - exit_spread) * 100
            else:
                pnl = (exit_spread - entry_spread) * 100
            entry_d = m["date"].iloc[entry_idx]
            # VIX at entry
            vix = None
            for offset in range(7):
                d_check = entry_d - pd.Timedelta(days=offset)
                if d_check in vix_map:
                    vix = vix_map[d_check]; break
            trades.append({
                "pair": name, "entry_date": entry_d,
                "pnl_pct": pnl - 0.68,  # 雙邊摩擦
                "days_held": days_held, "vix": vix,
            })
            pos = None
    return trades


def main():
    print("=" * 80)
    print("  VIX × 配對交易 Spread")
    print("=" * 80)
    vix_map = load_vix()
    all_trades = []
    for name, a, b in PAIRS:
        trades = simulate_pair(name, a, b, vix_map)
        print(f"  {name}: {len(trades)} trades")
        all_trades.extend(trades)

    df = pd.DataFrame(all_trades).dropna(subset=["vix"])
    print(f"\n  Total: {len(df)} trades with VIX")
    print(f"\n  📊 配對交易 by VIX bucket:")
    print(f"  {'bucket':<20} {'n':<5} {'mean':<8} {'win%':<6}")
    for blabel, sub in [
        ("low (vix<18)", df[df["vix"] < 18]),
        ("mid (18-25)", df[(df["vix"] >= 18) & (df["vix"] < 25)]),
        ("high (25-35)", df[(df["vix"] >= 25) & (df["vix"] < 35)]),
        ("extreme (≥35)", df[df["vix"] >= 35]),
    ]:
        if len(sub) < 10: continue
        n = len(sub)
        mean = sub["pnl_pct"].mean()
        win = (sub["pnl_pct"] > 0).mean() * 100
        print(f"  {blabel:<20} {n:<5} {mean:+.2f}%  {win:.1f}%")


if __name__ == "__main__":
    main()
