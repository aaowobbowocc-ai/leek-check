"""
統一 Portfolio Simulation —— 比較不同核心 ETF + 不同衛星策略 + regime DCA。

核心問題：
  1. 新興 ETF 能否取代 0050 作為核心？
  2. Early Hunter 用 V2 框架（閒置停核心）真實 CAGR 是多少？
  3. 加入 regime-based DCA（cash buffer 在熊市加碼）能加多少？

V2 框架：
  - 初始：(1 - cash_buffer_pct) × init 全買核心 ETF + cash_buffer_pct × init 留 cash
  - trade 進場：賣核心 ETF（按當天價格）→ 買 trade ticker
  - trade 出場：賣 trade ticker → 買回核心 ETF
  - regime DCA（可選）：核心月跌 -10% + 收盤 < 200MA → 用 50% buffer 加碼核心

公平期間：ETF 起始日對齊（用 max(ETF start, first trade date)）
"""
from __future__ import annotations

import io
import sys
from dataclasses import dataclass
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
INITIAL = 100_000.0
PER_TRADE_PCT = 0.10
COST_PCT = 0.004
CORES = ["0050", "00878", "00881", "00891", "00900", "0056", "006208", "00713", "00692"]

ETF_LABELS = {
    "0050":   "元大台50(大盤)",
    "00878":  "永續高息",
    "00881":  "5G/AI主題",
    "00891":  "半導體",
    "00900":  "特選高息",
    "0056":   "高股息",
    "006208": "富邦台50",
    "00713":  "高息低波",
    "00692":  "公司治理100",
}


@dataclass
class Trade:
    ticker: str
    entry_date: date
    exit_date: date
    return_pct: float


def load_etf_prices(ticker: str) -> dict[date, float] | None:
    df = load_ohlcv_cache(ticker, CACHE_YF)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date")
    return dict(zip(df["date"], df["close"].astype(float)))


def nearest_price(prices: dict, target: date, max_offset: int = 7) -> float | None:
    for i in range(max_offset):
        d = target - timedelta(days=i)
        if d in prices:
            return prices[d]
    return None


