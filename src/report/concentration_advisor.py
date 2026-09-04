"""
集中度動態調整 + DCA Gate + Crash Hedge 整合 module

三個邏輯整合（全部 regime-aware）：

1. **集中度建議**：依 regime 給出減持/加買/維持建議
   - LATE_BULL + 集中度 > 30% → 建議減持（保守）
   - MID_BULL/EARLY_BULL + 集中度 > 30% → 建議加買稀釋（順勢）
   - BEAR + 集中度 > 30% → 維持現金（避險）

2. **DCA Gate**：依 TAIEX 距 MA200/MA60 + VIX 判斷 DCA 倍率
   - 距 MA200 > +30%（極度過熱）→ 暫停（mult=0）
   - 距 MA60 < -5%（回檔機會）→ 加速（mult=1.5）
   - VIX > 30（恐慌）→ 暫停
   - 其他 → 正常（mult=1.0）

3. **Crash Hedge**：極端情境主動降倉
   - 進入：VIX > 30 AND TAIEX 月跌 > 15%
   - 退出：VIX 回落或 TAIEX 收復（簡化：當下條件不滿足即退出）
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ── Helpers ──

def _load_assets(project_root: Path) -> dict:
    p = project_root / "data" / "assets.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _fetch_price(ticker: str) -> float:
    """yfinance 取現價，台股加 .TW/.TWO suffix"""
    try:
        import yfinance as yf
        if not ticker.replace(".", "").isdigit():
            hist = yf.Ticker(ticker).history(period="3d")
            return float(hist["Close"].iloc[-1]) if not hist.empty else 0.0
        for sfx in [".TW", ".TWO"]:
            try:
                hist = yf.Ticker(ticker + sfx).history(period="3d")
                if not hist.empty:
                    return float(hist["Close"].iloc[-1])
            except Exception:
                continue
    except Exception:
        return 0.0
    return 0.0


def _get_taiex_monthly_change() -> float:
    """TAIEX 過去 22 個交易日（≈30 日曆日）累計漲跌 %"""
    try:
        import yfinance as yf
        h = yf.Ticker("^TWII").history(period="60d", auto_adjust=False)
        if len(h) < 22:
            return 0.0
        return (float(h["Close"].iloc[-1]) / float(h["Close"].iloc[-22]) - 1) * 100
    except Exception:
        return 0.0


def _load_taifex_supplement(project_root: Path) -> pd.DataFrame:
    """從 TAIFEX cache 補充最新外資 TX OI（FinMind 沒抓到的日期）"""
    import pandas as pd
    taifex_dir = project_root / "data" / "cache" / "taifex_inst"
    if not taifex_dir.exists():
        return pd.DataFrame()
    rows = []
    for f in sorted(taifex_dir.glob("*.parquet")):
        try:
            df = pd.read_parquet(f)
            sub = df[(df["futures_id"] == "TX") &
                     (df["institutional"] == "Foreign_Investor")]
            if not sub.empty:
                rows.append(sub)
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.concat(rows, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _get_foreign_tx_oi_zscore(project_root: Path, lookback_days: int = 60) -> dict:
    """
    外資台指期淨未平倉 z-score
    Backtest 已驗證 z<-2.0 後 10 日 TAIEX alpha +1.43%, t=4.09 (n=123)

    資料來源：
      1. FinMind cache (long history) — 主要
      2. TAIFEX cache (最新一日 supplement) — fallback，比 FinMind 早 12-16h
    """
    import pandas as pd
    from datetime import date, timedelta
    try:
        # 1. FinMind history
        path = project_root / "data" / "cache" / "finmind" / "extras" / "futures_institutional.parquet"
        if not path.exists():
            return {"available": False, "reason": "futures_institutional cache 不存在"}

        df = pd.read_parquet(path)
        df = df[(df["futures_id"] == "TX") & (df["institutional_investors"] == "外資")].copy()
        df["date"] = pd.to_datetime(df["date"])
        df["net_oi"] = (
            df["long_open_interest_balance_volume"] -
            df["short_open_interest_balance_volume"]
        )
        df = df[["date", "net_oi"]].sort_values("date").reset_index(drop=True)

        # 2. TAIFEX supplement（最新幾天 FinMind 還沒抓的）
        taifex = _load_taifex_supplement(project_root)
        if not taifex.empty:
            finmind_dates = set(df["date"])
            new_taifex = taifex[~taifex["date"].isin(finmind_dates)]
            if not new_taifex.empty:
                supp = new_taifex[["date", "net_oi"]].copy()
                df = pd.concat([df, supp], ignore_index=True)
                df = df.sort_values("date").reset_index(drop=True)

        # 計算 z-score
        df["net_oi_ma"] = df["net_oi"].rolling(lookback_days).mean()
        df["net_oi_std"] = df["net_oi"].rolling(lookback_days).std()
        df["z"] = (df["net_oi"] - df["net_oi_ma"]) / df["net_oi_std"]
        df = df.dropna(subset=["z"])

        if df.empty:
            return {"available": False, "reason": "資料不足"}

        latest = df.iloc[-1]
        latest_date = latest["date"].date()
        days_stale = (date.today() - latest_date).days
        is_stale = days_stale > 7

        # 連續 z < -2.0 天數
        consec = 0
        for z in reversed(df["z"].tolist()):
            if z < -2.0:
                consec += 1
            else:
                break

        # 來源標記
        finmind_max = pd.read_parquet(path)
        finmind_max = finmind_max[(finmind_max["futures_id"] == "TX") &
                                  (finmind_max["institutional_investors"] == "外資")]
        finmind_latest = pd.to_datetime(finmind_max["date"]).max()
        source = "TAIFEX (即時)" if pd.Timestamp(latest_date) > finmind_latest else "FinMind"

        return {
            "available": True,
            "latest_z": float(latest["z"]),
            "latest_date": latest_date.isoformat(),
            "consec_below_2": consec,
            "net_oi": int(latest["net_oi"]),
            "days_stale": days_stale,
            "is_stale": is_stale,
            "source": source,
        }
    except Exception as e:
        return {"available": False, "reason": str(e)[:80]}


# ── 集中度監控 ──

@dataclass
class HoldingSnapshot:
    ticker: str
    shares: int
    cost_incl_fee: float
    price: float
    market_value: float
    pct_of_holdings: float
    pl_pct: float


def compute_concentration(project_root: Path) -> tuple[list[HoldingSnapshot], float]:
    """回傳 (holdings list 排序後, 總持股市值)"""
    data = _load_assets(project_root)
    holdings = data.get("holdings", {}).get("long_term", [])
    if not holdings:
        return [], 0.0

    snaps: list[HoldingSnapshot] = []
    total_mv = 0.0
    for h in holdings:
        tk = str(h.get("ticker", ""))
        sh = int(h.get("shares", 0))
        cost = float(h.get("cost", 0))
        cost_fee = float(h.get("cost_incl_fee", cost))
        price = _fetch_price(tk) or cost_fee
        mv = sh * price
        total_mv += mv
        snaps.append(HoldingSnapshot(
            ticker=tk, shares=sh, cost_incl_fee=cost_fee,
            price=price, market_value=mv,
            pct_of_holdings=0.0,  # 後填
            pl_pct=(price / cost_fee - 1) * 100 if cost_fee > 0 else 0.0,
        ))

    for s in snaps:
        s.pct_of_holdings = (s.market_value / total_mv * 100) if total_mv > 0 else 0
    snaps.sort(key=lambda s: s.market_value, reverse=True)
    return snaps, total_mv


def concentration_advice(snap: HoldingSnapshot, regime_cycle: str) -> dict:
    """
    依 regime 給出單檔集中度建議

    Returns: {
        action: 'reduce' | 'dilute' | 'hold' | 'none',
        target_shares: int,        # 建議調整後股數（減持時）
        target_dilute_amount: float,  # 建議加買其他 ETF 金額
        reason: str
    }
    """
    if snap.pct_of_holdings <= 30:
        return {"action": "none", "target_shares": snap.shares,
                "target_dilute_amount": 0, "reason": "集中度正常"}

    # 計算目標股數（降至 30%）：
    # target_shares × price / (total_mv - removed_mv) = 0.30
    # 簡化：target_pct = 30% → target_shares = 30% / current_pct × current_shares
    target_pct = 30
    target_shares = max(1, int(snap.shares * target_pct / snap.pct_of_holdings))
    reduce_shares = snap.shares - target_shares

    if regime_cycle == "late_bull":
        # 折衷版：降至 35%（保留上升空間）
        target_pct_mid = 35
        target_shares_mid = max(1, int(snap.shares * target_pct_mid / snap.pct_of_holdings))
        reduce_mid = snap.shares - target_shares_mid
        return {
            "action": "reduce",
            "target_shares": target_shares,
            "target_shares_conservative": target_shares_mid,
            "target_dilute_amount": 0,
            "reason": (f"LATE_BULL 過熱 → 嚴格版減 {reduce_shares} 股至 {target_shares} 股 (=30%)；"
                       f"折衷版減 {reduce_mid} 股至 {target_shares_mid} 股 (=35%，保留上升空間)。"
                       f"當前 P/L {snap.pl_pct:+.1f}%")
        }
    elif regime_cycle in ("mid_bull", "early_bull"):
        # 加買稀釋：估計需新增金額讓 snap.pct = 30%
        # current_mv / (total_mv + add) = 0.30 → add = current_mv/0.30 - total_mv
        # 簡化：add ≈ 1.5 × (snap.market_value - 0.30 × current_total_mv)
        # 這裡傳出 snap.market_value 給 caller 自己算
        add_needed = (snap.market_value / 0.30) - (snap.market_value / (snap.pct_of_holdings / 100))
        return {
            "action": "dilute",
            "target_shares": snap.shares,
            "target_dilute_amount": max(0, add_needed),
            "reason": f"{regime_cycle.upper()} 順勢 → 加買 ETF 稀釋（新增 NT${add_needed:,.0f}）"
        }
    elif regime_cycle == "bear":
        return {
            "action": "hold",
            "target_shares": snap.shares,
            "target_dilute_amount": 0,
            "reason": "BEAR 避險 → 維持現金，不調整"
        }
    return {"action": "none", "target_shares": snap.shares,
            "target_dilute_amount": 0, "reason": ""}


# ── DCA Gate ──

@dataclass
class DCAGateResult:
    mode: str  # "accelerated" | "normal" | "paused" | "halted"
    multiplier: float
    reason: str


def evaluate_dca_gate(regime) -> DCAGateResult:
    """
    判斷當下是否該執行 DCA、加速或暫停

    優先序：halted > paused > accelerated > normal
    """
    pct_above_ma200 = (regime.taiex_close / regime.ma200 - 1) * 100
    pct_above_ma60 = (regime.taiex_close / regime.ma60 - 1) * 100

    # halted（最強訊號）
    if regime.vix > 30:
        return DCAGateResult(
            mode="halted", multiplier=0.0,
            reason=f"VIX {regime.vix:.1f} > 30 恐慌 → 暫停所有 DCA"
        )

    # paused（過熱）
    if pct_above_ma200 > 30:
        return DCAGateResult(
            mode="paused", multiplier=0.0,
            reason=f"TAIEX 距 MA200 {pct_above_ma200:+.1f}% > 30% 極度過熱 → 暫停 DCA 等回檔"
        )

    # accelerated（回檔機會）
    if pct_above_ma60 < -5:
        return DCAGateResult(
            mode="accelerated", multiplier=1.5,
            reason=f"TAIEX 距 MA60 {pct_above_ma60:+.1f}% 回檔 → 加速 DCA 1.5x"
        )

    # normal
    return DCAGateResult(
        mode="normal", multiplier=1.0,
        reason=f"TAIEX 距 MA200 {pct_above_ma200:+.1f}% / MA60 {pct_above_ma60:+.1f}% → 正常 DCA"
    )


# ── Crash Hedge ──

@dataclass
class CrashHedgeResult:
    active: bool
    level: str  # "crash" | "pre_crash" | "warning" | "normal"
    reason: str
    action: str  # 建議行動


def evaluate_crash_hedge(regime, project_root: Path | None = None) -> CrashHedgeResult:
    """
    多層 Crash Hedge：
      Crash Mode    : VIX > 30 AND TAIEX 月跌 > 15%（最嚴重）
      Pre-Crash     : 外資 TX z < -2.0 連續 3 日（已驗證 alpha +1.43%, t=4.09）
      Warning       : VIX > 30 OR TAIEX 月跌 > 15%（單一）OR 外資 z < -2.0 單日
      Normal        : 都未觸發
    """
    monthly_change = _get_taiex_monthly_change()
    vix_extreme = regime.vix > 30
    taiex_crash = monthly_change < -15

    # 外資 TX z-score 訊號
    foreign = {}
    if project_root is not None:
        foreign = _get_foreign_tx_oi_zscore(project_root)
    foreign_avail = foreign.get("available", False)
    foreign_z = foreign.get("latest_z", 0.0) if foreign_avail else 0.0
    foreign_consec = foreign.get("consec_below_2", 0) if foreign_avail else 0
    foreign_stale = foreign.get("is_stale", False)

    # === Crash Mode（最嚴重）===
    if vix_extreme and taiex_crash:
        return CrashHedgeResult(
            active=True, level="crash",
            reason=f"VIX {regime.vix:.1f} > 30 AND TAIEX 月跌 {monthly_change:+.1f}% < -15%",
            action="🚨 Crash Mode → 停止所有新倉、現金 ≥ 50%、等 VIX 從高點回落 30% 再進場"
        )

    # === Pre-Crash（外資轉空 3 日）===
    if foreign_avail and not foreign_stale and foreign_consec >= 3:
        return CrashHedgeResult(
            active=True, level="pre_crash",
            reason=f"外資 TX net OI z={foreign_z:+.2f} 連續 {foreign_consec} 日 < -2.0（{foreign['latest_date']}）",
            action=(f"⚠️ Pre-Crash 警戒 → 預期未來 10 日 TAIEX -1.43% (backtest t=4.09)。"
                    f"建議：暫停新倉、降低槓桿、保守至少 2 週")
        )

    # === Warning（單一條件）===
    warnings = []
    if vix_extreme:
        warnings.append(f"VIX {regime.vix:.1f} > 30")
    if taiex_crash:
        warnings.append(f"TAIEX 月跌 {monthly_change:+.1f}%")
    if foreign_avail and not foreign_stale and foreign_z < -2.0:
        warnings.append(f"外資 TX z={foreign_z:+.2f} < -2.0")
    if foreign_avail and foreign_stale:
        warnings.append(f"⚠️ 外資 TX 資料過期 {foreign['days_stale']} 天")

    if warnings:
        return CrashHedgeResult(
            active=False, level="warning",
            reason="; ".join(warnings),
            action="⚠️ 警戒中（尚未進入 pre-crash），密切觀察"
        )

    # === Normal ===
    foreign_str = f"外資 z={foreign_z:+.2f}" if foreign_avail else "外資資料不可用"
    return CrashHedgeResult(
        active=False, level="normal",
        reason=f"VIX {regime.vix:.1f} / TAIEX 月變 {monthly_change:+.1f}% / {foreign_str} → 正常",
        action=""
    )


# ── 主 render ──

def render_concentration_advisor_section(project_root: Path) -> str:
    """整合 3 個邏輯的 markdown section"""
    try:
        # 取得 regime
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        from src.risk.strategy_regime_gate import detect_current_regime
        regime = detect_current_regime()

        lines = [
            "## 🛡️ 集中度監控 + DCA Gate + Crash Hedge\n",
            "<details><summary>📖 這是什麼？（點開看說明）</summary>\n",
            "**作用**：每日自動風控檢查，避免在錯誤時點做錯誤動作。",
            "",
            "**3 個檢查項目：**",
            "- **Crash Hedge** — 偵測極端風險（VIX 爆 / TAIEX 崩 / 外資狂空）→ 觸發降倉",
            "- **DCA Gate** — 判斷現在適合加碼 ETF 嗎？(LATE_BULL 過熱期暫停 / 回檔期加速 1.5x)",
            "- **集中度** — 單一持股 > 30% 警報，依市場 regime 給減持/加碼/維持建議",
            "",
            "**已驗證 alpha：** 外資 TX z<-2.0 後 10 日 TAIEX 平均 -1.43%（t=4.09，n=123，3 期 OOS robust）",
            "</details>\n",
        ]

        # 1. Crash Hedge 先檢查（最高優先）
        crash = evaluate_crash_hedge(regime, project_root)
        foreign = _get_foreign_tx_oi_zscore(project_root)
        if crash.level == "crash":
            lines.append(f"### 🚨 Crash Mode 啟動")
            lines.append(f"**觸發：** {crash.reason}")
            lines.append(f"**動作：** {crash.action}\n")
        elif crash.level == "pre_crash":
            lines.append(f"### 🟠 Pre-Crash 警戒（外資轉空訊號）")
            lines.append(f"**觸發：** {crash.reason}")
            lines.append(f"**動作：** {crash.action}\n")
        elif crash.level == "warning":
            lines.append(f"### 🟡 風險警戒")
            lines.append(f"**狀態：** {crash.reason}")
            lines.append(f"**動作：** {crash.action}\n")
        else:
            # Normal — 顯示外資 TX z-score 現況（已驗證 t=4.09 訊號）
            if foreign.get("available") and not foreign.get("is_stale"):
                z = foreign["latest_z"]
                emoji = "🟢" if z > 0 else ("🟡" if z > -1.5 else "🟠")
                lines.append(f"### {emoji} 風控狀態：正常")
                lines.append(f"- VIX {regime.vix:.1f} / TAIEX 月變 {_get_taiex_monthly_change():+.1f}%")
                src = foreign.get("source", "FinMind")
                lines.append(f"- **外資 TX net OI z={z:+.2f}**（{foreign['latest_date']}, "
                             f"net={foreign['net_oi']:+,}, 來源={src}）— 警戒線 z<-2.0\n")

        # 2. DCA Gate
        dca = evaluate_dca_gate(regime)
        gate_emoji = {"halted": "🔴", "paused": "🟡", "normal": "🟢", "accelerated": "🟢⬆️"}
        lines.append(f"### {gate_emoji.get(dca.mode, '⚪')} DCA Gate: `{dca.mode.upper()}` (mult={dca.multiplier}x)")
        lines.append(f"**理由：** {dca.reason}\n")

        if dca.mode == "halted":
            lines.append("> 暫停所有 DCA 計畫（含 EWY、0050、00881、00947、00646）\n")
        elif dca.mode == "paused":
            lines.append("> 暫停大盤 DCA。EWY（韓國獨立）可繼續，等 TAIEX 回檔再啟動其他批次\n")
        elif dca.mode == "accelerated":
            lines.append("> 加速 DCA 1.5x。優先補充 0050 / 00881 / 00646（受影響大盤）\n")

        # 3. 集中度監控
        snaps, total_mv = compute_concentration(project_root)
        if snaps:
            lines.append(f"### 📊 持股集中度（總市值 NT${total_mv:,.0f}）\n")
            lines.append("| 代號 | 股數 | 市值 | 占持股 | P/L | 建議 |")
            lines.append("|---|---|---|---|---|---|")
            for s in snaps:
                advice = concentration_advice(s, regime.cycle)
                action_emoji = {"reduce": "🔴 減持", "dilute": "🟡 加買稀釋",
                                "hold": "⚪ 維持", "none": "✅"}
                badge = action_emoji.get(advice["action"], "—")
                if advice["action"] == "reduce":
                    badge = f"{badge} → {advice['target_shares']} 股"
                lines.append(f"| {s.ticker} | {s.shares} | NT${s.market_value:,.0f} | "
                             f"{s.pct_of_holdings:.1f}% | {s.pl_pct:+.1f}% | {badge} |")

            # 集中度警報明細
            over = [s for s in snaps if s.pct_of_holdings > 30]
            if over:
                lines.append(f"\n#### ⚠️ 集中度警報（{len(over)} 檔 > 30%）")
                for s in over:
                    advice = concentration_advice(s, regime.cycle)
                    if advice["action"] != "none":
                        lines.append(f"- **{s.ticker}** {s.pct_of_holdings:.0f}% → {advice['reason']}")

        return "\n".join(lines) + "\n"
    except Exception as e:
        import traceback
        return f"## 🛡️ 集中度 + DCA Gate\n\n計算失敗: {e}\n```\n{traceback.format_exc()}\n```\n"
