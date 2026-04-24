"""
ETF 動能輪動回測（Phase 15）— 月初 rebalance，持有 top N 強勢 ETF。

用法：
    python scripts/etf_momentum_backtest.py --start 2020-01-01 --end 2026-04-24
    python scripts/etf_momentum_backtest.py --start 2020-01-01 --end 2026-04-24 --top-n 2 --lookback 6

基準比較：
    同時計算買入持有 0050 的報酬作為 benchmark，直接看策略有沒有超額 alpha。

輸出：
    logs/etf_momentum_{ts}.md  — 彙總 + 月度持倉歷史
    logs/etf_momentum_{ts}.csv — 每月 rebalance 明細
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

from src.data.adr_fetcher import get_tw_ohlcv_adjusted
from src.strategy.etf_momentum import (
    ETFConfig,
    ETFRanking,
    load_config,
    portfolio_weights,
    rank_etfs,
)

CONFIG_PATH = ROOT / "config" / "etf_universe.yaml"
CACHE_DIR = ROOT / "data" / "cache" / "yfinance"
LOGS_DIR = ROOT / "logs"
BENCHMARK_TICKER = "0050"

# 台股成本假設
BUY_FEE = 0.001425
SELL_FEE = 0.001425
ETF_TAX = 0.001      # ETF 賣出稅率 0.1%（比個股 0.3% 低很多！）
SLIPPAGE = 0.001


@dataclass
class MonthlyRebalance:
    as_of: date
    ranking: ETFRanking
    weights: dict[str, float]
    equity_before: float
    equity_after: float
    trade_cost: float


def roundtrip_cost() -> float:
    """ETF 單次換手的往返成本比率。"""
    return BUY_FEE + SELL_FEE + ETF_TAX + SLIPPAGE * 2


def fetch_all(tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] 抓 {t} OHLCV...", flush=True)
        try:
            df = get_tw_ohlcv_adjusted(t, start, end, cache_dir=CACHE_DIR)
        except Exception as e:
            print(f"    失敗: {e}", flush=True)
            df = pd.DataFrame()
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df.sort_values("date").reset_index(drop=True)
        out[t] = df
    return out


def get_price(ohlcv: pd.DataFrame, d: date) -> float | None:
    """取 d 日或最接近 d 的前一個交易日的收盤價。"""
    if ohlcv is None or ohlcv.empty:
        return None
    df = ohlcv[ohlcv["date"] <= d]
    if df.empty:
        return None
    return float(df.iloc[-1]["close"])


def month_starts(start: date, end: date) -> list[date]:
    """產生每個月的第一天（rebalance 日）。"""
    out = []
    cur = date(start.year, start.month, 1)
    if cur < start:
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    while cur <= end:
        out.append(cur)
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def run_backtest(
    start: date,
    end: date,
    config: ETFConfig,
    initial_equity: float,
) -> tuple[list[MonthlyRebalance], pd.DataFrame, dict]:
    """
    Cleaner state machine: 明確分開 cash + holdings (ticker -> shares)。
    每月 mark-to-market → 排名 → 若異動則全平倉再買新組合。
    不偷吃未實現獲利（先前版本的 bug）。
    """
    fetch_start = start - timedelta(days=int(config.lookback_months * 35))
    tickers = list(set(config.etfs + [BENCHMARK_TICKER]))
    ohlcv_map = fetch_all(tickers, fetch_start, end)

    rebalance_dates = month_starts(start, end)
    print(f"  月份數：{len(rebalance_dates)}", flush=True)

    cash = initial_equity
    holdings: dict[str, int] = {}        # ticker -> shares only (乾淨)
    history: list[MonthlyRebalance] = []
    half_cost = roundtrip_cost() / 2     # 單邊成本（買或賣）

    for d in rebalance_dates:
        # 1. Mark-to-market: 拿當前市價算總權益
        holdings_value = 0.0
        for t, sh in holdings.items():
            p = get_price(ohlcv_map.get(t, pd.DataFrame()), d)
            if p is not None:
                holdings_value += sh * p
        equity_before = cash + holdings_value

        # 2. 排名 + 決定新組合
        ranking = rank_etfs(ohlcv_map, d, config)
        new_weights = portfolio_weights(ranking, config)
        new_tickers = set(new_weights.keys())
        old_tickers = set(holdings.keys())

        trade_cost_this_round = 0.0

        # 3. 若持倉組合不同 → 全平倉 + 重建
        if new_tickers != old_tickers or (ranking.defensive and holdings):
            # 賣出所有舊持倉，扣賣出成本
            sell_proceeds = 0.0
            for t, sh in holdings.items():
                p = get_price(ohlcv_map.get(t, pd.DataFrame()), d)
                if p is not None:
                    gross = sh * p
                    sell_cost = gross * half_cost
                    sell_proceeds += gross - sell_cost
                    trade_cost_this_round += sell_cost
            cash += sell_proceeds
            holdings = {}

            # 買進新組合
            if not ranking.defensive and new_weights:
                for t, w in new_weights.items():
                    alloc_pre_cost = cash * w     # 先用 cash 分配
                    p = get_price(ohlcv_map.get(t, pd.DataFrame()), d)
                    if p is None or p <= 0:
                        continue
                    # 實買金額 = alloc / (1 + half_cost) 讓「買入金額 + 買入成本 = alloc」
                    net_spend = alloc_pre_cost / (1.0 + half_cost)
                    sh = int(net_spend / p)
                    if sh > 0:
                        spend = sh * p
                        buy_cost = spend * half_cost
                        holdings[t] = sh
                        cash -= (spend + buy_cost)
                        trade_cost_this_round += buy_cost

        # 4. 本月結算：equity_after = cash + 新 holdings 市值
        holdings_value_after = 0.0
        for t, sh in holdings.items():
            p = get_price(ohlcv_map.get(t, pd.DataFrame()), d)
            if p is not None:
                holdings_value_after += sh * p
        equity_after = cash + holdings_value_after

        history.append(
            MonthlyRebalance(
                as_of=d,
                ranking=ranking,
                weights=new_weights,
                equity_before=round(equity_before, 2),
                equity_after=round(equity_after, 2),
                trade_cost=round(trade_cost_this_round, 2),
            )
        )

    # 5. 結算：最後一日 mark-to-market
    final_value = 0.0
    for t, sh in holdings.items():
        p = get_price(ohlcv_map.get(t, pd.DataFrame()), end)
        if p is not None:
            final_value += sh * p
    final_equity = cash + final_value

    # 6. 建權益曲線（用每個月 equity_after 近似）
    eq_df = pd.DataFrame(
        {"date": [h.as_of for h in history], "equity": [h.equity_after for h in history]}
    )

    # 7. benchmark: 買入持有 0050
    bm_df = ohlcv_map.get(BENCHMARK_TICKER, pd.DataFrame())
    bm_start_price = get_price(bm_df, start)
    bm_end_price = get_price(bm_df, end)
    bm_return = (bm_end_price / bm_start_price - 1.0) if (bm_start_price and bm_end_price) else 0.0

    metrics = {
        "initial_equity": initial_equity,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity / initial_equity - 1.0) * 100.0, 2),
        "benchmark_0050_return_pct": round(bm_return * 100.0, 2),
        "alpha_vs_0050_pct": round((final_equity / initial_equity - 1.0 - bm_return) * 100.0, 2),
        "n_rebalances": len(history),
        "n_defensive_months": sum(1 for h in history if h.ranking.defensive),
        "total_trade_cost": round(sum(h.trade_cost for h in history), 2),
    }

    # 8. 年化 + MaxDD + Sharpe（用 equity 曲線）
    if len(eq_df) > 1:
        eq_df["equity"] = eq_df["equity"].astype(float)
        years = (end - start).days / 365.25
        metrics["cagr_pct"] = round(
            ((final_equity / initial_equity) ** (1 / max(years, 0.01)) - 1.0) * 100.0, 2
        )
        running_max = eq_df["equity"].cummax()
        dd = (eq_df["equity"] - running_max) / running_max
        metrics["max_drawdown_pct"] = round(float(dd.min()) * 100.0, 2)
        pct_change = eq_df["equity"].pct_change().dropna()
        if pct_change.std() > 0:
            import math
            # 月報酬 → 年化波動 × sqrt(12)
            metrics["sharpe"] = round(
                float(pct_change.mean() / pct_change.std() * math.sqrt(12)), 2
            )
        else:
            metrics["sharpe"] = 0.0

    return history, eq_df, metrics


def write_report(
    history: list[MonthlyRebalance],
    metrics: dict,
    start: date,
    end: date,
    config: ETFConfig,
    out_md: Path,
) -> None:
    lines = [
        f"# ETF 動能輪動回測 — {start} ~ {end}",
        f"產出時間：{datetime.now().isoformat(timespec='seconds')}",
        f"策略：每月選 top {config.top_n} / lookback {config.lookback_months}M",
        f"成本：往返 {roundtrip_cost() * 100:.2f}%（含手續費 + 0.1% ETF 交易稅 + 滑價）",
        "",
        "## 核心指標",
        f"- 期初 / 期末權益：{metrics['initial_equity']:,.0f} → **{metrics['final_equity']:,.0f}**",
        f"- 總報酬：**{metrics['total_return_pct']:+.2f}%**",
        f"- 年化（CAGR）：**{metrics.get('cagr_pct', 0):.2f}%**",
        f"- Sharpe：{metrics.get('sharpe', 0):.2f}",
        f"- 最大回撤：{metrics.get('max_drawdown_pct', 0):.2f}%",
        f"- Rebalance 次數：{metrics['n_rebalances']}（其中 {metrics['n_defensive_months']} 個月現金防禦）",
        f"- 總交易成本：{metrics['total_trade_cost']:,.0f}",
        "",
        "## vs 0050 買入持有（Benchmark）",
        f"- 0050 同期報酬：{metrics['benchmark_0050_return_pct']:+.2f}%",
        f"- 策略 alpha：**{metrics['alpha_vs_0050_pct']:+.2f}%**",
        f"- {'✅ 策略勝 0050' if metrics['alpha_vs_0050_pct'] > 0 else '❌ 輸給 0050'}",
        "",
        "## 月度持倉歷史（最近 12 個月）",
        "| 日期 | 選股 | 防禦 | 權益 |",
        "|------|------|------|------|",
    ]
    for h in history[-12:]:
        selected_str = ", ".join(f"{t}({w:.0%})" for t, w in h.weights.items()) or "現金"
        defensive_str = "🔴" if h.ranking.defensive else "🟢"
        lines.append(f"| {h.as_of} | {selected_str} | {defensive_str} | {h.equity_after:,.0f} |")

    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=str, required=True)
    ap.add_argument("--end", type=str, required=True)
    ap.add_argument("--initial-equity", type=float, default=100_000.0)
    ap.add_argument("--top-n", type=int, default=None, help="覆寫 config.strategy.top_n")
    ap.add_argument("--lookback", type=int, default=None, help="覆寫 config.strategy.lookback_months")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    LOGS_DIR.mkdir(exist_ok=True)

    config = load_config(CONFIG_PATH)
    if args.top_n is not None:
        config = ETFConfig(
            etfs=config.etfs, lookback_months=config.lookback_months,
            top_n=args.top_n, equal_weight=config.equal_weight,
            cash_when_all_negative=config.cash_when_all_negative,
        )
    if args.lookback is not None:
        config = ETFConfig(
            etfs=config.etfs, lookback_months=args.lookback,
            top_n=config.top_n, equal_weight=config.equal_weight,
            cash_when_all_negative=config.cash_when_all_negative,
        )

    print(f"[1/2] 資料抓取（{len(config.etfs)} 檔 ETF + 0050 benchmark）", flush=True)
    history, eq_df, metrics = run_backtest(start, end, config, args.initial_equity)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = LOGS_DIR / f"etf_momentum_{ts}.md"
    csv_path = LOGS_DIR / f"etf_momentum_{ts}.csv"
    write_report(history, metrics, start, end, config, md_path)
    pd.DataFrame([
        {
            "date": h.as_of,
            "selected": ", ".join(h.weights.keys()) or "cash",
            "defensive": h.ranking.defensive,
            "equity_before": h.equity_before,
            "equity_after": h.equity_after,
            "trade_cost": h.trade_cost,
        } for h in history
    ]).to_csv(csv_path, index=False, encoding="utf-8-sig")

    print("\n=== ETF 動能輪動回測 ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"\n報告：{md_path}")


if __name__ == "__main__":
    main()