def compute_etf_buy_hold(prices: dict, start: date, end: date) -> tuple[float, float, float]:
    """回傳 (total_pct, cagr_pct, max_drawdown_pct)。"""
    p_start = nearest_price(prices, start)
    p_end = nearest_price(prices, end)
    if p_start is None or p_end is None or p_start <= 0:
        return 0.0, 0.0, 0.0
    total = (p_end / p_start - 1) * 100
    years = (end - start).days / 365.25
    cagr = ((p_end / p_start) ** (1 / years) - 1) * 100 if years > 0 else 0
    # max drawdown
    dates = sorted(d for d in prices if start <= d <= end)
    vals = [prices[d] for d in dates]
    if not vals:
        return total, cagr, 0
    peak = vals[0]
    max_dd = 0
    for v in vals:
        if v > peak:
            peak = v
        dd = (v / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd
    return total, cagr, max_dd


def simulate_vol_anomaly_exit(ohlcv: pd.DataFrame, entry_date: date, entry_close: float):
    df = ohlcv.sort_values("date").reset_index(drop=True).copy()
    df["ma200"] = df["close"].rolling(200).mean()
    after = df[df["date"] >= entry_date].reset_index(drop=True)
    if after.empty or len(after) < 2:
        return 0.0, entry_date
    peak = 0.0
    for i in range(1, len(after)):
        c = float(after.iloc[i]["close"])
        ma = after.iloc[i]["ma200"]
        ret = (c / entry_close - 1) * 100
        if ret > peak:
            peak = ret
        if pd.notna(ma) and c < float(ma) * 0.85:
            return ret, after.iloc[i]["date"]
        if peak >= 5.0 and (peak - ret) >= 25.0:
            return ret, after.iloc[i]["date"]
    last = after.iloc[-1]
    return (float(last["close"]) / entry_close - 1) * 100, last["date"]


def load_vol_anomaly_trades() -> list[Trade]:
    csv = ROOT / "logs" / "vol_anomaly_backtest_2020-01-01_2024-12-31.csv"
    df = pd.read_csv(csv)
    df["trigger_date"] = pd.to_datetime(df["trigger_date"]).dt.date
    trades = []
    for _, r in df.iterrows():
        ohlcv = load_ohlcv_cache(str(r["ticker"]), CACHE_YF)
        if ohlcv.empty:
            continue
        ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
        ret, exit_d = simulate_vol_anomaly_exit(ohlcv, r["trigger_date"], float(r["entry_close"]))
        trades.append(Trade(
            ticker=str(r["ticker"]), entry_date=r["trigger_date"],
            exit_date=exit_d, return_pct=ret,
        ))
    return trades


def load_early_hunter_trades() -> list[Trade]:
    csv = ROOT / "logs" / "early_hunter_20260425_160432.csv"
    df = pd.read_csv(csv)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.date
    return [
        Trade(
            ticker=str(r["ticker"]), entry_date=r["entry_date"],
            exit_date=r["exit_date"], return_pct=float(r["gross_return_pct"]),
        )
        for _, r in df.iterrows()
    ]


def load_early_hunter_trailing_trades() -> list[Trade]:
    """Early Hunter 用 Trailing -25pp 重新模擬出場的版本。"""
    csv = ROOT / "logs" / "early_hunter_trailing_v2.csv"
    if not csv.exists():
        print("  [WARN] early_hunter_trailing_v2.csv 不存在，跳過")
        return []
    df = pd.read_csv(csv)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.date
    return [
        Trade(
            ticker=str(r["ticker"]), entry_date=r["entry_date"],
            exit_date=r["exit_date"], return_pct=float(r["gross_return_pct"]),
        )
        for _, r in df.iterrows()
    ]


def run_v2_portfolio(
    trades: list[Trade],
    core_prices: dict[date, float],
    start_date: date,
    end_date: date,
    cash_buffer_pct: float = 0.0,
    enable_regime_dca: bool = False,
) -> dict:
    """
    V2 portfolio simulation:
      - 初始 (1 - buffer) 買核心、buffer 留 cash
      - trade 進場：賣核心換 trade
      - trade 出場：買回核心
      - regime DCA：核心月跌 -10% + 200MA 下方 → 50% buffer 加碼核心
    """
    # 過濾：只取 start_date 之後的 trade
    trades = [t for t in trades if start_date <= t.entry_date <= end_date]

    init_price = nearest_price(core_prices, start_date)
    if init_price is None:
        return {"error": "no init price"}

    core_amount_init = INITIAL * (1 - cash_buffer_pct)
    cash_buffer = INITIAL * cash_buffer_pct
    shares_core = core_amount_init / init_price

    open_positions = []
    closed = []
    skipped = 0
    dca_events = []

    # 對於 regime DCA 需要計算 200MA + 21 日報酬
    core_dates_sorted = sorted(d for d in core_prices if start_date <= d <= end_date)
    core_ma200 = {}
    if enable_regime_dca and len(core_dates_sorted) > 200:
        for i, d in enumerate(core_dates_sorted):
            if i >= 200:
                window = [core_prices[core_dates_sorted[j]] for j in range(i-200, i)]
                core_ma200[d] = np.mean(window)

    last_dca_date = start_date - timedelta(days=365)

    # Event-driven simulation
    event_dates = sorted(set(t.entry_date for t in trades) | set(t.exit_date for t in trades))
    if enable_regime_dca:
        event_dates = sorted(set(event_dates) | set(core_dates_sorted[::5]))   # 抽樣每 5 日檢查一次

    for d in event_dates:
        cur_core = nearest_price(core_prices, d)
        if cur_core is None:
            continue

        # 1. 出場
        still_open = []
        for pos in open_positions:
            if pos["exit_date"] <= d:
                exit_amount = pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST_PCT)
                shares_core += exit_amount / cur_core
                closed.append(pos)
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2. 進場
        for t in trades:
            if t.entry_date != d:
                continue
            allocation = INITIAL * PER_TRADE_PCT
            current_core_value = shares_core * cur_core
            if current_core_value >= allocation:
                shares_core -= allocation / cur_core
                open_positions.append({
                    "ticker": t.ticker, "entry_date": d,
                    "entry_amount": allocation * (1 - COST_PCT),
                    "exit_date": t.exit_date, "return_pct": t.return_pct,
                })
            else:
                skipped += 1

        # 3. Regime DCA 檢查
        if enable_regime_dca and cash_buffer > 0 and d in core_ma200:
            ma = core_ma200[d]
            # 21 日前的收盤
            prev_dates = [x for x in core_dates_sorted if x <= d - timedelta(days=21)]
            if prev_dates:
                prev_close = core_prices[prev_dates[-1]]
                month_change = (cur_core / prev_close - 1) * 100
                if (cur_core < ma and month_change < -10
                        and (d - last_dca_date).days > 30):
                    dca_amount = cash_buffer * 0.5
                    cash_buffer -= dca_amount
                    shares_core += dca_amount / cur_core
                    last_dca_date = d
                    dca_events.append({"date": d, "amount": dca_amount,
                                        "core_price": cur_core, "month_change": month_change})

    # 結算
    end_price = nearest_price(core_prices, end_date)
    if end_price is None:
        end_price = init_price
    portfolio_value = shares_core * end_price + cash_buffer
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
        "dca_events": dca_events,
    }


