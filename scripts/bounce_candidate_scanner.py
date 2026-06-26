"""短線反彈候選 scanner — 找急跌 oversold 但仍站中期均線的標的.

跟 daily_signal_scanner 的 quiet_limitdown_reversal 互補:
  - quiet_limitdown: 單日跌停 -9.5% AND 量縮 (極端事件)
  - 本 scanner: 5-15 天累跌 -15~-25% AND RSI<35 (緩跌 oversold)

篩選條件 (單檔皆需 pass):
  1. 5d return ∈ [-25%, -8%]   或  10d return ∈ [-25%, -12%]
  2. RSI 14 < 35  (oversold)
  3. close > MA60 (站穩中期均線,排除崩跌型)
  4. close < MA20 (短期超賣)
  5. 量價合理 (排除無交易量殭屍)
  6. 過去 60 天 max drawdown < -25% (排除完全崩壞型)

排序: 按反彈分數 (RSI 越低 + 跌幅越大 + 站越穩 60MA = 越高)

Output:
  data/paper_trades/bounce_candidates_YYYY-MM-DD.csv  (per-day)
  Discord push (if --discord)

Run:
  python -m scripts.bounce_candidate_scanner                  # today
  python -m scripts.bounce_candidate_scanner --discord
  python -m scripts.bounce_candidate_scanner --backtest 90    # historical 90d
"""
from __future__ import annotations
import argparse, os, sys, io, time
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env", override=True)

import pandas as pd
import numpy as np
import requests

TW   = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
INST = ROOT / "data" / "cache" / "finmind" / "institutional"
OUT_DIR = ROOT / "data" / "paper_trades"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ─── Filter parameters ────────────────────────────────────────────────────────
RET_5D_MIN  = -25.0   # 5d 跌幅 min (= max negative)
RET_5D_MAX  = -8.0    # 5d 跌幅 ceiling (太小不算「急跌」)
RET_10D_MIN = -25.0
RET_10D_MAX = -12.0
RSI_THRESHOLD       = 35.0
ABOVE_MA60_REQUIRED = True   # close > MA60
BELOW_MA20_REQUIRED = True   # close < MA20
MIN_AVG_DOLLAR_VOL  = 5_000_000  # 50 萬 NT$/day, 排除殭屍股
MAX_DRAWDOWN_60D    = -35.0   # 排除崩跌幅度 > 35% 的標的
TOP_N               = 20      # show top N candidates


def load_px(tk: str) -> pd.DataFrame:
    p = TW / f"{tk}.parquet"
    if not p.exists() or p.stat().st_size < 500:
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if df.empty or len(df) < 70:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df


def compute_bounce_indicators(df: pd.DataFrame, today: date) -> dict | None:
    """For one ticker, compute all bounce indicators relative to `today`."""
    today_idx = df.index[df["date"] == today]
    if len(today_idx) == 0:
        return None
    i = today_idx[-1]
    if i < 65:  # need 60 days of history before today
        return None

    close = df["close"].astype(float)
    today_close = float(close.iloc[i])

    # Returns
    ret_5d  = (today_close / close.iloc[i - 5]  - 1) * 100 if i >= 5 else None
    ret_10d = (today_close / close.iloc[i - 10] - 1) * 100 if i >= 10 else None
    ret_15d = (today_close / close.iloc[i - 15] - 1) * 100 if i >= 15 else None

    # MAs
    ma20 = float(close.iloc[i - 19 : i + 1].mean())
    ma60 = float(close.iloc[i - 59 : i + 1].mean())

    # RSI 14
    delta = close.diff()
    gain  = delta.where(delta > 0, 0).rolling(14).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = (100 - 100 / (1 + rs)).iloc[i]
    if pd.isna(rsi):
        return None

    # Avg dollar volume (60d)
    vol = df["volume"].astype(float).iloc[i - 59 : i + 1]
    px_avg = close.iloc[i - 59 : i + 1].mean()
    avg_dollar_vol = float(vol.mean() * px_avg)

    # 60d max drawdown
    window_close = close.iloc[i - 59 : i + 1]
    running_max = window_close.cummax()
    drawdown = ((window_close - running_max) / running_max * 100).min()

    return {
        "close":          today_close,
        "ret_5d":         ret_5d,
        "ret_10d":        ret_10d,
        "ret_15d":        ret_15d,
        "ma20":           ma20,
        "ma60":           ma60,
        "ma20_dist":      (today_close / ma20 - 1) * 100,
        "ma60_dist":      (today_close / ma60 - 1) * 100,
        "rsi14":          float(rsi),
        "avg_dollar_vol": avg_dollar_vol,
        "drawdown_60d":   float(drawdown),
    }


