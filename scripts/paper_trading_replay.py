"""
歷史回放 paper trading — 用過去真實資料跑 BacktestEngine，
一次性產出 ≥20 筆模擬交易，代替「傳統 paper trading 等 2–4 週」。

使用方式：
    python scripts/paper_trading_replay.py --start 2024-01-01 --end 2024-12-31
    python scripts/paper_trading_replay.py --start 2018-01-01 --end 2018-12-31   # 壓力年份
    python scripts/paper_trading_replay.py --survival                            # 跑三個壓力年
    python scripts/paper_trading_replay.py --walk-forward --start 2022-01-01 --end 2024-12-31

資料來源（首次執行會慢，之後走 parquet 快取）：
    - yfinance：台股還原股價 + TAIEX + TSM/NVDA/SOXX/VIX 夜盤
    - FinMind：法人買賣超 + 分點籌碼（需 FINMIND_TOKEN）

輸出：
    logs/replay_{timestamp}.md — 完整報告 + 交易明細 CSV
"""
from __future__ import annotations

import argparse
import os
import sys
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

from src.backtest.cost_model import CostConfig
from src.backtest.data_view import HistoricalDataView
from src.backtest.engine import BacktestEngine, BacktestReport
from src.backtest.survival_check import format_survival_report, run_survival_check
from src.backtest.walk_forward import run_walk_forward
from src.data.adr_fetcher import get_tw_ohlcv_adjusted
from src.data.finmind_client import FinMindClient
from src.strategy.scoring_pipeline import ScoringPipeline


WATCHLIST_PATH = ROOT / "config" / "watchlist.yaml"
STRATEGY_PATH = ROOT / "config" / "strategy.yaml"
SECTOR_PATH = ROOT / "config" / "sector_map.yaml"
DT_PATH = ROOT / "config" / "day_trader_brokers.yaml"
LOGS_DIR = ROOT / "logs"


# ─────────────────────────────────────────
# 資料載入
# ─────────────────────────────────────────
def load_watchlist() -> list[str]:
    raw = yaml.safe_load(WATCHLIST_PATH.read_text(encoding="utf-8"))
    tickers: list[str] = []
    for _, lst in raw.items():
        for t in lst:
            tickers.append(str(t))
    return sorted(set(tickers))


def load_ticker_meta(tickers: list[str]) -> dict[str, dict]:
    # 簡化：shares_outstanding 用粗估值，避免每檔都抓財報
    return {t: {"company_name": t, "shares_outstanding": 2_000_000_000} for t in tickers}


def fetch_ohlcv_bundle(tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] 抓 {t} 股價...", flush=True)
        df = get_tw_ohlcv_adjusted(t, start, end)
        out[t] = df
    return out


def fetch_taiex(start: date, end: date) -> pd.DataFrame:
    # 多抓 1 年緩衝，讓 TrendRegimeDetector 在回測首日就能算出 MA200
    print("  抓加權指數 (^TWII)（含 MA200 緩衝）...", flush=True)
    buffered_start = start - timedelta(days=365)
    return get_tw_ohlcv_adjusted("^TWII", buffered_start, end)


