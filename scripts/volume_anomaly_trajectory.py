"""
Volume Anomaly 軌跡分析（Phase 18b 出場邏輯校準）。

目的：對 v2 回測的 56 個觸發樣本，記錄每筆從進場後第 1~252 個交易日的
完整累計報酬，找出「最佳出場時機」與「動態 stop 應設多寬」。

回答三個關鍵問題：
  1. 60 天是真甜蜜點嗎？還是 30 / 90 / 120 天更好？
  2. Trailing stop 應該抓多寬（從高點回撤多少 % 出場）？
  3. 「假信號」（始終未漲 +5%）的早期辨識：T+幾天就能判斷？

輸出：
  - logs/vol_anomaly_trajectories.csv —— wide-format，rows=trade、cols=day_1…day_252
  - 終端機列印統計摘要
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Windows cp950 fallback
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategy.volume_anomaly_scanner import load_ohlcv_cache

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
INPUT_CSV = ROOT / "logs" / "vol_anomaly_backtest_2020-01-01_2024-12-31.csv"
OUTPUT_CSV = ROOT / "logs" / "vol_anomaly_trajectories.csv"
MAX_DAYS = 1500    # 約 6 年，看完整週期能持續多久


def build_trajectory(
    ticker: str, trigger_date: date, ohlcv: pd.DataFrame, max_days: int = MAX_DAYS,
) -> dict[int, float] | None:
    """從 trigger_date 起算每日累計 return（%）。"""
    df = ohlcv[ohlcv["date"] >= trigger_date].sort_values("date").reset_index(drop=True)
    if df.empty or len(df) < 2:
        return None
    entry = float(df.iloc[0]["close"])
    if entry <= 0:
        return None
    out = {}
    for i in range(1, min(max_days + 1, len(df))):
        out[i] = (float(df.iloc[i]["close"]) / entry - 1.0) * 100.0
    return out


def main() -> None:
    if not INPUT_CSV.exists():
        print(f"❌ 找不到 {INPUT_CSV}")
        return

    triggers = pd.read_csv(INPUT_CSV)
    triggers["trigger_date"] = pd.to_datetime(triggers["trigger_date"]).dt.date
    print(f"[1/3] 載入 {len(triggers)} 個觸發樣本")

    rows = []
    for _, t in triggers.iterrows():
        ohlcv = load_ohlcv_cache(str(t["ticker"]), CACHE_YF)
        if ohlcv.empty:
            continue
        traj = build_trajectory(str(t["ticker"]), t["trigger_date"], ohlcv)
        if traj is None:
            continue
        row = {"ticker": t["ticker"], "trigger_date": t["trigger_date"],
               "z": t["z"], "entry_close": t["entry_close"]}
        row.update({f"d{i}": v for i, v in traj.items()})
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"[2/3] 軌跡寫入 {OUTPUT_CSV.relative_to(ROOT)}: {len(df)} 筆")

    # ── 分析 ──
    print(f"\n[3/3] 軌跡統計分析")
    day_cols = [c for c in df.columns if c.startswith("d") and c[1:].isdigit()]
    day_cols.sort(key=lambda c: int(c[1:]))

    # 每日中位 / 平均 / 命中率
    print(f"\n{'='*70}")
    print(f"{'Day':<6} {'median%':<10} {'mean%':<10} {'hit≥15%':<10} {'hit≥30%':<10} {'fail≤-10%':<10}")
    print(f"{'-'*70}")
    checkpoints = [5, 10, 20, 30, 60, 100, 150, 252, 378, 504, 756, 1008, 1260]
    for d in checkpoints:
        col = f"d{d}"
        if col not in df.columns:
            continue
        s = df[col].dropna()
        if s.empty:
            continue
        median = s.median()
        mean = s.mean()
        hit15 = (s >= 15).sum() / len(s) * 100
        hit30 = (s >= 30).sum() / len(s) * 100
        fail = (s <= -10).sum() / len(s) * 100
        print(f"T+{d:<4} {median:>+8.1f}  {mean:>+8.1f}  {hit15:>7.1f}%  "
              f"{hit30:>7.1f}%  {fail:>7.1f}%   n={len(s)}")

    # 達到 +15% 的天數分佈
    print(f"\n{'='*70}")
    print(f"達到 +15% / +30% 的最早天數分佈（追蹤 trade-by-trade）")
    print(f"{'='*70}")

    hit15_days = []
    hit30_days = []
    never_hit5 = 0
    for _, r in df.iterrows():
        traj_vals = [(int(c[1:]), r[c]) for c in day_cols if pd.notna(r[c])]
        traj_vals.sort()
        hit15_d = next((d for d, v in traj_vals if v >= 15), None)
        hit30_d = next((d for d, v in traj_vals if v >= 30), None)
        ever_hit5 = any(v >= 5 for _, v in traj_vals)
        if hit15_d:
            hit15_days.append(hit15_d)
        if hit30_d:
            hit30_days.append(hit30_d)
        if not ever_hit5:
            never_hit5 += 1

    if hit15_days:
        s = pd.Series(hit15_days)
        print(f"+15% 達到：{len(s)}/{len(df)} ({len(s)/len(df)*100:.0f}%)，"
              f"中位數 {s.median():.0f} 日 / 平均 {s.mean():.0f} 日 / "
              f"P25={s.quantile(0.25):.0f} / P75={s.quantile(0.75):.0f}")
    if hit30_days:
        s = pd.Series(hit30_days)
        print(f"+30% 達到：{len(s)}/{len(df)} ({len(s)/len(df)*100:.0f}%)，"
              f"中位數 {s.median():.0f} 日 / 平均 {s.mean():.0f} 日 / "
              f"P25={s.quantile(0.25):.0f} / P75={s.quantile(0.75):.0f}")
    print(f"從未漲超過 +5%（疑似假信號）：{never_hit5}/{len(df)} "
          f"({never_hit5/len(df)*100:.0f}%)")

    # 最大回撤 / 高點 / 高點後回吐
    print(f"\n{'='*70}")
    print(f"風險與最佳出場分析")
    print(f"{'='*70}")

    max_drawdowns_from_entry = []
    max_drawdowns_from_peak = []
    peak_days = []
    days_to_peak = []
    pullback_after_peak_pct = []

    for _, r in df.iterrows():
        traj_vals = [(int(c[1:]), r[c]) for c in day_cols if pd.notna(r[c])]
        if not traj_vals:
            continue
        traj_vals.sort()
        days = [d for d, _ in traj_vals]
        vals = [v for _, v in traj_vals]
        # 最大回撤從 entry
        min_val = min(vals)
        max_drawdowns_from_entry.append(min_val)
        # 高點
        max_idx = int(np.argmax(vals))
        max_val = vals[max_idx]
        peak_day = days[max_idx]
        peak_days.append(peak_day)
        if max_val >= 5:   # 只計算「真的有起來」的
            days_to_peak.append(peak_day)
            # 高點後最大回吐
            after_peak = vals[max_idx:]
            if len(after_peak) > 1:
                pullback = max_val - min(after_peak)
                pullback_after_peak_pct.append(pullback)
                # 從高點回撤的幅度（相對 entry）
                max_drawdowns_from_peak.append(min(after_peak) - max_val)

    s_dd = pd.Series(max_drawdowns_from_entry)
    print(f"最大回撤（vs entry）：中位 {s_dd.median():.1f}% / "
          f"P25 {s_dd.quantile(0.25):.1f}% / P10 {s_dd.quantile(0.10):.1f}% / "
          f"最壞 {s_dd.min():.1f}%")
    if days_to_peak:
        s = pd.Series(days_to_peak)
        print(f"高點到達日（>=+5% 樣本）：中位 {s.median():.0f} 日 / "
              f"平均 {s.mean():.0f} 日 / P25={s.quantile(0.25):.0f} / "
              f"P75={s.quantile(0.75):.0f}")
    if pullback_after_peak_pct:
        s = pd.Series(pullback_after_peak_pct)
        print(f"高點後最大回吐：中位 {s.median():.1f}pp / "
              f"P75 {s.quantile(0.75):.1f}pp / P90 {s.quantile(0.90):.1f}pp")

    # 模擬不同出場策略
    print(f"\n{'='*70}")
    print(f"出場策略模擬（基於 56 筆樣本，含 200MA 結構停損版本）")
    print(f"{'='*70}")

    # ── 重新載入 OHLCV + 200MA 給策略模擬 ──
    samples_full: list[dict] = []
    for _, t in triggers.iterrows():
        ohlcv = load_ohlcv_cache(str(t["ticker"]), CACHE_YF)
        if ohlcv.empty:
            continue
        oh = ohlcv.sort_values("date").reset_index(drop=True).copy()
        oh["ma200"] = oh["close"].rolling(200).mean()
        after = oh[oh["date"] >= t["trigger_date"]].reset_index(drop=True)
        if len(after) < 2:
            continue
        entry = float(after.iloc[0]["close"])
        if entry <= 0:
            continue
        # 整理成 list of (day, close, ma200, return_pct)
        path = []
        for i in range(1, len(after)):
            c = float(after.iloc[i]["close"])
            ma = after.iloc[i]["ma200"]
            ma_val = float(ma) if pd.notna(ma) else None
            path.append({
                "day": i,
                "close": c,
                "ma200": ma_val,
                "ret_pct": (c / entry - 1.0) * 100.0,
            })
        samples_full.append({
            "ticker": str(t["ticker"]),
            "trigger_date": t["trigger_date"],
            "entry": entry,
            "path": path,
        })

    def simulate_hold_n(sample, n_days: int) -> float:
        path = sample["path"]
        for p in path:
            if p["day"] >= n_days:
                return p["ret_pct"]
        return path[-1]["ret_pct"]

    def simulate_trailing(sample, trail_pp: float, min_peak: float = 5.0) -> float:
        peak = 0.0
        for p in sample["path"]:
            if p["ret_pct"] > peak:
                peak = p["ret_pct"]
            if peak >= min_peak and (peak - p["ret_pct"]) >= trail_pp:
                return p["ret_pct"]
        return sample["path"][-1]["ret_pct"]

    def simulate_combo_c(
        sample,
        min_hold_days: int = 252,
        ma_pct_below: float = 0.95,
        trailing_threshold: float = 30.0,
        trailing_pp: float = 25.0,
        max_hold: int = 1500,
    ) -> float:
        """
        C 組合：持滿 ≥ min_hold_days + 跌破 200MA × ma_pct_below 隨時出場
              + 過 min_hold_days 後若高點 > trailing_threshold% 啟動 trailing -trailing_pp pp
        """
        peak = 0.0
        for p in sample["path"]:
            if p["day"] > max_hold:
                return p["ret_pct"]
            # peak 持續更新
            if p["ret_pct"] > peak:
                peak = p["ret_pct"]
            # 結構性停損（隨時生效）
            if p["ma200"] is not None and p["close"] < p["ma200"] * ma_pct_below:
                return p["ret_pct"]
            # min_hold_days 後啟動 trailing（前提：曾達 trailing_threshold）
            if p["day"] >= min_hold_days and peak >= trailing_threshold:
                if (peak - p["ret_pct"]) >= trailing_pp:
                    return p["ret_pct"]
        return sample["path"][-1]["ret_pct"]

    def simulate_combo_c_no_min_hold(sample, **kwargs) -> float:
        """C 變種：移除 min_hold，只看 200MA + 高點 trailing。"""
        return simulate_combo_c(sample, min_hold_days=0, **kwargs)

    def simulate_ma_only(sample, ma_pct_below: float = 0.95, max_hold: int = 1500) -> float:
        for p in sample["path"]:
            if p["day"] > max_hold:
                return p["ret_pct"]
            if p["ma200"] is not None and p["close"] < p["ma200"] * ma_pct_below:
                return p["ret_pct"]
        return sample["path"][-1]["ret_pct"]

    strategies = [
        ("持滿 60 天",                           lambda s: simulate_hold_n(s, 60)),
        ("持滿 252 天 (1y)",                     lambda s: simulate_hold_n(s, 252)),
        ("持滿 756 天 (3y)",                     lambda s: simulate_hold_n(s, 756)),
        ("Trailing -20pp from peak",             lambda s: simulate_trailing(s, 20)),
        ("Trailing -25pp from peak",             lambda s: simulate_trailing(s, 25)),
        ("MA200×0.95 only (跌破出場)",           lambda s: simulate_ma_only(s, 0.95)),
        ("C: 252d hold + MA + +30 後 -25pp",     simulate_combo_c),
        ("C2: 252d hold + MA × 0.92 + -25pp",    lambda s: simulate_combo_c(s, ma_pct_below=0.92)),
        ("C3: 252d hold + MA × 0.95 + -20pp",    lambda s: simulate_combo_c(s, trailing_pp=20)),
        ("C4: 504d hold + MA × 0.95 + -25pp",    lambda s: simulate_combo_c(s, min_hold_days=504)),
        ("C5: no min hold + MA + -25pp",          simulate_combo_c_no_min_hold),
    ]

    print(f"{'Strategy':<48} {'mean%':<9} {'median%':<10} {'hit≥15%':<9} {'fail≤-10':<9}")
    print(f"{'-'*87}")
    for name, fn in strategies:
        results = []
        for sample in samples_full:
            try:
                results.append(fn(sample))
            except Exception:
                continue
        if not results:
            continue
        ss = pd.Series(results)
        hit = (ss >= 15).sum() / len(ss) * 100
        fail = (ss <= -10).sum() / len(ss) * 100
        print(f"{name:<48} {ss.mean():>+7.1f}  {ss.median():>+8.1f}  "
              f"{hit:>6.1f}%  {fail:>6.1f}%")


if __name__ == "__main__":
    main()
