"""
Debug 模式 — 診斷「為什麼某段期間 0 trades」。

用法：
    python scripts/debug_pipeline.py --date 2018-06-15
    python scripts/debug_pipeline.py --sample 2018-01-01 2018-12-31 --n 10
    python scripts/debug_pipeline.py --sample 2022-06-01 2022-12-31 --n 8 --min-score 50

每個採樣日會印出：
    1. 防守模式？為什麼？
    2. regime 與 vol_ratio
    3. 每檔候選股的六大因子分數 + 合成分 + 入手區間
    4. 若 0 筆推薦，指出離門檻最近的候選
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

from scripts.paper_trading_replay import build_view, load_ticker_meta, load_watchlist
from src.strategy.composite_scorer import FactorBundle
from src.strategy.scoring_pipeline import (
    PipelineInput,
    ScoringPipeline,
    TickerInputs,
    _sentiment_to_factor,
)
from src.strategy.technical_factor import atr_from_ohlcv

STRATEGY = ROOT / "config" / "strategy.yaml"
SECTOR = ROOT / "config" / "sector_map.yaml"
DT = ROOT / "config" / "day_trader_brokers.yaml"


def debug_day(view, tickers: list[str], meta: dict, d: date, min_score_override: float | None) -> None:
    pipe = ScoringPipeline(STRATEGY, SECTOR, DT)
    if min_score_override is not None:
        pipe._composite._min_score = min_score_override  # noqa: SLF001

    snap = view.at(d)
    ticker_inputs: list[TickerInputs] = []
    for t in tickers:
        ohlcv = snap.ohlcv(t)
        if ohlcv.empty:
            continue
        last_date = pd.to_datetime(ohlcv["date"]).dt.date.max()
        recent_vol = int(
            ohlcv.loc[pd.to_datetime(ohlcv["date"]).dt.date == last_date, "volume"].sum()
        )
        m = meta.get(t, {})
        ticker_inputs.append(
            TickerInputs(
                ticker=t,
                company_name=m.get("company_name", t),
                ohlcv=ohlcv,
                institutional=snap.institutional(t),
                broker=snap.broker_on(t, last_date),
                shares_outstanding=int(m.get("shares_outstanding", 2_000_000_000)),
                recent_volume=recent_vol,
                news=[],
                sentiment=None,
                concentration=snap.concentration(t),
            )
        )

    print(f"\n{'═'*70}\n📅 {d}   候選 {len(ticker_inputs)} 檔\n{'═'*70}")

    if not ticker_inputs:
        print("⚠️  當天無任何股票資料")
        return

    out = pipe.run(
        PipelineInput(
            as_of_date=d,
            tickers=ticker_inputs,
            taiex_daily=snap.taiex_window(),
            overnight=_cast_overnight(snap.overnight),
        )
    )

    print(f"regime={out.regime}  vol_ratio={out.vol_ratio:.2f}  "
          f"atr_stop_mult={out.atr_stop_multiplier:.2f}")
    print(f"overnight: TSM {snap.overnight['tsmc_adr_change_pct']:+.2f}%  "
          f"VIX {snap.overnight['vix']:.1f}")
    print(f"權重（套用 regime 覆寫後）: {out.weights_used}")

    if out.defensive:
        print(f"🔴 防守模式啟動 — 原因：")
        for r in out.defensive_reasons:
            print(f"    • {r}")
        return

    # 逐檔重新算一次分數（不論是否達門檻都印）— 直接呼叫 composite.score
    # 先重建 bundle 逐檔印 breakdown
    print(f"\n推薦通過門檻：{len(out.recommendations)} 筆")
    if out.recommendations:
        for r in out.recommendations:
            print(f"  🎯 {r.ticker} score={r.score}  "
                  f"entry {r.entry_low}~{r.entry_high}  stop {r.stop}  target {r.target}")
    else:
        print("  （0 筆達標 — 以下列出 top 5 接近門檻者）")

    # 拉出所有候選的完整 breakdown（不分是否達標）
    bundles = _rebuild_bundles(pipe, snap, ticker_inputs, out)
    all_recos = [pipe._composite.score(b, weights=out.weights_used,
                                       atr_stop_multiplier=out.atr_stop_multiplier) for b in bundles]
    all_recos.sort(key=lambda r: r.score, reverse=True)

    print(f"\n全候選分數（高→低，門檻 {pipe._composite.min_score}）：")
    for r in all_recos[:8]:
        chk = "✅" if r.score >= pipe._composite.min_score else "❌"
        bd = r.breakdown
        print(
            f"  {chk} {r.ticker} {r.score:5.1f}  "
            f"chip={bd.get('chip_value', 0):.2f} sector={bd.get('sector_value', 0):.2f} "
            f"supply={bd.get('supply_chain_value', 0):.2f} news={bd.get('news_value', 0):.2f} "
            f"tech={bd.get('technical_value', 0):.2f} mkt={bd.get('market_value', 0):.2f}  "
            f"flags={list(r.flags.keys())}"
        )


def _rebuild_bundles(pipe: ScoringPipeline, snap, ticker_inputs: list[TickerInputs], out) -> list[FactorBundle]:
    """重跑 pipeline 內部邏輯取得每檔 FactorBundle（debug 專用）。"""
    bundles: list[FactorBundle] = []
    chip_scores: dict = {}
    peer_chip_vals: dict = {}
    atrs: dict = {}
    prev_closes: dict = {}

    for ti in ticker_inputs:
        chip_scores[ti.ticker] = pipe._chip.score(
            ti.ticker, ti.institutional, ti.broker,
            ti.shares_outstanding, ti.recent_volume,
            concentration=ti.concentration,
        )
        peer_chip_vals[ti.ticker] = chip_scores[ti.ticker].value
        atrs[ti.ticker] = atr_from_ohlcv(ti.ohlcv, period=14)
        if not ti.ohlcv.empty:
            prev_closes[ti.ticker] = float(ti.ohlcv.sort_values("date").iloc[-1]["close"])
        else:
            prev_closes[ti.ticker] = 0.0

    market_score = pipe._market.score(snap.taiex_window())
    for ti in ticker_inputs:
        tech_score = pipe._tech.score(ti.ohlcv)
        sector_score = pipe._sector.score(ti.ticker, peer_chip_vals, {})
        supply_score = pipe._supply.score(
            ti.ticker,
            nvda_change_pct=snap.overnight["nvda_change_pct"],
            sox_change_pct=snap.overnight["sox_change_pct"],
            tsm_change_pct=snap.overnight["tsmc_adr_change_pct"],
            leader_below_monthly_ma=market_score.flags.get("below_monthly_ma", False),
            ticker_price_above_5ma_pct=tech_score.breakdown.get("price_above_ma_pct", 0.0),
        )
        news_score = _sentiment_to_factor(ti.sentiment)
        bundles.append(
            FactorBundle(
                ticker=ti.ticker,
                chip=chip_scores[ti.ticker],
                sector=sector_score,
                supply_chain=supply_score,
                news=news_score,
                technical=tech_score,
                market=market_score,
                atr=atrs[ti.ticker],
                prev_close=prev_closes[ti.ticker],
            )
        )
    return bundles


def _cast_overnight(d: dict):
    from src.data.adr_fetcher import OvernightReport
    return OvernightReport(
        as_of_date=str(d.get("as_of_date", "")),
        tsmc_adr_close=float(d.get("tsmc_adr_close", float("nan"))),
        tsmc_adr_change_pct=float(d.get("tsmc_adr_change_pct", 0.0)),
        nvda_close=float(d.get("nvda_close", float("nan"))),
        nvda_change_pct=float(d.get("nvda_change_pct", 0.0)),
        sox_close=float(d.get("sox_close", float("nan"))),
        sox_change_pct=float(d.get("sox_change_pct", 0.0)),
        vix=float(d.get("vix", 15.0)),
        market_mode=str(d.get("market_mode", "normal")),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, help="YYYY-MM-DD 單日診斷")
    ap.add_argument("--sample", nargs=2, metavar=("START", "END"),
                    help="隨機抽 N 天做診斷")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--min-score", type=float, default=None)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    tickers = load_watchlist()
    meta = load_ticker_meta(tickers)

    if args.date:
        d = date.fromisoformat(args.date)
        view, _ = build_view(tickers, d - timedelta(days=60), d + timedelta(days=1))
        debug_day(view, tickers, meta, d, args.min_score)
        return

    if not args.sample:
        ap.error("需 --date 或 --sample START END")
    start, end = [date.fromisoformat(x) for x in args.sample]
    view, calendar = build_view(tickers, start, end)
    if not calendar:
        print("❌ 該區間無交易日資料")
        return
    random.seed(args.seed)
    days = sorted(random.sample(calendar, min(args.n, len(calendar))))
    print(f"抽樣 {len(days)} 天 / 共 {len(calendar)} 交易日\n")
    for d in days:
        debug_day(view, tickers, meta, d, args.min_score)


if __name__ == "__main__":
    main()