def fetch_overnight_series(start: date, end: date) -> dict[date, dict]:
    """
    一次抓整段 TSM / NVDA / SOXX / VIX，為每個 TW 交易日對應「前一美股交易日」的收盤。
    """
    import yfinance as yf  # type: ignore

    print("  抓 TSM / NVDA / SOXX / VIX 夜盤序列...", flush=True)
    syms = {"tsm": "TSM", "nvda": "NVDA", "sox": "SOXX", "vix": "^VIX"}
    closes: dict[str, pd.Series] = {}
    for k, sym in syms.items():
        raw = yf.download(
            sym,
            start=(start - timedelta(days=10)).isoformat(),
            end=(end + timedelta(days=5)).isoformat(),
            auto_adjust=True,
            progress=False,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        s = raw["Close"].dropna() if "Close" in raw.columns else pd.Series(dtype=float)
        s.index = pd.to_datetime(s.index).date
        closes[k] = s.squeeze() if hasattr(s, "squeeze") else s

    out: dict[date, dict] = {}
    biz_days = pd.date_range(start, end, freq="B")
    for ts in biz_days:
        d = ts.date()
        ref = d - timedelta(days=1)

        def pick(series: pd.Series, cur: date) -> tuple[float, float]:
            hits = [k for k in series.index if k <= cur]
            if not hits:
                return float("nan"), float("nan")
            last = max(hits)
            val = float(series.loc[last])
            prev_hits = [k for k in series.index if k < last]
            if not prev_hits:
                return val, float("nan")
            prev = float(series.loc[max(prev_hits)])
            return val, prev

        tsm_c, tsm_p = pick(closes["tsm"], ref)
        nvda_c, nvda_p = pick(closes["nvda"], ref)
        sox_c, sox_p = pick(closes["sox"], ref)
        vix_c, _ = pick(closes["vix"], ref)

        def chg(c: float, p: float) -> float:
            if p and p == p and p != 0:
                return (c - p) / p * 100
            return 0.0

        out[d] = {
            "as_of_date": d.isoformat(),
            "tsmc_adr_close": tsm_c,
            "tsmc_adr_change_pct": round(chg(tsm_c, tsm_p), 2),
            "nvda_close": nvda_c,
            "nvda_change_pct": round(chg(nvda_c, nvda_p), 2),
            "sox_close": sox_c,
            "sox_change_pct": round(chg(sox_c, sox_p), 2),
            "vix": round(vix_c, 2) if vix_c == vix_c else 15.0,
            "market_mode": "normal",
        }
    return out


def fetch_chip_bundles(
    finmind: FinMindClient | None,
    tickers: list[str],
    start: date,
    end: date,
) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
]:
    inst: dict[str, pd.DataFrame] = {}
    broker: dict[str, pd.DataFrame] = {}
    concentration: dict[str, pd.DataFrame] = {}
    margin: dict[str, pd.DataFrame] = {}
    if finmind is None:
        print("  [!] FINMIND_TOKEN 未設定，籌碼資料留空（僅靠技術 + 供應鏈）", flush=True)
        for t in tickers:
            inst[t] = pd.DataFrame()
            broker[t] = pd.DataFrame()
            concentration[t] = pd.DataFrame()
            margin[t] = pd.DataFrame()
        return inst, broker, concentration, margin
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] 抓 {t} 籌碼...", flush=True)
        try:
            inst[t] = finmind.get_institutional(t, start, end)
        except Exception as e:
            print(f"    institutional 失敗：{e}", flush=True)
            inst[t] = pd.DataFrame()
        try:
            broker[t] = finmind.get_broker_distribution(t, start, end)
        except Exception as e:
            print(f"    broker 失敗：{e}", flush=True)
            broker[t] = pd.DataFrame()
        try:
            concentration[t] = finmind.get_foreign_ownership(t, start, end)
        except Exception as e:
            print(f"    外資持股 失敗：{e}", flush=True)
            concentration[t] = pd.DataFrame()
        try:
            margin[t] = finmind.get_margin(t, start, end)
        except Exception as e:
            print(f"    融資融券 失敗：{e}", flush=True)
            margin[t] = pd.DataFrame()
    return inst, broker, concentration, margin


