"""
Early-Stage Hunter 回測（Phase 18）— 抓主升段早期，持 12 個月。

跟 quality_momentum_backtest 的關鍵差異：
  1. 持有期 12 個月（不是 1 個月）→ 大幅降低換手成本
  2. 出場條件：(a) 持滿 12 個月或 (b) 跌破 200MA
  3. 不停損小回撤 — 要承受 −30% 才能吃 +300%
  4. 部位：每月最多新增 3 檔（避免過度集中），同時最多持 10 檔

預期結果（基於 2018-2024 364 檔 5x+ 樣本）：
  - 命中率 15-25%
  - 命中時 +150% ~ +400%
  - 失敗時 −20% ~ −40%
  - 期望值正、波動極大、Sharpe 不一定贏 0050

用法：
  python scripts/early_hunter_backtest.py --start 2019-01-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
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

from src.strategy.early_hunter import scan_ticker

UNIVERSE_PATH = ROOT / "config" / "universe_all.yaml"
CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_FM = ROOT / "data" / "cache" / "finmind" / "finmind"
LOGS_DIR = ROOT / "logs"
BENCHMARK = "0050"

# 個股成本（含 user 三折手續費 + 七成月退）
EFFECTIVE_FEE = 0.001425 * 0.3 * 0.3   # ≈ 0.0128%
TAX = 0.003
SLIPPAGE = 0.001


@dataclass
class HunterTrade:
    ticker: str
    entry_date: date
    entry_price: float
    entry_score: float
    exit_date: date
    exit_price: float
    exit_reason: str       # "holding_period_end" | "broke_200ma" | "end_of_backtest"
    gross_return_pct: float
    hold_days: int


def load_universe() -> list[str]:
    raw = yaml.safe_load(UNIVERSE_PATH.read_text(encoding="utf-8"))
    return sorted(raw.get("tickers", []))


def _read_parquet_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def load_ohlcv(ticker: str) -> pd.DataFrame:
    df = _read_parquet_safe(CACHE_YF / f"{ticker}.parquet")
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def load_revenue(ticker: str) -> pd.DataFrame:
    """從 FinMind cache 讀月營收 raw → 補 revenue_yoy 欄位。"""
    df = _read_parquet_safe(CACHE_FM / f"TaiwanStockMonthRevenue_{ticker}.parquet")
    if df.empty:
        return df
    df = df.sort_values("date").reset_index(drop=True).copy()
    if "revenue" in df.columns and "revenue_yoy" not in df.columns:
        df["revenue_yoy"] = (
            df["revenue"].astype(float) / df["revenue"].astype(float).shift(12) - 1
        ) * 100.0
    return df


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


def below_200ma(ohlcv: pd.DataFrame, d: date) -> bool:
    df = ohlcv[ohlcv["date"] <= d]
    if len(df) < 200:
        return False
    cur = float(df.iloc[-1]["close"])
    ma = float(df["close"].tail(200).mean())
    return cur < ma


def price_at(ohlcv: pd.DataFrame, d: date) -> float | None:
    df = ohlcv[ohlcv["date"] <= d]
    if df.empty:
        return None
    return float(df.iloc[-1]["close"])


def run_backtest(
    start: date,
    end: date,
    threshold: float,
    max_concurrent: int,
    hold_days: int,
    initial_equity: float,
    universe_limit: int | None = None,
) -> tuple[list[HunterTrade], dict]:
    universe = load_universe()
    if universe_limit:
        universe = universe[:universe_limit]
    print(f"[1/3] 載入 {len(universe)} 檔資料")
    bundles: dict[str, dict] = {}
    skipped = 0
    for i, tk in enumerate(universe, 1):
        if i % 200 == 0 or i == len(universe):
            print(f"    [{i}/{len(universe)}]")
        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            skipped += 1
            continue
        bundles[tk] = {"ohlcv": ohlcv, "revenue": load_revenue(tk)}
    print(f"    跳過 {skipped} 檔無 OHLCV，剩 {len(bundles)} 檔")

    benchmark_df = load_ohlcv(BENCHMARK)
    rebalances = month_starts(start, end)
    print(f"[2/3] 回測：{len(rebalances)} 個月、threshold={threshold}, hold={hold_days}d")

    cash = initial_equity
    open_positions: list[dict] = []      # {ticker, entry_date, entry_price, shares, score}
    closed: list[HunterTrade] = []
    half_cost = (EFFECTIVE_FEE * 2 + TAX + SLIPPAGE * 2) / 2
    t0 = time.time()

    for i, d in enumerate(rebalances, 1):
        # 1. 處理出場
        still_open = []
        for pos in open_positions:
            ohlcv = bundles[pos["ticker"]]["ohlcv"]
            cur = price_at(ohlcv, d)
            if cur is None:
                still_open.append(pos)
                continue

            held = (d - pos["entry_date"]).days
            exit_reason = None

            # 出場條件
            if held >= hold_days:
                exit_reason = "holding_period_end"
            elif held >= 30 and below_200ma(ohlcv, d):
                exit_reason = "broke_200ma"

            if exit_reason:
                # 平倉
                gross_proceeds = pos["shares"] * cur
                sell_cost = gross_proceeds * half_cost
                cash += gross_proceeds - sell_cost
                gross_pct = (cur / pos["entry_price"] - 1.0) * 100.0
                closed.append(HunterTrade(
                    ticker=pos["ticker"], entry_date=pos["entry_date"],
                    entry_price=pos["entry_price"], entry_score=pos["score"],
                    exit_date=d, exit_price=cur, exit_reason=exit_reason,
                    gross_return_pct=round(gross_pct, 2), hold_days=held,
                ))
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2. 掃描 → 進場（同時最多 max_concurrent）
        if len(open_positions) >= max_concurrent:
            continue

        candidates = []
        owned = {p["ticker"] for p in open_positions}
        for tk, b in bundles.items():
            if tk in owned:
                continue
            sig = scan_ticker(
                ticker=tk, ohlcv=b["ohlcv"], revenue=b["revenue"],
                as_of=d, market_cap_btw=None, threshold=threshold,
            )
            if sig and sig.triggered:
                candidates.append((tk, sig.score))
        candidates.sort(key=lambda x: -x[1])

        slots = max_concurrent - len(open_positions)
        for tk, score in candidates[:slots]:
            ohlcv = bundles[tk]["ohlcv"]
            entry_px = price_at(ohlcv, d)
            if entry_px is None or entry_px <= 0:
                continue
            # 等權分配（剩餘現金 / 剩餘 slots）
            alloc = cash / max(slots - candidates[:slots].index((tk, score)), 1)
            net_spend = alloc / (1.0 + half_cost)
            shares = int(net_spend / entry_px)
            if shares <= 0:
                continue
            spend = shares * entry_px
            buy_cost = spend * half_cost
            cash -= (spend + buy_cost)
            open_positions.append({
                "ticker": tk, "entry_date": d, "entry_price": entry_px,
                "shares": shares, "score": score,
            })

        if i % 12 == 0 or i == len(rebalances):
            equity = cash + sum(
                p["shares"] * (price_at(bundles[p["ticker"]]["ohlcv"], d) or p["entry_price"])
                for p in open_positions
            )
            print(f"    [{i}/{len(rebalances)}] {d} open={len(open_positions)} "
                  f"closed={len(closed)} equity={equity:,.0f} ({(time.time()-t0)/60:.1f}m)")

    # 3. 結算
    print("[3/3] 結算")
    final_value = sum(
        p["shares"] * (price_at(bundles[p["ticker"]]["ohlcv"], end) or p["entry_price"])
        for p in open_positions
    )
    final_equity = cash + final_value

    # Benchmark
    bm_start = price_at(benchmark_df, start)
    bm_end = price_at(benchmark_df, end)
    bm_ret = (bm_end / bm_start - 1.0) if (bm_start and bm_end) else 0.0

    metrics = compute_metrics(closed, initial_equity, final_equity, start, end, bm_ret)
    return closed, metrics


def compute_metrics(
    trades: list[HunterTrade],
    init_eq: float,
    final_eq: float,
    start: date, end: date,
    bm_ret: float,
) -> dict:
    if not trades:
        return {"trades": 0, "final_equity": final_eq}
    wins = [t for t in trades if t.gross_return_pct > 0]
    losses = [t for t in trades if t.gross_return_pct <= 0]
    win_rate = len(wins) / len(trades) if trades else 0
    avg_win = sum(t.gross_return_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(abs(t.gross_return_pct) for t in losses) / len(losses) if losses else 0
    pl = avg_win / avg_loss if avg_loss > 0 else float("inf")
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    big_wins = [t for t in wins if t.gross_return_pct > 100]   # +100% 以上
    huge_wins = [t for t in wins if t.gross_return_pct > 300]   # +300% 以上

    years = max((end - start).days / 365.25, 0.01)
    cagr = ((final_eq / init_eq) ** (1 / years) - 1) * 100

    return {
        "trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "pl_ratio": round(pl, 2),
        "expectancy_pct": round(expectancy, 2),
        "big_wins_100pct": len(big_wins),
        "huge_wins_300pct": len(huge_wins),
        "best_trade_pct": round(max(t.gross_return_pct for t in trades), 2),
        "worst_trade_pct": round(min(t.gross_return_pct for t in trades), 2),
        "final_equity": round(final_eq, 2),
        "total_return_pct": round((final_eq / init_eq - 1.0) * 100, 2),
        "cagr_pct": round(cagr, 2),
        "benchmark_0050_return_pct": round(bm_ret * 100, 2),
        "alpha_vs_0050_pct": round((final_eq / init_eq - 1.0 - bm_ret) * 100, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=str, required=True)
    ap.add_argument("--end", type=str, required=True)
    ap.add_argument("--initial-equity", type=float, default=1_000_000.0)
    ap.add_argument("--threshold", type=float, default=60.0)
    ap.add_argument("--max-concurrent", type=int, default=10)
    ap.add_argument("--hold-days", type=int, default=365)
    ap.add_argument("--universe-limit", type=int, default=None)
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    LOGS_DIR.mkdir(exist_ok=True)

    trades, metrics = run_backtest(
        start, end, args.threshold, args.max_concurrent,
        args.hold_days, args.initial_equity, args.universe_limit,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md = LOGS_DIR / f"early_hunter_{ts}.md"
    csv = LOGS_DIR / f"early_hunter_{ts}.csv"

    lines = [
        f"# Early Hunter 回測 — {start} ~ {end}",
        f"產出時間：{datetime.now().isoformat(timespec='seconds')}",
        f"參數：threshold={args.threshold}, max_concurrent={args.max_concurrent}, hold={args.hold_days}d",
        "",
        "## 績效",
    ]
    for k, v in metrics.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    if trades:
        lines.append("## Top 20 大贏家")
        lines.append("| 進場 | 出場 | 代號 | 進價 | 出價 | 報酬 | 天數 | 原因 | Score |")
        lines.append("|------|------|------|------|------|------|------|------|-------|")
        big = sorted(trades, key=lambda t: -t.gross_return_pct)[:20]
        for t in big:
            lines.append(
                f"| {t.entry_date} | {t.exit_date} | {t.ticker} | "
                f"{t.entry_price:.2f} | {t.exit_price:.2f} | "
                f"{t.gross_return_pct:+.1f}% | {t.hold_days} | {t.exit_reason} | {t.entry_score:.1f} |"
            )
    md.write_text("\n".join(lines), encoding="utf-8")
    pd.DataFrame([t.__dict__ for t in trades]).to_csv(csv, index=False, encoding="utf-8-sig")

    print("\n=== Early Hunter Backtest ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"\n報告：{md}")


if __name__ == "__main__":
    main()
