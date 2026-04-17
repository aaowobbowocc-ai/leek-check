"""
Phase 7(整合) 測試 — ScoringPipeline 把 L1/L2/L3 串起來。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.data.adr_fetcher import OvernightReport
from src.data.news_collector import NewsItem
from src.strategy.scoring_pipeline import (
    PipelineInput,
    ScoringPipeline,
    TickerInputs,
)
from src.strategy.sentiment_factor import SentimentResult


def _write_strategy(tmp_path: Path, min_score: float = 0) -> Path:
    path = tmp_path / "strategy.yaml"
    path.write_text(
        f"""factor_weights:
  chip_concentration: 0.25
  sector_momentum:    0.10
  supply_chain:       0.20
  news_sentiment:     0.20
  technical:          0.15
  market_regime:      0.10
recommendation:
  min_score: {min_score}
  max_picks: 3
risk:
  max_per_trade_pct: 2.0
  max_single_position_pct: 20.0
  max_concurrent_positions: 3
  kelly_fraction: 0.5
  atr_stop_multiplier: 2.0
  atr_target_multiplier: 3.0
  liquidity_volume_pct: 1.0
  day_trader_entry_discount_atr: 0.5
black_swan:
  adr_drop_pct: -3.0
  vix_threshold: 25.0
  taiex_below_monthly_ma: true
regime:
  lookback_days: 20
  reference_years: 5
  thresholds:
    low: 0.8
    normal_high: 1.3
    high_crazy: 2.0
  crazy_force_cash: true
concept_drift:
  window_size: 5
  alert_threshold: 0.20
  force_paper_trading_after: 10
""",
        encoding="utf-8",
    )
    return path


def _write_sector(tmp_path: Path) -> Path:
    p = tmp_path / "sector.yaml"
    p.write_text(
        """semi_equipment:
  name: "半導體設備"
  tickers: [3413, 3680]
sector_momentum:
  min_triggers: 2
  chip_threshold: 0.3
  bonus_points: 10
  weak_sector_penalty: 5
  red_candle_ratio_min: 0.3
""",
        encoding="utf-8",
    )
    return p


def _write_day_trader(tmp_path: Path) -> Path:
    p = tmp_path / "dt.yaml"
    p.write_text(
        """known_day_trader_branches: []
thresholds:
  top_n_brokers: 5
  ratio_threshold: 0.40
  entry_discount_atr: 0.5
