"""
Sector rotation pair backtest: 0050 (semis-heavy) vs 0055 (financials).

Hypothesis: 0050/0055 ratio mean-reverts when |z-score| > 2.5.
- ratio_z > +2.5: short 0050, long 0055 (semis overheat)
- ratio_z < -2.5: long 0050, short 0055 (financials overheat)

4 variants:
  A) Naive 60d z-score
  B) 90d z-score
  C) 60d z + regime filter (only trade when 0050 > 200MA)
  D) ETF-hedged (replace short 0050 with long 00632R, IPO 2014-10)

Validation gates per variant:
  - n >= 30
  - MCPT p < 0.05
  - OOS split 2014-2019 vs 2020-2025
  - mean_net > 0.5% per trade after 0.34% x 2 = 0.68% combined cost

Cost model: 0.34% one-way per leg = 0.68% round-trip combined for the pair.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("c:/Users/USER/Desktop/INVEST")
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ---- cost: 0.34% one-way (TW ETF: 0.1425% fee + 0.1% sec tax + slip ~0.1%) ----
COST_ONEWAY = 0.0034  # one leg one-way
# pair has two legs; round-trip = enter+exit on each leg = 4 x 0.34% = 1.36% total
# But the spec says 0.34% x 2 = 0.68% combined cost. We follow spec: treat as 0.68%
# (this matches the user's "ETF round-trip ~0.34%" memory note for INVEST cost basis).
COMBINED_COST = 0.0068


def load_prices() -> pd.DataFrame:
    df50 = pd.read_parquet(ROOT / "data/cache/yfinance/global/0050_TW.parquet")
    df50["date"] = pd.to_datetime(df50["date"])
    df50 = df50.set_index("date")[["close"]].rename(columns={"close": "p50"})

    # 0055 is multi-index columns
    df55_raw = pd.read_parquet(ROOT / "data/cache/yfinance/tw_ohlcv/0055.parquet")
    if isinstance(df55_raw.columns, pd.MultiIndex):
        df55_raw.columns = [c[0] for c in df55_raw.columns]
    df55 = df55_raw[["Close"]].rename(columns={"Close": "p55"})
    df55.index = pd.to_datetime(df55.index)

    df632 = None
    p = ROOT / "data/cache/yfinance/tw_ohlcv/00632R.parquet"
    if p.exists():
        d = pd.read_parquet(p)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = [c[0] for c in d.columns]
        df632 = d[["Close", "Volume"]].rename(columns={"Close": "p632", "Volume": "v632"})
        df632.index = pd.to_datetime(df632.index)
        # 00632R was delisted ~2024-11-29 (NAV too low, TW inverse ETF auto-liquidate);
        # yfinance shows a bogus 6.8x jump 2024-12-02 with 0 volume thereafter. Cut.
        df632 = df632[df632.index <= "2024-11-29"]
        # also drop any remaining rows where volume == 0 (stale price)
        df632 = df632[df632["v632"] > 0]
        df632 = df632[["p632"]]

    out = df50.join(df55, how="inner")
    if df632 is not None:
        out = out.join(df632, how="left")
    out = out.dropna(subset=["p50", "p55"])
    out["ratio"] = out["p50"] / out["p55"]
    out["ma200_50"] = out["p50"].rolling(200).mean()
    return out


def gen_signals(df: pd.DataFrame, window: int, z_threshold: float = 2.5) -> pd.Series:
    mu = df["ratio"].rolling(window).mean()
    sd = df["ratio"].rolling(window).std()
    z = (df["ratio"] - mu) / sd
    return z


def simulate_pair(
    df: pd.DataFrame,
    z_window: int,
    use_regime: bool = False,
    use_etf_hedge: bool = False,
    z_entry: float = 2.5,
    z_exit: float = 0.0,
    max_days: int = 30,
):
    """Simulate one variant. Returns list of trade dicts."""
    z = gen_signals(df, z_window, z_entry)
    df = df.copy()
    df["z"] = z

    trades = []
    in_pos = False
    entry_idx = None
    entry_data = None
    direction = 0  # +1 = long 0050 / short 0055; -1 = short 0050 / long 0055

    idx_list = df.index.tolist()
    for i in range(len(df)):
        row = df.iloc[i]
        if pd.isna(row["z"]):
            continue
        if use_regime and pd.isna(row["ma200_50"]):
            continue
        if use_etf_hedge and pd.isna(row.get("p632", np.nan)):
            # ETF hedge requires 00632R available
            if in_pos:
                pass
            else:
                continue

        if not in_pos:
            entered = False
            if row["z"] >= z_entry:
                # short 0050, long 0055
                if use_regime and row["p50"] < row["ma200_50"]:
                    pass  # skip
                else:
                    direction = -1
                    entered = True
            elif row["z"] <= -z_entry:
                if use_regime and row["p50"] < row["ma200_50"]:
                    pass
                else:
                    direction = +1
                    entered = True
            if entered:
                # ETF hedge: only available if 00632R exists in row
                if use_etf_hedge and (
                    "p632" not in row or pd.isna(row["p632"])
                ):
                    continue
                in_pos = True
                entry_idx = i
                entry_data = {
                    "entry_date": idx_list[i],
                    "entry_p50": row["p50"],
                    "entry_p55": row["p55"],
                    "entry_p632": row.get("p632", np.nan),
                    "entry_z": row["z"],
                    "direction": direction,
                }
        else:
            held = i - entry_idx
            exit_now = False
            reason = ""
            # exit when z crosses zero in correct direction
            if direction == -1 and row["z"] <= z_exit:
                exit_now = True
                reason = "z_revert"
            elif direction == +1 and row["z"] >= -z_exit:
                exit_now = True
                reason = "z_revert"
            elif held >= max_days:
                exit_now = True
                reason = "time_stop"

            if exit_now:
                # P&L computation
                # 0050 leg return
                ret50 = (row["p50"] - entry_data["entry_p50"]) / entry_data["entry_p50"]
                ret55 = (row["p55"] - entry_data["entry_p55"]) / entry_data["entry_p55"]

                if use_etf_hedge and direction == -1:
                    # replace short 0050 with long 00632R (inverse ETF)
                    if pd.notna(entry_data["entry_p632"]) and pd.notna(row.get("p632", np.nan)):
                        ret_short_leg = (row["p632"] - entry_data["entry_p632"]) / entry_data["entry_p632"]
                    else:
                        # fallback to actual short
                        ret_short_leg = -ret50
                    long_leg = ret55  # long 0055
                    pnl = long_leg + ret_short_leg
                elif use_etf_hedge and direction == +1:
                    # short 0055 still required (no inverse 0055 ETF) -> normal pair
                    pnl = ret50 + (-ret55)
                else:
                    if direction == +1:
                        pnl = ret50 + (-ret55)
                    else:
                        pnl = (-ret50) + ret55

                pnl_net = pnl - COMBINED_COST
                trades.append({
                    "entry_date": entry_data["entry_date"],
                    "exit_date": idx_list[i],
                    "direction": direction,
                    "entry_z": entry_data["entry_z"],
                    "exit_z": row["z"],
                    "days": held,
                    "ret50": ret50,
                    "ret55": ret55,
                    "pnl_gross": pnl,
                    "pnl_net": pnl_net,
                    "exit_reason": reason,
                })
                in_pos = False
                entry_idx = None
                entry_data = None
    return pd.DataFrame(trades)


def mcpt(trades: pd.DataFrame, df: pd.DataFrame, n_sims: int = 500, seed: int = 7) -> float:
    """Monte Carlo permutation test: shuffle entry dates, recompute mean pnl_net.

    H0: pair-trade alpha = random direction at random dates produces same mean.
    """
    if trades.empty:
        return 1.0
    rng = np.random.default_rng(seed)
    actual = trades["pnl_net"].mean()
    # Build pool: random pair of dates and random direction, hold avg(days)
    avg_days = int(round(trades["days"].mean()))
    valid = df.dropna(subset=["p50", "p55"]).copy()
    n = len(trades)
    sims = []
    valid_idx = valid.index
    for _ in range(n_sims):
        sample_starts = rng.choice(len(valid_idx) - avg_days - 2, size=n, replace=True)
        dirs = rng.choice([-1, +1], size=n)
        means = []
        for s, d in zip(sample_starts, dirs):
            p50_e = valid.iloc[s]["p50"]
            p50_x = valid.iloc[s + avg_days]["p50"]
            p55_e = valid.iloc[s]["p55"]
            p55_x = valid.iloc[s + avg_days]["p55"]
            r50 = (p50_x - p50_e) / p50_e
            r55 = (p55_x - p55_e) / p55_e
            if d == +1:
                pnl = r50 - r55
            else:
                pnl = -r50 + r55
            means.append(pnl - COMBINED_COST)
        sims.append(np.mean(means))
    sims = np.array(sims)
    p = (np.sum(sims >= actual) + 1) / (n_sims + 1)
    return float(p)


def summarize(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0}
    return {
        "n": int(len(trades)),
        "mean_net": float(trades["pnl_net"].mean()),
        "median_net": float(trades["pnl_net"].median()),
        "win_rate": float((trades["pnl_net"] > 0).mean()),
        "std_net": float(trades["pnl_net"].std()),
        "sharpe_per_trade": float(trades["pnl_net"].mean() / trades["pnl_net"].std()) if trades["pnl_net"].std() > 0 else np.nan,
        "best": float(trades["pnl_net"].max()),
        "worst": float(trades["pnl_net"].min()),
        "avg_days": float(trades["days"].mean()),
        "cum_net": float(trades["pnl_net"].sum()),
    }


def oos_split(trades: pd.DataFrame, cut: str = "2020-01-01") -> dict:
    if trades.empty:
        return {"is_n": 0, "oos_n": 0}
    cut_d = pd.Timestamp(cut)
    is_t = trades[trades["entry_date"] < cut_d]
    oos_t = trades[trades["entry_date"] >= cut_d]
    return {
        "is_n": len(is_t),
        "is_mean_net": float(is_t["pnl_net"].mean()) if len(is_t) else np.nan,
        "is_winrate": float((is_t["pnl_net"] > 0).mean()) if len(is_t) else np.nan,
        "oos_n": len(oos_t),
        "oos_mean_net": float(oos_t["pnl_net"].mean()) if len(oos_t) else np.nan,
        "oos_winrate": float((oos_t["pnl_net"] > 0).mean()) if len(oos_t) else np.nan,
    }


def run():
    df = load_prices()
    print(f"data range {df.index[0].date()} -> {df.index[-1].date()}, n={len(df)}")
    print(f"00632R available from: {df['p632'].first_valid_index() if 'p632' in df.columns else 'N/A'}")
    print()

    variants = {
        "A_naive_60d": dict(z_window=60, use_regime=False, use_etf_hedge=False),
        "B_90d":       dict(z_window=90, use_regime=False, use_etf_hedge=False),
        "C_regime":    dict(z_window=60, use_regime=True,  use_etf_hedge=False),
        "D_etf_hedge": dict(z_window=60, use_regime=False, use_etf_hedge=True),
    }

    rows = []
    all_trades = {}
    for name, kw in variants.items():
        # constrain ETF hedge variant to start when 00632R exists
        sub = df.copy()
        if kw["use_etf_hedge"]:
            first = sub["p632"].first_valid_index()
            if first is not None:
                sub = sub.loc[first:]
        trades = simulate_pair(sub, **kw)
        all_trades[name] = trades
        s = summarize(trades)
        oos = oos_split(trades)
        p = mcpt(trades, sub) if len(trades) >= 5 else 1.0
        row = {"variant": name, **s, "mcpt_p": p, **oos}
        # gate flags
        gate_n = s.get("n", 0) >= 30
        gate_p = p < 0.05
        gate_mean = s.get("mean_net", -1) > 0.005
        gate_oos = (
            (oos.get("is_mean_net", -1) or -1) > 0
            and (oos.get("oos_mean_net", -1) or -1) > 0
        )
        row["gate_n>=30"] = gate_n
        row["gate_mcpt_p<0.05"] = gate_p
        row["gate_mean>0.5%"] = gate_mean
        row["gate_oos_both_pos"] = gate_oos
        row["gate_all_pass"] = all([gate_n, gate_p, gate_mean, gate_oos])
        rows.append(row)
        print(f"{name}: n={s.get('n',0)}, mean_net={s.get('mean_net', float('nan')):.4f}, "
              f"win={s.get('win_rate', float('nan')):.2%}, mcpt_p={p:.3f}, "
              f"is_n={oos['is_n']} oos_n={oos['oos_n']}")

    out = pd.DataFrame(rows)
    out_path = LOG_DIR / "sector_rotation_pair.csv"
    out.to_csv(out_path, index=False)
    print(f"\nSaved -> {out_path}")

    # also save trade detail for best variant
    for name, t in all_trades.items():
        if not t.empty:
            t.to_csv(LOG_DIR / f"sector_rotation_pair_trades_{name}.csv", index=False)

    return out, all_trades


if __name__ == "__main__":
    run()
