"""
台股交易成本模型 — 計畫 §8.3。

單次往返成本 ≈ 0.7%（手續費 0.1425% × 2 + 證交稅 0.3% + 滑價 0.1%）
→ 若目標價 < 進場價 × 1.007，扣完成本是虧錢。

tax_rate_discount 允許：
  - 當沖降稅：0.5（證交稅減半 → 0.15%）
  - 券商手續費折扣：0.3–0.6（常見 65 折 ≈ 0.000926）
  - 完全現股交易：1.0（預設）

`simulate_fill` 回傳「扣除成本後的實際淨收益」，供回測引擎直接寫進交易簿。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CostConfig:
    buy_fee_rate: float = 0.001425
    sell_fee_rate: float = 0.001425
    tax_rate: float = 0.003
    slippage_rate: float = 0.001
    tax_rate_discount: float = 1.0      # 1.0 = 無折扣；0.5 = 當沖降稅；<1 為券商手續費折扣套在兩邊

    @classmethod
    def from_yaml(cls, path: Path | str) -> "CostConfig":
        with Path(path).open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        c = cfg.get("costs", {}) or {}
        return cls(
            buy_fee_rate=float(c.get("buy_fee_rate", 0.001425)),
            sell_fee_rate=float(c.get("sell_fee_rate", 0.001425)),
            tax_rate=float(c.get("tax_rate", 0.003)),
            slippage_rate=float(c.get("slippage_rate", 0.001)),
            tax_rate_discount=float(c.get("tax_rate_discount", 1.0)),
        )

    def total_cost_ratio(self) -> float:
        """往返總成本比例（用於「目標價是否能覆蓋成本」的快速判斷）。"""
        return (
            self.buy_fee_rate * self.tax_rate_discount
            + self.sell_fee_rate * self.tax_rate_discount
            + self.tax_rate * self.tax_rate_discount
            + 2 * self.slippage_rate
        )


@dataclass(frozen=True)
class TradeResult:
    entry_price_paid: float          # 含滑價的實際進場價
    exit_price_received: float       # 含滑價的實際出場價
    gross_return_pct: float          # 未扣成本的毛報酬
    net_return_pct: float            # 扣完成本的淨報酬
    total_cost_pct: float            # 實際成本佔進場市值比
    pnl: float                       # 淨損益金額（以進場市值為基礎）


def simulate_fill(
    config: CostConfig,
    entry_price: float,
    exit_price: float,
    shares: int,
) -> TradeResult:
    """
    用保守滑價模擬成交：
      - 買進實付 = 掛價 × (1 + slippage)
      - 賣出實收 = 掛價 × (1 − slippage)
      - 手續費兩邊都扣 × tax_rate_discount
      - 證交稅只有賣出扣 × tax_rate_discount
    """
    if shares <= 0 or entry_price <= 0 or exit_price <= 0:
        return TradeResult(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    buy_price = entry_price * (1 + config.slippage_rate)
    sell_price = exit_price * (1 - config.slippage_rate)

    buy_cost = buy_price * shares
    sell_gross = sell_price * shares

    buy_fee = buy_cost * config.buy_fee_rate * config.tax_rate_discount
    sell_fee = sell_gross * config.sell_fee_rate * config.tax_rate_discount
    tax = sell_gross * config.tax_rate * config.tax_rate_discount

    net_pnl = sell_gross - buy_cost - buy_fee - sell_fee - tax
    gross_pnl = sell_gross - buy_cost

    net_return_pct = net_pnl / buy_cost * 100.0
    gross_return_pct = gross_pnl / buy_cost * 100.0
    total_cost_pct = (buy_fee + sell_fee + tax) / buy_cost * 100.0 + 2 * config.slippage_rate * 100.0

    return TradeResult(
        entry_price_paid=round(buy_price, 4),
        exit_price_received=round(sell_price, 4),
        gross_return_pct=round(gross_return_pct, 4),
        net_return_pct=round(net_return_pct, 4),
        total_cost_pct=round(total_cost_pct, 4),
        pnl=round(net_pnl, 2),
    )
