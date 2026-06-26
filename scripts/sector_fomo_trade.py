"""
Sector FOMO Trade — Pair lag trade v3。

v2 → v3 架構轉變：
  v2: 固定 leader → 固定 follower
  v3: leader 噴 → 掃描全族群所有小弟 → 進場「散戶 FOMO 指紋」最強的那檔

族群定義（leader → siblings 由市值/流動性遞減）：
  AI server: 3231 緯創 → [2382 廣達, 2356 英業達]
  PCB:       8046 南電 → [3037 欣興, 3189 景碩]
  散熱:      3017 奇鋐 → [3324 雙鴻, 3338 泰碩, 6125 廣運]
  貨櫃:      2615 萬海 → [2603 長榮, 2609 陽明]
  主機板:    2376 技嘉 → [3515 華擎]

Leader 訊號（v2 嚴格條件）：
  - 09:30 後
  - 累積量 > 昨日 × 50%
  - 漲幅 > +3% (從 open)
  - 突破 5 日新高

各小弟 FOMO score（leader 訊號當下計算）：
  S1. 從 open 漲幅 > 0 但 < leader 漲幅（還有空間）
  S2. 1 分量 > 過去 30 分均量 × 2x（散戶湧入）
  S3. 短期 momentum：5 分內收盤遞增

選 FOMO score 最高 1-2 檔進場 + lag 5/10/15 min。
出場：1320 強制 OR trail -1.0%
"""
from __future__ import annotations

import io
import sys
from dataclasses import dataclass
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

