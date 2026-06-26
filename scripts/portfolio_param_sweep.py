"""
Portfolio Param Sweep (follow-up of portfolio_level_backtest.py)

問題: 5-slot portfolio CAGR +10.4% 輸 0050 +21.7% (-11.3pp/yr)。要確認是
  (a) 容量太小 → 增大 slots 可解
  (b) 優先序錯 → 改成 high vol_ratio first 可解
  (c) signal mix 拖累 → only-down 可解
  (d) 整套路線 dead-end → 上述都救不回來

掃描:
  MAX_POSITIONS = [5, 10, 20, 50]
  priority      = ["low_vr", "high_vr", "down_only", "up_only"]

每個 config 報 CAGR / Max DD / 執行率。比對 0050 = +21.7%/yr (Max DD -33.8%)
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

# Reuse functions from main backtest
from scripts.portfolio_level_backtest import (
    compute_signal_events,
    compute_daily_nav,
    TW_CACHE,
    START_DATE,
    END_DATE,
)

CACHE_PATH = ROOT / "data" / "cache" / "portfolio_events.parquet"


def get_events() -> pd.DataFrame:
    if CACHE_PATH.exists():
        print(f"  載入 cached events from {CACHE_PATH.relative_to(ROOT)}")
        return pd.read_parquet(CACHE_PATH)
    print("  計算 events (一次性，cache 後可重用)...")
    down = compute_signal_events("down")
    up = compute_signal_events("up")
    events = pd.concat([down, up]).sort_values("signal_date").reset_index(drop=True)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    events["signal_date"] = pd.to_datetime(events["signal_date"])
    events["exit_date"] = pd.to_datetime(events["exit_date"])
    events.to_parquet(CACHE_PATH)
    return events


def simulate_with_priority(events: pd.DataFrame, max_pos: int, priority_mode: str) -> dict:
    """priority_mode: low_vr / high_vr / down_only / up_only / both_high_vr"""
    events = events.copy()
    events["signal_date"] = pd.to_datetime(events["signal_date"]).dt.date
    events["exit_date"] = pd.to_datetime(events["exit_date"]).dt.date

    # Filter signal set
    if priority_mode == "down_only":
        events = events[events["direction"] == "down"]
    elif priority_mode == "up_only":
        events = events[events["direction"] == "up"]

    # Sort by priority
    events = events.copy()
    if priority_mode in ("low_vr",):
        events = events.sort_values(["signal_date", "priority", "vol_ratio"])
    elif priority_mode in ("high_vr", "both_high_vr"):
        events = events.sort_values(
            ["signal_date", "priority", "vol_ratio"], ascending=[True, True, False]
        )
    elif priority_mode == "down_only":
        events = events.sort_values(["signal_date", "vol_ratio"])
    elif priority_mode == "up_only":
        events = events.sort_values(["signal_date", "vol_ratio"])
    events = events.reset_index(drop=True)

    signal_dates = sorted(events["signal_date"].unique())
    open_positions: list = []
    executed_idx: list = []
    skipped_cap_idx: list = []
    skipped_ov_idx: list = []

    for dt in signal_dates:
        open_positions = [p for p in open_positions if p["exit_date"] > dt]
        today_idx = events.index[events["signal_date"] == dt]
        open_tickers = {p["ticker"] for p in open_positions}

        for ri in today_idx:
            sig = events.iloc[ri]
            if sig["ticker"] in open_tickers:
                skipped_ov_idx.append(ri)
                continue
            if len(open_positions) >= max_pos:
                skipped_cap_idx.append(ri)
                continue
            open_positions.append({
                "ticker": sig["ticker"],
                "exit_date": sig["exit_date"],
            })
            open_tickers.add(sig["ticker"])
            executed_idx.append(ri)

    executed = events.iloc[executed_idx] if executed_idx else pd.DataFrame()
    nav_df = compute_daily_nav(executed, max_pos) if len(executed) > 0 else pd.DataFrame()

    if nav_df.empty:
        return {"cagr": 0, "dd": 0, "n_exec": 0, "n_skip_cap": len(skipped_cap_idx),
                "exec_alpha": 0, "skip_alpha": 0}

    start_nav = nav_df["nav"].iloc[0]
    end_nav = nav_df["nav"].iloc[-1]
    yrs = (pd.to_datetime(nav_df["date"].iloc[-1]) - pd.to_datetime(nav_df["date"].iloc[0])).days / 365.25
    cagr = (end_nav / start_nav) ** (1 / yrs) - 1 if yrs > 0 else 0
    running_max = nav_df["nav"].cummax()
    dd = (nav_df["nav"] / running_max - 1).min()

    exec_alpha = float(executed["fwd_return"].mean()) if len(executed) > 0 else 0
    skip_alpha = float(events.iloc[skipped_cap_idx]["fwd_return"].mean()) if skipped_cap_idx else 0

    return {
        "cagr": cagr,
        "dd": dd,
        "n_exec": len(executed_idx),
        "n_skip_cap": len(skipped_cap_idx),
        "exec_alpha": exec_alpha,
        "skip_alpha": skip_alpha,
    }


def main():
    print("=" * 78)
    print("  Portfolio Param Sweep")
    print("  Baseline: 0050 CAGR +21.7%/yr  Max DD -33.8%")
    print("=" * 78)

    events = get_events()
    print(f"  Total events: {len(events):,}")

    configs = []
    for max_pos in [5, 10, 20, 50]:
        for mode in ["low_vr", "high_vr", "down_only", "up_only"]:
            configs.append((max_pos, mode))

    print(f"\n  {'config':<26} {'CAGR':>8} {'MaxDD':>8} {'n_exec':>7} "
          f"{'skip_cap':>9} {'exec_α':>8} {'skip_α':>8} {'vs 0050':>9}")
    print(f"  {'-'*26} {'-'*8} {'-'*8} {'-'*7} {'-'*9} {'-'*8} {'-'*8} {'-'*9}")

    BASELINE_CAGR = 0.217
    rows = []
    for max_pos, mode in configs:
        r = simulate_with_priority(events, max_pos, mode)
        beats = r["cagr"] > BASELINE_CAGR
        marker = "✅" if beats else "❌"
        label = f"max={max_pos:>2} {mode}"
        print(f"  {label:<26} "
              f"{r['cagr']*100:>+7.1f}% "
              f"{r['dd']*100:>+7.1f}% "
              f"{r['n_exec']:>7,} "
              f"{r['n_skip_cap']:>9,} "
              f"{r['exec_alpha']:>+7.2f}% "
              f"{r['skip_alpha']:>+7.2f}% "
              f"{(r['cagr']-BASELINE_CAGR)*100:>+7.1f}pp{marker}")
        rows.append({"config": label, **r})

    # Save sweep results
    df_sweep = pd.DataFrame(rows)
    out = ROOT / "logs" / "portfolio_sweep.csv"
    out.parent.mkdir(exist_ok=True)
    df_sweep.to_csv(out, index=False)
    print(f"\n  ✅ 寫入 {out.relative_to(ROOT)}")

    # Best config
    best = df_sweep.loc[df_sweep["cagr"].idxmax()]
    print(f"\n  🏆 Best: {best['config']}  CAGR={best['cagr']*100:+.1f}%  "
          f"vs 0050 {(best['cagr']-BASELINE_CAGR)*100:+.1f}pp")

    if best["cagr"] < BASELINE_CAGR:
        print(f"\n  🚨 沒有任何 config 贏 0050 — quiet_limit portfolio 路線完蛋")
        print(f"     建議：放棄當主策略，僅作為信號參考 / 選股工具")
    elif best["cagr"] - BASELINE_CAGR < 0.03:
        print(f"\n  ⚠️ 贏 0050 < 3pp，扣 risk-adj 後可能不值得")
    else:
        print(f"\n  ✅ 找到能贏 0050 的 config，可進入 paper trade")


if __name__ == "__main__":
    main()
