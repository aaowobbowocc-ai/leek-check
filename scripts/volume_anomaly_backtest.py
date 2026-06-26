"""
Volume Anomaly 6 年回測 — 找反證 + 校準閾值（Phase 18b Step 4）。

目的：**不是看賺多少**，是看「Vol Anomaly 訊號是否有預測力」。

方法：
  1. 2019-2024 月度掃描全市場
  2. 對每個觸發訊號記錄 forward return：T+5 / T+10 / T+20 / T+60 / T+252
  3. 對比 baseline：同期間隨機抽樣的同板別個股
  4. 評估假信號率：觸發後 -20% 的比例

關鍵指標：
  - 命中率：T+60 內 +30% 的比例（vs baseline）
  - 失敗率：T+60 內 -20% 的比例（vs baseline）
  - 期望值：mean(forward_return)
  - z 敏感度：分桶看不同 z 區間的表現

重要：
  - **沒做 survivorship bias 修正**（universe 是 2026 年的清單）
  - **沒模擬交易成本**（不買賣，純訊號統計）
  - 結果僅供「校準 z 閾值」+「估計訊號強度」使用，不能當作策略績效

用法：
  python scripts/volume_anomaly_backtest.py --start 2020-01-01 --end 2024-12-31
  python scripts/volume_anomaly_backtest.py --universe-limit 200   # 快速測試
"""
from __future__ import annotations

import argparse
import io
import sys
import time

# Windows cp950 fallback：強制 stdout utf-8 + line buffering
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategy.volume_anomaly import scan_volume_anomaly
from src.strategy.volume_anomaly_scanner import (
    guess_board,
    load_ohlcv_cache,
    load_universe,
)

UNIVERSE_PATH = ROOT / "config" / "universe_all.yaml"
CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)

FORWARD_WINDOWS = [5, 10, 20, 60, 252]
HIT_THRESHOLD = 30.0    # +30% 視為命中
FAIL_THRESHOLD = -20.0  # -20% 視為失敗
BASELINE_SAMPLE_SIZE = 5000


@dataclass
class TriggerOutcome:
    ticker: str
    trigger_date: date
    board: str
    z: float
    days_z_above_2: int
    score: float
    entry_close: float
    fwd_5d: float | None
    fwd_10d: float | None
    fwd_20d: float | None
    fwd_60d: float | None
    fwd_252d: float | None


def month_starts(start: date, end: date) -> list[date]:
    out = []
    cur = date(start.year, start.month, 1)
    if cur < start:
        cur = (date(cur.year + 1, 1, 1) if cur.month == 12
               else date(cur.year, cur.month + 1, 1))
    while cur <= end:
        out.append(cur)
        cur = (date(cur.year + 1, 1, 1) if cur.month == 12
               else date(cur.year, cur.month + 1, 1))
    return out


def forward_returns(
    ohlcv: pd.DataFrame, entry_date: date, windows: list[int]
) -> dict[int, float | None]:
    """從 entry_date 開始計算各 window 後的累計 return（%）。"""
    df = ohlcv[ohlcv["date"] >= entry_date].sort_values("date").reset_index(drop=True)
    if df.empty:
        return {w: None for w in windows}
    entry_price = float(df.iloc[0]["close"])
    if entry_price <= 0:
        return {w: None for w in windows}

    out = {}
    for w in windows:
        if len(df) > w:
            future_price = float(df.iloc[w]["close"])
            out[w] = (future_price / entry_price - 1.0) * 100.0
        else:
            out[w] = None
    return out


def baseline_random_sample(
    bundles: dict[str, pd.DataFrame],
    rebalances: list[date],
    sample_size: int,
    windows: list[int],
    seed: int = 42,
) -> list[dict]:
    """
    Baseline：在同期間、同 universe 隨機抽樣個股 + 進場日期。
    用於對比 Vol Anomaly 訊號是否有 alpha。
    """
    rng = np.random.default_rng(seed)
    tickers = list(bundles.keys())
    out = []
    for _ in range(sample_size):
        tk = rng.choice(tickers)
        d = rebalances[rng.integers(len(rebalances))]
        ohlcv = bundles[tk]
        # 確保進場日有資料
        df_at = ohlcv[ohlcv["date"] >= d]
        if df_at.empty or len(df_at) < max(windows) + 1:
            continue
        rets = forward_returns(ohlcv, d, windows)
        out.append({"ticker": tk, "entry_date": d, **{f"fwd_{w}d": rets[w] for w in windows}})
    return out


