"""
Phase 10 測試 — paper_tracker 快照寫入、reconcile 對帳、ledger 統計。

驗證三個核心路徑：
  1. reco 隔日觸 target → closed_target + gross_return > 0
  2. reco 隔日觸 stop  → closed_stop + gross_return < 0
  3. reco 連續 5 日都沒進入入手區間 → expired
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.portfolio.paper_tracker import (
    PaperTrade,
    load_daily,
    reconcile,
    record_daily,
    summarize,
)


# ─────────────────────────────────────────
# Test fixtures
# ─────────────────────────────────────────
@dataclass(frozen=True)
class _FakeReco:
    """Duck-type Recommendation 的最小欄位。"""
    ticker: str
    score: float
    entry_low: float
    entry_high: float
    target: float
    stop: float
    atr: float


def _mk_bar(d: date, o: float, h: float, l: float, c: float) -> dict:
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": 1_000_000}


def _make_fetcher(bars_by_ticker: dict[str, list[dict]]):
    """回傳一個 ohlcv_fetcher，從 bars 中過濾 [start, end]。"""
    def _fetch(ticker: str, start: date, end: date) -> pd.DataFrame:
        rows = [b for b in bars_by_ticker.get(ticker, []) if start <= b["date"] <= end]
        return pd.DataFrame(rows)
    return _fetch


# ─────────────────────────────────────────
# record_daily / load_daily
# ─────────────────────────────────────────
def test_record_daily_writes_json(tmp_path: Path) -> None:
    recos = [_FakeReco("3413", 80.0, 320.0, 330.0, 360.0, 310.0, 5.0)]
    p = record_daily(tmp_path, date(2026, 4, 24), recos)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["reco_date"] == "2026-04-24"
    assert len(data["recommendations"]) == 1
    assert data["recommendations"][0]["ticker"] == "3413"
    assert data["recommendations"][0]["target"] == 360.0


def test_load_daily_sorts_by_date(tmp_path: Path) -> None:
    record_daily(tmp_path, date(2026, 4, 25), [_FakeReco("A", 80, 100, 110, 120, 90, 2)])
    record_daily(tmp_path, date(2026, 4, 23), [_FakeReco("B", 80, 100, 110, 120, 90, 2)])
    snaps = load_daily(tmp_path)
    assert [s["reco_date"] for s in snaps] == ["2026-04-23", "2026-04-25"]


def test_load_daily_nonexistent_returns_empty(tmp_path: Path) -> None:
    assert load_daily(tmp_path / "nope") == []


# ─────────────────────────────────────────
# reconcile — 核心情境
# ─────────────────────────────────────────
def test_reconcile_target_hit(tmp_path: Path) -> None:
    reco_date = date(2026, 4, 20)
    recos = [_FakeReco("3413", 82.0, 320.0, 330.0, 360.0, 310.0, 5.0)]
    record_daily(tmp_path, reco_date, recos)

    bars = {
        "3413": [
            _mk_bar(date(2026, 4, 21), 325, 332, 322, 328),  # 進場日：[322,332] 與 [320,330] 有重疊
            _mk_bar(date(2026, 4, 22), 330, 355, 328, 352),
            _mk_bar(date(2026, 4, 23), 352, 365, 350, 362),  # target 360 觸發
        ]
    }
    trades = reconcile(tmp_path, _make_fetcher(bars), as_of=date(2026, 4, 24))
    assert len(trades) == 1
    t = trades[0]
    assert t.status == "closed_target"
    assert t.entry_date == "2026-04-21"
    assert t.exit_date == "2026-04-23"
    assert t.exit_price == 360.0
    assert t.gross_return_pct > 0


def test_reconcile_stop_hit(tmp_path: Path) -> None:
    reco_date = date(2026, 4, 20)
    recos = [_FakeReco("X", 75.0, 100.0, 105.0, 120.0, 95.0, 3.0)]
    record_daily(tmp_path, reco_date, recos)

    bars = {
        "X": [
            _mk_bar(date(2026, 4, 21), 102, 106, 100, 103),  # 進場日
            _mk_bar(date(2026, 4, 22), 103, 104, 93, 94),    # stop 95 觸發
        ]
    }
    trades = reconcile(tmp_path, _make_fetcher(bars), as_of=date(2026, 4, 24))
    assert len(trades) == 1
    t = trades[0]
    assert t.status == "closed_stop"
    assert t.exit_price == 95.0
    assert t.gross_return_pct < 0


def test_reconcile_stop_preferred_over_target_same_day(tmp_path: Path) -> None:
    """同日 low 穿 stop + high 穿 target → 保守回 stop（與 backtest 一致）。"""
    reco_date = date(2026, 4, 20)
    recos = [_FakeReco("Y", 75.0, 100.0, 105.0, 120.0, 95.0, 3.0)]
    record_daily(tmp_path, reco_date, recos)
    bars = {
        "Y": [
            _mk_bar(date(2026, 4, 21), 102, 106, 101, 103),   # 進場
            _mk_bar(date(2026, 4, 22), 103, 125, 90, 115),    # low<=95 stop + high>=120 target
        ]
    }
    trades = reconcile(tmp_path, _make_fetcher(bars), as_of=date(2026, 4, 24))
    assert trades[0].status == "closed_stop"


def test_reconcile_expired_when_entry_never_hits(tmp_path: Path) -> None:
    """5 天都沒進入入手區間 → expired。"""
    reco_date = date(2026, 4, 20)
    recos = [_FakeReco("Z", 75.0, 200.0, 210.0, 230.0, 195.0, 3.0)]
    record_daily(tmp_path, reco_date, recos)
    bars = {
        "Z": [
            _mk_bar(date(2026, 4, 21), 150, 160, 145, 155),
            _mk_bar(date(2026, 4, 22), 155, 165, 150, 160),
            _mk_bar(date(2026, 4, 23), 160, 170, 155, 165),
            _mk_bar(date(2026, 4, 24), 165, 175, 160, 170),
            _mk_bar(date(2026, 4, 25), 170, 180, 165, 175),
        ]
    }
    trades = reconcile(tmp_path, _make_fetcher(bars), as_of=date(2026, 4, 26))
    assert trades[0].status == "expired"
    assert trades[0].entry_date is None


def test_reconcile_open_when_no_exit_yet(tmp_path: Path) -> None:
    """進場了但 stop/target 都沒觸發，未達 timeout → open。"""
    reco_date = date(2026, 4, 20)
    recos = [_FakeReco("O", 80.0, 100.0, 110.0, 150.0, 90.0, 3.0)]
    record_daily(tmp_path, reco_date, recos)
    bars = {
        "O": [
            _mk_bar(date(2026, 4, 21), 105, 108, 102, 107),  # 進場
            _mk_bar(date(2026, 4, 22), 107, 112, 104, 110),
            _mk_bar(date(2026, 4, 23), 110, 115, 108, 113),
        ]
    }
    trades = reconcile(tmp_path, _make_fetcher(bars), as_of=date(2026, 4, 24))
    assert trades[0].status == "open"
    assert trades[0].entry_date == "2026-04-21"
    assert trades[0].exit_date is None


def test_reconcile_skips_future_reco_dates(tmp_path: Path) -> None:
    """reco_date >= as_of 的快照不應出現在結果。"""
    record_daily(tmp_path, date(2026, 4, 30), [_FakeReco("F", 80, 100, 110, 120, 90, 2)])
    trades = reconcile(tmp_path, _make_fetcher({}), as_of=date(2026, 4, 24))
    assert trades == []


def test_reconcile_timeout(tmp_path: Path) -> None:
    reco_date = date(2026, 4, 1)
    recos = [_FakeReco("T", 75.0, 100.0, 105.0, 200.0, 90.0, 5.0)]
    record_daily(tmp_path, reco_date, recos)
    # 22 個交易日都在入手區間上方但不碰 target/stop → 應在第 20 天 timeout
    bars = {
        "T": [
            _mk_bar(date(2026, 4, 1) + timedelta(days=i + 1), 105, 110, 103, 108)
            for i in range(25)
        ]
    }
    trades = reconcile(
        tmp_path, _make_fetcher(bars),
        as_of=date(2026, 5, 30), max_hold_days=20,
    )
    assert trades[0].status == "closed_timeout"


# ─────────────────────────────────────────
# summarize
# ─────────────────────────────────────────
def test_summarize_basic_stats() -> None:
    trades = [
        PaperTrade("A", "2026-04-01", "closed_target", "2026-04-02", 100, "2026-04-05", 120, 20.0, 80),
        PaperTrade("B", "2026-04-02", "closed_target", "2026-04-03", 50, "2026-04-04", 55, 10.0, 80),
        PaperTrade("C", "2026-04-03", "closed_stop", "2026-04-04", 100, "2026-04-05", 90, -10.0, 80),
        PaperTrade("D", "2026-04-04", "open", "2026-04-05", 100, None, None, 5.0, 80),
        PaperTrade("E", "2026-04-05", "pending", None, None, None, None, None, 80),
    ]
    s = summarize(trades)
    assert s.total == 5
    assert s.closed == 3
    assert s.open == 1
    assert s.pending == 1
    assert s.wins == 2
    assert s.losses == 1
    assert s.win_rate == pytest.approx(2 / 3, abs=1e-3)
    assert s.avg_win_pct == pytest.approx(15.0)
    assert s.avg_loss_pct == pytest.approx(10.0)
    assert s.pl_ratio == pytest.approx(1.5)
    # E(x) = 0.667 × 15 − 0.333 × 10 ≈ 6.67
    assert s.expectancy_pct == pytest.approx(6.67, abs=0.1)


def test_summarize_empty() -> None:
    s = summarize([])
    assert s.total == 0
    assert s.win_rate == 0.0
