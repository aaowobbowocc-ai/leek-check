"""
MES-MNQ Pair Trading 1H Backtest — Phase B-1 (intraday-friendly)

關鍵差異 vs daily:
  - 1H bars，rolling window 240h (10 trading days)
  - Hold time < 24h 目標（避 prop firm overnight rule）
  - Timeout 48h
  - 樣本應比 daily 多 ~20x

Gate（針對 prop firm 框架）：
  - Profit Factor > 1.5
  - Max loss/trade < 2% (留 buffer 給 prop firm 3% daily limit)
  - Win rate > 55%
  - Mean net > +0.03% per trade
  - Avg hold time < 24h（intraday-friendly）

如果通過 → 進入 Phase B-2（更多 intraday 策略）
如果未過 → 路線封閉
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta
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

# 參數
ENTRY_Z = 2.5  # 嚴格 quality filter
EXIT_Z = 0.5
TIMEOUT_HOURS = 24  # 純 intraday，符合 prop firm overnight rule
ROLLING_WINDOW_HOURS = 240  # 10 trading days

COST_PER_PAIR = 3.48  # MES + MNQ round-trip
AVG_NOTIONAL = 40_000
COST_PCT = COST_PER_PAIR / AVG_NOTIONAL * 100  # ~0.0087%


def fetch_1h() -> pd.DataFrame:
    import yfinance as yf
    print("  抓取 ES=F / NQ=F 730 天 1H 資料...")
    es = yf.Ticker("ES=F").history(period="730d", interval="1h")
    nq = yf.Ticker("NQ=F").history(period="730d", interval="1h")
    if es.empty or nq.empty:
        raise RuntimeError("yfinance 1H 抓不到")
    df = pd.DataFrame({"es": es["Close"], "nq": nq["Close"]}).dropna()
    df.index = pd.to_datetime(df.index).tz_convert("UTC").tz_localize(None)
    print(f"  Aligned: {df.index[0]} ~ {df.index[-1]} ({len(df)} bars)")
    return df


def compute_spread(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["log_es"] = np.log(df["es"])
    df["log_nq"] = np.log(df["nq"])
    df["spread"] = df["log_es"] - df["log_nq"]
    df["spread_mean"] = df["spread"].rolling(ROLLING_WINDOW_HOURS).mean()
    df["spread_std"] = df["spread"].rolling(ROLLING_WINDOW_HOURS).std()
    df["z"] = (df["spread"] - df["spread_mean"]) / df["spread_std"]
    df["ret_es"] = df["log_es"].diff()
    df["ret_nq"] = df["log_nq"].diff()
    return df


def simulate(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["z"]).copy().reset_index()
    trades = []
    pos = "flat"
    entry_idx = None
    entry_spread = None
    direction = None

    for i in range(len(df)):
        row = df.iloc[i]
        z = row["z"]

        if pos == "flat":
            if z > ENTRY_Z:
                pos = "short_spread"; direction = "short"
                entry_idx = i; entry_spread = row["spread"]
            elif z < -ENTRY_Z:
                pos = "long_spread"; direction = "long"
                entry_idx = i; entry_spread = row["spread"]
            continue

        hours_held = i - entry_idx
        exit_now = abs(z) < EXIT_Z or hours_held >= TIMEOUT_HOURS

        if exit_now:
            exit_spread = row["spread"]
            if direction == "short":
                gross_pct = (entry_spread - exit_spread) * 100
            else:
                gross_pct = (exit_spread - entry_spread) * 100
            net_pct = gross_pct - COST_PCT
            entry_dt = df.iloc[entry_idx]["Datetime"]
            exit_dt = row["Datetime"]
            trades.append({
                "entry_dt": entry_dt, "exit_dt": exit_dt,
                "direction": direction,
                "entry_z": df.iloc[entry_idx]["z"],
                "exit_z": z,
                "hours_held": hours_held,
                "gross_pct": round(gross_pct, 4),
                "net_pct": round(net_pct, 4),
                "exit_reason": "z_revert" if abs(z) < EXIT_Z else "timeout",
            })
            pos = "flat"; entry_idx = None; entry_spread = None; direction = None

    return pd.DataFrame(trades)


def evaluate(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"verdict": "❌ FAIL", "reason": "no trades"}
    n = len(trades)
    wins = (trades["net_pct"] > 0).sum()
    losses = n - wins
    win_rate = wins / n * 100
    gross_wins = trades.loc[trades["net_pct"] > 0, "net_pct"].sum()
    gross_losses = abs(trades.loc[trades["net_pct"] <= 0, "net_pct"].sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    mean_net = trades["net_pct"].mean()
    cum_net = trades["net_pct"].sum()
    max_loss = trades["net_pct"].min()
    max_win = trades["net_pct"].max()
    avg_hours = trades["hours_held"].mean()
    intraday_pct = (trades["hours_held"] <= 24).sum() / n * 100
    timeout_pct = (trades["exit_reason"] == "timeout").sum() / n * 100

    cumret = trades["net_pct"].cumsum()
    rolling_max = cumret.cummax()
    max_dd = (cumret - rolling_max).min()

    # Gate
    pf_pass = pf > 1.5
    loss_pass = abs(max_loss) < 2.0  # 比 daily 嚴 (3% → 2%)
    win_pass = win_rate > 55
    mean_pass = mean_net > 0.03
    hold_pass = avg_hours < 24  # intraday-friendly
    all_pass = pf_pass and loss_pass and win_pass and mean_pass and hold_pass

    return {
        "n_trades": n, "wins": wins, "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(pf, 3),
        "mean_net_pct": round(mean_net, 4),
        "cum_net_pct": round(cum_net, 2),
        "max_loss_per_trade_pct": round(max_loss, 4),
        "max_win_per_trade_pct": round(max_win, 4),
        "max_dd_cumulative_pct": round(max_dd, 4),
        "avg_hours_held": round(avg_hours, 1),
        "intraday_pct (≤24h)": round(intraday_pct, 1),
        "timeout_pct": round(timeout_pct, 1),
        "gate_pf_15": pf_pass,
        "gate_loss_2pct": loss_pass,
        "gate_win_55": win_pass,
        "gate_mean_003": mean_pass,
        "gate_intraday": hold_pass,
        "verdict": "✅ PASS" if all_pass else "❌ FAIL",
    }


def main():
    print("=" * 70)
    print("  MES-MNQ Pair 1H Backtest — Phase B-1 (intraday-friendly)")
    print("=" * 70)

    df = fetch_1h()
    df = compute_spread(df)

    corr = df["ret_es"].corr(df["ret_nq"])
    print(f"\n  ES-NQ 1H return 相關性: {corr:.4f}")

    trades = simulate(df)
    print(f"\n  模擬完成 → {len(trades)} 筆 pair trade")

    r = evaluate(trades)
    print("\n" + "=" * 70)
    print("  📊 結果統計")
    print("=" * 70)
    for k, v in r.items():
        print(f"  {k:30s} = {v}")

    # 月度績效
    if not trades.empty:
        trades["year_month"] = pd.to_datetime(trades["exit_dt"]).dt.to_period("M")
        print("\n  📅 月度績效 (last 12 months):")
        monthly = trades.groupby("year_month").agg(
            n=("net_pct", "count"),
            wr=("net_pct", lambda s: (s > 0).mean() * 100),
            mean=("net_pct", "mean"),
            cum=("net_pct", "sum"),
        ).tail(12)
        print(monthly.to_string())

    # Hold time 分布
    if not trades.empty:
        print("\n  ⏱️ Hold time 分布:")
        bins = [0, 6, 12, 24, 36, 48]
        labels = ["0-6h", "6-12h", "12-24h", "24-36h", "36-48h"]
        trades["hold_bin"] = pd.cut(trades["hours_held"], bins=bins, labels=labels, include_lowest=True)
        bin_stats = trades.groupby("hold_bin", observed=True).agg(
            n=("net_pct", "count"),
            wr=("net_pct", lambda s: (s > 0).mean() * 100),
            mean=("net_pct", "mean"),
        )
        print(bin_stats.to_string())

    today = datetime.now().strftime("%Y%m%d")
    out_csv = OUT_DIR / f"mes_mnq_pair_1h_backtest_{today}.csv"
    trades.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ trades → {out_csv}")

    print("\n" + "=" * 70)
    print(f"  🎯 Phase B-1 Verdict: {r['verdict']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
