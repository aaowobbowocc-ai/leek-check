"""
Revenue YoY Portfolio OOS Validation

驗證 max=20 yoy_asc 配置（in-sample CAGR +26.5%）在子期間是否仍 robust。

Split:
  - 1H: 2020-01-01 to 2022-12-31 (3 years; COVID + bull market)
  - 2H: 2023-01-01 to 2025-12-31 (3 years; AI boom + Trump crash)

如果兩期都贏 0050 → 真 robust，可 paper trade 部署
如果一期輸 → regime-dependent，需加條件
如果兩期都輸 → in-sample fluke，撤回 conclusion
"""
from __future__ import annotations

import io
import sys
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

CACHE = ROOT / "data" / "cache" / "revenue_yoy_events.parquet"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def filter_period(events: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.to_datetime(start)
    e = pd.to_datetime(end)
    df = events.copy()
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    return df[(df["signal_date"] >= s) & (df["signal_date"] <= e)].reset_index(drop=True)


def simulate(events: pd.DataFrame, max_pos: int, priority: str) -> dict:
    events = events.copy()
    events["signal_date"] = pd.to_datetime(events["signal_date"]).dt.date
    events["exit_date"] = pd.to_datetime(events["exit_date"]).dt.date

    asc = priority == "yoy_asc"
    events = events.sort_values(
        ["signal_date", "yoy"], ascending=[True, asc]
    ).reset_index(drop=True)

    signal_dates = sorted(events["signal_date"].unique())
    open_pos: list = []
    exec_idx: list = []
    skip_idx: list = []

    for dt in signal_dates:
        open_pos = [p for p in open_pos if p["exit_date"] > dt]
        today_idx = events.index[events["signal_date"] == dt]
        open_tk = {p["ticker"] for p in open_pos}

        for ri in today_idx:
            sig = events.iloc[ri]
            if sig["ticker"] in open_tk:
                continue
            if len(open_pos) >= max_pos:
                skip_idx.append(ri)
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
    rmax = nav["nav"].cummax()
    dd = (nav["nav"] / rmax - 1).min()
    return {
        "cagr": cagr,
        "dd": dd,
        "n_exec": len(exec_idx),
        "exec_alpha": float(executed["fwd_return"].mean()) if len(executed) > 0 else 0,
    }


def etf_cagr(start: str, end: str, ticker: str = "0050") -> tuple:
    p = TW_CACHE / f"{ticker}.parquet"
    if not p.exists():
        return 0, 0
    try:
        etf = pd.read_parquet(p)
        etf["date"] = pd.to_datetime(etf["date"])
        etf = etf.sort_values("date")
        s = pd.to_datetime(start)
        e = pd.to_datetime(end)
        etf = etf[(etf["date"] >= s) & (etf["date"] <= e)]
        if len(etf) < 2:
            return 0, 0
        total = etf["close"].iloc[-1] / etf["close"].iloc[0] - 1
        yrs = (etf["date"].iloc[-1] - etf["date"].iloc[0]).days / 365.25
        cagr = (1 + total) ** (1 / yrs) - 1 if yrs > 0 else 0
        dd = (etf["close"] / etf["close"].cummax() - 1).min()
        return cagr, dd
    except Exception:
        return 0, 0


def main():
    print("=" * 78)
    print("  Revenue YoY OOS Validation (max=20 yoy_asc)")
    print("=" * 78)

    if not CACHE.exists():
        print(f"  ❌ Cache 不存在: {CACHE}")
        print(f"     請先跑 python scripts/revenue_yoy_portfolio_test.py")
        return

    all_events = pd.read_parquet(CACHE)
    print(f"  載入 {len(all_events):,} events")

    splits = [
        ("Full", "2020-01-01", "2025-12-31"),
        ("1H (COVID + bull)", "2020-01-01", "2022-12-31"),
        ("2H (AI boom + Trump)", "2023-01-01", "2025-12-31"),
    ]

    print(f"\n  {'Period':<22} {'n_events':>9} {'CAGR':>8} {'MaxDD':>8} "
          f"{'exec_α':>8} {'0050 CAGR':>10} {'Δ vs 0050':>11}")
    print(f"  {'-'*22} {'-'*9} {'-'*8} {'-'*8} {'-'*8} {'-'*10} {'-'*11}")

    results = []
    for label, s, e in splits:
        sub = filter_period(all_events, s, e)
        if sub.empty:
            print(f"  {label}: 無事件")
            continue
        r = simulate(sub, max_pos=20, priority="yoy_asc")
        ec, ed = etf_cagr(s, e)
        diff = r["cagr"] - ec
        marker = "✅" if diff > 0 else "❌"
        print(f"  {label:<22} {len(sub):>9,} "
              f"{r['cagr']*100:>+7.1f}% "
              f"{r['dd']*100:>+7.1f}% "
              f"{r['exec_alpha']:>+7.2f}% "
              f"{ec*100:>+9.1f}% "
              f"{diff*100:>+8.1f}pp{marker}")
        results.append({"period": label, **r, "etf_cagr": ec, "diff": diff})

    # OOS verdict
    if len(results) >= 3:
        oos_1h = results[1]["diff"]
        oos_2h = results[2]["diff"]
        print(f"\n  {'='*40}")
        print(f"  OOS Verdict")
        print(f"  {'='*40}")
        if oos_1h > 0 and oos_2h > 0:
            print(f"  ✅ 兩期 OOS 都贏 0050 — 真 robust，可進 paper trade")
            print(f"     1H: {oos_1h*100:+.1f}pp, 2H: {oos_2h*100:+.1f}pp")
        elif oos_1h > 0 and oos_2h <= 0:
            print(f"  ⚠️ 只 2020-2022 贏，2023-2025 失效 — post-2022 regime change?")
            print(f"     可能解釋: AI boom 期間 Revenue YoY signal 被定價 / 散戶過度進場")
        elif oos_1h <= 0 and oos_2h > 0:
            print(f"  ⚠️ 只 2023-2025 贏，COVID 期間失效 — alpha 可能 post-2023 才浮現")
        else:
            print(f"  🚨 兩期 OOS 都輸 0050 — full-period +4.8pp 是 in-sample 過擬合")
            print(f"     撤回「Revenue YoY 真 alpha」結論")


if __name__ == "__main__":
    main()