def run_backtest(
    start: date,
    end: date,
    score_threshold: float,
    universe_limit: int | None,
) -> tuple[list[TriggerOutcome], list[dict]]:
    universe = load_universe(UNIVERSE_PATH)
    if universe_limit:
        universe = universe[:universe_limit]

    print(f"[1/4] 載入 {len(universe)} 檔 OHLCV ...")
    bundles: dict[str, pd.DataFrame] = {}
    skipped = 0
    for i, tk in enumerate(universe, 1):
        if i % 500 == 0:
            print(f"    [{i}/{len(universe)}]")
        df = load_ohlcv_cache(tk, CACHE_YF)
        if df.empty or len(df) < 90:
            skipped += 1
            continue
        bundles[tk] = df
    print(f"    跳過 {skipped} 檔資料不足，剩 {len(bundles)} 檔")

    rebalances = month_starts(start, end)
    # 留 forward window 緩衝：超過 end - 252 天的不處理（forward 252d 不完整）
    cutoff = end - timedelta(days=max(FORWARD_WINDOWS) + 30)
    rebalances = [d for d in rebalances if d <= cutoff]

    print(f"[2/4] 月度掃描：{len(rebalances)} 個觸發點 × {len(bundles)} 檔 ...")
    outcomes: list[TriggerOutcome] = []
    t0 = time.time()
    for i, d in enumerate(rebalances, 1):
        triggered_today = 0
        for tk, ohlcv in bundles.items():
            df_history = ohlcv[ohlcv["date"] <= d]
            if len(df_history) < 90:
                continue
            try:
                sig = scan_volume_anomaly(
                    ticker=tk,
                    ohlcv=df_history,
                    as_of=d,
                    inner_outer=None,
                    market_cap_btw=None,
                    board=guess_board(tk),
                    ex_dividend_dates=None,
                    score_threshold=score_threshold,
                )
            except Exception:
                continue
            if sig is None or not sig.triggered:
                continue
            triggered_today += 1

            rets = forward_returns(ohlcv, d, FORWARD_WINDOWS)
            outcomes.append(TriggerOutcome(
                ticker=tk,
                trigger_date=d,
                board=sig.board,
                z=sig.modified_z,
                days_z_above_2=sig.days_z_above_2,
                score=sig.score,
                entry_close=sig.close,
                fwd_5d=rets[5],
                fwd_10d=rets[10],
                fwd_20d=rets[20],
                fwd_60d=rets[60],
                fwd_252d=rets[252],
            ))
        elapsed = time.time() - t0
        print(f"  [{i}/{len(rebalances)}] {d} → {triggered_today} 觸發  ({elapsed:.0f}s)")

    print(f"[3/4] Baseline 隨機抽樣 ({BASELINE_SAMPLE_SIZE} 樣本) ...")
    baseline = baseline_random_sample(
        bundles, rebalances, BASELINE_SAMPLE_SIZE, FORWARD_WINDOWS,
    )

    return outcomes, baseline


def summarize(
    outcomes: list[TriggerOutcome],
    baseline: list[dict],
    out_path: Path,
) -> None:
    """印出 Vol Anomaly vs Baseline 對比 + 寫 CSV。"""
    if not outcomes:
        print("⚠️ 沒有觸發訊號，無法統計。")
        return

    df_out = pd.DataFrame([asdict(o) for o in outcomes])
    df_bl = pd.DataFrame(baseline) if baseline else pd.DataFrame()

    print(f"\n{'='*60}")
    print(f"Vol Anomaly Backtest — 找反證")
    print(f"{'='*60}")
    print(f"觸發數：{len(df_out)}")
    print(f"Baseline 樣本：{len(df_bl)}")

    print(f"\n{'─'*60}")
    print(f"Forward Return — 中位數 / 平均 / 命中率(+30%) / 失敗率(-20%)")
    print(f"{'─'*60}")
    print(f"{'Window':<10} {'Anomaly':<35} {'Baseline':<35}")
    for w in FORWARD_WINDOWS:
        col = f"fwd_{w}d"
        anom_vals = df_out[col].dropna()
        bl_vals = df_bl[col].dropna() if not df_bl.empty and col in df_bl else pd.Series([])

        def stat_str(s: pd.Series) -> str:
            if s.empty:
                return "n=0"
            hit = (s >= HIT_THRESHOLD).sum() / len(s) * 100
            fail = (s <= FAIL_THRESHOLD).sum() / len(s) * 100
            return (f"med={s.median():+.1f}% mean={s.mean():+.1f}% "
                    f"hit={hit:.1f}% fail={fail:.1f}% n={len(s)}")

        print(f"T+{w:<3}d     {stat_str(anom_vals):<35} {stat_str(bl_vals):<35}")

    # z 分桶
    print(f"\n{'─'*60}")
    print(f"z 分桶分析（T+60d）— 用來校準閾值")
    print(f"{'─'*60}")
    df_out["z_bucket"] = pd.cut(df_out["z"], bins=[0, 2.0, 2.5, 3.0, 3.5, 4.0, 100],
                                  labels=["<2.0", "2.0-2.5", "2.5-3.0", "3.0-3.5", "3.5-4.0", ">4.0"])
    fwd60 = df_out.dropna(subset=["fwd_60d"])
    bucket_stats = fwd60.groupby("z_bucket", observed=True)["fwd_60d"].agg(
        ["count", "median", "mean",
         lambda s: (s >= HIT_THRESHOLD).sum() / len(s) * 100,
         lambda s: (s <= FAIL_THRESHOLD).sum() / len(s) * 100]
    )
    bucket_stats.columns = ["n", "median%", "mean%", "hit%", "fail%"]
    print(bucket_stats.to_string(float_format=lambda x: f"{x:.1f}"))

    # 寫 CSV
    out_path.parent.mkdir(exist_ok=True, parents=True)
    df_out.to_csv(out_path, index=False)
    print(f"\n[OK] 詳細結果已寫入 {out_path.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2020-01-01")
    parser.add_argument("--end", type=str, default="2024-12-31")
    parser.add_argument("--score-threshold", type=float, default=60.0)
    parser.add_argument("--universe-limit", type=int, default=None,
                        help="限制掃描檔數（debug 用）")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    outcomes, baseline = run_backtest(
        start=start, end=end,
        score_threshold=args.score_threshold,
        universe_limit=args.universe_limit,
    )

    out_path = LOGS_DIR / f"vol_anomaly_backtest_{start}_{end}.csv"
    summarize(outcomes, baseline, out_path)


if __name__ == "__main__":
    main()
