"""盤中 13:25 量縮跌停掃描 → Discord 推播

Strategy: scan_signal_5 (量縮跌停反彈) 的 intraday 早期偵測。

Why 13:25? TW market 09:00-13:30. By 13:25 ~95% of daily volume is set, so
projected vol_ratio is reliable. Earlier than 13:00 has too much noise.

Watchlist: 最近 90 日有過 scanner_hits 的 ticker + 你的持股 + ORB whitelist
           (限制 universe 才能在 5 分鐘內掃完)

Flow:
  1. yfinance bulk download (period=1d, interval=1m) for watchlist
  2. For each: compute today_pct (vs prev close), projected_vr
  3. Match: pct ≤ -9% AND projected_vr < 0.8
  4. Push Discord with limit-buy suggestion for T+1 09:00

Run modes:
  python -m scripts.intraday_quiet_limitdown_scanner               # 一次掃描
  python -m scripts.intraday_quiet_limitdown_scanner --watch       # 跑到 13:25 結束
"""
from __future__ import annotations
import argparse, os, sys, io, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env", override=True)

import pandas as pd
import yfinance as yf
import requests

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
SCANNER_HITS = ROOT / "data" / "paper_trades" / "scanner_hits.csv"

# Limit-down threshold (TW market 10% daily limit; trigger at -9%)
LIMITDOWN_PCT = -9.0
VR_THRESHOLD  = 0.8
DAYS_FOR_VOL_AVG = 20  # use 20d (intraday lookback we can fetch easily)

TW_TZ = timezone(timedelta(hours=8))


def build_watchlist() -> list[str]:
    """Recent scanner hits + holdings + ORB whitelist."""
    watch = set()
    # Recent scanner hits (last 90 days)
    if SCANNER_HITS.exists():
        try:
            df = pd.read_csv(SCANNER_HITS)
            cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            df = df[df["scan_date"] >= cutoff]
            watch.update(df["ticker"].astype(str).unique().tolist())
        except Exception:
            pass
    # Holdings
    try:
        import json
        with open(ROOT / "data" / "assets.json") as f:
            a = json.load(f)
        for h in a["holdings"]["long_term"]:
            watch.add(str(h["ticker"]))
    except Exception:
        pass
    # ORB whitelist
    watch.update(["2408", "2485"])

    # Limit to TW listed format (4-digit numerics)
    return sorted([t for t in watch if t.isdigit() and len(t) == 4])


def fetch_intraday_pct_vr(tickers: list[str]) -> pd.DataFrame:
    """Fetch intraday metrics for each ticker. Returns DataFrame with:
    ticker, last_price, prev_close, pct, today_vol, vol_20d_avg, projected_vr.

    Uses yfinance bulk download for speed.
    """
    rows = []
    # yfinance handles TW with .TW suffix
    yf_tickers = [f"{t}.TW" for t in tickers]
    # Bulk get latest 30 days daily for prev close + vol_20d_avg
    print(f"  Fetching daily bars for {len(yf_tickers)} tickers (bulk)...")
    daily = yf.download(yf_tickers, period="60d", interval="1d",
                        group_by="ticker", auto_adjust=False, progress=False,
                        threads=True)
    print(f"  Fetching latest 1m bars (bulk)...")
    intra = yf.download(yf_tickers, period="1d", interval="1m",
                        group_by="ticker", auto_adjust=False, progress=False,
                        threads=True)

    for tk, ytk in zip(tickers, yf_tickers):
        try:
            d_df = daily[ytk] if isinstance(daily.columns, pd.MultiIndex) else daily
            d_df = d_df.dropna(subset=["Close"])
            if len(d_df) < 20:
                continue
            prev_close = float(d_df["Close"].iloc[-2])  # last completed day
            vol_20d_avg = float(d_df["Volume"].iloc[-21:-1].mean())

            i_df = intra[ytk] if isinstance(intra.columns, pd.MultiIndex) else intra
            i_df = i_df.dropna(subset=["Close"])
            if i_df.empty:
                continue
            last_price = float(i_df["Close"].iloc[-1])
            today_vol = float(i_df["Volume"].sum())

            pct = (last_price / prev_close - 1) * 100

            # Projected full-day vol: assume bars cover ~95% of trading day if late afternoon
            now_tw = datetime.now(TW_TZ)
            elapsed_min = (now_tw.hour - 9) * 60 + now_tw.minute
            elapsed_min = max(min(elapsed_min, 270), 30)  # 30~270 min (09:00-13:30)
            day_progress = elapsed_min / 270.0
            projected_full_vol = today_vol / day_progress
            projected_vr = projected_full_vol / vol_20d_avg if vol_20d_avg > 0 else 0

            rows.append({
                "ticker":       tk,
                "last_price":   last_price,
                "prev_close":   prev_close,
                "pct":          round(pct, 2),
                "today_vol":    int(today_vol),
                "vol_20d_avg":  int(vol_20d_avg),
                "projected_vr": round(projected_vr, 2),
            })
        except Exception:
            continue
    return pd.DataFrame(rows)