def is_candidate(ind: dict) -> bool:
    """Apply filters."""
    if ind is None:
        return False
    # Return condition: 5d OR 10d in oversold range
    cond_5d  = (ind["ret_5d"]  is not None) and (RET_5D_MIN  <= ind["ret_5d"]  <= RET_5D_MAX)
    cond_10d = (ind["ret_10d"] is not None) and (RET_10D_MIN <= ind["ret_10d"] <= RET_10D_MAX)
    if not (cond_5d or cond_10d):
        return False
    # RSI oversold
    if ind["rsi14"] >= RSI_THRESHOLD:
        return False
    # Below MA20 (short oversold)
    if BELOW_MA20_REQUIRED and ind["close"] >= ind["ma20"]:
        return False
    # Above MA60 (mid-term still healthy)
    if ABOVE_MA60_REQUIRED and ind["close"] <= ind["ma60"]:
        return False
    # Liquidity
    if ind["avg_dollar_vol"] < MIN_AVG_DOLLAR_VOL:
        return False
    # Not in catastrophic decline
    if ind["drawdown_60d"] < MAX_DRAWDOWN_60D:
        return False
    return True


def bounce_score(ind: dict) -> float:
    """Higher = better bounce candidate.
    +RSI 越低 (oversold)
    +跌幅越大 (但不超過 -25%)
    +60MA 上方距離越大 (越不像崩跌)
    -20MA 距離過大 (太遠不易反彈)
    """
    rsi_score = max(0, (35 - ind["rsi14"])) * 2.0  # 0~70
    # Use whichever return is more negative (deeper drop)
    drop = min(ind["ret_5d"] or 0, ind["ret_10d"] or 0)
    drop_score = min(50, abs(drop) * 1.5)  # cap at 50
    ma60_score = min(20, max(0, ind["ma60_dist"]))  # 0~20
    return rsi_score + drop_score + ma60_score


def scan(today: date) -> pd.DataFrame:
    if not TW.exists():
        return pd.DataFrame()
    tks = sorted([p.stem for p in TW.glob("*.parquet")
                   if p.stem.isdigit() and len(p.stem) == 4
                   and not p.stem.startswith("00")])

    print(f"  Scanning {len(tks)} tickers for bounce setups @ {today}...")
    results = []
    for j, tk in enumerate(tks):
        if j % 500 == 0 and j > 0:
            print(f"    {j}/{len(tks)}")
        df = load_px(tk)
        if df.empty:
            continue
        ind = compute_bounce_indicators(df, today)
        if not is_candidate(ind):
            continue
        score = bounce_score(ind)
        results.append({
            "ticker":         tk,
            "close":          round(ind["close"], 2),
            "ret_5d":         round(ind["ret_5d"], 2) if ind["ret_5d"] is not None else None,
            "ret_10d":        round(ind["ret_10d"], 2) if ind["ret_10d"] is not None else None,
            "ret_15d":        round(ind["ret_15d"], 2) if ind["ret_15d"] is not None else None,
            "rsi14":          round(ind["rsi14"], 1),
            "ma20_dist":      round(ind["ma20_dist"], 2),
            "ma60_dist":      round(ind["ma60_dist"], 2),
            "drawdown_60d":   round(ind["drawdown_60d"], 1),
            "avg_dollar_vol": int(ind["avg_dollar_vol"]),
            "score":          round(score, 1),
        })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("score", ascending=False)


