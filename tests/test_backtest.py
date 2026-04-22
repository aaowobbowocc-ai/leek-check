"""
Phase 8 測試 — 回測引擎骨架：cost model + data view + engine。
嚴格驗證：
  1. 成本扣除正確（手續費 + 0.3% 證交稅 + 滑價）
  2. data_view 不會回傳 date >= cutoff 的列（look-ahead bias 防線）
  3. engine 能跑完一段假資料，輸出關鍵指標
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest.cost_model import CostConfig, simulate_fill
from src.backtest.data_view import HistoricalDataView
from src.backtest.engine import BacktestEngine
from src.strategy.scoring_pipeline import ScoringPipeline


# ─────────────────────────────────────────
# cost_model
# ─────────────────────────────────────────
def test_cost_roundtrip_ratio_is_sensible() -> None:
    cfg = CostConfig()
    # 往返成本 = 0.1425%*2 + 0.3% + 0.1%*2 ≈ 0.785%
    assert 0.006 < cfg.total_cost_ratio() < 0.009


def test_simulate_fill_net_less_than_gross_when_profit() -> None:
    cfg = CostConfig()
    r = simulate_fill(cfg, entry_price=100.0, exit_price=105.0, shares=1000)
    assert r.gross_return_pct > r.net_return_pct
    # 毛 ~5%，扣完成本約 4.2% 左右
    assert 3.0 < r.net_return_pct < 4.5
    assert r.pnl > 0


def test_simulate_fill_small_profit_becomes_loss() -> None:
    """買 100 賣 100.5（+0.5%），扣完成本應變負報酬。"""
    cfg = CostConfig()
    r = simulate_fill(cfg, entry_price=100.0, exit_price=100.5, shares=1000)
    assert r.net_return_pct < 0
    assert r.gross_return_pct > 0


def test_simulate_fill_zero_shares_returns_zero() -> None:
    r = simulate_fill(CostConfig(), 100.0, 105.0, shares=0)
    assert r.pnl == 0.0


def test_cost_tax_discount_reduces_cost() -> None:
    normal = CostConfig(tax_rate_discount=1.0)
    day_trade = CostConfig(tax_rate_discount=0.5)
    assert day_trade.total_cost_ratio() < normal.total_cost_ratio()


# ─────────────────────────────────────────
# data_view — look-ahead bias 防護
# ─────────────────────────────────────────
def _fake_df(start: date, n: int) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append(
            {
                "date": start + timedelta(days=i),
                "open": 100 + i * 0.1,
                "high": 101 + i * 0.1,
                "low": 99 + i * 0.1,
                "close": 100 + i * 0.1,
                "volume": 10_000 + i,
            }
        )
    return pd.DataFrame(rows)


def test_view_never_leaks_cutoff_date() -> None:
    df = _fake_df(date(2024, 1, 1), 30)
    view = HistoricalDataView(
        ohlcv_by_ticker={"3413": df},
        institutional_by_ticker={},
        broker_by_ticker={},
        taiex=df.assign(close=df["close"] * 200),
        overnight_by_date={},
    )
    cutoff = date(2024, 1, 15)
    snap = view.at(cutoff)
    ohlcv_hist = snap.ohlcv("3413")
    max_date = pd.to_datetime(ohlcv_hist["date"]).dt.date.max()
    assert max_date < cutoff  # 嚴格小於
    assert max_date == cutoff - timedelta(days=1)


def test_view_bar_returns_exact_day() -> None:
    df = _fake_df(date(2024, 1, 1), 30)
    view = HistoricalDataView({"3413": df}, {}, {}, df, {})
    target = date(2024, 1, 15)
    bar = view.bar("3413", target)
    assert bar is not None
    assert bar["date"] == target


def test_view_missing_ticker_returns_empty() -> None:
    view = HistoricalDataView({}, {}, {}, pd.DataFrame(), {})
    snap = view.at(date(2024, 1, 10))
    assert snap.ohlcv("9999").empty
    assert view.bar("9999", date(2024, 1, 10)) is None


# ─────────────────────────────────────────
# engine — end-to-end smoke test
# ─────────────────────────────────────────
def _write_strategy(tmp_path: Path) -> Path:
    p = tmp_path / "strategy.yaml"
    p.write_text(
        """factor_weights:
  chip_concentration: 0.25
  sector_momentum:    0.10
  supply_chain:       0.20
  news_sentiment:     0.20
  technical:          0.15
  market_regime:      0.10
recommendation:
  min_score: 0
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
costs:
  buy_fee_rate: 0.001425
  sell_fee_rate: 0.001425
  tax_rate: 0.003
  slippage_rate: 0.001
  tax_rate_discount: 1.0
black_swan:
  adr_drop_pct: -10.0   # 很寬鬆，避免防守模式干擾測試
  vix_threshold: 99.0
  taiex_below_monthly_ma: false
regime:
  lookback_days: 20
  reference_years: 5
  thresholds:
    low: 0.8
    normal_high: 1.3
    high_crazy: 99.0     # 禁用狂波強制空手
  crazy_force_cash: false