def main() -> None:
    # ─────────── 載入 trade lists ───────────
    print("[1/4] 載入 trade lists ...")
    vol_trades = load_vol_anomaly_trades()
    eh_trades = load_early_hunter_trades()
    eh_trailing_trades = load_early_hunter_trailing_trades()
    print(f"  Vol Anomaly: {len(vol_trades)} 筆")
    print(f"  Early Hunter (12M): {len(eh_trades)} 筆")
    print(f"  Early Hunter (Trailing): {len(eh_trailing_trades)} 筆")

    # ─────────── ETF Buy & Hold 比較 ───────────
    print(f"\n[2/4] ETF 純持有 CAGR 對比")
    etf_data = {tk: load_etf_prices(tk) for tk in CORES}
    etf_data = {k: v for k, v in etf_data.items() if v is not None}

    # 對齊期間 = max(ETF 起始) ~ max(trade exit)
    all_starts = [min(p.keys()) for p in etf_data.values()]
    aligned_start = max(all_starts)
    all_trade_exits = [t.exit_date for t in vol_trades + eh_trades]
    aligned_end = max(all_trade_exits)

    print(f"  對齊期間：{aligned_start} ~ {aligned_end}")
    print(f"  {'ETF':<10} {'標的':<18} {'Total':<10} {'CAGR':<10} {'Max DD':<10}")
    print(f"  {'-'*58}")
    etf_cagrs = {}
    for tk, prices in etf_data.items():
        total, cagr, dd = compute_etf_buy_hold(prices, aligned_start, aligned_end)
        etf_cagrs[tk] = cagr
        label = ETF_LABELS.get(tk, "")
        print(f"  {tk:<10} {label:<18} {total:>+7.1f}%  {cagr:>+7.2f}%  {dd:>+7.1f}%")

    # ─────────── V2 Portfolio: trade × core 矩陣 ───────────
    print(f"\n[3/4] V2 Portfolio：每個策略 × 每個核心 ETF（對齊期間）")
    print(f"  {'Strategy':<22} {'Core':<10} {'標的':<18} {'CAGR':<10} {'Alpha vs B&H':<12}")
    print(f"  {'-'*80}")

    strategies = [
        ("Vol Anomaly", vol_trades),
        ("EH (12M/200MA)", eh_trades),
        ("EH (Trailing-25pp)", eh_trailing_trades),
    ]
    for strategy_name, trades in strategies:
        if not trades:
            continue
        for tk in CORES:
            if tk not in etf_data:
                continue
            etf_start = min(etf_data[tk].keys())
            sim_start = max(etf_start, aligned_start)
            result = run_v2_portfolio(trades, etf_data[tk], sim_start, aligned_end)
            if "error" in result:
                continue
            bh_total, bh_cagr, _ = compute_etf_buy_hold(etf_data[tk], sim_start, aligned_end)
            alpha = result["cagr"] - bh_cagr
            label = ETF_LABELS.get(tk, "")
            print(f"  {strategy_name:<22} {tk:<10} {label:<18} "
                  f"{result['cagr']:>+7.2f}%  "
                  f"{alpha:>+7.2f}pp ({result['n_trades']} 筆)")

    # ─────────── Regime DCA 加值 ───────────
    print(f"\n[4/4] Regime DCA 加值（cash buffer 37% + 熊市加碼 0050）")
    print(f"  {'Strategy':<22} {'Buffer':<10} {'DCA':<6} {'CAGR':<10} {'DCA n':<8}")
    print(f"  {'-'*70}")

    core_0050 = etf_data["0050"]
    for strategy_name, trades in strategies:
        if not trades:
            continue
        for buffer_pct, dca_on in [(0.0, False), (0.37, False), (0.37, True)]:
            sim_start = max(min(core_0050.keys()), aligned_start)
            result = run_v2_portfolio(
                trades, core_0050, sim_start, aligned_end,
                cash_buffer_pct=buffer_pct,
                enable_regime_dca=dca_on,
            )
            label = f"buffer={buffer_pct*100:.0f}% dca={'on' if dca_on else 'off'}"
            print(f"  {strategy_name:<22} {label:<25} "
                  f"{result['cagr']:>+7.2f}%  "
                  f"{len(result.get('dca_events', [])):<6}")


if __name__ == "__main__":
    main()
