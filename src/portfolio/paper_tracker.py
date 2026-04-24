"""
Paper Trading 追蹤器（Phase 10） — 把每日晨報的 recommendations 存檔，
之後用後續真實日線 OHLCV 逐筆對照「是否進場、是否觸發止損/止盈/超時」。

設計原則（避免 Phase 12/13 重覆犯錯）：
  - 「每日快照」是真相來源：data/paper_trades/YYYY-MM-DD.json
  - ledger 是「由快照重算」的衍生產物，可重建、不持久化關鍵狀態
  - reconcile() 是純函式（input: 快照清單 + OHLCV getter → output: ledger 列表），
    好測試、不偷改檔案

晨報流程整合：
  morning_briefing.py 在 pipeline 跑完後呼叫 record_daily()，把當日 recos
  寫到 data/paper_trades/YYYY-MM-DD.json。
  使用者可隨時執行 scripts/paper_check.py 重算 ledger + 印統計。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd


# ─────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────
@dataclass(frozen=True)
class PaperRecord:
    """某日晨報產出的單檔推薦快照（record_daily 寫入）。"""
    ticker: str
    entry_low: float
    entry_high: float
    target: float
    stop: float
    atr: float
    score: float


@dataclass(frozen=True)
class PaperTrade:
    """reconcile() 後的成交結果（可能還沒平倉）。"""
    ticker: str
    reco_date: str          # 晨報日期（YYYY-MM-DD）
    status: str             # pending / open / closed_target / closed_stop / closed_timeout / expired
    entry_date: str | None = None
    entry_price: float | None = None
    exit_date: str | None = None
    exit_price: float | None = None
    gross_return_pct: float | None = None  # 未扣成本的粗報酬（留給使用者直觀看）
    reco_score: float = 0.0

    @property
    def is_closed(self) -> bool:
        return self.status.startswith("closed_")


# ─────────────────────────────────────────
# 寫入：每日快照
# ─────────────────────────────────────────
def record_daily(
    state_dir: Path | str,
    as_of: date,
    recommendations: list,        # list[Recommendation]，用 duck typing 避免循環 import
) -> Path:
    """
    把 recommendations 序列化為 data/paper_trades/YYYY-MM-DD.json。
    若當日檔案已存在會覆寫（晨報每日只產一次）。
    """
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    payload = {
        "reco_date": as_of.isoformat(),
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "recommendations": [
            {
                "ticker": r.ticker,
                "score": float(r.score),
                "entry_low": float(r.entry_low),
                "entry_high": float(r.entry_high),
                "target": float(r.target),
                "stop": float(r.stop),
                "atr": float(r.atr),
            }
            for r in recommendations
        ],
    }
    out = state / f"{as_of.isoformat()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_daily(state_dir: Path | str) -> list[dict]:
    """讀出所有 YYYY-MM-DD.json 的快照（按日期升冪）。"""
    state = Path(state_dir)
    if not state.exists():
        return []
    snapshots = []
    for p in sorted(state.glob("????-??-??.json")):
        try:
            snapshots.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return snapshots


# ─────────────────────────────────────────
# Reconcile：把快照 + OHLCV 推成 PaperTrade 列表
# ─────────────────────────────────────────
OhlcvFetcher = Callable[[str, date, date], pd.DataFrame]


def reconcile(
    state_dir: Path | str,
    ohlcv_fetcher: OhlcvFetcher,
    as_of: date,
    max_hold_days: int = 20,
) -> list[PaperTrade]:
    """
    對每個快照的每筆 recommendation 推算當下狀態：
      - 若 reco_date >= as_of：無法驗證（未來交易日）→ 跳過
      - 若 reco_date 隔日（T+1）的 bar [low, high] ∩ [entry_low, entry_high] 非空 → 模擬進場
        進場價 = 區間交集中點（與 backtest engine 一致的保守估計）
      - 進場後逐日掃 bar：先看 low 是否觸 stop，再看 high 是否觸 target
        （同日同觸發保守視為 stop，跟 backtest engine §8.1 一致）
      - 持股天數 >= max_hold_days 仍未觸發 → timeout 以當日收盤出場
      - 未進場（entry_date 之後連續日都沒重疊）→ expired

    注意：不套用交易成本，給使用者看「毛報酬」直覺；正式績效比對仍以回測為準。
    """
    trades: list[PaperTrade] = []
    for snap in load_daily(state_dir):
        reco_date = date.fromisoformat(snap["reco_date"])
        if reco_date >= as_of:
            continue  # 未有隔日資料可驗證

        for reco in snap["recommendations"]:
            trade = _simulate_single(
                ticker=reco["ticker"],
                reco_date=reco_date,
                entry_low=reco["entry_low"],
                entry_high=reco["entry_high"],
                target=reco["target"],
                stop=reco["stop"],
                score=reco["score"],
                as_of=as_of,
                ohlcv_fetcher=ohlcv_fetcher,
                max_hold_days=max_hold_days,
            )
            trades.append(trade)
    return trades


def _simulate_single(
    ticker: str,
    reco_date: date,
    entry_low: float,
    entry_high: float,
    target: float,
    stop: float,
    score: float,
    as_of: date,
    ohlcv_fetcher: OhlcvFetcher,
    max_hold_days: int,
) -> PaperTrade:
    start = reco_date + timedelta(days=1)
    # 多抓幾天 buffer 避免週末假日
    fetch_end = min(as_of, start + timedelta(days=max_hold_days + 10))
    df = ohlcv_fetcher(ticker, start, fetch_end)
    if df is None or df.empty:
        return PaperTrade(
            ticker=ticker, reco_date=reco_date.isoformat(),
            status="pending", reco_score=score,
        )

    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of]

    # 找第一個進場日（bar 與入手區間重疊）
    entry_idx = None
    for i, bar in df.iterrows():
        lo = max(float(bar["low"]), entry_low)
        hi = min(float(bar["high"]), entry_high)
        if lo <= hi:
            entry_idx = i
            entry_price = (lo + hi) / 2.0
            entry_date = bar["date"]
            break

    if entry_idx is None:
        # 還沒進場但持續追蹤：只要 as_of 仍有未觀察完的後續 bar，status=pending；
        # 若 bar 已覆蓋超過 5 天仍沒打到入手區間 → expired（實盤不會再追）
        status = "expired" if len(df) >= 5 else "pending"
        return PaperTrade(
            ticker=ticker, reco_date=reco_date.isoformat(),
            status=status, reco_score=score,
        )

    # 進場後：掃 stop / target / timeout
    for j in range(entry_idx, len(df)):
        bar = df.iloc[j]
        hold_days = j - entry_idx
        bar_low = float(bar["low"])
        bar_high = float(bar["high"])
        hit_stop = bar_low <= stop
        hit_target = bar_high >= target

        if hit_stop:
            gross = (stop - entry_price) / entry_price * 100.0
            return PaperTrade(
                ticker=ticker, reco_date=reco_date.isoformat(),
                status="closed_stop",
                entry_date=entry_date.isoformat(), entry_price=round(entry_price, 2),
                exit_date=bar["date"].isoformat(), exit_price=round(stop, 2),
                gross_return_pct=round(gross, 2), reco_score=score,
            )
        if hit_target:
            gross = (target - entry_price) / entry_price * 100.0
            return PaperTrade(
                ticker=ticker, reco_date=reco_date.isoformat(),
                status="closed_target",
                entry_date=entry_date.isoformat(), entry_price=round(entry_price, 2),
                exit_date=bar["date"].isoformat(), exit_price=round(target, 2),
                gross_return_pct=round(gross, 2), reco_score=score,
            )
        if hold_days >= max_hold_days:
            close_px = float(bar["close"])
            gross = (close_px - entry_price) / entry_price * 100.0
            return PaperTrade(
                ticker=ticker, reco_date=reco_date.isoformat(),
                status="closed_timeout",
                entry_date=entry_date.isoformat(), entry_price=round(entry_price, 2),
                exit_date=bar["date"].isoformat(), exit_price=round(close_px, 2),
                gross_return_pct=round(gross, 2), reco_score=score,
            )

    # 掃到底還沒觸發：open
    last = df.iloc[-1]
    mark_pct = (float(last["close"]) - entry_price) / entry_price * 100.0
    return PaperTrade(
        ticker=ticker, reco_date=reco_date.isoformat(),
        status="open",
        entry_date=entry_date.isoformat(), entry_price=round(entry_price, 2),
        gross_return_pct=round(mark_pct, 2), reco_score=score,
    )


# ─────────────────────────────────────────
# Ledger summary
# ─────────────────────────────────────────
@dataclass(frozen=True)
class LedgerSummary:
    total: int
    closed: int
    open: int
    pending: int
    expired: int
    wins: int
    losses: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    pl_ratio: float
    expectancy_pct: float


def summarize(trades: list[PaperTrade]) -> LedgerSummary:
    total = len(trades)
    closed = [t for t in trades if t.is_closed]
    open_ = [t for t in trades if t.status == "open"]
    pending = [t for t in trades if t.status == "pending"]
    expired = [t for t in trades if t.status == "expired"]
    wins = [t for t in closed if (t.gross_return_pct or 0) > 0]
    losses = [t for t in closed if (t.gross_return_pct or 0) <= 0]
    win_rate = (len(wins) / len(closed)) if closed else 0.0
    avg_win = (sum(t.gross_return_pct for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(abs(t.gross_return_pct) for t in losses) / len(losses)) if losses else 0.0
    pl = (avg_win / avg_loss) if avg_loss > 0 else float("inf")
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    return LedgerSummary(
        total=total, closed=len(closed), open=len(open_),
        pending=len(pending), expired=len(expired),
        wins=len(wins), losses=len(losses),
        win_rate=round(win_rate, 4),
        avg_win_pct=round(avg_win, 3),
        avg_loss_pct=round(avg_loss, 3),
        pl_ratio=round(pl, 3),
        expectancy_pct=round(expectancy, 3),
    )
