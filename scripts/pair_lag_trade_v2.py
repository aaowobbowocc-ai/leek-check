"""
Pair Lag Trade v2 — 大幅收緊 leader threshold。

v1 結果（leader 量30% + 漲1.5%）：全部失敗，每 pair n=88-183（太鬆）。

v2 收緊：
  Leader 訊號 = 累積量 > 昨日 × 50% AND 漲 > +3%（從 30%/+1.5% 收緊）
  + Catalyst 條件：leader 必須創 5 日新高（突破近期壓力）
  + 同日 cooldown：訊號後 60 分內不重複（避免 stuck signal）
  + 09:30 後才接受訊號（避開開盤 30 分雜訊）

Sweep:
  Lag time:   5, 10, 15, 30 分（去掉 60 分減少 noise）
  Exit:       1320, trail_-1.0, trail_-2.0
"""
from __future__ import annotations

import io
import sys
from datetime import date
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
COST = CostConfig(tax_rate_discount=0.5).total_cost_ratio() * 100

PAIRS = [
    ("AI server",  "3231", "緯創",   "2382", "廣達"),
    ("PCB",        "8046", "南電",   "3037", "欣興"),
    ("散熱",       "3017", "奇鋐",   "3324", "雙鴻"),
    ("貨櫃",       "2615", "萬海",   "2603", "長榮"),
    ("主機板",     "2376", "技嘉",   "3515", "華擎"),
]

# v2 收緊參數
LEADER_VOL_RATIO = 0.50        # 累積量 > 昨日 × 50%（從 30% 收緊）
LEADER_RET_THRESHOLD = 3.0     # 漲幅 > +3%（從 +1.5% 收緊）
CATALYST_5D_HIGH_BREAK = True  # 必須創 5 日新高
COOLDOWN_MIN = 60              # 同 ticker 訊號 cooldown
EARLIEST_SIGNAL = "09:30:00"   # 開盤 30 分後才接受訊號