def detect_matches(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[(df["pct"] <= LIMITDOWN_PCT) &
               (df["projected_vr"] < VR_THRESHOLD)].sort_values("projected_vr")


def push_discord(matches: pd.DataFrame, scan_time: datetime):
    if matches.empty or not DISCORD_WEBHOOK:
        return
    lines = [f"📉 **量縮跌停盤中早報** ({scan_time.strftime('%H:%M')})"]
    lines.append(f"觸發條件: pct ≤ -9% AND projected_vr < 0.8")
    lines.append("")
    for _, r in matches.head(15).iterrows():
        limit_buy = r["last_price"] * 1.005
        lines.append(
            f"• **{r['ticker']}** @ {r['last_price']:.2f}  "
            f"({r['pct']:+.2f}%, vr~{r['projected_vr']:.2f}x)  "
            f"T+1 限價: NT$ {limit_buy:.2f}"
        )
    if len(matches) > 15:
        lines.append(f"  ...({len(matches) - 15} 檔未列)")
    lines.append("")
    lines.append("> ⚠️ 此為盤中早報,**收盤前情況可能變**。實際進場依 14:00 收盤後 scanner_hits.csv 為準。")
    lines.append("> 進場規則: T+1 09:00 限價買; 5d hold; +5% TP 鎖一半; -7% stop")
    text = "\n".join(lines)
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": text}, timeout=10)
        print(f"  ✓ Discord 推播 {len(matches)} 檔")
    except Exception as e:
        print(f"  ⚠️ Discord 推播失敗: {e}")


def scan_once(verbose: bool = True):
    now_tw = datetime.now(TW_TZ)
    if verbose:
        print(f"\n[{now_tw.strftime('%H:%M:%S')}] Scanning...")
    watch = build_watchlist()
    if verbose:
        print(f"  Watchlist: {len(watch)} tickers")
    df = fetch_intraday_pct_vr(watch)
    if verbose:
        print(f"  Got intraday data for {len(df)} tickers")
    matches = detect_matches(df)
    if verbose:
        if matches.empty:
            print(f"  ✓ No quiet limit-down matches")
        else:
            print(f"  🚨 {len(matches)} matches:")
            for _, r in matches.iterrows():
                print(f"    {r['ticker']:<6} @ {r['last_price']:>7.2f}  "
                      f"{r['pct']:+.2f}%  vr={r['projected_vr']:.2f}x")
    push_discord(matches, now_tw)
    return matches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true",
                        help="Run continuously 13:00-13:25, scan every 5min")
    args = parser.parse_args()

    if not args.watch:
        scan_once()
        return

    print(f"Watch mode: scanning every 5 min until 13:25 TW time")
    while True:
        now = datetime.now(TW_TZ)
        if now.hour < 13 or (now.hour == 13 and now.minute < 0):
            sleep_s = ((13 - now.hour) * 3600 + (-now.minute) * 60)
            print(f"  Waiting {sleep_s}s until 13:00...")
            time.sleep(min(sleep_s, 600))
            continue
        if now.hour > 13 or (now.hour == 13 and now.minute > 30):
            print(f"  Past 13:30, exiting")
            break
        scan_once()
        time.sleep(300)  # 5 min between scans


if __name__ == "__main__":
    main()
