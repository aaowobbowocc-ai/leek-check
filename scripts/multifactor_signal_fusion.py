"""
Multi-factor Signal Fusion — 三大訊號獨立 vs 疊加 alpha 比較。

訊號定義：
  S1 散戶比例極低（< 過去 1 年 20% 分位）：籌碼從散戶轉法人
  S2 外資連 3 日買超：法人加碼確認
  S3 量能 z >= 2.5（vs 60日 mean/std）：量能爆發

對每檔（intersect of holding + institutional + yfinance caches）：
  - 標記每天觸發了哪些訊號
  - 對每組合計算「signal 觸發後 60 日報酬」
  - 對照 baseline = 同 ticker 隨機 60 日

輸出：
  S1 alone, S2 alone, S3 alone
  S1+S2, S1+S3, S2+S3
  S1+S2+S3
  → 看 stacking 是否額外加成
"""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
HOLD = ROOT / "data" / "cache" / "finmind" / "holding"
INST = ROOT / "data" / "cache" / "finmind" / "institutional"
TW = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"

HOLD_DAYS = 60
COST = 0.34


def load_ohlcv(tk):
    p = TW / f"{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def compute_retail_pct(tk):
    """散戶（< 5 萬張）持股比例 weekly → 日內 forward fill"""
    p = HOLD / f"{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date

    # 散戶定義：<= 50,000 持股級距
    retail_levels = ["1-999", "1,000-5,000", "5,001-10,000",
                     "10,001-15,000", "15,001-20,000", "20,001-30,000",
                     "30,001-40,000", "40,001-50,000"]

    df["is_retail"] = df["HoldingSharesLevel"].isin(retail_levels)
    grp = df.groupby(["date", "is_retail"])["percent"].sum().unstack(fill_value=0)
    if True not in grp.columns: return pd.DataFrame()
    out = pd.DataFrame({"date": grp.index, "retail_pct": grp[True].values})
    return out.sort_values("date").reset_index(drop=True)


def compute_foreign_consec(tk):
    """外資連續買超天數"""
    p = INST / f"{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    pivot = df.pivot_table(index="date", columns="name", values="net_buy",
                           aggfunc="sum").reset_index()
    pivot.columns.name = None
    pivot = pivot.sort_values("date").reset_index(drop=True)
    if "Foreign_Investor" not in pivot.columns: return pd.DataFrame()
    pivot["fi_buy"] = pivot["Foreign_Investor"] > 0

    # consecutive buy run
    run = 0
    consec = []
    for v in pivot["fi_buy"]:
        run = run + 1 if v else 0
        consec.append(run)
    pivot["fi_consec"] = consec
    return pivot[["date", "fi_consec"]]


def annotate_signals(tk):
    """合併三訊號 → 日線 dataframe"""
    px = load_ohlcv(tk)
    if px.empty: return pd.DataFrame()

    # S3 vol anomaly
    px["vol_ma60"] = px["volume"].rolling(60).mean()
    px["vol_std60"] = px["volume"].rolling(60).std()
    px["vol_z"] = (px["volume"] - px["vol_ma60"]) / px["vol_std60"]
    px["S3"] = px["vol_z"] >= 2.5

    # S2 foreign consec
    fi = compute_foreign_consec(tk)
    px = pd.merge(px, fi, on="date", how="left")
    px["fi_consec"] = px["fi_consec"].fillna(0)
    px["S2"] = px["fi_consec"] >= 3

    # S1 retail concentration
    rp = compute_retail_pct(tk)
    if not rp.empty:
        px2 = px.sort_values("date").copy()
        rp2 = rp.sort_values("date").copy()
        px2["date_dt"] = pd.to_datetime(px2["date"])
        rp2["date_dt"] = pd.to_datetime(rp2["date"])
        px = pd.merge_asof(px2, rp2[["date_dt", "retail_pct"]],
                           on="date_dt", direction="backward").drop(columns=["date_dt"])
        # rolling 252-day 20th percentile
        px["retail_p20"] = px["retail_pct"].rolling(252, min_periods=60).quantile(0.20)
        px["S1"] = px["retail_pct"] < px["retail_p20"]
    else:
        px["S1"] = False

    # forward return — FIX 2026-05-04: next-day entry (避免 look-ahead bias)
    # 原: px["fwd_60"] = (px["close"].shift(-HOLD_DAYS) / px["close"] - 1) ...
    # 觸發發生在收盤後（信號用當日資料判斷）→ 隔天才能進場
    # entry = next day close, exit = entry + HOLD_DAYS days later
    px["fwd_60"] = (px["close"].shift(-(HOLD_DAYS + 1)) / px["close"].shift(-1) - 1) * 100 - COST
    return px


def random_baseline(s, hold, n=2000, seed=None):
    """Same-ticker random baseline — also use next-day entry

    C2-6 FIX: seed=None (per-call random) 避免全域 seed=42 造成
    所有 ticker 抽到結構性相同的 index 位置而低估 variance。
    """
    rng = np.random.default_rng(seed)
    if len(s) <= hold + 1: return np.array([])
    valid = s.dropna().values
    if len(valid) <= hold + 1: return np.array([])
    idx = rng.integers(0, len(valid) - hold - 1, n)
    # next-day entry: idx+1 (open) -> idx+1+hold (exit)
    return (valid[idx + 1 + hold] / valid[idx + 1] - 1) * 100 - COST


