"""
散戶比例極值反向 rescue test (9 年 cross-regime)

Memory 說：weekly 9991 sample → 散戶比例極端區 lift +11.3pp p<0.001
但沒做 daily 9 年 cross-regime 驗證

資料：TaiwanStockHoldingSharesPer (週度大戶持股分級)
邏輯：
  - 散戶 = 持股 1-50 張 (小戶) 的 percent 比例
  - 散戶比例極高 (>80% percentile) → 後續 fwd return 弱（反向 short）
  - 散戶比例極低 (<20% percentile) → 後續 fwd return 強（反向 long）
跨 4 期分層驗證 + MCPT
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
CACHE_HOLD = ROOT / "data" / "cache" / "finmind" / "holding"
CACHE_HOLD.mkdir(parents=True, exist_ok=True)
SEED = 42
N_BOOT = 500

PERIODS = [
    ("A 2017-2019", date(2017, 1, 1), date(2019, 12, 31)),
    ("B 2020 covid", date(2020, 1, 1), date(2020, 12, 31)),
    ("C 2021-2022 熊", date(2021, 1, 1), date(2022, 12, 31)),
    ("D 2023-2026 牛", date(2023, 1, 1), date(2026, 4, 30)),
]


def fetch_holding(token: str, ticker: str) -> pd.DataFrame:
    cp = CACHE_HOLD / f"{ticker}.parquet"
    if cp.exists():
        return pd.read_parquet(cp)
    rows = []
    # 抓 2017-2026 分批（每年一次避免 timeout）
    for year in range(2017, 2027):
        try:
            r = requests.get("https://api.finmindtrade.com/api/v4/data",
                             params={"dataset": "TaiwanStockHoldingSharesPer",
                                     "data_id": ticker,
                                     "start_date": f"{year}-01-01",
                                     "end_date": f"{year}-12-31",
                                     "token": token}, timeout=30)
            p = r.json()
            if p.get("status") == 200 and p.get("data"):
                rows.extend(p["data"])
        except Exception as e:
            print(f"    {ticker} {year}: {e}")
        time.sleep(0.1)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.to_parquet(cp, index=False)
    return df


def load_ohlcv(tk):
    p = CACHE_YF / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def parse_level(level: str) -> tuple[int, int]:
    """Parse '40,001-50,000' or '1-999' or 'more than 1,000,001' → (low, high)."""
    if not isinstance(level, str):
        return (-1, -1)
    s = level.replace(",", "").strip()
    if "more than" in s.lower():
        try:
            n = int("".join(c for c in s if c.isdigit()))
            return (n, 99999999)
        except: return (-1, -1)
    if "-" in s:
        parts = s.split("-")
        try:
            return (int(parts[0]), int(parts[1]))
        except: return (-1, -1)
    if s.lower() == "total" or "差異" in level:
        return (-1, -1)
    return (-1, -1)


def compute_retail_pct(df: pd.DataFrame) -> pd.DataFrame:
    """
    Range parse:
      散戶 = level low < 1000 (含 '1-999')
      中戶 = 1,000 - 50,000
      大戶 = > 50,000
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["percent"] = pd.to_numeric(df["percent"], errors="coerce")
    df = df.dropna(subset=["percent"])

    df["level_low"] = df["HoldingSharesLevel"].apply(lambda x: parse_level(x)[0])
    df = df[df["level_low"] >= 0]  # 排除 'total' 和差異說明

    out = []
    for d, sub in df.groupby("date"):
        retail = sub[sub["level_low"] < 1000]["percent"].sum()
        medium = sub[(sub["level_low"] >= 1000) & (sub["level_low"] < 50000)]["percent"].sum()
        large = sub[sub["level_low"] >= 50000]["percent"].sum()
        out.append({"date": d, "retail_pct": retail,
                    "medium_pct": medium, "large_pct": large,
                    "small_plus_retail": retail})
    res = pd.DataFrame(out)
    if res.empty:
        return res
    return res.sort_values("date").reset_index(drop=True)


def test_signal(ohlcv: pd.DataFrame, weekly: pd.DataFrame,
                signal_col: str, hold_days: int) -> pd.DataFrame:
    """
    以散戶比例 z-score 跨期判斷:
      訊號 high (>2σ) → 隔日 short
      訊號 low (<-2σ) → 隔日 long
    回傳 (date, signal_z, fwd_return)
    """
    if weekly.empty or ohlcv.empty:
        return pd.DataFrame()
    weekly = weekly.copy()
    # rolling 26 週 (~6 個月) z-score
    weekly["mean"] = weekly[signal_col].rolling(26).mean()
    weekly["std"] = weekly[signal_col].rolling(26).std()
    weekly["z"] = (weekly[signal_col] - weekly["mean"]) / weekly["std"]
    weekly = weekly.dropna(subset=["z"])

    o_dates = list(ohlcv["date"])
    rows = []
    for _, w in weekly.iterrows():
        d = w["date"]
        # 找下一個交易日
        future = [x for x in o_dates if x >= d]
        if len(future) < hold_days + 2: continue
        d0 = future[0]
        idx0 = o_dates.index(d0)
        if idx0 + 1 + hold_days >= len(o_dates): continue
        entry = float(ohlcv.iloc[idx0 + 1]["open"])
        exit_p = float(ohlcv.iloc[idx0 + 1 + hold_days]["close"])
        fwd = (exit_p / entry - 1) * 100
        rows.append({"date": d, "z": w["z"], "fwd_ret": fwd})
    return pd.DataFrame(rows)


