"""
Crash Escape Strategy Backtest — 平常 100% 0050，只有 CRASH 才逃

對比 4 種策略 (NT$100K → 2020-01-02 to 2026-05-05):

A. 0050 BTH baseline (永遠 100% 0050)
B. MA200 trend (TAIEX > MA200 → 100% 0050, 否則 → 100% cash)
C. CRASH escape (default 100% 0050, regime=CRASH 才 100% cash)
D. Aggressive escape (CRASH OR 60d ret < -10% → cash)

每個策略月底 check 切換，trade cost 0.3% one-way (0050 流動性好)。
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

START = "2020-01-02"
END = "2026-05-05"
INITIAL = 100_000
COST = 0.003  # 0.3% one-way (0050 大型 ETF cost 低)


def classify(dist_ma200, vol_30d, ret_60d):
    if pd.isna(dist_ma200) or pd.isna(vol_30d) or pd.isna(ret_60d):
        return "UNKNOWN"
    if ret_60d < -15 and vol_30d > 25:
        return "CRASH"
    if dist_ma200 < -5 and ret_60d < 0:
        return "BEAR"
    if dist_ma200 > 20:
        return "STRONG_BULL"
    if abs(dist_ma200) < 5:
        return "SIDEWAYS"
    if dist_ma200 > 0:
        return "BULL_TREND"
    return "SIDEWAYS"


def main():
    # Load TAIEX (regime calc) + 0050 (trade target)
    twii = pd.read_parquet(ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "^TWII.parquet")
    twii["date"] = pd.to_datetime(twii["date"]).dt.tz_localize(None)
    twii = twii.sort_values("date").reset_index(drop=True)
    twii["log_ret"] = np.log(twii["close"] / twii["close"].shift(1))
    twii["ret_60d"] = twii["close"].pct_change(60) * 100
    twii["ma200"] = twii["close"].rolling(200).mean()
    twii["dist_ma200"] = (twii["close"] / twii["ma200"] - 1) * 100
    twii["vol_30d"] = twii["log_ret"].rolling(30).std() * np.sqrt(252) * 100
    twii["regime"] = twii.apply(
        lambda r: classify(r["dist_ma200"], r["vol_30d"], r["ret_60d"]), axis=1
    )
    twii["above_ma200"] = twii["close"] > twii["ma200"]

    px = pd.read_parquet(ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / "0050.parquet")
    px["date"] = pd.to_datetime(px["date"]).dt.tz_localize(None)
    px = px.sort_values("date").reset_index(drop=True)
    px = px.merge(twii[["date", "regime", "above_ma200", "ret_60d", "dist_ma200", "vol_30d"]], on="date")

    px = px[(px["date"] >= pd.to_datetime(START)) & (px["date"] <= pd.to_datetime(END))].reset_index(drop=True)
    print(f"  Period: {px['date'].iloc[0].date()} ~ {px['date'].iloc[-1].date()}, {len(px)} 天")

    # Month ends
    px["ym"] = px["date"].dt.to_period("M")
    month_end_idx = px.groupby("ym").tail(1).index.tolist()

    def simulate(name, decide_fn):
        """decide_fn(row) → True = hold 0050, False = cash"""
        nav = float(INITIAL)
        in_market = False  # start in cash, decide on first month-end
        nav_history = []
        switches = 0
        last_switch_dt = None

        # Track positions
        last_close = px.iloc[0]["close"]
        nav_history.append({"date": px.iloc[0]["date"], "nav": nav, "in_market": False})

        for i in range(len(px)):
            row = px.iloc[i]
            today_close = row["close"]

            # Mark to market if in market
            if in_market and i > 0:
                prev_close = px.iloc[i - 1]["close"]
                ret = today_close / prev_close - 1
                nav *= (1 + ret)

            # On month end, decide
            if i in month_end_idx:
                target_in = decide_fn(row)
                if target_in != in_market:
                    nav *= (1 - COST)  # one-way switch cost
                    in_market = target_in
                    switches += 1
                    last_switch_dt = row["date"]

            nav_history.append({"date": row["date"], "nav": nav, "in_market": in_market})

        df_nav = pd.DataFrame(nav_history)
        cagr_yrs = (df_nav["date"].iloc[-1] - df_nav["date"].iloc[0]).days / 365.25
        cagr = (df_nav["nav"].iloc[-1] / INITIAL) ** (1 / cagr_yrs) - 1
        running_max = df_nav["nav"].cummax()
        max_dd = (df_nav["nav"] / running_max - 1).min()

        return {
            "name": name,
            "final_nav": df_nav["nav"].iloc[-1],
            "total_ret": (df_nav["nav"].iloc[-1] / INITIAL - 1) * 100,
            "cagr": cagr * 100,
            "max_dd": max_dd * 100,
            "switches": switches,
            "in_market_pct": df_nav["in_market"].mean() * 100,
            "history": df_nav,
        }

    strategies = [
        ("A. 0050 BTH (baseline)", lambda r: True),
        ("B. MA200 trend", lambda r: bool(r["above_ma200"])),
        ("C. CRASH escape only", lambda r: r["regime"] != "CRASH"),
        ("D. CRASH + 60d<-10%", lambda r: r["regime"] != "CRASH" and not (pd.notna(r["ret_60d"]) and r["ret_60d"] < -10)),
        ("E. CRASH + BEAR escape", lambda r: r["regime"] not in ("CRASH", "BEAR")),
    ]

    results = [simulate(name, fn) for name, fn in strategies]

    print()
    print("=" * 80)
    print(f"  {'Strategy':<28} {'最終 NAV':>12} {'總報酬':>9} {'CAGR':>8} {'Max DD':>8} {'切換':>5} {'在市':>7}")
    print("=" * 80)
    for r in results:
        print(f"  {r['name']:<28} NT${r['final_nav']:>10,.0f}  "
              f"{r['total_ret']:>+7.1f}%  {r['cagr']:>+6.1f}%  {r['max_dd']:>+6.1f}%  "
              f"{r['switches']:>4}  {r['in_market_pct']:>5.1f}%")

    # Detailed comparison
    bth = results[0]
    print(f"\n  ===== 對比 0050 BTH =====")
    for r in results[1:]:
        diff = r["final_nav"] - bth["final_nav"]
        diff_pct = (r["final_nav"] / bth["final_nav"] - 1) * 100
        dd_diff = r["max_dd"] - bth["max_dd"]
        print(f"  {r['name']:<28}: NT${diff:>+10,.0f} ({diff_pct:>+6.1f}%) "
              f"vs DD {dd_diff:+.1f}pp ({r['max_dd']:+.1f}% vs {bth['max_dd']:+.1f}%)")

    # Year-by-year for top picks
    print(f"\n  ===== 年度報酬 (vs 0050 BTH) =====")
    print(f"  {'Year':<6}", end="")
    for r in results:
        print(f"{r['name'][:18]:>20}", end="")
    print()
    for yr in range(2020, 2027):
        line = f"  {yr:<6}"
        for r in results:
            df = r["history"].copy()
            df["year"] = pd.to_datetime(df["date"]).dt.year
            sub = df[df["year"] == yr]
            if len(sub) < 2:
                line += f"{'-':>20}"
                continue
            ret = (sub["nav"].iloc[-1] / sub["nav"].iloc[0] - 1) * 100
            line += f"{ret:>+18.1f}% "
        print(line)


if __name__ == "__main__":
    main()
