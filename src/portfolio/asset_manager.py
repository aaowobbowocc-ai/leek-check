"""
AssetManager: 讀 assets.json、估值、輸出資金配置上限。

隱私保護：若 assets.json.user_uuid 有值，env USER_UUID 必須相符才解遮罩。
在醫院等公開環境下，只要把 USER_UUID 清空，所有金額就會顯示為 ***。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field


PriceFetcher = Callable[[str], float]

MASK = "***"


# ─────────────────────────────────────────
# Pydantic schema（對應 assets.json）
# ─────────────────────────────────────────
class Holding(BaseModel):
    ticker: str = Field(min_length=1)
    shares: int = Field(gt=0)
    cost: float = Field(gt=0)


class Holdings(BaseModel):
    long_term: list[Holding] = Field(default_factory=list)
    short_term: list[Holding] = Field(default_factory=list)


class RiskBudget(BaseModel):
    max_per_trade_pct: float = Field(gt=0, le=100)
    max_single_position_pct: float = Field(gt=0, le=100)
    max_concurrent_positions: int = Field(gt=0)


class Assets(BaseModel):
    user_uuid: str = ""
    cash: float = Field(ge=0)
    holdings: Holdings = Field(default_factory=Holdings)
    risk_budget: RiskBudget


# ─────────────────────────────────────────
# 衍生值（不可變）
# ─────────────────────────────────────────
@dataclass(frozen=True)
class HoldingValuation:
    ticker: str
    shares: int
    cost: float
    price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pct: float


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash: float
    long_term: tuple[HoldingValuation, ...]
    short_term: tuple[HoldingValuation, ...]

    @property
    def long_term_value(self) -> float:
        return sum(h.market_value for h in self.long_term)

    @property
    def short_term_value(self) -> float:
        return sum(h.market_value for h in self.short_term)

    @property
    def net_worth(self) -> float:
        return self.cash + self.long_term_value + self.short_term_value

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(h.unrealized_pnl for h in (*self.long_term, *self.short_term))


@dataclass(frozen=True)
class AllocationLimits:
    net_worth: float
    per_trade_risk_budget: float     # 單筆最大可承受風險（台幣）
    single_position_cap: float       # 單檔部位上限（台幣）
    max_concurrent_positions: int


# ─────────────────────────────────────────
# AssetManager
# ─────────────────────────────────────────
class AssetManager:
    def __init__(
        self,
        assets_path: Path | str,
        price_fetcher: PriceFetcher,
        env_uuid: str | None = None,
    ) -> None:
        self.assets_path = Path(assets_path)
        self.price_fetcher = price_fetcher
        self._env_uuid = (
            env_uuid if env_uuid is not None else os.environ.get("USER_UUID", "")
        )
        self._assets = self._load()

    def _load(self) -> Assets:
        with self.assets_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return Assets.model_validate(raw)

    @property
    def authorized(self) -> bool:
        """若 assets.json.user_uuid 為空 → 開發模式，預設授權。
        有值時 env USER_UUID 必須完全相符才授權。"""
        required = self._assets.user_uuid
        if not required:
            return True
        return required == self._env_uuid

    def snapshot(self) -> PortfolioSnapshot:
        def value(h: Holding) -> HoldingValuation:
            price = float(self.price_fetcher(h.ticker))
            mv = h.shares * price
            pnl = (price - h.cost) * h.shares
            pct = (price - h.cost) / h.cost if h.cost else 0.0
            return HoldingValuation(
                ticker=h.ticker,
                shares=h.shares,
                cost=h.cost,
                price=price,
                market_value=mv,
                unrealized_pnl=pnl,
                unrealized_pct=pct,
            )

        return PortfolioSnapshot(
            cash=self._assets.cash,
            long_term=tuple(value(h) for h in self._assets.holdings.long_term),
            short_term=tuple(value(h) for h in self._assets.holdings.short_term),
        )

    def allocation_limits(self, snap: PortfolioSnapshot | None = None) -> AllocationLimits:
        snap = snap if snap is not None else self.snapshot()
        r = self._assets.risk_budget
        net = snap.net_worth
        return AllocationLimits(
            net_worth=net,
            per_trade_risk_budget=net * r.max_per_trade_pct / 100.0,
            single_position_cap=net * r.max_single_position_pct / 100.0,
            max_concurrent_positions=r.max_concurrent_positions,
        )

    # ─────────────────────────────────────
    # 遮罩格式化（金額經過此處才能印出）
    # ─────────────────────────────────────
    def format_amount(self, value: float) -> str:
        if not self.authorized:
            return MASK
        return f"{value:,.0f}"

    @staticmethod
    def format_pct(fraction: float) -> str:
        return f"{fraction * 100:+.2f}%"
