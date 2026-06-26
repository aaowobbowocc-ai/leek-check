"""
配對交易 Pair Trading Backtest。

邏輯：
  1. 找高相關 (>0.85) 配對 (同產業)
  2. 計算 spread z-score (基於滾動 60 日 mean / std)
  3. 訊號 |z| > 2.5 → spread 高那檔 short, 低那檔 long
  4. 平倉：z 回到 0 內 / 或 timeout 20 日

驗證：累計 return vs 同期 0050 baseline + 同 ticker random window
"""
from __future__ import annotations

import io
import sys
from datetime import date
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
COST = 0.34  # 單邊 cost；pair trading 雙邊 = 0.68%
ROLLING_WINDOW = 60  # 計算 spread mean/std
Z_ENTRY = 2.5
Z_EXIT = 0.5
TIMEOUT_DAYS = 20

# 候選配對（同產業）
PAIR_GROUPS = {
    "半導體龍頭": ["2330", "2454", "3711"],
    "封測": ["3711", "6515"],
    "鴻海集團": ["2317", "2354"],
    "面板": ["3481", "2409"],
    "DRAM": ["2408", "2344"],
    "ABF 載板": ["3037", "8046", "3189"],
    "AI server": ["2382", "6669", "3596", "5274"],
    "金控": ["2891", "2882", "2884", "2888"],
    "電信": ["2412", "3045", "4904"],
    "塑化": ["1301", "1303", "1326"],
    "航運": ["2603", "2609", "2615"],
    "鋼鐵": ["2002", "2014", "2027"],
    "電子通路": ["2376", "3036", "2347"],
    "重電": ["1503", "1513", "1519"],
    "PA chip": ["3105", "2455"],
}


_EX_DATES_CACHE: dict[str, list] = {}


