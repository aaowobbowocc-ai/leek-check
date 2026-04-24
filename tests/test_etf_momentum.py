"""
ETF 動能輪動單元測試：
  1. 排名正確
  2. 絕對動能防禦（全負報酬 → 空手）
  3. 資料不足處理
  4. look-ahead 防護（只吃 <= as_of 的資料）
  5. portfolio_weights 等權分配
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.strategy.etf_momentum import (
    ETFConfig,
    compute_return,
    load_config,
    portfolio_weights,
    rank_etfs,
)


def _mk_ohlcv(start: date, closes: list[float]) -> pd.DataFrame:
    rows = [
        {
            "date": start + timedelta(days=i),
            "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
            "volume": 1_000_000,
        }
        for i, c in enumerate(closes)
    ]
    return pd.DataFrame(rows)


def _default_config(etfs: list[str]) -> ETFConfig:
    return ETFConfig(
        etfs=etfs,
        lookback_months=6,
        top_n=2,
        equal_weight=True,
        cash_when_all_negative=True,
    )


# ─────────────────────────────────────────
# compute_return
# ─────────────────────────────────────────
def test_compute_return_basic() -> None:
    closes = [100.0] + [100.0 + i for i in range(1, 130)]   # 130 筆
    df = _mk_ohlcv(date(2025, 1, 1), closes)
    r = compute_return(df, as_of=df.iloc[-1]["date"], lookback_days=126)
    assert r is not None and r > 0   # 上漲應為正


def test_compute_return_insufficient_data() -> None:
    df = _mk_ohlcv(date(2025, 1, 1), [100.0] * 50)
    assert compute_return(df, as_of=date(2025, 2, 20), lookback_days=126) is None


def test_compute_return_empty() -> None:
    assert compute_return(pd.DataFrame(), as_of=date(2025, 1, 1), lookback_days=126) is None


def test_compute_return_respects_as_of_cutoff() -> None:
    """as_of 之後的 bar 不應被看到。"""
    closes = [100.0] * 130 + [500.0] * 10    # 最後 10 天暴衝到 500
    df = _mk_ohlcv(date(2025, 1, 1), closes)
    # 用第 130 天當 cutoff → 不該看到 500 的噴發
    cutoff = df.iloc[129]["date"]
    r = compute_return(df, as_of=cutoff, lookback_days=126)
    assert r is not None
    # 應該接近 0（一路平）
    assert abs(r) < 0.01


# ─────────────────────────────────────────
# rank_etfs
# ─────────────────────────────────────────
def test_rank_etfs_selects_top_n_by_momentum() -> None:
    """三檔 ETF：A 漲 30%、B 漲 10%、C 跌 5% → top 2 = [A, B]。"""
    base = date(2025, 1, 1)
    n = 130
    ohlcv_map = {
        "A": _mk_ohlcv(base, [100.0 * (1 + 0.3 * i / (n - 1)) for i in range(n)]),
        "B": _mk_ohlcv(base, [100.0 * (1 + 0.1 * i / (n - 1)) for i in range(n)]),
        "C": _mk_ohlcv(base, [100.0 * (1 - 0.05 * i / (n - 1)) for i in range(n)]),
    }
    cfg = _default_config(["A", "B", "C"])
    ranking = rank_etfs(ohlcv_map, as_of=ohlcv_map["A"].iloc[-1]["date"], config=cfg)

    assert ranking.selected == ["A", "B"]
    assert ranking.defensive is False
    assert ranking.rankings[0][0] == "A"   # 降冪排列


def test_rank_etfs_defensive_when_all_negative() -> None:
    """全部 6M 報酬為負 → 現金防禦、selected 空。"""
    base = date(2025, 1, 1)
    n = 130
    ohlcv_map = {
        "A": _mk_ohlcv(base, [100.0 * (1 - 0.05 * i / (n - 1)) for i in range(n)]),
        "B": _mk_ohlcv(base, [100.0 * (1 - 0.10 * i / (n - 1)) for i in range(n)]),
    }
    cfg = _default_config(["A", "B"])
    ranking = rank_etfs(ohlcv_map, as_of=ohlcv_map["A"].iloc[-1]["date"], config=cfg)

    assert ranking.defensive is True
    assert ranking.selected == []


def test_rank_etfs_only_picks_positive_returns() -> None:
    """top_n=2，但只有 1 檔正報酬 → 只選 1 檔。"""
    base = date(2025, 1, 1)
    n = 130
    ohlcv_map = {
        "A": _mk_ohlcv(base, [100.0 * (1 + 0.2 * i / (n - 1)) for i in range(n)]),  # +20%
        "B": _mk_ohlcv(base, [100.0 * (1 - 0.05 * i / (n - 1)) for i in range(n)]), # -5%
        "C": _mk_ohlcv(base, [100.0 * (1 - 0.02 * i / (n - 1)) for i in range(n)]), # -2%
    }
    cfg = _default_config(["A", "B", "C"])
    ranking = rank_etfs(ohlcv_map, as_of=ohlcv_map["A"].iloc[-1]["date"], config=cfg)

    assert ranking.selected == ["A"]
    assert ranking.defensive is False


def test_rank_etfs_skips_missing_data() -> None:
    """某檔資料不足 → 不應 crash，跳過該檔。"""
    base = date(2025, 1, 1)
    n = 130
    ohlcv_map = {
        "A": _mk_ohlcv(base, [100.0 + i for i in range(n)]),
        "B": _mk_ohlcv(base, [100.0] * 20),   # 資料不足
    }
    cfg = _default_config(["A", "B"])
    ranking = rank_etfs(ohlcv_map, as_of=ohlcv_map["A"].iloc[-1]["date"], config=cfg)

    assert len(ranking.rankings) == 1   # 只有 A 有排名
    assert "A" in ranking.selected


# ─────────────────────────────────────────
# portfolio_weights
# ─────────────────────────────────────────
def test_portfolio_weights_equal() -> None:
    from src.strategy.etf_momentum import ETFRanking
    ranking = ETFRanking(
        as_of=date(2025, 1, 1),
        rankings=[("A", 0.2), ("B", 0.15)],
        selected=["A", "B"],
        defensive=False,
    )
    cfg = _default_config(["A", "B"])
    weights = portfolio_weights(ranking, cfg)
    assert weights == {"A": 0.5, "B": 0.5}


def test_portfolio_weights_defensive_returns_empty() -> None:
    from src.strategy.etf_momentum import ETFRanking
    ranking = ETFRanking(
        as_of=date(2025, 1, 1),
        rankings=[], selected=[], defensive=True,
    )
    cfg = _default_config(["A"])
    assert portfolio_weights(ranking, cfg) == {}


# ─────────────────────────────────────────
# load_config
# ─────────────────────────────────────────
def test_load_config_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "etf.yaml"
    path.write_text(
        """etfs:
  - ticker: "0050"
    name: "元大台灣50"
  - ticker: "00878"
    name: "國泰永續高股息"
strategy:
  lookback_months: 3
  top_n: 1
  equal_weight: true
  cash_when_all_negative: true
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.etfs == ["0050", "00878"]
    assert cfg.lookback_months == 3
    assert cfg.top_n == 1