def build_view(
    tickers: list[str], start: date, end: date
) -> tuple[HistoricalDataView, list[date]]:
    ohlcv = fetch_ohlcv_bundle(tickers, start, end)
    taiex = fetch_taiex(start, end)
    overnight = fetch_overnight_series(start, end)
    token = os.environ.get("FINMIND_TOKEN", "")
    finmind = FinMindClient(token) if token else None
    inst, broker, concentration, margin = fetch_chip_bundles(finmind, tickers, start, end)

    view = HistoricalDataView(
        ohlcv_by_ticker=ohlcv,
        institutional_by_ticker=inst,
        broker_by_ticker=broker,
        concentration_by_ticker=concentration,
        margin_by_ticker=margin,
        taiex=taiex,
        overnight_by_date=overnight,
    )

    # trading_calendar = TAIEX 有報價的日子
    if taiex.empty:
        raise RuntimeError("加權指數資料為空，無法建立交易日曆")
    calendar = sorted(pd.to_datetime(taiex["date"]).dt.date.unique())
    calendar = [d for d in calendar if start <= d <= end]
    return view, calendar


# ─────────────────────────────────────────
# 報告
# ─────────────────────────────────────────
def write_report(
    mode: str, start: date, end: date, report: BacktestReport, out_path: Path
) -> None:
    m = report.metrics
    lines = [
        f"# 歷史回放報告 — {mode}",
        f"區間：{start} ~ {end}",
        f"產出時間：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 績效指標",
        f"- 交易筆數：{int(m.get('trades', 0))}",
        f"- 勝率：{m.get('win_rate', 0):.2%}",
        f"- 盈虧比：{m.get('pl_ratio', 0):.2f}",
        f"- 期望值：{m.get('expectancy_pct', 0):.2f}%",
        f"- 最大回撤：{m.get('max_drawdown_pct', 0):.2%}",
        f"- Sharpe：{m.get('sharpe', 0):.2f}",
        f"- 總報酬：{m.get('total_return_pct', 0):+.2f}%",
        f"- 期末權益：{m.get('final_equity', 0):,.0f}",
        "",
        "## 達標檢查（計畫 §8.2）",
        f"- 勝率 > 55%：{'✅' if m.get('win_rate', 0) > 0.55 else '❌'}",
        f"- 盈虧比 > 1.5：{'✅' if m.get('pl_ratio', 0) > 1.5 else '❌'}",
        f"- Expectancy > 0.5%：{'✅' if m.get('expectancy_pct', 0) > 0.5 else '❌'}",
        f"- MaxDD < 15%：{'✅' if m.get('max_drawdown_pct', 0) > -0.15 else '❌'}",
        f"- Sharpe > 1.0：{'✅' if m.get('sharpe', 0) > 1.0 else '❌'}",
        "",
    ]
    if report.trades:
        lines.append("## 最近 20 筆交易")
        lines.append("| 進場 | 出場 | 代號 | 股數 | 進價 | 出價 | 淨報酬 | 原因 |")
        lines.append("|------|------|------|------|------|------|--------|------|")
        for t in report.trades[-20:]:
            lines.append(
                f"| {t.entry_date} | {t.exit_date} | {t.ticker} | {t.shares} | "
                f"{t.entry_price:.2f} | {t.exit_price:.2f} | {t.net_return_pct:+.2f}% | {t.exit_reason} |"
            )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    csv_path = out_path.with_suffix(".csv")
    pd.DataFrame([t.__dict__ for t in report.trades]).to_csv(csv_path, index=False)
    print(f"\n報告：{out_path}", flush=True)
    print(f"交易明細：{csv_path}", flush=True)


# ─────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────
_MIN_SCORE_OVERRIDE: float | None = None


