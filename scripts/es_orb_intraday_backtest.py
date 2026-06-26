"""
ES Opening Range Breakout (ORB) Intraday Backtest — Phase B-2

策略邏輯（經典 Larry Williams ORB）：
  - 美盤 cash session 09:30 ET 開盤
  - 09:00-10:00 ET 的 1H bar 作為 Opening Range（OR）
  - OR_high / OR_low / OR_size = OR_high - OR_low
  - 從 10:00 ET 起追蹤突破：
    - 後續 1H bar close > OR_high → 多單（next bar open 進場）
    - 後續 1H bar close < OR_low  → 空單
  - Stop loss: OR mid-point (進場後反向 OR/2)
  - Take profit: 進場價 ± OR_size × 1.5
  - 強制平倉: 15:00 ET（避過夜，留 buffer 給 prop firm overnight rule）
  - 一天最多 1 筆 trade（避 hyperactive）

Gate（prop firm 框架）：
  - PF > 1.5
  - Win rate > 50%
  - Max loss/trade < 1.5% (留 buffer)
  - Mean net > +0.05%
  - Avg hold < 6h
  - Best Day Rule: 最佳單日 < 累積獲利 50%

摩擦：
  - MES round-trip $1.74，notional $30K
  - cost_pct = 0.0058% per round-trip
"""
from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "scripts" / "output"
OUT_DIR.mkdir(exist_ok=True, parents=True)

# 參數
OR_HOUR_ET = 9       # 09:00-10:00 ET = OR
ENTRY_AFTER_HOUR = 10  # 從 10:00 ET 起追蹤突破
EOD_HOUR_ET = 15     # 15:00 ET 強制平倉
TP_MULTIPLE = 1.5    # 1.5R take profit
SL_FRACTION = 0.5    # OR mid-point as stop (= 0.5 × OR size 反向)
REVERSE_MODE = True  # True = fade breakout (反向), False = momentum breakout

# 摩擦（per leg single direction round-trip）
COST_PER_RT = 1.74
AVG_NOTIONAL = 30_000
COST_PCT = COST_PER_RT / AVG_NOTIONAL * 100  # ~0.0058%