def push_discord(df: pd.DataFrame, scan_date: date):
    if df.empty or not DISCORD_WEBHOOK:
        return
    lines = [f"📈 **短線反彈候選** ({scan_date}, top {min(len(df), TOP_N)} / {len(df)} 檔)"]
    lines.append(f"條件: 5d 或 10d 跌 -8~-25% + RSI<35 + 站 60MA 上方")
    lines.append("")
    lines.append(f"```")
    lines.append(f"{'代號':<6} {'收盤':>8} {'5d':>7} {'10d':>7} {'RSI':>5} {'分數':>5}")
    for _, r in df.head(TOP_N).iterrows():
        r5 = f"{r['ret_5d']:+.1f}%" if r['ret_5d'] is not None else "—"
        r10 = f"{r['ret_10d']:+.1f}%" if r['ret_10d'] is not None else "—"
        lines.append(f"{r['ticker']:<6} {r['close']:>8.2f} {r5:>7} {r10:>7} {r['rsi14']:>5.1f} {r['score']:>5.1f}")
    lines.append("```")
    lines.append("**進場規則**: T+1 09:00 限價 +0.5% buy; 5-10d hold; +5% TP 鎖一半; -7% stop")
    lines.append("**警示**: 此為**未經 backtest** 的策略,僅供參考。建議 1-3 檔小量試水")
    text = "\n".join(lines)
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": text}, timeout=10)
        print(f"  ✓ Discord push {len(df.head(TOP_N))} candidates")
    except Exception as e:
        print(f"  ⚠️ Discord fail: {e}")


def _latest_available_date() -> date:
    """Find latest date in any cached parquet."""
    candidates = list(TW.glob("2330.parquet"))  # use 2330 as anchor
    if not candidates:
        return date.today()
    df = pd.read_parquet(candidates[0])
    if df.empty:
        return date.today()
    return pd.to_datetime(df["date"]).dt.date.max()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discord", action="store_true")
    ap.add_argument("--date", help="YYYY-MM-DD")
    ap.add_argument("--backtest", type=int, default=0,
                    help="Show candidate counts for last N days")
    args = ap.parse_args()

    today = (datetime.strptime(args.date, "%Y-%m-%d").date()
             if args.date else _latest_available_date())

    if args.backtest > 0:
        print(f"=== Bounce Scanner Backtest (last {args.backtest} days) ===")
        # Quick survey: how many candidates per day historically
        counts = []
        d = today
        for _ in range(args.backtest):
            if d.weekday() < 5:
                df = scan(d)
                counts.append((d.isoformat(), len(df)))
                print(f"  {d}: {len(df)} candidates")
            d -= timedelta(days=1)
        # Stats
        cnts = [c for _, c in counts]
        print(f"\n  Mean: {np.mean(cnts):.1f}/day, Median: {np.median(cnts):.0f},"
              f" Max: {max(cnts)}, Min: {min(cnts)}")
        return

    print(f"=== Bounce Candidate Scanner ({today}) ===")
    df = scan(today)
    if df.empty:
        print(f"  ✓ No candidates today")
        return

    # Save CSV
    out = OUT_DIR / f"bounce_candidates_{today}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  Found {len(df)} candidates, saved -> {out.name}")
    print(f"\n  Top {TOP_N}:")
    print(f"  {'代號':<6} {'收盤':>8} {'5d':>7} {'10d':>7} {'15d':>7} {'RSI':>5} "
          f"{'MA60d':>7} {'分數':>5}")
    for _, r in df.head(TOP_N).iterrows():
        r5  = f"{r['ret_5d']:+.1f}%" if r['ret_5d'] is not None else "—"
        r10 = f"{r['ret_10d']:+.1f}%" if r['ret_10d'] is not None else "—"
        r15 = f"{r['ret_15d']:+.1f}%" if r['ret_15d'] is not None else "—"
        print(f"  {r['ticker']:<6} {r['close']:>8.2f} {r5:>7} {r10:>7} {r15:>7} "
              f"{r['rsi14']:>5.1f} {r['ma60_dist']:>+6.1f}% {r['score']:>5.1f}")

    if args.discord:
        push_discord(df, today)


if __name__ == "__main__":
    main()
