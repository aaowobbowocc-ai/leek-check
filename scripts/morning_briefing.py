"""
晨報主入口 — 每個交易日 08:30 由 Windows Task Scheduler 呼叫。

流程：
  1. 讀取設定（strategy.yaml / watchlist / sector_map / day_trader）
  2. 載入資產快照（assets.json）
  3. 抓 ADR 夜盤（yfinance）
  4. 對觀察清單的每檔 ticker：
       a. 取 FinMind 法人籌碼（parquet 快取）
       b. 取 yfinance 還原股價（parquet 快取）
       c. 收集 Google News → sentiment_factor（Claude Haiku）
  5. 跑 ScoringPipeline
  6. 讀 ConceptDriftDetector 狀態
  7. render_morning_report → 寫 logs/YYYY-MM-DD.md + 印到終端機

環境變數（.env）：
  ANTHROPIC_API_KEY   — Claude API
  FINMIND_TOKEN       — FinMind API
  FUGLE_API_KEY       — （選用，有才啟用 Fugle 即時報價）
  USER_UUID           — 資產金額遮罩（醫院環境）

執行方式：
  python scripts/morning_briefing.py
  python scripts/morning_briefing.py --dry-run --date 2026-04-16
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# 確保 src/ 在 import 路徑
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "config" / ".env")

import yaml

from src.data.adr_fetcher import get_overnight_report, get_tw_ohlcv_adjusted
from src.data.finmind_client import FinMindClient
from src.data.fugle_client import FugleClient
from src.data.hybrid_client import HybridClient
from src.data.mops_client import MOPSClient
from src.data.news_collector import NewsCollector
from src.data.twse_client import TWSEClient
from src.portfolio.asset_manager import AssetManager
from src.portfolio.paper_tracker import record_daily as paper_record_daily
from src.report.allocation_advisor import (
    StockTracker,
    detect_regime,
    is_quarterly_rebalance_day,
    render_allocation_section,
)
from src.report.daily_report import render_morning_report, save_and_print
from src.risk.concept_drift import ConceptDriftDetector
from src.strategy.scoring_pipeline import (
    PipelineInput,
    ScoringPipeline,
    TickerInputs,
)
from src.strategy.sentiment_factor import SentimentAnalyzer, SentimentResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 設定路徑
# ─────────────────────────────────────────
CONFIG = ROOT / "config"
STRATEGY_YAML = CONFIG / "strategy.yaml"
WATCHLIST_YAML = CONFIG / "watchlist.yaml"
SECTOR_MAP_YAML = CONFIG / "sector_map.yaml"
DAY_TRADER_YAML = CONFIG / "day_trader_brokers.yaml"
NEWS_KEYWORDS_YAML = CONFIG / "news_keywords.yaml"
ASSETS_JSON = ROOT / "data" / "assets.json"
CACHE_DIR = ROOT / "data" / "cache"
DRIFT_LOG = ROOT / "data" / "state" / "drift_log.parquet"
PAPER_TRADES_DIR = ROOT / "data" / "paper_trades"


def _load_watchlist() -> dict[str, str]:
    """回傳 {ticker_str: company_name}"""
    with WATCHLIST_YAML.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    company_map = {
        "2330": "台積電", "2317": "鴻海", "2454": "聯發科",
        "3413": "京鼎", "3680": "家登", "3131": "弘塑", "8996": "高力", "6274": "台燿",
        "3037": "欣興", "3189": "景碩", "6449": "鈺邦",
        "3017": "奇鋐", "3042": "晶技",
        "2382": "廣達", "3231": "緯創", "2376": "技嘉",
    }
    tickers: dict[str, str] = {}
    for group in cfg.values():
        if isinstance(group, list):
            for t in group:
                ts = str(t)
                tickers[ts] = company_map.get(ts, ts)
    return tickers


def _shares_outstanding() -> dict[str, int]:
    """股本（粗估，實際應從 FinMind 財報取）— 先用固定值"""
    return {k: 1_000_000_000 for k in _load_watchlist()}


def main(as_of_date: date, dry_run: bool = False) -> None:
    logger.info("=== 台股晨報 %s %s===", as_of_date, "[DRY RUN] " if dry_run else "")

    # ── 資產快照 ──────────────────────────────
    fugle_key = os.environ.get("FUGLE_API_KEY") or None
    price_client = FugleClient(api_key=fugle_key)
    if not ASSETS_JSON.exists():
        logger.warning("assets.json 不存在，請從 data/assets.json.example 複製並填寫。使用空快照繼續。")
        am = None
    else:
        am = AssetManager(ASSETS_JSON, price_fetcher=price_client.as_price_fetcher())
    from src.portfolio.asset_manager import PortfolioSnapshot
    portfolio = am.snapshot() if am else PortfolioSnapshot(cash=0.0, long_term=(), short_term=())

    # ── ADR 夜盤 ──────────────────────────────
    logger.info("抓取 ADR 夜盤 ...")
    try:
        overnight = get_overnight_report(as_of_date)
    except Exception as e:
        logger.warning("ADR 抓取失敗，使用預設值: %s", e)
        from src.data.adr_fetcher import OvernightReport
        overnight = OvernightReport(
            as_of_date=as_of_date.isoformat(),
            tsmc_adr_close=float("nan"), tsmc_adr_change_pct=0.0,
            nvda_close=float("nan"), nvda_change_pct=0.0,
            sox_close=float("nan"), sox_change_pct=0.0,
            vix=15.0, market_mode="normal",
        )

    # ── 觀察清單 + 資料抓取 ───────────────────
    watchlist = _load_watchlist()
    shares = _shares_outstanding()
    finmind_token = os.environ.get("FINMIND_TOKEN", "")
    use_hybrid = os.environ.get("USE_HYBRID", "0") == "1"

    if use_hybrid:
        # Phase 17a：自抓器模式（取消 FinMind Sponsor 後的選擇）
        # PER / 法人 / 月營收 走 TWSE/MOPS；分點籌碼仍用 FinMind（無替代）
        finmind_backend = FinMindClient(token=finmind_token, cache_dir=CACHE_DIR / "finmind") if finmind_token else None
        finmind = HybridClient(
            twse=TWSEClient(),
            mops=MOPSClient(),
            finmind=finmind_backend,
        )
        logger.info("使用 HybridClient（自抓器模式：TWSE PER/法人 + MOPS 月營收）")
    else:
        finmind = FinMindClient(token=finmind_token, cache_dir=CACHE_DIR / "finmind")
    news_coll = NewsCollector(NEWS_KEYWORDS_YAML)

    # Claude 情緒分析
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    sentiment_analyzer: SentimentAnalyzer | None = None
    if anthropic_key:
        import anthropic
        sentiment_analyzer = SentimentAnalyzer(anthropic.Anthropic(api_key=anthropic_key))

    start_date = as_of_date - timedelta(days=365)
    ticker_inputs: list[TickerInputs] = []

    for ticker, company in watchlist.items():
        logger.info("  處理 %s %s ...", ticker, company)
        try:
            ohlcv = get_tw_ohlcv_adjusted(
                ticker, start_date, as_of_date,
                cache_dir=CACHE_DIR / "yfinance",
            )
        except Exception as e:
            logger.warning("    OHLCV 失敗: %s", e)
            continue

        import pandas as pd
        inst = broker = concentration = margin = pd.DataFrame()
        if finmind_token:
            try:
                inst = finmind.get_institutional(ticker, start_date, as_of_date)
            except Exception as e:
                logger.warning("    FinMind institutional 失敗: %s", e)
            try:
                broker = finmind.get_broker_distribution(ticker, as_of_date - timedelta(days=3), as_of_date)
            except Exception as e:
                logger.warning("    FinMind broker 失敗: %s", e)
            try:
                concentration = finmind.get_foreign_ownership(ticker, start_date, as_of_date)
            except Exception as e:
                logger.warning("    FinMind 外資持股 失敗: %s", e)
            try:
                margin = finmind.get_margin(ticker, start_date, as_of_date)
            except Exception as e:
                logger.warning("    FinMind 融資融券 失敗: %s", e)

        if not ohlcv.empty:
            recent_vol = int(ohlcv.sort_values("date").iloc[-1]["volume"])
        else:
            recent_vol = 0

        # 新聞 + 情緒
        sentiment: SentimentResult | None = None
        if not dry_run and sentiment_analyzer:
            try:
                news_items = news_coll.collect(ticker, company, lookback_hours=24)
                sentiment = sentiment_analyzer.score(ticker, company, news_items)
                logger.info("    情緒分數 %s: %.2f (%s)", ticker, sentiment.score, sentiment.reason)
            except Exception as e:
                logger.warning("    情緒分析失敗: %s", e)

        ticker_inputs.append(
            TickerInputs(
                ticker=ticker,
                company_name=company,
                ohlcv=ohlcv,
                institutional=inst,
                broker=broker,
                shares_outstanding=shares.get(ticker, 1_000_000_000),
                recent_volume=recent_vol,
                sentiment=sentiment,
                concentration=concentration,
                margin=margin,
            )
        )

    # ── TAIEX ─────────────────────────────────
    logger.info("抓取加權指數 ...")
    try:
        taiex = get_tw_ohlcv_adjusted("^TWII", start_date, as_of_date, cache_dir=CACHE_DIR / "yfinance")
    except Exception:
        import pandas as pd
        taiex = pd.DataFrame()

    # ── Pipeline ──────────────────────────────
    pipe = ScoringPipeline(STRATEGY_YAML, SECTOR_MAP_YAML, DAY_TRADER_YAML)
    pipe_out = pipe.run(
        PipelineInput(
            as_of_date=as_of_date,
            tickers=ticker_inputs,
            taiex_daily=taiex,
            overnight=overnight,
        )
    )

    # ── Concept Drift ─────────────────────────
    drift = ConceptDriftDetector(STRATEGY_YAML, DRIFT_LOG).verdict()

    # ── 晨報 ──────────────────────────────────
    taiex_close = 0.0
    taiex_above_ma = True
    if not taiex.empty:
        taiex_sorted = taiex.sort_values("date")
        taiex_close = float(taiex_sorted.iloc[-1]["close"])
        # 月線 = 20 日 SMA（台股慣例），盤前判斷用前日收盤
        if len(taiex_sorted) >= 20:
            ma20 = float(taiex_sorted["close"].tail(20).mean())
            taiex_above_ma = taiex_close > ma20

    report_md = render_morning_report(
        pipeline_out=pipe_out,
        portfolio=portfolio,
        drift=drift,
        company_names=watchlist,
        min_score=75.0,
        max_positions=3,
        taiex_close=taiex_close,
        taiex_above_ma=taiex_above_ma,
        asset_manager=am if am else None,
    )

    # ── 全球配置 + 部位建議（Phase 17a）──────────────
    advisor_md = ""
    try:
        regime = detect_regime(taiex) if not taiex.empty else None

        # 個股追蹤：2345 智邦 OCO 停損停利
        stock_trackers: list[StockTracker] = []
        for tk, name, cost, stop, target in [
            ("2345", "智邦", 2139.0, 1925.0, 2460.0),
        ]:
            try:
                df = pd.read_parquet(ROOT / f"data/cache/yfinance/tw_ohlcv/{tk}.parquet")
                cur = float(df.sort_values("date").iloc[-1]["close"])
                stock_trackers.append(
                    StockTracker(ticker=tk, name=name, cost=cost,
                                  current=cur, stop_loss=stop, take_profit=target)
                )
            except Exception:
                pass

        advisor_md = render_allocation_section(
            regime=regime,
            stock_trackers=stock_trackers,
            drift_checks=None,
            is_rebalance_day=is_quarterly_rebalance_day(as_of_date),
        )
    except Exception as e:
        logger.warning("配置建議生成失敗: %s", e)

    if advisor_md:
        report_md = report_md.rstrip() + "\n\n---\n\n" + advisor_md

    save_and_print(report_md, as_of_date)
    logger.info("晨報完成 → logs/%s.md", as_of_date)

    # ── Paper Trading 快照（Phase 10）────────────
    # 防守模式下 pipe_out.recommendations 為空，仍寫空檔以保留「當日系統有在跑」的軌跡
    if not dry_run:
        paper_path = paper_record_daily(
            PAPER_TRADES_DIR, as_of_date, pipe_out.recommendations
        )
        n = len(pipe_out.recommendations)
        logger.info(
            "Paper trade 快照：%s 筆推薦 → %s",
            n, paper_path.relative_to(ROOT),
        )


# ─────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台股晨報")
    parser.add_argument("--date", type=str, help="指定日期 YYYY-MM-DD（預設今天）")
    parser.add_argument("--dry-run", action="store_true", help="不呼叫 Claude API，不寫 logs")
    args = parser.parse_args()

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    main(run_date, dry_run=args.dry_run)
