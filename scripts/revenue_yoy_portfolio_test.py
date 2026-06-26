"""
Revenue YoY Portfolio Test (extends portfolio_capacity_constraint findings)

問題: quiet_limit portfolio 全輸 0050，根因是 crash-day clustering 把「alpha」
     轉成 market beta。Revenue YoY 是月公告訊號（每月 11-21 日散布），
     不會在單日聚集 → 理論上 selection bias 應該小很多。

測試:
  - Revenue YoY raw > 30%（簡化版）
  - Hold 60d
  - 5/10/20 slots × low_vr ... 不適用（Revenue YoY 沒 vol_ratio）
  - 改用 priority: by yoy desc（最高 YoY 優先）
  - 比對 0050 同期

如果 Revenue YoY portfolio 贏 0050 → INVEST 仍有真 alpha 路線
如果也輸 → 確認 TW long-only stock picking 全面 dead，定位改為 0050 + 警報
"""
from __future__ import annotations

import io
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.portfolio_level_backtest import compute_daily_nav

REV_DIR = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
COST = 0.78
HOLD = 60
YOY_THRESHOLD = 30.0
PRIOR_REV_MIN = 1e7
START_DATE = "2020-01-01"
END_DATE = "2025-12-31"

CACHE = ROOT / "data" / "cache" / "revenue_yoy_events.parquet"


def collect_events() -> pd.DataFrame:
    if CACHE.exists():
        print(f"  載入 cached events from {CACHE.relative_to(ROOT)}")
        return pd.read_parquet(CACHE)

    print(f"  計算 Revenue YoY events ({START_DATE} – {END_DATE})...")
    records = []
    files = list(REV_DIR.glob("TaiwanStockMonthRevenue_*.parquet"))
    start_dt = pd.to_datetime(START_DATE).date()
    end_dt = pd.to_datetime(END_DATE).date()

    for i, p in enumerate(files):
        tk = p.stem.replace("TaiwanStockMonthRevenue_", "")
        if not tk.isdigit() or len(tk) != 4:
            continue

        try:
            rev = pd.read_parquet(p)
        except Exception:
            continue
        if len(rev) < 24:
            continue

        rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
        rev["prior_revenue"] = rev["revenue"].shift(12)
        rev["yoy"] = (rev["revenue"] / rev["prior_revenue"] - 1) * 100

        px_path = TW_CACHE / f"{tk}.parquet"
        if not px_path.exists():
            continue
        try:
            px = pd.read_parquet(px_path)
        except Exception:
            continue
        if len(px) < HOLD + 65 or "open" not in px.columns:
            continue
        px["date"] = pd.to_datetime(px["date"]).dt.date
        px = px.sort_values("date").reset_index(drop=True)

        for ridx in range(12, len(rev)):
            row = rev.iloc[ridx]
            yoy = float(row["yoy"]) if pd.notna(row["yoy"]) else float("nan")
            prior = float(row.get("prior_revenue", 0) or 0)
            if pd.isna(yoy) or yoy < YOY_THRESHOLD or abs(yoy) > 500:
                continue
            if prior < PRIOR_REV_MIN:
                continue

            period_start = pd.to_datetime(row["date"]).date()
            ct = row.get("create_time", None)
            if ct and pd.notna(ct) and ct != "":
                try:
                    ann_dt = pd.to_datetime(ct).date()
                except Exception:
                    ann_dt = period_start + timedelta(days=14)
            else:
                ann_dt = period_start + timedelta(days=14)

            if ann_dt < start_dt or ann_dt > end_dt:
                continue

            px_after = px.index[px["date"] >= ann_dt]
            if len(px_after) == 0:
                continue
            ti = int(px_after[0])
            if ti + 1 >= len(px) or ti + HOLD >= len(px):
                continue

            entry = float(px.iloc[ti + 1]["open"])
            if entry <= 0:
                continue
            exit_p = float(px.iloc[ti + HOLD]["close"])
            fwd = (exit_p / entry - 1) * 100 - COST

            records.append({
                "ticker": tk,
                "signal_date": ann_dt,
                "exit_date": px.iloc[ti + HOLD]["date"],
                "fwd_return": fwd,
                "yoy": yoy,
            })

        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(files)}] events={len(records)}")

    df = pd.DataFrame(records).sort_values("signal_date").reset_index(drop=True)
    if not df.empty:
        df["signal_date"] = pd.to_datetime(df["signal_date"])
        df["exit_date"] = pd.to_datetime(df["exit_date"])
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(CACHE)
    return df


