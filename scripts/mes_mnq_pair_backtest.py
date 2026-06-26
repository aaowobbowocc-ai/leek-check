"""
MES-MNQ Pair Trading Backtest — Phase A 期貨可行性驗證

目的：驗證 US 指數期貨 pair trading 是否有 alpha（扣摩擦後）

Pair: ES=F (S&P 500 期貨) vs NQ=F (Nasdaq-100 期貨)
  - MES 是 ES 的 micro 版（mult $5/point vs ES $50/point）
  - MNQ 是 NQ 的 micro 版（mult $2/point vs NQ $20/point）
  - 價格行為相同，只是合約 size 1/10
  - 用 ES/NQ 做 backtest，因為流動性更好、資料更完整

策略邏輯（與 TW DRAM 2408-2344 +3.16%/筆對應）：
  - log_spread = log(ES) - log(NQ)
  - 60d rolling mean + std → z-score
  - 進場：|z| > 2.5
  - 出場：|z| < 0.5 OR timeout 20 個交易日
  - 持倉：MES 1 contract + MNQ 1 contract（簡化，beta-neutral 暫不調）

摩擦成本（Topstep）：
  - MES: $0.74/round-trip + $1 滑點 ≈ $1.74
  - MNQ: $0.74/round-trip + $1 滑點 ≈ $1.74
  - 雙邊總計: ~$3.48 per pair trade

Notional 計算：
  - MES @ 6000 × $5 = $30,000
  - MNQ @ 25000 × $2 = $50,000
  - 平均約 $40,000

Gate（Phase A 通過條件）：
  - Profit Factor > 1.5
  - Max Drawdown (per trade) < 3% notional
  - Win rate > 55%
  - Mean net return > +0.05% per trade（after $3.48 cost）

Output:
  scripts/output/mes_mnq_pair_backtest_YYYYMMDD.csv
"""
from __future__ import annotations

import io
import sys
from datetime import datetime
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
ENTRY_Z = 2.0  # 從 2.5 放寬到 2.0
EXIT_Z = 0.5
TIMEOUT_DAYS = 10  # 從 20 縮短到 10，避開「持久不收斂」損失
ROLLING_WINDOW = 60

# 摩擦成本（USD per pair round-trip）
COST_PER_PAIR = 3.48  # MES + MNQ 雙邊
# 假設平均 notional $40,000
AVG_NOTIONAL = 40_000
COST_PCT = COST_PER_PAIR / AVG_NOTIONAL * 100  # ≈ 0.0087%


def fetch_data(years: int = 5) -> pd.DataFrame:
    """抓 ES=F 和 NQ=F daily 資料"""
    import yfinance as yf

    print(f"  抓取 ES=F 和 NQ=F 過去 {years} 年 daily 資料...")
    es = yf.Ticker("ES=F").history(period=f"{years}y", auto_adjust=False)
    nq = yf.Ticker("NQ=F").history(period=f"{years}y", auto_adjust=False)

    if es.empty or nq.empty:
        raise RuntimeError("yfinance 抓不到 ES=F 或 NQ=F")

    df = pd.DataFrame({
        "es": es["Close"],
        "nq": nq["Close"],
    }).dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    print(f"  ES range: {es.index[0].date()} ~ {es.index[-1].date()} ({len(es)} bars)")
    print(f"  NQ range: {nq.index[0].date()} ~ {nq.index[-1].date()} ({len(nq)} bars)")
    print(f"  Aligned: {df.index[0].date()} ~ {df.index[-1].date()} ({len(df)} bars)")
    return df


def compute_spread(df: pd.DataFrame) -> pd.DataFrame:
    """計算 log spread + rolling z-score"""
    df = df.copy()
    df["log_es"] = np.log(df["es"])
    df["log_nq"] = np.log(df["nq"])
    df["spread"] = df["log_es"] - df["log_nq"]
    df["spread_mean"] = df["spread"].rolling(ROLLING_WINDOW).mean()
    df["spread_std"] = df["spread"].rolling(ROLLING_WINDOW).std()
    df["z"] = (df["spread"] - df["spread_mean"]) / df["spread_std"]

    # Correlation（整段 log return）
    df["ret_es"] = df["log_es"].diff()
    df["ret_nq"] = df["log_nq"].diff()
    return df


def simulate_trades(df: pd.DataFrame) -> pd.DataFrame:
    """
    模擬 pair trading

    State: position = "flat" | "short_spread" | "long_spread"
      - short_spread: z > +2.5 → short ES, long NQ（賭 spread 收斂）
      - long_spread:  z < -2.5 → long ES, short NQ
      - 出場：|z| < 0.5 或 timeout

    P&L per pair trade:
      - short_spread P&L = entry_spread - exit_spread
      - long_spread  P&L = exit_spread - entry_spread
      - 都用 log return（百分比）
    """
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
                pos = "short_spread"
                direction = "short"
                entry_idx = i
                entry_spread = row["spread"]
            elif z < -ENTRY_Z:
                pos = "long_spread"
                direction = "long"
                entry_idx = i
                entry_spread = row["spread"]
            continue

        # 已有部位 → 看是否出場
        days_held = i - entry_idx
        exit_now = abs(z) < EXIT_Z or days_held >= TIMEOUT_DAYS

        if exit_now:
            exit_spread = row["spread"]
            if direction == "short":
                gross_pct = (entry_spread - exit_spread) * 100  # log return %
            else:
                gross_pct = (exit_spread - entry_spread) * 100

            net_pct = gross_pct - COST_PCT
            trades.append({
                "entry_date": df.iloc[entry_idx]["Date"],
                "exit_date": row["Date"],
                "direction": direction,
                "entry_z": df.iloc[entry_idx]["z"],
                "exit_z": z,
                "entry_spread": entry_spread,
                "exit_spread": exit_spread,
                "days_held": days_held,
                "gross_pct": round(gross_pct, 4),
                "net_pct": round(net_pct, 4),
                "exit_reason": "z_revert" if abs(z) < EXIT_Z else "timeout",
            })
            pos = "flat"
            entry_idx = None
            entry_spread = None
            direction = None

    return pd.DataFrame(trades)


