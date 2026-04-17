"""
Phase 5 測試 — composite_scorer 的加權、flag 處理、ATR 止損/目標、排序。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.strategy.composite_scorer import CompositeScorer, FactorBundle
from src.strategy.factor_base import FactorScore


def _write_strategy_yaml(tmp_path: Path, min_score: float = 75) -> Path:
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
""",
        encoding="utf-8",
    )
    return path


def _bundle(
    ticker: str,
    chip: float = 0.8,
    sector: float = 1.0,
    supply: float = 0.7,
    news: float = 0.6,   # 情緒 0.6 映射成 0.8
    tech: float = 0.7,
    market: float = 0.8,
    atr: float = 5.0,
    prev_close: float = 200.0,
    day_trader: bool = False,
    leader_div: bool = False,
    sector_weak: bool = False,
) -> FactorBundle:
    return FactorBundle(
        ticker=ticker,
        chip=FactorScore(value=chip, flags={"day_trader_risk": day_trader}, reason="chip 理由"),
        sector=FactorScore(value=sector, flags={"sector_weak": sector_weak}, reason="族群理由"),
        supply_chain=FactorScore(value=supply, flags={"leader_divergence": leader_div}, reason="供應鏈理由"),
        news=FactorScore(value=news, reason="新聞理由"),
        technical=FactorScore(value=tech, reason="技術理由"),
        market=FactorScore(value=market, reason="大盤理由"),
        atr=atr,
        prev_close=prev_close,
    )


def test_high_scoring_bundle_exceeds_threshold(tmp_path: Path) -> None:
    scorer = CompositeScorer(_write_strategy_yaml(tmp_path))
    reco = scorer.score(_bundle("3413"))
    assert reco.score >= 75
    assert reco.ticker == "3413"


def test_stop_and_target_use_atr(tmp_path: Path) -> None:
    scorer = CompositeScorer(_write_strategy_yaml(tmp_path))
    reco = scorer.score(_bundle("3413", atr=5.0, prev_close=200.0))
    assert reco.stop == pytest.approx(200 - 2 * 5.0)
    assert reco.target == pytest.approx(200 + 3 * 5.0)


def test_day_trader_risk_discounts_entry(tmp_path: Path) -> None:
    scorer = CompositeScorer(_write_strategy_yaml(tmp_path))
    normal = scorer.score(_bundle("3413", atr=5.0, prev_close=200.0))
    dt = scorer.score(_bundle("3413", atr=5.0, prev_close=200.0, day_trader=True))
    # 入手區間整段下修 0.5 × ATR = 2.5
    assert dt.entry_high == pytest.approx(normal.entry_high - 2.5)
    assert dt.entry_low == pytest.approx(normal.entry_low - 2.5)
    assert dt.flags["day_trader_risk"] is True


def test_leader_divergence_halves_position(tmp_path: Path) -> None:
    scorer = CompositeScorer(_write_strategy_yaml(tmp_path))
    normal = scorer.score(_bundle("3413"))
    div = scorer.score(_bundle("3413", leader_div=True))
    assert normal.max_position_pct == 20.0
    assert div.max_position_pct == 10.0


def test_sector_weak_deducts_score(tmp_path: Path) -> None:
    scorer = CompositeScorer(_write_strategy_yaml(tmp_path))
    normal = scorer.score(_bundle("3413"))
    weak = scorer.score(_bundle("3413", sector_weak=True))
    assert weak.score < normal.score
    # 扣 5 分
    assert normal.score - weak.score == pytest.approx(5.0, abs=0.2)


def test_news_sentiment_mapping(tmp_path: Path) -> None:
    """news.value = -1 → normalized 0；news.value = +1 → normalized 1。"""
    scorer = CompositeScorer(_write_strategy_yaml(tmp_path))
    bull = scorer.score(_bundle("3413", news=1.0))
    bear = scorer.score(_bundle("3413", news=-1.0))
    assert bull.score > bear.score
    # 差距 = news 權重 × (1 − 0) × 100 = 20 分
    assert bull.score - bear.score == pytest.approx(20.0, abs=0.2)


def test_rank_filters_and_sorts(tmp_path: Path) -> None:
    scorer = CompositeScorer(_write_strategy_yaml(tmp_path, min_score=60))
    bundles = [
        _bundle("A", chip=0.9, news=0.8),   # 高分
        _bundle("B", chip=0.3, sector=0.3, news=-0.5, tech=0.3, market=0.3),  # 低分
        _bundle("C", chip=0.6, news=0.4),   # 中等
    ]
    ranked = scorer.rank(bundles)
    assert len(ranked) <= 3
    assert ranked[0].ticker == "A"
    assert all(r.score >= 60 for r in ranked)
    # 確認降冪排序
    scores = [r.score for r in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_enforces_max_picks(tmp_path: Path) -> None:
    path = tmp_path / "strategy.yaml"
    # min_score 很低，讓全部過關，驗證 max_picks 截斷
    path.write_text(
        """factor_weights:
  chip_concentration: 0.25
  sector_momentum:    0.10
  supply_chain:       0.20
  news_sentiment:     0.20
  technical:          0.15
  market_regime:      0.10
recommendation:
  min_score: 0
  max_picks: 2
risk:
  atr_stop_multiplier: 2.0
  atr_target_multiplier: 3.0
  max_single_position_pct: 20.0
""",
        encoding="utf-8",
    )
    scorer = CompositeScorer(path)
    bundles = [_bundle(t) for t in ("A", "B", "C", "D")]
    assert len(scorer.rank(bundles)) == 2