SECTORS = [
    # (sector_name, leader, [siblings])
    ("AI server", "3231", ["2382", "2356"]),
    ("PCB",       "8046", ["3037", "3189"]),
    ("散熱",      "3017", ["3324", "3338", "6125"]),
    ("貨櫃",      "2615", ["2603", "2609"]),
    ("主機板",    "2376", ["3515"]),
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


def compute_fomo_score(
    sibling_day: pd.DataFrame,
    signal_time: str,
    leader_ret: float,
) -> dict | None:
    """
    回傳 FOMO score + 詳情。
    score = S1 + S2 + S3，越高越強。
    """
    if sibling_day.empty:
        return None
    open_bar = sibling_day[sibling_day["minute_str"] == "09:00:00"]
    if open_bar.empty:
        open_bar = sibling_day.head(1)
    open_p = float(open_bar.iloc[0]["close"])
    if open_p <= 0:
        return None

    # 截至 signal_time
    so_far = sibling_day[sibling_day["minute_str"] <= signal_time]
    if len(so_far) < 30:
        return None
    sig_bar = so_far.tail(1).iloc[0]
    sig_close = float(sig_bar["close"])
    sib_ret = (sig_close / open_p - 1) * 100

    # S1: 跟漲但 < leader
    if sib_ret <= 0 or sib_ret >= leader_ret:
        return None
    s1 = sib_ret / leader_ret  # 0~1，跟越多分越高

    # S2: 1 分量 vs 過去 30 分均量
    last_30 = so_far.tail(31)
    avg_vol_30 = float(last_30["volume"].head(30).mean())
    cur_vol = float(sig_bar["volume"])
    if avg_vol_30 <= 0:
        return None
    vol_burst = cur_vol / avg_vol_30
    s2 = min(vol_burst / 5, 2.0)  # cap at 2

    # S3: 5 分 momentum
    last_5 = so_far.tail(5)
    if len(last_5) < 5:
        return None
    closes = last_5["close"].astype(float).tolist()
    rising_count = sum(1 for i in range(1, 5) if closes[i] > closes[i - 1])
    s3 = rising_count / 4  # 0~1

    score = s1 + s2 + s3
    return {
        "ret": sib_ret, "vol_burst": vol_burst,
        "rising_5min": rising_count,
        "score": score, "sig_close": sig_close,
    }


def trade_sibling(
    sibling_day: pd.DataFrame,
    signal_time: str,
    lag_min: int,
    exit_strategy: str,
) -> dict | None:
    sig_h, sig_m, _ = signal_time.split(":")
    sig_total = int(sig_h) * 60 + int(sig_m) + lag_min
    if sig_total >= 13 * 60 + 20:
        return None
    eh, em = sig_total // 60, sig_total % 60
    entry_t = f"{eh:02d}:{em:02d}:00"

    entry_bar = sibling_day[sibling_day["minute_str"] == entry_t]
    if entry_bar.empty:
        candidates = sibling_day[sibling_day["minute_str"] >= entry_t].head(1)
        if candidates.empty:
            return None
        entry_bar = candidates
    entry_p = float(entry_bar.iloc[0]["close"])
    actual_t = entry_bar.iloc[0]["minute_str"]

    bars_after = sibling_day[sibling_day["minute_str"] >= actual_t].reset_index(drop=True)
    if len(bars_after) < 2:
        return None

    if exit_strategy == "1320":
        last = bars_after[bars_after["minute_str"] <= "13:20:00"].tail(1)
        if last.empty:
            last = bars_after.tail(1)
        exit_p = float(last.iloc[0]["close"])
    elif exit_strategy.startswith("trail_"):
        trail = float(exit_strategy.replace("trail_", ""))
        peak = entry_p
        exit_p = None
        for _, bar in bars_after.iterrows():
            c = float(bar["close"])
            peak = max(peak, c)
            if (c / peak - 1) * 100 <= trail:
                exit_p = c
                break
            if bar["minute_str"] >= "13:20:00":
                exit_p = c
                break
        if exit_p is None:
            exit_p = float(bars_after.iloc[-1]["close"])
    else:
        return None

    gross = (exit_p / entry_p - 1) * 100
    return {"entry_time": actual_t, "entry_price": entry_p, "exit_price": exit_p,
            "gross": gross, "net": gross - COST}


def get_5d_high(ldf: pd.DataFrame, until_d: date) -> float:
    days_before = sorted([d for d in ldf["date"].unique() if d < until_d])[-5:]
    if not days_before:
        return 0.0
    sub = ldf[ldf["date"].isin(days_before)]
    return float(sub["high"].max()) if not sub.empty else 0.0


def run_sector(
    sector_name: str,
    leader_df: pd.DataFrame,
    siblings_df: dict[str, pd.DataFrame],
    lag: int,
    exit_strat: str,
    pick_top_n: int = 1,
) -> pd.DataFrame:
    daily_vol_l = leader_df.groupby("date")["volume"].sum().to_dict()
    days = sorted(set(leader_df["date"]))
    if siblings_df:
        common_days = days
        for sdf in siblings_df.values():
            common_days = [d for d in common_days if d in set(sdf["date"])]
        days = common_days

    results = []
    for i, d in enumerate(days):
        if i == 0:
            continue
        prev_vol = daily_vol_l.get(days[i - 1], 0)
        high_5d = get_5d_high(leader_df, d)
        leader_day = leader_df[leader_df["date"] == d]
        sig_time = detect_leader_signal(leader_day, prev_vol, high_5d)
        if sig_time is None:
            continue

        # leader ret at signal
        open_bar = leader_day[leader_day["minute_str"] == "09:00:00"]
        if open_bar.empty:
            open_bar = leader_day.head(1)
        open_p = float(open_bar.iloc[0]["close"])
        sig_bar_l = leader_day[leader_day["minute_str"] == sig_time]
        if sig_bar_l.empty:
            continue
        leader_ret = (float(sig_bar_l.iloc[0]["close"]) / open_p - 1) * 100

        # 計算每個 sibling 的 FOMO score
        scored = []
        for sib_tk, sib_df_full in siblings_df.items():
            sib_day = sib_df_full[sib_df_full["date"] == d]
            score_info = compute_fomo_score(sib_day, sig_time, leader_ret)
            if score_info is None:
                continue
            scored.append((sib_tk, sib_day, score_info))

        if not scored:
            continue
        # 取 top N
        scored.sort(key=lambda x: x[2]["score"], reverse=True)
        for sib_tk, sib_day, score_info in scored[:pick_top_n]:
            trade = trade_sibling(sib_day, sig_time, lag, exit_strat)
            if trade is None:
                continue
            results.append({
                "sector": sector_name, "date": d, "sig_time": sig_time,
                "sibling": sib_tk, "fomo_score": score_info["score"],
                "leader_ret": leader_ret, "sib_ret_at_sig": score_info["ret"],
                **trade,
            })
    return pd.DataFrame(results)


def main() -> None:
    print(f"摩擦成本: {COST:.3f}%/round-trip")
    print(f"v3 Sector FOMO Scan")
    print(f"Leader: 量 > 昨日 × {LEADER_VOL_RATIO*100:.0f}%, 漲 > +{LEADER_RET_THRESHOLD}%, 破 5 日新高")
    print()

    data: dict[str, pd.DataFrame] = {}
    for _, leader, siblings in SECTORS:
        for tk in [leader, *siblings]:
            if tk not in data:
                data[tk] = load_minute(tk)
                print(f"  {tk}: {len(data[tk]):,} rows")

    sweep = [(lag, exit_s) for lag in [5, 10, 15] for exit_s in ["1320", "trail_-1.0", "trail_-2.0"]]

    print("\n" + "=" * 90)
    print(f"v3 Sector FOMO Sweep ({len(SECTORS)} sectors × {len(sweep)} configs × top1/top2)")
    print("=" * 90)
    print(f"  {'sector':<11} {'lag':>4} {'exit':<12} {'top':>4} {'n':>4} {'win%':>6} {'mean':>7}")

    all_summary = []
    for sec_name, leader, siblings in SECTORS:
        ldf = data[leader]
        sib_dfs = {tk: data[tk] for tk in siblings if not data[tk].empty}
        if ldf.empty or not sib_dfs:
            print(f"  {sec_name:<11} (no data)")
            continue
        for lag, exit_s in sweep:
            for top_n in [1, 2]:
                trades = run_sector(sec_name, ldf, sib_dfs, lag, exit_s, top_n)
                if trades.empty:
                    continue
                win = (trades["net"] > 0).mean() * 100
                mean = trades["net"].mean()
                flag = " ⭐" if win >= 55 and mean >= 0.3 else ""
                print(f"  {sec_name:<11} {lag:>4} {exit_s:<12} {top_n:>4} {len(trades):>4} "
                      f"{win:>5.1f}% {mean:>+6.2f}%{flag}")
                all_summary.append({
                    "sector": sec_name, "lag": lag, "exit": exit_s, "top_n": top_n,
                    "n": len(trades), "win_pct": win, "mean_net": mean,
                })

    summary = pd.DataFrame(all_summary)
    out = ROOT / "logs" / "sector_fomo_summary.csv"
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