def simulate(events: pd.DataFrame, max_pos: int, priority: str = "yoy_desc") -> dict:
    events = events.copy()
    events["signal_date"] = pd.to_datetime(events["signal_date"]).dt.date
    events["exit_date"] = pd.to_datetime(events["exit_date"]).dt.date

    if priority == "yoy_desc":
        events = events.sort_values(["signal_date", "yoy"], ascending=[True, False])
    elif priority == "yoy_asc":
        events = events.sort_values(["signal_date", "yoy"], ascending=[True, True])
    else:
        events = events.sort_values(["signal_date"])
    events = events.reset_index(drop=True)

    signal_dates = sorted(events["signal_date"].unique())
    open_positions: list = []
    executed_idx: list = []
    skipped_cap_idx: list = []

    for dt in signal_dates:
        open_positions = [p for p in open_positions if p["exit_date"] > dt]
        today_idx = events.index[events["signal_date"] == dt]
        open_tickers = {p["ticker"] for p in open_positions}

        for ri in today_idx:
            sig = events.iloc[ri]
            if sig["ticker"] in open_tickers:
                continue
            if len(open_positions) >= max_pos:
                skipped_cap_idx.append(ri)
                continue
            open_positions.append({
                "ticker": sig["ticker"],
                "exit_date": sig["exit_date"],
            })
            open_tickers.add(sig["ticker"])
            executed_idx.append(ri)

    executed = events.iloc[executed_idx] if executed_idx else pd.DataFrame()
    nav = compute_daily_nav(executed, max_pos) if len(executed) > 0 else pd.DataFrame()

    if nav.empty:
        return {"cagr": 0, "dd": 0, "n_exec": 0, "n_skip": len(skipped_cap_idx),
                "exec_alpha": 0, "skip_alpha": 0}

    yrs = (pd.to_datetime(nav["date"].iloc[-1]) - pd.to_datetime(nav["date"].iloc[0])).days / 365.25
    cagr = (nav["nav"].iloc[-1] / nav["nav"].iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0
    rmax = nav["nav"].cummax()
    dd = (nav["nav"] / rmax - 1).min()

    exec_alpha = float(executed["fwd_return"].mean()) if len(executed) > 0 else 0
    skip_alpha = (
        float(events.iloc[skipped_cap_idx]["fwd_return"].mean())
        if skipped_cap_idx else 0
    )

    return {
        "cagr": cagr,
        "dd": dd,
        "n_exec": len(executed_idx),
        "n_skip": len(skipped_cap_idx),
        "exec_alpha": exec_alpha,
        "skip_alpha": skip_alpha,
    }


def main():
    print("=" * 78)
    print("  Revenue YoY Portfolio Test")
    print("  Baseline: 0050 CAGR +21.7%/yr  Max DD -33.8% (2020-2025)")
    print("=" * 78)

    events = collect_events()
    if events.empty:
        print("  ❌ 無事件")
        return

    print(f"\n  Events 期間: {events['signal_date'].min()} ~ {events['signal_date'].max()}")
    print(f"  Total events: {len(events):,}")

    # Daily clustering
    spd = events.groupby(events["signal_date"].dt.date).size()
    print(f"  日均訊號: {spd.mean():.1f}  中位: {spd.median():.0f}  最高: {spd.max()}")
    n_high = (spd > 5).sum()
    n_high_20 = (spd > 20).sum()
    print(f"  > 5 訊號的日: {n_high} 天  > 20: {n_high_20} 天  "
          f"（vs quiet_limit: 245 天 > 5）")

    # Naive alpha
    naive = events["fwd_return"].mean()
    print(f"\n  Naive alpha (no portfolio constraint): {naive:+.2f}%/trade")

    print(f"\n  {'config':<24} {'CAGR':>8} {'MaxDD':>8} {'n_exec':>7} {'n_skip':>8} "
          f"{'exec_α':>8} {'skip_α':>8} {'vs 0050':>9}")
    print(f"  {'-'*24} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*9}")

    BASELINE = 0.217
    rows = []
    for max_pos in [5, 10, 20, 50]:
        for priority in ["yoy_desc", "yoy_asc"]:
            r = simulate(events, max_pos, priority)
            beats = r["cagr"] > BASELINE
            marker = "✅" if beats else "❌"
            label = f"max={max_pos:>2} {priority}"
            print(f"  {label:<24} "
                  f"{r['cagr']*100:>+7.1f}% "
                  f"{r['dd']*100:>+7.1f}% "
                  f"{r['n_exec']:>7,} "
                  f"{r['n_skip']:>8,} "
                  f"{r['exec_alpha']:>+7.2f}% "
                  f"{r['skip_alpha']:>+7.2f}% "
                  f"{(r['cagr']-BASELINE)*100:>+7.1f}pp{marker}")
            rows.append({"config": label, **r})

    df_sweep = pd.DataFrame(rows)
    out = ROOT / "logs" / "revenue_yoy_portfolio_sweep.csv"
    out.parent.mkdir(exist_ok=True)
    df_sweep.to_csv(out, index=False)
    print(f"\n  ✅ 寫入 {out.relative_to(ROOT)}")

    best = df_sweep.loc[df_sweep["cagr"].idxmax()]
    print(f"\n  🏆 Best: {best['config']}  CAGR={best['cagr']*100:+.1f}%  "
          f"vs 0050 {(best['cagr']-BASELINE)*100:+.1f}pp")

    if best["cagr"] >= BASELINE:
        print(f"\n  ✅ Revenue YoY 在 portfolio 仍贏 0050 — INVEST 真 alpha 路線確認")
    elif best["cagr"] >= BASELINE - 0.03:
        print(f"\n  ⚠️ 接近 0050 但未超越（差 < 3pp）")
    else:
        print(f"\n  🚨 Revenue YoY portfolio 也輸 0050 — TW long-only 全面 dead")
        print(f"     INVEST 應重新定位為「DCA 0050 + 信號警報 only」")


if __name__ == "__main__":
    main()
