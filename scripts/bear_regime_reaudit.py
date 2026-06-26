"""Bear regime re-audit — 在「TAIEX 距 MA200 > +25%」(LATE_BULL/STRONG_BULL)
重測「memory 寫死永久放棄」的策略。

過去結論都在 bull regime 驗證:
  - ORB 當沖 → 永久放棄
  - TW long-only 全 dead end
  - 連漲 only → -2.5%

但 bear/late bull regime 可能反轉,值得驗:
  H1. ORB SHORT 在 LATE_BULL: 開盤量爆 + 跌破 5min low → SHORT
  H2. 量爆漲停隔日 SHORT (LATE_BULL filter): 漲停 + 量爆 → 隔日 open SHORT
  H3. 連漲 N 日後 SHORT (LATE_BULL filter): 連 5 日漲 → SHORT 5d
"""
from __future__ import annotations
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
import pandas as pd
import numpy as np
from pathlib import Path
import math

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"

def t_stat(arr):
    arr = np.asarray(arr, dtype=float)
    if len(arr) < 2 or arr.std(ddof=1) == 0:
        return 0.0
    return arr.mean() / (arr.std(ddof=1) / math.sqrt(len(arr)))


# Identify LATE_BULL regimes historically (TAIEX 距 MA200 > +25%)
def get_late_bull_regime():
    twii = CACHE / "^TWII.parquet"
    if not twii.exists():
        # Fallback: 用 0050
        twii = CACHE / "0050.parquet"
    df = pd.read_parquet(twii)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ma200"] = df["close"].rolling(200).mean()
    df["dist_ma200"] = (df["close"] / df["ma200"] - 1) * 100
    df["late_bull"] = df["dist_ma200"] > 25
    return df


def test_h2_limitup_volspike_short(regime_df):
    """量爆漲停隔日 SHORT (LATE_BULL filter)"""
    print("\n[H2] 量爆漲停隔日 SHORT (LATE_BULL)")
    universe = []
    for p in CACHE.glob("*.parquet"):
        tk = p.stem
        if tk.startswith("0") and len(tk) == 4:  # ETF
            continue
        if not tk.isdigit() or len(tk) != 4:
            continue
        universe.append(tk)
    universe = universe[:200]   # 限制 200 檔避免過久
    print(f"  Universe: {len(universe)} 個股")

    all_pnls = []
    for tk in universe:
        try:
            df = pd.read_parquet(CACHE / f"{tk}.parquet")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) < 250:
                continue
            df = df.merge(regime_df[["date","late_bull","dist_ma200"]], on="date", how="left")
            df["ret"] = df["close"].pct_change()
            df["vol_20"] = df["volume"].rolling(20).mean()
            df["vol_z"] = (df["volume"] - df["vol_20"]) / df["volume"].rolling(20).std()
            # 漲停 ≈ +9.5% 以上 (允許 9.x%)
            df["is_limitup"] = df["ret"] >= 0.095
            for i in range(20, len(df) - 5):
                if not df["is_limitup"].iloc[i]:
                    continue
                if pd.isna(df["vol_z"].iloc[i]) or df["vol_z"].iloc[i] < 2.0:
                    continue
                if not df["late_bull"].iloc[i]:
                    continue
                # 隔日 open SHORT, hold 5d exit
                if i + 5 >= len(df):
                    continue
                next_open = df["open"].iloc[i+1]
                exit_close = df["close"].iloc[i+5]
                # SHORT: gain when price drops
                pnl = (next_open - exit_close) / next_open * 100 - 0.585  # cost
                all_pnls.append(pnl)
        except Exception:
            continue
    arr = np.array(all_pnls)
    n = len(arr)
    if n < 10:
        print(f"  n={n} 樣本太小")
        return
    mu = arr.mean()
    t = t_stat(arr)
    wr = (arr > 0).mean() * 100
    icon = "✅" if t>2 and mu>0.3 else "⚠️" if mu>0 else "❌"
    print(f"  n={n} mean={mu:+.3f}% t={t:+.2f} WR={wr:.0f}% {icon}")


