"""
Revenue YoY Portfolio + Liquidity Filter — 確認 alpha 在可執行範圍仍 robust

問題: max=20 yoy_asc portfolio CAGR +26.5% 是 in-sample 結果。
     3-AI critique 指出小型股流動性差，實單滑價遠高於 0.78%。

方法: 加日均成交額 filter 後重跑 portfolio backtest
  - L1 filter: 觸發前 60 日 avg dollar vol > 1 億/日
  - L2 filter: > 5 億/日 (institutional grade)
  - L3 filter: > 10 億/日 (大型股 only)

預期:
  若 alpha 在 L2/L3 仍 > 0050 → real alpha
  若 alpha 在 L2/L3 接近 0 或負 → 之前的 +26.5% 是 illiquid noise
"""
from __future__ import annotations

import io
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.portfolio_level_backtest import compute_daily_nav

EVENTS_CACHE = ROOT / "data" / "cache" / "revenue_yoy_events.parquet"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
ETF_PATH = TW_CACHE / "0050.parquet"


def attach_liquidity(events: pd.DataFrame) -> pd.DataFrame:
    """為每個 event 計算觸發前 60 日 avg dollar volume."""
    print("  attaching liquidity to events...")
    events = events.copy()
    events["signal_date"] = pd.to_datetime(events["signal_date"])
    events["avg_dv_60d"] = np.nan

    cache: dict = {}
    for i, row in events.iterrows():
        tk = row["ticker"]
        if tk not in cache:
            try:
                df = pd.read_parquet(TW_CACHE / f"{tk}.parquet")
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)
                df["dv"] = df["close"] * df["volume"]
                cache[tk] = df
            except Exception:
                cache[tk] = None
                continue
        df = cache[tk]
        if df is None:
            continue
        sig_dt = row["signal_date"]
        before = df[df["date"] < sig_dt].tail(60)
        if len(before) < 30:
            continue
        events.loc[i, "avg_dv_60d"] = before["dv"].mean()

    return events