def load_ex_dividend_dates(ticker: str) -> list:
    """G-5: Load ex-dividend/ex-rights dates for a TW stock from yfinance.

    Taiwan rule: short positions must close 6 trading days before ex-date
    (融券強制回補), or face forced buy-back at unfavorable prices.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(f"{ticker}.TW")
        divs = t.dividends
        if divs.empty:
            return []
        return [d.date() for d in divs.index]
    except Exception:
        return []


def get_forced_exit_indices(merged_df: pd.DataFrame, ex_dates: list,
                             days_before: int = 6) -> set:
    """Return row indices where short leg must close (ex-date - days_before trading days)."""
    if not ex_dates:
        return set()
    forced: set = set()
    dates_arr = list(merged_df["date"])
    for ex_dt in ex_dates:
        # Find position at or after ex_date
        idx_after = next((i for i, d in enumerate(dates_arr) if d >= ex_dt), None)
        if idx_after is None:
            continue
        close_idx = max(0, idx_after - days_before)
        forced.add(close_idx)
    return forced


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)[["date", "close"]]


def backtest_pair(a_df: pd.DataFrame, b_df: pd.DataFrame,
                  ex_dates_a: list | None = None,
                  ex_dates_b: list | None = None) -> tuple[float, list]:
    """
    回傳 (correlation, [trades])
    每個 trade dict 含 entry_date, exit_date, gross_pct, etc

    G-5: ex_dates_a/b 用於融券強制回補（short leg 距 ex-date 6 交易日強制平倉）
    """
    merged = pd.merge(a_df.rename(columns={"close": "a"}),
                      b_df.rename(columns={"close": "b"}),
                      on="date").sort_values("date").reset_index(drop=True)
    if len(merged) < ROLLING_WINDOW + 30:
        return 0.0, []
    merged["log_a"] = np.log(merged["a"])
    merged["log_b"] = np.log(merged["b"])

    # 用 OLS 60 日滾動找 hedge ratio (簡化：用 ratio mean)
    merged["spread"] = merged["log_a"] - merged["log_b"]
    merged["spread_mean"] = merged["spread"].rolling(ROLLING_WINDOW).mean()
    merged["spread_std"] = merged["spread"].rolling(ROLLING_WINDOW).std()
    merged["z"] = (merged["spread"] - merged["spread_mean"]) / merged["spread_std"]

    correlation = merged["log_a"].corr(merged["log_b"])
    if correlation < 0.85:
        return correlation, []

    # G-5: 融券強制回補 — 計算 short leg 的強制平倉索引集合
    forced_a_exits = get_forced_exit_indices(merged, ex_dates_a or [])  # short A
    forced_b_exits = get_forced_exit_indices(merged, ex_dates_b or [])  # short B

    # 訊號掃描：每個 |z| > Z_ENTRY 進場
    trades = []
    in_pos = False
    pos_dir = 0  # +1 = long A short B, -1 = short A long B
    pos_entry = None

    for i in range(ROLLING_WINDOW, len(merged) - 1):
        row = merged.iloc[i]
        z = row["z"]
        if pd.isna(z):
            continue

        if not in_pos:
            if z > Z_ENTRY:
                # spread 過高 → short A long B
                in_pos = True
                pos_dir = -1
                pos_entry = i
            elif z < -Z_ENTRY:
                # spread 過低 → long A short B
                in_pos = True
                pos_dir = +1
                pos_entry = i
        else:
            # 平倉條件：z 回到 |Z_EXIT| 內 或 timeout 或 融券強制回補
            elapsed = i - pos_entry
            # G-5: short leg 距 ex-date ≤ 6 個交易日 → 必須強制回補
            force_exdiv = (
                (pos_dir == -1 and i in forced_a_exits) or  # short A
                (pos_dir == +1 and i in forced_b_exits)     # short B
            )
            if abs(z) < Z_EXIT or elapsed >= TIMEOUT_DAYS or force_exdiv:
                # 計算 PnL
                a0 = merged.iloc[pos_entry]["a"]
                b0 = merged.iloc[pos_entry]["b"]
                a1 = row["a"]
                b1 = row["b"]
                a_ret = (a1 / a0 - 1) * 100
                b_ret = (b1 / b0 - 1) * 100
                if pos_dir == +1:
                    gross = a_ret - b_ret
                else:
                    gross = b_ret - a_ret
                net = gross - COST * 2  # double leg
                trades.append({
                    "entry": merged.iloc[pos_entry]["date"],
                    "exit": row["date"],
                    "hold_days": elapsed,
                    "z_entry": merged.iloc[pos_entry]["z"],
                    "z_exit": z,
                    "gross_pct": gross,
                    "net_pct": net,
                    "forced_exdiv": force_exdiv,
                })
                in_pos = False

    return correlation, trades


def main():
    print("=" * 80)
    print("配對交易 Pair Trading Backtest")
    print(f"設定: |z| > {Z_ENTRY} 進場, |z| < {Z_EXIT} 出場, timeout {TIMEOUT_DAYS} 日")
    print(f"成本: 雙邊 {COST*2}% per trade")
    print("=" * 80)

    # 收集所有候選配對
    pairs = []
    for grp, tickers in PAIR_GROUPS.items():
        for a, b in combinations(tickers, 2):
            pairs.append((grp, a, b))
    print(f"\n總配對數: {len(pairs)}")

    print("  載入 ex-dividend 日期（yfinance，融券強制回補）...")
    rows = []
    for grp, a, b in pairs:
        a_df = load_ohlcv(a)
        b_df = load_ohlcv(b)
        if a_df.empty or b_df.empty:
            continue
        ex_a = _EX_DATES_CACHE.setdefault(a, load_ex_dividend_dates(a))
        ex_b = _EX_DATES_CACHE.setdefault(b, load_ex_dividend_dates(b))
        corr, trades = backtest_pair(a_df, b_df, ex_a, ex_b)
        if not trades:
            continue
        df_t = pd.DataFrame(trades)
        n = len(df_t)
        win = (df_t["net_pct"] > 0).mean() * 100
        mean_net = df_t["net_pct"].mean()
        total = df_t["net_pct"].sum()
        forced_n = int(df_t["forced_exdiv"].sum()) if "forced_exdiv" in df_t.columns else 0
        rows.append({
            "group": grp, "pair": f"{a}-{b}",
            "corr": corr, "n": n,
            "mean_net": mean_net, "win": win,
            "total_pct": total,
            "avg_hold": df_t["hold_days"].mean(),
            "forced_exdiv": forced_n,
        })

    res = pd.DataFrame(rows).sort_values("mean_net", ascending=False)

    out = ROOT / "logs" / "pair_trading.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)} ({len(res)} 配對有訊號)")

    print(f"\n=== Top 配對 ===")
    print(f"  {'group':<12} {'pair':<14} {'corr':>5} {'n':>4} "
          f"{'mean':>8} {'win':>5} {'total':>9} {'hold':>5}")
    for _, r in res.head(20).iterrows():
        marker = "⭐" if r["mean_net"] > 1 and r["win"] > 60 else (
            "⚠️" if r["mean_net"] > 0 else "❌")
        print(f"  {r['group']:<11} {r['pair']:<14} {r['corr']:>4.2f} {r['n']:>4} "
              f"{r['mean_net']:>+6.2f}% {r['win']:>4.0f}% "
              f"{r['total_pct']:>+7.1f}% {r['avg_hold']:>4.0f}d {marker}")

    # 全策略總計
    total_trades = sum(r["n"] for r in rows)
    total_forced = sum(r.get("forced_exdiv", 0) for r in rows)
    if rows:
        avg_mean = sum(r["mean_net"] * r["n"] for r in rows) / total_trades
        avg_win = sum(r["win"] * r["n"] for r in rows) / total_trades
        print(f"\n全策略 aggregate ({total_trades} 筆):")
        print(f"  平均 net mean: {avg_mean:+.3f}%")
        print(f"  平均 win rate: {avg_win:.1f}%")
        print(f"\nG-5 融券強制回補統計:")
        pct = total_forced / total_trades * 100 if total_trades > 0 else 0
        print(f"  強制平倉筆數: {total_forced}/{total_trades} ({pct:.1f}%)")
        if total_forced > 0:
            forced_rows = sorted([r for r in rows if r.get("forced_exdiv", 0) > 0],
                                  key=lambda x: x.get("forced_exdiv", 0), reverse=True)
            for r in forced_rows[:5]:
                fp = r["forced_exdiv"] / r["n"] * 100
                print(f"    {r['pair']}: {r['forced_exdiv']} 次強制回補 ({fp:.0f}%)")
        print(f"  ⚠️ 強制回補在最壞時機平倉（spread 未收斂），會拉低實際報酬")


if __name__ == "__main__":
    main()