def pipeline_factory() -> ScoringPipeline:
    pipe = ScoringPipeline(STRATEGY_PATH, SECTOR_PATH, DT_PATH)
    if _MIN_SCORE_OVERRIDE is not None:
        pipe._composite._min_score = _MIN_SCORE_OVERRIDE  # noqa: SLF001 — 除錯用途
    return pipe


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=str, help="YYYY-MM-DD")
    ap.add_argument("--end", type=str, help="YYYY-MM-DD")
    ap.add_argument("--survival", action="store_true", help="跑 2018/2020/2022 壓力年")
    ap.add_argument("--walk-forward", action="store_true", help="走 Walk-Forward 盲測")
    ap.add_argument("--min-score", type=float, default=None,
                    help="覆寫 strategy.yaml 的 recommendation.min_score（除錯用）")
    ap.add_argument("--initial-equity", type=float, default=100_000.0)
    ap.add_argument("--test-months", type=int, default=1,
                    help="Walk-Forward 盲測窗大小（月），短線策略建議 3")
    ap.add_argument("--train-months", type=int, default=24,
                    help="Walk-Forward 訓練窗大小（月），12 更貼近台股族群輪動節奏")
    ap.add_argument("--fixed-preset", type=str, default=None,
                    help="固定使用指定 preset（如 default）跳過 grid search，排除權重搜尋過擬合")
    args = ap.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)

    if args.min_score is not None:
        global _MIN_SCORE_OVERRIDE
        _MIN_SCORE_OVERRIDE = float(args.min_score)
        print(f"[override] recommendation.min_score = {args.min_score}", flush=True)

    if args.survival:
        start, end = date(2018, 1, 1), date(2023, 1, 1)
        tickers = load_watchlist()
        print(f"[1/2] 準備資料 {start} ~ {end}，共 {len(tickers)} 檔...", flush=True)
        view, calendar = build_view(tickers, start, end)
        print(f"[2/2] 跑 survival check（{len(calendar)} 個交易日）...", flush=True)
        results = run_survival_check(
            view=view,
            pipeline_factory=pipeline_factory,
            cost=CostConfig(),
            trading_calendar=calendar,
            watchlist=tickers,
            ticker_meta=load_ticker_meta(tickers),
            initial_equity=args.initial_equity,
        )
        print(format_survival_report(results))
        out = LOGS_DIR / f"replay_survival_{datetime.now():%Y%m%d_%H%M%S}.md"
        out.write_text(format_survival_report(results), encoding="utf-8")
        print(f"\n報告：{out}", flush=True)
        return

    if not args.start or not args.end:
        ap.error("需提供 --start 與 --end，或使用 --survival")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    tickers = load_watchlist()

    print(f"[1/2] 準備資料 {start} ~ {end}，共 {len(tickers)} 檔...", flush=True)
    view, calendar = build_view(tickers, start, end)
    print(f"    交易日：{len(calendar)} 天", flush=True)

    if args.walk_forward:
        print("[2/2] Walk-Forward 盲測...", flush=True)
        rep = run_walk_forward(
            view=view,
            pipeline_factory=pipeline_factory,
            cost=CostConfig(),
            trading_calendar=calendar,
            watchlist=tickers,
            ticker_meta=load_ticker_meta(tickers),
            start=start,
            end=end,
            train_months=args.train_months,
            test_months=args.test_months,
            initial_equity=args.initial_equity,
            fixed_preset=args.fixed_preset,
        )
        print(rep.summary())
        out = LOGS_DIR / f"replay_wf_{datetime.now():%Y%m%d_%H%M%S}.md"
        lines = [
            f"# Walk-Forward 回放 — {start} ~ {end}",
            f"視窗數：{len(rep.windows)}",
            f"彙總：{rep.summary()}",
            "",
            "## 各視窗選用權重",
        ]
        for w in rep.windows:
            lines.append(
                f"- {w.test_start}~{w.test_end} → **{w.chosen_preset}** "
                f"(test expectancy {w.test_metrics.get('expectancy_pct', 0):.2f}%)"
            )
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n報告：{out}", flush=True)
        return

    print("[2/2] 單段回測...", flush=True)
    engine = BacktestEngine(
        pipeline=pipeline_factory(),
        view=view,
        cost=CostConfig(),
        initial_equity=args.initial_equity,
    )
    report = engine.run(calendar, tickers, load_ticker_meta(tickers))
    print(report.summary())
    out = LOGS_DIR / f"replay_{datetime.now():%Y%m%d_%H%M%S}.md"
    write_report("單段回測", start, end, report, out)


if __name__ == "__main__":
    main()
