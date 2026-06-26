"""
Volume Anomaly Portfolio Simulation（Phase 18b 最終驗證）。

回答終極問題：「完全照建議跑，年報酬率是多少？」

模擬假設：
  - 衛星部位規模 = 100,000（normalize）
  - 每筆觸發配 10%（讓最多 10 筆同時持有 → 滿載 ~ 多少時間）
  - 出場規則：Trailing -25pp from peak（v2 軌跡校準版）
  - 硬停損：跌破 200MA × 0.85（極端情境）
  - 資金不足時跳過該觸發（佇列邏輯）
  - 不考慮交易成本（保守可加 0.4% buffer）

對比基準：
  - 0050（同期持有）
  - All cash（衛星完全閒置）

輸出：
  - 5 年總報酬 / CAGR
  - 同時持倉數時間軸
  - 最大資金占用 / 利用率
  - 與 0050 對比
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
SATELLITE_ASSET = 100_000.0
PER_TRADE_PCT = 0.10            # 每筆 10%
TRAILING_DRAWDOWN_PP = 25.0
HARD_STOP_MA_PCT = 0.85
TRADING_COST_PCT = 0.004        # 進+出總成本（券商 + 證交稅 + 滑價）保守


def simulate_trade_exit(
    ohlcv: pd.DataFrame, entry_date: date, entry_close: float,
    trailing_pp: float = TRAILING_DRAWDOWN_PP,
    hard_stop_ma_pct: float = HARD_STOP_MA_PCT,
    max_hold_days: int = 1500,
) -> tuple[float, date, str]:
    """
    模擬單筆 trailing -25pp + 200MA × 0.85 hard stop 的出場。
    回傳 (return_pct, exit_date, reason)
    """
    df = ohlcv.sort_values("date").reset_index(drop=True).copy()
    df["ma200"] = df["close"].rolling(200).mean()
    after = df[df["date"] >= entry_date].reset_index(drop=True)
    if after.empty or len(after) < 2:
        return 0.0, entry_date, "no_data"

    peak = 0.0
    for i in range(1, len(after)):
        if i > max_hold_days:
            ret = (float(after.iloc[i]["close"]) / entry_close - 1) * 100
            return ret, after.iloc[i]["date"], "max_hold"

        c = float(after.iloc[i]["close"])
        ma = after.iloc[i]["ma200"]
        ret = (c / entry_close - 1) * 100

        if ret > peak:
            peak = ret

        # 硬停損：跌破 200MA × 0.85
        if pd.notna(ma) and c < float(ma) * hard_stop_ma_pct:
            return ret, after.iloc[i]["date"], "hard_stop_ma"

        # Trailing -25pp（高點 > 5% 才啟動避免雜訊）
        if peak >= 5.0 and (peak - ret) >= trailing_pp:
            return ret, after.iloc[i]["date"], "trailing"

    last = after.iloc[-1]
    return (float(last["close"]) / entry_close - 1) * 100, last["date"], "end_of_data"


def run_portfolio_sim(
    triggers: pd.DataFrame,
    initial_capital: float = SATELLITE_ASSET,
    per_trade_pct: float = PER_TRADE_PCT,
    cost_pct: float = TRADING_COST_PCT,
) -> dict:
    triggers = triggers.sort_values("trigger_date").reset_index(drop=True).copy()
    triggers["trigger_date"] = pd.to_datetime(triggers["trigger_date"]).dt.date

    cash = initial_capital
    open_positions = []   # list of dicts
    closed_trades = []
    skipped = 0

    # 預先模擬所有 trade 的出場（避免重複算）
    print(f"[1/3] 預先模擬 {len(triggers)} 筆 trailing 出場 ...")
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
            "entry_close": float(row["entry_close"]),
            "exit_date": exit_date,
            "return_pct": ret_pct,
            "reason": reason,
            "hold_days": (exit_date - row["trigger_date"]).days,
        }

    # 模擬 portfolio
    print(f"[2/3] Portfolio simulation ...")
    all_dates = sorted(set(t["entry_date"] for t in trade_exits.values()) |
                       set(t["exit_date"] for t in trade_exits.values()))

    daily_open_count = []
    daily_capital_deployed = []
    daily_dates = []

    for d in all_dates:
        # 出場處理
        still_open = []
        for pos in open_positions:
            if pos["exit_date"] <= d:
                exit_amount = pos["entry_amount"] * (1 + pos["return_pct"] / 100)
                exit_amount *= (1 - cost_pct)   # 出場成本
                cash += exit_amount
                pos["realized"] = exit_amount
                closed_trades.append(pos)
            else:
                still_open.append(pos)
        open_positions = still_open

        # 進場處理（同日所有觸發）
        for idx, exit_info in trade_exits.items():
            if exit_info["entry_date"] != d:
                continue
            allocation = initial_capital * per_trade_pct
            if cash >= allocation:
                cash -= allocation
                pos = {
                    "ticker": exit_info["ticker"],
                    "entry_date": d,
                    "entry_amount": allocation * (1 - cost_pct),  # 進場成本
                    "exit_date": exit_info["exit_date"],
                    "return_pct": exit_info["return_pct"],
                    "reason": exit_info["reason"],
                    "hold_days": exit_info["hold_days"],
                }
                open_positions.append(pos)
            else:
                skipped += 1

        daily_open_count.append(len(open_positions))
        daily_capital_deployed.append(initial_capital - cash)
        daily_dates.append(d)

    # 結算：把仍持有的部位用最後價格估值
    final_open_value = 0.0
    last_date = max(t["exit_date"] for t in trade_exits.values())
    for pos in open_positions:
        if pos["exit_date"] > last_date:
            est = pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - cost_pct)
            final_open_value += est
    total_value = cash + final_open_value + sum(t.get("realized", 0) for t in closed_trades) - sum(
        t.get("realized", 0) for t in closed_trades
    )
    # 簡化：所有 trade 都已 close（因為 trade_exits 給定了 exit_date）
    total_value = cash + sum(p["entry_amount"] * (1 + p["return_pct"] / 100) * (1 - cost_pct)
                              for p in open_positions)

    return {
        "initial_capital": initial_capital,
        "final_value": total_value,
        "total_trades": len(closed_trades) + len(open_positions),
        "skipped": skipped,
        "closed_trades": closed_trades,
        "still_open": open_positions,
        "daily_dates": daily_dates,
        "daily_open_count": daily_open_count,
        "daily_capital_deployed": daily_capital_deployed,
        "trade_exits": trade_exits,
    }


def benchmark_0050(start: date, end: date) -> tuple[float, float]:
    """0050 同期持有報酬。"""
    df = load_ohlcv_cache("0050", CACHE_YF)
    if df.empty:
        return 0.0, 0.0
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date")
    s = df[df["date"] >= start]
    e = df[df["date"] <= end]
    if s.empty or e.empty:
        return 0.0, 0.0
    s_close = float(s.iloc[0]["close"])
    e_close = float(e.iloc[-1]["close"])
    total = (e_close / s_close - 1) * 100
    years = (end - start).days / 365.25
    cagr = ((e_close / s_close) ** (1 / years) - 1) * 100
    return total, cagr


def main() -> None:
    if not INPUT_CSV.exists():
        print(f"❌ {INPUT_CSV} 不存在")
        return

    triggers = pd.read_csv(INPUT_CSV)
    print(f"載入 {len(triggers)} 個觸發樣本")

    result = run_portfolio_sim(triggers)

    initial = result["initial_capital"]
    final = result["final_value"]
    total_return_pct = (final / initial - 1) * 100

    closed = result["closed_trades"]
    if closed:
        all_trades = closed + result["still_open"]
        first_date = min(t["entry_date"] for t in all_trades)
        last_date = max(t["exit_date"] for t in all_trades)
        years = (last_date - first_date).days / 365.25
        cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0
    else:
        years = 0
        cagr = 0

    bm_total, bm_cagr = benchmark_0050(first_date, last_date)

    print(f"\n{'='*70}")
    print(f"Portfolio Simulation Result（Trailing -25pp + 200MA × 0.85）")
    print(f"{'='*70}")
    print(f"初始衛星資金     : ${initial:,.0f}")
    print(f"結束總值         : ${final:,.0f}")
    print(f"總報酬           : {total_return_pct:+.1f}%")
    print(f"觀察期           : {first_date} ~ {last_date} ({years:.2f} 年)")
    print(f"CAGR             : **{cagr:+.1f}% / 年**")
    print(f"觸發數           : {result['total_trades']}（跳過 {result['skipped']}）")

    print(f"\n{'─'*70}")
    print(f"基準對比")
    print(f"{'─'*70}")
    print(f"0050 同期        : 總 {bm_total:+.1f}%、CAGR {bm_cagr:+.1f}%")
    print(f"Vol Anomaly      : 總 {total_return_pct:+.1f}%、CAGR {cagr:+.1f}%")
    print(f"超額報酬 (alpha) : {total_return_pct - bm_total:+.1f}pp / {cagr - bm_cagr:+.1f}pp/年")

    # 出場原因分佈
    print(f"\n{'─'*70}")
    print(f"出場原因分佈")
    print(f"{'─'*70}")
    all_trades = closed + result["still_open"]
    reasons = pd.Series([t["reason"] for t in all_trades]).value_counts()
    for reason, count in reasons.items():
        avg_ret = np.mean([t["return_pct"] for t in all_trades if t["reason"] == reason])
        avg_hold = np.mean([t["hold_days"] for t in all_trades if t["reason"] == reason])
        print(f"{reason:<20} n={count:<3} avg_return={avg_ret:+.1f}%  avg_hold={avg_hold:.0f}d")

    # 同時持倉統計
    print(f"\n{'─'*70}")
    print(f"資金利用率 / 同時持倉")
    print(f"{'─'*70}")
    if result["daily_open_count"]:
        max_open = max(result["daily_open_count"])
        avg_open = np.mean(result["daily_open_count"])
        max_deployed = max(result["daily_capital_deployed"])
        avg_deployed = np.mean(result["daily_capital_deployed"])
        deployed_pct = avg_deployed / initial * 100
        print(f"最大同時持倉      : {max_open} 檔")
        print(f"平均同時持倉      : {avg_open:.1f} 檔")
        print(f"資金平均部署比例  : {deployed_pct:.1f}%（其餘 {100-deployed_pct:.1f}% 閒置）")
        print(f"最高資金部署      : ${max_deployed:,.0f}")

    # 加上交易成本後的影響
    print(f"\n{'─'*70}")
    print(f"已扣除交易成本：每筆進+出共 {TRADING_COST_PCT*100:.1f}%（券商 + 證交稅 + 滑價）")
    print(f"{'─'*70}")


if __name__ == "__main__":
    main()
