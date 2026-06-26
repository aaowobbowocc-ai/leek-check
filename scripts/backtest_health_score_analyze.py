"""
讀取 health_score_backtest.csv,做:
  1. OOS 切分 (2020-22 IS / 2023-26 OOS)
  2. 正確的 portfolio simulation (non-overlapping monthly entries, equal-weight slot)
  3. 信心區間 + bootstrap t
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "backtest" / "health_score_backtest.csv"


def print_block(df: pd.DataFrame, title: str):
    """打印單期統計表 (HIGH/MID/LOW × 各 hold)。"""
    print(f"\n=== {title} ===")
    if df.empty:
        print("  (no events)")
        return
    for hold in sorted(df["hold"].unique()):
        sub = df[df["hold"] == hold]
        bench = sub[sub["bucket"] == "0050_BENCHMARK"]["ret"]
        bench_m = bench.mean() if len(bench) else float("nan")
        bench_n = len(bench)
        print(f"\n  Hold {hold} 日 (0050 baseline mean: {bench_m:+.2f}% n={bench_n})")
        print(f"  {'Bucket':<18} {'mean':>8} {'std':>7} {'n':>5} {'win%':>6} "
              f"{'alpha':>8} {'t-stat':>8} {'p-val':>7}")
        for bucket in ["LOW(<50)", "MID(50-69)", "HIGH(>=70)"]:
            seg = sub[sub["bucket"] == bucket]
            if seg.empty:
                continue
            m = seg["ret"].mean()
            s = seg["ret"].std()
            n = len(seg)
            win = (seg["ret"] > 0).mean() * 100
            alpha = m - bench_m
            # 對 bench 做 Welch t-test (兩組獨立樣本)
            if n > 1 and len(bench) > 1:
                t, p = stats.ttest_ind(seg["ret"], bench, equal_var=False)
            else:
                t, p = float("nan"), float("nan")
            print(f"  {bucket:<18} {m:+8.2f}% {s:6.2f} {n:5} {win:5.1f}% "
                  f"{alpha:+7.2f}pp {t:+7.2f} {p:6.3f}")


def portfolio_simulation(df: pd.DataFrame, hold: int, n_slots: int = 5,
                         min_score: float = 70.0) -> dict:
    """
    正確的 portfolio sim:每月只開 1 個 cohort,持有 hold 日後出場再開下一個。
    cohort 內等權持有 ≤ n_slots 檔 (取分數最高的 HIGH 股),
    若不足 n_slots 檔則剩餘空槽 0% return (cash drag)。
    bench:同期等長 0050 hold。
    """
    sub = df[(df["hold"] == hold) & (df["bucket"] != "0050_BENCHMARK")].copy()
    if sub.empty:
        return {}
    # 月度 cohort
    sub["date"] = pd.to_datetime(sub["date"])
    sub = sub.sort_values(["date", "score"], ascending=[True, False])

    # 取每月 HIGH bucket 前 n_slots 檔
    high_only = sub[sub["score"] >= min_score]

    cohorts = []
    for date, grp in high_only.groupby("date"):
        # 每月取分數最高的 n_slots 檔
        top = grp.head(n_slots)
        rets = top["ret"].tolist()
        # 不足補 0 (cash 空轉)
        while len(rets) < n_slots:
            rets.append(0.0)
        cohort_ret = float(np.mean(rets))
        cohorts.append({"date": date, "cohort_ret": cohort_ret, "n_picks": len(top)})

    if not cohorts:
        return {}
    ch = pd.DataFrame(cohorts).sort_values("date").reset_index(drop=True)

    # bench: 0050 同期 hold (從 backtest CSV)
    bench = df[(df["hold"] == hold) & (df["bucket"] == "0050_BENCHMARK")][["date", "ret"]]
    bench["date"] = pd.to_datetime(bench["date"])
    bench.columns = ["date", "bench_ret"]
    merged = ch.merge(bench, on="date", how="inner")
    if merged.empty:
        return {}

    # ⚠️ 正確處理 overlap:每月開新倉,持 hold 日後出場
    # 但因為 hold=60 而 rebalance interval ~21 日,實際上會 3 個月 cohort overlap
    # 為了避免 stack 幻覺,我們只取「不 overlap 月份」(每 ceil(hold/21) 月取 1 個 cohort)
    skip = max(1, int(np.ceil(hold / 21)))
    non_overlap = merged.iloc[::skip].reset_index(drop=True)
    if len(non_overlap) < 2:
        return {}

    non_overlap["port_cum"] = (1 + non_overlap["cohort_ret"] / 100).cumprod() - 1
    non_overlap["bench_cum"] = (1 + non_overlap["bench_ret"] / 100).cumprod() - 1

    n = len(non_overlap)
    years = (non_overlap["date"].iloc[-1] - non_overlap["date"].iloc[0]).days / 365.25
    port_final = non_overlap["port_cum"].iloc[-1]
    bench_final = non_overlap["bench_cum"].iloc[-1]

    def cagr(total, yrs):
        if total <= -1 or yrs <= 0:
            return float("nan")
        return ((1 + total) ** (1 / yrs) - 1) * 100

    port_cagr = cagr(port_final, years)
    bench_cagr = cagr(bench_final, years)

    # MaxDD
    def max_dd(cum_series):
        # 換成 wealth (1 + cum)
        wealth = 1 + cum_series
        peak = wealth.cummax()
        dd = (wealth - peak) / peak
        return dd.min() * 100

    port_dd = max_dd(non_overlap["port_cum"])
    bench_dd = max_dd(non_overlap["bench_cum"])

    return {
        "n_cohorts": n,
        "years": years,
        "skip_months": skip,
        "port_cum": port_final * 100,
        "bench_cum": bench_final * 100,
        "port_cagr": port_cagr,
        "bench_cagr": bench_cagr,
        "alpha_pp": port_cagr - bench_cagr,
        "port_dd": port_dd,
        "bench_dd": bench_dd,
        "avg_picks": ch["n_picks"].mean(),
        "min_picks": int(ch["n_picks"].min()),
        "n_empty_months": int((ch["n_picks"] == 0).sum()),
    }


def main():
    if not CSV.exists():
        print(f"[X] 找不到 {CSV},請先跑 backtest_health_score.py")
        return

    df = pd.read_csv(CSV)
    df["date"] = pd.to_datetime(df["date"])
    print(f"載入 {len(df):,} events,期間 {df['date'].min().date()} ~ {df['date'].max().date()}")

    # 全期
    print_block(df, "全期 (2020-01 ~ 2026-05)")

    # OOS split: IS = 2020-2022, OOS = 2023-2026
    is_df = df[df["date"] < "2023-01-01"]
    oos_df = df[df["date"] >= "2023-01-01"]
    print_block(is_df, "IS 2020-2022 (in-sample)")
    print_block(oos_df, "OOS 2023-2026 (out-of-sample)")

    # 更細切:逐年
    print("\n=== 逐年 HIGH 組 60d alpha (年度單調性檢查) ===")
    print(f"  {'年份':<6} {'n':>4} {'HIGH mean':>11} {'0050 mean':>11} {'alpha':>9}")
    h60 = df[(df["hold"] == 60) & (df["bucket"] == "HIGH(>=70)")]
    b60 = df[(df["hold"] == 60) & (df["bucket"] == "0050_BENCHMARK")]
    for y in sorted(df["date"].dt.year.unique()):
        h_y = h60[h60["date"].dt.year == y]
        b_y = b60[b60["date"].dt.year == y]
        if h_y.empty or b_y.empty:
            continue
        a = h_y["ret"].mean() - b_y["ret"].mean()
        print(f"  {y:<6} {len(h_y):>4} {h_y['ret'].mean():>+10.2f}% "
              f"{b_y['ret'].mean():>+10.2f}% {a:>+7.2f}pp")

    # Portfolio simulation (正確版)
    print("\n=== Portfolio simulation:正確版 (non-overlapping cohort) ===")
    for hold in [60, 120]:
        for slots in [3, 5]:
            r = portfolio_simulation(df, hold=hold, n_slots=slots, min_score=70.0)
            if not r:
                continue
            print(f"\n  Hold {hold}d × Top-{slots} slots (skip {r['skip_months']} 月避免 overlap)")
            print(f"    n cohorts: {r['n_cohorts']} ({r['years']:.2f} 年)")
            print(f"    平均每 cohort 選到 {r['avg_picks']:.1f} 檔 "
                  f"(min {r['min_picks']},空月 {r['n_empty_months']} 個)")
            print(f"    Portfolio 累積 {r['port_cum']:+.1f}% / CAGR {r['port_cagr']:+.2f}% / MaxDD {r['port_dd']:+.1f}%")
            print(f"    0050 baseline 累積 {r['bench_cum']:+.1f}% / CAGR {r['bench_cagr']:+.2f}% / MaxDD {r['bench_dd']:+.1f}%")
            print(f"    Alpha: {r['alpha_pp']:+.2f} pp/yr")


if __name__ == "__main__":
    main()