concept_drift:
  window_size: 5
  alert_threshold: 0.20
  force_paper_trading_after: 10
""",
        encoding="utf-8",
    )
    return p


def _write_sector(tmp_path: Path) -> Path:
    p = tmp_path / "sector.yaml"
    p.write_text(
        """semi_equipment:
  name: "半導體設備"
  tickers: [3413, 3680]
sector_momentum:
  min_triggers: 1
  chip_threshold: 0.1
  bonus_points: 10
  weak_sector_penalty: 5
  red_candle_ratio_min: 0.1
""",
        encoding="utf-8",
    )
    return p


def _write_dt(tmp_path: Path) -> Path:
    p = tmp_path / "dt.yaml"
    p.write_text(
        """known_day_trader_branches: []
thresholds:
  top_n_brokers: 5
  ratio_threshold: 0.40
""",
        encoding="utf-8",
    )
    return p


def _fake_bullish_ohlcv(ticker: str, n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(hash(ticker) % 2**32)
    rows = []
    price = 200.0
    for i in range(n):
        drift = 0.3
        noise = rng.normal(0, 1.5)
        close = price + drift + noise
        rows.append(
            {
                "date": date(2024, 1, 1) + timedelta(days=i),
                "open": price,
                "high": max(price, close) + 1.0,
                "low": min(price, close) - 1.0,
                "close": close,
                "volume": int(5_000_000 + rng.integers(-100_000, 100_000)),
            }
        )
        price = close
    return pd.DataFrame(rows)


def _fake_inst_series(start: date, n: int) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append(
            {
                "date": start + timedelta(days=i),
                "name": "投信",
                "buy": 2_000_000,
                "sell": 500_000,
                "net_buy": 1_500_000,
            }
        )
    return pd.DataFrame(rows)


def _fake_broker_single(d: date) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [d] * 5,
            "broker_id": ["1", "2", "3", "4", "5"],
            "buy": [100_000, 80_000, 60_000, 40_000, 20_000],
            "sell": [10_000] * 5,
            "net_buy": [90_000, 70_000, 50_000, 30_000, 10_000],
        }
    )


def _fake_taiex(n: int = 120) -> pd.DataFrame:
    rows = []
    base = 20_000
    for i in range(n):
        close = base + i * 10
        rows.append(
            {
                "date": date(2024, 1, 1) + timedelta(days=i),
                "open": close,
                "high": close + 80,
                "low": close - 80,
                "close": close,
            }
        )
    return pd.DataFrame(rows)


def test_backtest_engine_runs_end_to_end(tmp_path: Path) -> None:
    pipe = ScoringPipeline(
        _write_strategy(tmp_path), _write_sector(tmp_path), _write_dt(tmp_path)
    )

    ohlcv = {"3413": _fake_bullish_ohlcv("3413"), "3680": _fake_bullish_ohlcv("3680")}
    inst = {
        "3413": _fake_inst_series(date(2024, 1, 1), 120),
        "3680": _fake_inst_series(date(2024, 1, 1), 120),
    }
    # broker 每日都有一份（回測取 last_date 的那份）
    all_broker_rows = []
    for i in range(120):
        d = date(2024, 1, 1) + timedelta(days=i)
        b = _fake_broker_single(d)
        all_broker_rows.append(b)
    broker_all = pd.concat(all_broker_rows, ignore_index=True)
    broker = {"3413": broker_all, "3680": broker_all.copy()}

    view = HistoricalDataView(
        ohlcv_by_ticker=ohlcv,
        institutional_by_ticker=inst,
        broker_by_ticker=broker,
        taiex=_fake_taiex(),
        overnight_by_date={
            date(2024, 1, 1) + timedelta(days=i): {
                "tsmc_adr_change_pct": 0.5,
                "nvda_change_pct": 1.0,
                "sox_change_pct": 0.8,
                "vix": 16.0,
                "market_mode": "normal",
            }
            for i in range(120)
        },
    )

    engine = BacktestEngine(
        pipeline=pipe,
        view=view,
        cost=CostConfig(),
        initial_equity=100_000,
    )

    trading_days = [date(2024, 3, 1) + timedelta(days=i) for i in range(30)]
    ticker_meta = {
        "3413": {"company_name": "京鼎", "shares_outstanding": 1_000_000_000},
        "3680": {"company_name": "家登", "shares_outstanding": 1_000_000_000},
    }

    report = engine.run(trading_days, ["3413", "3680"], ticker_meta)

    # 驗證：有 equity curve 且長度相符
    assert len(report.equity_curve) == len(trading_days)
    # 指標都有輸出（不論賺賠）
    assert "trades" in report.metrics
    assert "max_drawdown_pct" in report.metrics
    # 權益曲線數字合理（沒有爆炸或變負）
    assert report.equity_curve["equity"].min() > 0


def test_backtest_engine_defensive_days_make_no_trades(tmp_path: Path) -> None:
    """TSMC ADR 暴跌 → 防守模式 → 不應開新倉。"""
    pipe = ScoringPipeline(
        _write_strategy(tmp_path), _write_sector(tmp_path), _write_dt(tmp_path)
    )

    ohlcv = {"3413": _fake_bullish_ohlcv("3413")}
    inst = {"3413": _fake_inst_series(date(2024, 1, 1), 120)}
    broker = {"3413": _fake_broker_single(date(2024, 1, 10))}

    view = HistoricalDataView(
        ohlcv,
        inst,
        broker,
        _fake_taiex(),
        # 每天都暴跌 → 每天都防守
        overnight_by_date={
            date(2024, 1, 1) + timedelta(days=i): {
                "tsmc_adr_change_pct": -15.0,
                "nvda_change_pct": -15.0,
                "sox_change_pct": -15.0,
                "vix": 45.0,
                "market_mode": "defensive",
            }
            for i in range(120)
        },
    )
    engine = BacktestEngine(pipe, view, CostConfig())
    report = engine.run(
        [date(2024, 3, 1) + timedelta(days=i) for i in range(10)],
        ["3413"],
        {"3413": {"company_name": "京鼎", "shares_outstanding": 1_000_000_000}},
    )
    # ADR_drop 閾值測試模式設為 -10.0，-15% 超過 → 防守 → 0 筆交易
    assert report.metrics.get("trades", 0) == 0


# ─────────────────────────────────────────
# walk_forward + survival_check
# ─────────────────────────────────────────
def _build_view_for_wf() -> HistoricalDataView:
    ohlcv = {"3413": _fake_bullish_ohlcv("3413", n=400), "3680": _fake_bullish_ohlcv("3680", n=400)}
    inst = {
        "3413": _fake_inst_series(date(2024, 1, 1), 400),
        "3680": _fake_inst_series(date(2024, 1, 1), 400),
    }
    all_broker = pd.concat(
        [_fake_broker_single(date(2024, 1, 1) + timedelta(days=i)) for i in range(400)],
        ignore_index=True,
    )
    broker = {"3413": all_broker, "3680": all_broker.copy()}
    return HistoricalDataView(
        ohlcv_by_ticker=ohlcv,
        institutional_by_ticker=inst,
        broker_by_ticker=broker,
        taiex=_fake_taiex(n=400),
        overnight_by_date={
            date(2024, 1, 1) + timedelta(days=i): {
                "tsmc_adr_change_pct": 0.3,
                "nvda_change_pct": 0.5,
                "sox_change_pct": 0.4,
                "vix": 16.0,
                "market_mode": "normal",
            }
            for i in range(400)
        },
    )


def test_walk_forward_runs_on_synthetic_data(tmp_path: Path) -> None:
    from src.backtest.walk_forward import run_walk_forward

    strat = _write_strategy(tmp_path)
    sect = _write_sector(tmp_path)
    dt = _write_dt(tmp_path)

    def factory() -> ScoringPipeline:
        return ScoringPipeline(strat, sect, dt)

    view = _build_view_for_wf()
    calendar = [date(2024, 1, 1) + timedelta(days=i) for i in range(400)]

    report = run_walk_forward(
        view=view,
        pipeline_factory=factory,
        cost=CostConfig(),
        trading_calendar=calendar,
        watchlist=["3413", "3680"],
        ticker_meta={
            "3413": {"company_name": "京鼎", "shares_outstanding": 1_000_000_000},
            "3680": {"company_name": "家登", "shares_outstanding": 1_000_000_000},
        },
        start=date(2024, 6, 1),
        end=date(2024, 8, 1),
        train_months=3,            # 測試用短期間
        test_months=1,
    )

    # 至少產出 1 個視窗，且每個視窗的 chosen_preset 合法
    assert len(report.windows) >= 1
    for w in report.windows:
        assert w.chosen_preset in {"default", "chip_heavy", "technical_heavy", "supply_chain_heavy"}
        assert w.train_end <= w.test_start
    # 整體權益曲線不得變負
    if not report.equity_curve.empty:
        assert report.equity_curve["equity"].min() > 0


def test_survival_check_handles_missing_data(tmp_path: Path) -> None:
    from src.backtest.survival_check import run_survival_check

    def factory() -> ScoringPipeline:
        return ScoringPipeline(
            _write_strategy(tmp_path), _write_sector(tmp_path), _write_dt(tmp_path)
        )

    # 完全沒有 2018/2020/2022 的資料 → 三個視窗都應回報「無交易日資料」
    view = HistoricalDataView({}, {}, {}, pd.DataFrame(), {})
    results = run_survival_check(
        view=view,
        pipeline_factory=factory,
        cost=CostConfig(),
        trading_calendar=[],
        watchlist=["3413"],
        ticker_meta={"3413": {"company_name": "京鼎", "shares_outstanding": 1_000_000_000}},
    )
    assert len(results) == 3
    assert all(not r.passed for r in results)
    assert all("無交易日資料" in r.reason for r in results)
