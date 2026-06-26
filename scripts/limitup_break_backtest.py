"""
限漲停打開反向 backtest。

研究：
  漲停打開（散戶恐慌賣 + 主力倒貨）後，反向操作有 alpha 嗎？

策略：
  1. 偵測漲停日：daily K close >= prev_close × 1.095（漲幅 ≥ 9.5%）
  2. 若隔日（T+1）開盤跳高但收盤回測（盤中漲停打開）→ short
  3. 若 T+1 持續強勢 → no signal
  4. 持有 1-3 天，扣 0.34% cost

驗收：
  excess vs 0050，CI 全正
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
sys.path.insert(0, str(ROOT))

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
COST = 0.34
SEED = 42
N_BOOT = 500


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def detect_limitup_breaks(df: pd.DataFrame, hold_days: int = 1) -> pd.DataFrame:
    """
    detect:
      - T 日漲停（close > prev_close × 1.095）
      - T+1 開盤 > T 日 close × 1.005（跳高）
      - T+1 收盤 < T+1 開盤（盤中漲停打開或回測）→ 反向訊號

    short：T+1 收盤進場 → 持 hold_days 後賣
    """
    if df.empty or len(df) < hold_days + 5:
        return pd.DataFrame()
    df = df.copy()
    df["prev_close"] = df["close"].shift(1)
    df["limitup"] = df["close"] / df["prev_close"] >= 1.095

    df["next_open"] = df["open"].shift(-1)
    df["next_close"] = df["close"].shift(-1)
    df["next_high"] = df["high"].shift(-1)
    df["future_close"] = df["close"].shift(-1 - hold_days)

    # 進場條件
    df["jumped"] = df["next_open"] > df["close"] * 1.005
    df["broke_down"] = df["next_close"] < df["next_open"]
    df["trigger"] = df["limitup"] & df["jumped"] & df["broke_down"]

    triggered = df[df["trigger"]].copy()
    triggered = triggered.dropna(subset=["next_close", "future_close"])
    if triggered.empty:
        return pd.DataFrame()

    # short return = (entry - exit) / entry
    triggered["s_ret"] = (triggered["next_close"] - triggered["future_close"]) / triggered["next_close"] * 100
    triggered["net"] = triggered["s_ret"] - COST
    return triggered[["date", "next_close", "future_close", "s_ret", "net"]]


def stats(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"n": 0, "mean": np.nan, "win": np.nan,
                "ci_low": np.nan, "ci_high": np.nan}
    rets = df["net"].values
    rng = np.random.default_rng(SEED)
    if n >= 5:
        boot = np.array([rng.choice(rets, size=n, replace=True).mean() for _ in range(N_BOOT)])
        ci_low, ci_high = np.percentile(boot, [2.5, 97.5])
    else:
        ci_low = ci_high = np.nan
    return {
        "n": n, "mean": rets.mean(),
        "win": (rets > 0).mean() * 100,
        "ci_low": ci_low, "ci_high": ci_high,
    }


def main():
    print("=" * 80)
    print("限漲停打開反向 Backtest")
    print("=" * 80)

    # 全 universe（cache 中所有 ticker）
    tickers = sorted({p.stem for p in CACHE_YF.glob("*.parquet")
                      if p.stem.isdigit() and 4 <= len(p.stem) <= 6})
    print(f"\nUniverse: {len(tickers)} ticker")

    rows = []
    for tk in tickers:
        df = load_ohlcv(tk)
        if df.empty or len(df) < 100:
            continue
        for hold in [1, 2, 3, 5]:
            triggered = detect_limitup_breaks(df, hold)
            st = stats(triggered)
            if st["n"] >= 5:
                rows.append({"ticker": tk, "hold_days": hold, **st})

    res = pd.DataFrame(rows)
    if res.empty:
        print("❌ 全 universe 無觸發訊號（漲停打開 + 跳空 + 收回 同時滿足太少見）"); return

    def tier(r):
        if r["n"] >= 10 and r["mean"] > 0 and r["ci_low"] > 0:
            return "A"
        if r["n"] >= 5 and r["mean"] > 0:
            return "B"
        return "C"
    res["tier"] = res.apply(tier, axis=1)

    out_csv = ROOT / "logs" / "limitup_break.csv"
    res.sort_values("mean", ascending=False).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out_csv.relative_to(ROOT)} ({len(res)} rows)")

    a = res[res["tier"] == "A"]
    b = res[res["tier"] == "B"]
    print(f"\nTier A: {len(a)}, Tier B: {len(b)}, Tier C: {len(res)-len(a)-len(b)}")

    if not a.empty:
        print(f"\n=== Tier A (top 25) ===")
        print(f"  {'tk':<7} {'hold':>4} {'n':>4} {'mean':>7} {'win':>5} {'CI':>20}")
        for _, r in a.sort_values("mean", ascending=False).head(25).iterrows():
            print(f"  {r['ticker']:<7} {r['hold_days']:>3}d {r['n']:>4} "
                  f"{r['mean']:>+5.2f}% {r['win']:>4.0f}% "
                  f"[{r['ci_low']:>+5.2f}, {r['ci_high']:>+5.2f}]")

    if not b.empty:
        print(f"\n=== Tier B (top 15) ===")
        for _, r in b.sort_values("mean", ascending=False).head(15).iterrows():
            print(f"  {r['ticker']:<7} {r['hold_days']:>3}d {r['n']:>4} "
                  f"{r['mean']:>+5.2f}% {r['win']:>4.0f}% "
                  f"[{r['ci_low']:>+5.2f}, {r['ci_high']:>+5.2f}]")


if __name__ == "__main__":
    main()
