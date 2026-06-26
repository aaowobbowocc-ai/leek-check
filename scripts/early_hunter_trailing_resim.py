"""
Early Hunter — Trailing -25pp 出場重新模擬。

原始回測用「持滿 12 個月 / 跌破 200MA」，此腳本改用：
  - Trailing -25pp from peak（高點 > 5% 才啟動）
  - Hard stop：200MA × 0.85
  - 無時間上限（最多 1500 天）

直接讀入已有的 96 筆 entry（early_hunter_*.csv），
re-simulate 出場後重跑 V2 portfolio，比較 alpha 有無改善。
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
INPUT_CSV = ROOT / "logs" / "early_hunter_20260425_160432.csv"
INITIAL = 100_000.0
PER_TRADE_PCT = 0.10
COST_PCT = 0.004
MAX_HOLD_DAYS = 1500


def simulate_trailing_exit(
    ohlcv: pd.DataFrame,
    entry_date: date,
    entry_price: float,
    trailing_pp: float = 25.0,
    hard_stop_ma_pct: float = 0.85,
) -> tuple[float, date, str]:
    """Trailing -25pp + 200MA×0.85 hard stop。回傳 (return_pct, exit_date, reason)。"""
    df = ohlcv.sort_values("date").reset_index(drop=True).copy()
    df["ma200"] = df["close"].rolling(200).mean()
    after = df[df["date"] >= entry_date].reset_index(drop=True)
    if after.empty or len(after) < 2:
        return 0.0, entry_date, "no_data"

    peak = 0.0
    for i in range(1, len(after)):
        if i > MAX_HOLD_DAYS:
            ret = (float(after.iloc[i]["close"]) / entry_price - 1) * 100
            return ret, after.iloc[i]["date"], "max_hold"

        c = float(after.iloc[i]["close"])
        ma = after.iloc[i]["ma200"]
        ret = (c / entry_price - 1) * 100

        if ret > peak:
            peak = ret

        if pd.notna(ma) and c < float(ma) * hard_stop_ma_pct:
            return ret, after.iloc[i]["date"], "hard_stop_ma"

        if peak >= 5.0 and (peak - ret) >= trailing_pp:
            return ret, after.iloc[i]["date"], "trailing"

    last = after.iloc[-1]
    return (float(last["close"]) / entry_price - 1) * 100, last["date"], "end_of_data"


def nearest_price(prices: dict, target: date) -> float | None:
    for i in range(7):
        d = target - timedelta(days=i)
        if d in prices:
            return prices[d]
    return None


def run_v2_portfolio(
    trade_exits: list[dict],
    core_prices: dict[date, float],
    start_date: date,
    end_date: date,
) -> dict:
    init_price = nearest_price(core_prices, start_date)
    if init_price is None:
        return {"error": "no init price"}

    shares_core = INITIAL / init_price
    open_positions = []
    closed = []
    skipped = 0

    all_events = sorted(
        set(t["entry_date"] for t in trade_exits) |
        set(t["exit_date"] for t in trade_exits)
    )

    for d in all_events:
        cur_core = nearest_price(core_prices, d)
        if cur_core is None:
            continue

        # 出場
        still_open = []
        for pos in open_positions:
            if pos["exit_date"] <= d:
                exit_amount = pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST_PCT)
                shares_core += exit_amount / cur_core
                closed.append(pos)
            else:
                still_open.append(pos)
        open_positions = still_open

        # 進場
        for t in trade_exits:
            if t["entry_date"] != d:
                continue
            allocation = INITIAL * PER_TRADE_PCT
            if shares_core * cur_core >= allocation:
                shares_core -= allocation / cur_core
                open_positions.append({
                    "ticker": t["ticker"], "entry_date": d,
                    "entry_amount": allocation * (1 - COST_PCT),
                    "exit_date": t["exit_date"],
                    "return_pct": t["return_pct"],
                    "reason": t["reason"],
                    "hold_days": (t["exit_date"] - d).days,
                })
            else:
                skipped += 1

    end_price = nearest_price(core_prices, end_date)
    if end_price is None:
        end_price = init_price

    portfolio_value = shares_core * end_price
    for pos in open_positions:
        portfolio_value += pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST_PCT)

    years = (end_date - start_date).days / 365.25
    cagr = ((portfolio_value / INITIAL) ** (1 / years) - 1) * 100

    return {
        "final_value": portfolio_value,
        "total_return_pct": (portfolio_value / INITIAL - 1) * 100,
        "cagr": cagr,
        "years": years,
        "n_trades": len(closed) + len(open_positions),
        "skipped": skipped,
        "closed": closed,
        "still_open": open_positions,
    }


def main() -> None:
    df = pd.read_csv(INPUT_CSV)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["entry_price"] = df["entry_price"].astype(float)
    df["ticker"] = df["ticker"].astype(str)
    print(f"[1/3] 讀入 {len(df)} 筆 Early Hunter entry")

    # Re-simulate exits with trailing
    print(f"[2/3] Trailing -25pp 重新模擬出場 ...")
    trade_exits = []
    no_data = 0
    for _, row in df.iterrows():
        ohlcv = load_ohlcv_cache(row["ticker"], CACHE_YF)
        if ohlcv.empty:
            no_data += 1
            continue
        ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
        ret, exit_d, reason = simulate_trailing_exit(
            ohlcv, row["entry_date"], row["entry_price"],
        )
        trade_exits.append({
            "ticker": row["ticker"],
            "entry_date": row["entry_date"],
            "exit_date": exit_d,
            "return_pct": ret,
            "reason": reason,
            "orig_return_pct": float(row["gross_return_pct"]),
            "orig_exit_reason": row["exit_reason"],
        })

    print(f"    {len(trade_exits)} 筆成功，{no_data} 筆無資料")

    # 比較原始 vs trailing 出場結果
    orig_returns = [float(r) for r in df["gross_return_pct"]]
    new_returns = [t["return_pct"] for t in trade_exits]
    new_hold = [(t["exit_date"] - t["entry_date"]).days for t in trade_exits]

    print(f"\n{'─'*60}")
    print(f"出場重新模擬對比（逐筆）")
    print(f"{'─'*60}")
    print(f"  {'指標':<20} {'原始(12M/200MA)':<20} {'Trailing -25pp':<20}")
    print(f"  {'-'*60}")
    print(f"  {'N 筆':<20} {len(df):<20} {len(trade_exits):<20}")
    print(f"  {'Mean return':<20} {np.mean(orig_returns):>+8.2f}%          {np.mean(new_returns):>+8.2f}%")
    print(f"  {'Median return':<20} {np.median(orig_returns):>+8.2f}%          {np.median(new_returns):>+8.2f}%")
    print(f"  {'Win rate':<20} {sum(r>0 for r in orig_returns)/len(orig_returns)*100:>8.1f}%          {sum(r>0 for r in new_returns)/len(new_returns)*100:>8.1f}%")
    print(f"  {'Avg hold days':<20} {np.mean(df['hold_days']):>8.0f}d            {np.mean(new_hold):>8.0f}d")

    # 出場原因分佈
    from collections import Counter
    reason_counts = Counter(t["reason"] for t in trade_exits)
    print(f"\n  出場原因: {dict(reason_counts)}")

    # 儲存 trailing 版 CSV（供 portfolio_unified.py 使用）
    out_csv = ROOT / "logs" / "early_hunter_trailing_v2.csv"
    rows = []
    for t in trade_exits:
        rows.append({
            "ticker": t["ticker"],
            "entry_date": t["entry_date"],
            "exit_date": t["exit_date"],
            "gross_return_pct": round(t["return_pct"], 2),
            "exit_reason": t["reason"],
            "hold_days": (t["exit_date"] - t["entry_date"]).days,
        })
    pd.DataFrame(rows).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"  → 儲存至 {out_csv}")

    # V2 Portfolio simulation
    print(f"\n[3/3] V2 Portfolio：閒置資金停 0050 ...")
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    start_date = min(t["entry_date"] for t in trade_exits)
    end_date = max(t["exit_date"] for t in trade_exits)

    result = run_v2_portfolio(trade_exits, prices_0050, start_date, end_date)

    # 0050 Buy & Hold 同期
    p_start = nearest_price(prices_0050, start_date)
    p_end = nearest_price(prices_0050, end_date)
    years = (end_date - start_date).days / 365.25
    bh_cagr = ((p_end / p_start) ** (1 / years) - 1) * 100 if p_start and years > 0 else 0
    bh_total = (p_end / p_start - 1) * 100 if p_start else 0
    alpha = result["cagr"] - bh_cagr

    print(f"\n{'='*70}")
    print(f"Early Hunter — Trailing -25pp V2 Portfolio")
    print(f"{'='*70}")
    print(f"觀察期                 : {start_date} ~ {end_date} ({result['years']:.2f} 年)")
    print(f"結束總值               : ${result['final_value']:,.0f}")
    print(f"總報酬                 : {result['total_return_pct']:>+.1f}%")
    print(f"CAGR                   : {result['cagr']:>+.2f}%")
    print(f"觸發數                 : {result['n_trades']} 筆（跳過 {result['skipped']}）")

    print(f"\n{'─'*70}")
    print(f"0050 Buy & Hold (同期) : {bh_total:>+.1f}%  CAGR {bh_cagr:>+.2f}%")
    print(f"Early Hunter Trailing  : {result['total_return_pct']:>+.1f}%  CAGR {result['cagr']:>+.2f}%")
    print(f"Alpha vs 0050          : {alpha:>+.2f}pp/年")

    # 原始 Early Hunter V2 結果對比
    print(f"\n{'─'*70}")
    print(f"改進對比（同框架 V2）")
    print(f"{'─'*70}")
    print(f"  原始（12M/200MA）     CAGR +13.82%  Alpha -13.16pp  ← 持太久回吐")
    print(f"  Trailing -25pp        CAGR {result['cagr']:>+.2f}%  Alpha {alpha:>+.2f}pp")


if __name__ == "__main__":
    main()
