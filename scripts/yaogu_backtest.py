"""
妖股雷達回測 — MVP 版，只吃 yfinance OHLCV，不跑 FinMind。

對 config/yaogu_watchlist.yaml 的每檔 × 每個交易日 D：
  1. 若 scan_ticker(D) 回 triggered=True → 模擬 D+1 開盤 × 1.01 進場
  2. 每日掃 bar：觸 stop (−7%)、target (+20%) 或 timeout (7 個交易日)
  3. 同時最多持 5 檔，進場要從「未持有」的候選中挑 score 最高

輸出：
  logs/yaogu_backtest_{timestamp}.md   — 彙總 + 前 20 筆交易
  logs/yaogu_backtest_{timestamp}.csv  — 所有模擬交易明細

用法：
  python scripts/yaogu_backtest.py --start 2023-01-01 --end 2026-04-24
  python scripts/yaogu_backtest.py --start 2023-01-01 --end 2026-04-24 --threshold 70
"""
from __future__ import annotations

import argparse
import sys
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

from src.data.adr_fetcher import get_tw_ohlcv_adjusted
from src.strategy.yaogu_radar import scan_ticker

WATCHLIST_PATH = ROOT / "config" / "yaogu_watchlist.yaml"
CACHE_DIR = ROOT / "data" / "cache" / "yfinance"
LOGS_DIR = ROOT / "logs"

ENTRY_SLIPPAGE = 1.01            # 買在隔日開盤 × 1.01
MAX_CONCURRENT = 5


@dataclass
class OpenTrade:
    ticker: str
    entry_date: date
    entry_price: float
    entry_score: float


@dataclass
class ClosedTrade:
    ticker: str
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    exit_reason: str
    gross_return_pct: float
    entry_score: float
    hold_days: int


def load_universe() -> list[str]:
    raw = yaml.safe_load(WATCHLIST_PATH.read_text(encoding="utf-8"))
    tickers: list[str] = []
    for group in raw.values():
        if isinstance(group, list):
            tickers.extend(str(t) for t in group)
    return sorted(set(tickers))


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


def trading_days_from(ohlcv_map: dict[str, pd.DataFrame], start: date, end: date) -> list[date]:
    """用最長的一檔 OHLCV 當交易日曆（排除週末假日）。"""
    biggest = max(ohlcv_map.values(), key=lambda df: len(df), default=pd.DataFrame())
    if biggest.empty:
        return []
    dates = sorted(d for d in biggest["date"].unique() if start <= d <= end)
    return dates


def bar_for(df: pd.DataFrame, d: date) -> dict | None:
    rows = df[df["date"] == d]
    if rows.empty:
        return None
    r = rows.iloc[0]
    return {
        "open": float(r["open"]),
        "high": float(r["high"]),
        "low": float(r["low"]),
        "close": float(r["close"]),
        "volume": float(r["volume"]),
    }


def hist_before(df: pd.DataFrame, d: date) -> pd.DataFrame:
    return df[df["date"] <= d]


