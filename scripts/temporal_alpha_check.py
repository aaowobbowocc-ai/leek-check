"""
時間分層驗證：訊號是否集中在牛市？
對 top contrarian 訊號 split 2017-2022 vs 2023-2026 看 alpha 是否持續。
"""
from __future__ import annotations
import sys, io
from pathlib import Path
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_INST = ROOT / "data" / "cache" / "finmind" / "institutional"

NAME_MAP = {"foreign": "Foreign_Investor",
            "investment_trust": "Investment_Trust",
            "dealer": "Dealer_self"}


def load_ohlcv(tk):
    p = CACHE_YF / f"{tk}.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def signal_dates(inst_df, name_col, n_consec, is_sell=True):
    pivot = inst_df.pivot_table(index="date", columns="name", values="net_buy",
                                 aggfunc="sum").reset_index()
    pivot.columns.name = None
    pivot = pivot.sort_values("date").reset_index(drop=True)
    if name_col not in pivot.columns:
        return []
    if is_sell:
        pivot["match"] = pivot[name_col] < 0
    else:
        pivot["match"] = pivot[name_col] > 0
    pivot["consec"] = pivot["match"].astype(int).rolling(n_consec).sum()
    return pivot[pivot["consec"] == n_consec]["date"].tolist()


def split_period_returns(ohlcv, sig_dates, hold, cutoff_date):
    """分前後段算 mean return"""
    o_dates = list(ohlcv["date"])
    early, late = [], []
    for d in sig_dates:
        if d not in o_dates:
            continue
        idx = o_dates.index(d)
        if idx + 1 + hold >= len(o_dates):
            continue
        entry = float(ohlcv.iloc[idx + 1]["open"])
        exit_p = float(ohlcv.iloc[idx + 1 + hold]["close"])
        ret = (exit_p / entry - 1) * 100
        if d < cutoff_date:
            early.append(ret)
        else:
            late.append(ret)
    return early, late


def random_window_split(ohlcv, hold, cutoff_date):
    early, late = [], []
    for i in range(len(ohlcv) - hold - 2):
        d = ohlcv.iloc[i]["date"]
        entry = float(ohlcv.iloc[i + 1]["open"])
        exit_p = float(ohlcv.iloc[i + 1 + hold]["close"])
        ret = (exit_p / entry - 1) * 100
        if d < cutoff_date:
            early.append(ret)
        else:
            late.append(ret)
    return np.array(early), np.array(late)


def main():
    from datetime import date
    cutoff = date(2023, 1, 1)

    cases = [
        ("2317", "investment_trust", 3, 10, "投信連賣 3d / 10d"),
        ("2308", "investment_trust", 3, 10, "投信連賣 3d / 10d"),
        ("00881", "foreign", 5, 10, "外資連賣 5d / 10d"),
        ("00881", "foreign", 3, 10, "外資連賣 3d / 10d"),
        ("0050", "investment_trust", 3, 10, "投信連賣 3d / 10d"),
        ("2330", "investment_trust", 3, 5, "投信連賣 3d / 5d"),
    ]

    print("=" * 110)
    print(f"時間分層驗證 (cutoff: {cutoff})")
    print(f"早期 = 2017-2022 (含 2018-2019 多空、2020 covid 跌)")
    print(f"晚期 = 2023-2026 (大牛市)")
    print("=" * 110)
    print(f"\n{'ticker':<8} {'規則':<22} "
          f"{'sig 早':>10} {'sig 晚':>10} {'rand 早':>10} {'rand 晚':>10} "
          f"{'alpha 早':>10} {'alpha 晚':>10} {'判定':>10}")
    print("-" * 110)

    for tk, investor, nc, hold, label in cases:
        ohlcv = load_ohlcv(tk)
        inst_p = CACHE_INST / f"{tk}.parquet"
        if not inst_p.exists():
            continue
        inst = pd.read_parquet(inst_p)
        inst["date"] = pd.to_datetime(inst["date"]).dt.date

        name_col = NAME_MAP[investor]
        sig_d = signal_dates(inst, name_col, nc, is_sell=True)
        sig_early, sig_late = split_period_returns(ohlcv, sig_d, hold, cutoff)
        rand_early, rand_late = random_window_split(ohlcv, hold, cutoff)

        if not sig_early or not sig_late:
            print(f"  {tk:<7} {label:<22}  早或晚期無樣本")
            continue

        sig_e = np.mean(sig_early)
        sig_l = np.mean(sig_late)
        rand_e = rand_early.mean()
        rand_l = rand_late.mean()
        alpha_e = sig_e - rand_e
        alpha_l = sig_l - rand_l

        # 判定：兩期都正 = robust
        if alpha_e > 0 and alpha_l > 0:
            verdict = "✅ robust"
        elif alpha_l > 0 and alpha_e <= 0:
            verdict = "⚠️ regime"
        elif alpha_e > 0 and alpha_l <= 0:
            verdict = "⚠️ 已失效"
        else:
            verdict = "❌ 假"

        print(f"  {tk:<7} {label:<22} "
              f"{sig_e:>+8.2f}%({len(sig_early)}) {sig_l:>+8.2f}%({len(sig_late)}) "
              f"{rand_e:>+9.2f}% {rand_l:>+9.2f}% "
              f"{alpha_e:>+8.2f}% {alpha_l:>+8.2f}% {verdict:>10}")


if __name__ == "__main__":
    main()
