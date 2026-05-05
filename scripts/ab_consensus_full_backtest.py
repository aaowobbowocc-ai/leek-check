"""
AB 雙重共識 Multifactor Combo — Full Backtest 2017-2025

A signal: 連漲 + 法人買 (monster_limitup_foreign)
  條件: 3 日內 ≥ 2 次當日漲幅 ≥ 9% AND 當日法人淨買 ≥ 200,000 股 (200 張)

B signal: 中小妖股 S1+S3
  條件: 散戶比例 < 252日 20% 分位 AND 量能 z >= 2.5
  排除: 大型權值（2330/2317/2454/2412/2891/2882/2002/1303/1301/2308）

AB 共識: 同一檔同一日 A AND B 都觸發
hold: 60 日（next-day open 進場）

驗證 (memory baseline):
  歷史 n=126, alpha +8.78%, t=+3.83, OOS+MCPT 通過
  目標: 用最新 2017-2025 完整資料 reconfirm + walk-forward
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
INST_CACHE = ROOT / "data" / "cache" / "finmind" / "institutional"
HOLD_CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
COST = 0.78
HOLD_DAYS = 60

LARGE_CAP_EXCLUDE = {
    "2330", "2317", "2454", "2412", "2891", "2882",
    "2002", "1303", "1301", "2308",
}

# B signal 用的散戶分級（小戶）
RETAIL_LEVELS = [
    "1-999", "1,000-5,000", "5,001-10,000",
    "10,001-15,000", "15,001-20,000", "20,001-30,000",
    "30,001-40,000", "40,001-50,000",
]


def load_px(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if df.empty or len(df) < 80:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["pct"] = df["close"].pct_change() * 100
    df["vol_ma60"] = df["volume"].rolling(60).mean()
    df["vol_std60"] = df["volume"].rolling(60).std()
    df["vol_z"] = (df["volume"] - df["vol_ma60"]) / df["vol_std60"]
    return df


def load_inst(tk: str) -> pd.DataFrame:
    p = INST_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    pivot = df.pivot_table(
        index="date", columns="name", values="net_buy",
        aggfunc="sum", fill_value=0,
    ).reset_index()
    pivot.columns.name = None
    return pivot.sort_values("date").reset_index(drop=True)


def load_retail(tk: str) -> pd.DataFrame:
    p = HOLD_CACHE / f"TaiwanStockHoldingSharesPer_{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df["is_retail"] = df["HoldingSharesLevel"].isin(RETAIL_LEVELS)
    grp = df.groupby(["date", "is_retail"])["percent"].sum().unstack(fill_value=0)
    if True not in grp.columns:
        return pd.DataFrame()
    return pd.DataFrame({
        "date": grp.index,
        "retail_pct": grp[True].values,
    }).reset_index(drop=True)


def detect_ab_events(tk: str) -> list[dict]:
    """掃 ticker 全期間，找出 AB 共識觸發事件。"""
    px = load_px(tk)
    if px.empty or len(px) < 252:
        return []

    inst = load_inst(tk)
    if inst.empty:
        return []
    fi_col = "Foreign_Investor"
    inv_col = "Investment_Trust"
    if fi_col not in inst.columns:
        inst[fi_col] = 0
    if inv_col not in inst.columns:
        inst[inv_col] = 0
    inst["inst_net"] = inst[fi_col] + inst[inv_col]

    retail = load_retail(tk)
    if retail.empty:
        return []

    # Merge: px (daily) + inst (daily) + retail (monthly forward-fill)
    df = px.merge(inst[["date", "inst_net"]], on="date", how="left")
    df["inst_net"] = df["inst_net"].fillna(0)

    df = df.merge(retail, on="date", how="left")
    df["retail_pct"] = df["retail_pct"].ffill()

    # A signal: 3-day window has ≥2 days with pct >= 9% AND inst_net >= 200,000 shares today
    df["near_lu"] = (df["pct"] >= 9.0).astype(int)
    df["near_lu_3d"] = df["near_lu"].rolling(3).sum()
    df["A"] = (df["near_lu_3d"] >= 2) & (df["inst_net"] >= 200_000)

    # B signal: retail < 252d p20 AND vol_z >= 2.5
    df["retail_p20"] = df["retail_pct"].rolling(252, min_periods=60).quantile(0.2)
    df["B"] = (df["retail_pct"] < df["retail_p20"]) & (df["vol_z"] >= 2.5)

    # Consensus
    df["AB"] = df["A"] & df["B"]

    # Forward 60d return
    df["entry_open"] = df["open"].shift(-1)
    df["exit_close"] = df["close"].shift(-HOLD_DAYS)
    df["fwd_60d"] = (df["exit_close"] / df["entry_open"] - 1) * 100 - COST

    sig = df[df["AB"]].copy()
    if sig.empty:
        return []

    events = []
    for _, row in sig.iterrows():
        if pd.isna(row["fwd_60d"]):
            continue
        events.append({
            "ticker": tk,
            "date": row["date"],
            "pct": row["pct"],
            "inst_net": row["inst_net"],
            "retail_pct": row["retail_pct"],
            "vol_z": row["vol_z"],
            "fwd_60d": row["fwd_60d"],
        })
    return events


def random_baseline(events: pd.DataFrame, n_iter: int = 100) -> tuple[float, float]:
    """Same-ticker random baseline: 對每個事件，從同 ticker 隨機抽一日跑 60d hold。"""
    if events.empty:
        return 0.0, 0.0
    means = []
    for _ in range(n_iter):
        rets = []
        for _, row in events.iterrows():
            tk = row["ticker"]
            px = load_px(tk)
            if px.empty or len(px) < 80:
                continue
            valid_idx = px.index[60:len(px) - HOLD_DAYS - 1]
            if len(valid_idx) == 0:
                continue
            r = np.random.choice(valid_idx)
            entry = px.iloc[r + 1]["open"]
            exit_p = px.iloc[r + HOLD_DAYS]["close"]
            if entry <= 0:
                continue
            rets.append((exit_p / entry - 1) * 100 - COST)
        if rets:
            means.append(np.mean(rets))
    if not means:
        return 0.0, 0.0
    return float(np.mean(means)), float(np.std(means))


def main():
    print("=" * 80)
    print("  AB 雙重共識 Multifactor Combo — Full Backtest")
    print(f"  Hold: {HOLD_DAYS}d, COST: {COST}%, Period: 2017-2025")
    print("=" * 80)

    # Universe: tickers with all 3 caches
    tw_tks = {p.stem for p in TW_CACHE.glob("*.parquet")
              if p.stem.isdigit() and len(p.stem) == 4}
    inst_tks = {p.stem for p in INST_CACHE.glob("*.parquet")}
    hold_tks = {p.stem.replace("TaiwanStockHoldingSharesPer_", "")
                for p in HOLD_CACHE.glob("TaiwanStockHoldingSharesPer_*.parquet")}

    universe = sorted((tw_tks & inst_tks & hold_tks) - LARGE_CAP_EXCLUDE)
    print(f"\n  Universe: {len(universe)} tickers (excl. {len(LARGE_CAP_EXCLUDE)} large caps)")

    print(f"\n  掃描 AB 共識事件...")
    all_events = []
    for i, tk in enumerate(universe):
        try:
            events = detect_ab_events(tk)
            all_events.extend(events)
        except Exception:
            continue
        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(universe)}] events so far: {len(all_events)}")

    df = pd.DataFrame(all_events)
    if df.empty:
        print("  ❌ 無事件")
        return

    print(f"\n  Total AB consensus events: {len(df):,}")

    # Full sample stats
    fwd = df["fwd_60d"].dropna()
    t, p = stats.ttest_1samp(fwd, 0, alternative="greater")
    win = (fwd > 0).mean() * 100
    print(f"\n  === Full Sample Stats ===")
    print(f"    n = {len(fwd):,}")
    print(f"    mean = {fwd.mean():+.2f}%")
    print(f"    median = {fwd.median():+.2f}%")
    print(f"    std = {fwd.std():.2f}%")
    print(f"    t-stat = {t:+.2f}")
    print(f"    p-value = {p:.5f}")
    print(f"    win rate = {win:.1f}%")

    # Random baseline
    print(f"\n  === Same-ticker Random Baseline (n_iter=50) ===")
    base_mean, base_std = random_baseline(df, n_iter=50)
    incremental = fwd.mean() - base_mean
    print(f"    Baseline mean: {base_mean:+.2f}%  (std across iterations: {base_std:.2f}%)")
    print(f"    Incremental alpha vs baseline: {incremental:+.2f}pp")

    # OOS split
    print(f"\n  === OOS Walk-Forward Split ===")
    df["year"] = df["date"].dt.year
    splits = [
        ("2017-2019", 2017, 2019),
        ("2020-2022", 2020, 2022),
        ("2023-2025", 2023, 2025),
    ]
    print(f"  {'Period':<14} {'n':>5}  {'mean':>8}  {'t':>7}  {'p':>8}  {'win%':>6}")
    for label, ys, ye in splits:
        sub = df[(df["year"] >= ys) & (df["year"] <= ye)]["fwd_60d"].dropna()
        if len(sub) < 5:
            print(f"  {label:<14} n={len(sub)} (太少)")
            continue
        ts, ps = stats.ttest_1samp(sub, 0, alternative="greater")
        ws = (sub > 0).mean() * 100
        flag = "✅" if ps < 0.05 else "❌"
        print(f"  {label:<14} {len(sub):>5}  {sub.mean():>+7.2f}%  {ts:>+6.2f}  {ps:>8.5f}{flag}  {ws:>5.1f}%")

    # By year
    print(f"\n  === Year-by-Year Breakdown ===")
    print(f"  {'Year':<6} {'n':>4}  {'mean':>8}  {'win%':>6}")
    for yr in sorted(df["year"].unique()):
        sub = df[df["year"] == yr]["fwd_60d"].dropna()
        if len(sub) < 1:
            continue
        ws = (sub > 0).mean() * 100
        print(f"  {yr:<6} {len(sub):>4}  {sub.mean():>+7.2f}%  {ws:>5.1f}%")

    # Top tickers
    print(f"\n  === Top 10 Tickers by Event Count ===")
    tk_counts = df.groupby("ticker").agg(n=("fwd_60d", "count"), mean_ret=("fwd_60d", "mean")).sort_values("n", ascending=False).head(10)
    print(tk_counts.round(2).to_string())

    # MCPT
    print(f"\n  === Monte Carlo Permutation Test (n_iter=200) ===")
    null_means = []
    for _ in range(200):
        sample_rets = []
        for _, row in df.iterrows():
            px = load_px(row["ticker"])
            if px.empty or len(px) < 80: continue
            valid_idx = px.index[60:len(px) - HOLD_DAYS - 1]
            if len(valid_idx) == 0: continue
            ridx = np.random.choice(valid_idx)
            entry = px.iloc[ridx + 1]["open"]
            exit_p = px.iloc[ridx + HOLD_DAYS]["close"]
            if entry > 0:
                sample_rets.append((exit_p / entry - 1) * 100 - COST)
        if sample_rets:
            null_means.append(np.mean(sample_rets))
    null_arr = np.array(null_means)
    actual = fwd.mean()
    mcpt_p = (null_arr >= actual).mean()
    print(f"    Actual mean: {actual:+.2f}%")
    print(f"    Null mean: {null_arr.mean():+.2f}% (std {null_arr.std():.2f}%)")
    print(f"    MCPT p-value: {mcpt_p:.4f}")
    if mcpt_p < 0.01:
        print(f"    ✅ Highly significant (p < 0.01)")
    elif mcpt_p < 0.05:
        print(f"    ✅ Significant (p < 0.05)")
    else:
        print(f"    ❌ Not significant")

    # Save events
    out = ROOT / "logs" / "ab_consensus_events.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ Events saved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
