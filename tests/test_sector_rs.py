"""
Phase 11 — Sector 相對強弱（RS）測試。

驗證：
  - compute_sector_rs 正確算出族群等權報酬 − TAIEX 報酬
  - top_sectors 在 distinct_clusters=True 下，跳過同 cluster
    （Top 1 設備 + Top 2 材料同屬 semi_downstream → 應挑到 Top 3 其他 cluster）
  - leader_bonus / laggard_penalty 如預期調整 value
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.strategy.sector_factor import SectorFactor


def _write_sector_yaml(tmp_path: Path) -> Path:
    """
    3 族群 × 2 cluster + 1 單獨 cluster 共 3 cluster：
      - equipment (cluster: semi_downstream)   — tickers 3413, 3680
      - materials (cluster: semi_downstream)   — tickers 5347, 5483
      - cooling   (cluster: cooling_ai_server) — tickers 3017, 3042
    """
    path = tmp_path / "sector.yaml"
    path.write_text(
        """equipment:
  name: "半導體設備"
  cluster: "semi_downstream"
  tickers: [3413, 3680]
materials:
  name: "半導體材料"
  cluster: "semi_downstream"
  tickers: [5347, 5483]
cooling:
  name: "伺服器散熱"
  cluster: "cooling_ai_server"
  tickers: [3017, 3042]
sector_momentum:
  min_triggers: 3
  chip_threshold: 0.6
  bonus_points: 10
  weak_sector_penalty: 5
  red_candle_ratio_min: 0.3
relative_strength:
  lookback_days: 20
  top_n: 2
  bottom_n: 1
  enforce_distinct_clusters: true
  leader_bonus: 0.30
  laggard_penalty: 0.20
""",
        encoding="utf-8",
    )
    return path


def _synthetic_ohlcv(start_price: float, end_price: float, n: int = 25) -> pd.DataFrame:
    """
    產生 n 天線性成長/下跌的 OHLCV。
    保證 lookback=20 時，closes[-1] == end_price 且 closes[-21] == start_price（頭 n-21 天持平在 start_price）。
    """
    rows = []
    lead_in = max(0, n - 21)  # 前段持平在 start_price，讓 closes[-21] 精準等於 start_price
    for i in range(n):
        if i < lead_in:
            close = start_price
        else:
            j = i - lead_in
            close = start_price + (end_price - start_price) * (j / 20)
        rows.append(
            {
                "date": date(2024, 1, 1) + timedelta(days=i),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000_000,
            }
        )
    return pd.DataFrame(rows)


def test_compute_sector_rs_returns_excess(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    # Equipment +10% 20 日報酬；Materials +5%；Cooling 0%；TAIEX +2%
    sector_closes = {
        "3413": _synthetic_ohlcv(100.0, 110.0),
        "3680": _synthetic_ohlcv(100.0, 110.0),
        "5347": _synthetic_ohlcv(100.0, 105.0),
        "5483": _synthetic_ohlcv(100.0, 105.0),
        "3017": _synthetic_ohlcv(100.0, 100.0),
        "3042": _synthetic_ohlcv(100.0, 100.0),
    }
    taiex = _synthetic_ohlcv(17_000.0, 17_340.0)  # +2%

    rs = factor.compute_sector_rs(sector_closes, taiex)
    assert rs["equipment"] > rs["materials"] > rs["cooling"]
    assert abs(rs["equipment"] - (10 - 2)) < 0.5  # ≈ +8%
    assert abs(rs["cooling"] - (0 - 2)) < 0.5     # ≈ −2%


def test_top_sectors_enforces_distinct_clusters(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    # 人造 RS：Top 1 equipment、Top 2 materials（同 cluster: semi_downstream）、Top 3 cooling
    rs_map = {"equipment": 8.0, "materials": 5.0, "cooling": -2.0}
    leaders = factor.top_sectors(rs_map, n=2)
    assert "equipment" in leaders
    assert "materials" not in leaders  # 被跳過（同 cluster）
    assert "cooling" in leaders         # 往下找到不同 cluster


def test_top_sectors_allows_same_cluster_when_flag_off(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    rs_map = {"equipment": 8.0, "materials": 5.0, "cooling": -2.0}
    leaders = factor.top_sectors(rs_map, n=2, distinct_clusters=False)
    assert leaders == {"equipment", "materials"}


def test_bottom_sectors_picks_laggards(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    rs_map = {"equipment": 8.0, "materials": 5.0, "cooling": -2.0}
    laggards = factor.bottom_sectors(rs_map, n=1)
    assert laggards == {"cooling"}


def test_leader_flag_set_without_changing_value(tmp_path: Path) -> None:
    """leader/laggard 只打 flag — 實際 composite 層非對稱加減分由 CompositeScorer 處理。"""
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    chips = {"3413": 0.7}
    candles = {"3413": (100, 102)}
    base = factor.score("3413", chips, candles)
    lead = factor.score("3413", chips, candles, leader_sectors={"equipment"})
    assert lead.value == base.value           # sector value 不變
    assert lead.flags["sector_leading"] is True
    assert base.flags["sector_leading"] is False


def test_laggard_flag_set_without_changing_value(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    chips = {"3413": 0.7, "3680": 0.7}
    candles = {"3413": (100, 102), "3680": (200, 205)}
    base = factor.score("3413", chips, candles)
    lag = factor.score("3413", chips, candles, laggard_sectors={"equipment"})
    assert lag.value == base.value
    assert lag.flags["sector_lagging"] is True


def test_compute_sector_rs_graceful_on_empty(tmp_path: Path) -> None:
    factor = SectorFactor(_write_sector_yaml(tmp_path))
    # TAIEX 不足 20 天
    thin_taiex = _synthetic_ohlcv(100.0, 105.0, n=10)
    rs = factor.compute_sector_rs({}, thin_taiex)
    assert rs == {}
