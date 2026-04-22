"""
籌碼集中度因子（權重 25%，計畫中最強的短線先行指標）。

資料來源：FinMindClient.get_institutional / get_broker_distribution / get_foreign_ownership

評分組成（加權合成 0–1）：
  - 投信連買天數（0.35）：連買 ≥ 5 日給滿分，線性遞減
  - 投信買超佔股本 %（0.35）：> 0.5% 給滿分
  - 前 15 大分點淨買超佔成交量 %（0.30）：> 2% 給滿分（大額買盤挾持走勢）

加分（Phase 11 定盤：foreign 與 double_dragon 互斥 max(foreign, dd)，封頂 0.15）：
  - 外資加碼 (+0.15)：外資持股率連續 3 日上升 → Free 方案無分點時的替代訊號
    打開 2023–2025 AI 設備股行情的關鍵 — 投信保守時外資仍持續堆疊
  - Phase 11.4 曾試過疊加 clamp（封頂 0.20），但 2024 Sharpe 1.96→1.19 大幅退化
    → 顧問假設成立：foreign 與 dd 是高度共線的「大錢進場」訊號，強制疊加會讓
    共振時的分數虛高，在牛市（隨便買都漲）下失去對入手位階的挑剔
  - Phase 11.5 (D-only) 單獨回退 clamp 後 2024 回到 1.49（仍低於 baseline 1.96），
    證明同時有 (B) clamp 與 (D) 移除 penalty 兩個兇手；最終回退到 11.3 定盤
  - 雙龍取珠 (Double Dragon Filter, +0.10)：必須同時滿足
    前提（散戶退場）：過去 5 日融資總額減少 + ≥ 3 天下跌 + 收盤未跌
    板機（聰明錢進場）：投信或外資連續淨買 ≥ 2 日
    意義：散戶離開只是「前提」，大戶進場才是「板機」。單純融資下降在 2023
    回測中是負 alpha（價格假撐後續崩），只在確認機構同時吃貨時才是真實的籌碼換手。
    Phase 11.3 演化脈絡：
      - Phase 11 原設計：signal A (散戶退場單獨) + signal B (投信+融資降) 各 +0.08
      - Phase 11.2 修復 MarginPurchaseLimit vs TodayBalance 欄位 bug，訊號才真正觸發
      - Phase 11.3 合併為單一 AND 條件：散戶退場 + 機構確認 → +0.10
    環境敏感度：若 watchlist 寬度 < 40%（權值股獨強環境），此 bonus 減半，
    避免在中小型股被吸金的行情下誤追進已被機構出貨的標的

附帶風控訊號（flags）：
  - day_trader_risk：前 15 大買超分點中，隔日沖黑名單佔比 > 40% → True
    composite_scorer 會據此把「入手區間」下修為 前收 − 0.5 × ATR（見計畫 §L2）
  - foreign_accumulating：外資持股率連 3 日上升 → True
  - double_dragon：雙龍取珠訊號觸發（散戶退場 + 機構吃貨）→ True
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
        foreign_bonus: float = 0.15,
        foreign_streak_required: int = 3,
        double_dragon_bonus: float = 0.10,
        double_dragon_smart_streak_required: int = 2,
        margin_lookback_long: int = 5,
        margin_min_down_days: int = 3,
        breadth_halve_threshold: float = 0.40,
    ) -> None:
        self._day_trader_ids, self._dt_thresholds = _load_day_trader_config(
            day_trader_config_path
        )
        self._consecutive_full = consecutive_days_full
        self._shares_pct_full = shares_pct_full
        self._broker_pct_full = broker_volume_pct_full
        self._w_consec, self._w_pct, self._w_broker = weights
        self._foreign_bonus = foreign_bonus
        self._foreign_streak_required = foreign_streak_required
        self._double_dragon_bonus = double_dragon_bonus
        self._smart_streak_required = double_dragon_smart_streak_required
        self._margin_lookback_long = margin_lookback_long
        self._margin_min_down_days = margin_min_down_days
        self._breadth_halve_threshold = breadth_halve_threshold

    def score(
        self,
        ticker: str,
        institutional: pd.DataFrame,
        broker: pd.DataFrame,
        shares_outstanding: int,
        recent_volume: int,
        concentration: pd.DataFrame | None = None,
        margin: pd.DataFrame | None = None,
        ohlcv: pd.DataFrame | None = None,
        breadth: float | None = None,
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

        base_value = (
            self._w_consec * s_consec
            + self._w_pct * s_pct
            + self._w_broker * s_broker
        )

        foreign_pct, foreign_streak, foreign_accum = self._foreign_signal(concentration)
        foreign_bonus_val = self._foreign_bonus if foreign_accum else 0.0

        dd_bonus_val, double_dragon, dd_source = self._double_dragon(
            margin=margin,
            ohlcv=ohlcv,
            trust_streak=consec,
            foreign_streak=foreign_streak,
            breadth=breadth,
        )

        # Phase 11 定盤：foreign 與 double_dragon 擇大者（互斥取 max，封頂 = foreign 0.15）
        # 11.4 曾試過加總 clamp 0.20，但 2024 Sharpe 從 1.96 崩到 1.19 — 共線訊號疊加導致虛高
        bonus = max(foreign_bonus_val, dd_bonus_val)
        value = clamp01(base_value + bonus)

        threshold = float(self._dt_thresholds.get("ratio_threshold", 0.40))
        day_trader_risk = day_trader_ratio > threshold

        return FactorScore(
            value=value,
            breakdown={
                "trust_streak_days": float(consec),
                "trust_net_buy_pct_of_shares": round(pct_of_shares, 3),
                "top15_broker_net_pct_of_volume": round(broker_pct, 3),
                "day_trader_ratio": round(day_trader_ratio, 3),
                "foreign_pct": round(foreign_pct, 3),
                "foreign_streak_days": float(foreign_streak),
                "double_dragon_source": float(dd_source),  # 0=無、1=投信、2=外資、3=雙方
                "double_dragon_bonus_applied": round(dd_bonus_val, 3),
            },
            flags={
                "day_trader_risk": day_trader_risk,
                "foreign_accumulating": foreign_accum,
                "double_dragon": double_dragon,
            },
            reason=self._build_reason(
                consec, pct_of_shares, day_trader_risk, foreign_accum, double_dragon, dd_source
            ),
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

    def _foreign_signal(
        self, ownership: pd.DataFrame | None
    ) -> tuple[float, int, bool]:
        """
        回傳 (最新外資持股 %、連續上升天數、是否連 N 日加碼)。
        「連續上升」= 最近 N 日外資持股率嚴格遞增（strict monotonic）。
        """
        if ownership is None or ownership.empty or "foreign_pct" not in ownership:
            return 0.0, 0, False
        df = ownership.sort_values("date")
        series = df["foreign_pct"].astype(float).tolist()
        if not series:
            return 0.0, 0, False
        last = series[-1]
        streak = 0
        for i in range(len(series) - 1, 0, -1):
            if series[i] > series[i - 1]:
                streak += 1
            else:
                break
        accumulating = streak >= self._foreign_streak_required
        return last, streak, accumulating

    def _double_dragon(
        self,
        margin: pd.DataFrame | None,
        ohlcv: pd.DataFrame | None,
        trust_streak: int,
        foreign_streak: int,
        breadth: float | None,
    ) -> tuple[float, bool, int]:
        """
        雙龍取珠（Phase 11.3）— 籌碼換手的高勝率複合訊號。

        AND 條件：
          1. 散戶退場前提：5 日融資總額減少 + ≥ 3 天下跌 + 收盤未跌
          2. 機構板機    ：投信 OR 外資連續淨買 ≥ smart_streak_required（預設 2 日）

        bonus：預設 +0.10；若 breadth < 門檻（權值股獨強環境）→ 減半 +0.05。

        回傳 (bonus, flag, source_code)
          source_code: 0=無、1=投信觸發、2=外資觸發、3=雙方同時

        Graceful degrade：margin 為空（FinMind 盤後 21:00 前尚未結算）→ (0, False, 0)
        """
        if margin is None or margin.empty or "margin_balance" not in margin:
            return 0.0, False, 0

        mg = margin.sort_values("date")
        balances = mg["margin_balance"].astype(float).tolist()

        n_long = self._margin_lookback_long
        min_down = self._margin_min_down_days
        if len(balances) <= n_long:
            return 0.0, False, 0

        window = balances[-(n_long + 1):]
        net_decrease = window[-1] < window[0]
        down_days = sum(1 for i in range(n_long) if window[i + 1] < window[i])
        price_not_down = self._price_not_down(ohlcv, n_long)
        if not (net_decrease and down_days >= min_down and price_not_down):
            return 0.0, False, 0

        # 板機：機構確認進場
        req = self._smart_streak_required
        trust_confirmed = trust_streak >= req
        foreign_confirmed = foreign_streak >= req
        if not (trust_confirmed or foreign_confirmed):
            return 0.0, False, 0

        source = (1 if trust_confirmed else 0) + (2 if foreign_confirmed else 0)

        bonus = self._double_dragon_bonus
        if breadth is not None and breadth < self._breadth_halve_threshold:
            bonus = bonus / 2.0

        return bonus, True, source

    @staticmethod
    def _price_not_down(ohlcv: pd.DataFrame | None, lookback: int) -> bool:
        """最近 lookback 日內收盤未跌（end >= start）。"""
        if ohlcv is None or ohlcv.empty or "close" not in ohlcv:
            return False
        sorted_df = ohlcv.sort_values("date")
        if len(sorted_df) <= lookback:
            return False
        closes = sorted_df["close"].astype(float).tolist()
        return closes[-1] >= closes[-1 - lookback]

    @staticmethod
    def _build_reason(
        consec: int,
        pct_shares: float,
        day_trader: bool,
        foreign_accum: bool,
        double_dragon: bool,
        dd_source: int,
    ) -> str:
        parts = []
        if consec >= 3:
            parts.append(f"投信連買 {consec} 日")
        if pct_shares >= 0.3:
            parts.append(f"吸 {pct_shares:.2f}% 股本")
        if foreign_accum:
            parts.append("外資連日加碼")
        if double_dragon:
            who = {1: "投信", 2: "外資", 3: "投信+外資"}.get(dd_source, "機構")
            parts.append(f"雙龍取珠（散戶退場＋{who}進場）")
        if day_trader:
            parts.append("⚠️ 隔日沖主導")
        return "、".join(parts) or "籌碼平淡"