def test_h3_consec_up_short(regime_df):
    """連漲 5 日後 SHORT 5d (LATE_BULL filter)"""
    print("\n[H3] 連漲 5 日後 SHORT 5d (LATE_BULL)")
    universe = [p.stem for p in CACHE.glob("*.parquet") if p.stem.isdigit() and len(p.stem)==4][:200]
    all_pnls = []
    for tk in universe:
        try:
            df = pd.read_parquet(CACHE / f"{tk}.parquet")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) < 250:
                continue
            df = df.merge(regime_df[["date","late_bull"]], on="date", how="left")
            df["up_day"] = df["close"] > df["close"].shift(1)
            df["consec_up"] = df["up_day"].rolling(5).sum()
            for i in range(20, len(df) - 5):
                if df["consec_up"].iloc[i] < 5:   # 連漲 5 日
                    continue
                if not df["late_bull"].iloc[i]:
                    continue
                if i + 5 >= len(df):
                    continue
                next_open = df["open"].iloc[i+1]
                exit_close = df["close"].iloc[i+5]
                pnl = (next_open - exit_close) / next_open * 100 - 0.585
                all_pnls.append(pnl)
        except Exception:
            continue
    arr = np.array(all_pnls)
    n = len(arr)
    if n < 10:
        print(f"  n={n} 樣本太小")
        return
    mu = arr.mean()
    t = t_stat(arr)
    wr = (arr > 0).mean() * 100
    icon = "✅" if t>2 and mu>0.3 else "⚠️" if mu>0 else "❌"
    print(f"  n={n} mean={mu:+.3f}% t={t:+.2f} WR={wr:.0f}% {icon}")


def test_h4_late_bull_top_quintile_short(regime_df):
    """LATE_BULL 中 dist_ma200 top quintile 個股 → SHORT 10d"""
    print("\n[H4] LATE_BULL 中距離 MA200 > +50% 個股 → SHORT 10d")
    universe = [p.stem for p in CACHE.glob("*.parquet") if p.stem.isdigit() and len(p.stem)==4][:200]
    all_pnls = []
    for tk in universe:
        try:
            df = pd.read_parquet(CACHE / f"{tk}.parquet")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) < 250:
                continue
            df["ma200"] = df["close"].rolling(200).mean()
            df["dist"] = (df["close"]/df["ma200"]-1)*100
            df = df.merge(regime_df[["date","late_bull"]], on="date", how="left")
            for i in range(20, len(df) - 10):
                if not df["late_bull"].iloc[i] or pd.isna(df["dist"].iloc[i]):
                    continue
                if df["dist"].iloc[i] < 50:
                    continue
                if i + 10 >= len(df):
                    continue
                next_open = df["open"].iloc[i+1]
                exit_close = df["close"].iloc[i+10]
                pnl = (next_open - exit_close) / next_open * 100 - 0.585
                all_pnls.append(pnl)
        except Exception:
            continue
    arr = np.array(all_pnls)
    n = len(arr)
    if n < 10:
        print(f"  n={n} 樣本太小")
        return
    mu = arr.mean()
    t = t_stat(arr)
    wr = (arr > 0).mean() * 100
    icon = "✅" if t>2 and mu>0.3 else "⚠️" if mu>0 else "❌"
    print(f"  n={n} mean={mu:+.3f}% t={t:+.2f} WR={wr:.0f}% {icon}")


if __name__ == "__main__":
    from datetime import datetime, timezone, timedelta
    TW = timezone(timedelta(hours=8))
    print(f"Bear Regime Re-audit — {datetime.now(TW).strftime('%Y-%m-%d %H:%M TW')}")

    regime_df = get_late_bull_regime()
    n_late_bull = regime_df["late_bull"].sum()
    print(f"歷史 LATE_BULL 日數: {n_late_bull} ({n_late_bull/len(regime_df)*100:.1f}%)")

    test_h2_limitup_volspike_short(regime_df)
    test_h3_consec_up_short(regime_df)
    test_h4_late_bull_top_quintile_short(regime_df)
