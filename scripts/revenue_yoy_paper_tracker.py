"""
Revenue YoY Paper Trade Tracker

每日 (cron 排程或手動) 跑一次:
  1. 掃 scanner_hits.csv 找新的 revenue_relative_yoy + deploy_ready 訊號
  2. 記錄 paper position: entry @ 隔日 open, hold 60d
  3. 檢查既有 open positions 是否到 exit date
  4. 計算實際 return + 滑價分析
  5. 寫入 revenue_yoy_paper.csv

驗證目標:
  6-8 週 paper trade 後對比 backtest 預期 +25.7%/yr
  Slippage = 實際進場價 vs 訊號日 close 的偏差
"""
from __future__ import annotations

import io
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
SCANNER_HITS = ROOT / "data" / "paper_trades" / "scanner_hits.csv"
PAPER_LOG = ROOT / "data" / "paper_trades" / "revenue_yoy_paper.csv"

HOLD_DAYS = 60
COST_TOTAL = 0.78  # round-trip


def load_px(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def get_next_trading_day(tk: str, after: date) -> tuple[pd.Timestamp, float] | None:
    """Find the first trading day strictly after `after`, return (date, open)"""
    px = load_px(tk)
    if px.empty:
        return None
    after_ts = pd.Timestamp(after)
    sub = px[px["date"] > after_ts]
    if sub.empty:
        return None
    row = sub.iloc[0]
    return (row["date"], float(row["open"]))


def get_close_on_or_before(tk: str, target: date) -> tuple[pd.Timestamp, float] | None:
    px = load_px(tk)
    if px.empty:
        return None
    target_ts = pd.Timestamp(target)
    sub = px[px["date"] <= target_ts]
    if sub.empty:
        return None
    row = sub.iloc[-1]
    return (row["date"], float(row["close"]))


def load_paper() -> pd.DataFrame:
    if not PAPER_LOG.exists():
        return pd.DataFrame(columns=[
            "ticker", "signal_date", "entry_date", "entry_price",
            "scheduled_exit_date", "actual_exit_date", "exit_price",
            "yoy_pct", "avg_dv_yi", "status",
            "gross_pct", "net_pct", "vs_0050_alpha", "notes",
        ])
    return pd.read_csv(PAPER_LOG)


def save_paper(df: pd.DataFrame):
    PAPER_LOG.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(PAPER_LOG, index=False, encoding="utf-8-sig")


def detect_new_signals(today: date) -> list[dict]:
    """Read scanner_hits.csv 抓今天新觸發的 revenue_relative_yoy + deploy_ready"""
    if not SCANNER_HITS.exists():
        return []
    try:
        df = pd.read_csv(SCANNER_HITS)
    except Exception:
        return []
    if df.empty:
        return []
    df["scan_date"] = pd.to_datetime(df["scan_date"]).dt.date
    today_hits = df[
        (df["scan_date"] == today)
        & (df["signal"] == "revenue_relative_yoy")
    ]
    # Filter for deploy_ready=True (handle both bool and string)
    if "deploy_ready" in today_hits.columns:
        today_hits = today_hits[
            today_hits["deploy_ready"].astype(str).str.lower().isin(["true", "1"])
        ]
    return today_hits.to_dict("records")


def open_new_positions(today: date, paper: pd.DataFrame) -> pd.DataFrame:
    new_signals = detect_new_signals(today)
    if not new_signals:
        return paper

    existing_open = paper[paper["status"] == "open"]
    existing_open_keys = set(zip(
        existing_open["ticker"].astype(str),
        existing_open["signal_date"].astype(str),
    ))

    rows = []
    for sig in new_signals:
        tk = str(sig["ticker"])
        sig_dt = pd.to_datetime(sig["scan_date"]).date()
        key = (tk, str(sig_dt))
        if key in existing_open_keys:
            continue

        # Entry: next trading day open
        entry = get_next_trading_day(tk, sig_dt)
        if entry is None:
            continue
        entry_dt, entry_px = entry
        scheduled_exit = entry_dt.date() + timedelta(days=HOLD_DAYS * 1.4)
        # Approximate calendar days; actual will pick nearest trading day at exit

        rows.append({
            "ticker": tk,
            "signal_date": str(sig_dt),
            "entry_date": entry_dt.date().isoformat() if not pd.isna(entry_dt) else "",
            "entry_price": entry_px,
            "scheduled_exit_date": scheduled_exit.isoformat(),
            "actual_exit_date": "",
            "exit_price": 0.0,
            "yoy_pct": float(sig.get("yoy_pct", 0)),
            "avg_dv_yi": float(sig.get("avg_dv_60d_yi", 0)),
            "status": "open",
            "gross_pct": 0.0,
            "net_pct": 0.0,
            "vs_0050_alpha": 0.0,
            "notes": f"deploy_ready, tier={sig.get('yoy_tier', '')}",
        })

    if rows:
        new_df = pd.DataFrame(rows)
        paper = pd.concat([paper, new_df], ignore_index=True)
        print(f"  ✅ 開新 paper positions: {len(rows)} 檔")
        for r in rows:
            print(f"    {r['ticker']}: entry {r['entry_date']} @ {r['entry_price']:.2f}, "
                  f"YoY +{r['yoy_pct']}%, {r['avg_dv_yi']}億/日")
    return paper


def close_due_positions(today: date, paper: pd.DataFrame) -> pd.DataFrame:
    open_pos = paper[paper["status"] == "open"]
    if open_pos.empty:
        return paper

    today_ts = pd.Timestamp(today)
    closed_count = 0
    for idx, row in open_pos.iterrows():
        entry_dt = pd.to_datetime(row["entry_date"])
        if pd.isna(entry_dt):
            continue
        # Close after 60 trading days roughly (use calendar days × 1.4)
        target_exit_dt = entry_dt + pd.Timedelta(days=int(HOLD_DAYS * 1.4))
        if today_ts < target_exit_dt:
            continue

        # Find actual exit price (close on target_exit_dt or before)
        tk = str(row["ticker"])
        exit_info = get_close_on_or_before(tk, target_exit_dt.date())
        if exit_info is None:
            continue
        actual_exit_dt, exit_px = exit_info

        # Calculate net return
        entry_px = float(row["entry_price"])
        if entry_px <= 0:
            continue
        gross = (exit_px / entry_px - 1) * 100
        net = gross - COST_TOTAL

        # 0050 same-period return for alpha calc
        etf_entry = get_close_on_or_before("0050", entry_dt.date())
        etf_exit = get_close_on_or_before("0050", actual_exit_dt.date())
        if etf_entry and etf_exit:
            etf_ret = (etf_exit[1] / etf_entry[1] - 1) * 100
            alpha = net - etf_ret
        else:
            alpha = 0.0

        paper.at[idx, "actual_exit_date"] = actual_exit_dt.date().isoformat()
        paper.at[idx, "exit_price"] = exit_px
        paper.at[idx, "gross_pct"] = round(gross, 2)
        paper.at[idx, "net_pct"] = round(net, 2)
        paper.at[idx, "vs_0050_alpha"] = round(alpha, 2)
        paper.at[idx, "status"] = "closed"
        closed_count += 1
        print(f"  📤 平倉 {tk}: gross {gross:+.2f}%, net {net:+.2f}%, alpha vs 0050 {alpha:+.2f}pp")

    if closed_count == 0 and len(open_pos) > 0:
        print(f"  ⏸️ 無到期 positions（{len(open_pos)} 檔仍 open）")
    return paper


def status_report(paper: pd.DataFrame):
    if paper.empty:
        print("\n  📊 無 paper trade 紀錄")
        return
    open_pos = paper[paper["status"] == "open"]
    closed_pos = paper[paper["status"] == "closed"]
    print(f"\n  === Paper Trade Status ===")
    print(f"  Open positions: {len(open_pos)}")
    print(f"  Closed positions: {len(closed_pos)}")
    if not closed_pos.empty:
        net = pd.to_numeric(closed_pos["net_pct"], errors="coerce")
        alpha = pd.to_numeric(closed_pos["vs_0050_alpha"], errors="coerce")
        print(f"  Mean net return: {net.mean():+.2f}%")
        print(f"  Mean alpha vs 0050: {alpha.mean():+.2f}pp")
        print(f"  Win rate: {(net > 0).mean()*100:.1f}%")
        print(f"  Cumulative net: {net.apply(lambda x: 1+x/100).prod()*100-100:+.1f}%")
    if not open_pos.empty:
        print(f"\n  Open positions list:")
        for _, row in open_pos.iterrows():
            print(f"    {row['ticker']}: entry {row['entry_date']} @ {row['entry_price']:.2f}, "
                  f"target exit {row['scheduled_exit_date']}, YoY +{row['yoy_pct']}%")


def main():
    today = date.today()
    print("=" * 70)
    print(f"  Revenue YoY Paper Trade Tracker — {today}")
    print("=" * 70)

    paper = load_paper()
    print(f"\n  Loaded {len(paper)} historical records")

    # 1. Open new positions from today's signals
    paper = open_new_positions(today, paper)

    # 2. Close any positions that hit exit date
    paper = close_due_positions(today, paper)

    # 3. Status report
    status_report(paper)

    # 4. Save
    save_paper(paper)
    print(f"\n  ✅ 寫入 {PAPER_LOG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
