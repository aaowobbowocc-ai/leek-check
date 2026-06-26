"""
短倉策略診斷 — 找出為什麼 3231 緯創、6125 廣運 CI 跨 0。

目標：
  1. 跑 3231 + 6125 的 best variants，記錄每筆交易
  2. 拆 win / loss，分析 loss 的特徵（gap、regime、特定日期分群）
  3. 測 4 個 fix：
     A. 加 ^TWII 大盤弱勢 filter
     B. 加 gap risk filter（過去 60 日有 gap up >5% 的 ticker 不空）
     C. wider stop_buffer (1.5%)
     D. dynamic position sizing（gap risk 高的縮倉）
  4. 看哪個 fix 把 CI 推回正

最終決定：能不能進入 paper trade，還是永久放棄。
"""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout is not None and getattr(sys.stdout, "encoding", None) \
        and sys.stdout.encoding.lower() != "utf-8" \
        and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "cache" / "finmind" / "minute"
TWII_CACHE = ROOT / "data" / "cache" / "yfinance" / "global" / "TWII.parquet"
COST = 0.34
SCAN_END = "11:30"
EXIT_TIME = "13:20"


def load_minute(tk):
    files = sorted(CACHE.glob(f"{tk}_*.parquet"))
    if not files: return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    if df.empty: return df
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date_only"] = df["dt"].dt.date
    df["minute_str"] = df["dt"].dt.strftime("%H:%M")
    return df.sort_values("dt").reset_index(drop=True)


def load_twii():
    if not TWII_CACHE.exists():
        return pd.DataFrame()
    df = pd.read_parquet(TWII_CACHE)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df["twii_5d_pct"] = df["close"].pct_change(5) * 100
    df["twii_20d_pct"] = df["close"].pct_change(20) * 100
    df["above_ma20"] = df["close"] > df["close"].rolling(20).mean()
    return df


