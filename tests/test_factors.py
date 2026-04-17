"""
Phase 4 測試 — chip / technical / supply_chain / sector / market 因子。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.strategy.chip_factor import ChipFactor
from src.strategy.market_factor import MarketFactor
from src.strategy.sector_factor import SectorFactor
from src.strategy.supply_chain_factor import SupplyChainFactor
from src.strategy.technical_factor import TechnicalFactor, atr_from_ohlcv


# ─────────────────────────────────────────
# ChipFactor
# ─────────────────────────────────────────
def _write_day_trader_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "day_trader.yaml"
    path.write_text(
        """known_day_trader_branches:
  - broker_id: "9200"
    branch_name: "台北"
    full: "凱基-台北"
  - broker_id: "9600"
    branch_name: "建國"
    full: "富邦-建國"
thresholds:
  top_n_brokers: 5
  ratio_threshold: 0.40
  entry_discount_atr: 0.5
""",
        encoding="utf-8",
    )
    return path


def _inst_df(trust_days: int, daily_net: int) -> pd.DataFrame:
    rows = []
    base = date(2026, 4, 15)
    for i in range(10):
        rows.append({"date": base - timedelta(days=i), "name": "外資", "buy": 0, "sell": 0, "net_buy": 0})
    for i in range(trust_days):
        rows.append({"date": base - timedelta(days=i), "name": "投信", "buy": daily_net, "sell": 0, "net_buy": daily_net})
    return pd.DataFrame(rows)


def _broker_df(net_buys: list[int], broker_ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date(2026, 4, 15)] * len(net_buys),
            "broker_id": broker_ids,
            "buy": [nb + 100 for nb in net_buys],
            "sell": [100] * len(net_buys),
            "net_buy": net_buys,
        }
    )


def test_chip_trust_streak_gives_positive_score(tmp_path: Path) -> None:
    dt_path = _write_day_trader_yaml(tmp_path)
    factor = ChipFactor(dt_path)
    inst = _inst_df(trust_days=5, daily_net=1_000_000)
    broker = _broker_df([50_000, 40_000, 30_000, 20_000, 10_000], ["7777", "8888", "5000", "6000", "4000"])

    result = factor.score(
        "3413",
        institutional=inst,
        broker=broker,
        shares_outstanding=1_000_000_000,  # 10 億股
        recent_volume=10_000_000,
    )

    assert result.value > 0.3
    assert result.breakdown["trust_streak_days"] == 5
    assert result.flags["day_trader_risk"] is False


def test_chip_detects_day_trader_risk(tmp_path: Path) -> None:
    dt_path = _write_day_trader_yaml(tmp_path)
    factor = ChipFactor(dt_path)
    inst = _inst_df(trust_days=0, daily_net=0)
    # top 5 分點中 3 檔是隔日沖黑名單 → 60%
    broker = _broker_df(
        [100_000, 80_000, 60_000, 40_000, 20_000],
        ["9200", "9600", "9200", "5555", "6666"],
    )
    result = factor.score("3413", inst, broker, 1_000_000_000, 10_000_000)
    assert result.flags["day_trader_risk"] is True
    assert "隔日沖" in result.reason


def test_chip_empty_data_returns_zero(tmp_path: Path) -> None:
    dt_path = _write_day_trader_yaml(tmp_path)
    factor = ChipFactor(dt_path)
    result = factor.score("3413", pd.DataFrame(), pd.DataFrame(), 1_000_000_000, 10_000_000)
    assert result.value == 0.0


# ─────────────────────────────────────────
# TechnicalFactor
# ─────────────────────────────────────────
def _fake_ohlcv(n: int = 30, trend: str = "up") -> pd.DataFrame:
    base = 100.0
    rows = []
    for i in range(n):
        if trend == "up":
            close = base + i * 0.5
        elif trend == "down":
            close = base - i * 0.5
        else:
            close = base + (1 if i % 2 == 0 else -1)
        rows.append(
            {
                "date": date(2026, 1, 1) + timedelta(days=i),
                "open": close - 0.3,
                "high": close + 0.5,
                "low": close - 0.6,
                "close": close,
                "volume": 10_000 + i * 100,
            }
        )
    return pd.DataFrame(rows)


def test_technical_uptrend_positive() -> None:
    df = _fake_ohlcv(30, "up")
    factor = TechnicalFactor()
    result = factor.score(df)
    assert result.value > 0.0
    assert result.breakdown["price_above_ma_pct"] > 0


def test_technical_downtrend_low_score() -> None:
    df = _fake_ohlcv(30, "down")
    factor = TechnicalFactor()
    result = factor.score(df)
    assert result.value < 0.5


def test_technical_insufficient_data() -> None:
    df = _fake_ohlcv(5)
    result = TechnicalFactor().score(df)
    assert result.value == 0.0
    assert result.reason == "資料不足"


def test_atr_utility() -> None:
    df = _fake_ohlcv(30, "up")
    atr = atr_from_ohlcv(df, period=14)
    assert atr > 0.0


# ─────────────────────────────────────────
# SupplyChainFactor
# ─────────────────────────────────────────
def test_supply_chain_positive_overnight() -> None:
    factor = SupplyChainFactor()
    result = factor.score(
        "3413",
        nvda_change_pct=2.0,
        sox_change_pct=1.5,
        tsm_change_pct=1.0,
        leader_below_monthly_ma=False,
        ticker_price_above_5ma_pct=0.5,
    )
    assert result.value > 0.5
    assert result.flags["leader_divergence"] is False


def test_supply_chain_leader_divergence_flag() -> None:
    factor = SupplyChainFactor()
    result = factor.score(
        "3680",
        nvda_change_pct=0.0,
        sox_change_pct=0.0,
        tsm_change_pct=0.0,
        leader_below_monthly_ma=True,
        ticker_price_above_5ma_pct=2.0,
    )
    assert result.flags["leader_divergence"] is True
    assert "龍頭背離" in result.reason


def test_supply_chain_unknown_ticker_uses_default_corr() -> None:
    factor = SupplyChainFactor()
    result = factor.score(
        "9999",
        nvda_change_pct=1.0,
        sox_change_pct=1.0,
        tsm_change_pct=1.0,
        leader_below_monthly_ma=False,
        ticker_price_above_5ma_pct=0.0,
    )
    # 預設 corr 0.3，weighted ~0.3%，介於中性
    assert 0.4 < result.value < 0.7


# ─────────────────────────────────────────
# SectorFactor
# ─────────────────────────────────────────
def _write_sector_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "sector.yaml"
    path.write_text(
        """semi_equipment:
  name: "半導體設備"
  tickers: [3413, 3680, 3131, 8996]
