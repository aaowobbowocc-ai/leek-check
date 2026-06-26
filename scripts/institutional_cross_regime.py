"""
法人 momentum / contrarian 跨牛熊 9 年驗證 (2017-2026)

對 top 候選跑 4 個時段 alpha：
  A 2017-2019 (混合)
  B 2020 covid
  C 2021-2022 (2022 熊 -22%)
  D 2023-2026 (大牛市)
"""
from __future__ import annotations
import io, sys
from datetime import date
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

PERIODS = [
    ("A 2017-2019", date(2017, 1, 1), date(2019, 12, 31)),
    ("B 2020 covid", date(2020, 1, 1), date(2020, 12, 31)),
    ("C 2021-2022", date(2021, 1, 1), date(2022, 12, 31)),
    ("D 2023-2026", date(2023, 1, 1), date(2026, 4, 30)),
]


def load_ohlcv(tk):
    p = CACHE_YF / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def signal_dates(inst_df, name_col, n_consec, direction):
    pivot = inst_df.pivot_table(index="date", columns="name", values="net_buy",
                                 aggfunc="sum").reset_index()
    pivot.columns.name = None
    pivot = pivot.sort_values("date").reset_index(drop=True)
    if name_col not in pivot.columns:
        return []
    if direction == "buy":
        pivot["match"] = pivot[name_col] > 0
    else:
        pivot["match"] = pivot[name_col] < 0
    pivot["consec"] = pivot["match"].astype(int).rolling(n_consec).sum()
    return pivot[pivot["consec"] == n_consec]["date"].tolist()


def returns_in_period(ohlcv, sig_dates, hold, start, end):
    o_dates = list(ohlcv["date"])
    rets = []
    for d in sig_dates:
        if not (start <= d <= end):
            continue
        if d not in o_dates:
            continue
        idx = o_dates.index(d)
        if idx + 1 + hold >= len(o_dates):
            continue
        entry = float(ohlcv.iloc[idx + 1]["open"])
        exit_p = float(ohlcv.iloc[idx + 1 + hold]["close"])
        rets.append((exit_p / entry - 1) * 100)
    return rets


def random_in_period(ohlcv, hold, start, end):
    rets = []
    for i in range(len(ohlcv) - hold - 2):
        d = ohlcv.iloc[i]["date"]
        if not (start <= d <= end):
            continue
        entry = float(ohlcv.iloc[i + 1]["open"])
        exit_p = float(ohlcv.iloc[i + 1 + hold]["close"])
        rets.append((exit_p / entry - 1) * 100)
    return np.array(rets)


def main():
    cases = [
        # (ticker, investor, n_consec, hold, direction, label)
        ("2308", "foreign", 3, 20, "buy",  "2308 外資連買 3d/20d (momentum)"),
        ("2308", "investment_trust", 3, 10, "sell", "2308 投信連賣 3d/10d (contrarian)"),
        ("0050", "dealer", 3, 20, "buy",  "0050 自營商連買 3d/20d (momentum)"),
        ("0050", "investment_trust", 3, 10, "sell", "0050 投信連賣 3d/10d (contrarian)"),
        ("006208", "foreign", 3, 20, "buy", "006208 外資連買 3d/20d"),
        ("00881", "foreign", 3, 20, "buy", "00881 外資連買 3d/20d"),
        ("00881", "foreign", 5, 10, "sell", "00881 外資連賣 5d/10d (contrarian)"),
        ("2330", "investment_trust", 3, 5, "sell", "2330 投信連賣 3d/5d (contrarian)"),
    ]

    print("=" * 120)
    print("法人訊號跨牛熊 9 年驗證 (2017-2026)")
    print("=" * 120)

    for tk, investor, nc, hold, direction, label in cases:
        ohlcv = load_ohlcv(tk)
        inst_p = CACHE_INST / f"{tk}.parquet"
        if not inst_p.exists() or ohlcv.empty:
            print(f"\n❌ {tk} 資料不全"); continue

        inst = pd.read_parquet(inst_p)
        inst["date"] = pd.to_datetime(inst["date"]).dt.date
        sig_d = signal_dates(inst, NAME_MAP[investor], nc, direction)

        print(f"\n=== {label} (全期 {len(sig_d)} 訊號) ===")
        print(f"{'period':<18} {'n_sig':>5} {'sig':>8} {'rand':>8} {'alpha':>8} "
              f"{'sigma':>7} {'verdict':>10}")

        all_periods_alpha = []
        for p_label, p_start, p_end in PERIODS:
            sig_rets = returns_in_period(ohlcv, sig_d, hold, p_start, p_end)
            rand_rets = random_in_period(ohlcv, hold, p_start, p_end)
            if len(sig_rets) < 3 or len(rand_rets) < 30:
                print(f"  {p_label:<16}  sample 不足 (sig={len(sig_rets)})")
                continue
            sig_mean = np.mean(sig_rets)
            rand_mean = rand_rets.mean()
            rand_std = rand_rets.std()
            alpha = sig_mean - rand_mean
            sigma = (alpha / (rand_std / np.sqrt(len(sig_rets)))) if rand_std > 0 else 0

            v = "✅ robust" if sigma > 1.96 and alpha > 0 else (
                "⚠️ 弱" if alpha > 0 else "❌ 假")
            all_periods_alpha.append(alpha)
            print(f"  {p_label:<16} {len(sig_rets):>5} {sig_mean:>+6.2f}% "
                  f"{rand_mean:>+6.2f}% {alpha:>+6.2f}% {sigma:>+6.2f} {v:>10}")

        # 結論
        if len(all_periods_alpha) >= 3:
            n_pos = sum(1 for a in all_periods_alpha if a > 0)
            print(f"  → {n_pos}/{len(all_periods_alpha)} 期 alpha > 0  "
                  f"(平均 {np.mean(all_periods_alpha):+.2f}%)")


if __name__ == "__main__":
    main()