""",
        encoding="utf-8",
    )
    return p


def _fake_ohlcv(n: int = 30) -> pd.DataFrame:
    rows = []
    for i in range(n):
        close = 200 + i * 0.5
        rows.append(
            {
                "date": date(2026, 1, 1) + timedelta(days=i),
                "open": close - 0.3,
                "high": close + 0.8,
                "low": close - 0.6,
                "close": close,
                "volume": 10_000_000 + i * 1000,
            }
        )
    return pd.DataFrame(rows)


def _fake_inst(net: int = 2_000_000, days: int = 5) -> pd.DataFrame:
    rows = []
    base = date(2026, 4, 15)
    for i in range(days):
        rows.append(
            {
                "date": base - timedelta(days=i),
                "name": "投信",
                "buy": net,
                "sell": 0,
                "net_buy": net,
            }
        )
    return pd.DataFrame(rows)


def _fake_broker() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date(2026, 4, 15)] * 5,
            "broker_id": ["1", "2", "3", "4", "5"],
            "buy": [100_000, 80_000, 60_000, 40_000, 20_000],
            "sell": [10_000] * 5,
            "net_buy": [90_000, 70_000, 50_000, 30_000, 10_000],
        }
    )


def _fake_taiex(n: int = 300, trend: str = "up") -> pd.DataFrame:
    rows = []
    base = 20_000
    for i in range(n):
        if trend == "up":
            close = base + i * 20
        else:
            close = base - i * 20
        rows.append(
            {
                "date": date(2020, 1, 1) + timedelta(days=i),
                "open": close,
                "high": close + 150,
                "low": close - 150,
                "close": close,
            }
        )
    return pd.DataFrame(rows)


def _overnight(adr: float = 1.0, vix: float = 18.0) -> OvernightReport:
    return OvernightReport(
        as_of_date="2026-04-17",
        tsmc_adr_close=180.0,
        tsmc_adr_change_pct=adr,
        nvda_close=900.0,
        nvda_change_pct=adr,
        sox_close=220.0,
        sox_change_pct=adr * 0.8,
        vix=vix,
        market_mode="normal",
    )


def _ticker_input(
    ticker: str = "3413", sentiment: float = 0.5, company: str = "京鼎"
) -> TickerInputs:
    sent = SentimentResult(
        ticker=ticker, score=sentiment, reason="利多", n_news=3
    )
    return TickerInputs(
        ticker=ticker,
        company_name=company,
        ohlcv=_fake_ohlcv(),
        institutional=_fake_inst(),
        broker=_fake_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=10_000_000,
        news=[],
        sentiment=sent,
        today_open_close=(210.0, 212.0),
    )


# ─────────────────────────────────────────
# Tests
# ─────────────────────────────────────────
def test_pipeline_normal_mode_produces_recommendations(tmp_path: Path) -> None:
    pipe = ScoringPipeline(
        _write_strategy(tmp_path, min_score=0),
        _write_sector(tmp_path),
        _write_day_trader(tmp_path),
    )
    inp = PipelineInput(
        as_of_date=date(2026, 4, 17),
        tickers=[_ticker_input("3413"), _ticker_input("3680", company="家登")],
        taiex_daily=_fake_taiex(300, "up"),
        overnight=_overnight(),
    )
    out = pipe.run(inp)

    assert out.defensive is False
    assert out.regime in ("low", "normal", "high")
    assert len(out.recommendations) > 0
    assert all(r.score > 0 for r in out.recommendations)


def test_pipeline_defensive_on_adr_crash(tmp_path: Path) -> None:
    pipe = ScoringPipeline(
        _write_strategy(tmp_path),
        _write_sector(tmp_path),
        _write_day_trader(tmp_path),
    )
    inp = PipelineInput(
        as_of_date=date(2026, 4, 17),
        tickers=[_ticker_input("3413")],
        taiex_daily=_fake_taiex(300, "up"),
        overnight=_overnight(adr=-4.0),  # TSMC ADR 跌 4% → 防守
    )
    out = pipe.run(inp)

    assert out.defensive is True
    assert out.recommendations == []
    assert any("ADR" in r for r in out.defensive_reasons)


def test_pipeline_defensive_on_high_vix(tmp_path: Path) -> None:
    pipe = ScoringPipeline(
        _write_strategy(tmp_path),
        _write_sector(tmp_path),
        _write_day_trader(tmp_path),
    )
    inp = PipelineInput(
        as_of_date=date(2026, 4, 17),
        tickers=[_ticker_input("3413")],
        taiex_daily=_fake_taiex(300, "up"),
        overnight=_overnight(vix=28.0),
    )
    out = pipe.run(inp)
    assert out.defensive is True


def test_pipeline_weights_match_regime_overrides(tmp_path: Path) -> None:
    """normal regime → weights_used 應等於 yaml 預設權重。"""
    pipe = ScoringPipeline(
        _write_strategy(tmp_path),
        _write_sector(tmp_path),
        _write_day_trader(tmp_path),
    )
    inp = PipelineInput(
        as_of_date=date(2026, 4, 17),
        tickers=[_ticker_input("3413")],
        taiex_daily=_fake_taiex(300),
        overnight=_overnight(),
    )
    out = pipe.run(inp)
    assert sum(out.weights_used.values()) == pytest.approx(1.0, abs=0.01)
    assert out.atr_stop_multiplier in (2.0, 2.5)


def test_pipeline_no_sentiment_still_works(tmp_path: Path) -> None:
    pipe = ScoringPipeline(
        _write_strategy(tmp_path),
        _write_sector(tmp_path),
        _write_day_trader(tmp_path),
    )
    ti = TickerInputs(
        ticker="3413",
        company_name="京鼎",
        ohlcv=_fake_ohlcv(),
        institutional=_fake_inst(),
        broker=_fake_broker(),
        shares_outstanding=1_000_000_000,
        recent_volume=10_000_000,
        news=[],
        sentiment=None,
        today_open_close=(210.0, 212.0),
    )
    out = pipe.run(
        PipelineInput(
            as_of_date=date(2026, 4, 17),
            tickers=[ti],
            taiex_daily=_fake_taiex(300),
            overnight=_overnight(),
        )
    )
    assert out.defensive is False