def fetch_1h() -> pd.DataFrame:
    """抓 ES=F 1H 資料，轉 ET 時區"""
    import yfinance as yf
    print("  抓取 ES=F 730 天 1H 資料...")
    es = yf.Ticker("ES=F").history(period="730d", interval="1h")
    if es.empty:
        raise RuntimeError("yfinance 抓不到 ES=F")
    df = es[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    # 轉 ET 時區
    df.index = df.index.tz_convert("America/New_York")
    df["et_date"] = df.index.date
    df["et_hour"] = df.index.hour
    print(f"  Range: {df.index[0]} ~ {df.index[-1]} ({len(df)} bars)")
    return df


def simulate_orb(df: pd.DataFrame) -> pd.DataFrame:
    """ORB 邏輯模擬"""
    trades = []
    # 按 ET 日期分組
    for et_date, day_bars in df.groupby("et_date"):
        # OR bar: ET 09:00-10:00
        or_bars = day_bars[day_bars["et_hour"] == OR_HOUR_ET]
        if or_bars.empty:
            continue
        or_bar = or_bars.iloc[0]
        or_high = or_bar["high"]
        or_low = or_bar["low"]
        or_size = or_high - or_low
        if or_size <= 0:
            continue

        # 進場追蹤期：10:00-15:00 ET
        track_bars = day_bars[
            (day_bars["et_hour"] >= ENTRY_AFTER_HOUR) &
            (day_bars["et_hour"] < EOD_HOUR_ET)
        ].copy().sort_index()

        if track_bars.empty:
            continue

        # 找第一個突破
        position = None  # "long" or "short"
        entry_price = None
        entry_dt = None
        stop = None
        target = None

        for j, (idx, bar) in enumerate(track_bars.iterrows()):
            if position is None:
                # 檢測突破：bar close 突破 OR
                breakout_up = bar["close"] > or_high
                breakout_down = bar["close"] < or_low

                if breakout_up:
                    if j + 1 < len(track_bars):
                        next_bar = track_bars.iloc[j + 1]
                        # Reverse mode: fade breakout (突破上緣 → 做空)
                        position = "short" if REVERSE_MODE else "long"
                        entry_price = next_bar["open"]
                        entry_dt = next_bar.name
                        if position == "long":
                            stop = entry_price - or_size * SL_FRACTION
                            target = entry_price + or_size * TP_MULTIPLE
                        else:
                            # Fade: stop 在 breakout 延續方向，target 在 OR mid 或更下
                            stop = entry_price + or_size * SL_FRACTION
                            target = entry_price - or_size * TP_MULTIPLE
                        continue
                elif breakout_down:
                    if j + 1 < len(track_bars):
                        next_bar = track_bars.iloc[j + 1]
                        position = "long" if REVERSE_MODE else "short"
                        entry_price = next_bar["open"]
                        entry_dt = next_bar.name
                        if position == "short":
                            stop = entry_price + or_size * SL_FRACTION
                            target = entry_price - or_size * TP_MULTIPLE
                        else:
                            stop = entry_price - or_size * SL_FRACTION
                            target = entry_price + or_size * TP_MULTIPLE
                        continue

            else:
                # 已有部位 → 檢測 stop / target / EOD
                exit_price = None
                exit_reason = None

                if position == "long":
                    if bar["low"] <= stop:
                        exit_price = stop
                        exit_reason = "stop"
                    elif bar["high"] >= target:
                        exit_price = target
                        exit_reason = "target"
                else:
                    if bar["high"] >= stop:
                        exit_price = stop
                        exit_reason = "stop"
                    elif bar["low"] <= target:
                        exit_price = target
                        exit_reason = "target"

                if exit_price is not None:
                    # 算報酬
                    if position == "long":
                        gross_pct = (exit_price / entry_price - 1) * 100
                    else:
                        gross_pct = (entry_price / exit_price - 1) * 100
                    net_pct = gross_pct - 2 * COST_PCT  # round trip
                    trades.append({
                        "et_date": et_date, "entry_dt": entry_dt, "exit_dt": bar.name,
                        "direction": position, "or_high": or_high, "or_low": or_low,
                        "or_size_pct": (or_size / entry_price) * 100,
                        "entry_price": entry_price, "exit_price": exit_price,
                        "stop": stop, "target": target,
                        "hours_held": j - track_bars.index.get_loc(entry_dt) + 1 if entry_dt in track_bars.index else 0,
                        "gross_pct": round(gross_pct, 4),
                        "net_pct": round(net_pct, 4),
                        "exit_reason": exit_reason,
                    })
                    position = None
                    break  # 一天最多 1 筆

        # EOD 強制平倉（如果還持倉）
        if position is not None:
            last_bar = track_bars.iloc[-1]
            exit_price = last_bar["close"]
            if position == "long":
                gross_pct = (exit_price / entry_price - 1) * 100
            else:
                gross_pct = (entry_price / exit_price - 1) * 100
            net_pct = gross_pct - 2 * COST_PCT
            trades.append({
                "et_date": et_date, "entry_dt": entry_dt, "exit_dt": last_bar.name,
                "direction": position, "or_high": or_high, "or_low": or_low,
                "or_size_pct": (or_size / entry_price) * 100,
                "entry_price": entry_price, "exit_price": exit_price,
                "stop": stop, "target": target,
                "hours_held": 0,
                "gross_pct": round(gross_pct, 4),
                "net_pct": round(net_pct, 4),
                "exit_reason": "eod",
            })

    return pd.DataFrame(trades)


def evaluate(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"verdict": "❌ FAIL", "reason": "no trades"}
    n = len(trades)
    wins = (trades["net_pct"] > 0).sum()
    losses = n - wins
    win_rate = wins / n * 100
    gross_wins = trades.loc[trades["net_pct"] > 0, "net_pct"].sum()
    gross_losses = abs(trades.loc[trades["net_pct"] <= 0, "net_pct"].sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    mean_net = trades["net_pct"].mean()
    cum_net = trades["net_pct"].sum()
    max_loss = trades["net_pct"].min()
    max_win = trades["net_pct"].max()

    # Best Day Rule
    daily = trades.groupby("et_date")["net_pct"].sum()
    best_day = daily.max()
    best_day_pct_of_total = (best_day / cum_net * 100) if cum_net > 0 else 0

    # Drawdown
    cumret = trades["net_pct"].cumsum()
    rolling_max = cumret.cummax()
    max_dd = (cumret - rolling_max).min()

    # Reasons
    reason_dist = trades["exit_reason"].value_counts(normalize=True) * 100

    pf_pass = pf > 1.5
    loss_pass = abs(max_loss) < 1.5
    win_pass = win_rate > 50
    mean_pass = mean_net > 0.05
    best_day_pass = best_day_pct_of_total < 50  # Best Day Rule

    return {
        "n_trades": n, "wins": wins, "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(pf, 3),
        "mean_net_pct": round(mean_net, 4),
        "cum_net_pct": round(cum_net, 2),
        "max_loss_pct": round(max_loss, 4),
        "max_win_pct": round(max_win, 4),
        "max_dd_pct": round(max_dd, 4),
        "best_day_pct": round(best_day, 4),
        "best_day_share_of_total": round(best_day_pct_of_total, 2),
        "exit_target_pct": round(reason_dist.get("target", 0), 1),
        "exit_stop_pct": round(reason_dist.get("stop", 0), 1),
        "exit_eod_pct": round(reason_dist.get("eod", 0), 1),
        "gate_pf_15": pf_pass,
        "gate_loss_15": loss_pass,
        "gate_win_50": win_pass,
        "gate_mean_005": mean_pass,
        "gate_best_day_50": best_day_pass,
        "verdict": "✅ PASS" if all([pf_pass, loss_pass, win_pass, mean_pass, best_day_pass]) else "❌ FAIL",
    }


def main():
    print("=" * 70)
    print("  ES ORB Intraday Backtest — Phase B-2")
    print("=" * 70)

    df = fetch_1h()
    trades = simulate_orb(df)
    print(f"\n  模擬完成 → {len(trades)} 筆 trade")

    r = evaluate(trades)
    print("\n" + "=" * 70)
    print("  📊 結果統計")
    print("=" * 70)
    for k, v in r.items():
        print(f"  {k:30s} = {v}")

    if not trades.empty:
        # 月度
        trades["year_month"] = pd.to_datetime(trades["et_date"]).dt.to_period("M")
        print("\n  📅 月度績效 (last 12 months):")
        monthly = trades.groupby("year_month").agg(
            n=("net_pct", "count"),
            wr=("net_pct", lambda s: (s > 0).mean() * 100),
            mean=("net_pct", "mean"),
            cum=("net_pct", "sum"),
        ).tail(12)
        print(monthly.to_string())

        # 方向分布
        print("\n  🧭 方向 / 出場原因分布:")
        dir_stats = trades.groupby("direction").agg(
            n=("net_pct", "count"),
            wr=("net_pct", lambda s: (s > 0).mean() * 100),
            mean=("net_pct", "mean"),
        )
        print(dir_stats.to_string())

    today = datetime.now().strftime("%Y%m%d")
    out_csv = OUT_DIR / f"es_orb_intraday_{today}.csv"
    trades.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ trades → {out_csv}")

    print("\n" + "=" * 70)
    print(f"  🎯 Verdict: {r['verdict']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
