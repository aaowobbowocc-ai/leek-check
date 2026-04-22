"""
族群動能因子（權重 10%）— 避免「單檔強、族群弱」的出貨陷阱。

Phase 5 初版：
  - 同族群內 chip 觸發數 + 紅 K 比例

Phase 11 新增（族群相對強弱 RS）：
  - 每日對每個 sector 計算「20 日等權報酬 − TAIEX 20 日報酬」
  - 取 Top N（強制跨不同 cluster）→ leader：標的在此族群 sector.value + leader_bonus
  - 取 Bottom N → laggard：標的在此族群 sector.value × (1 - laggard_penalty)
  - 解決 2023 漏接 AI/設備族群輪動的問題

評分邏輯：
  1. 找出本標的所屬產業
  2. 計算同產業「chip 因子觸發數」
  3. 計算同產業「紅 K 比例」
  4. 若所屬產業為 RS leader → +leader_bonus；為 laggard → ×(1-laggard_penalty)
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
    rs_cfg = cfg.pop("relative_strength", {}) or {}
    ticker_to_sector: dict[str, str] = {}
    sector_meta: dict[str, dict] = {}
    for sector_key, sector_val in cfg.items():
        if not isinstance(sector_val, dict):
            continue
        tickers = sector_val.get("tickers") or []
        sector_meta[sector_key] = {
            "name": sector_val.get("name", sector_key),
            "cluster": sector_val.get("cluster", sector_key),
            "tickers": [str(t) for t in tickers],
        }
        for t in tickers:
            ticker_to_sector[str(t)] = sector_key
    return ticker_to_sector, {"meta": sector_meta, "momentum": momentum_cfg, "rs": rs_cfg}


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
        rs = cfg["rs"]
        self._rs_lookback = int(rs.get("lookback_days", 20))
        self._rs_top_n = int(rs.get("top_n", 2))
        self._rs_bottom_n = int(rs.get("bottom_n", 2))
        self._rs_distinct = bool(rs.get("enforce_distinct_clusters", True))
        self._leader_bonus = float(rs.get("leader_bonus", 0.15))
        self._laggard_penalty = float(rs.get("laggard_penalty", 0.20))

    # ─────────────────────────────────────
    # 對外 API
    # ─────────────────────────────────────
    def score(
        self,
        ticker: str,
        peer_chip_scores: dict[str, float],
        peer_today_candles: dict[str, tuple[float, float]],
        leader_sectors: set[str] | None = None,
        laggard_sectors: set[str] | None = None,
        sector_rs: dict[str, float] | None = None,
    ) -> FactorScore:
        """
        peer_chip_scores: {peer_ticker: chip_factor.value}（同族群，含自己也可）
        peer_today_candles: {peer_ticker: (open, close)}（同族群當日 K 線）
        leader_sectors / laggard_sectors: 由 top_sectors() 產生的 sector key 集合
        sector_rs: {sector_key: rs_pct} — 供晨報顯示本族群 RS vs TAIEX
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

        # Phase 11：RS leader/laggard 只打 flag，最終 bonus/penalty 由 composite_scorer 對
        # 整個 100 分制 composite score 非對稱套用（penalty 比 bonus 大 — 避免過度促進
        # 弱股卻又必要過濾掉弱族群）
        leader_sectors = leader_sectors or set()
        laggard_sectors = laggard_sectors or set()
        is_leader = sector in leader_sectors
        is_laggard = sector in laggard_sectors

        rs_pct = float((sector_rs or {}).get(sector, 0.0))

        return FactorScore(
            value=value,
            breakdown={
                "sector": sector,
                "sector_name": self._sector_meta[sector]["name"],
                "sector_triggers": float(triggers),
                "red_candle_ratio": round(red_ratio, 2),
                "sector_rs_pct": round(rs_pct, 3),
            },
            flags={
                "sector_weak": sector_weak,
                "sector_leading": is_leader,
                "sector_lagging": is_laggard,
            },
            reason=self._build_reason(
                self._sector_meta[sector]["name"],
                triggers,
                sector_weak,
                is_leader,
                is_laggard,
                rs_pct,
            ),
        )

    def sector_of(self, ticker: str) -> str | None:
        return self._ticker_to_sector.get(str(ticker))

    def peers_of(self, ticker: str) -> list[str]:
        sector = self.sector_of(ticker)
        if sector is None:
            return []
        return list(self._sector_meta[sector]["tickers"])

    def cluster_of(self, sector_key: str) -> str:
        meta = self._sector_meta.get(sector_key)
        return meta["cluster"] if meta else sector_key

    @property
    def all_sectors(self) -> list[str]:
        return list(self._sector_meta.keys())

    @property
    def rs_lookback(self) -> int:
        return self._rs_lookback

    # ─────────────────────────────────────
    # Phase 11：RS 計算
    # ─────────────────────────────────────
    def compute_sector_rs(
        self,
        sector_closes: dict[str, pd.DataFrame],
        taiex_close: pd.DataFrame,
    ) -> dict[str, float]:
        """
        sector_closes: {ticker: ohlcv_df}（僅讀 < cutoff 的歷史，呼叫端負責過濾）
        taiex_close:   加權指數歷史 OHLCV（同樣僅 < cutoff）

        回傳 {sector_key: rs_pct}，其中 rs_pct = 族群等權 20 日報酬 − TAIEX 20 日報酬（百分比）
        """
        lookback = self._rs_lookback
        taiex_ret = _window_return_pct(taiex_close, lookback)
        if taiex_ret is None:
            return {}

        rs_map: dict[str, float] = {}
        for sector_key, meta in self._sector_meta.items():
            returns: list[float] = []
            for tk in meta["tickers"]:
                df = sector_closes.get(tk)
                r = _window_return_pct(df, lookback)
                if r is not None:
                    returns.append(r)
            if not returns:
                continue
            sector_ret = sum(returns) / len(returns)
            rs_map[sector_key] = sector_ret - taiex_ret
        return rs_map

    def top_sectors(
        self,
        rs_map: dict[str, float],
        n: int | None = None,
        distinct_clusters: bool | None = None,
    ) -> set[str]:
        """挑 Top N RS 族群，optionally 強制跨 cluster（跳過已選 cluster 的族群）。"""
        n = self._rs_top_n if n is None else n
        distinct = self._rs_distinct if distinct_clusters is None else distinct_clusters
        if not rs_map or n <= 0:
            return set()
        ranked = sorted(rs_map.items(), key=lambda kv: kv[1], reverse=True)
        chosen: list[str] = []
        used_clusters: set[str] = set()
        for sector_key, _ in ranked:
            if distinct:
                cluster = self.cluster_of(sector_key)
                if cluster in used_clusters:
                    continue
                used_clusters.add(cluster)
            chosen.append(sector_key)
            if len(chosen) >= n:
                break
        return set(chosen)

    def bottom_sectors(
        self,
        rs_map: dict[str, float],
        n: int | None = None,
    ) -> set[str]:
        """挑 Bottom N RS 族群（laggard，不強制 distinct cluster — 整個集群一起弱也算）。"""
        n = self._rs_bottom_n if n is None else n
        if not rs_map or n <= 0:
            return set()
        ranked = sorted(rs_map.items(), key=lambda kv: kv[1])
        return {sector_key for sector_key, _ in ranked[:n]}

    # ─────────────────────────────────────
    # 內部
    # ─────────────────────────────────────
    @staticmethod
    def _build_reason(
        sector_name: str,
        triggers: int,
        weak: bool,
        leading: bool,
        lagging: bool,
        rs_pct: float,
    ) -> str:
        parts = [f"{sector_name} 同 {triggers} 檔觸發"]
        if leading:
            parts.append(f"族群 RS 領先 TAIEX {rs_pct:+.1f}%")
        elif lagging:
            parts.append(f"⚠️ 族群 RS 落後 TAIEX {rs_pct:+.1f}%")
        if weak:
            parts.append("⚠️ 族群偏弱")
        return "、".join(parts)


def _window_return_pct(df: pd.DataFrame, lookback: int) -> float | None:
    """取最後 `lookback` 日的報酬率（%）。資料不足或欄位缺失 → None。"""
    if df is None or df.empty or "close" not in df:
        return None
    sorted_df = df.sort_values("date")
    if len(sorted_df) <= lookback:
        return None
    closes = sorted_df["close"].astype(float).tolist()
    start = closes[-1 - lookback]
    end = closes[-1]
    if start <= 0:
        return None
    return (end - start) / start * 100.0