def evaluate(trades: pd.DataFrame) -> dict:
    """計算 Phase A gate 指標"""
    if trades.empty:
        return {"verdict": "❌ FAIL", "reason": "No trades triggered"}

    n = len(trades)
    wins = (trades["net_pct"] > 0).sum()
    losses = (trades["net_pct"] <= 0).sum()
    win_rate = wins / n * 100

    gross_wins = trades.loc[trades["net_pct"] > 0, "net_pct"].sum()
    gross_losses = abs(trades.loc[trades["net_pct"] <= 0, "net_pct"].sum())
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    mean_net = trades["net_pct"].mean()
    median_net = trades["net_pct"].median()
    cum_net = trades["net_pct"].sum()

    max_loss_per_trade = trades["net_pct"].min()
    max_win_per_trade = trades["net_pct"].max()

    avg_days = trades["days_held"].mean()
    timeout_pct = (trades["exit_reason"] == "timeout").sum() / n * 100

    # Drawdown（cumulative P&L 序列）
    cumret = trades["net_pct"].cumsum()
    rolling_max = cumret.cummax()
    drawdown = cumret - rolling_max
    max_dd_overall = drawdown.min()

    # Phase A Gate
    pf_pass = profit_factor > 1.5
    dd_pass = abs(max_loss_per_trade) < 3.0  # per trade
    win_pass = win_rate > 55
    mean_pass = mean_net > 0.05

    all_pass = pf_pass and dd_pass and win_pass and mean_pass
    verdict = "✅ PASS" if all_pass else "❌ FAIL"

    return {
        "n_trades": n,
        "wins": wins, "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 3),
        "mean_net_pct": round(mean_net, 4),
        "median_net_pct": round(median_net, 4),
        "cum_net_pct": round(cum_net, 2),
        "max_loss_per_trade_pct": round(max_loss_per_trade, 4),
        "max_win_per_trade_pct": round(max_win_per_trade, 4),
        "max_dd_cumulative_pct": round(max_dd_overall, 4),
        "avg_days_held": round(avg_days, 1),
        "timeout_pct": round(timeout_pct, 1),
        "gate_pf_15": pf_pass,
        "gate_dd_3pct": dd_pass,
        "gate_win_55": win_pass,
        "gate_mean_005": mean_pass,
        "verdict": verdict,
    }


def main():
    print("=" * 70)
    print("  MES-MNQ Pair Trading Backtest — Phase A 期貨可行性驗證")
    print("=" * 70)

    df = fetch_data(years=5)
    df = compute_spread(df)

    corr = df["ret_es"].corr(df["ret_nq"])
    print(f"\n  ES-NQ daily return 相關性: {corr:.4f}")

    trades = simulate_trades(df)
    print(f"\n  模擬完成 → {len(trades)} 筆 pair trade")

    results = evaluate(trades)

    print("\n" + "=" * 70)
    print("  📊 結果統計")
    print("=" * 70)
    for k, v in results.items():
        print(f"  {k:30s} = {v}")

    # 按年度分組統計
    if not trades.empty:
        trades["year"] = pd.to_datetime(trades["exit_date"]).dt.year
        print("\n  📅 年度績效:")
        print(f"  {'Year':<6} {'n':>4} {'win%':>7} {'mean':>8} {'cum':>9}")
        for yr, grp in trades.groupby("year"):
            wr = (grp["net_pct"] > 0).sum() / len(grp) * 100
            print(f"  {yr:<6} {len(grp):>4} {wr:>6.1f}% {grp['net_pct'].mean():>+7.3f}% {grp['net_pct'].sum():>+8.2f}%")

    # 寫入 CSV
    today = datetime.now().strftime("%Y%m%d")
    out_csv = OUT_DIR / f"mes_mnq_pair_backtest_{today}.csv"
    trades.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ trades 寫入 {out_csv}")

    # 結論
    print("\n" + "=" * 70)
    print(f"  🎯 Phase A Verdict: {results['verdict']}")
    print("=" * 70)
    if results["verdict"] == "✅ PASS":
        print("  → 進入 Phase B（demo 帳戶 6 個月驗證）")
    else:
        failed_gates = []
        if not results.get("gate_pf_15"): failed_gates.append(f"PF {results['profit_factor']} < 1.5")
        if not results.get("gate_dd_3pct"): failed_gates.append(f"max loss {results['max_loss_per_trade_pct']:.2f}% > 3%")
        if not results.get("gate_win_55"): failed_gates.append(f"win {results['win_rate_pct']:.1f}% < 55%")
        if not results.get("gate_mean_005"): failed_gates.append(f"mean {results['mean_net_pct']:.4f}% < 0.05%")
        print(f"  → 失敗原因: {'; '.join(failed_gates)}")


if __name__ == "__main__":
    main()
