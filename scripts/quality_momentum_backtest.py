"""
Quality Momentum 全市場回測（Phase 16 主角）。

每月第一個交易日：
  1. 讀 config/universe_all.yaml 的 2494 檔 universe
  2. 對每檔算 5 因子（用 src/strategy/quality_momentum.py）
  3. 橫斷面 z-score 合成 → 選 top N
  4. Equal weight 重分配（扣交易成本）
  5. 下個月底 mark-to-market

用法：
    python scripts/quality_momentum_backtest.py --start 2021-01-01 --end 2026-04-25
    python scripts/quality_momentum_backtest.py --start 2021-01-01 --end 2026-04-25 --top-n 20
    python scripts/quality_momentum_backtest.py --universe-limit 500  # 市值前 500 做 MVP

前置：
  必須先跑 scripts/bulk_fetch_universe.py 把資料抓到 parquet cache。
  本腳本只讀快取，不打 API。

成本：
  個股買賣含手續費 2 × 0.1425% + 證交稅 0.3% + 滑價 0.2% = 0.785% 往返
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

from src.data.adr_fetcher import get_tw_ohlcv_adjusted
from src.data.finmind_client import FinMindClient
from src.strategy.quality_momentum import (
    FactorWeights,
    compute_ticker_factors,
    cross_sectional_score,
)

UNIVERSE_PATH = ROOT / "config" / "universe_all.yaml"
CACHE_YF = ROOT / "data" / "cache" / "yfinance"
LOGS_DIR = ROOT / "logs"
BENCHMARK_TICKER = "0050"

# 個股成本（比 ETF 貴）
BUY_FEE = 0.001425
SELL_FEE = 0.001425
STOCK_TAX = 0.003
SLIPPAGE = 0.001


@dataclass
class MonthlyRebalance:
    as_of: date
    selected: list[str]
    equity_before: float
    equity_after: float
    trade_cost: float
    factor_coverage: int     # 有幾檔算得出完整因子
    universe_size: int


def roundtrip_cost() -> float:
    return BUY_FEE + SELL_FEE + STOCK_TAX + SLIPPAGE * 2   # 往返約 0.785%


def load_universe(limit: int | None = None) -> list[str]:
    raw = yaml.safe_load(UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers = sorted(raw.get("tickers", []))
    if limit:
        tickers = tickers[:limit]
    return tickers


def load_ticker_bundle(
    ticker: str,
    finmind: FinMindClient,
    start: date,
    end: date,
) -> dict[str, pd.DataFrame]:
    """從快取讀某檔所有必要資料。API 失敗則回空 DataFrame。"""
    ohlcv = pd.DataFrame()
    try:
        ohlcv = get_tw_ohlcv_adjusted(ticker, start, end, cache_dir=CACHE_YF)
    except Exception:
        pass

    per_pbr = pd.DataFrame()
    try:
        per_pbr = finmind.get_per_pbr(ticker, start, end)
    except Exception:
        pass

    financials = pd.DataFrame()
    try:
        # 多抓 2 年 buffer 供 TTM ROE 計算
        fs_start = date(start.year - 2, start.month, 1)
        financials = finmind.get_financial_statements(ticker, fs_start, end)
    except Exception:
        pass

    revenue = pd.DataFrame()
    try:
        rev_start = date(start.year - 1, start.month, 1)
        revenue = finmind.get_monthly_revenue(ticker, rev_start, end)
    except Exception:
        pass

    return {"ohlcv": ohlcv, "per_pbr": per_pbr, "financials": financials, "revenue": revenue}


def month_starts(start: date, end: date) -> list[date]:
    out = []
    cur = date(start.year, start.month, 1)
    if cur < start:
        cur = date(cur.year + (1 if cur.month == 12 else 0),
                   1 if cur.month == 12 else cur.month + 1, 1)
    while cur <= end:
        out.append(cur)
        cur = date(cur.year + (1 if cur.month == 12 else 0),
                   1 if cur.month == 12 else cur.month + 1, 1)
    return out


def close_on(ohlcv: pd.DataFrame, d: date) -> float | None:
    """取 d 日（或最近交易日）收盤價。"""
    if ohlcv is None or ohlcv.empty:
        return None
    df = ohlcv.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= d]
    if df.empty:
        return None
    return float(df.iloc[-1]["close"])


def compute_cross_section(
    bundles: dict[str, dict[str, pd.DataFrame]],
    as_of: date,
    weights: FactorWeights,
) -> pd.DataFrame:
    """
    對所有 ticker 算因子 → 橫斷面 z-score。
    回傳 columns=['momentum','quality_roe','value_pe','low_vol','revenue_growth','score']
    index=ticker。
    """
    rows: dict[str, dict] = {}
    for ticker, data in bundles.items():
        factors = compute_ticker_factors(
            ticker=ticker,
            as_of=as_of,
            ohlcv=data["ohlcv"],
            per_pbr=data["per_pbr"],
            financials=data["financials"],
            revenue=data["revenue"],
        )
        rows[ticker] = factors

    df = pd.DataFrame.from_dict(rows, orient="index")
    if df.empty:
        return df.assign(score=0.0)

    return cross_sectional_score(df, weights=weights)


def run_backtest(
    start: date,
    end: date,
    top_n: int,
    initial_equity: float,
    universe: list[str],
    weights: FactorWeights,
    finmind: FinMindClient,
) -> tuple[list[MonthlyRebalance], dict]:

    rebalance_dates = month_starts(start, end)
    print(f"[1/3] 載入 {len(universe)} 檔資料（從快取）", flush=True)

    # 一次性讀入所有 ticker 資料（快取存在就很快）
    bundles: dict[str, dict[str, pd.DataFrame]] = {}
    for i, tk in enumerate(universe, 1):
        if i % 100 == 0 or i == len(universe):
            print(f"    [{i}/{len(universe)}]", flush=True)
        bundles[tk] = load_ticker_bundle(tk, finmind, start, end)

    # Benchmark
    bm_ohlcv = pd.DataFrame()
    try:
        bm_ohlcv = get_tw_ohlcv_adjusted(BENCHMARK_TICKER, start, end, cache_dir=CACHE_YF)
    except Exception:
        pass

    print(f"[2/3] 回測：{len(rebalance_dates)} 個月 × top {top_n}", flush=True)

    cash = initial_equity
    holdings: dict[str, int] = {}
    history: list[MonthlyRebalance] = []
    half_cost = roundtrip_cost() / 2
    t0 = time.time()

    for i, d in enumerate(rebalance_dates, 1):
        # 1. 當前 mark-to-market
        holdings_value = 0.0
        for t, sh in holdings.items():
            p = close_on(bundles[t]["ohlcv"], d)
            if p is not None:
                holdings_value += sh * p
        equity_before = cash + holdings_value

        # 2. 橫斷面評分 → top N
        scored = compute_cross_section(bundles, as_of=d, weights=weights)
        valid = scored.dropna(subset=["momentum", "quality_roe"])
        coverage = len(valid)

        if coverage >= top_n:
            top = valid.sort_values("score", ascending=False).head(top_n).index.tolist()
        else:
            top = []   # 資料不足，空手

        new_tickers = set(top)
        old_tickers = set(holdings.keys())
        trade_cost_this = 0.0

        if new_tickers != old_tickers:
            # 賣出所有舊持股
            for t, sh in holdings.items():
                p = close_on(bundles[t]["ohlcv"], d)
                if p is None:
                    continue
                gross = sh * p
                sell_cost = gross * half_cost
                cash += gross - sell_cost
                trade_cost_this += sell_cost
            holdings = {}

            # 買入新持股（等權）
            if top:
                w = 1.0 / len(top)
                for t in top:
                    alloc = cash * w
                    p = close_on(bundles[t]["ohlcv"], d)
                    if p is None or p <= 0:
                        continue
                    net_spend = alloc / (1.0 + half_cost)
                    sh = int(net_spend / p)
                    if sh > 0:
                        spend = sh * p
                        buy_cost = spend * half_cost
                        holdings[t] = sh
                        cash -= (spend + buy_cost)
                        trade_cost_this += buy_cost

        # 3. 本月結算
        holdings_value_after = 0.0
        for t, sh in holdings.items():
            p = close_on(bundles[t]["ohlcv"], d)
            if p is not None:
                holdings_value_after += sh * p
        equity_after = cash + holdings_value_after

        history.append(
            MonthlyRebalance(
                as_of=d, selected=top,
                equity_before=round(equity_before, 2),
                equity_after=round(equity_after, 2),
                trade_cost=round(trade_cost_this, 2),
                factor_coverage=coverage,
                universe_size=len(universe),
            )
        )

        if i % 12 == 0 or i == len(rebalance_dates):
            print(
                f"    [{i}/{len(rebalance_dates)}] {d} "
                f"coverage={coverage}/{len(universe)} "
                f"equity={equity_after:,.0f} "
                f"({(time.time() - t0) / 60:.1f} 分鐘)",
                flush=True,
            )

    print("[3/3] 結算", flush=True)
    final_value = 0.0
    for t, sh in holdings.items():
        p = close_on(bundles[t]["ohlcv"], end)
        if p is not None:
            final_value += sh * p
    final_equity = cash + final_value

    # Benchmark
    bm_start = close_on(bm_ohlcv, start)
    bm_end = close_on(bm_ohlcv, end)
    bm_return = (bm_end / bm_start - 1.0) if (bm_start and bm_end) else 0.0

    # 權益曲線 metrics
    eq = pd.DataFrame([(h.as_of, h.equity_after) for h in history],
                      columns=["date", "equity"])
    metrics = {
        "initial_equity": initial_equity,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity / initial_equity - 1.0) * 100.0, 2),
        "benchmark_0050_return_pct": round(bm_return * 100.0, 2),
        "alpha_vs_0050_pct": round(
            (final_equity / initial_equity - 1.0 - bm_return) * 100.0, 2
        ),
        "n_rebalances": len(history),
        "total_trade_cost": round(sum(h.trade_cost for h in history), 2),
        "avg_coverage": round(
            sum(h.factor_coverage for h in history) / max(1, len(history)), 1
        ),
    }

    if len(eq) > 1:
        years = (end - start).days / 365.25
        metrics["cagr_pct"] = round(
            ((final_equity / initial_equity) ** (1 / max(years, 0.01)) - 1.0) * 100.0, 2
        )
        running_max = eq["equity"].cummax()
        dd = (eq["equity"] - running_max) / running_max
        metrics["max_drawdown_pct"] = round(float(dd.min()) * 100.0, 2)
        pct_change = eq["equity"].pct_change().dropna()
        if len(pct_change) > 0 and pct_change.std() > 0:
            import math
            metrics["sharpe"] = round(
                float(pct_change.mean() / pct_change.std() * math.sqrt(12)), 2
            )

    return history, metrics


def write_report(
    history: list[MonthlyRebalance],
    metrics: dict,
    start: date,
    end: date,
    top_n: int,
    out_md: Path,
) -> None:
    lines = [
        f"# Quality Momentum 全市場回測 — {start} ~ {end}",
        f"產出時間：{datetime.now().isoformat(timespec='seconds')}",
        f"策略：每月 top {top_n} 等權 / 5 因子合成 z-score",
        f"成本：往返 {roundtrip_cost() * 100:.2f}%（手續費 + 0.3% 證交稅 + 滑價）",
        "",
        "## 核心指標",
        f"- 期初 / 期末：{metrics['initial_equity']:,.0f} → **{metrics['final_equity']:,.0f}**",
        f"- 總報酬：**{metrics['total_return_pct']:+.2f}%**",
        f"- 年化（CAGR）：**{metrics.get('cagr_pct', 0):.2f}%**",
        f"- Sharpe：{metrics.get('sharpe', 0):.2f}",
        f"- 最大回撤：{metrics.get('max_drawdown_pct', 0):.2f}%",
        f"- Rebalance：{metrics['n_rebalances']} 次",
        f"- 平均因子覆蓋率：{metrics['avg_coverage']}/{history[0].universe_size if history else 0} 檔",
        f"- 總交易成本：{metrics['total_trade_cost']:,.0f}",
        "",
        "## vs 0050 買入持有",
        f"- 0050 同期：{metrics['benchmark_0050_return_pct']:+.2f}%",
        f"- 策略 alpha：**{metrics['alpha_vs_0050_pct']:+.2f}%**",
        f"- {'✅ 策略勝 0050' if metrics['alpha_vs_0050_pct'] > 0 else '❌ 輸 0050'}",
        "",
        "## 最近 12 個月持倉",
        "| 日期 | 選股 (top N) | 權益 |",
        "|------|--------------|------|",
    ]
    for h in history[-12:]:
        sel = ", ".join(h.selected[:10]) + (f" ...({len(h.selected)})" if len(h.selected) > 10 else "")
        lines.append(f"| {h.as_of} | {sel or '空手'} | {h.equity_after:,.0f} |")

    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=str, required=True)
    ap.add_argument("--end", type=str, required=True)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--initial-equity", type=float, default=1_000_000.0)
    ap.add_argument("--universe-limit", type=int, default=None,
                    help="只用 universe 前 N 檔（MVP 用 500 快跑）")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    LOGS_DIR.mkdir(exist_ok=True)

    import os
    token = os.environ.get("FINMIND_TOKEN", "")
    finmind = FinMindClient(token=token)
    universe = load_universe(limit=args.universe_limit)

    weights = FactorWeights()    # 預設 30/25/20/15/10
    history, metrics = run_backtest(
        start=start, end=end, top_n=args.top_n,
        initial_equity=args.initial_equity,
        universe=universe, weights=weights, finmind=finmind,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = LOGS_DIR / f"quality_momentum_{ts}.md"
    csv_path = LOGS_DIR / f"quality_momentum_{ts}.csv"

    write_report(history, metrics, start, end, args.top_n, md_path)
    pd.DataFrame([
        {"date": h.as_of, "selected": ",".join(h.selected), "equity": h.equity_after,
         "trade_cost": h.trade_cost, "coverage": h.factor_coverage}
        for h in history
    ]).to_csv(csv_path, index=False, encoding="utf-8-sig")

    print("\n=== Quality Momentum Backtest ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"\n報告：{md_path}")


if __name__ == "__main__":
    main()
