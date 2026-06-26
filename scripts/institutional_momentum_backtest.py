"""
三大法人連續買超 Momentum Backtest。

研究問題：
  1. 外資 / 投信 / 自營商 連 N 日買超 → 後 5/10/20 日報酬？
  2. 連續買超天數的 alpha 邊際效用？
  3. 哪類法人訊號最強？

策略候選：
  V1. 外資連 N 日買超 → 隔日 long，持有 5/10/20 日
  V2. 投信連 N 日買超 → 同上
  V3. 三大法人都買超 → confluence signal

驗收：
  - OOS test mean > 0 + CI low > 0 + n >= 30 → Tier A
  - test_n >= 10 + mean > 0 → Tier B (watchlist)
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
CACHE_INST = ROOT / "data" / "cache" / "finmind" / "institutional"
CACHE_INST.mkdir(parents=True, exist_ok=True)

COST = 0.34
CUTOFF = pd.Timestamp("2025-06-01")
SEED = 42
N_BOOT = 500

# 限制在已有 institutional cache 的 ticker（避免再 backfill）
TICKERS = sorted({p.stem for p in CACHE_INST.glob("*.parquet")})


def get_institutional(client, ticker: str, start: date, end: date) -> pd.DataFrame:
    """從 cache 讀，沒有則 fetch + cache。"""
    cp = CACHE_INST / f"{ticker}.parquet"
    if cp.exists():
        df = pd.read_parquet(cp)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df
    try:
        df = client.get_institutional(ticker, start, end)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df.to_parquet(cp, index=False)
        return df
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


def compute_signal(inst_df: pd.DataFrame, ohlcv: pd.DataFrame, n_consec: int,
                   investor: str, hold_days: int) -> pd.DataFrame:
    """
    investor: 'foreign' / 'investment_trust' / 'dealer' / 'all_three'
    inst_df 結構：每天每法人一列 (name 欄區分)
    """
    if inst_df.empty or ohlcv.empty:
        return pd.DataFrame()

    name_map = {
        "foreign": "Foreign_Investor",
        "investment_trust": "Investment_Trust",
        "dealer": "Dealer_self",
    }

    # Pivot：每天一列，各法人 net_buy 為欄
    pivot = inst_df.pivot_table(
        index="date", columns="name", values="net_buy", aggfunc="sum"
    ).reset_index()
    pivot.columns.name = None
    pivot = pivot.sort_values("date").reset_index(drop=True)

    if investor == "all_three":
        cols_needed = ["Foreign_Investor", "Investment_Trust", "Dealer_self"]
        if not all(c in pivot.columns for c in cols_needed):
            return pd.DataFrame()
        pivot["is_buy"] = (
            (pivot["Foreign_Investor"] > 0) &
            (pivot["Investment_Trust"] > 0) &
            (pivot["Dealer_self"] > 0)
        )
    else:
        col = name_map.get(investor)
        if col not in pivot.columns:
            return pd.DataFrame()
        pivot["is_buy"] = pivot[col] > 0

    pivot["consec"] = pivot["is_buy"].astype(int).rolling(n_consec).sum()
    pivot["trigger"] = pivot["consec"] == n_consec
    inst = pivot

    # 對每個 trigger，計算 entry 日 (T+1 開盤) 持有 hold_days 後 close
    ohlcv = ohlcv.copy()
    ohlcv["next_open"] = ohlcv["open"].shift(-1)
    ohlcv["future_close"] = ohlcv["close"].shift(-hold_days)

    merged = pd.merge(
        inst[["date", "trigger"]], ohlcv[["date", "close", "next_open", "future_close"]],
        on="date", how="inner"
    )
    triggered = merged[merged["trigger"]].copy()
    triggered = triggered.dropna(subset=["next_open", "future_close"])
    if triggered.empty:
        return pd.DataFrame()

    # 隔日開盤買 → 持有 hold_days
    triggered["gross_pct"] = (triggered["future_close"] / triggered["next_open"] - 1) * 100
    triggered["net_pct"] = triggered["gross_pct"] - COST  # 一筆來回成本
    return triggered[["date", "next_open", "future_close", "gross_pct", "net_pct"]]


def stats_walk_forward(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"n": 0, "full_mean": np.nan, "full_win": np.nan,
                "test_n": 0, "test_mean": np.nan, "test_win": np.nan,
                "ci_low": np.nan, "ci_high": np.nan}
    rets = df["net_pct"].values
    test = df[pd.to_datetime(df["date"]) >= CUTOFF]
    rng = np.random.default_rng(SEED)
    if n >= 5:
        boot = np.array([rng.choice(rets, size=n, replace=True).mean() for _ in range(N_BOOT)])
        ci_low, ci_high = np.percentile(boot, [2.5, 97.5])
    else:
        ci_low, ci_high = np.nan, np.nan
    return {
        "n": n,
        "full_mean": rets.mean(),
        "full_win": (rets > 0).mean() * 100,
        "test_n": len(test),
        "test_mean": test["net_pct"].mean() if len(test) else np.nan,
        "test_win": (test["net_pct"] > 0).mean() * 100 if len(test) else np.nan,
        "ci_low": ci_low,
        "ci_high": ci_high,
    }


def main():
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_KEY") or ""
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    print(f"=== 三大法人 momentum backtest | {len(TICKERS)} ticker ===")

    client = FinMindClient(token=token)
    start, end = date(2024, 1, 1), date(2026, 4, 26)

    # Backfill institutional data
    print(f"\n[1/3] 抓 institutional data...")
    t0 = time.time()
    inst_data = {}
    ohlcv_data = {}
    for i, tk in enumerate(TICKERS):
        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            continue
        ohlcv_data[tk] = ohlcv
        inst = get_institutional(client, tk, start, end)
        if not inst.empty:
            inst_data[tk] = inst
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(TICKERS)}] {time.time()-t0:.0f}s")
    print(f"  完成 {len(inst_data)} ticker / {time.time()-t0:.0f}s")

    # 跑變體
    print(f"\n[2/3] 跑變體...")
    rows = []
    for tk, inst in inst_data.items():
        ohlcv = ohlcv_data[tk]
        for investor in ["foreign", "investment_trust", "dealer", "all_three"]:
            for n_consec in [3, 5, 7]:
                for hold in [5, 10, 20]:
                    trades = compute_signal(inst, ohlcv, n_consec, investor, hold)
                    st = stats_walk_forward(trades)
                    if st["n"] >= 10:
                        rows.append({
                            "ticker": tk,
                            "investor": investor,
                            "n_consec": n_consec,
                            "hold_days": hold,
                            **st,
                        })

    res = pd.DataFrame(rows)
    if res.empty:
        print("❌ 無結果"); return

    def tier(r):
        if r["test_n"] >= 30 and r["test_mean"] > 0 and r["ci_low"] > 0:
            return "A"
        if r["test_n"] >= 10 and r["test_mean"] > 0:
            return "B"
        return "C"
    res["tier"] = res.apply(tier, axis=1)

    out_csv = ROOT / "logs" / "institutional_momentum.csv"
    res.sort_values("test_mean", ascending=False).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[3/3] 寫入 {out_csv.relative_to(ROOT)} ({len(res)} rows)")

    # Summary
    tier_a = res[res["tier"] == "A"]
    tier_b = res[res["tier"] == "B"]
    print(f"\nTier A: {len(tier_a)}")
    print(f"Tier B: {len(tier_b)}")
    print(f"Tier C: {len(res) - len(tier_a) - len(tier_b)}")

    for t_label in ["A", "B"]:
        sub = res[res["tier"] == t_label].sort_values("test_mean", ascending=False)
        if sub.empty:
            continue
        print(f"\n=== Tier {t_label} (top 20) ===")
        print(f"  {'tk':<5} {'investor':<18} {'consec':>6} {'hold':>5} "
              f"{'n':>4} {'OOS m/w':>14} {'CI':>20}")
        for _, r in sub.head(20).iterrows():
            print(f"  {r['ticker']:<5} {r['investor']:<18} "
                  f"{r['n_consec']:>5}d {r['hold_days']:>4}d "
                  f"{r['n']:>4} {r['test_mean']:>+5.2f}%/{r['test_win']:>3.0f}% "
                  f"[{r['ci_low']:>+5.2f}, {r['ci_high']:>+5.2f}]")


if __name__ == "__main__":
    main()
