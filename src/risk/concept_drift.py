"""
Concept Drift Detector（計畫 §3.5）— 預測 vs 實際報酬偏離監控。

機制：
  - 每筆已平倉交易，記一組 (predicted_return, actual_return)
  - 維護滾動 window（預設 5 筆）
  - sum(|pred − actual|) 超過 alert_threshold（預設 0.20 = 20%）→ drift alert
  - 連續 force_paper_trading_after 筆（預設 10）仍 drift → 強制 paper trading

持久化：將紀錄落地到 data/state/drift_log.parquet，晨報每日開機時讀。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import yaml


@dataclass(frozen=True)
class DriftVerdict:
    alert: bool
    force_paper: bool
    window_divergence: float
    streak_alerts: int
    reason: str


class ConceptDriftDetector:
    def __init__(
        self,
        strategy_yaml_path: Path | str,
        log_path: Path | str,
    ) -> None:
        with Path(strategy_yaml_path).open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cd = cfg.get("concept_drift", {}) or {}
        self._window_size = int(cd.get("window_size", 5))
        self._alert_threshold = float(cd.get("alert_threshold", 0.20))
        self._force_after = int(cd.get("force_paper_trading_after", 10))
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self._load_log()

    def record(
        self,
        trade_date: date,
        ticker: str,
        predicted_return: float,
        actual_return: float,
    ) -> None:
        row = {
            "date": trade_date,
            "ticker": str(ticker),
            "predicted": float(predicted_return),
            "actual": float(actual_return),
            "abs_error": abs(float(predicted_return) - float(actual_return)),
        }
        self._log = pd.concat([self._log, pd.DataFrame([row])], ignore_index=True)
        self._persist()

    def verdict(self) -> DriftVerdict:
        if self._log.empty or len(self._log) < self._window_size:
            return DriftVerdict(False, False, 0.0, 0, "紀錄不足")

        recent = self._log.tail(self._window_size)
        divergence = float(recent["abs_error"].sum())
        alert = divergence > self._alert_threshold

        streak = self._trailing_alert_streak()
        force = streak >= self._force_after

        reason = (
            f"滾動 {self._window_size} 筆偏差 {divergence:.1%} > {self._alert_threshold:.0%}"
            if alert
            else "模型與市場對齊"
        )
        return DriftVerdict(alert, force, round(divergence, 4), streak, reason)

    def reset(self) -> None:
        self._log = self._empty_log()
        self._persist()

    # ─────────────────────────────────────
    # 內部
    # ─────────────────────────────────────
    def _trailing_alert_streak(self) -> int:
        """從最新一筆往回數，連續 window 滑動中每次都觸發 alert 的次數。"""
        if len(self._log) < self._window_size:
            return 0
        arr = self._log["abs_error"].to_numpy()
        streak = 0
        for i in range(len(arr), self._window_size - 1, -1):
            window_sum = arr[i - self._window_size : i].sum()
            if window_sum > self._alert_threshold:
                streak += 1
            else:
                break
        return streak

    def _load_log(self) -> pd.DataFrame:
        if self._log_path.exists():
            try:
                return pd.read_parquet(self._log_path)
            except Exception:
                pass
        return self._empty_log()

    @staticmethod
    def _empty_log() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["date", "ticker", "predicted", "actual", "abs_error"]
        )

    def _persist(self) -> None:
        self._log.to_parquet(self._log_path, index=False)