def evaluate_combo(label, df, mask):
    sub = df[mask & df["fwd_60"].notna()]
    if len(sub) < 5: return None
    return {
        "label": label,
        "n": len(sub),
        "mean": sub["fwd_60"].mean(),
        "win": (sub["fwd_60"] > 0).mean(),
        "median": sub["fwd_60"].median(),
    }


def main():
    print("=" * 90)
    print("🔬 Multi-factor Signal Fusion — 散戶比例 + 外資 + 量能")
    print(f"   hold={HOLD_DAYS}d  cost={COST}%  baseline=同 ticker random window")
    print("=" * 90)

    # 共同覆蓋 ticker
    hold_tks = {p.stem for p in HOLD.glob("*.parquet")}
    inst_tks = {p.stem for p in INST.glob("*.parquet")}
    px_tks = {p.stem for p in TW.glob("*.parquet")}
    common = sorted(hold_tks & inst_tks & px_tks)
    # 只測個股（剔除 0050/006208/00881 等 ETF）
    individuals = [t for t in common if not t.startswith("00") and t != "0050"]
    print(f"\n樣本 ticker: {individuals}")

    aggregated = {k: [] for k in [
        "BASE", "S1", "S2", "S3", "S1+S2", "S1+S3", "S2+S3", "S1+S2+S3"
    ]}

    for tk in individuals:
        df = annotate_signals(tk)
        if df.empty: continue
        # 同 ticker baseline
        base = random_baseline(df["close"], HOLD_DAYS)
        if len(base) < 100: continue
        aggregated["BASE"].append((tk, base))

        for label, mask in [
            ("S1", df["S1"] == True),
            ("S2", df["S2"] == True),
            ("S3", df["S3"] == True),
            ("S1+S2", (df["S1"] == True) & (df["S2"] == True)),
            ("S1+S3", (df["S1"] == True) & (df["S3"] == True)),
            ("S2+S3", (df["S2"] == True) & (df["S3"] == True)),
            ("S1+S2+S3", (df["S1"] == True) & (df["S2"] == True) & (df["S3"] == True)),
        ]:
            r = evaluate_combo(label, df, mask)
            if r:
                aggregated[label].append((tk, df.loc[mask & df["fwd_60"].notna(), "fwd_60"].values))

    # 集合所有 sample 全市場聚合
    print(f"\n{'組合':<12} {'n samples':>10} {'mean%':>8} {'median%':>9} {'win':>6} {'vs baseline':>12}")
    print(f"{'-'*12} {'-'*10} {'-'*8} {'-'*9} {'-'*6} {'-'*12}")

    base_all = np.concatenate([b for _, b in aggregated["BASE"]])
    print(f"{'BASE (任意進場)':<12} {len(base_all):>10} "
          f"{base_all.mean():>+7.2f} {np.median(base_all):>+8.2f} "
          f"{(base_all>0).mean():>5.0%} {'(reference)':>12}")

    base_mean = base_all.mean()
    for combo in ["S1", "S2", "S3", "S1+S2", "S1+S3", "S2+S3", "S1+S2+S3"]:
        samples = aggregated[combo]
        if not samples:
            print(f"{combo:<12} {'(0 samples)':>10}")
            continue
        all_rets = np.concatenate([r for _, r in samples])
        if len(all_rets) == 0:
            print(f"{combo:<12} {'(0)':>10}")
            continue
        mean = all_rets.mean()
        med = np.median(all_rets)
        win = (all_rets > 0).mean()
        alpha = mean - base_mean
        sig = "⭐⭐⭐" if alpha > 5 else ("⭐⭐" if alpha > 2 else ("⭐" if alpha > 0.5 else "  "))
        print(f"{combo:<12} {len(all_rets):>10} "
              f"{mean:>+7.2f} {med:>+8.2f} {win:>5.0%} {alpha:>+10.2f}% {sig}")

    # 每檔細節（看 stacking 是否一致）
    print(f"\n\n{'='*90}")
    print(f"每檔細節（only 訊號 n >= 5 的組合）")
    print(f"{'='*90}")
    print(f"  {'ticker':<8} {'combo':<12} {'n':>5} {'mean%':>8} {'win':>6} {'alpha':>8}")
    for tk in individuals:
        df = annotate_signals(tk)
        if df.empty: continue
        base = random_baseline(df["close"], HOLD_DAYS)
        if len(base) < 100: continue
        bm = base.mean()
        for label, mask in [
            ("S1", df["S1"] == True),
            ("S2", df["S2"] == True),
            ("S3", df["S3"] == True),
            ("S1+S2", df["S1"] & df["S2"]),
            ("S1+S3", df["S1"] & df["S3"]),
            ("S2+S3", df["S2"] & df["S3"]),
            ("S1+S2+S3", df["S1"] & df["S2"] & df["S3"]),
        ]:
            r = evaluate_combo(label, df, mask)
            if r and r["n"] >= 5:
                alpha = r["mean"] - bm
                print(f"  {tk:<8} {label:<12} {r['n']:>5} "
                      f"{r['mean']:>+7.2f} {r['win']:>5.0%} {alpha:>+7.2f}")

    print(f"\n{'='*90}")
    print(f"判定：")
    print(f"  alpha > 5pp → 該組合明顯加成")
    print(f"  S1+S2+S3 alpha > 任一單獨訊號 → fusion 有意義")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()
