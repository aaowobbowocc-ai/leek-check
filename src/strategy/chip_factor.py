"""
籌碼集中度因子（權重 25%，計畫中最強的短線先行指標）。

資料來源：FinMindClient.get_institutional / get_broker_distribution

評分組成（加權合成 0–1）：
  - 投信連買天數（0.35）：連買 ≥ 5 日給滿分，線性遞減
  - 投信買超佔股本 %（0.35）：> 0.5% 給滿分
  - 前 15 大分點淨買超佔成交量 %（0.30）：> 2% 給滿分（大額買盤挾持走勢）

附帶風控訊號（flags）：
  - day_trader_risk：前 15 大買超分點中，隔日沖黑名單佔比 > 40% → True
    composite_scorer 會據此把「入手區間」下修為 前收 − 0.5 × ATR（見計畫 §L2）
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from src.strategy.factor_base import FactorScore, clamp01


def _load_day_trader_config(path: Path | str) -> tuple[set[str], dict]:
    with Path(path).open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    branches = cfg.get("known_day_trader_branches", []) or []
    ids: set[str] = set()
    for b in branches:
        bid = b.get("broker_id")
        if bid:
            ids.add(str(bid))
    thresholds = cfg.get("thresholds", {}) or {}
    return ids, thresholds


class ChipFactor:
    def __init__(
        self,
        day_trader_config_path: Path | str,
        consecutive_days_full: int = 5,
        shares_pct_full: float = 0.5,
        broker_volume_pct_full: float = 2.0,
        weights: tuple[float, float, float] = (0.35, 0.35, 0.30),
    ) -> None:
        self._day_trader_ids, self._dt_thresholds = _load_day_trader_config(
            day_trader_config_path
        )
        self._consecutive_full = consecutive_days_full
        self._shares_pct_full = shares_pct_full
        self._broker_pct_full = broker_volume_pct_full
        self._w_consec, self._w_pct, self._w_broker = weights

    def score(
        self,
        ticker: str,
        institutional: pd.DataFrame,
        broker: pd.DataFrame,
        shares_outstanding: int,
        recent_volume: int,
    ) -> FactorScore:
        """
        institutional: 欄位 date | name | buy | sell | net_buy（多日，需已按 date 排序）
        broker:        欄位 date | broker_id | ... | net_buy（單一交易日）
        shares_outstanding: 股本（股數）
        recent_volume:      T-1 日成交量（股）
        """
        consec = self._investment_trust_streak(institutional)
        pct_of_shares = self._investment_trust_shares_pct(
            institutional, shares_outstanding
        )
        broker_pct, day_trader_ratio = self._broker_concentration(broker, recent_volume)

        s_consec = clamp01(consec / self._consecutive_full)
        s_pct = clamp01(pct_of_shares / self._shares_pct_full)
        s_broker = clamp01(broker_pct / self._broker_pct_full)

        value = (
            self._w_consec * s_consec
            + self._w_pct * s_pct
            + self._w_broker * s_broker
        )

        threshold = float(self._dt_thresholds.get("ratio_threshold", 0.40))
        day_trader_risk = day_trader_ratio > threshold

        return FactorScore(
            value=clamp01(value),
            breakdown={
                "trust_streak_days": float(consec),
                "trust_net_buy_pct_of_shares": round(pct_of_shares, 3),
                "top15_broker_net_pct_of_volume": round(broker_pct, 3),
                "day_trader_ratio": round(day_trader_ratio, 3),
            },
            flags={"day_trader_risk": day_trader_risk},
            reason=self._build_reason(consec, pct_of_shares, day_trader_risk),
        )

    # ─────────────────────────────────────
    # 子計算
    # ─────────────────────────────────────
    @staticmethod
    def _investment_trust_streak(df: pd.DataFrame) -> int:
        """投信（name == '投信'）由近至遠的連續買超天數。"""
        if df.empty or "name" not in df:
            return 0
        trust = df[df["name"] == "投信"].sort_values("date", ascending=False)
        streak = 0
        for net in trust["net_buy"]:
            if net > 0:
                streak += 1
            else:
                break
        return streak

    @staticmethod
    def _investment_trust_shares_pct(df: pd.DataFrame, shares_outstanding: int) -> float:
        """投信累計買超佔股本 %（用全 df，不去篩 streak 內）。"""
        if df.empty or shares_outstanding <= 0 or "name" not in df:
            return 0.0
        trust_total = df.loc[df["name"] == "投信", "net_buy"].sum()
        return float(trust_total) / shares_outstanding * 100.0

    def _broker_concentration(
        self, broker: pd.DataFrame, recent_volume: int
    ) -> tuple[float, float]:
        """
        回傳 (前 15 大分點淨買超佔當日成交量 %、隔日沖分點佔前 15 大比例)。
        """
        if broker.empty or recent_volume <= 0:
            return 0.0, 0.0
        top_n = int(self._dt_thresholds.get("top_n_brokers", 15))
        top = broker.nlargest(top_n, "net_buy")
        net_sum = float(top["net_buy"].sum())
        pct = net_sum / recent_volume * 100.0

        if "broker_id" in top and len(top) > 0:
            ids = top["broker_id"].astype(str)
            hit = ids.isin(self._day_trader_ids).sum()
            dt_ratio = float(hit) / len(top)
        else:
            dt_ratio = 0.0

        return pct, dt_ratio

    @staticmethod
    def _build_reason(consec: int, pct_shares: float, day_trader: bool) -> str:
        parts = []
        if consec >= 3:
            parts.append(f"投信連買 {consec} 日")
        if pct_shares >= 0.3:
            parts.append(f"吸 {pct_shares:.2f}% 股本")
        if day_trader:
            parts.append("⚠️ 隔日沖主導")
        return "、".join(parts) or "籌碼平淡"