def simulate_short(
    tk, pump, vol_ratio, retreat, stop_buf, tp,
    twii_filter=None,
    gap_filter=False,
    custom_stop_buf=None,
):
    """跑單一變體，回傳每筆交易明細。"""
    df = load_minute(tk)
    if df.empty: return pd.DataFrame()

    # 前日量
    daily_vol = df.groupby("date_only")["volume"].sum().to_dict()
    days = sorted(df["date_only"].unique())
    twii = load_twii()

    # 過去 gap up history
    daily_open_close = df.groupby("date_only").agg(
        first_minute=("dt", "first"),
        first_open=("open", "first"),
        last_close=("close", "last"),
    ).reset_index()
    daily_open_close = daily_open_close.sort_values("date_only").reset_index(drop=True)
    daily_open_close["prev_close"] = daily_open_close["last_close"].shift(1)
    daily_open_close["gap_pct"] = (
        daily_open_close["first_open"] / daily_open_close["prev_close"] - 1
    ) * 100

    trades = []
    sb = custom_stop_buf if custom_stop_buf is not None else stop_buf

    for i, d in enumerate(days):
        if i == 0: continue
        prev_v = daily_vol.get(days[i-1], 0)
        if prev_v <= 0: continue

        # ^TWII filter
        if twii_filter is not None and not twii.empty:
            t = twii[twii["date"] == d]
            if t.empty: continue
            row = t.iloc[-1]
            if twii_filter == "weak" and row["above_ma20"]:
                continue  # 大盤強，不空
            if twii_filter == "down_5d" and row["twii_5d_pct"] >= 0:
                continue

        # Gap filter: 過去 60 日有 gap up >5% 的 ticker 跳過
        if gap_filter:
            past = daily_open_close[daily_open_close["date_only"] < d].tail(60)
            if not past.empty and (past["gap_pct"] > 5).sum() >= 2:
                continue  # 有 2 次以上 gap up >5%，gap risk 高，跳過

        day = df[df["date_only"] == d].copy()
        if day.empty: continue
        day_open = float(day.iloc[0]["close"])

        # 找進場時點
        cum_vol = 0
        intraday_high = day_open
        entry_price = None
        entry_time = None
        for _, row in day.iterrows():
            mins = row["minute_str"]
            if mins > SCAN_END: break
            cum_vol += float(row["volume"])
            close = float(row["close"])
            high = float(row["high"])
            intraday_high = max(intraday_high, high)
            cum_ret = close / day_open - 1

            if cum_ret < pump: continue
            if cum_vol / prev_v < vol_ratio: continue
            retreat_pct = 1 - close / intraday_high
            if not (retreat <= retreat_pct < retreat + 0.02): continue

            entry_price = close
            entry_time = mins
            break

        if entry_price is None: continue

        # 出場：跑剩下的 minute
        rest = day[day["minute_str"] > entry_time]
        stop_price = intraday_high * (1 + sb)
        tp_price = entry_price * (1 - tp)
        exit_price = None
        exit_reason = None
        for _, row in rest.iterrows():
            mins = row["minute_str"]
            high = float(row["high"])
            low = float(row["low"])
            if high >= stop_price:
                exit_price = stop_price
                exit_reason = "stop"
                break
            if low <= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
                break
            if mins >= EXIT_TIME:
                exit_price = float(row["close"])
                exit_reason = "timeout"
                break

        if exit_price is None and not rest.empty:
            exit_price = float(rest.iloc[-1]["close"])
            exit_reason = "eod"
        if exit_price is None: continue

        gross_pct = (entry_price - exit_price) / entry_price * 100
        net_pct = gross_pct - COST

        # Append twii context
        twii_5d = None
        twii_above_ma20 = None
        if not twii.empty:
            t = twii[twii["date"] == d]
            if not t.empty:
                twii_5d = float(t.iloc[-1]["twii_5d_pct"])
                twii_above_ma20 = bool(t.iloc[-1]["above_ma20"])

        # Past 60d gap count
        past_gap_count = 0
        if not daily_open_close.empty:
            past = daily_open_close[daily_open_close["date_only"] < d].tail(60)
            past_gap_count = int((past["gap_pct"] > 5).sum())

        trades.append({
            "date": d, "ticker": tk,
            "entry_time": entry_time, "entry_price": entry_price,
            "exit_price": exit_price, "exit_reason": exit_reason,
            "intraday_high": intraday_high, "stop_buf": sb,
            "gross_pct": gross_pct, "net_pct": net_pct,
            "twii_5d": twii_5d, "twii_above_ma20": twii_above_ma20,
            "past_gap_count": past_gap_count,
        })

    return pd.DataFrame(trades)


def evaluate(label, trades_df):
    if trades_df.empty:
        return {"label": label, "n": 0, "mean": None, "win": None,
                "ci_low": None, "ci_high": None}
    n = len(trades_df)
    arr = trades_df["net_pct"].values
    mean = arr.mean()
    win = (arr > 0).mean()
    # bootstrap CI
    rng = np.random.default_rng(42)
    means = [arr[rng.integers(0, n, n)].mean() for _ in range(500)]
    ci_low, ci_high = np.percentile(means, [2.5, 97.5])
    return {"label": label, "n": n, "mean": mean, "win": win,
            "ci_low": ci_low, "ci_high": ci_high}