def simulate(events: pd.DataFrame, max_pos: int = 20) -> dict:
    if events.empty:
        return {"cagr": 0, "dd": 0, "n_exec": 0, "exec_alpha": 0}
    events = events.copy()
    events["signal_date"] = pd.to_datetime(events["signal_date"]).dt.date
    events["exit_date"] = pd.to_datetime(events["exit_date"]).dt.date
    events = events.sort_values(
        ["signal_date", "yoy"], ascending=[True, True]
    ).reset_index(drop=True)

    signal_dates = sorted(events["signal_date"].unique())
    open_pos: list = []
    exec_idx: list = []

    for dt in signal_dates:
        open_pos = [p for p in open_pos if p["exit_date"] > dt]
        today_idx = events.index[events["signal_date"] == dt]
        open_tk = {p["ticker"] for p in open_pos}
        for ri in today_idx:
            sig = events.iloc[ri]
            if sig["ticker"] in open_tk:
                continue
            if len(open_pos) >= max_pos:
                continue
            open_pos.append({"ticker": sig["ticker"], "exit_date": sig["exit_date"]})
            open_tk.add(sig["ticker"])
            exec_idx.append(ri)

    executed = events.iloc[exec_idx] if exec_idx else pd.DataFrame()
    nav = compute_daily_nav(executed, max_pos) if len(executed) > 0 else pd.DataFrame()
    if nav.empty:
        return {"cagr": 0, "dd": 0, "n_exec": 0, "exec_alpha": 0}

    yrs = (pd.to_datetime(nav["date"].iloc[-1]) - pd.to_datetime(nav["date"].iloc[0])).days / 365.25
    cagr = (nav["nav"].iloc[-1] / nav["nav"].iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0
    dd = (nav["nav"] / nav["nav"].cummax() - 1).min()
    return {
        "cagr": cagr,
        "dd": dd,
        "n_exec": len(exec_idx),
        "exec_alpha": float(executed["fwd_return"].mean()) if len(executed) > 0 else 0,
    }


def etf_cagr(start: str, end: str) -> float:
    etf = pd.read_parquet(ETF_PATH)
    etf["date"] = pd.to_datetime(etf["date"])
    etf = etf.sort_values("date")
    s, e = pd.to_datetime(start), pd.to_datetime(end)
    sub = etf[(etf["date"] >= s) & (etf["date"] <= e)]
    if len(sub) < 2:
        return 0
    total = sub["close"].iloc[-1] / sub["close"].iloc[0] - 1
    yrs = (sub["date"].iloc[-1] - sub["date"].iloc[0]).days / 365.25
    return (1 + total) ** (1 / yrs) - 1 if yrs > 0 else 0


def main():
    print("=" * 80)
    print("  Revenue YoY Portfolio + Liquidity Filter")
    print("=" * 80)

    if not EVENTS_CACHE.exists():
        print("  ❌ Events cache 不存在")
        return

    events = pd.read_parquet(EVENTS_CACHE)
    events = attach_liquidity(events)
    valid = events.dropna(subset=["avg_dv_60d"])
    print(f"  Total events: {len(events):,}, with liquidity: {len(valid):,}")

    print(f"\n  Liquidity 分布:")
    print(f"    Median: {valid['avg_dv_60d'].median()/1e8:.1f} 億/日")
    print(f"    P25: {valid['avg_dv_60d'].quantile(0.25)/1e8:.1f} 億")
    print(f"    P75: {valid['avg_dv_60d'].quantile(0.75)/1e8:.1f} 億")
    print(f"    P90: {valid['avg_dv_60d'].quantile(0.9)/1e8:.1f} 億")

    # Filter levels (NT$/day average dollar volume)
    filters = [
        ("No filter (all)", 0),
        ("L1: > 1 億/日", 1e8),
        ("L2: > 3 億/日", 3e8),
        ("L3: > 5 億/日", 5e8),
        ("L4: > 10 億/日", 10e8),
    ]

    splits = [
        ("Full 2020-2025", "2020-01-01", "2025-12-31"),
        ("1H 2020-2022", "2020-01-01", "2022-12-31"),
        ("2H 2023-2025", "2023-01-01", "2025-12-31"),
    ]
    etf_cagrs = {label: etf_cagr(s, e) for label, s, e in splits}

    print(f"\n  0050 baseline CAGR:")
    for label, _, _ in splits:
        print(f"    {label}: {etf_cagrs[label]*100:+.1f}%/yr")

    print(f"\n  {'Filter':<22} ", end="")
    for label, _, _ in splits:
        print(f"  {label[:14]:>14}", end="")
    print()
    print("  " + "-" * 22 + (" " + "-" * 14) * 3)

    rows = []
    for fname, threshold in filters:
        filtered = valid[valid["avg_dv_60d"] >= threshold]
        row = {"filter": fname, "n_total": len(filtered)}
        line = f"  {fname:<22} "
        for label, s, e in splits:
            sub = filtered[
                (filtered["signal_date"] >= pd.to_datetime(s))
                & (filtered["signal_date"] <= pd.to_datetime(e))
            ]
            if len(sub) < 5:
                line += f"{'(n<5)':>16}"
                continue
            r = simulate(sub, max_pos=20)
            etf_c = etf_cagrs[label]
            diff = r["cagr"] - etf_c
            mark = "✅" if diff > 0 else "❌"
            line += f"  {r['cagr']*100:>+5.1f}%(n={r['n_exec']:>4}){mark}"
            row[f"{label}_cagr"] = r["cagr"]
            row[f"{label}_diff"] = diff
        print(line)
        rows.append(row)

    # Save
    df = pd.DataFrame(rows)
    out = ROOT / "logs" / "revenue_yoy_liquidity_filter_sweep.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n  ✅ Saved to {out.relative_to(ROOT)}")

    # Conclusion
    print(f"\n  === Conclusion ===")
    if rows:
        # Compare best filter for 2H (the failure period)
        best = max(rows, key=lambda r: r.get("2H 2023-2025_diff", -999))
        print(f"  Best 2H performer: {best['filter']}")
        print(f"    2H CAGR: {best.get('2H 2023-2025_cagr', 0)*100:+.1f}%  vs 0050 +37.3%")
        print(f"    Diff: {best.get('2H 2023-2025_diff', 0)*100:+.1f}pp")

        # 1H performance preserved?
        no_filter = [r for r in rows if "No filter" in r["filter"]][0]
        l3 = next((r for r in rows if r["filter"] == "L3: > 5 億/日"), None)
        if l3:
            print(f"\n  L3 filter (流動性 > 5 億/日):")
            print(f"    Full: {l3.get('Full 2020-2025_cagr', 0)*100:+.1f}% vs no-filter {no_filter.get('Full 2020-2025_cagr', 0)*100:+.1f}%")
            print(f"    1H:   {l3.get('1H 2020-2022_cagr', 0)*100:+.1f}% vs no-filter {no_filter.get('1H 2020-2022_cagr', 0)*100:+.1f}%")
            full_drop = (l3.get('Full 2020-2025_cagr', 0) - no_filter.get('Full 2020-2025_cagr', 0)) * 100
            print(f"    → CAGR 變化 vs no-filter: {full_drop:+.1f}pp")
            if full_drop > -3:
                print(f"    ✅ Alpha 在 L3 流動性下保留，可實單")
            else:
                print(f"    🚨 流動性 filter 後 alpha 大幅縮水，原 +26.5% 部分來自不流動小股")


if __name__ == "__main__":
    main()
