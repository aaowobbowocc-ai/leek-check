"""
真 alpha 驗證 v2：訊號日 vs 同 ticker 隨機進場（不是 vs 0050）

針對 2308 / 00881 等候選，檢查：
  signal_window_excess vs random_same_ticker_window
  差距才是真的 event-driven alpha
"""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_INST = ROOT / "data" / "cache" / "finmind" / "institutional"
CUTOFF = pd.Timestamp("2025-06-01")
SEED = 42
N_BOOT = 2000

NAME_MAP = {
    "foreign": "Foreign_Investor",
    "investment_trust": "Investment_Trust",
    "dealer": "Dealer_self",
}


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def get_signal_dates(inst_df: pd.DataFrame, investor: str, n_consec: int) -> list:
    """回傳法人連 N 日買超的訊號日。"""
    pivot = inst_df.pivot_table(
        index="date", columns="name", values="net_buy", aggfunc="sum"
    ).reset_index()
    pivot.columns.name = None
    pivot = pivot.sort_values("date").reset_index(drop=True)
    col = NAME_MAP.get(investor)
    if col not in pivot.columns:
        return []
    pivot["is_buy"] = pivot[col] > 0
    pivot["consec"] = pivot["is_buy"].astype(int).rolling(n_consec).sum()
    pivot["trigger"] = pivot["consec"] == n_consec
    return pivot[pivot["trigger"]]["date"].tolist()


def compute_window_returns(ohlcv: pd.DataFrame, entry_dates: list, hold_days: int) -> list:
    """對每個 entry_date 算 hold_days 後的 return."""
    o = ohlcv.set_index("date").sort_index()
    o_dates = list(o.index)
    rets = []
    for d in entry_dates:
        if d not in o_dates:
            continue
        idx = o_dates.index(d)
        # entry = next day open
        entry_idx = idx + 1
        exit_idx = entry_idx + hold_days
        if entry_idx >= len(o_dates) or exit_idx >= len(o_dates):
            continue
        entry = float(o.iloc[entry_idx]["open"])
        exit_p = float(o.iloc[exit_idx]["close"])
        rets.append((exit_p / entry - 1) * 100)
    return rets


def compute_random_window_returns(ohlcv: pd.DataFrame, hold_days: int) -> np.ndarray:
    """所有 hold_days 視窗的 returns（buy at any open, sell hold_days later close）"""
    o = ohlcv.sort_values("date").reset_index(drop=True)
    rets = []
    for i in range(len(o) - hold_days - 1):
        entry = float(o.iloc[i + 1]["open"])
        exit_p = float(o.iloc[i + 1 + hold_days]["close"])
        rets.append((exit_p / entry - 1) * 100)
    return np.array(rets)


def main():
    cases = [
        ("2308", "foreign", 3, 20),
        ("2308", "foreign", 3, 10),
        ("2308", "investment_trust", 3, 20),
        ("00881", "foreign", 7, 20),
        ("00881", "foreign", 5, 20),
        ("00881", "foreign", 3, 20),
        ("006208", "foreign", 3, 20),
        ("0050", "dealer", 3, 20),
        ("7750", "foreign", 3, 20),
    ]

    print("=" * 110)
    print("真 Alpha 驗證 v2：訊號日 vs 同 ticker 隨機進場")
    print("=" * 110)
    print(f"\n{'ticker':<8} {'investor':<18} {'consec':>4} {'hold':>4} "
          f"{'sig_n':>5} {'sig_mean':>9} {'rand_mean':>9} "
          f"{'true_alpha':>11} {'rand_std':>9} {'sigma':>7}")
    print("-" * 110)

    rng = np.random.default_rng(SEED)
    for tk, investor, n_consec, hold in cases:
        ohlcv = load_ohlcv(tk)
        inst_p = CACHE_INST / f"{tk}.parquet"
        if not inst_p.exists():
            print(f"  {tk}: 無 institutional cache"); continue

        inst = pd.read_parquet(inst_p)
        inst["date"] = pd.to_datetime(inst["date"]).dt.date

        sig_dates = get_signal_dates(inst, investor, n_consec)
        sig_rets = compute_window_returns(ohlcv, sig_dates, hold)
        rand_rets = compute_random_window_returns(ohlcv, hold)

        if not sig_rets or len(rand_rets) == 0:
            continue

        sig_mean = np.mean(sig_rets)
        rand_mean = rand_rets.mean()
        rand_std = rand_rets.std()
        true_alpha = sig_mean - rand_mean
        sigma = (true_alpha / (rand_std / np.sqrt(len(sig_rets)))) if rand_std > 0 else np.nan

        # significance: sigma > 1.96 = 95% confidence (one-sided > 1.65)
        marker = "⭐" if sigma > 1.96 else ("⚠️" if sigma > 1.0 else "❌")
        print(f"  {tk:<8} {investor:<18} {n_consec:>3}d {hold:>3}d "
              f"{len(sig_rets):>5} {sig_mean:>+7.2f}% {rand_mean:>+7.2f}% "
              f"{true_alpha:>+9.2f}% {rand_std:>7.2f}% {sigma:>+6.2f} {marker}")


if __name__ == "__main__":
    main()
