"""
Portfolio-Level Backtest (Gemini #1 / ChatGPT #8)

問題: 個別訊號 backtest 假設無限資本——每個觸發都執行。
     現實: max 5 positions，capital constraint 可能嚴重削減 portfolio alpha。
     特別危險: market crash 時大量 limitdown 同日觸發 → 容量滿 → 跳過最有 alpha 的機會。

模擬兩個主力訊號 (quiet_limitdown 20d + quiet_limitup 20d)，期間 2020-2025:
  1. 預算所有事件（next-day open entry + 實際 forward return）
  2. 模擬 portfolio (最多 MAX_POSITIONS 倉，equal weight)
     優先序: limitdown > limitup（alpha 8.22% vs 4.83%）
             同類型: vol_ratio 低者優先（更「量縮」的訊號更強）
  3. 比較: 執行率 / 選中 vs 跳過的 alpha 差異 / 高 VIX 日 clustering

關鍵問題: capacity constraint 是否在最需要 alpha 時（高 VIX）才大量 bite？
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
COST = 0.78
HOLD_DAYS = 20
MAX_POSITIONS = 5
START_DATE = "2020-01-01"
END_DATE = "2025-12-31"
# priority: lower = higher priority
SIGNAL_PRIORITY = {"down": 0, "up": 1}


def compute_signal_events(direction: str) -> pd.DataFrame:
    """Pre-compute all quiet limitdown/up events with actual forward returns."""
    records = []
    files = list(TW_CACHE.glob("*.parquet"))

    for i, p in enumerate(files):
        if p.stat().st_size < 500:
            continue
        tk = p.stem
        # Skip ETFs and indices
        if not tk.isdigit() or len(tk) != 4:
            continue
        try:
            px = pd.read_parquet(p)
        except Exception:
            continue
        if len(px) < 80:
            continue

        px["date"] = pd.to_datetime(px["date"]).dt.date
        px = px.sort_values("date").reset_index(drop=True)
        if "open" not in px.columns:
            continue
        px["pct"] = px["close"].pct_change() * 100
        px["vol_ma60"] = px["volume"].rolling(60).mean()
        px["vol_ratio"] = px["volume"] / px["vol_ma60"]

        start_dt = pd.to_datetime(START_DATE).date()
        end_dt = pd.to_datetime(END_DATE).date()

        for idx in range(60, len(px) - HOLD_DAYS - 1):
            row = px.iloc[idx]
            if row["date"] < start_dt or row["date"] > end_dt:
                continue

            pct = row["pct"]
            vr = row["vol_ratio"]
            if pd.isna(pct) or pd.isna(vr):
                continue

            is_signal = (
                (pct <= -9.5 if direction == "down" else pct >= 9.5) and vr < 0.8
            )
            if not is_signal:
                continue

            entry = float(px.iloc[idx + 1]["open"])
            if entry <= 0:
                continue
            exit_p = float(px.iloc[idx + HOLD_DAYS]["close"])
            fwd = (exit_p / entry - 1) * 100 - COST

            records.append({
                "ticker": tk,
                "signal_date": row["date"],
                "exit_date": px.iloc[idx + HOLD_DAYS]["date"],
                "fwd_return": fwd,
                "direction": direction,
                "vol_ratio": float(vr),
                "priority": SIGNAL_PRIORITY[direction],
            })

        if (i + 1) % 500 == 0:
            print(f"  [{i+1}/{len(files)}] {direction} events={len(records)}")

    return pd.DataFrame(records).sort_values("signal_date").reset_index(drop=True)


def simulate_portfolio(events: pd.DataFrame, max_pos: int = MAX_POSITIONS) -> dict:
    """
    Simulate portfolio with position slot constraints.

    Priority: limitdown > limitup, then lower vol_ratio within same type.
    Non-overlapping: same ticker cannot be held twice.

    Returns dict with executed/skipped events and stats.
    """
    events = events.copy().sort_values(
        ["signal_date", "priority", "vol_ratio"]
    ).reset_index(drop=True)

    signal_dates = sorted(events["signal_date"].unique())

    open_positions: list[dict] = []
    executed_rows: list[int] = []
    skipped_cap_rows: list[int] = []
    skipped_overlap_rows: list[int] = []

    for dt in signal_dates:
        # Close positions whose exit_date <= current signal_date
        open_positions = [p for p in open_positions if p["exit_date"] > dt]

        # Today's signals (already sorted by priority, vol_ratio)
        today_mask = events["signal_date"] == dt
        today_idx = events.index[today_mask]

        open_tickers = {p["ticker"] for p in open_positions}

        for row_i in today_idx:
            sig = events.iloc[row_i]
            if sig["ticker"] in open_tickers:
                skipped_overlap_rows.append(row_i)
                continue
            if len(open_positions) >= max_pos:
                skipped_cap_rows.append(row_i)
                continue
            open_positions.append({
                "ticker": sig["ticker"],
                "exit_date": sig["exit_date"],
                "idx": row_i,
            })
            open_tickers.add(sig["ticker"])
            executed_rows.append(row_i)

    executed = events.iloc[executed_rows] if executed_rows else pd.DataFrame()
    skipped_cap = events.iloc[skipped_cap_rows] if skipped_cap_rows else pd.DataFrame()
    skipped_ov = events.iloc[skipped_overlap_rows] if skipped_overlap_rows else pd.DataFrame()

    return {
        "executed": executed,
        "skipped_capacity": skipped_cap,
        "skipped_overlap": skipped_ov,
        "n_executed": len(executed_rows),
        "n_skipped_cap": len(skipped_cap_rows),
        "n_skipped_ov": len(skipped_overlap_rows),
    }


def compute_daily_nav(executed: pd.DataFrame,
                       max_pos: int = MAX_POSITIONS) -> pd.DataFrame:
    """Slot-based portfolio NAV simulation.

    Each slot holds 1/max_pos of starting NAV, evolves independently via trades.
    Total NAV = sum of slot values. Cash earns 0%.

    Critical: replaces the buggy `prod(1 + r/N)` formula which conflates parallel
    slots with sequential compounding (over-states CAGR by 30-50pp).
    """
    if executed.empty:
        return pd.DataFrame()

    events = executed.sort_values("signal_date").reset_index(drop=True)

    slot_values = [1.0 / max_pos] * max_pos
    slot_exit: list = [None] * max_pos
    slot_return = [0.0] * max_pos

    all_dates = sorted(set(
        list(events["signal_date"]) + list(events["exit_date"])
    ))

    nav_history = []

    for dt in all_dates:
        # Close slots whose exit_date <= today
        for s in range(max_pos):
            if slot_exit[s] is not None and slot_exit[s] <= dt:
                slot_values[s] *= (1 + slot_return[s] / 100)
                slot_exit[s] = None
                slot_return[s] = 0.0

        # Open positions for today's signals (FIFO into free slots)
        today_sigs = events[events["signal_date"] == dt]
        for _, sig in today_sigs.iterrows():
            free = next(
                (s for s in range(max_pos) if slot_exit[s] is None),
                None,
            )
            if free is None:
                continue
            slot_exit[free] = sig["exit_date"]
            slot_return[free] = float(sig["fwd_return"])

        nav_history.append({"date": dt, "nav": sum(slot_values)})

    return pd.DataFrame(nav_history)


def report_alpha(data: pd.DataFrame, label: str) -> float:
    """Print alpha stats for a set of events, return mean."""
    if data.empty:
        print(f"  {label}: n=0")
        return 0.0
    arr = data["fwd_return"].dropna()
    if len(arr) < 5:
        print(f"  {label}: n={len(arr)} (too few)")
        return float(arr.mean()) if len(arr) > 0 else 0.0
    t, p = stats.ttest_1samp(arr, 0, alternative="greater")
    win = (arr > 0).mean() * 100
    print(f"  {label}: n={len(arr):,}  mean={arr.mean():+.2f}%  "
          f"t={t:+.2f}  p={p:.4f}  win={win:.1f}%")
    return float(arr.mean())


def signals_per_day_clustering(events: pd.DataFrame) -> pd.Series:
    return events.groupby("signal_date").size().sort_values(ascending=False)


def main():
    print("=" * 72)
    print("  Portfolio-Level Backtest (Gemini #1 / ChatGPT #8)")
    print(f"  Period: {START_DATE} – {END_DATE}  Max positions: {MAX_POSITIONS}")
    print("=" * 72)

    print(f"\n  計算 quiet_limitdown 事件...")
    down = compute_signal_events("down")
    print(f"  計算 quiet_limitup 事件...")
    up = compute_signal_events("up")

    all_events = pd.concat([down, up]).sort_values(
        ["signal_date", "priority", "vol_ratio"]
    ).reset_index(drop=True)

    n_down, n_up = len(down), len(up)
    n_total = len(all_events)
    n_years = 5
    print(f"\n  {'':=<60}")
    print(f"  事件總覽（無資本限制）")
    print(f"  {'':=<60}")
    print(f"  limitdown: {n_down:,}  limitup: {n_up:,}  合計: {n_total:,}")
    print(f"  每年平均: {n_total//n_years:,}/yr  每日平均: {n_total//(n_years*252):.1f}/day")

    print(f"\n  個別訊號 alpha（無資本限制）:")
    alpha_down = report_alpha(down, "limitdown (unrestricted)")
    alpha_up = report_alpha(up, "limitup   (unrestricted)")
    weighted_naive = (alpha_down * n_down + alpha_up * n_up) / n_total
    print(f"\n  加權平均 naive alpha: {weighted_naive:+.2f}%/trade")

    # Signals per day clustering
    spd = signals_per_day_clustering(all_events)
    n_crowded = (spd > MAX_POSITIONS).sum()
    total_overflow = (spd[spd > MAX_POSITIONS] - MAX_POSITIONS).sum()
    print(f"\n  {'':=<60}")
    print(f"  每日訊號數分布（capacity = {MAX_POSITIONS}）")
    print(f"  {'':=<60}")
    print(f"  日均: {spd.mean():.1f}  中位數: {spd.median():.0f}  最高: {spd.max()}")
    print(f"  超過容量 ({MAX_POSITIONS}) 的天數: {n_crowded}  溢出訊號: {total_overflow:,}")
    if n_crowded > 0:
        crowded_dates = spd[spd > MAX_POSITIONS].head(10)
        print(f"  前 5 個最擁擠日（通常是 crash day）:")
        for d, cnt in crowded_dates.head(5).items():
            print(f"    {d}: {cnt} 個訊號 (溢出 {cnt - MAX_POSITIONS})")

    print(f"\n  {'':=<60}")
    print(f"  模擬 portfolio（max {MAX_POSITIONS} positions, priority: down > up）")
    print(f"  {'':=<60}")
    result = simulate_portfolio(all_events, MAX_POSITIONS)

    n_exec = result["n_executed"]
    n_cap = result["n_skipped_cap"]
    n_ov = result["n_skipped_ov"]
    cap_util = n_exec / (n_exec + n_cap) if (n_exec + n_cap) > 0 else 0

    print(f"\n  執行: {n_exec:,}  跳過(容量滿): {n_cap:,}  跳過(overlap): {n_ov:,}")
    print(f"  容量利用率: {cap_util*100:.1f}%（{n_cap/(n_exec+n_cap)*100:.1f}% 因容量被迫放棄）")

    print(f"\n  選中 vs 放棄的 alpha 比較:")
    alpha_exec = report_alpha(result["executed"], "✅ 執行的訊號")
    alpha_skip = report_alpha(result["skipped_capacity"], "⏭️ 跳過的訊號（容量滿）")

    if not result["skipped_capacity"].empty and not result["executed"].empty:
        bias = alpha_skip - alpha_exec
        print(f"\n  📊 Selection Bias Analysis:")
        print(f"    執行 alpha: {alpha_exec:+.2f}%  跳過 alpha: {alpha_skip:+.2f}%")
        if abs(bias) < 0.5:
            print(f"  ✅ alpha 差異 {bias:+.2f}pp — capacity constraint 沒有顯著 selection bias")
            print(f"     執行的訊號代表性良好（先到先得策略合理）")
        elif bias > 0:
            print(f"  🚨 跳過的訊號 alpha 更高 {bias:+.2f}pp — capacity constraint 偏向跳過好機會")
            print(f"     根因: 高 VIX crash day 大量觸發，slots 先被普通訊號佔用")
        else:
            print(f"  ✅ 執行的訊號 alpha 高於跳過 {-bias:+.2f}pp — 優先序有效")

    # Breakdown by direction
    if not result["executed"].empty:
        print(f"\n  執行訊號方向分布:")
        exec_d = result["executed"]
        for direction in ["down", "up"]:
            sub = exec_d[exec_d["direction"] == direction]
            if len(sub) > 0:
                print(f"    {direction}: {len(sub):,} 筆  mean={sub['fwd_return'].mean():+.2f}%")

    # Portfolio CAGR via slot-based daily NAV (correct accounting for parallel slots)
    if not result["executed"].empty:
        nav_df = compute_daily_nav(result["executed"], MAX_POSITIONS)
        if not nav_df.empty:
            start_nav = float(nav_df["nav"].iloc[0])
            end_nav = float(nav_df["nav"].iloc[-1])
            total_ret = end_nav / start_nav - 1
            actual_years = (
                pd.to_datetime(nav_df["date"].iloc[-1])
                - pd.to_datetime(nav_df["date"].iloc[0])
            ).days / 365.25
            cagr = (1 + total_ret) ** (1 / actual_years) - 1 if actual_years > 0 else 0
            # Max drawdown
            running_max = nav_df["nav"].cummax()
            dd = (nav_df["nav"] / running_max - 1).min()
            print(f"\n  📊 Portfolio NAV 模擬 (slot-based, equal weight 1/{MAX_POSITIONS}):")
            print(f"  Total return ({actual_years:.1f}yr): {total_ret*100:+.1f}%")
            print(f"  CAGR:        {cagr*100:+.1f}%/yr")
            print(f"  Max DD:      {dd*100:.1f}%")
            print(f"  (cash earns 0%；實務可加 HIBOR/定存 ~1-2%)")

            # Save NAV
            out = ROOT / "logs" / "portfolio_nav.csv"
            out.parent.mkdir(exist_ok=True)
            nav_df.to_csv(out, index=False)
            print(f"  → 寫入 {out.relative_to(ROOT)} ({len(nav_df)} 筆 NAV)")

            # Compare to 0050
            etf_path = TW_CACHE / "0050.parquet"
            if etf_path.exists():
                try:
                    etf = pd.read_parquet(etf_path)
                    etf["date"] = pd.to_datetime(etf["date"]).dt.date
                    etf = etf.sort_values("date")
                    start_dt = pd.to_datetime(START_DATE).date()
                    end_dt = pd.to_datetime(END_DATE).date()
                    etf = etf[(etf["date"] >= start_dt) & (etf["date"] <= end_dt)]
                    if len(etf) > 2:
                        etf_total = etf["close"].iloc[-1] / etf["close"].iloc[0] - 1
                        etf_yrs = (etf["date"].iloc[-1] - etf["date"].iloc[0]).days / 365.25
                        etf_cagr = (1 + etf_total) ** (1 / etf_yrs) - 1
                        etf_dd = (etf["close"] / etf["close"].cummax() - 1).min()
                        print(f"\n  vs 0050 ({START_DATE}–{END_DATE}):")
                        print(f"  0050 CAGR:   {etf_cagr*100:+.1f}%/yr")
                        print(f"  0050 Max DD: {etf_dd*100:.1f}%")
                        print(f"  Alpha:       {(cagr - etf_cagr)*100:+.1f}pp/yr")
                except Exception as e:
                    print(f"  ⚠️ 0050 比較失敗: {e}")

    print(f"\n  {'':=<60}")
    print(f"  Conclusion")
    print(f"  {'':=<60}")
    total_possible = n_exec + n_cap
    skip_rate = n_cap / total_possible * 100 if total_possible > 0 else 0
    if skip_rate > 20:
        print(f"  🚨 Capital constraint 顯著: {skip_rate:.0f}% 訊號因容量滿被放棄")
        print(f"     個別訊號 alpha ({weighted_naive:+.2f}%) 高估了 portfolio 可實現收益")
        print(f"     建議: 增加 positions 至 8-10，或只執行 limitdown（最強訊號）")
    elif skip_rate > 10:
        print(f"  ⚠️ Capital constraint 中等: {skip_rate:.0f}% 訊號跳過")
        print(f"     Portfolio alpha 略低於個別訊號 naive 估算")
    else:
        print(f"  ✅ Capital constraint 輕微 ({skip_rate:.0f}% skipped)")
        print(f"     Portfolio alpha ≈ individual signal alpha — 無顯著稀釋")


if __name__ == "__main__":
    main()
