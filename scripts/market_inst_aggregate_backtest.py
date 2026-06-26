"""
全市場法人總買超 z-score → TAIEX timing alpha

Hypothesis: 全市場法人 net buy 加總（外資+投信+自營商）的極端 z 預測 TAIEX

跟 government bank 全市場 aggregate 不同：
  - government bank 已驗證弱 (+0.89%, t=1.05)
  - 但全部法人是真正的 smart money

包含完整 OOS + MCPT 驗證流程
"""
from __future__ import annotations
import io, sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
HOLD_PERIODS = [5, 10, 20, 60]
ROLL_WINDOW = 60
N_PERMUTE = 1000


def aggregate_market_inst():
    """彙總全市場法人 daily net buy"""
    print("  彙總全市場法人 net buy...")
    daily_total = {}
    for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"):
        try:
            df = pd.read_parquet(p)
            df["net"] = df["buy"] - df["sell"]
            df["date"] = pd.to_datetime(df["date"])
            agg = df.groupby("date")["net"].sum()
            for d, v in agg.items():
                daily_total[d] = daily_total.get(d, 0) + v
        except Exception:
            continue
    market = pd.DataFrame({"date": list(daily_total.keys()),
                          "inst_net": list(daily_total.values())}).sort_values("date").reset_index(drop=True)
    market["inst_net_ma"] = market["inst_net"].rolling(ROLL_WINDOW).mean()
    market["inst_net_std"] = market["inst_net"].rolling(ROLL_WINDOW).std()
    market["z"] = (market["inst_net"] - market["inst_net_ma"]) / market["inst_net_std"]
    return market.dropna(subset=["z"]).reset_index(drop=True)


def merge_taiex(market):
    import yfinance as yf
    h = yf.Ticker("^TWII").history(period="3000d", auto_adjust=False)
    spot = pd.DataFrame({
        "date": pd.to_datetime(h.index).tz_localize(None),
        "close": h["Close"].values,
    })
    df = market.merge(spot, on="date", how="inner")
    for hd in HOLD_PERIODS:
        df[f"fwd_{hd}d"] = (df["close"].shift(-hd) / df["close"] - 1) * 100
    df = df.dropna(subset=[f"fwd_{HOLD_PERIODS[-1]}d"])
    df["year"] = df["date"].dt.year
    return df


def event_study(df, z_thresh, hold):
    fwd = f"fwd_{hold}d"
    base_mean = df[fwd].mean()
    base_std = df[fwd].std()
    long_s = df[df["z"] > z_thresh]
    short_s = df[df["z"] < -z_thresh]
    rows = []
    for direction, sub in [("long", long_s), ("short", short_s)]:
        n = len(sub.dropna(subset=[fwd]))
        if n < 30: continue
        mean = sub[fwd].mean()
        if direction == "long":
            alpha = mean - base_mean
            win = (sub[fwd] > 0).mean() * 100
        else:
            alpha = base_mean - mean
            win = (sub[fwd] < 0).mean() * 100
        t = alpha / (base_std / np.sqrt(n)) if base_std > 0 else None
        rows.append({"z": z_thresh, "hold": hold, "direction": direction,
                    "n": n, "mean": round(mean, 2), "alpha": round(alpha, 2),
                    "win_pct": round(win, 1),
                    "t": round(t, 2) if t else None})
    return rows


def find_best(df):
    """掃 z × hold 找最佳 long/short"""
    print("\n  ▶ Event Study Grid:")
    rows = []
    for z in [1.0, 1.5, 2.0, 2.5]:
        for h in HOLD_PERIODS:
            rows.extend(event_study(df, z, h))
    grid = pd.DataFrame(rows)
    print("\n  Top 5 long:")
    top_l = grid[grid["direction"] == "long"].sort_values("alpha", ascending=False).head(5)
    print(top_l.to_string(index=False))
    print("\n  Top 5 short (預期跌):")
    top_s = grid[grid["direction"] == "short"].sort_values("alpha", ascending=False).head(5)
    print(top_s.to_string(index=False))
    return grid


