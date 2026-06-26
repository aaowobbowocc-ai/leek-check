"""
Pair Lag SHORT Trade — 空跟漲股 + 防守前高 + 移動停利。

「二哥力竭」收割散戶邏輯：
  1. 大哥（leader）噴 + 量爆（user 的 v3 signal）
  2. lag 後二哥 FOMO 跟漲，量爆 + 上影線力竭
  3. 在二哥力竭信號出現時，**反手放空**
  4. 防守前高 = stop loss above 進場前 N 分鐘 high + buffer
  5. 移動停利 = 從進場後最低點反彈 X% 即出
  6. 13:20 強制平倉（避免軋空隔日）

Sweep:
  Lag time:        5, 10, 15, 30 分
  Stop loss:       前高 + 0.5% / 1.0% / 1.5% (buffer)
  Trail TP:        最低點 +0.5% / +1.0% / +1.5%
  Exit:            13:20 force

放空成本（含融券手續費）:
  fee 0.1425% × 2 + 證交稅 0.3% × 0.5（當沖）+ 滑價 0.1% × 2 + 借券費 0.08% (annualized → daily)
  ≈ 0.55% per round-trip（略高於做多）

注意：實務上有 禁券名單 + 軋空風險，本測純驗證 alpha 是否存在。
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

CACHE_MIN = ROOT / "data" / "cache" / "finmind" / "minute"
COST_SHORT = 0.55       # 放空往返成本（略高於做多 0.49）

PAIRS = [
    ("AI server",  "3231", "緯創",   "2382", "廣達"),
    ("PCB",        "8046", "南電",   "3037", "欣興"),
    ("散熱",       "3017", "奇鋐",   "3324", "雙鴻"),
    ("貨櫃",       "2615", "萬海",   "2603", "長榮"),
    ("主機板",     "2376", "技嘉",   "3515", "華擎"),
]

LEADER_VOL_RATIO = 0.50
LEADER_RET_THRESHOLD = 3.0
EARLIEST_SIGNAL = "09:30:00"


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


def detect_leader_signal(
    day_df: pd.DataFrame,
    prev_day_total_vol: int,
    high_5d: float,
) -> str | None:
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
        cum_vol += float(bar["volume"])
        if bar["minute_str"] < EARLIEST_SIGNAL or bar["minute_str"] > "12:00:00":
            continue
        if cum_vol < prev_day_total_vol * LEADER_VOL_RATIO:
            continue
        ret_pct = (float(bar["close"]) / open_p - 1) * 100
        if ret_pct < LEADER_RET_THRESHOLD:
            continue
        if float(bar["close"]) <= high_5d:
            continue
        return bar["minute_str"]
    return None


def short_follower(
    follower_day: pd.DataFrame,
    signal_time: str,
    lag_min: int,
    stop_buffer_pct: float,
    trail_tp_pct: float,
) -> dict | None:
    """
    放空：signal + lag 後進場放空。
    防守 stop = 進場前 N 分鐘 high + buffer
    Trail TP = 從進場後最低點反彈 trail_tp_pct
    13:20 force exit
    """
    sig_h, sig_m, _ = signal_time.split(":")
    sig_total = int(sig_h) * 60 + int(sig_m) + lag_min
    if sig_total >= 13 * 60 + 20:
        return None
    eh, em = sig_total // 60, sig_total % 60
    entry_t = f"{eh:02d}:{em:02d}:00"

    entry_bar = follower_day[follower_day["minute_str"] == entry_t]
    if entry_bar.empty:
        candidates = follower_day[follower_day["minute_str"] >= entry_t].head(1)
        if candidates.empty:
            return None
        entry_bar = candidates
    entry_price = float(entry_bar.iloc[0]["close"])
    actual_t = entry_bar.iloc[0]["minute_str"]

    # 進場前 30 分內的 high 當前高
    sig_minute = int(sig_h) * 60 + int(sig_m)
    pre_start_minute = max(sig_minute - 30, 9 * 60)  # 09:00 起算
    pre_h, pre_m = pre_start_minute // 60, pre_start_minute % 60
    pre_window_start = f"{pre_h:02d}:{pre_m:02d}:00"
    pre_window = follower_day[
        (follower_day["minute_str"] >= pre_window_start)
        & (follower_day["minute_str"] <= actual_t)
    ]
    if pre_window.empty:
        return None
    prev_high = float(pre_window["high"].max())
    stop_price = prev_high * (1 + stop_buffer_pct / 100)

    bars_after = follower_day[follower_day["minute_str"] > actual_t].reset_index(drop=True)
    if bars_after.empty:
        return None

    # 短線追蹤：最低點 + 反彈 trail_tp_pct
    lowest = entry_price
    exit_price, exit_t, reason = None, None, "default"
    for _, bar in bars_after.iterrows():
        c = float(bar["close"])
        h = float(bar["high"])
        l = float(bar["low"])

        # Stop loss：價格漲過 stop_price → 出場（虧）
        if h >= stop_price:
            exit_price = stop_price
            exit_t = bar["minute_str"]
            reason = "stop_at_prev_high"
            break

        # 更新最低
        if l < lowest:
            lowest = l

        # Trail TP：從最低反彈 trail_tp_pct → 出場（賺）
        # 但要等先有獲利才啟動 trail
        if lowest < entry_price * 0.99:    # 至少先賺 1% 才啟動 trail
            trail_trigger = lowest * (1 + trail_tp_pct / 100)
            if h >= trail_trigger:
                exit_price = trail_trigger
                exit_t = bar["minute_str"]
                reason = "trail_tp"
                break

        # 13:20 強制
        if bar["minute_str"] >= "13:20:00":
            exit_price = c
            exit_t = bar["minute_str"]
            reason = "force_1320"
            break

    if exit_price is None:
        exit_price = float(bars_after.iloc[-1]["close"])
        exit_t = bars_after.iloc[-1]["minute_str"]
        reason = "end_of_data"

    # 空單 P&L: gain when exit_price < entry_price
    gross = (entry_price / exit_price - 1) * 100
    return {
        "entry_time": actual_t, "exit_time": exit_t,
        "entry_price": entry_price, "exit_price": exit_price,
        "prev_high": prev_high, "stop_price": stop_price,
        "lowest_after_entry": lowest,
        "gross": gross, "net": gross - COST_SHORT,
        "reason": reason,
    }


def get_5d_high(ldf: pd.DataFrame, until_d: date) -> float:
    days_before = sorted([d for d in ldf["date"].unique() if d < until_d])[-5:]
    if not days_before:
        return 0.0
    sub = ldf[ldf["date"].isin(days_before)]
    return float(sub["high"].max()) if not sub.empty else 0.0


def run_pair(
    pair_name: str,
    leader_df: pd.DataFrame,
    follower_df: pd.DataFrame,
    lag: int,
    stop_buffer: float,
    trail_tp: float,
) -> pd.DataFrame:
    daily_vol = leader_df.groupby("date")["volume"].sum().to_dict()
    days = sorted(set(leader_df["date"]) & set(follower_df["date"]))
    results = []
    for i, d in enumerate(days):
        if i == 0:
            continue
        prev_vol = daily_vol.get(days[i - 1], 0)
        high_5d = get_5d_high(leader_df, d)
        leader_day = leader_df[leader_df["date"] == d]
        follower_day = follower_df[follower_df["date"] == d]
        if leader_day.empty or follower_day.empty:
            continue
        sig_time = detect_leader_signal(leader_day, prev_vol, high_5d)
        if sig_time is None:
            continue
        trade = short_follower(follower_day, sig_time, lag, stop_buffer, trail_tp)
        if trade is None:
            continue
        results.append({"pair": pair_name, "date": d, "sig_time": sig_time, **trade})
    return pd.DataFrame(results)


def main() -> None:
    print(f"放空摩擦成本: {COST_SHORT:.2f}%")
    print(f"Leader 訊號: 量 > 昨日 × {LEADER_VOL_RATIO*100:.0f}%, 漲 > +{LEADER_RET_THRESHOLD}%, 破 5 日新高\n")

    data: dict[str, pd.DataFrame] = {}
    for _, lc, _, fc, _ in PAIRS:
        for c in (lc, fc):
            if c not in data:
                data[c] = load_minute(c)
                print(f"  {c}: {len(data[c]):,} rows")

    sweep = [
        # (lag, stop_buffer_pct, trail_tp_pct)
        (5, 0.5, 0.5), (5, 0.5, 1.0), (5, 1.0, 1.0),
        (10, 0.5, 0.5), (10, 0.5, 1.0), (10, 1.0, 1.0), (10, 1.5, 1.5),
        (15, 0.5, 1.0), (15, 1.0, 1.0),
        (30, 1.0, 1.5),
    ]

    print("\n" + "=" * 90)
    print(f"Pair Lag SHORT Sweep ({len(PAIRS)} pairs × {len(sweep)} configs)")
    print("=" * 90)
    print(f"  {'pair':<11} {'lag':>4} {'stop+%':>7} {'tp+%':>6} {'n':>4} {'win%':>6} {'mean':>7} {'median':>7}")

    all_summary = []
    for pair_name, lc, _, fc, _ in PAIRS:
        ldf, fdf = data[lc], data[fc]
        if ldf.empty or fdf.empty:
            print(f"  {pair_name:<11} (no data)")
            continue
        for lag, stop_buf, trail_tp in sweep:
            trades = run_pair(pair_name, ldf, fdf, lag, stop_buf, trail_tp)
            if trades.empty:
                continue
            win = (trades["net"] > 0).mean() * 100
            mean = trades["net"].mean()
            median = trades["net"].median()
            flag = " ⭐" if win >= 55 and mean >= 0.3 else ""
            print(f"  {pair_name:<11} {lag:>4} {stop_buf:>+6.1f}% {trail_tp:>+5.1f}% "
                  f"{len(trades):>4} {win:>5.1f}% {mean:>+6.2f}% {median:>+6.2f}%{flag}")
            all_summary.append({
                "pair": pair_name, "lag": lag,
                "stop_buf": stop_buf, "trail_tp": trail_tp,
                "n": len(trades), "win_pct": win,
                "mean_net": mean, "median_net": median,
            })

    summary = pd.DataFrame(all_summary)
    out = ROOT / "logs" / "pair_lag_short_summary.csv"
    summary.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")

    print("\n" + "=" * 90)
    pass_c = summary[(summary["win_pct"] >= 55) & (summary["mean_net"] >= 0.3)]
    if len(pass_c) > 0:
        print(f"✅ {len(pass_c)} 個過 gate")
        print(pass_c.to_string(index=False))
    else:
        print("❌ 0 個過 gate")
        if not summary.empty:
            print("\n最佳 5 個:")
            print(summary.nlargest(5, "mean_net").to_string(index=False))


if __name__ == "__main__":
    main()