def run_backtest(
    start: date,
    end: date,
    threshold: float,
    target_pct: float,
    stop_pct: float,
    timeout_days: int,
) -> tuple[list[ClosedTrade], list[OpenTrade], list[str]]:
    universe = load_universe()
    print(f"[1/2] 抓資料（{len(universe)} 檔，{start} ~ {end}）", flush=True)
    ohlcv_map = fetch_all(universe, start - timedelta(days=150), end)

    calendar = trading_days_from(ohlcv_map, start, end)
    print(f"[2/2] 回測（{len(calendar)} 個交易日）", flush=True)

    open_trades: dict[str, OpenTrade] = {}
    closed: list[ClosedTrade] = []
    skipped: list[str] = []   # (date, ticker, reason) 記錄被濾掉的候選

    for i, d in enumerate(calendar):
        if i % 50 == 0:
            print(f"    progress: {i}/{len(calendar)} ({d})", flush=True)

        # ── 1. 先處理出場（固定 target / stop / timeout）────────
        for ticker in list(open_trades.keys()):
            pos = open_trades[ticker]
            bar = bar_for(ohlcv_map[ticker], d)
            if bar is None:
                continue
            hold_days = (d - pos.entry_date).days
            stop_px = pos.entry_price * (1 + stop_pct / 100.0)
            target_px = pos.entry_price * (1 + target_pct / 100.0)
            hit_stop = bar["low"] <= stop_px
            hit_target = bar["high"] >= target_px

            exit_reason = None
            exit_px = None
            if hit_stop and hit_target:
                exit_reason, exit_px = "stop", stop_px       # 保守：同日同觸先停損
            elif hit_stop:
                exit_reason, exit_px = "stop", stop_px
            elif hit_target:
                exit_reason, exit_px = "target", target_px
            elif hold_days >= timeout_days:
                exit_reason, exit_px = "timeout", bar["close"]

            if exit_reason:
                gross = (exit_px / pos.entry_price - 1.0) * 100.0
                closed.append(
                    ClosedTrade(
                        ticker=ticker,
                        entry_date=pos.entry_date,
                        entry_price=round(pos.entry_price, 2),
                        exit_date=d,
                        exit_price=round(exit_px, 2),
                        exit_reason=exit_reason,
                        gross_return_pct=round(gross, 2),
                        entry_score=pos.entry_score,
                        hold_days=hold_days,
                    )
                )
                del open_trades[ticker]

        # ── 2. 掃雷達找候選 ────────────────────
        candidates: list[tuple[str, float]] = []
        for ticker in universe:
            if ticker in open_trades:
                continue
            hist = hist_before(ohlcv_map[ticker], d)
            sig = scan_ticker(hist, as_of=d, threshold=threshold)
            if sig and sig.triggered:
                candidates.append((ticker, sig.score))

        candidates.sort(key=lambda x: x[1], reverse=True)

        # ── 3. 模擬進場（D+1 開盤 × 1.01）────────
        idx = calendar.index(d)
        if idx + 1 >= len(calendar):
            continue
        next_d = calendar[idx + 1]

        for ticker, score in candidates:
            if len(open_trades) >= MAX_CONCURRENT:
                break
            next_bar = bar_for(ohlcv_map[ticker], next_d)
            if next_bar is None:
                continue
            entry_px = next_bar["open"] * ENTRY_SLIPPAGE
            # 確認 entry_px 仍在當日 [low, high] 內（避免跳空太猛進不去）
            if entry_px > next_bar["high"]:
                skipped.append(f"{d} {ticker} score={score:.1f} gap_up_miss")
                continue
            open_trades[ticker] = OpenTrade(
                ticker=ticker,
                entry_date=next_d,
                entry_price=entry_px,
                entry_score=score,
            )

    # ── 4. 結算尚未平倉 ───────────────────────────
    if calendar:
        last = calendar[-1]
        for ticker, pos in open_trades.items():
            bar = bar_for(ohlcv_map[ticker], last)
            if bar is None:
                continue
            exit_px = bar["close"]
            gross = (exit_px / pos.entry_price - 1.0) * 100.0
            closed.append(
                ClosedTrade(
                    ticker=ticker,
                    entry_date=pos.entry_date,
                    entry_price=round(pos.entry_price, 2),
                    exit_date=last,
                    exit_price=round(exit_px, 2),
                    exit_reason="end_of_backtest",
                    gross_return_pct=round(gross, 2),
                    entry_score=pos.entry_score,
                    hold_days=(last - pos.entry_date).days,
                )
            )

    return closed, list(open_trades.values()), skipped


VALID_EXIT_REASONS = {"stop", "target", "timeout", "end_of_backtest"}


