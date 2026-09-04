"""
Volume Anomaly Scanner（Phase 18b Step 6）— 批次掃描 universe + 晨報整合。

職責：
  1. 從 universe_all.yaml + parquet 快取載入 OHLCV
  2. 對每檔跑 scan_volume_anomaly
  3. 排序、過濾、產出晨報區段
  4. 寫入 data/state/volume_anomaly_history.parquet（幽靈追蹤）

設計取捨：
  - 板別判定：用 ticker 開頭粗分（1-3 多為 TWSE、4-9 多為 OTC），
    這不是最準的，但 OTC 清單需要另外抓 TPEx → 後續再補。
  - 市值估算：先用 None（讓市值門檻不啟用），
    完整版需要從 MOPS / FinMind 抓「股本 × 收盤」。
  - 內盤比：FinMind Free 方案無此資料 → direction 全為 "unknown"。
    分數會被「方向確認」這層降低（25 → 8），但不一票否決。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from dataclasses import dataclass
from datetime import timedelta

from src.strategy.chip_concentration import (
    ChipConcentrationSignal,
    evaluate_concentration,
)
from src.strategy.trade_plan import (
    TradePlan,
    generate_trade_plan,
    render_trade_plan_block,
)
from src.strategy.volume_anomaly import (
    VolumeAnomalySignal,
    append_to_history,
    scan_volume_anomaly,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichedAnomalySignal:
    """Vol Anomaly + Chip Concentration + Trade Plan 合成訊號。"""
    vol: VolumeAnomalySignal
    chip: ChipConcentrationSignal | None
    final_score: float
    trade_plan: TradePlan | None = None

    @property
    def ticker(self) -> str:
        return self.vol.ticker

    @property
    def board(self):
        return self.vol.board

    @property
    def triggered(self) -> bool:
        return self.vol.triggered


# ─────────────────────────────────────────
# 板別粗判（後續可以從 TWSE / TPEx 清單精確化）
# ─────────────────────────────────────────
_OTC_PREFIXES = ("4", "5", "6", "8")   # 大致規則


def guess_board(ticker: str) -> str:
    """
    粗略板別判定，僅用於閾值差異。
    上市/OTC 完整清單後續可從 src/data/twse_client 補。
    """
    t = str(ticker)
    if t.startswith("00") or t.startswith("0050"):
        return "TWSE"   # 大部分 ETF
    if t and t[0] in _OTC_PREFIXES:
        return "OTC"
    return "TWSE"


# ─────────────────────────────────────────
# 載入 universe + OHLCV
# ─────────────────────────────────────────
def load_universe(universe_yaml: Path) -> list[str]:
    raw = yaml.safe_load(universe_yaml.read_text(encoding="utf-8"))
    return sorted(raw.get("tickers", []))


def load_ohlcv_cache(ticker: str, cache_dir: Path) -> pd.DataFrame:
    path = cache_dir / f"{ticker}.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


# ─────────────────────────────────────────
# 批次掃描
# ─────────────────────────────────────────
def scan_universe(
    as_of: date,
    universe_yaml: Path,
    ohlcv_cache_dir: Path,
    score_threshold: float = 70.0,
    universe_limit: int | None = None,
    progress_every: int = 500,
) -> list[VolumeAnomalySignal]:
    """
    對 universe 內所有 ticker 跑 Vol Anomaly 掃描。
    回傳全部訊號（含未觸發的，便於診斷）。

    universe_limit: 限制數量（debug 用）
    """
    universe = load_universe(universe_yaml)
    if universe_limit:
        universe = universe[:universe_limit]

    logger.info("Vol Anomaly scan: universe %d tickers", len(universe))
    results: list[VolumeAnomalySignal] = []
    skipped = 0

    for i, tk in enumerate(universe, 1):
        if i % progress_every == 0:
            logger.info("  scan progress %d/%d, triggered=%d",
                        i, len(universe),
                        sum(1 for s in results if s.triggered))
        ohlcv = load_ohlcv_cache(tk, ohlcv_cache_dir)
        if ohlcv.empty or len(ohlcv) < 90:
            skipped += 1
            continue
        try:
            sig = scan_volume_anomaly(
                ticker=tk,
                ohlcv=ohlcv,
                as_of=as_of,
                inner_outer=None,        # FinMind Free 無此資料
                market_cap_btw=None,     # 暫不啟用市值門檻
                board=guess_board(tk),
                ex_dividend_dates=None,  # 後續補 TWSE 日曆
                score_threshold=score_threshold,
            )
            if sig is not None:
                results.append(sig)
        except Exception as e:
            logger.debug("    scan %s 失敗: %s", tk, e)

    logger.info("Vol Anomaly scan complete: %d 檔有效, %d 檔跳過, %d 觸發",
                len(results), skipped, sum(1 for s in results if s.triggered))
    return results


# ─────────────────────────────────────────
# 晨報區段渲染
# ─────────────────────────────────────────
def enrich_with_chip_concentration(
    signals: list[VolumeAnomalySignal],
    finmind_client,
    only_triggered: bool = True,
    ohlcv_lookup=None,
    total_assets_twd: int = 600_000,
) -> list[EnrichedAnomalySignal]:
    """
    對 Vol Anomaly 訊號補上 Chip Concentration 評估。
    只對 triggered=True 的訊號抓 FinMind 資料（避免對 2000+ 檔全抓）。

    Sponsor 方案額外啟用：
      - get_holding_shares_per() → L3 大戶持股斜率
      - get_broker_distribution() → L4 分點集中度
    finmind_client 不支援這些方法時自動降級（不影響 L1/L2）。
    """
    out: list[EnrichedAnomalySignal] = []
    for sig in signals:
        chip: ChipConcentrationSignal | None = None
        if (sig.triggered or not only_triggered) and finmind_client is not None:
            try:
                start = sig.as_of - timedelta(days=45)
                end = sig.as_of
                foreign = finmind_client.get_foreign_ownership(sig.ticker, start, end)
                inst = finmind_client.get_institutional(sig.ticker, start, end)

                # L3 大戶持股（Sponsor）
                holding_shares: pd.DataFrame | None = None
                if hasattr(finmind_client, "get_holding_shares_per"):
                    try:
                        holding_shares = finmind_client.get_holding_shares_per(
                            sig.ticker, start, end
                        )
                    except Exception:
                        holding_shares = None

                # L4 分點集中度（Sponsor）
                broker_df: pd.DataFrame | None = None
                ohlcv_for_chip: pd.DataFrame = pd.DataFrame()
                if ohlcv_lookup is not None:
                    try:
                        raw = ohlcv_lookup(sig.ticker)
                        if raw is not None and not raw.empty:
                            ohlcv_for_chip = raw
                    except Exception:
                        pass
                if hasattr(finmind_client, "get_broker_distribution") and not ohlcv_for_chip.empty:
                    try:
                        broker_df = finmind_client.get_broker_distribution(
                            sig.ticker, start, end
                        )
                    except Exception:
                        broker_df = None

                chip = evaluate_concentration(
                    ticker=sig.ticker,
                    foreign_holding=foreign,
                    institutional=inst,
                    ohlcv=ohlcv_for_chip,
                    as_of=sig.as_of,
                    holding_shares=holding_shares,
                    broker_df=broker_df,
                )
            except Exception as e:
                logger.debug("    chip concentration %s 失敗: %s", sig.ticker, e)
                chip = None

        bonus = chip.score_bonus if chip is not None else 0.0
        final = max(0.0, min(100.0, sig.score + bonus))

        # Trade plan：只對 triggered 訊號 + 有 ohlcv 才計算
        plan: TradePlan | None = None
        if sig.triggered and ohlcv_lookup is not None:
            try:
                ohlcv = ohlcv_lookup(sig.ticker)
                if ohlcv is not None and not ohlcv.empty:
                    plan = generate_trade_plan(
                        ticker=sig.ticker,
                        as_of=sig.as_of,
                        close=sig.close,
                        z=sig.modified_z,
                        direction=sig.direction,
                        chip_level=chip.level if chip else None,
                        ohlcv=ohlcv,
                        total_assets_twd=total_assets_twd,
                    )
            except Exception as e:
                logger.debug("    trade plan %s 失敗: %s", sig.ticker, e)

        out.append(EnrichedAnomalySignal(
            vol=sig, chip=chip, final_score=final, trade_plan=plan,
        ))
    return out


# ─── Ticker 名稱對照（FinMind cache 優先，twstock fallback）───
_NAME_MAP: dict[str, str] | None = None


# Manual override (always takes priority over FinMind cache + twstock).
# Source: Yahoo Finance verified 2026-05-07.
_MANUAL_NAME_OVERRIDES: dict[str, str] = {
    # User holdings (full coverage)
    "009819": "中信數據及電力",
    "00635U": "期元大S&P黃金",
    "00646":  "元大S&P500",
    "00947":  "台新臺灣IC設計動能",  # was missing "動能"
    "0050":   "元大台灣50",
    "2345":   "智邦",
    "2408":   "南亞科",
    "3017":   "奇鋐",
    "4543":   "萬在",         # 上櫃 (TPEx)
    "6233":   "旺玖科技",      # 上櫃 (TPEx)
    # Other newer ETFs that may not be in FinMind cache
    "00946":  "群益科技高息成長",
    "00929":  "復華台灣科技優息",
    "00939":  "統一台灣高息動能",
    "00940":  "元大臺灣價值高息",
    "00961":  "野村臺灣創新領航",
    "00963":  "兆豐臺灣藍籌30",
    "00978":  "中信臺灣智慧50",
}


def _get_name_map() -> dict[str, str]:
    global _NAME_MAP
    if _NAME_MAP is not None:
        return _NAME_MAP
    name_map: dict[str, str] = {}

    # 1. FinMind cache（含 ETF / 興櫃 / OTC，最完整若已建）
    cache_path = (
        Path(__file__).resolve().parents[2]
        / "data" / "cache" / "finmind" / "finmind"
        / "TaiwanStockInfo.parquet"
    )
    if cache_path.exists():
        try:
            df = pd.read_parquet(cache_path)
            name_map.update(zip(df["stock_id"].astype(str), df["stock_name"].astype(str)))
        except Exception:
            pass

    # 2. twstock fallback（offline，46K+ ticker 涵蓋）
    try:
        import twstock
        for tk, info in twstock.codes.items():
            if str(tk) not in name_map and info and info.name:
                name_map[str(tk)] = info.name
    except ImportError:
        pass

    # 3. Manual overrides — ALWAYS takes priority (FinMind/twstock 名稱可能過時或錯誤)
    name_map.update(_MANUAL_NAME_OVERRIDES)

    _NAME_MAP = name_map
    return _NAME_MAP


def lookup_ticker_name(ticker: str) -> str:
    """取股票中文名；找不到回 ticker 本身。"""
    return _get_name_map().get(str(ticker), str(ticker))


# ─── 等寬對齊（CJK / emoji 視覺寬度 = 2）───
def _visual_width(s: str) -> int:
    import unicodedata
    w = 0
    for c in s:
        ea = unicodedata.east_asian_width(c)
        # F/W = full-width；A (ambiguous) 在 Discord/CJK 環境算 2；emoji 普遍算 2
        if ea in ("F", "W", "A"):
            w += 2
        elif ord(c) >= 0x2600:    # emoji / 符號區段，多數 2 寬
            w += 2
        else:
            w += 1
    return w


def _pad(s: str, width: int, align: str = "left") -> str:
    cur = _visual_width(s)
    pad = max(0, width - cur)
    if align == "right":
        return " " * pad + s
    return s + " " * pad


def render_anomaly_section(
    signals: list[VolumeAnomalySignal] | list[EnrichedAnomalySignal],
    top_n: int = 10,
) -> str:
    """
    產出晨報「🔔 異常量能預警」區段。
    僅顯示 triggered=True 的，按分數降序前 N 名。
    """
    enriched_mode = bool(signals) and isinstance(signals[0], EnrichedAnomalySignal)
    triggered = [s for s in signals if s.triggered]
    sort_key = (lambda s: s.final_score) if enriched_mode else (lambda s: s.score)
    triggered.sort(key=sort_key, reverse=True)

    lines = ["## 🔔 [Paper] 異常量能預警（吃貨期候選）"]

    if not triggered:
        lines.append("\n_今日無觸發訊號。_")
        lines.append(f"\n_全市場掃描 {len(signals)} 檔有效樣本。_")
        return "\n".join(lines) + "\n"

    lines.append(
        f"\n_全市場掃描 {len(signals)} 檔，{len(triggered)} 檔觸發。"
        f"以下為分數前 {min(top_n, len(triggered))} 名候選：_\n"
    )
    # 使用 code block + 等寬字體對齊（CJK-aware）
    lines.append("```")
    # 欄寬 + 空格分隔（按視覺寬度）
    SEP = "  "
    if enriched_mode:
        header = SEP.join([
            _pad("代號", 6),
            _pad("名稱", 10),
            _pad("板別", 5),
            _pad("z", 5, "right"),
            _pad("10dZ", 5, "right"),
            _pad("方向", 5),
            _pad("籌碼", 7),
            _pad("5d%", 7, "right"),
            _pad("MA", 2),
            _pad("分", 3, "right"),
        ])
    else:
        header = SEP.join([
            _pad("代號", 6),
            _pad("名稱", 10),
            _pad("板別", 5),
            _pad("z", 5, "right"),
            _pad("10dZ", 5, "right"),
            _pad("方向", 5),
            _pad("收盤", 8, "right"),
            _pad("5d%", 7, "right"),
            _pad("MA", 2),
            _pad("分", 3, "right"),
        ])
    lines.append(header)
    lines.append("-" * _visual_width(header))

    direction_icon = {"buying": "🟢 買", "selling": "🔴 賣", "unknown": "⚪ ?"}
    chip_badge_map = {
        "strong_accumulation": "🟢🟢 強",
        "moderate_accumulation": "🟢 中",
        "weak_accumulation": "🟡 弱",
        "no_accumulation": "⚪ —",
        "distribution": "🔴 出",
    }
    for s in triggered[:top_n]:
        if enriched_mode:
            v = s.vol
            chip_badge = chip_badge_map.get(s.chip.level, "⚪—") if s.chip else "⚪NA"
            ma_icon = "Y" if v.above_200ma else "N"
            name = lookup_ticker_name(v.ticker)
            while _visual_width(name) > 10:
                name = name[:-1]
            dir_short = {"buying": "🟢買", "selling": "🔴賣", "unknown": "⚪?"}.get(v.direction, "?")
            row = SEP.join([
                _pad(str(v.ticker), 6),
                _pad(name, 10),
                _pad(v.board, 5),
                _pad(f"{v.modified_z:.2f}", 5, "right"),
                _pad(f"{v.days_z_above_2}/10", 5, "right"),
                _pad(dir_short, 5),
                _pad(chip_badge, 7),
                _pad(f"{v.price_change_5d_pct:+.1f}%", 7, "right"),
                _pad(ma_icon, 2),
                _pad(f"{s.final_score:.0f}", 3, "right"),
            ])
        else:
            ma_icon = "Y" if s.above_200ma else "N"
            name = lookup_ticker_name(s.ticker)
            while _visual_width(name) > 10:
                name = name[:-1]
            dir_short = {"buying": "🟢買", "selling": "🔴賣", "unknown": "⚪?"}.get(s.direction, "?")
            row = SEP.join([
                _pad(str(s.ticker), 6),
                _pad(name, 10),
                _pad(s.board, 5),
                _pad(f"{s.modified_z:.2f}", 5, "right"),
                _pad(f"{s.days_z_above_2}/10", 5, "right"),
                _pad(dir_short, 5),
                _pad(f"{s.close:.2f}", 8, "right"),
                _pad(f"{s.price_change_5d_pct:+.1f}%", 7, "right"),
                _pad(ma_icon, 2),
                _pad(f"{s.score:.0f}", 3, "right"),
            ])
        lines.append(row)
    lines.append("```")

    # Trade plan 詳細區塊（enriched mode + 至少一個有 plan）
    if enriched_mode:
        plans_block_added = False
        for s in triggered[:top_n]:
            if not isinstance(s, EnrichedAnomalySignal) or s.trade_plan is None:
                continue
            if not plans_block_added:
                lines.append("\n### 📋 進出場建議（針對每一檔）\n")
                plans_block_added = True
            lines.append(render_trade_plan_block(s.trade_plan))

    lines.append(
        "\n**⚠️ 重要說明**："
        "\n- 本訊號為「**幽靈追蹤**」階段，**不建議直接進場**"
        "\n- 內盤比資料未啟用（需 FinMind Sponsor），方向多為「⚪ ?」"
        "\n- 訊號每日寫入 `data/state/volume_anomaly_history.parquet`，"
        "6-12 個月後回查命中率才能驗證策略 alpha"
    )
    if enriched_mode:
        lines.append(
            "\n- 進出場建議基於回測甜蜜區（z 3.0-3.5），"
            "🟢 高信心 = z 甜蜜 + 籌碼/方向確認；🟡 中信心 = z 甜蜜但方向未確認；"
            "🟠 低信心 = z 不在甜蜜區，僅實驗追蹤"
        )
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────
# 對外整合 API
# ─────────────────────────────────────────
def run_anomaly_scan_for_briefing(
    as_of: date,
    project_root: Path,
    universe_limit: int | None = None,
    finmind_client=None,
) -> tuple[str, list]:
    """
    晨報主入口呼叫此函式：
      1. 全市場 Vol Anomaly 掃描
      2. 對 triggered 訊號補抓 Chip Concentration（若提供 finmind_client）
      3. 寫入歷史紀錄
      4. 產出 Markdown 區段

    回傳 (markdown, all_signals)
    """
    universe_yaml = project_root / "config" / "universe_all.yaml"
    cache_dir = project_root / "data" / "cache" / "yfinance" / "tw_ohlcv"
    history_path = project_root / "data" / "state" / "volume_anomaly_history.parquet"

    if not universe_yaml.exists():
        return "## 🔔 [Paper] 異常量能預警\n\n_universe_all.yaml 不存在，跳過掃描。_\n", []

    signals = scan_universe(
        as_of=as_of,
        universe_yaml=universe_yaml,
        ohlcv_cache_dir=cache_dir,
        universe_limit=universe_limit,
    )

    # 幽靈追蹤：寫入歷史（只寫 raw VolumeAnomalySignal）
    if signals:
        append_to_history(history_path, signals)

    # Chip Concentration + Trade Plan 補強（只對 triggered 抓 FinMind）
    final_signals: list = signals
    triggered_ct = sum(1 for s in signals if s.triggered)
    if triggered_ct > 0:
        ohlcv_lookup = lambda tk: load_ohlcv_cache(tk, cache_dir)
        try:
            logger.info("Enrichment: %d triggered tickers", triggered_ct)
            final_signals = enrich_with_chip_concentration(
                signals, finmind_client,
                only_triggered=True,
                ohlcv_lookup=ohlcv_lookup,
            )
        except Exception as e:
            logger.warning("Enrichment 失敗: %s", e)

    md = render_anomaly_section(final_signals)
    return md, final_signals
