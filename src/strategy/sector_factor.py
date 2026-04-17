"""
族群動能因子（權重 10%）— 避免「單檔強、族群弱」的出貨陷阱。

資料來源：
  - config/sector_map.yaml（產業分類）
  - 每檔個股的 chip_factor 分數（同一交易日）
  - 每檔個股當日 K 線（判斷紅 K 比例）

評分邏輯：
  1. 找出本標的所屬產業
  2. 計算同產業「chip 因子觸發數」（chip.value > sector_cfg.chip_threshold 的檔數）
  3. 計算同產業「紅 K 比例」（收 > 開 的檔數比例）

輸出：
  - value：0–1，triggers >= min_triggers 給滿分，遞增
  - flags.sector_weak：紅 K 比例 < red_candle_ratio_min → 扣分訊號
  - breakdown.sector_triggers / breakdown.red_candle_ratio
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from src.strategy.factor_base import FactorScore, clamp01


def _load_sector_map(path: Path | str) -> tuple[dict[str, str], dict]:
    with Path(path).open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    momentum_cfg = cfg.pop("sector_momentum", {}) or {}
    ticker_to_sector: dict[str, str] = {}
    sector_meta: dict[str, dict] = {}
    for sector_key, sector_val in cfg.items():
        if not isinstance(sector_val, dict):
            continue
        tickers = sector_val.get("tickers") or []
        sector_meta[sector_key] = {
            "name": sector_val.get("name", sector_key),
            "tickers": [str(t) for t in tickers],
        }
        for t in tickers:
            ticker_to_sector[str(t)] = sector_key
    return ticker_to_sector, {"meta": sector_meta, "momentum": momentum_cfg}


class SectorFactor:
    def __init__(self, sector_map_path: Path | str) -> None:
        self._ticker_to_sector, cfg = _load_sector_map(sector_map_path)
        self._sector_meta: dict[str, dict] = cfg["meta"]
        m = cfg["momentum"]
        self._min_triggers = int(m.get("min_triggers", 3))
        self._chip_threshold = float(m.get("chip_threshold", 0.6))
        self._bonus_points = float(m.get("bonus_points", 10))   # 僅供 composite 參考
        self._weak_penalty = float(m.get("weak_sector_penalty", 5))
        self._red_ratio_min = float(m.get("red_candle_ratio_min", 0.3))

    def score(
        self,
        ticker: str,
        peer_chip_scores: dict[str, float],
        peer_today_candles: dict[str, tuple[float, float]],
    ) -> FactorScore:
        """
        peer_chip_scores: {peer_ticker: chip_factor.value}（同族群，含自己也可）
        peer_today_candles: {peer_ticker: (open, close)}（同族群當日 K 線）
        """
        sector = self._ticker_to_sector.get(str(ticker))
        if sector is None:
            return FactorScore(
                value=0.0, breakdown={"sector": "unknown"}, reason="未分類產業"
            )

        sector_tickers = set(self._sector_meta[sector]["tickers"])

        # 同族群中 chip 觸發數
        triggered = [
            t
            for t, v in peer_chip_scores.items()
            if str(t) in sector_tickers and v > self._chip_threshold
        ]
        triggers = len(triggered)

        # 同族群紅 K 比例
        candles_in_sector = [
            (o, c) for t, (o, c) in peer_today_candles.items() if str(t) in sector_tickers
        ]
        if candles_in_sector:
            red = sum(1 for (o, c) in candles_in_sector if c > o)
            red_ratio = red / len(candles_in_sector)
        else:
            red_ratio = 0.0

        value = clamp01(triggers / self._min_triggers)
        sector_weak = red_ratio < self._red_ratio_min and len(candles_in_sector) > 0

        return FactorScore(
            value=value,
            breakdown={
                "sector": sector,
                "sector_name": self._sector_meta[sector]["name"],
                "sector_triggers": float(triggers),
                "red_candle_ratio": round(red_ratio, 2),
            },
            flags={"sector_weak": sector_weak},
            reason=self._build_reason(
                self._sector_meta[sector]["name"], triggers, sector_weak
            ),
        )

    def sector_of(self, ticker: str) -> str | None:
        return self._ticker_to_sector.get(str(ticker))

    def peers_of(self, ticker: str) -> list[str]:
        sector = self.sector_of(ticker)
        if sector is None:
            return []
        return list(self._sector_meta[sector]["tickers"])

    @staticmethod
    def _build_reason(sector_name: str, triggers: int, weak: bool) -> str:
        parts = [f"{sector_name} 同 {triggers} 檔觸發"]
        if weak:
            parts.append("⚠️ 族群偏弱")
        return "、".join(parts)