def summarize(trades: list[ClosedTrade]) -> dict:
    if not trades:
        return {"n": 0}
    real_closed = [t for t in trades if t.exit_reason in VALID_EXIT_REASONS]
    wins = [t for t in real_closed if t.gross_return_pct > 0]
    losses = [t for t in real_closed if t.gross_return_pct <= 0]

    avg_win = sum(t.gross_return_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(abs(t.gross_return_pct) for t in losses) / len(losses) if losses else 0
    win_rate = len(wins) / len(real_closed) if real_closed else 0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    pl = avg_win / avg_loss if avg_loss > 0 else float("inf")

    # 最大連輸
    max_consec_loss = 0
    cur = 0
    for t in sorted(real_closed, key=lambda x: x.exit_date):
        if t.gross_return_pct <= 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0

    return {
        "n": len(real_closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "pl_ratio": round(pl, 2),
        "expectancy_pct": round(expectancy, 2),
        "max_consecutive_losses": max_consec_loss,
        "best_trade": max(t.gross_return_pct for t in real_closed) if real_closed else 0,
        "worst_trade": min(t.gross_return_pct for t in real_closed) if real_closed else 0,
    }


def write_report(
    trades: list[ClosedTrade],
    summary: dict,
    start: date,
    end: date,
    threshold: float,
    target_pct: float,
    stop_pct: float,
    timeout_days: int,
    out_md: Path,
) -> None:
    lines = [
        f"# 妖股雷達回測 — {start} ~ {end}",
        f"產出時間：{datetime.now().isoformat(timespec='seconds')}",
        f"進場門檻：score ≥ {threshold}",
        f"止損/目標/超時：{stop_pct:+.0f}% / {target_pct:+.0f}% / {timeout_days} 日",
        f"同時最多持倉：{MAX_CONCURRENT} 檔",
        "",
        "## 績效指標",
        f"- 總交易筆數：{summary.get('n', 0)}",
        f"- 勝 / 負：{summary.get('wins', 0)} / {summary.get('losses', 0)}",
        f"- 勝率：{summary.get('win_rate', 0):.2%}",
        f"- 盈虧比：{summary.get('pl_ratio', 0):.2f}",
        f"- 期望值：{summary.get('expectancy_pct', 0):+.2f}%",
        f"- 平均獲利：{summary.get('avg_win_pct', 0):+.2f}%",
        f"- 平均虧損：{summary.get('avg_loss_pct', 0):-.2f}%",
        f"- 最大連輸筆數：{summary.get('max_consecutive_losses', 0)}",
        f"- 最佳 / 最差單筆：{summary.get('best_trade', 0):+.2f}% / {summary.get('worst_trade', 0):+.2f}%",
        "",
        "## 達標檢查",
        f"- 勝率 > 30%：{'✅' if summary.get('win_rate', 0) > 0.30 else '❌'}",
        f"- 盈虧比 > 2.5：{'✅' if summary.get('pl_ratio', 0) > 2.5 else '❌'}",
        f"- 期望值 > +1%：{'✅' if summary.get('expectancy_pct', 0) > 1.0 else '❌'}",
        "",
    ]
    if trades:
        lines.append("## 最佳 10 筆交易")
        lines.append("| 進場 | 出場 | 代號 | 進價 | 出價 | 報酬 | 天數 | 原因 | Score |")
        lines.append("|------|------|------|------|------|------|------|------|-------|")
        best = sorted(trades, key=lambda t: t.gross_return_pct, reverse=True)[:10]
        for t in best:
            lines.append(
                f"| {t.entry_date} | {t.exit_date} | {t.ticker} | {t.entry_price} | "
                f"{t.exit_price} | {t.gross_return_pct:+.2f}% | {t.hold_days} | {t.exit_reason} | {t.entry_score:.1f} |"
            )
        lines.append("")
        lines.append("## 最差 10 筆交易")
        lines.append("| 進場 | 出場 | 代號 | 進價 | 出價 | 報酬 | 天數 | 原因 | Score |")
        lines.append("|------|------|------|------|------|------|------|------|-------|")
        worst = sorted(trades, key=lambda t: t.gross_return_pct)[:10]
        for t in worst:
            lines.append(
                f"| {t.entry_date} | {t.exit_date} | {t.ticker} | {t.entry_price} | "
                f"{t.exit_price} | {t.gross_return_pct:+.2f}% | {t.hold_days} | {t.exit_reason} | {t.entry_score:.1f} |"
            )

    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=str, required=True)
    ap.add_argument("--end", type=str, required=True)
    ap.add_argument("--threshold", type=float, default=60.0)
    ap.add_argument("--target-pct", type=float, default=20.0, help="目標價漲幅 (%)")
    ap.add_argument("--stop-pct", type=float, default=-7.0, help="止損跌幅 (%，負值)")
    ap.add_argument("--timeout-days", type=int, default=7)
    ap.add_argument("--tag", type=str, default="", help="輸出檔名標記")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    LOGS_DIR.mkdir(exist_ok=True)

    trades, still_open, _ = run_backtest(
        start, end, args.threshold, args.target_pct, args.stop_pct, args.timeout_days,
    )
    summary = summarize(trades)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    md_path = LOGS_DIR / f"yaogu_backtest{tag}_{ts}.md"
    csv_path = LOGS_DIR / f"yaogu_backtest{tag}_{ts}.csv"
    write_report(
        trades, summary, start, end, args.threshold,
        args.target_pct, args.stop_pct, args.timeout_days,
        md_path,
    )
    pd.DataFrame([t.__dict__ for t in trades]).to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"\n=== 彙總 (target={args.target_pct}%, stop={args.stop_pct}%, timeout={args.timeout_days}d) ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\n報告：{md_path}")


if __name__ == "__main__":
    main()