def oos_test(df, z_thresh, hold, direction):
    """3 期 OOS"""
    print(f"\n  📅 OOS: z={z_thresh}, hold={hold}d, dir={direction}")
    splits = [
        ("2017-2019", df[df["year"] <= 2019]),
        ("2020-2022", df[(df["year"] >= 2020) & (df["year"] <= 2022)]),
        ("2023-2025", df[df["year"] >= 2023]),
    ]
    n_pass = 0
    for label, sub in splits:
        if direction == "long":
            triggers = sub[sub["z"] > z_thresh]
        else:
            triggers = sub[sub["z"] < -z_thresh]
        fwd = f"fwd_{hold}d"
        n = len(triggers.dropna(subset=[fwd]))
        if n < 20:
            print(f"    {label}: n={n} (太少)")
            continue
        sig_mean = triggers[fwd].mean()
        base_mean = sub[fwd].mean()
        base_std = sub[fwd].std()
        if direction == "long":
            alpha = sig_mean - base_mean
        else:
            alpha = base_mean - sig_mean
        t = alpha / (base_std / np.sqrt(n)) if base_std > 0 else None
        verdict = "✅" if alpha > 0.5 and (t or 0) > 1.5 else "⚠️"
        if alpha > 0.5 and (t or 0) > 1.5: n_pass += 1
        t_str = f"{t:+.2f}" if t else "n/a"
        print(f"    {label}: n={n}, alpha={alpha:+.2f}%, t={t_str} {verdict}")
    return n_pass


def mcpt_test(df, z_thresh, hold, direction):
    """MCPT: random shuffle z labels"""
    print(f"\n  🎲 MCPT: z={z_thresh}, hold={hold}d, dir={direction}")
    fwd = f"fwd_{hold}d"
    if direction == "long":
        triggers = df[df["z"] > z_thresh]
    else:
        triggers = df[df["z"] < -z_thresh]
    n_signals = len(triggers.dropna(subset=[fwd]))
    if n_signals < 30: return None

    sig_mean = triggers[fwd].mean()
    base_mean = df[fwd].mean()
    if direction == "long":
        real_alpha = sig_mean - base_mean
    else:
        real_alpha = base_mean - sig_mean

    rng = np.random.RandomState(42)
    fwd_arr = df[fwd].dropna().values
    fakes = []
    for _ in range(N_PERMUTE):
        idx = rng.choice(len(fwd_arr), size=n_signals, replace=False)
        fake_sig_mean = fwd_arr[idx].mean()
        if direction == "long":
            fake_alpha = fake_sig_mean - fwd_arr.mean()
        else:
            fake_alpha = fwd_arr.mean() - fake_sig_mean
        fakes.append(fake_alpha)
    fakes = np.array(fakes)
    p = (fakes >= real_alpha).sum() / N_PERMUTE
    print(f"    Real alpha: {real_alpha:+.3f}%, MCPT p={p:.4f} {'✅' if p<0.05 else '❌'}")
    return p


def main():
    print("=" * 80)
    print("  全市場法人總買超 z-score → TAIEX timing alpha")
    print("=" * 80)

    market = aggregate_market_inst()
    print(f"  Market days: {len(market)}, range {market['date'].min().date()} ~ {market['date'].max().date()}")

    df = merge_taiex(market)
    print(f"  Merged with TAIEX: {len(df)}")

    grid = find_best(df)

    # 對每個 top alpha 跑 OOS + MCPT
    print("\n" + "=" * 80)
    print("  對 Top 4 候選做 OOS + MCPT")
    print("=" * 80)
    candidates = []
    for direction in ["long", "short"]:
        sub = grid[grid["direction"] == direction]
        for _, row in sub.sort_values("alpha", ascending=False).head(2).iterrows():
            if row["alpha"] > 0.5 and (row["t"] or 0) > 2:
                candidates.append((row["z"], row["hold"], direction))

    for z, h, d in candidates:
        print(f"\n--- candidate: z={z}, hold={h}d, dir={d} ---")
        oos_pass = oos_test(df, z, h, d)
        p = mcpt_test(df, z, h, d)
        verdict = "✅ 通過" if oos_pass >= 3 and (p or 1) < 0.05 else "⚠️ 未過 OOS / MCPT"
        print(f"\n  Verdict: OOS robust {oos_pass}/3, MCPT p={p:.4f if p else 'n/a'} → {verdict}")


if __name__ == "__main__":
    main()
