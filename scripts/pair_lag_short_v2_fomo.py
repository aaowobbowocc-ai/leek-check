"""
Pair Lag SHORT v2 — 跟著大戶倒貨，等 follower 自己量爆才進場。

v1 vs v2 差異：
  v1: fixed lag (5/10/15/30 分後進場)
  v2: 等 follower 自己 1 分量爆（散戶 FOMO 確認）才進場

進場流程：
  1. Leader 噴：量爆 + 漲幅 > 3% + 破 5 日新高 (signal time T_lead)
  2. 從 T_lead 起監控 follower 的 1 分量
  3. follower 1 分量 > 過去 30 分均量 × X 倍（散戶 FOMO 進場）
  4. 在量爆當下 close 反手放空
  5. 防守 stop = 量爆前 30 分內 high + buffer（散戶推不過 = 壓力）
  6. 移動停利：跌出 1% 後啟動 trail，從最低反彈 Y% 即出
  7. 13:20 強制平倉

收割邏輯：
  - 散戶 FOMO 量爆推升 = 主力倒貨給散戶
  - 我們跟主力一起空，比散戶早幾分鐘 detect
  - 量爆出現的瞬間 = 主力出貨完畢 = 反轉時點
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
COST_SHORT = 0.55

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


def detect_leader_signal(day_df: pd.DataFrame, prev_day_total_vol: int, high_5d: float) -> str | None:
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
        if (float(bar["close"]) / open_p - 1) * 100 < LEADER_RET_THRESHOLD:
            continue
        if float(bar["close"]) <= high_5d:
            continue
        return bar["minute_str"]
    return None


def short_on_follower_fomo(
    follower_day: pd.DataFrame,
    leader_signal_time: str,
    vol_burst_mult: float,
    vol_burst_window: int,
    stop_buffer_pct: float,
    trail_tp_pct: float,
    max_wait_min: int = 60,
) -> dict | None:
    """
    從 leader signal 後監控 follower 量爆 → 進場 short。
    """
    bars = follower_day.reset_index(drop=True)
    bars_after_lead = bars[bars["minute_str"] > leader_signal_time].reset_index(drop=True)
    if len(bars_after_lead) < vol_burst_window + 5:
        return None

    sig_h, sig_m, _ = leader_signal_time.split(":")
    sig_minute = int(sig_h) * 60 + int(sig_m)

    # 從 leader signal 後找 first 量爆 bar
    fomo_idx = -1
    for i in range(len(bars_after_lead)):
        bar = bars_after_lead.iloc[i]
        # 超過 max_wait 不等
        bh, bm, _ = bar["minute_str"].split(":")
        if int(bh) * 60 + int(bm) - sig_minute > max_wait_min:
            break
        # 13:00 後不抓（接近收盤）
        if bar["minute_str"] > "13:00:00":
            break

        # 過去 N 分均量
        if i < vol_burst_window:
            continue
        recent_vols = bars_after_lead.iloc[max(0, i - vol_burst_window):i]["volume"].astype(float)
        avg_vol = recent_vols.mean()
        if avg_vol <= 0:
            continue
        cur_vol = float(bar["volume"])
        if cur_vol < avg_vol * vol_burst_mult:
            continue
        # 確認漲（FOMO 推升才算）
        prev_close = float(bars_after_lead.iloc[i - 1]["close"])
        if float(bar["close"]) <= prev_close:
            continue
        fomo_idx = i
        break

    if fomo_idx < 0:
        return None    # 沒量爆訊號

    fomo_bar = bars_after_lead.iloc[fomo_idx]
    entry_price = float(fomo_bar["close"])
    entry_time = fomo_bar["minute_str"]

    # 進場前 30 分 high
    pre_window = bars_after_lead.iloc[max(0, fomo_idx - 30):fomo_idx + 1]
    prev_high = float(pre_window["high"].max())
    stop_price = prev_high * (1 + stop_buffer_pct / 100)

    # 進場後監控
    bars_after_entry = bars_after_lead.iloc[fomo_idx + 1:].reset_index(drop=True)
    if bars_after_entry.empty:
        return None

    lowest = entry_price
    exit_price, exit_t, reason = None, None, "default"
    for _, bar in bars_after_entry.iterrows():
        c = float(bar["close"])
        h = float(bar["high"])
        l = float(bar["low"])

        # Stop loss
        if h >= stop_price:
            exit_price = stop_price
            exit_t = bar["minute_str"]
            reason = "stop_at_prev_high"
            break

        if l < lowest:
            lowest = l

        # Trail TP（先賺 1% 才啟動）
        if lowest < entry_price * 0.99:
            trail_trigger = lowest * (1 + trail_tp_pct / 100)
            if h >= trail_trigger:
                exit_price = trail_trigger
                exit_t = bar["minute_str"]
                reason = "trail_tp"
                break

        if bar["minute_str"] >= "13:20:00":
            exit_price = c
            exit_t = bar["minute_str"]
            reason = "force_1320"
            break

    if exit_price is None:
        exit_price = float(bars_after_entry.iloc[-1]["close"])
        exit_t = bars_after_entry.iloc[-1]["minute_str"]
        reason = "end_of_data"

    gross = (entry_price / exit_price - 1) * 100
    return {
        "leader_sig_time": leader_signal_time,
        "fomo_entry_time": entry_time,
        "wait_min": (int(entry_time[:2]) * 60 + int(entry_time[3:5])) - sig_minute,
        "entry_price": entry_price, "exit_price": exit_price,
        "exit_time": exit_t,
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
    vol_burst_mult: float,
    vol_burst_window: int,
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
        trade = short_on_follower_fomo(
            follower_day, sig_time, vol_burst_mult,
            vol_burst_window, stop_buffer, trail_tp,
        )
        if trade is None:
            continue
        results.append({"pair": pair_name, "date": d, **trade})
    return pd.DataFrame(results)


def main() -> None:
    print(f"放空摩擦: {COST_SHORT:.2f}%")
    print(f"Leader: 量 > {LEADER_VOL_RATIO*100:.0f}%, 漲 > {LEADER_RET_THRESHOLD}%, 破 5d 新高")
    print(f"Follower 量爆: 1 分量 > 過去 30 分均量 × N 倍\n")

    data: dict[str, pd.DataFrame] = {}
    for _, lc, _, fc, _ in PAIRS:
        for c in (lc, fc):
            if c not in data:
                data[c] = load_minute(c)
                print(f"  {c}: {len(data[c]):,} rows")

    sweep = [
        # (vol_burst_mult, vol_burst_window, stop_buffer, trail_tp)
        (3.0, 30, 0.5, 0.5),
        (3.0, 30, 1.0, 1.0),
        (5.0, 30, 0.5, 1.0),
        (5.0, 30, 1.0, 1.0),
        (5.0, 30, 1.0, 1.5),
        (5.0, 60, 1.0, 1.0),
        (8.0, 30, 0.5, 0.5),
        (8.0, 30, 1.0, 1.5),
        (10.0, 30, 1.0, 2.0),
    ]

    print("\n" + "=" * 100)
    print(f"Pair Lag SHORT v2 FOMO Sweep ({len(PAIRS)} pairs × {len(sweep)} configs)")
    print("=" * 100)
    print(f"  {'pair':<11} {'vol×':>5} {'win':>4} {'stop+%':>7} {'tp+%':>6} "
          f"{'n':>4} {'win%':>6} {'mean':>7} {'median':>7}")

    all_summary = []
    for pair_name, lc, _, fc, _ in PAIRS:
        ldf, fdf = data[lc], data[fc]
        if ldf.empty or fdf.empty:
            print(f"  {pair_name:<11} (no data)")
            continue
        for vbm, vbw, stop_buf, trail_tp in sweep:
            trades = run_pair(pair_name, ldf, fdf, vbm, vbw, stop_buf, trail_tp)
            if trades.empty:
                continue
            win = (trades["net"] > 0).mean() * 100
            mean = trades["net"].mean()
            median = trades["net"].median()
            flag = " ⭐" if win >= 55 and mean >= 0.3 else ""
            print(f"  {pair_name:<11} {vbm:>5.1f} {vbw:>4d} {stop_buf:>+6.1f}% "
                  f"{trail_tp:>+5.1f}% {len(trades):>4} {win:>5.1f}% "
                  f"{mean:>+6.2f}% {median:>+6.2f}%{flag}")
            all_summary.append({
                "pair": pair_name, "vol_mult": vbm, "vol_window": vbw,
                "stop_buf": stop_buf, "trail_tp": trail_tp,
                "n": len(trades), "win_pct": win,
                "mean_net": mean, "median_net": median,
            })

    summary = pd.DataFrame(all_summary)
    out = ROOT / "logs" / "pair_lag_short_v2_summary.csv"
    summary.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")

    print("\n" + "=" * 100)
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
