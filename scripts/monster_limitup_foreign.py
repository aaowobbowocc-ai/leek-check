"""
妖股策略 #1 — 連續漲停板 + 法人首日進場 backtest。

訊號定義：
  trigger date = 滿足以下三條件:
    (a) 過去 3 日有至少 2 次「漲幅 >= 9%」（接近漲停）
    (b) 當日法人（外資 OR 投信）淨買入 >= 200 張
    (c) 過去 5 日法人都是淨賣出（"首日進場"概念）

對比:
  - 純 (a)：只有連續漲停，無法人
  - 純 (a)+(b)：連漲 + 法人買，但不要求 "首日"
  - (a)+(b)+(c)：完整訊號

forward return: 30 / 60 / 120 日
baseline: 同 ticker random window
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
INST = ROOT / "data" / "cache" / "finmind" / "institutional"
TW = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"

COST = 0.34
HOLDS = [30, 60, 120]


def load_px(tk):
    p = TW / f"{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df["pct"] = df["close"].pct_change() * 100
    return df


def load_inst(tk):
    p = INST / f"{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    pivot = df.pivot_table(index="date", columns="name", values="net_buy",
                            aggfunc="sum", fill_value=0).reset_index()
    pivot.columns.name = None
    return pivot.sort_values("date").reset_index(drop=True)


def annotate(tk):
    px = load_px(tk)
    if px.empty or len(px) < 50: return pd.DataFrame()
    inst = load_inst(tk)
    if inst.empty: return pd.DataFrame()

    df = pd.merge(px, inst, on="date", how="left")
    fi_col = "Foreign_Investor" if "Foreign_Investor" in df.columns else None
    inv_col = "Investment_Trust" if "Investment_Trust" in df.columns else None
    if fi_col is None and inv_col is None: return pd.DataFrame()

    df["fi"] = df[fi_col].fillna(0) if fi_col else 0
    df["inv"] = df[inv_col].fillna(0) if inv_col else 0
    df["inst_net"] = df["fi"] + df["inv"]

    # 訊號 a: 過去 3 日有 >= 2 次 pct >= 9%
    df["near_lu"] = df["pct"] >= 9.0
    df["a_trigger"] = df["near_lu"].rolling(3).sum() >= 2

    # 訊號 b: 當日法人淨買 >= 200 張（單位：股，假設 1 張 = 1000）
    df["b_trigger"] = df["inst_net"] >= 200000  # 200 lots in shares

    # 訊號 c: 過去 5 日法人都是賣 — "首日"
    df["c_seller_5d"] = df["inst_net"].rolling(5).max().shift(1) <= 0  # past 5d net <= 0

    # FIX 2026-05-04: next-day entry (避免 look-ahead bias)
    # 原: shift(-h) / close = trigger 當日 close 進場，但 a_trigger 用「過去 3 日漲幅 >=2 次」
    # b_trigger 用 inst_net 法人盤後資料 → 必須隔天才能進場
    for h in HOLDS:
        df[f"fwd_{h}"] = (df["close"].shift(-(h + 1)) / df["close"].shift(-1) - 1) * 100 - COST

    return df


def random_baseline(s, hold, n=2000, seed=42):
    """Same-ticker random — also next-day entry (對齊修正後的 forward return)"""
    rng = np.random.default_rng(seed)
    valid = s.dropna().values
    if len(valid) <= hold + 1: return np.array([])
    idx = rng.integers(0, len(valid) - hold - 1, n)
    # next-day entry: idx+1 -> idx+1+hold
    return (valid[idx + 1 + hold] / valid[idx + 1] - 1) * 100 - COST


def evaluate(label, sub_returns, base_mean):
    if len(sub_returns) < 5: return None
    arr = np.array(sub_returns)
    return {
        "label": label,
        "n": len(arr),
        "mean": arr.mean(),
        "median": np.median(arr),
        "win": (arr > 0).mean(),
        "alpha": arr.mean() - base_mean,
    }


def main():
    print("=" * 90)
    print("👹 妖股策略 #1 — 連續漲停板 + 法人首日")
    print("   訊號: 過去 3 日有 ≥2 次漲 9%+ 觸發；疊加法人買 / 首日轉買")
    print("=" * 90)

    # 過濾出有 institutional + price 的 ticker（個股，剔除 ETF）
    inst_tks = {p.stem for p in INST.glob("*.parquet")}
    tw_tks = {p.stem for p in TW.glob("*.parquet")}
    tks = sorted([t for t in inst_tks & tw_tks
                   if not t.startswith("00") and t.isdigit() and len(t) == 4])
    print(f"\n樣本 ticker: {len(tks)} 檔（個股，4 位數字）")
    if len(tks) > 100:
        # 隨機抽 100 加快速度
        rng = np.random.default_rng(42)
        tks = list(rng.choice(tks, 100, replace=False))
        print(f"  → 隨機抽樣 100 檔以加速")

    aggregated = {h: {"BASE": [], "A": [], "AB": [], "ABC": []} for h in HOLDS}

    for tk in tks:
        df = annotate(tk)
        if df.empty: continue

        # baseline 同 ticker random
        for h in HOLDS:
            base = random_baseline(df["close"], h, n=500)
            if len(base) > 50:
                aggregated[h]["BASE"].extend(base.tolist())

        # A 連續漲停
        a_mask = df["a_trigger"].fillna(False)
        # AB 加法人
        ab_mask = a_mask & df["b_trigger"].fillna(False)
        # ABC 加首日轉買
        abc_mask = ab_mask & df["c_seller_5d"].fillna(False)

        for h in HOLDS:
            for label, mask in [("A", a_mask), ("AB", ab_mask), ("ABC", abc_mask)]:
                rets = df.loc[mask & df[f"fwd_{h}"].notna(), f"fwd_{h}"].tolist()
                aggregated[h][label].extend(rets)

    print(f"\n{'hold':<5} {'組合':<6} {'n':>6} {'mean%':>8} {'median%':>9} "
          f"{'win':>6} {'alpha':>10}")
    print(f"{'-'*5} {'-'*6} {'-'*6} {'-'*8} {'-'*9} {'-'*6} {'-'*10}")

    for h in HOLDS:
        base = np.array(aggregated[h]["BASE"])
        if len(base) < 100:
            print(f"{h:>3}d  BASE  insufficient")
            continue
        bm = base.mean()
        print(f"{h:>3}d  BASE   {len(base):>5}  {bm:>+7.2f} {np.median(base):>+8.2f} "
              f"{(base>0).mean():>5.0%} {'(reference)':>10}")
        for label in ["A", "AB", "ABC"]:
            arr = aggregated[h][label]
            r = evaluate(label, arr, bm)
            if r is None:
                print(f"     {label:<6} {'(<5)':>6}")
                continue
            sig = "⭐⭐⭐" if r["alpha"] > 8 else ("⭐⭐" if r["alpha"] > 4 else ("⭐" if r["alpha"] > 1 else "  "))
            print(f"     {label:<6} {r['n']:>5}  {r['mean']:>+7.2f} {r['median']:>+8.2f} "
                  f"{r['win']:>5.0%} {r['alpha']:>+9.2f}% {sig}")

    print(f"\n判定:")
    print(f"  alpha > 4pp = 訊號值得進入 paper trade")
    print(f"  ABC > AB > A = 加 filter 確實放大 alpha")
    print(f"  反之，過濾後 alpha 縮 → 訊號是 noise")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()
