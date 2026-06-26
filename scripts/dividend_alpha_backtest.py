"""
除權息日 alpha backtest。

研究：
  TW 高股息 ETF / 個股除權息日前後是否有可預測 pattern？

策略候選：
  V1. 除息前 N 日進場 → 除息日賣（搶填息預期）
  V2. 除息日進場 → 後 N 日賣（搶填息）
  V3. 除息前漲幅大者 → 除息後 short（reversal）

驗收：
  excess return vs 0050 baseline，CI 全正
"""
from __future__ import annotations

import io
import os
import sys
import time
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

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

from src.data.finmind_client import FinMindClient  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_DIV = ROOT / "data" / "cache" / "finmind" / "dividend"
CACHE_DIV.mkdir(parents=True, exist_ok=True)
CUTOFF = pd.Timestamp("2025-06-01")
SEED = 42
N_BOOT = 1000

# 高股息 ETF + 個股
TARGETS = ["00919", "00929", "00940", "00878", "00713", "00701", "0056",
           "00881", "00946", "00891", "00892", "0050",
           "2330", "2317", "2454", "2412", "1101", "2002"]


def fetch_dividend(client, ticker: str, start: date, end: date) -> pd.DataFrame:
    """除權息日 + 配息資料 (TaiwanStockDividend)."""
    cp = CACHE_DIV / f"{ticker}.parquet"
    if cp.exists():
        return pd.read_parquet(cp)
    try:
        df = client._fetch_dataset(
            "TaiwanStockDividendResult", ticker, start, end
        )
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df.to_parquet(cp, index=False)
        return df
    except Exception as e:
        print(f"  ❌ {ticker}: {e}")
        return pd.DataFrame()


