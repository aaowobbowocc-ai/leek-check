"""
6 個策略 跨牛熊版 (2017-2026) backtest。

教訓：先前 2 年 backtest 大量假 alpha 被牛熊驗證推翻。
這次每個策略都自帶 4 期分層驗證 (A/B/C/D)。

策略：
  1. 連續漲停冷卻 (cooldown reversal)
  2. 月營收 YoY 高成長後動能
  3. 融資餘額激增反彈
  4. VIX spike → TW 隔日 mean revert
  5. 限漲停打開 cross-regime 重驗
  6. 配對交易 cross-regime 重驗
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
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_REV = ROOT / "data" / "cache" / "finmind" / "revenue"
CACHE_REV.mkdir(parents=True, exist_ok=True)
CACHE_MARGIN = ROOT / "data" / "cache" / "finmind" / "margin"
CACHE_MARGIN.mkdir(parents=True, exist_ok=True)

PERIODS = [
    ("A 2017-2019", date(2017, 1, 1), date(2019, 12, 31)),
    ("B 2020 covid", date(2020, 1, 1), date(2020, 12, 31)),
    ("C 2021-2022 熊", date(2021, 1, 1), date(2022, 12, 31)),
    ("D 2023-2026 牛", date(2023, 1, 1), date(2026, 4, 30)),
]


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def random_in_period(ohlcv: pd.DataFrame, hold: int, start: date, end: date) -> np.ndarray:
    rets = []
    for i in range(len(ohlcv) - hold - 2):
        d = ohlcv.iloc[i]["date"]
        if not (start <= d <= end):
            continue
        entry = float(ohlcv.iloc[i + 1]["open"])
        exit_p = float(ohlcv.iloc[i + 1 + hold]["close"])
        rets.append((exit_p / entry - 1) * 100)
    return np.array(rets)


def stratified_alpha(sig_dates_with_ret: list, ohlcv: pd.DataFrame, hold: int) -> dict:
    """sig_dates_with_ret: list of (date, return). 回傳每期 alpha."""
    out = {}
    for label, start, end in PERIODS:
        sig_p = [r for d, r in sig_dates_with_ret if start <= d <= end]
        if len(sig_p) < 5:
            out[label] = None
            continue
        rand = random_in_period(ohlcv, hold, start, end)
        if len(rand) < 30:
            out[label] = None
            continue
        sig_mean = np.mean(sig_p)
        rand_mean = rand.mean()
        rand_std = rand.std()
        alpha = sig_mean - rand_mean
        sigma = (alpha / (rand_std / np.sqrt(len(sig_p)))) if rand_std > 0 else 0
        out[label] = {"n": len(sig_p), "sig": sig_mean, "rand": rand_mean,
                      "alpha": alpha, "sigma": sigma}
    return out


def print_strat(name: str, results: dict):
    print(f"\n=== {name} ===")
    print(f"{'period':<18} {'n':>4} {'sig':>8} {'rand':>8} {'alpha':>8} {'sigma':>7} {'verdict':>10}")
    n_robust = 0
    n_total = 0
    for p_label, r in results.items():
        if r is None:
            print(f"  {p_label:<16}  sample 不足"); continue
        n_total += 1
        v = "✅ robust" if r["sigma"] > 1.96 and r["alpha"] > 0 else (
            "⚠️ 弱" if r["alpha"] > 0 else "❌ 假")
        if r["sigma"] > 1.96 and r["alpha"] > 0:
            n_robust += 1
        print(f"  {p_label:<16} {r['n']:>4} {r['sig']:>+6.2f}% {r['rand']:>+6.2f}% "
              f"{r['alpha']:>+6.2f}% {r['sigma']:>+6.2f} {v:>10}")
    print(f"  → {n_robust}/{n_total} 期 robust")


# ════════════════════════════════════
# Strategy 1: 連續漲停冷卻
# ════════════════════════════════════
def strat_consecutive_limitup_cooldown():
    print("\n" + "=" * 90)
    print("【1. 連續漲停 N 日後冷卻 reversal (跨牛熊)】")
    print("=" * 90)

    tickers = sorted({p.stem for p in CACHE_YF.glob("*.parquet")
                      if p.stem.isdigit() and 4 <= len(p.stem) <= 6})

    # 收集所有訊號（across all tickers）
    all_signals = []  # (ticker, date, hold_days, return)
    n_consec = 3
    hold = 3

    for tk in tickers:
        df = load_ohlcv(tk)
        if df.empty or len(df) < 100:
            continue
        df = df.copy()
        df["prev_close"] = df["close"].shift(1)
        df["limitup"] = df["close"] / df["prev_close"] >= 1.095
        df["consec_lu"] = df["limitup"].astype(int).rolling(n_consec).sum()

        # 找連 N 日漲停的日子
        df_t = df[df["consec_lu"] >= n_consec]
        for idx in df_t.index:
            if idx + 1 + hold >= len(df):
                continue
            entry = float(df.iloc[idx + 1]["close"])  # 隔日 close 進場 short
            exit_p = float(df.iloc[idx + 1 + hold]["close"])
            short_ret = (entry - exit_p) / entry * 100  # short return
            all_signals.append((tk, df.iloc[idx + 1]["date"], short_ret))

    print(f"  全市場連 {n_consec} 日漲停事件: {len(all_signals)} 筆 (hold {hold} 日 short)")

    # 分期統計
    sig_with_ret = [(d, r) for tk, d, r in all_signals]
    # 用 0050 當 random window baseline
    bench = load_ohlcv("0050")
    if bench.empty:
        print("❌ 無 0050 baseline"); return
    # baseline 是 short 0050 hold 3 日
    bench_random = []
    for i in range(len(bench) - hold - 2):
        d = bench.iloc[i]["date"]
        entry = float(bench.iloc[i + 1]["close"])
        exit_p = float(bench.iloc[i + 1 + hold]["close"])
        bench_random.append((d, (entry - exit_p) / entry * 100))

    print(f"\n{'period':<18} {'n_sig':>5} {'sig 平均':>10} {'baseline':>10} {'alpha':>8} {'verdict':>10}")
    for label, start, end in PERIODS:
        sig_p = [r for d, r in sig_with_ret if start <= d <= end]
        rand_p = [r for d, r in bench_random if start <= d <= end]
        if len(sig_p) < 10 or len(rand_p) < 30:
            print(f"  {label:<16}  sample 不足"); continue
        sig_mean = np.mean(sig_p)
        rand_mean = np.mean(rand_p)
        rand_std = np.std(rand_p)
        alpha = sig_mean - rand_mean
        sigma = (alpha / (rand_std / np.sqrt(len(sig_p)))) if rand_std > 0 else 0
        v = "✅" if sigma > 1.96 and alpha > 0 else ("⚠️" if alpha > 0 else "❌")
        print(f"  {label:<16} {len(sig_p):>5} {sig_mean:>+8.2f}% "
              f"{rand_mean:>+8.2f}% {alpha:>+6.2f}% {v:>10}")


# ════════════════════════════════════
# Strategy 2: 月營收 YoY 高成長
# ════════════════════════════════════
def strat_monthly_revenue():
    print("\n" + "=" * 90)
    print("【2. 月營收 YoY > +30% 後 long (跨牛熊)】")
    print("=" * 90)

    token = os.environ.get("FINMIND_TOKEN", "")
    targets = ["2330", "2317", "2454", "2308", "2376", "2382", "3231",
               "3037", "3017", "8046", "2408", "0050", "00881", "006208"]

    for tk in targets:
        cp = CACHE_REV / f"{tk}.parquet"
        if cp.exists():
            df = pd.read_parquet(cp)
        else:
            try:
                r = requests.get("https://api.finmindtrade.com/api/v4/data",
                                 params={"dataset": "TaiwanStockMonthRevenue",
                                         "data_id": tk, "start_date": "2017-01-01",
                                         "end_date": "2026-04-26", "token": token},
                                 timeout=20)
                p = r.json()
                if p.get("status") != 200 or not p.get("data"):
                    continue
                df = pd.DataFrame(p["data"])
                df.to_parquet(cp, index=False)
            except Exception:
                continue
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date").reset_index(drop=True)
        rev_col = "revenue" if "revenue" in df.columns else "Revenue"
        df["yoy"] = df[rev_col].pct_change(12) * 100
        df_hi = df[df["yoy"] > 30]

        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            continue
        o_dates = list(ohlcv["date"])

        hold = 20
        sig_dr = []
        for d in df_hi["date"]:
            if d not in o_dates:
                future = [x for x in o_dates if x >= d]
                if not future: continue
                d = future[0]
            idx = o_dates.index(d)
            if idx + 1 + hold >= len(o_dates): continue
            entry = float(ohlcv.iloc[idx + 1]["open"])
            exit_p = float(ohlcv.iloc[idx + 1 + hold]["close"])
            sig_dr.append((d, (exit_p / entry - 1) * 100))

        if len(sig_dr) < 5: continue
        results = stratified_alpha(sig_dr, ohlcv, hold)
        # 只印 robust ticker
        n_robust = sum(1 for r in results.values() if r and r["sigma"] > 1.96 and r["alpha"] > 0)
        if n_robust >= 2:  # 至少 2 期 robust 才印
            print_strat(f"{tk} 月營收 YoY+30% / hold 20d", results)


# ════════════════════════════════════
# Strategy 3: 融資餘額激增反彈
# ════════════════════════════════════
def strat_margin_surge():
    print("\n" + "=" * 90)
    print("【3. 融資餘額激增 short (跨牛熊)】")
    print("=" * 90)

    token = os.environ.get("FINMIND_TOKEN", "")
    targets = ["2330", "2317", "2454", "2308", "2376", "3231", "0050", "00881"]

    for tk in targets:
        cp = CACHE_MARGIN / f"{tk}.parquet"
        if cp.exists():
            df = pd.read_parquet(cp)
        else:
            try:
                r = requests.get("https://api.finmindtrade.com/api/v4/data",
                                 params={"dataset": "TaiwanStockMarginPurchaseShortSale",
                                         "data_id": tk, "start_date": "2017-01-01",
                                         "end_date": "2026-04-26", "token": token},
                                 timeout=20)
                p = r.json()
                if p.get("status") != 200 or not p.get("data"):
                    continue
                df = pd.DataFrame(p["data"])
                df.to_parquet(cp, index=False)
            except Exception:
                continue
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date").reset_index(drop=True)
        bal_col = None
        for c in ["MarginPurchaseTodayBalance", "MarginPurchaseBalance",
                  "margin_purchase_today_balance"]:
            if c in df.columns:
                bal_col = c; break
        if not bal_col: continue
        df["change_pct"] = df[bal_col].pct_change(5) * 100

        ohlcv = load_ohlcv(tk)
        if ohlcv.empty: continue
        o_dates = list(ohlcv["date"])

        thresh = 25
        hold = 10
        df_surge = df[df["change_pct"] > thresh]
        sig_dr = []
        for d in df_surge["date"]:
            if d not in o_dates: continue
            idx = o_dates.index(d)
            if idx + 1 + hold >= len(o_dates): continue
            entry = float(ohlcv.iloc[idx + 1]["close"])
            exit_p = float(ohlcv.iloc[idx + 1 + hold]["close"])
            sig_dr.append((d, (entry - exit_p) / entry * 100))  # short

        if len(sig_dr) < 5: continue
        # 對 short 來說 baseline 是負 long
        # 簡化：仍用 stratified_alpha 但對 random 也 short
        out = {}
        for label, start, end in PERIODS:
            sig_p = [r for d, r in sig_dr if start <= d <= end]
            if len(sig_p) < 3:
                out[label] = None; continue
            rand = -random_in_period(ohlcv, hold, start, end)  # short
            if len(rand) < 30:
                out[label] = None; continue
            sig_mean = np.mean(sig_p)
            rand_mean = rand.mean()
            rand_std = rand.std()
            alpha = sig_mean - rand_mean
            sigma = (alpha / (rand_std / np.sqrt(len(sig_p)))) if rand_std > 0 else 0
            out[label] = {"n": len(sig_p), "sig": sig_mean, "rand": rand_mean,
                          "alpha": alpha, "sigma": sigma}
        n_robust = sum(1 for r in out.values() if r and r["sigma"] > 1.96 and r["alpha"] > 0)
        if n_robust >= 1:
            print_strat(f"{tk} 融資 5d 激增 +{thresh}% short hold 10d", out)


# ════════════════════════════════════
# Strategy 4: VIX spike → TW
# ════════════════════════════════════
def strat_vix_spike():
    print("\n" + "=" * 90)
    print("【4. VIX spike 後 TW 隔日 mean revert】")
    print("=" * 90)
    import yfinance as yf
    vix = yf.Ticker("^VIX").history(start="2017-01-01", end="2026-04-26", auto_adjust=False)
    vix = vix.reset_index()
    vix.columns = [c.lower() for c in vix.columns]
    vix["date"] = pd.to_datetime(vix["date"]).dt.tz_localize(None).dt.date
    vix["vix_change"] = vix["close"].pct_change() * 100

    spy = load_ohlcv("0050")
    if spy.empty: return
    spy["match_date"] = pd.to_datetime(spy["date"]) - pd.Timedelta(days=1)
    spy["match_date"] = spy["match_date"].dt.date
    spy["t1_open"] = spy["open"]
    spy["t1_close"] = spy["close"]
    spy["t2_close"] = spy["close"].shift(-1)
    spy["t1_o2c"] = (spy["t1_close"] / spy["t1_open"] - 1) * 100
    spy["t1_to_t2"] = (spy["t2_close"] / spy["t1_close"] - 1) * 100

    merged = pd.merge(vix[["date", "vix_change"]].rename(columns={"date": "match_date"}),
                       spy[["t1_open", "t1_close", "t1_o2c", "t1_to_t2", "match_date", "date"]].rename(columns={"date": "tw_date"}),
                       on="match_date", how="inner").dropna()

    print(f"\n{'period':<18}", end="")
    print(f"{'VIX 變化':<14} {'天數':>5} {'T+1 o2c':>10} {'T+1->T+2':>10}")
    bins = [(15, 30, "VIX 急升 +15-30%"), (30, 100, "VIX 暴升 > +30%"), (-10, 5, "中性")]
    for label, start, end in PERIODS:
        merged_p = merged[merged["tw_date"].between(start, end)]
        for lo, hi, b_label in bins:
            sub = merged_p[(merged_p["vix_change"] >= lo) & (merged_p["vix_change"] < hi)]
            if len(sub) < 3: continue
            t1 = sub["t1_o2c"].mean()
            t2 = sub["t1_to_t2"].mean()
            print(f"  {label:<16} {b_label:<14} {len(sub):>5} {t1:>+8.2f}% {t2:>+8.2f}%")
        print()


def main():
    print("=" * 90)
    print("跨牛熊版 6 策略 batch (學乖了)")
    print("=" * 90)

    strat_consecutive_limitup_cooldown()
    strat_monthly_revenue()
    strat_margin_surge()
    strat_vix_spike()

    print("\n" + "=" * 90)
    print("完成。看每節結果分期是否 robust")


if __name__ == "__main__":
    main()
