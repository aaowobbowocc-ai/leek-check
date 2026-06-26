"""
法人 momentum 訊號 真 Alpha 驗證。

把 institutional_momentum.csv 的 Tier A/B 候選重算：
  excess_return = stock_signal_return - 0050_same_window_return

然後重新分 Tier：
  Tier A 真 alpha: excess_OOS_mean > 0 AND excess_CI_low > 0 AND test_n >= 30
  Tier B: excess > 0 但 CI 跨 0
  Tier C: 訊號 = bull market 帶動，無真 alpha
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

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_INST = ROOT / "data" / "cache" / "finmind" / "institutional"
CUTOFF = pd.Timestamp("2025-06-01")
SEED = 42
N_BOOT = 1000

NAME_MAP = {
    "foreign": "Foreign_Investor",
    "investment_trust": "Investment_Trust",
    "dealer": "Dealer_self",
}


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def get_baseline(b_df: pd.DataFrame, entry_dates: list, hold_days: int) -> pd.Series:
    """0050 同期 hold_days 報酬。"""
    b_df = b_df.copy()
    b_df["next_open"] = b_df["open"].shift(-1)
    b_df["future_close"] = b_df["close"].shift(-hold_days)
    b_df["b_ret"] = (b_df["future_close"] / b_df["next_open"] - 1) * 100
    b_lookup = dict(zip(b_df["date"], b_df["b_ret"]))
    return pd.Series([b_lookup.get(d, np.nan) for d in entry_dates])


def compute_excess_for_variant(
    inst_df: pd.DataFrame,
    ticker_ohlcv: pd.DataFrame,
    baseline_ohlcv: pd.DataFrame,
    investor: str, n_consec: int, hold_days: int,
) -> pd.DataFrame:
    if inst_df.empty or ticker_ohlcv.empty or baseline_ohlcv.empty:
        return pd.DataFrame()

    pivot = inst_df.pivot_table(
        index="date", columns="name", values="net_buy", aggfunc="sum"
    ).reset_index()
    pivot.columns.name = None
    pivot = pivot.sort_values("date").reset_index(drop=True)

    if investor == "all_three":
        cols = ["Foreign_Investor", "Investment_Trust", "Dealer_self"]
        if not all(c in pivot.columns for c in cols):
            return pd.DataFrame()
        pivot["is_buy"] = (
            (pivot["Foreign_Investor"] > 0) &
            (pivot["Investment_Trust"] > 0) &
            (pivot["Dealer_self"] > 0)
        )
    else:
        col = NAME_MAP.get(investor)
        if col not in pivot.columns:
            return pd.DataFrame()
        pivot["is_buy"] = pivot[col] > 0

    pivot["consec"] = pivot["is_buy"].astype(int).rolling(n_consec).sum()
    pivot["trigger"] = pivot["consec"] == n_consec

    o = ticker_ohlcv.copy()
    o["next_open"] = o["open"].shift(-1)
    o["future_close"] = o["close"].shift(-hold_days)
    o["s_ret"] = (o["future_close"] / o["next_open"] - 1) * 100

    merged = pd.merge(pivot[["date", "trigger"]], o[["date", "s_ret"]], on="date", how="inner")
    triggered = merged[merged["trigger"]].copy()
    triggered = triggered.dropna(subset=["s_ret"])
    if triggered.empty:
        return pd.DataFrame()

    # 0050 baseline 同期報酬
    b_returns = get_baseline(baseline_ohlcv, triggered["date"].tolist(), hold_days)
    triggered["b_ret"] = b_returns.values
    triggered = triggered.dropna(subset=["b_ret"])

    triggered["excess"] = triggered["s_ret"] - triggered["b_ret"]
    return triggered[["date", "s_ret", "b_ret", "excess"]]


def stats_excess(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"n": 0, "excess_mean": np.nan, "excess_win": np.nan,
                "test_n": 0, "test_excess_mean": np.nan, "test_excess_win": np.nan,
                "ci_low": np.nan, "ci_high": np.nan,
                "raw_mean": np.nan, "baseline_mean": np.nan}

    excess = df["excess"].values
    test = df[pd.to_datetime(df["date"]) >= CUTOFF]
    rng = np.random.default_rng(SEED)
    if n >= 5:
        boot = np.array([rng.choice(excess, size=n, replace=True).mean() for _ in range(N_BOOT)])
        ci_low, ci_high = np.percentile(boot, [2.5, 97.5])
    else:
        ci_low, ci_high = np.nan, np.nan

    return {
        "n": n,
        "excess_mean": excess.mean(),
        "excess_win": (excess > 0).mean() * 100,
        "test_n": len(test),
        "test_excess_mean": test["excess"].mean() if len(test) else np.nan,
        "test_excess_win": (test["excess"] > 0).mean() * 100 if len(test) else np.nan,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "raw_mean": df["s_ret"].mean(),
        "baseline_mean": df["b_ret"].mean(),
    }


def main():
    print("=" * 90)
    print("法人 momentum 真 Alpha 驗證 (vs 0050 同期 baseline)")
    print("=" * 90)

    # 載入舊結果
    src = ROOT / "logs" / "institutional_momentum.csv"
    if not src.exists():
        print(f"❌ {src} 不存在，先跑 institutional_momentum_backtest"); return
    raw = pd.read_csv(src, dtype={"ticker": str})
    candidates = raw[raw["tier"].isin(["A", "B"])].copy()
    print(f"\n[1/3] 候選: {len(candidates)} (Tier A: {(raw.tier=='A').sum()}, B: {(raw.tier=='B').sum()})")

    # 載 0050 baseline
    baseline = load_ohlcv("0050")
    if baseline.empty:
        print("❌ 0050 cache 不存在"); return
    print(f"  0050 baseline: {len(baseline)} days")

    # 跑 excess for each candidate
    print(f"\n[2/3] 重算 excess return...")
    rows = []
    for i, r in candidates.iterrows():
        tk = str(r["ticker"])
        if tk == "0050":
            # 0050 自己 vs 0050 = 0，不算
            continue
        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            continue
        inst_path = CACHE_INST / f"{tk}.parquet"
        if not inst_path.exists():
            continue
        inst = pd.read_parquet(inst_path)
        inst["date"] = pd.to_datetime(inst["date"]).dt.date

        df = compute_excess_for_variant(
            inst, ohlcv, baseline,
            r["investor"], int(r["n_consec"]), int(r["hold_days"])
        )
        if df.empty:
            continue
        st = stats_excess(df)
        if st["n"] >= 10:
            rows.append({
                "ticker": tk,
                "investor": r["investor"],
                "n_consec": int(r["n_consec"]),
                "hold_days": int(r["hold_days"]),
                **st,
                "old_tier": r["tier"],
            })

    res = pd.DataFrame(rows)
    if res.empty:
        print("❌ 無結果"); return

    def tier(r):
        if r["test_n"] >= 30 and r["test_excess_mean"] > 0 and r["ci_low"] > 0:
            return "A"
        if r["test_n"] >= 10 and r["test_excess_mean"] > 0 and r["ci_low"] > -1:
            return "B"
        return "C"
    res["new_tier"] = res.apply(tier, axis=1)

    out_csv = ROOT / "logs" / "institutional_alpha_verified.csv"
    res.sort_values("test_excess_mean", ascending=False).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[3/3] 寫入 {out_csv.relative_to(ROOT)} ({len(res)} 候選驗證)")

    a = res[res["new_tier"] == "A"]
    b = res[res["new_tier"] == "B"]
    c = res[res["new_tier"] == "C"]
    print(f"\n真 Alpha 驗證後:")
    print(f"  Tier A (真 alpha): {len(a)}")
    print(f"  Tier B (邊緣): {len(b)}")
    print(f"  Tier C (大盤帶動): {len(c)}")

    # 上一版 vs 這版差異
    print(f"\n  舊 Tier A 數量: {(res['old_tier']=='A').sum()}")
    print(f"  舊→新 Tier A: {((res['old_tier']=='A') & (res['new_tier']=='A')).sum()} 留下")
    print(f"  舊 Tier A 但新 C: {((res['old_tier']=='A') & (res['new_tier']=='C')).sum()} 被淘汰")

    for label, sub in [("Tier A 真 alpha (top 25)", a), ("Tier B 邊緣 (top 15)", b)]:
        if sub.empty:
            continue
        sub = sub.sort_values("test_excess_mean", ascending=False)
        print(f"\n=== {label} ===")
        print(f"  {'tk':<7} {'investor':<18} {'cons':>4} {'hold':>4} "
              f"{'n':>4} {'OOS excess':>12} {'win':>5} {'raw':>7} {'base':>7} {'CI':>22}")
        head = sub.head(25 if 'A' in label else 15)
        for _, r in head.iterrows():
            print(f"  {r['ticker']:<7} {r['investor']:<18} "
                  f"{r['n_consec']:>3}d {r['hold_days']:>3}d "
                  f"{r['n']:>4} {r['test_excess_mean']:>+10.2f}% "
                  f"{r['test_excess_win']:>4.0f}% "
                  f"{r['raw_mean']:>+6.2f}% {r['baseline_mean']:>+6.2f}% "
                  f"[{r['ci_low']:>+6.2f}, {r['ci_high']:>+6.2f}]")


if __name__ == "__main__":
    main()
