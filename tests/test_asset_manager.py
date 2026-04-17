from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.portfolio.asset_manager import MASK, AssetManager


def _write_assets(path: Path, data: dict) -> Path:
    p = path / "assets.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _sample(user_uuid: str = "") -> dict:
    return {
        "user_uuid": user_uuid,
        "cash": 520_000,
        "holdings": {
            "long_term": [
                {"ticker": "0050", "shares": 150, "cost": 135.2},
                {"ticker": "00878", "shares": 8000, "cost": 22.5},
            ],
            "short_term": [],
        },
        "risk_budget": {
            "max_per_trade_pct": 2.0,
            "max_single_position_pct": 20.0,
            "max_concurrent_positions": 3,
        },
    }


PRICES = {"0050": 150.0, "00878": 25.0, "3413": 190.0}


def fake_price(ticker: str) -> float:
    return PRICES[ticker]


# ─────────────────────────────────────────
# Valuation
# ─────────────────────────────────────────
def test_snapshot_computes_market_value_and_pnl(tmp_path: Path) -> None:
    p = _write_assets(tmp_path, _sample())
    am = AssetManager(p, price_fetcher=fake_price)
    snap = am.snapshot()

    assert snap.cash == 520_000
    assert len(snap.long_term) == 2

    h0050 = snap.long_term[0]
    assert h0050.ticker == "0050"
    assert h0050.market_value == pytest.approx(150 * 150.0)
    assert h0050.unrealized_pnl == pytest.approx((150.0 - 135.2) * 150)
    assert h0050.unrealized_pct == pytest.approx((150.0 - 135.2) / 135.2)

    expected_long = 150 * 150.0 + 8000 * 25.0
    assert snap.long_term_value == pytest.approx(expected_long)
    assert snap.net_worth == pytest.approx(520_000 + expected_long)


def test_allocation_limits_derived_from_net_worth(tmp_path: Path) -> None:
    p = _write_assets(tmp_path, _sample())
    am = AssetManager(p, price_fetcher=fake_price)
    limits = am.allocation_limits()

    expected_net = 520_000 + 150 * 150.0 + 8000 * 25.0
    assert limits.net_worth == pytest.approx(expected_net)
    assert limits.per_trade_risk_budget == pytest.approx(expected_net * 0.02)
    assert limits.single_position_cap == pytest.approx(expected_net * 0.20)
    assert limits.max_concurrent_positions == 3


# ─────────────────────────────────────────
# Privacy guard (USER_UUID)
# ─────────────────────────────────────────
def test_authorized_when_uuid_empty(tmp_path: Path) -> None:
    """無 user_uuid → 開發模式，預設授權。"""
    p = _write_assets(tmp_path, _sample(user_uuid=""))
    am = AssetManager(p, price_fetcher=fake_price, env_uuid="anything")
    assert am.authorized is True
    assert am.format_amount(123_456) == "123,456"


def test_authorized_when_uuid_matches(tmp_path: Path) -> None:
    p = _write_assets(tmp_path, _sample(user_uuid="secret-123"))
    am = AssetManager(p, price_fetcher=fake_price, env_uuid="secret-123")
    assert am.authorized is True
    assert am.format_amount(900_000) == "900,000"


def test_unauthorized_when_uuid_missing_env(tmp_path: Path) -> None:
    """env USER_UUID 空白但 assets.json 有值 → 遮罩。"""
    p = _write_assets(tmp_path, _sample(user_uuid="secret-123"))
    am = AssetManager(p, price_fetcher=fake_price, env_uuid="")
    assert am.authorized is False
    assert am.format_amount(900_000) == MASK


def test_unauthorized_when_uuid_mismatch(tmp_path: Path) -> None:
    p = _write_assets(tmp_path, _sample(user_uuid="secret-123"))
    am = AssetManager(p, price_fetcher=fake_price, env_uuid="wrong")
    assert am.authorized is False
    assert am.format_amount(900_000) == MASK


def test_format_pct_never_masked(tmp_path: Path) -> None:
    """漲跌幅不含財產資訊，即便未授權也應顯示（只隱藏絕對金額）。"""
    p = _write_assets(tmp_path, _sample(user_uuid="secret-123"))
    am = AssetManager(p, price_fetcher=fake_price, env_uuid="wrong")
    assert am.authorized is False
    assert am.format_pct(0.0543) == "+5.43%"


# ─────────────────────────────────────────
# Schema validation
# ─────────────────────────────────────────
def test_rejects_negative_cash(tmp_path: Path) -> None:
    bad = _sample()
    bad["cash"] = -1
    p = _write_assets(tmp_path, bad)
    with pytest.raises(ValidationError):
        AssetManager(p, price_fetcher=fake_price)


def test_rejects_non_positive_shares(tmp_path: Path) -> None:
    bad = _sample()
    bad["holdings"]["long_term"][0]["shares"] = 0
    p = _write_assets(tmp_path, bad)
    with pytest.raises(ValidationError):
        AssetManager(p, price_fetcher=fake_price)


def test_rejects_out_of_range_pct(tmp_path: Path) -> None:
    bad = _sample()
    bad["risk_budget"]["max_per_trade_pct"] = 150.0
    p = _write_assets(tmp_path, bad)
    with pytest.raises(ValidationError):
        AssetManager(p, price_fetcher=fake_price)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        AssetManager(tmp_path / "nonexistent.json", price_fetcher=fake_price)