cooling:
  name: "伺服器散熱"
  tickers: [3017, 3042]
sector_momentum:
  min_triggers: 3
  chip_threshold: 0.6
  bonus_points: 10
  weak_sector_penalty: 5
  red_candle_ratio_min: 0.3
""",
        encoding="utf-8",
    )
    return path


def test_sector_triggers_when_peers_hot(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    chips = {"3413": 0.8, "3680": 0.75, "3131": 0.7, "8996": 0.3}
    candles = {"3413": (100, 102), "3680": (200, 205), "3131": (150, 151), "8996": (80, 78)}
    result = factor.score("3413", chips, candles)
    assert result.value == pytest.approx(1.0)
    assert result.breakdown["sector_triggers"] == 3
    assert result.flags["sector_weak"] is False


def test_sector_weak_flag_when_red_candles_few(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    chips = {"3413": 0.9}
    candles = {"3413": (100, 99), "3680": (200, 198), "3131": (150, 149), "8996": (80, 79)}
    result = factor.score("3413", chips, candles)
    assert result.flags["sector_weak"] is True
    assert result.breakdown["red_candle_ratio"] < 0.3


def test_sector_unknown_ticker(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    result = factor.score("9999", {}, {})
    assert result.value == 0.0
    assert result.reason == "未分類產業"


def test_sector_peers_helper(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    peers = factor.peers_of("3413")
    assert set(peers) == {"3413", "3680", "3131", "8996"}
    assert factor.sector_of("3413") == "semi_equipment"
    assert factor.sector_of("9999") is None


# ─────────────────────────────────────────
# MarketFactor
# ─────────────────────────────────────────
def _fake_taiex(n: int = 30, trend: str = "up") -> pd.DataFrame:
    base = 20_000
    rows = []
    for i in range(n):
        close = base + (i * 20 if trend == "up" else -i * 20)
        rows.append({"date": date(2026, 1, 1) + timedelta(days=i), "close": close})
    return pd.DataFrame(rows)


def test_market_above_ma() -> None:
    result = MarketFactor().score(_fake_taiex(30, "up"))
    assert result.value > 0.5
    assert result.flags["below_monthly_ma"] is False


def test_market_below_ma_low_score() -> None:
    result = MarketFactor().score(_fake_taiex(30, "down"))
    assert result.value < 0.5
    assert result.flags["below_monthly_ma"] is True


def test_market_insufficient_data_neutral() -> None:
    result = MarketFactor().score(_fake_taiex(5))
    assert result.value == 0.5