def stratify_by_quintile(df: pd.DataFrame, ohlcv: pd.DataFrame, period_label, start, end):
    """同期間 quintile alpha"""
    p = df[(df["date"] >= start) & (df["date"] <= end)]
    if len(p) < 20:
        return None
    # 按 z 分 quintile
    q1, q2, q3, q4 = p["z"].quantile([0.2, 0.4, 0.6, 0.8])
    p = p.copy()
    try:
        p["quintile"] = pd.qcut(p["z"], 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
    except Exception:
        return None
    out = {}
    for q in [1, 2, 3, 4, 5]:
        sub = p[p["quintile"] == q]
        if sub.empty: continue
        out[q] = {"n": len(sub), "mean": sub["fwd_ret"].mean(),
                  "win": (sub["fwd_ret"] > 0).mean() * 100}
    # spread = Q1 (最低散戶) - Q5 (最高散戶)
    if 1 in out and 5 in out:
        spread = out[1]["mean"] - out[5]["mean"]
        return {"period": period_label, "Q1": out[1], "Q5": out[5], "spread": spread,
                "n_total": len(p)}
    return None


def main():
    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    targets = ["2330", "2317", "2454", "2308", "2376", "0050", "00881", "006208",
               "2408", "2485", "3231", "3037", "2345"]

    print("=" * 90)
    print("散戶比例極值反向 rescue test")
    print("=" * 90)

    print(f"\n[1/3] 抓 {len(targets)} ticker × 9 年大戶持股資料...")
    holding_data = {}
    t0 = time.time()
    for i, tk in enumerate(targets):
        df = fetch_holding(token, tk)
        if not df.empty:
            holding_data[tk] = df
            n_dates = df["date"].nunique() if "date" in df.columns else 0
            print(f"  ✅ {tk}: {len(df)} rows, {n_dates} 日期")
        else:
            print(f"  ❌ {tk}: 無資料")
        time.sleep(0.2)
    print(f"  完成 {time.time()-t0:.0f}s")

    if not holding_data:
        print("❌ 無資料"); return

    print(f"\n[2/3] 計算散戶比例 + cross-regime test...")
    print(f"\n{'ticker':<7} {'period':<14} {'Q1 mean':>9} {'Q5 mean':>9} "
          f"{'spread Q1-Q5':>13} {'verdict':>10}")
    print("-" * 80)

    all_results = []
    for tk, hold_df in holding_data.items():
        ohlcv = load_ohlcv(tk)
        if ohlcv.empty: continue
        weekly = compute_retail_pct(hold_df)
        if weekly.empty: continue

        sig = test_signal(ohlcv, weekly, "small_plus_retail", hold_days=20)
        if sig.empty: continue

        for label, start, end in PERIODS:
            r = stratify_by_quintile(sig, ohlcv, label, start, end)
            if r is None: continue
            spread = r["spread"]
            mark = "✅" if spread > 2 else ("⚠️" if spread > 0 else "❌")
            print(f"  {tk:<6} {label:<13} "
                  f"{r['Q1']['mean']:>+7.2f}% {r['Q5']['mean']:>+7.2f}% "
                  f"{spread:>+11.2f}pp {mark:>10}")
            all_results.append({
                "ticker": tk, "period": label,
                "Q1_mean": r["Q1"]["mean"], "Q5_mean": r["Q5"]["mean"],
                "spread": spread, "n": r["n_total"]
            })

    print(f"\n[3/3] 結論")
    res = pd.DataFrame(all_results)
    if not res.empty:
        # 按 ticker 統計：4 期都正才是真 robust
        robust = res.groupby("ticker").agg(
            n_periods=("period", "count"),
            n_robust=("spread", lambda x: (x > 2).sum()),
            avg_spread=("spread", "mean"),
        )
        print(f"\n各 ticker 跨期分析（spread > 2pp 算 robust）:")
        print(robust.sort_values("n_robust", ascending=False).to_string())
        out = ROOT / "logs" / "retail_concentration_rescue.csv"
        res.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
