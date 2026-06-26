"""
Revenue YoY Regime-Filter Test

問題: Revenue YoY portfolio 兩期 +17% 穩定，但 2H 2023-2025 輸 0050 +37%。
     根因是 AI boom 期 TSMC 集中度橫掃，廣度因子打不過。

假設: 加 regime filter 「只在 0050 弱期執行」可救 2H 表現。
     真正想實現的是 barbell 的 dynamic allocation。

測試 5 個 regime filter（基於 TAIEX 訊號當下狀態）:
  A. None (baseline)                    — 總執行
  B. TAIEX 60d return < +10% (no boom)  — 排除強牛
  C. TAIEX 30d vol > 15% (vol regime)   — 高波動才執行
  D. TAIEX 距 MA200 < +20% (no euphoria) — 非過熱才執行
  E. TAIEX 60d return < 0 (drawdown)    — 純逆勢

目標: 找到 2H beat 0050 的 filter；如果都救不了，barbell 衛星定位最終確認。
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

EVENTS_CACHE = ROOT / "data" / "cache" / "revenue_yoy_events.parquet"
TWII_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "^TWII.parquet"
ETF_PATH = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "0050.parquet"


def load_taiex_regime() -> pd.DataFrame:
    """Compute TAIEX regime indicators per trading day."""
    twii = pd.read_parquet(TWII_PATH)
    twii["date"] = pd.to_datetime(twii["date"])
    twii = twii.sort_values("date").reset_index(drop=True)
    twii["ret_60d"] = twii["close"].pct_change(60) * 100
    twii["ma200"] = twii["close"].rolling(200).mean()
    twii["dist_ma200"] = (twii["close"] / twii["ma200"] - 1) * 100
    # 30d realized vol annualized
    twii["log_ret"] = np.log(twii["close"] / twii["close"].shift(1))
    twii["vol_30d"] = twii["log_ret"].rolling(30).std() * np.sqrt(252) * 100
    return twii[["date", "ret_60d", "dist_ma200", "vol_30d"]]


def attach_regime(events: pd.DataFrame, regime: pd.DataFrame) -> pd.DataFrame:
    events = events.copy()
    events["signal_date"] = pd.to_datetime(events["signal_date"]).astype("datetime64[ns]")
    regime = regime.copy()
    regime["date"] = pd.to_datetime(regime["date"]).astype("datetime64[ns]")
    events = events.sort_values("signal_date")
    regime = regime.sort_values("date")
    merged = pd.merge_asof(events, regime, left_on="signal_date", right_on="date",
                            direction="backward")
    return merged


FILTERS = {
    "A: None":                        lambda e: e,
    "B: TAIEX 60d ret < +10%":        lambda e: e[e["ret_60d"] < 10],
    "C: TAIEX vol30 > 15%":           lambda e: e[e["vol_30d"] > 15],
    "D: TAIEX dist MA200 < +20%":     lambda e: e[e["dist_ma200"] < 20],
    "E: TAIEX 60d ret < 0":           lambda e: e[e["ret_60d"] < 0],
}


def simulate(events: pd.DataFrame, max_pos: int = 20) -> dict:
    if events.empty:
        return {"cagr": 0, "dd": 0, "n_exec": 0}
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
        return {"cagr": 0, "dd": 0, "n_exec": 0}
    yrs = (pd.to_datetime(nav["date"].iloc[-1]) - pd.to_datetime(nav["date"].iloc[0])).days / 365.25
    cagr = (nav["nav"].iloc[-1] / nav["nav"].iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0
    dd = (nav["nav"] / nav["nav"].cummax() - 1).min()
    return {"cagr": cagr, "dd": dd, "n_exec": len(exec_idx)}


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


def filter_by_period(events: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    df = events.copy()
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    s, e = pd.to_datetime(start), pd.to_datetime(end)
    return df[(df["signal_date"] >= s) & (df["signal_date"] <= e)].reset_index(drop=True)


def main():
    print("=" * 80)
    print("  Revenue YoY Regime Filter Test (max=20 yoy_asc)")
    print("  目標：找到 2H 2023-2025 仍贏 0050 +37% 的 regime filter")
    print("=" * 80)

    if not EVENTS_CACHE.exists():
        print("  ❌ 先跑 revenue_yoy_portfolio_test.py 產 cache")
        return

    events = pd.read_parquet(EVENTS_CACHE)
    regime = load_taiex_regime()
    events_r = attach_regime(events, regime)
    print(f"  載入 {len(events_r):,} events，attach regime indicators")

    print(f"\n  Regime 樣本分布（events 觸發時的 TAIEX 狀態）:")
    print(f"    ret_60d:    p10={events_r['ret_60d'].quantile(0.1):.1f}  "
          f"median={events_r['ret_60d'].median():.1f}  "
          f"p90={events_r['ret_60d'].quantile(0.9):.1f}")
    print(f"    vol_30d:    p10={events_r['vol_30d'].quantile(0.1):.1f}  "
          f"median={events_r['vol_30d'].median():.1f}  "
          f"p90={events_r['vol_30d'].quantile(0.9):.1f}")
    print(f"    dist_MA200: p10={events_r['dist_ma200'].quantile(0.1):.1f}  "
          f"median={events_r['dist_ma200'].median():.1f}  "
          f"p90={events_r['dist_ma200'].quantile(0.9):.1f}")

    splits = [
        ("Full 2020-2025", "2020-01-01", "2025-12-31"),
        ("1H 2020-2022", "2020-01-01", "2022-12-31"),
        ("2H 2023-2025", "2023-01-01", "2025-12-31"),
    ]
    etf_cagrs = {label: etf_cagr(s, e) for label, s, e in splits}

    print(f"\n  0050 baseline:")
    for label, _, _ in splits:
        print(f"    {label}: {etf_cagrs[label]*100:+.1f}%/yr")

    print(f"\n  {'Filter':<32} ", end="")
    for label, _, _ in splits:
        print(f"{label[:14]:>16}", end="")
    print()
    print(f"  {'-'*32} {'-'*14} {'-'*14} {'-'*14}")

    rows = []
    for fname, ffunc in FILTERS.items():
        filtered = ffunc(events_r)
        row_data = {"filter": fname}
        line = f"  {fname:<32} "
        for label, s, e in splits:
            sub = filter_by_period(filtered, s, e)
            r = simulate(sub, max_pos=20)
            etf_c = etf_cagrs[label]
            diff = r["cagr"] - etf_c
            marker = "✅" if diff > 0 else "❌"
            line += f"{r['cagr']*100:>+5.1f}%(n={r['n_exec']:>4})"
            line += f"{marker} "
            row_data[f"{label}_cagr"] = r["cagr"]
            row_data[f"{label}_diff"] = diff
        print(line)
        rows.append(row_data)

    df = pd.DataFrame(rows)
    out = ROOT / "logs" / "revenue_yoy_regime_filter.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n  ✅ 寫入 {out.relative_to(ROOT)}")

    # Find best filter for 2H
    if "2H 2023-2025_cagr" in df.columns:
        best = df.loc[df["2H 2023-2025_diff"].idxmax()]
        print(f"\n  🏆 2H 最佳 filter: {best['filter']}")
        print(f"     CAGR={best['2H 2023-2025_cagr']*100:+.1f}%  "
              f"diff vs 0050={best['2H 2023-2025_diff']*100:+.1f}pp")

        if best["2H 2023-2025_diff"] > 0:
            print(f"\n  ✅ Regime filter 救活 2H — 可進入 paper trade")
        elif best["2H 2023-2025_diff"] > -0.05:
            print(f"\n  ⚠️ 接近 0050（差 < 5pp）— marginal but not winning")
        else:
            print(f"\n  🚨 沒有 regime filter 救得了 2H — barbell 衛星是最終定位")
            print(f"     TSMC AI boom 結構性壟斷，任何廣度策略都打不過")


if __name__ == "__main__":
    main()
