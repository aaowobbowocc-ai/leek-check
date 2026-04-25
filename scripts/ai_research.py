"""
AI Research CLI — 對指定 ticker 做 Claude 體檢，輸出 Markdown 報告。

用法：
    python scripts/ai_research.py 2330                   # 單檔
    python scripts/ai_research.py --batch 3413,3680,8996  # 批次
    python scripts/ai_research.py --candidates           # 跑 Quality Momentum
                                                          #   選 top 30 自動分析

依賴：
    config/.env 需有 ANTHROPIC_API_KEY
    可選 FINMIND_TOKEN（沒設則用 HybridClient + TWSE/MOPS 自抓）

輸出：
    logs/ai_research_{ticker}_{date}.md
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

from src.data.finmind_client import FinMindClient
from src.data.hybrid_client import HybridClient
from src.data.mops_client import MOPSClient
from src.data.news_collector import NewsCollector
from src.data.twse_client import TWSEClient
from src.strategy.ai_research_helper import (
    AIResearchAnalyzer,
    AIResearchReport,
    gather_ticker_data,
)

LOGS = ROOT / "logs"
NEWS_KEYWORDS = ROOT / "config" / "news_keywords.yaml"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


_COMPANY_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科",
    "3413": "京鼎", "3680": "家登", "3131": "弘塑", "8996": "高力",
    "6274": "台燿", "3037": "欣興", "3189": "景碩", "6449": "鈺邦",
    "3017": "奇鋐", "3042": "晶技", "2382": "廣達", "3231": "緯創", "2376": "技嘉",
    "2345": "智邦", "6770": "力積電", "00905": "中信數據及電力",
}


def _make_client():
    """組合資料源 — 優先 HybridClient，FinMind 作 fallback for broker 等。"""
    finmind_token = os.environ.get("FINMIND_TOKEN", "")
    fm = FinMindClient(token=finmind_token) if finmind_token else None
    return HybridClient(twse=TWSEClient(), mops=MOPSClient(), finmind=fm)


def _make_analyzer():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[ERROR] ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    return AIResearchAnalyzer(client)


def analyze_one(ticker: str, as_of: date | None = None) -> AIResearchReport:
    as_of = as_of or date.today()
    company = _COMPANY_NAMES.get(ticker, ticker)
    finmind = _make_client()
    news = NewsCollector(NEWS_KEYWORDS) if NEWS_KEYWORDS.exists() else None
    analyzer = _make_analyzer()

    print(f"  [收集] {ticker} {company} 資料...", flush=True)
    bundle = gather_ticker_data(
        ticker=ticker, company_name=company,
        finmind=finmind, news_collector=news, as_of=as_of,
    )
    print(f"  [分析] 餵給 Claude...", flush=True)
    report = analyzer.analyze(bundle)

    LOGS.mkdir(exist_ok=True)
    out = LOGS / f"ai_research_{ticker}_{as_of.isoformat()}.md"
    out.write_text(report.to_markdown(), encoding="utf-8")
    print(f"  [完成] → {out}", flush=True)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", nargs="?", help="單檔 ticker，如 2330")
    ap.add_argument("--batch", type=str, help="逗號分隔多個 ticker")
    ap.add_argument("--as-of", type=str, help="分析日期 YYYY-MM-DD（預設 today）")
    args = ap.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    if args.batch:
        tickers = [t.strip() for t in args.batch.split(",") if t.strip()]
    elif args.ticker:
        tickers = [args.ticker]
    else:
        ap.error("需提供 ticker 或 --batch")

    print(f"=== AI Research 開跑（{len(tickers)} 檔，as_of={as_of}）===")
    for tk in tickers:
        try:
            r = analyze_one(tk, as_of)
            tag = "🟢" if not r.has_error else "🔴"
            print(f"  {tag} {tk}: {r.verdict[:50] if not r.has_error else r.error}")
        except KeyboardInterrupt:
            print("中斷")
            break
        except Exception as e:
            print(f"  🔴 {tk}: {e}")


if __name__ == "__main__":
    main()
