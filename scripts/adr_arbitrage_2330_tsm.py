"""
ADR arbitrage backtest: TSM (NYSE ADR) overnight -> 2330.TW next-day open->close.

Hypothesis: TSM close vs 2330 next-day open exhibits ADR-implied premium.
When premium z>+2 (TSM rich), 2330 opens fade. z<-2 (TSM cheap), 2330 chases up.
Long-only (no SHORT, no US brokerage).

Entry: 2330 next-day OPEN. Exit: same-day CLOSE. Cost: 0.585% round-trip.
Validation: OOS 2010-17 / 2018-25 + MCPT 1000 + cost-adj win rate + outlier-strip.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:/Users/USER/Desktop/INVEST")
CACHE = ROOT / "data/cache/yfinance/global"
LOG_PATH = ROOT / "logs/adr_arbitrage_2330_tsm.csv"

COST_RT = 0.00585  # 0.585% round-trip (個股 含稅+手續費+滑價)
Z_LONG = -2.0      # TSM cheap -> long 2330 next open
Z_SHORT = 2.0      # would short, but skipped (no margin)
ROLL_WIN = 60      # rolling z-score window

RNG = np.random.default_rng(42)


def load_prices() -> pd.DataFrame:
    tsm = pd.read_parquet(CACHE / "TSM.parquet")[["date", "close", "open"]].rename(
        columns={"close": "tsm_close", "open": "tsm_open"}
    )
    tw = pd.read_parquet(CACHE / "2330_TW.parquet")[["date", "close", "open"]].rename(
        columns={"close": "tw_close", "open": "tw_open"}
    )
    tsm["date"] = pd.to_datetime(tsm["date"]).dt.date
    tw["date"] = pd.to_datetime(tw["date"]).dt.date

    # FX -- cache is short, download full history via yfinance
    try:
        import yfinance as yf
        fx_df = yf.download("TWD=X", start="2009-12-01", end="2026-05-07",
                            progress=False, auto_adjust=False)
        if fx_df.empty:
            raise RuntimeError("yfinance returned empty TWD=X")
        if isinstance(fx_df.columns, pd.MultiIndex):
            fx_df.columns = fx_df.columns.get_level_values(0)
        fx = fx_df.reset_index()[["Date", "Close"]].rename(
            columns={"Date": "date", "Close": "usdtwd"}
        )
        fx["date"] = pd.to_datetime(fx["date"]).dt.date
    except Exception as e:
        print(f"[warn] yfinance fetch failed ({e}); falling back to constant USDTWD=30.5")
        all_dates = sorted(set(tsm["date"]) | set(tw["date"]))
        fx = pd.DataFrame({"date": all_dates, "usdtwd": 30.5})

    return tsm, tw, fx


def build_signal(tsm: pd.DataFrame, tw: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    """
    Align: for each TW trading day d (entry day), the 'overnight info' is
    TSM_close on US date d-1 (which is reported BEFORE TW opens on date d).
    Premium = TSM_close_USD * USDTWD / 2330_close_TWD_prev_day  (>1 = TSM rich)
    Then z-score the premium over rolling 60d.
    """
    tw = tw.sort_values("date").reset_index(drop=True)
    tsm = tsm.sort_values("date").reset_index(drop=True)
    fx = fx.sort_values("date").reset_index(drop=True)

    # Forward-fill FX onto a daily index, then map to each TSM date
    all_dates = pd.date_range(min(min(tw["date"]), min(tsm["date"])),
                              max(max(tw["date"]), max(tsm["date"])))
    fx_idx = pd.DataFrame({"date": [d.date() for d in all_dates]})
    fx_idx = fx_idx.merge(fx, on="date", how="left").ffill()

    tsm = tsm.merge(fx_idx, on="date", how="left")

    # For each TW day d, lookup most-recent TSM close strictly before d (asof)
    tw_dt = pd.DataFrame({"date": tw["date"]})
    tw_dt["dt"] = pd.to_datetime(tw_dt["date"])
    tsm["dt"] = pd.to_datetime(tsm["date"])
    tw_dt = tw_dt.sort_values("dt")
    tsm_sorted = tsm.sort_values("dt")
    # asof: last TSM row with dt < tw_dt
    merged = pd.merge_asof(
        tw_dt, tsm_sorted[["dt", "tsm_close", "usdtwd"]],
        on="dt", direction="backward", allow_exact_matches=False,
    )
    merged = merged.merge(tw, on="date", how="left").sort_values("date").reset_index(drop=True)
    # need previous-day 2330 close to compute premium ratio (info available before open)
    merged["tw_close_prev"] = merged["tw_close"].shift(1)

    # ADR-implied TWD price = TSM_USD * USDTWD * (2330_shares_per_ADR=5)
    # But ratio shifts; what matters is z-score of the implied/actual ratio
    merged["tsm_implied_twd"] = merged["tsm_close"] * merged["usdtwd"] * 5.0
    merged["premium"] = (merged["tsm_implied_twd"] / merged["tw_close_prev"]) - 1.0

    # rolling z (60d) using only prior data (shift 1 to avoid same-day contamination)
    prem = merged["premium"]
    mu = prem.shift(1).rolling(ROLL_WIN, min_periods=30).mean()
    sd = prem.shift(1).rolling(ROLL_WIN, min_periods=30).std()
    merged["z"] = (prem - mu) / sd

    # Trade outcome: open->close on same TW day d
    merged["intraday_ret"] = merged["tw_close"] / merged["tw_open"] - 1.0
    return merged


def run_trades(df: pd.DataFrame) -> pd.DataFrame:
    trades = []
    for _, r in df.iterrows():
        z = r["z"]
        if pd.isna(z) or pd.isna(r["intraday_ret"]):
            continue
        if z <= Z_LONG:
            direction = "LONG"
            gross = r["intraday_ret"]
        elif z >= Z_SHORT:
            # skipped per spec (no shorting capacity for retail in TW individual)
            continue
        else:
            continue
        net = gross - COST_RT
        trades.append({
            "date": r["date"],
            "direction": direction,
            "z": z,
            "premium": r["premium"],
            "tsm_close": r["tsm_close"],
            "usdtwd": r["usdtwd"],
            "tw_open": r["tw_open"],
            "tw_close": r["tw_close"],
            "gross_ret": gross,
            "cost": COST_RT,
            "net_ret": net,
        })
    return pd.DataFrame(trades)


def t_stat(arr: np.ndarray) -> float:
    if len(arr) < 2:
        return 0.0
    sd = arr.std(ddof=1)
    if sd == 0:
        return 0.0
    return arr.mean() / (sd / np.sqrt(len(arr)))


def mcpt(all_intraday: np.ndarray, n_sample: int, observed_mean: float, n_iter: int = 1000) -> float:
    if n_sample == 0:
        return 1.0
    pool = all_intraday[~np.isnan(all_intraday)]
    if len(pool) < n_sample:
        return 1.0
    hits = 0
    for _ in range(n_iter):
        sim = RNG.choice(pool, size=n_sample, replace=False) - COST_RT
        if sim.mean() >= observed_mean:
            hits += 1
    return hits / n_iter


def main():
    print("[1/4] Loading prices + FX...")
    tsm, tw, fx = load_prices()
    print(f"  TSM rows={len(tsm)} TW rows={len(tw)} FX rows={len(fx)}")

    print("[2/4] Building signal...")
    df = build_signal(tsm, tw, fx)
    print(f"  Aligned rows={len(df)} (with z-score: {df['z'].notna().sum()})")

    print("[3/4] Running trades (LONG only, z<={:.1f})...".format(Z_LONG))
    trades = run_trades(df)
    print(f"  N trades = {len(trades)}")
    if len(trades) == 0:
        print("FAIL: no trades produced")
        return

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(LOG_PATH, index=False)
    print(f"  Saved {LOG_PATH}")

    print("[4/4] Stats...")
    nets = trades["net_ret"].values
    grosses = trades["gross_ret"].values

    full_mean_net = nets.mean()
    full_mean_gross = grosses.mean()
    full_t = t_stat(nets)
    win_rate = (nets > 0).mean()
    cum_net = (1 + nets).prod() - 1

    # OOS
    trades["dt"] = pd.to_datetime(trades["date"])
    train = trades[trades["dt"] < "2018-01-01"]["net_ret"].values
    test = trades[trades["dt"] >= "2018-01-01"]["net_ret"].values
    train_mean = train.mean() if len(train) else float("nan")
    test_mean = test.mean() if len(test) else float("nan")
    train_t = t_stat(train) if len(train) else 0.0
    test_t = t_stat(test) if len(test) else 0.0

    # Outlier strip (top/bottom 5%)
    if len(nets) >= 20:
        lo, hi = np.percentile(nets, [5, 95])
        trimmed = nets[(nets >= lo) & (nets <= hi)]
        trim_mean = trimmed.mean()
        trim_t = t_stat(trimmed)
    else:
        trim_mean = trim_t = float("nan")

    # MCPT vs random same-day intraday returns
    all_intraday = df["intraday_ret"].dropna().values
    mcpt_p = mcpt(all_intraday, len(nets), full_mean_net, n_iter=1000)

    # Verdict
    gates = {
        "OOS_train_pos_t15": (train_mean > 0) and (train_t > 1.5),
        "OOS_test_pos_t15": (test_mean > 0) and (test_t > 1.5),
        "MCPT_p<0.05": mcpt_p < 0.05,
        "win_rate>50%": win_rate > 0.50,
        "trimmed_mean>0": trim_mean > 0,
    }
    n_pass = sum(gates.values())
    if n_pass == 5:
        verdict = "PASS"
    elif n_pass >= 3:
        verdict = "EDGE"
    else:
        verdict = "FAIL"

    print("\n" + "=" * 60)
    print("ADR Arbitrage 2330 vs TSM — Backtest Summary")
    print("=" * 60)
    print(f"N trades           = {len(trades)}")
    print(f"Date range         = {trades['date'].min()} .. {trades['date'].max()}")
    print(f"Mean GROSS         = {full_mean_gross*100:+.4f}%")
    print(f"Mean NET (cost)    = {full_mean_net*100:+.4f}%  (cost={COST_RT*100:.3f}%)")
    print(f"t-stat (net)       = {full_t:.3f}")
    print(f"Win rate (net>0)   = {win_rate*100:.2f}%")
    print(f"Cumulative net     = {cum_net*100:+.2f}%")
    print(f"Trimmed (5/95) net = {trim_mean*100:+.4f}%  t={trim_t:.2f}")
    print(f"OOS TRAIN (<2018)  = {train_mean*100:+.4f}%  t={train_t:.2f}  n={len(train)}")
    print(f"OOS TEST  (>=2018) = {test_mean*100:+.4f}%  t={test_t:.2f}  n={len(test)}")
    print(f"MCPT p (1000 iter) = {mcpt_p:.4f}")
    print()
    for g, ok in gates.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {g}")
    print()
    print(f"VERDICT: {verdict}  ({n_pass}/5 gates)")
    print("=" * 60)

    # Stash a one-line summary
    summary = {
        "verdict": verdict,
        "n_trades": len(trades),
        "mean_net": full_mean_net,
        "t_stat": full_t,
        "win_rate": win_rate,
        "train_mean": train_mean, "train_t": train_t, "n_train": len(train),
        "test_mean": test_mean, "test_t": test_t, "n_test": len(test),
        "mcpt_p": mcpt_p,
        "trim_mean": trim_mean,
        "gates_passed": n_pass,
    }
    pd.DataFrame([summary]).to_csv(ROOT / "logs/adr_arbitrage_2330_tsm_summary.csv", index=False)
    return summary


if __name__ == "__main__":
    main()
