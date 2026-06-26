"""
限漲停打開反向 Alpha v2 驗證 — 同 ticker random window baseline

對之前 245 Tier A 候選跑「signal_window_return vs random_window_return」
篩出真 alpha
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
COST = 0.34
SEED = 42


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def detect_limitup_breaks(df: pd.DataFrame, hold_days: int) -> pd.DataFrame:
    if df.empty or len(df) < hold_days + 5:
        return pd.DataFrame()
    df = df.copy()
    df["prev_close"] = df["close"].shift(1)
    df["limitup"] = df["close"] / df["prev_close"] >= 1.095
    df["next_open"] = df["open"].shift(-1)
    df["next_close"] = df["close"].shift(-1)
    df["future_close"] = df["close"].shift(-1 - hold_days)
    df["jumped"] = df["next_open"] > df["close"] * 1.005
    df["broke_down"] = df["next_close"] < df["next_open"]
    df["trigger"] = df["limitup"] & df["jumped"] & df["broke_down"]
    triggered = df[df["trigger"]].copy()
    triggered = triggered.dropna(subset=["next_close", "future_close"])
    if triggered.empty:
        return pd.DataFrame()
    triggered["s_ret"] = (triggered["next_close"] - triggered["future_close"]) / triggered["next_close"] * 100
    return triggered[["date", "s_ret"]]


def random_short_returns(df: pd.DataFrame, hold_days: int) -> np.ndarray:
    """同 ticker 任意點 short 開盤、hold_days 後平倉。"""
    rets = []
    for i in range(len(df) - hold_days - 2):
        entry = float(df.iloc[i + 1]["close"])
        exit_p = float(df.iloc[i + 1 + hold_days]["close"])
        s_ret = (entry - exit_p) / entry * 100
        rets.append(s_ret)
    return np.array(rets)


def main():
    src = ROOT / "logs" / "limitup_break.csv"
    if not src.exists():
        print(f"❌ {src}"); return
    raw = pd.read_csv(src, dtype={"ticker": str})
    candidates = raw[raw["tier"].isin(["A", "B"])].copy()
    print(f"驗證候選: {len(candidates)} (A: {(raw.tier=='A').sum()}, B: {(raw.tier=='B').sum()})")

    rows = []
    for i, r in candidates.iterrows():
        tk = str(r["ticker"])
        hold = int(r["hold_days"])
        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            continue
        sig = detect_limitup_breaks(ohlcv, hold)
        if sig.empty or len(sig) < 5:
            continue
        rand = random_short_returns(ohlcv, hold) - COST  # 都扣 cost
        sig_net = sig["s_ret"].values - COST
        sig_mean = sig_net.mean()
        rand_mean = rand.mean()
        rand_std = rand.std()
        true_alpha = sig_mean - rand_mean
        sigma = (true_alpha / (rand_std / np.sqrt(len(sig_net)))) if rand_std > 0 else np.nan
        rows.append({
            "ticker": tk, "hold_days": hold,
            "n_sig": len(sig_net),
            "sig_mean": sig_mean,
            "rand_mean": rand_mean,
            "true_alpha": true_alpha,
            "sigma": sigma,
        })

    res = pd.DataFrame(rows)
    if res.empty:
        print("❌ 無結果"); return

    def tier(r):
        if r["sigma"] > 1.96 and r["true_alpha"] > 0.5 and r["n_sig"] >= 10:
            return "A"
        if r["sigma"] > 1.0 and r["true_alpha"] > 0:
            return "B"
        return "C"
    res["tier"] = res.apply(tier, axis=1)

    out = ROOT / "logs" / "limitup_break_v2.csv"
    res.sort_values("true_alpha", ascending=False).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")

    a = res[res["tier"] == "A"].sort_values("true_alpha", ascending=False)
    b = res[res["tier"] == "B"].sort_values("true_alpha", ascending=False)
    print(f"\nv2 後 Tier A: {len(a)} (淘汰率 {(1-len(a)/len(res))*100:.1f}%)")
    print(f"v2 後 Tier B: {len(b)}")

    if not a.empty:
        print(f"\n=== Tier A 真 alpha (top 30) ===")
        print(f"  {'tk':<7} {'hold':>5} {'n':>4} {'sig':>9} {'rand':>9} {'alpha':>9} {'sigma':>7}")
        for _, r in a.head(30).iterrows():
            print(f"  {r['ticker']:<7} {r['hold_days']:>3}d {r['n_sig']:>4} "
                  f"{r['sig_mean']:>+7.2f}% {r['rand_mean']:>+7.2f}% "
                  f"{r['true_alpha']:>+7.2f}% {r['sigma']:>+6.2f}")


if __name__ == "__main__":
    main()
