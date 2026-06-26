"""持股漲停隔日反轉警示 — 量爆漲停的早盤建議賣訊號.

Audit memory:
  量爆漲停 (vol_ratio > 1.5):
    - D+1 跳空 +2.5% 開盤
    - 但 D+1 盤中回吐 -0.55%
    - Trading rule: 量爆漲停「開盤即賣」
  量縮漲停 (vol_ratio < 0.8):
    - D+1 盤中續漲 +0.57%
    - Trading rule: 量縮漲停 hold

(memory: project_limitup_overnight_reversal.md)

只對 USER HOLDINGS 跑(不對全市場),早盤用 Discord 提醒「T+1 開盤即賣」.

執行:
  python -m scripts.holdings_limitup_alert            # T+1 早盤前跑
  python -m scripts.holdings_limitup_alert --check-d 2026-05-07  # check specific date
"""
from __future__ import annotations
import argparse, io, sys, json, os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env", override=True)

import pandas as pd
import requests

from src.strategy.volume_anomaly_scanner import lookup_ticker_name

ASSETS_JSON = ROOT / "data" / "assets.json"
TW_CACHE    = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

LIMITUP_PCT     = 9.5
VOL_HIGH_RATIO  = 1.5    # 量爆 threshold
VOL_LOW_RATIO   = 0.8    # 量縮 threshold


def get_holdings() -> list[str]:
    if not ASSETS_JSON.exists():
        return []
    with open(ASSETS_JSON) as f:
        a = json.load(f)
    out = []
    for h in a["holdings"].get("long_term", []):
        tk = h.get("ticker", "")
        # 4-digit ticker only (skip ETFs that don't really limit-up at 9.5)
        if tk and tk.isdigit() and len(tk) == 4:
            out.append(tk)
    return out


def check_ticker(ticker: str, target_date: date | None = None) -> dict | None:
    """Check if ticker did 漲停 on target_date (or last trading day if None).
    Returns dict with details or None if no trigger."""
    p = TW_CACHE / f"{ticker}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)

    if target_date:
        idx = df.index[df["date"] == target_date]
        if len(idx) == 0:
            return None
        i = idx[0]
    else:
        i = len(df) - 1   # last available

    if i < 60:
        return None

    today_close = float(df["close"].iloc[i])
    prev_close = float(df["close"].iloc[i - 1])
    pct = (today_close / prev_close - 1) * 100
    if pct < LIMITUP_PCT:
        return None  # not 漲停

    vol_today = float(df["volume"].iloc[i])
    vol_ma60 = float(df["volume"].iloc[i - 59 : i + 1].mean())
    if vol_ma60 <= 0:
        return None
    vr = vol_today / vol_ma60

    classify = "量爆" if vr > VOL_HIGH_RATIO else ("量縮" if vr < VOL_LOW_RATIO else "中性")
    return {
        "ticker":       ticker,
        "date":         df["date"].iloc[i],
        "close":        today_close,
        "pct":          round(pct, 2),
        "vol_ratio":    round(vr, 2),
        "classify":     classify,
    }


def push_discord(triggers: list[dict]):
    if not triggers or not DISCORD_WEBHOOK:
        return
    lines = ["🔔 **持股漲停 — 早盤建議**", ""]
    sells, holds = [], []
    for t in triggers:
        name = lookup_ticker_name(t["ticker"])
        line = (f"**{t['ticker']} {name}** D 收 {t['close']:.2f} "
                f"({t['pct']:+.2f}%, 量比 {t['vol_ratio']}x = {t['classify']})")
        if t["classify"] == "量爆":
            sells.append(line + "\n  → 🟢 T+1 開盤即賣 (audit: 隔日盤中回吐 -0.55%)")
        elif t["classify"] == "量縮":
            holds.append(line + "\n  → 🟡 量縮 hold (audit: 盤中續漲 +0.57%)")
        else:
            holds.append(line + "\n  → ⚪ 中性,觀察")
    if sells:
        lines.append("**🚨 量爆漲停 — 早盤觀察 (warning)**")
        lines.append("_注意: research 說回吐 -0.55%,但 60d reality calibration 顯示 +0.86%_")
        lines.append("_當前 regime 可能反彈延續,**不要盲目早盤賣**_")
        lines.extend(sells)
        lines.append("")
    if holds:
        lines.append("**🟡 量縮 / 中性漲停 — hold**")
        lines.extend(holds)
    text = "\n".join(lines)
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": text}, timeout=10)
        print(f"  ✓ Discord pushed {len(triggers)} alerts")
    except Exception as e:
        print(f"  ⚠️ Discord fail: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-d", help="YYYY-MM-DD to check (default = last available)")
    ap.add_argument("--no-discord", action="store_true")
    args = ap.parse_args()

    target_date = (datetime.strptime(args.check_d, "%Y-%m-%d").date()
                    if args.check_d else None)

    holdings = get_holdings()
    print(f"Checking {len(holdings)} holdings: {holdings}")

    triggers = []
    for tk in holdings:
        r = check_ticker(tk, target_date)
        if r:
            triggers.append(r)
            print(f"  🚨 {tk}: {r['pct']:+.2f}% vol_ratio {r['vol_ratio']}x ({r['classify']})")
        else:
            print(f"  {tk}: 無漲停")

    if not args.no_discord and triggers:
        push_discord(triggers)


if __name__ == "__main__":
    main()
