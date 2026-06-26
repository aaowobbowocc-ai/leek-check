"""
Pair Lag Trade Backtest — 領漲股訊號 → 等 lag 後跟漲股進場。

5 個 pair：
  AI server: 3231 緯創 → 2382 廣達
  PCB:       8046 南電 → 3037 欣興
  散熱:      3017 奇鋐 → 3324 雙鴻
  貨櫃:      2615 萬海 → 2603 長榮
  主機板:    2376 技嘉 → 3515 華擎

Leader 訊號（任何時刻 09:00-12:00）：
  累積成交量 > 昨日全天 × 30% AND
  從當日 open 漲幅 > +1.5%

Lag 後 follower 進場：
  在 leader 訊號時間 + lag 分鐘進場（用該分鐘 close）

Sweep:
  Lag time:   5, 10, 15, 30, 60 分
  Exit:       13:20 強制 OR trailing -1%

成本：當沖 0.49% per round-trip
驗收 gate：win rate ≥ 55% AND mean net ≥ +0.3%
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.cost_model import CostConfig  # noqa: E402

CACHE_MIN = ROOT / "data" / "cache" / "finmind" / "minute"
COST = CostConfig(tax_rate_discount=0.5).total_cost_ratio() * 100   # 0.49%

PAIRS = [
    ("AI server",  "3231", "緯創",   "2382", "廣達"),
    ("PCB",        "8046", "南電",   "3037", "欣興"),
    ("散熱",       "3017", "奇鋐",   "3324", "雙鴻"),
    ("貨櫃",       "2615", "萬海",   "2603", "長榮"),
    ("主機板",     "2376", "技嘉",   "3515", "華擎"),
]

LEADER_VOL_RATIO = 0.30      # 累積量 > 昨日 × 30%
LEADER_RET_THRESHOLD = 1.5   # 漲幅 > +1.5%


def load_minute_for_ticker(ticker: str) -> pd.DataFrame:
    """讀全部 monthly cache 串成單一 DataFrame。"""
    files = sorted(CACHE_MIN.glob(f"{ticker}_*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["minute_str"] = df["dt"].dt.strftime("%H:%M:%S")
    return df.sort_values("dt").reset_index(drop=True)


def detect_leader_signal_time(day_df: pd.DataFrame, prev_day_total_vol: int) -> str | None:
    """回傳 leader 訊號觸發的 minute_str（HH:MM:SS）；無則 None。"""
    if day_df.empty or prev_day_total_vol <= 0:
        return None
    open_bar = day_df[day_df["minute_str"] == "09:00:00"]
    if open_bar.empty:
        open_bar = day_df.head(1)
    open_p = float(open_bar.iloc[0]["close"])
    if open_p <= 0:
        return None
    # 在 09:00-12:00 範圍掃描
    cum_vol = 0
    for _, bar in day_df.iterrows():
        if bar["minute_str"] < "09:00:00" or bar["minute_str"] > "12:00:00":
            continue
        cum_vol += float(bar["volume"])
        if cum_vol < prev_day_total_vol * LEADER_VOL_RATIO:
            continue
        ret_pct = (float(bar["close"]) / open_p - 1) * 100
        if ret_pct >= LEADER_RET_THRESHOLD:
            return bar["minute_str"]
    return None


def trade_follower(
    follower_day: pd.DataFrame,
    signal_time: str,
    lag_minutes: int,
    exit_strategy: str = "1320",     # "1320" or "trail_-1.0"
) -> dict | None:
    """進場：signal_time + lag 分鐘的 close。出場：13:20 OR trailing。"""
    sig_h, sig_m, sig_s = signal_time.split(":")
    sig_minutes_total = int(sig_h) * 60 + int(sig_m) + lag_minutes
    if sig_minutes_total >= 13 * 60 + 20:
        return None    # 訊號太晚
    entry_h = sig_minutes_total // 60
    entry_m = sig_minutes_total % 60
    entry_time = f"{entry_h:02d}:{entry_m:02d}:00"

    entry_bar = follower_day[follower_day["minute_str"] == entry_time]
    if entry_bar.empty:
        # fallback：取最近一根
        candidates = follower_day[follower_day["minute_str"] >= entry_time].head(1)
        if candidates.empty:
            return None
        entry_bar = candidates
    entry_price = float(entry_bar.iloc[0]["close"])
    actual_entry_time = entry_bar.iloc[0]["minute_str"]

    bars_after = follower_day[follower_day["minute_str"] >= actual_entry_time].reset_index(drop=True)
    if len(bars_after) < 2:
        return None

    if exit_strategy == "1320":
        exit_bar = bars_after[bars_after["minute_str"] <= "13:20:00"].tail(1)
        if exit_bar.empty:
            exit_bar = bars_after.tail(1)
        exit_price = float(exit_bar.iloc[0]["close"])
        exit_time = exit_bar.iloc[0]["minute_str"]
        reason = "force_1320"
    elif exit_strategy.startswith("trail_"):
        trail_pct = float(exit_strategy.replace("trail_", ""))
        peak = entry_price
        exit_price, exit_time, reason = None, None, "default"
        for _, bar in bars_after.iterrows():
            c = float(bar["close"])
            peak = max(peak, c)
            from_peak = (c / peak - 1) * 100
            if from_peak <= trail_pct:
                exit_price = c
                exit_time = bar["minute_str"]
                reason = "trail"
                break
            if bar["minute_str"] >= "13:20:00":
                exit_price = c
                exit_time = bar["minute_str"]
                reason = "force_1320"
                break
        if exit_price is None:
            exit_price = float(bars_after.iloc[-1]["close"])
            exit_time = bars_after.iloc[-1]["minute_str"]
            reason = "end_of_data"
    else:
        return None

    gross = (exit_price / entry_price - 1) * 100
    net = gross - COST
    return {
        "entry_time": actual_entry_time, "exit_time": exit_time,
        "entry_price": entry_price, "exit_price": exit_price,
        "gross": gross, "net": net, "reason": reason,
    }


def run_pair(
    pair_name: str,
    leader_df: pd.DataFrame,
    follower_df: pd.DataFrame,
    lag_minutes: int,
    exit_strategy: str,
) -> pd.DataFrame:
    """跑一個 pair 的全部交易日。"""
    daily_vol_leader = leader_df.groupby("date")["volume"].sum().to_dict()
    unique_days = sorted(set(leader_df["date"].unique()) & set(follower_df["date"].unique()))

    results = []
    for i, d in enumerate(unique_days):
        if i == 0:
            continue
        prev_d = unique_days[i - 1]
        prev_vol = daily_vol_leader.get(prev_d, 0)
        leader_day = leader_df[leader_df["date"] == d]
        follower_day = follower_df[follower_df["date"] == d]
        if leader_day.empty or follower_day.empty:
            continue
        sig_time = detect_leader_signal_time(leader_day, prev_vol)
        if sig_time is None:
            continue
        trade = trade_follower(follower_day, sig_time, lag_minutes, exit_strategy)
        if trade is None:
            continue
        results.append({
            "pair": pair_name, "date": d,
            "leader_signal_time": sig_time,
            **trade,
        })
    return pd.DataFrame(results)


def main() -> None:
    print(f"摩擦成本: {COST:.3f}%/round-trip")
    print(f"Leader 訊號: 累積量 > 昨日 × {LEADER_VOL_RATIO*100:.0f}% AND 漲 >+{LEADER_RET_THRESHOLD}%")

    # Pre-load
    print("\n載入 minute K...")
    data: dict[str, pd.DataFrame] = {}
    for _, l_code, _, f_code, _ in PAIRS:
        for code in (l_code, f_code):
            if code not in data:
                data[code] = load_minute_for_ticker(code)
                print(f"  {code}: {len(data[code]):,} rows")

    # Sweep
    lag_options = [5, 10, 15, 30, 60]
    exit_options = ["1320", "trail_-1.0"]

    print("\n" + "=" * 90)
    print(f"Pair Lag Trade Sweep ({len(PAIRS)} pairs × {len(lag_options)} lags × {len(exit_options)} exits)")
    print("=" * 90)
    print(f"  {'pair':<14} {'lag':>4} {'exit':<12} {'n':>4} {'win%':>6} {'mean':>7} {'median':>7}")

    all_summary = []
    for pair_name, l_code, l_name, f_code, f_name in PAIRS:
        leader_df = data[l_code]
        follower_df = data[f_code]
        if leader_df.empty or follower_df.empty:
            print(f"  {pair_name:<14} (no data)")
            continue
        for lag in lag_options:
            for exit_strat in exit_options:
                trades = run_pair(pair_name, leader_df, follower_df, lag, exit_strat)
                if trades.empty:
                    continue
                win = (trades["net"] > 0).mean() * 100
                mean = trades["net"].mean()
                median = trades["net"].median()
                flag = " ⭐" if win >= 55 and mean >= 0.3 else ""
                print(
                    f"  {pair_name:<14} {lag:>4} {exit_strat:<12} "
                    f"{len(trades):>4} {win:>5.1f}% "
                    f"{mean:>+6.2f}% {median:>+6.2f}%{flag}"
                )
                all_summary.append({
                    "pair": pair_name, "lag": lag, "exit": exit_strat,
                    "n": len(trades), "win_pct": win,
                    "mean_net": mean, "median_net": median,
                })

    summary_df = pd.DataFrame(all_summary)
    out = ROOT / "logs" / "pair_lag_trade_summary.csv"
    summary_df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")

    # Go/no-go
    print("\n" + "=" * 90)
    pass_configs = summary_df[(summary_df["win_pct"] >= 55) & (summary_df["mean_net"] >= 0.3)]
    if len(pass_configs) > 0:
        print(f"✅ {len(pass_configs)} 個配置過 gate")
        print(pass_configs[["pair", "lag", "exit", "n", "win_pct", "mean_net"]].to_string(index=False))
    else:
        print("❌ 無配置過 gate")
        print("   最佳 5 個（按 mean_net）:")
        if not summary_df.empty:
            print(summary_df.nlargest(5, "mean_net")[
                ["pair", "lag", "exit", "n", "win_pct", "mean_net"]
            ].to_string(index=False))


if __name__ == "__main__":
    main()