def fetch_dividend_alt(client, ticker: str, start: date, end: date) -> pd.DataFrame:
    """Alternative: TaiwanStockDividend (公告)"""
    cp = CACHE_DIV / f"{ticker}_announce.parquet"
    if cp.exists():
        return pd.read_parquet(cp)
    try:
        # 直接呼叫 FinMind API
        import requests
        token = os.environ.get("FINMIND_TOKEN") or ""
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "TaiwanStockDividend",
            "data_id": ticker,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "token": token,
        }
        resp = requests.get(url, params=params, timeout=30)
        payload = resp.json()
        if payload.get("status") != 200 or not payload.get("data"):
            return pd.DataFrame()
        df = pd.DataFrame(payload["data"])
        # 找除權息日欄位
        date_col = None
        for c in ["CashExDividendTradingDate", "ExDividendTradingDate", "date"]:
            if c in df.columns:
                date_col = c; break
        if not date_col:
            return pd.DataFrame()
        df["ex_date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date
        df = df.dropna(subset=["ex_date"])
        df.to_parquet(cp, index=False)
        return df
    except Exception as e:
        print(f"  alt failed {ticker}: {e}")
        return pd.DataFrame()


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def compute_event_returns(ex_dates: list, ohlcv: pd.DataFrame, baseline: pd.DataFrame,
                          before: int, after: int) -> pd.DataFrame:
    """
    對每個除息日，計算：
      - before/after 期間 ticker 報酬
      - 同期 0050 報酬
      - excess
    """
    rows = []
    o = ohlcv.set_index("date").sort_index()
    b = baseline.set_index("date").sort_index()
    o_dates = list(o.index)
    b_dates = list(b.index)

    for ex in ex_dates:
        # 找 ex_date 前 before 日 / 後 after 日
        try:
            ex_idx = o_dates.index(ex)
        except ValueError:
            # 不在交易日，找最近
            ex = next((d for d in o_dates if d >= ex), None)
            if ex is None:
                continue
            ex_idx = o_dates.index(ex)

        start_idx = ex_idx - before
        end_idx = ex_idx + after
        if start_idx < 0 or end_idx >= len(o_dates):
            continue

        entry_date = o_dates[start_idx]
        exit_date = o_dates[end_idx]

        entry_close = float(o.loc[entry_date, "close"])
        exit_close = float(o.loc[exit_date, "close"])
        s_ret = (exit_close / entry_close - 1) * 100

        # baseline (0050)
        try:
            b_entry_idx = b_dates.index(entry_date)
            b_exit_idx = b_dates.index(exit_date)
            b_entry = float(b.iloc[b_entry_idx]["close"])
            b_exit = float(b.iloc[b_exit_idx]["close"])
            b_ret = (b_exit / b_entry - 1) * 100
        except (ValueError, KeyError):
            continue

        rows.append({
            "ex_date": ex, "entry_date": entry_date, "exit_date": exit_date,
            "s_ret": s_ret, "b_ret": b_ret, "excess": s_ret - b_ret,
        })
    return pd.DataFrame(rows)


def stats(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"n": 0, "excess_mean": np.nan, "win": np.nan,
                "ci_low": np.nan, "ci_high": np.nan,
                "raw_mean": np.nan, "baseline_mean": np.nan}
    rng = np.random.default_rng(SEED)
    excess = df["excess"].values
    if n >= 5:
        boot = np.array([rng.choice(excess, size=n, replace=True).mean() for _ in range(N_BOOT)])
        ci_low, ci_high = np.percentile(boot, [2.5, 97.5])
    else:
        ci_low = ci_high = np.nan
    return {
        "n": n,
        "excess_mean": excess.mean(),
        "win": (excess > 0).mean() * 100,
        "ci_low": ci_low, "ci_high": ci_high,
        "raw_mean": df["s_ret"].mean(),
        "baseline_mean": df["b_ret"].mean(),
    }


def main():
    token = os.environ.get("FINMIND_TOKEN") or ""
    if not token:
        print("❌ FINMIND_TOKEN not set"); return
    client = FinMindClient(token=token)

    print("=" * 80)
    print("除權息日 Alpha Backtest")
    print("=" * 80)

    baseline = load_ohlcv("0050")
    if baseline.empty:
        print("❌ 0050 cache 不存在"); return
    print(f"\n0050 baseline: {len(baseline)} days")

    # 抓 dividend events
    print(f"\n[1/3] 抓除權息資料 ({len(TARGETS)} 標的)...")
    events = {}
    for tk in TARGETS:
        df = fetch_dividend_alt(client, tk, date(2024, 1, 1), date(2026, 4, 26))
        if not df.empty and "ex_date" in df.columns:
            ex_dates = sorted(df["ex_date"].dropna().unique().tolist())
            ex_dates = [d for d in ex_dates if d >= date(2024, 1, 1) and d <= date(2026, 4, 26)]
            if ex_dates:
                events[tk] = ex_dates
                print(f"  ✅ {tk}: {len(ex_dates)} 次除息")

    if not events:
        print("❌ 無除息資料"); return

    # 跑變體
    print(f"\n[2/3] 跑變體（before / after × ticker）...")
    rows = []
    for tk, ex_list in events.items():
        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            continue
        for before in [0, 3, 5, 10]:
            for after in [3, 5, 10, 20]:
                event_returns = compute_event_returns(ex_list, ohlcv, baseline, before, after)
                if event_returns.empty:
                    continue
                st = stats(event_returns)
                rows.append({
                    "ticker": tk,
                    "before": before, "after": after,
                    **st,
                })

    res = pd.DataFrame(rows)
    if res.empty:
        print("❌ 無結果"); return

    def tier(r):
        if r["n"] >= 10 and r["excess_mean"] > 0 and r["ci_low"] > 0:
            return "A"
        if r["n"] >= 5 and r["excess_mean"] > 0 and r["ci_low"] > -0.5:
            return "B"
        return "C"
    res["tier"] = res.apply(tier, axis=1)

    out_csv = ROOT / "logs" / "dividend_alpha.csv"
    res.sort_values("excess_mean", ascending=False).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[3/3] 寫入 {out_csv.relative_to(ROOT)} ({len(res)} rows)")

    a = res[res["tier"] == "A"].sort_values("excess_mean", ascending=False)
    b = res[res["tier"] == "B"].sort_values("excess_mean", ascending=False)
    print(f"\nTier A: {len(a)}, Tier B: {len(b)}, Tier C: {len(res)-len(a)-len(b)}")

    if not a.empty:
        print("\n=== Tier A ===")
        print(f"  {'tk':<7} {'before':>6} {'after':>5} {'n':>3} "
              f"{'excess':>7} {'win':>5} {'raw':>7} {'base':>7} {'CI':>20}")
        for _, r in a.head(20).iterrows():
            print(f"  {r['ticker']:<7} {r['before']:>5}d {r['after']:>4}d {r['n']:>3} "
                  f"{r['excess_mean']:>+5.2f}% {r['win']:>4.0f}% "
                  f"{r['raw_mean']:>+5.2f}% {r['baseline_mean']:>+5.2f}% "
                  f"[{r['ci_low']:>+5.2f}, {r['ci_high']:>+5.2f}]")

    if not b.empty:
        print("\n=== Tier B (top 10) ===")
        for _, r in b.head(10).iterrows():
            print(f"  {r['ticker']:<7} {r['before']:>5}d {r['after']:>4}d {r['n']:>3} "
                  f"{r['excess_mean']:>+5.2f}% {r['win']:>4.0f}% "
                  f"[{r['ci_low']:>+5.2f}, {r['ci_high']:>+5.2f}]")


if __name__ == "__main__":
    main()
