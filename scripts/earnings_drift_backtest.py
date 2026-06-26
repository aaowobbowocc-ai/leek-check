"""
Earnings Drift / 季報後動能 — 季報公告日後 abnormal return。

研究：
  TW 季報公告（FinMind: TaiwanStockFinancialStatements）後 N 日報酬
  EPS 高成長 vs 低成長股的後續表現

策略：
  Filter: 季 EPS YoY > +30% → buy 公告後隔日 → hold 5/10/20 日
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
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_FS = ROOT / "data" / "cache" / "finmind" / "financial"
CACHE_FS.mkdir(parents=True, exist_ok=True)


def fetch_eps(token: str, ticker: str) -> pd.DataFrame:
    cp = CACHE_FS / f"{ticker}.parquet"
    if cp.exists():
        return pd.read_parquet(cp)
    try:
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "TaiwanStockFinancialStatements",
            "data_id": ticker,
            "start_date": "2023-01-01",
            "end_date": "2026-04-26",
            "token": token,
        }
        r = requests.get(url, params=params, timeout=30)
        payload = r.json()
        if payload.get("status") != 200 or not payload.get("data"):
            return pd.DataFrame()
        df = pd.DataFrame(payload["data"])
        # 找 EPS 欄
        eps_df = df[df["type"] == "EPS"][["date", "value"]].copy()
        eps_df.columns = ["announce_date", "eps"]
        eps_df["announce_date"] = pd.to_datetime(eps_df["announce_date"]).dt.date
        eps_df = eps_df.sort_values("announce_date").reset_index(drop=True)
        eps_df.to_parquet(cp, index=False)
        return eps_df
    except Exception as e:
        print(f"  ❌ {ticker}: {e}")
        return pd.DataFrame()


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def compute_event_alpha(eps_df: pd.DataFrame, ohlcv: pd.DataFrame, hold_days: int) -> pd.DataFrame:
    """
    對每個 EPS 公告日，計算：
      - 隔日進場、hold_days 後賣出
      - signal return
    """
    if eps_df.empty or ohlcv.empty:
        return pd.DataFrame()
    eps_df = eps_df.copy()
    eps_df["eps_yoy"] = eps_df["eps"].pct_change(4)  # 4 季前同期 YoY

    # 篩高成長
    eps_df = eps_df[eps_df["eps_yoy"] > 0.30].copy()  # +30%+
    if eps_df.empty:
        return pd.DataFrame()

    rows = []
    o_dates = list(ohlcv["date"])
    for _, e in eps_df.iterrows():
        ann = e["announce_date"]
        # 找 announce 後第一個交易日
        future = [d for d in o_dates if d > ann]
        if len(future) < hold_days + 1:
            continue
        entry_date = future[0]
        exit_date = future[hold_days]
        entry = float(ohlcv[ohlcv["date"] == entry_date].iloc[0]["open"])
        exit_p = float(ohlcv[ohlcv["date"] == exit_date].iloc[0]["close"])
        ret = (exit_p / entry - 1) * 100
        rows.append({
            "announce_date": ann,
            "eps_yoy": e["eps_yoy"],
            "entry_date": entry_date,
            "exit_date": exit_date,
            "ret_pct": ret,
        })
    return pd.DataFrame(rows)


def random_window_returns(ohlcv: pd.DataFrame, hold_days: int) -> np.ndarray:
    rets = []
    for i in range(len(ohlcv) - hold_days - 2):
        entry = float(ohlcv.iloc[i + 1]["open"])
        exit_p = float(ohlcv.iloc[i + 1 + hold_days]["close"])
        rets.append((exit_p / entry - 1) * 100)
    return np.array(rets)


def main():
    token = os.environ.get("FINMIND_TOKEN") or ""
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    print("=" * 80)
    print("Earnings Drift / 季報後動能 (EPS YoY +30%+)")
    print("=" * 80)

    # 用熱門 ticker（先測試）
    tickers = ["2330", "2317", "2454", "2308", "2376", "2382",
               "3231", "3037", "3017", "8046", "3711", "6669",
               "0050", "00881", "006208"]

    print(f"\n[1/3] 抓 EPS 資料 ({len(tickers)} ticker)...")
    eps_data = {}
    for tk in tickers:
        df = fetch_eps(token, tk)
        if not df.empty:
            eps_data[tk] = df
            print(f"  ✅ {tk}: {len(df)} 筆 EPS")
        time.sleep(0.1)

    if not eps_data:
        print("❌ 無 EPS 資料"); return

    print(f"\n[2/3] 計算 event alpha (vs random window)...")
    rows = []
    for tk, eps in eps_data.items():
        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            continue
        for hold in [3, 5, 10, 20, 30]:
            events = compute_event_alpha(eps, ohlcv, hold)
            if events.empty or len(events) < 3:
                continue
            sig_mean = events["ret_pct"].mean()
            rand = random_window_returns(ohlcv, hold)
            rand_mean = rand.mean()
            rand_std = rand.std()
            true_alpha = sig_mean - rand_mean
            sigma = (true_alpha / (rand_std / np.sqrt(len(events)))) if rand_std > 0 else 0
            rows.append({
                "ticker": tk, "hold_days": hold,
                "n": len(events),
                "sig_mean": sig_mean,
                "rand_mean": rand_mean,
                "true_alpha": true_alpha,
                "sigma": sigma,
            })

    res = pd.DataFrame(rows)
    if res.empty:
        print("❌ 無結果（可能 EPS YoY +30% 樣本太少）"); return

    res = res.sort_values("true_alpha", ascending=False)
    print(f"\n[3/3] 結果")
    print(f"  {'tk':<7} {'hold':>5} {'n':>3} {'sig':>9} {'rand':>9} {'alpha':>9} {'sigma':>7}")
    for _, r in res.iterrows():
        marker = "⭐" if r["sigma"] > 1.96 else ("⚠️" if r["sigma"] > 1 else "❌")
        print(f"  {r['ticker']:<7} {r['hold_days']:>3}d {r['n']:>3} "
              f"{r['sig_mean']:>+7.2f}% {r['rand_mean']:>+7.2f}% "
              f"{r['true_alpha']:>+7.2f}% {r['sigma']:>+6.2f} {marker}")

    out = ROOT / "logs" / "earnings_drift.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