def main():
    print("=" * 90)
    print("短倉策略診斷 — 3231 緯創 + 6125 廣運")
    print("=" * 90)

    # Best variants from memory
    variants = [
        ("3231", 0.030, 0.30, 0.005, 0.005, 0.020),  # 3231 best 1
        ("3231", 0.020, 0.30, 0.010, 0.005, 0.015),  # 3231 best 2
        ("3231", 0.020, 0.30, 0.010, 0.005, 0.010),  # 3231 best 3
        ("6125", 0.020, 0.50, 0.005, 0.005, 0.020),  # 6125 Tier A 邊緣
    ]

    for tk, pump, vol, ret, sbuf, tp in variants:
        label_base = f"{tk} pump{pump*100:.0f}/vol{vol*100:.0f}/ret{ret*100:.1f}/tp{tp*100:.0f}"
        print(f"\n{'─'*90}")
        print(f"📊 {label_base}")
        print(f"{'─'*90}")

        # Baseline (no filter)
        base = simulate_short(tk, pump, vol, ret, sbuf, tp)
        if base.empty:
            print(f"  ❌ no trades")
            continue

        # 拆 win/loss 分布
        wins = base[base["net_pct"] > 0]
        losses = base[base["net_pct"] <= 0]
        print(f"  總 n={len(base)} | wins={len(wins)} ({len(wins)/len(base):.0%}) | losses={len(losses)}")
        if not wins.empty:
            print(f"  win 平均 +{wins['net_pct'].mean():.2f}% (max +{wins['net_pct'].max():.2f}%)")
        if not losses.empty:
            print(f"  loss 平均 {losses['net_pct'].mean():.2f}% (max {losses['net_pct'].min():.2f}%)")
            print(f"  loss exit_reason 分布:")
            print(f"    {losses['exit_reason'].value_counts().to_dict()}")
            # loss 在 大盤強 vs 弱
            if losses["twii_above_ma20"].notna().any():
                bull_loss = losses[losses["twii_above_ma20"] == True]
                bear_loss = losses[losses["twii_above_ma20"] == False]
                print(f"    losses 大盤上 MA20: {len(bull_loss)} ({len(bull_loss)/len(losses):.0%})")
                print(f"    losses 大盤下 MA20: {len(bear_loss)} ({len(bear_loss)/len(losses):.0%})")

        # 4 個 fix 比較
        print(f"\n  🔧 變體比較:")
        results = []
        results.append(("baseline (no filter)", evaluate("baseline", base)))

        # A. 大盤弱勢 filter
        f_weak = simulate_short(tk, pump, vol, ret, sbuf, tp, twii_filter="weak")
        results.append(("A. ^TWII < MA20", evaluate("A", f_weak)))

        f_5d = simulate_short(tk, pump, vol, ret, sbuf, tp, twii_filter="down_5d")
        results.append(("B. ^TWII 5d < 0", evaluate("B", f_5d)))

        # C. Gap filter
        f_gap = simulate_short(tk, pump, vol, ret, sbuf, tp, gap_filter=True)
        results.append(("C. 排除 gap risk 高", evaluate("C", f_gap)))

        # D. Wider stop_buffer
        f_wide = simulate_short(tk, pump, vol, ret, sbuf, tp, custom_stop_buf=0.015)
        results.append(("D. stop_buffer 1.5%", evaluate("D", f_wide)))

        # 組合 A+C
        f_ac = simulate_short(tk, pump, vol, ret, sbuf, tp, twii_filter="weak", gap_filter=True)
        results.append(("A+C 組合", evaluate("A+C", f_ac)))

        print(f"\n  {'變體':<22} {'n':>5} {'mean%':>8} {'win':>6} {'CI low':>9} {'CI high':>9} {'狀態':>6}")
        for name, r in results:
            if r["mean"] is None:
                print(f"  {name:<22} no data")
                continue
            sig = "✅" if r["ci_low"] > 0 else ("⚠️" if r["mean"] > 0 else "❌")
            print(f"  {name:<22} {r['n']:>5} "
                  f"{r['mean']:>+7.2f} {r['win']*100:>5.0f}% "
                  f"{r['ci_low']:>+8.2f} {r['ci_high']:>+8.2f} {sig}")

    print(f"\n{'='*90}")
    print(f"判定:")
    print(f"  ✅ CI low > 0  → 真有 alpha，paper trade")
    print(f"  ⚠️ mean > 0 但 CI 跨 0 → 噪音多，需更大樣本")
    print(f"  ❌ mean ≤ 0 → 該 fix 沒救")
    print(f"{'='*90}")


if __name__ == "__main__":
    main()
