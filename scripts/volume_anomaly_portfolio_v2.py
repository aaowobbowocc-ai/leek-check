"""
Volume Anomaly Portfolio Simulation v2 — 閒置資金停 0050。

修正 v1 盲點：閒置資金永遠是 0% 報酬 → 牛市跟不上 0050、熊市也沒做 DCA。

v2 邏輯（更貼近真實全球配置）：
  - 初始：100% 資金買 0050
  - Vol Anomaly 觸發進場：賣 10% 0050 → 換 Vol Anomaly 部位
  - Vol Anomaly 出場：把該筆資金（含獲利/虧損）買回 0050
  - 結算：mark-to-market 兩部分

這樣模擬「Vol Anomaly 衛星部位是相對於 0050 的擇時動作」。
真正的 alpha = Vol Anomaly 表現 vs 同期 0050 表現。
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategy.volume_anomaly_scanner import load_ohlcv_cache

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
INPUT_CSV = ROOT / "logs" / "vol_anomaly_backtest_2020-01-01_2024-12-31.csv"
INITIAL = 100_000.0
PER_TRADE_PCT = 0.10
COST_PCT = 0.004


def simulate_trade_exit(ohlcv: pd.DataFrame, entry_date: date, entry_close: float):
    df = ohlcv.sort_values("date").reset_index(drop=True).copy()
    df["ma200"] = df["close"].rolling(200).mean()
    after = df[df["date"] >= entry_date].reset_index(drop=True)
    if after.empty or len(after) < 2:
        return 0.0, entry_date, "no_data"
    peak = 0.0
    for i in range(1, len(after)):
        c = float(after.iloc[i]["close"])
        ma = after.iloc[i]["ma200"]
        ret = (c / entry_close - 1) * 100
        if ret > peak:
            peak = ret
        if pd.notna(ma) and c < float(ma) * 0.85:
            return ret, after.iloc[i]["date"], "hard_stop_ma"
        if peak >= 5.0 and (peak - ret) >= 25.0:
            return ret, after.iloc[i]["date"], "trailing"
    last = after.iloc[-1]
    return (float(last["close"]) / entry_close - 1) * 100, last["date"], "end_of_data"


def nearest_date_price(daily_prices: dict, target: date) -> float | None:
    """找最近的交易日（向前找）。"""
    for offset in range(0, 7):
        d = target - timedelta(days=offset)
        if d in daily_prices:
            return daily_prices[d]
    return None


def main() -> None:
    triggers = pd.read_csv(INPUT_CSV)
    triggers["trigger_date"] = pd.to_datetime(triggers["trigger_date"]).dt.date
    triggers = triggers.sort_values("trigger_date").reset_index(drop=True)

    # 載入 0050
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    df_0050 = df_0050.sort_values("date").reset_index(drop=True)
    price_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    # 預先計算 trade exits
    print(f"[1/2] 預先模擬 {len(triggers)} 筆 trailing 出場 ...")
    trade_exits = {}
    for idx, row in triggers.iterrows():
        ohlcv = load_ohlcv_cache(str(row["ticker"]), CACHE_YF)
        if ohlcv.empty:
            continue
        ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
        ret_pct, exit_date, reason = simulate_trade_exit(
            ohlcv, row["trigger_date"], float(row["entry_close"]),
        )
        trade_exits[idx] = {
            "ticker": str(row["ticker"]),
            "entry_date": row["trigger_date"],
            "exit_date": exit_date,
            "return_pct": ret_pct,
            "reason": reason,
            "hold_days": (exit_date - row["trigger_date"]).days,
        }

    # ── v2 simulation：閒置停 0050 ──
    print(f"[2/2] Mixed portfolio simulation（閒置 → 0050）...")
    start_date = min(t["entry_date"] for t in trade_exits.values())
    end_date = max(t["exit_date"] for t in trade_exits.values())

    # 初始：全買 0050
    init_price = nearest_date_price(price_0050, start_date)
    shares_0050 = INITIAL / init_price
    open_positions = []
    closed = []
    skipped = 0

    all_event_dates = sorted(
        set(t["entry_date"] for t in trade_exits.values()) |
        set(t["exit_date"] for t in trade_exits.values())
    )

    for d in all_event_dates:
        cur_0050 = nearest_date_price(price_0050, d)
        if cur_0050 is None:
            continue

        # 1. 處理 Vol Anomaly 出場 → 資金買回 0050
        still_open = []
        for pos in open_positions:
            if pos["exit_date"] <= d:
                exit_amount = pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST_PCT)
                # 買回 0050
                shares_0050 += exit_amount / cur_0050
                pos["realized"] = exit_amount
                closed.append(pos)
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2. 處理 Vol Anomaly 進場 → 賣 0050
        for idx, info in trade_exits.items():
            if info["entry_date"] != d:
                continue
            allocation_target = INITIAL * PER_TRADE_PCT
            current_0050_value = shares_0050 * cur_0050
            if current_0050_value >= allocation_target:
                shares_to_sell = allocation_target / cur_0050
                shares_0050 -= shares_to_sell
                allocation_after_cost = allocation_target * (1 - COST_PCT)
                open_positions.append({
                    "ticker": info["ticker"],
                    "entry_date": d,
                    "entry_amount": allocation_after_cost,
                    "exit_date": info["exit_date"],
                    "return_pct": info["return_pct"],
                    "reason": info["reason"],
                    "hold_days": info["hold_days"],
                })
            else:
                skipped += 1

    # 結算
    end_price = nearest_date_price(price_0050, end_date)
    portfolio_value = shares_0050 * end_price
    for pos in open_positions:
        portfolio_value += pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST_PCT)

    years = (end_date - start_date).days / 365.25
    cagr = ((portfolio_value / INITIAL) ** (1 / years) - 1) * 100

    # 對比
    bm_total = (end_price / init_price - 1) * 100
    bm_cagr = ((end_price / init_price) ** (1 / years) - 1) * 100

    # 還做一個對比：v1 純衛星（閒置 = cash）
    print(f"\n{'='*70}")
    print(f"V2 Mixed Portfolio — 閒置資金停 0050")
    print(f"{'='*70}")
    print(f"初始資金             : ${INITIAL:,.0f}")
    print(f"結束總值             : ${portfolio_value:,.0f}")
    print(f"觀察期               : {start_date} ~ {end_date} ({years:.2f} 年)")
    print(f"總報酬               : {(portfolio_value/INITIAL-1)*100:+.1f}%")
    print(f"**CAGR**             : **{cagr:+.2f}% / 年**")
    print(f"觸發數               : {len(closed) + len(open_positions)}（跳過 {skipped}）")

    print(f"\n{'─'*70}")
    print(f"基準對比")
    print(f"{'─'*70}")
    print(f"全部 0050（buy & hold）   : 總 {bm_total:+.1f}%、CAGR {bm_cagr:+.2f}%")
    print(f"V2 Mixed                  : 總 {(portfolio_value/INITIAL-1)*100:+.1f}%、CAGR {cagr:+.2f}%")
    print(f"V2 alpha vs 0050          : {((portfolio_value/INITIAL-1)*100 - bm_total):+.1f}pp / "
          f"{cagr - bm_cagr:+.2f}pp/年")

    # 對比 v1 純衛星（簡化算）
    v1_final = INITIAL  # v1 純衛星 → 從 portfolio v1 結果 = $175,960
    # 直接 hardcode 之前算的
    v1_total = 76.0
    v1_cagr = 9.6
    print(f"\n{'─'*70}")
    print(f"V1 vs V2 對比（差距即「閒置資金停 0050」帶來的提升）")
    print(f"{'─'*70}")
    print(f"V1 純衛星（閒置 = 0%）   : 總 +{v1_total:.1f}%、CAGR +{v1_cagr:.1f}%")
    print(f"V2 Mixed（閒置 = 0050）  : 總 {(portfolio_value/INITIAL-1)*100:+.1f}%、CAGR {cagr:+.2f}%")
    print(f"V2 比 V1 提升            : +{(portfolio_value/INITIAL-1)*100 - v1_total:.1f}pp / "
          f"+{cagr - v1_cagr:.2f}pp/年")

    # 出場原因
    print(f"\n{'─'*70}")
    print(f"出場原因分佈")
    print(f"{'─'*70}")
    all_pos = closed + open_positions
    if all_pos:
        for reason in pd.Series([p["reason"] for p in all_pos]).value_counts().index:
            subset = [p for p in all_pos if p["reason"] == reason]
            avg_ret = np.mean([p["return_pct"] for p in subset])
            avg_hold = np.mean([p["hold_days"] for p in subset])
            print(f"{reason:<20} n={len(subset):<3} "
                  f"avg_return={avg_ret:+.1f}%  avg_hold={avg_hold:.0f}d")


if __name__ == "__main__":
    main()