def load_minute(ticker: str) -> pd.DataFrame:
    files = sorted(CACHE_MIN.glob(f"{ticker}_*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["minute_str"] = df["dt"].dt.strftime("%H:%M:%S")
    return df.sort_values("dt").reset_index(drop=True)


def detect_leader_signal_v2(
    day_df: pd.DataFrame,
    prev_day_total_vol: int,
    high_5d: float,
) -> str | None:
    """
    v2 leader 訊號：
      - 09:30 後
      - 累積量 > 昨日 × 50%
      - 漲幅 > +3% (從當日 open)
      - 突破 5 日高 (catalyst confirm)
    """
    if day_df.empty or prev_day_total_vol <= 0 or high_5d <= 0:
        return None
    open_bar = day_df[day_df["minute_str"] == "09:00:00"]
    if open_bar.empty:
        open_bar = day_df.head(1)
    open_p = float(open_bar.iloc[0]["close"])
    if open_p <= 0:
        return None

    cum_vol = 0
    for _, bar in day_df.iterrows():
        if bar["minute_str"] < EARLIEST_SIGNAL or bar["minute_str"] > "12:00:00":
            cum_vol += float(bar["volume"])
            continue
        cum_vol += float(bar["volume"])
        if cum_vol < prev_day_total_vol * LEADER_VOL_RATIO:
            continue
        ret_pct = (float(bar["close"]) / open_p - 1) * 100
        if ret_pct < LEADER_RET_THRESHOLD:
            continue
        if CATALYST_5D_HIGH_BREAK and float(bar["close"]) <= high_5d:
            continue
        return bar["minute_str"]
    return None


def trade_follower(
    follower_day: pd.DataFrame,
    signal_time: str,
    lag_minutes: int,
    exit_strategy: str = "1320",
) -> dict | None:
    sig_h, sig_m, _ = signal_time.split(":")
    sig_total = int(sig_h) * 60 + int(sig_m) + lag_minutes
    if sig_total >= 13 * 60 + 20:
        return None
    entry_h, entry_m = sig_total // 60, sig_total % 60
    entry_time = f"{entry_h:02d}:{entry_m:02d}:00"

    entry_bar = follower_day[follower_day["minute_str"] == entry_time]
    if entry_bar.empty:
        candidates = follower_day[follower_day["minute_str"] >= entry_time].head(1)
        if candidates.empty:
            return None
        entry_bar = candidates
    entry_price = float(entry_bar.iloc[0]["close"])
    actual_entry = entry_bar.iloc[0]["minute_str"]

    bars_after = follower_day[follower_day["minute_str"] >= actual_entry].reset_index(drop=True)
    if len(bars_after) < 2:
        return None

    if exit_strategy == "1320":
        exit_bar = bars_after[bars_after["minute_str"] <= "13:20:00"].tail(1)
        if exit_bar.empty:
            exit_bar = bars_after.tail(1)
        exit_price = float(exit_bar.iloc[0]["close"])
    elif exit_strategy.startswith("trail_"):
        trail = float(exit_strategy.replace("trail_", ""))
        peak = entry_price
        exit_price = None
        for _, bar in bars_after.iterrows():
            c = float(bar["close"])
            peak = max(peak, c)
            if (c / peak - 1) * 100 <= trail:
                exit_price = c
                break
            if bar["minute_str"] >= "13:20:00":
                exit_price = c
                break
        if exit_price is None:
            exit_price = float(bars_after.iloc[-1]["close"])
    else:
        return None

    gross = (exit_price / entry_price - 1) * 100
    return {
        "entry_time": actual_entry,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross": gross, "net": gross - COST,
    }


def get_5d_high(leader_df: pd.DataFrame, until_date: date) -> float:
    """leader 過去 5 個交易日 high 的最大值。"""
    days_before = sorted([d for d in leader_df["date"].unique() if d < until_date])[-5:]
    if not days_before:
        return 0.0
    sub = leader_df[leader_df["date"].isin(days_before)]
    return float(sub["high"].max()) if not sub.empty else 0.0


def run(
    pair_name: str,
    leader_df: pd.DataFrame,
    follower_df: pd.DataFrame,
    lag: int,
    exit_strat: str,
) -> pd.DataFrame:
    daily_vol = leader_df.groupby("date")["volume"].sum().to_dict()
    days = sorted(set(leader_df["date"]) & set(follower_df["date"]))
    results = []
    last_signal_day = None
    for i, d in enumerate(days):
        if i == 0:
            continue
        prev_vol = daily_vol.get(days[i - 1], 0)
        high_5d = get_5d_high(leader_df, d)
        leader_day = leader_df[leader_df["date"] == d]
        follower_day = follower_df[follower_df["date"] == d]
        sig_time = detect_leader_signal_v2(leader_day, prev_vol, high_5d)
        if sig_time is None:
            continue
        trade = trade_follower(follower_day, sig_time, lag, exit_strat)
        if trade is None:
            continue
        results.append({"pair": pair_name, "date": d, "sig_time": sig_time, **trade})
    return pd.DataFrame(results)


def main() -> None:
    print(f"摩擦成本: {COST:.3f}%")
    print(f"v2 Leader 訊號: 量 > 昨日 × {LEADER_VOL_RATIO*100:.0f}%, 漲 > +{LEADER_RET_THRESHOLD}%, "
          f"破 5 日新高, 09:30 後")
    print()

    data: dict[str, pd.DataFrame] = {}
    for _, lc, _, fc, _ in PAIRS:
        for c in (lc, fc):
            if c not in data:
                data[c] = load_minute(c)
                print(f"  {c}: {len(data[c]):,} rows")

    sweep = [(lag, exit_s) for lag in [5, 10, 15, 30] for exit_s in ["1320", "trail_-1.0", "trail_-2.0"]]

    print("\n" + "=" * 80)
    print(f"Pair Lag Trade v2 ({len(PAIRS)} pairs × {len(sweep)} configs)")
    print("=" * 80)
    print(f"  {'pair':<12} {'lag':>4} {'exit':<12} {'n':>4} {'win%':>6} {'mean':>7} {'median':>7}")

    all_summary = []
    for pair_name, lc, _, fc, _ in PAIRS:
        ldf, fdf = data[lc], data[fc]
        if ldf.empty or fdf.empty:
            print(f"  {pair_name:<12} (no data: {lc} or {fc})")
            continue
        for lag, exit_s in sweep:
            trades = run(pair_name, ldf, fdf, lag, exit_s)
            if trades.empty:
                print(f"  {pair_name:<12} {lag:>4} {exit_s:<12} 0   (no signals)")
                continue
            win = (trades["net"] > 0).mean() * 100
            mean = trades["net"].mean()
            median = trades["net"].median()
            flag = " ⭐" if win >= 55 and mean >= 0.3 else ""
            print(f"  {pair_name:<12} {lag:>4} {exit_s:<12} {len(trades):>4} "
                  f"{win:>5.1f}% {mean:>+6.2f}% {median:>+6.2f}%{flag}")
            all_summary.append({
                "pair": pair_name, "lag": lag, "exit": exit_s,
                "n": len(trades), "win_pct": win,
                "mean_net": mean, "median_net": median,
            })

    summary = pd.DataFrame(all_summary)
    out = ROOT / "logs" / "pair_lag_trade_v2_summary.csv"
    summary.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")

    print("\n" + "=" * 80)
    pass_c = summary[(summary["win_pct"] >= 55) & (summary["mean_net"] >= 0.3)]
    if len(pass_c) > 0:
        print(f"✅ {len(pass_c)} 個過 gate")
        print(pass_c.to_string(index=False))
    else:
        print("❌ 0 個過 gate")
        if not summary.empty:
            print("\n最佳 5 個（按 mean_net）:")
            print(summary.nlargest(5, "mean_net").to_string(index=False))


if __name__ == "__main__":
    main()
