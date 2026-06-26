"""
2330 除息事件深度測試 — 找最佳持有期。
test before: 0/3/5/7/10/15/20 days
test after:  3/5/10/15/20/30/45/60/90 days

看持有期變長 alpha 會繼續增加還是 plateau / 反轉
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
CACHE_DIV = ROOT / "data" / "cache" / "finmind" / "dividend"


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def compute_event_returns(ex_dates: list, ohlcv: pd.DataFrame, baseline: pd.DataFrame,
                          before: int, after: int) -> pd.DataFrame:
    rows = []
    o = ohlcv.set_index("date").sort_index()
    b = baseline.set_index("date").sort_index()
    o_dates = list(o.index)
    b_dates = list(b.index)
    for ex in ex_dates:
        try:
            ex_idx = o_dates.index(ex)
        except ValueError:
            ex_candidates = [d for d in o_dates if d >= ex]
            if not ex_candidates:
                continue
            ex = ex_candidates[0]
            ex_idx = o_dates.index(ex)
        start_idx = ex_idx - before
        end_idx = ex_idx + after
        if start_idx < 0 or end_idx >= len(o_dates):
            continue
        entry_date = o_dates[start_idx]
        exit_date = o_dates[end_idx]
        entry = float(o.loc[entry_date, "close"])
        exit_p = float(o.loc[exit_date, "close"])
        s_ret = (exit_p / entry - 1) * 100
        try:
            b_e = b_dates.index(entry_date); b_x = b_dates.index(exit_date)
            b_ret = (float(b.iloc[b_x]["close"]) / float(b.iloc[b_e]["close"]) - 1) * 100
        except ValueError:
            continue
        rows.append({"ex_date": ex, "s_ret": s_ret, "b_ret": b_ret,
                     "excess": s_ret - b_ret})
    return pd.DataFrame(rows)


def main():
    baseline = load_ohlcv("0050")
    ohlcv = load_ohlcv("2330")

    div = pd.read_parquet(CACHE_DIV / "2330_announce.parquet")
    ex_dates = sorted(set(div["ex_date"].dropna().tolist()))
    ex_dates = [d for d in ex_dates if d >= date(2024, 1, 1)]
    print(f"2330 除息日 ({len(ex_dates)}):")
    for d in ex_dates:
        print(f"  {d}")

    BEFORES = [0, 3, 5, 7, 10, 15, 20, 30]
    AFTERS = [3, 5, 10, 15, 20, 30, 45, 60, 90]

    print(f"\n{len(BEFORES)} × {len(AFTERS)} = {len(BEFORES)*len(AFTERS)} 變體")

    grid = pd.DataFrame(index=BEFORES, columns=AFTERS, dtype=float)
    n_grid = pd.DataFrame(index=BEFORES, columns=AFTERS, dtype=int)
    for b_d in BEFORES:
        for a_d in AFTERS:
            ev = compute_event_returns(ex_dates, ohlcv, baseline, b_d, a_d)
            if not ev.empty:
                grid.at[b_d, a_d] = ev["excess"].mean()
                n_grid.at[b_d, a_d] = len(ev)

    print(f"\n=== Excess return matrix (vs 0050 baseline) ===")
    print(f"{'before \\ after':>15}", end="")
    for a_d in AFTERS:
        print(f"{a_d:>7}d", end="")
    print()
    for b_d in BEFORES:
        print(f"{b_d:>13}d  ", end="")
        for a_d in AFTERS:
            v = grid.at[b_d, a_d]
            n = n_grid.at[b_d, a_d]
            if pd.isna(v):
                print(f"{'-':>8}", end="")
            else:
                marker = "*" if v > 4 else " "
                print(f"{v:>+6.2f}%{marker}", end="")
        print()

    print(f"\n=== Sample size matrix (n=) ===")
    print(f"{'before \\ after':>15}", end="")
    for a_d in AFTERS:
        print(f"{a_d:>7}d", end="")
    print()
    for b_d in BEFORES:
        print(f"{b_d:>13}d  ", end="")
        for a_d in AFTERS:
            n = n_grid.at[b_d, a_d]
            if pd.isna(n):
                print(f"{'-':>8}", end="")
            else:
                print(f"{int(n):>8}", end="")
        print()


if __name__ == "__main__":
    main()
