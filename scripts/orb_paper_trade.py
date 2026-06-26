"""
ORB Scalp Paper Trade — 2408 南亞科 + 任何 whitelist ticker。

兩個 mode（用 cron / Task Scheduler 排程）:
  --mode morning  約 09:20 跑：抓今日 minute K（09:00-09:15 已成形）→ 偵測 ORB → 開倉
  --mode close    約 13:25 跑：抓 13:20 close → 計算 net return → 平倉 + 推播

ORB 規則（沿用 2408 walk-forward 驗證版）:
  09:00-09:15 cumulative volume > 昨日全天 × 30%
  AND 09:15 close > 09:00-09:05 high
  → entry @ 09:15 close
  → exit @ 13:20 close
  cost: 0.34% / 筆（手續費 3 折 + 當沖減半稅）

WHITELIST = ["2408"]   # 後續加入 Tier A passers

帳本: data/paper_trades/orb_ledger.csv（append-only）
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

from src.notify.discord_client import DiscordNotifier  # noqa: E402

# ── Config ──
# 每個 ticker 的 ORB 規則（從 orb_param_sweep_summary.md Tier A 來的）
# (entry_time, vol_threshold, breakout_ref ∈ {"open5","open15"}, caveat)
WHITELIST_RULES: dict[str, dict] = {
    "2408": {
        "entry_time": "09:15", "vol_threshold": 0.30, "breakout_ref": "open5",
        "caveat": "Tier A: full+OOS 都 positive (n=19, OOS +0.99%)"
    },
    "2485": {
        "entry_time": "09:45", "vol_threshold": 0.30, "breakout_ref": "open15",
        "caveat": "⚠️ Tier A but OOS-only sample (n=16, +1.58%) — 規則未經 train 驗證"
    },
}
COST_PCT = 0.34
EXIT_TIME = "13:20"
LEDGER_PATH = ROOT / "data" / "paper_trades" / "orb_ledger.csv"
LEDGER_HEADERS = [
    "trade_date", "ticker", "name",
    "open5_high", "vol_ratio_15min", "prev_day_total_vol",
    "entry_time", "entry_price",
    "exit_time", "exit_price",
    "gross_return_pct", "net_return_pct", "is_winner",
    "status", "logged_at",
]

API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockKBar"


def lookup_name(ticker: str) -> str:
    try:
        from src.strategy.volume_anomaly_scanner import lookup_ticker_name
        return lookup_ticker_name(str(ticker)) or ticker
    except Exception:
        return ticker


def fetch_today_minute(token: str, ticker: str, d: date) -> pd.DataFrame:
    """單日 minute K 抓取（含 retry）。"""
    params = {
        "dataset": DATASET, "data_id": ticker,
        "start_date": d.isoformat(), "end_date": d.isoformat(),
        "token": token,
    }
    for retry in range(3):
        try:
            resp = requests.get(API_URL, params=params, timeout=30)
            payload = resp.json()
            if payload.get("status") == 200:
                rows = payload.get("data") or []
                if not rows:
                    return pd.DataFrame()
                df = pd.DataFrame(rows)
                df["dt"] = pd.to_datetime(df["date"].astype(str) + " " + df["minute"].astype(str))
                df["minute_str"] = df["dt"].dt.strftime("%H:%M")
                return df.sort_values("dt").reset_index(drop=True)
            print(f"  status={payload.get('status')}: {payload.get('msg', '')[:120]}")
        except Exception as e:
            if retry == 2:
                print(f"  fetch failed: {e}")
        time.sleep(1.0)
    return pd.DataFrame()


def fetch_prev_day_volume(token: str, ticker: str, d: date) -> int:
    """抓 d 之前最近一個交易日的 total volume（用 daily K）。"""
    # 往前抓 7 天保險
    start = d - timedelta(days=10)
    params = {
        "dataset": "TaiwanStockPrice", "data_id": ticker,
        "start_date": start.isoformat(), "end_date": (d - timedelta(days=1)).isoformat(),
        "token": token,
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=30)
        payload = resp.json()
        if payload.get("status") == 200 and payload.get("data"):
            df = pd.DataFrame(payload["data"]).sort_values("date")
            if not df.empty:
                # Trading_Volume 是「股」，minute K volume 是「張」 → 除 1000 轉換
                last = df.iloc[-1]
                shares = int(last.get("Trading_Volume") or last.get("volume") or 0)
                return shares // 1000
    except Exception as e:
        print(f"  prev day vol fetch failed: {e}")
    return 0


def check_regime_ok() -> tuple[bool, str]:
    """
    用 strategy_regime_gate 嚴格判定。
    熊市 OR late_bull (距 MA200 > 25%) 都暫停 ORB
    （因為 ORB 在 late_bull 過熱期表現也下滑）
    """
    try:
        from src.risk.strategy_regime_gate import detect_current_regime
        r = detect_current_regime()
        pct = (r.taiex_close / r.ma200 - 1) * 100

        if r.trend == "bear":
            return False, f"🔴 熊市 → ORB 暫停 (TAIEX {r.taiex_close:.0f} < MA60)"
        if r.cycle == "late_bull":
            return False, (f"🟠 LATE_BULL 過熱 → ORB 暫停 "
                            f"(距 MA200 {pct:+.1f}%, 超過 +25% 過熱閾值)")
        if r.cycle in ("early_bull", "mid_bull"):
            return True, f"🟢 {r.cycle.upper()} (距 MA200 {pct:+.1f}%) → ORB 啟用"
        return True, f"🟡 sideways → ORB 繼續但小心"
    except Exception as e:
        return True, f"regime check error: {e}"


def detect_orb(day_df: pd.DataFrame, prev_day_total_vol: int, rule: dict) -> dict | None:
    if day_df.empty or prev_day_total_vol <= 0:
        return None
    entry_time = rule["entry_time"]
    vol_threshold = rule["vol_threshold"]
    breakout_ref = rule["breakout_ref"]

    if breakout_ref == "open5":
        ref_window = day_df[day_df["minute_str"] <= "09:04"]
    else:
        ref_window = day_df[day_df["minute_str"] <= "09:14"]
    if ref_window.empty:
        return None
    ref_high = float(ref_window["high"].max())

    # 累積量到 entry_time 之前
    cum_window = day_df[day_df["minute_str"] < entry_time]
    if cum_window.empty:
        return None
    cum_vol = float(cum_window["volume"].sum())
    vol_ratio = cum_vol / prev_day_total_vol

    bar = day_df[day_df["minute_str"] == entry_time]
    if bar.empty:
        h, m = entry_time.split(":")
        for delta in [-1, 1, -2, 2]:
            adj = f"{h}:{int(m)+delta:02d}"
            bar = day_df[day_df["minute_str"] == adj]
            if not bar.empty:
                break
        if bar.empty:
            return None
    entry_price = float(bar["close"].iloc[0])

    if vol_ratio < vol_threshold:
        return None
    if entry_price <= ref_high:
        return None

    return {
        "open5_high": ref_high,
        "vol_ratio": vol_ratio,
        "entry_price": entry_price,
        "entry_time": entry_time,
    }


def get_exit_price(day_df: pd.DataFrame) -> tuple[str, float] | None:
    for tt in [EXIT_TIME, "13:19", "13:21", "13:25", "13:30"]:
        bar = day_df[day_df["minute_str"] == tt]
        if not bar.empty:
            return (tt, float(bar["close"].iloc[0]))
    if not day_df.empty:
        return ("eod", float(day_df.iloc[-1]["close"]))
    return None


def append_ledger(row: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = LEDGER_PATH.exists()
    with LEDGER_PATH.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in LEDGER_HEADERS})


def find_open_today(ticker: str, today: date) -> dict | None:
    if not LEDGER_PATH.exists():
        return None
    df = pd.read_csv(LEDGER_PATH, dtype={"ticker": str})
    today_str = today.isoformat()
    sub = df[(df["ticker"] == ticker) & (df["trade_date"] == today_str) & (df["status"] == "open")]
    if sub.empty:
        return None
    return sub.iloc[-1].to_dict()


def update_ledger_close(trade_date: date, ticker: str, exit_time: str, exit_price: float,
                       gross: float, net: float, is_winner: bool) -> None:
    df = pd.read_csv(LEDGER_PATH, dtype={"ticker": str})
    # Force float dtype for numeric columns to avoid LossySetitemError
    for col in ("exit_price", "gross_return_pct", "net_return_pct"):
        df[col] = df[col].astype("float64")
    df["exit_time"] = df["exit_time"].astype("object")
    df["is_winner"] = df["is_winner"].astype("object")
    mask = (df["ticker"] == ticker) & (df["trade_date"] == trade_date.isoformat()) & (df["status"] == "open")
    if not mask.any():
        return
    df.loc[mask, "exit_time"] = exit_time
    df.loc[mask, "exit_price"] = float(exit_price)
    df.loc[mask, "gross_return_pct"] = float(gross)
    df.loc[mask, "net_return_pct"] = float(net)
    df.loc[mask, "is_winner"] = bool(is_winner)
    df.loc[mask, "status"] = "closed"
    df.to_csv(LEDGER_PATH, index=False, encoding="utf-8-sig")


def push_discord(msg: str) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        print("  DISCORD_WEBHOOK_URL not set, skip push")
        return
    DiscordNotifier(url).send(msg)


def run_morning(token: str, today: date) -> None:
    # Regime check
    regime_ok, regime_msg = check_regime_ok()
    print(f"\n[regime] {regime_msg}")
    if not regime_ok:
        push_discord(f"⏸ **ORB 暫停**：{regime_msg}\n\n"
                      f"2021-2022 熊市 backtest: 2485 -0.97%/筆，2408 alpha 接近 0\n"
                      f"等 regime 改善（TAIEX > MA200）再啟用")
        return

    alerts = []
    for tk, rule in WHITELIST_RULES.items():
        print(f"\n[{tk}] morning ORB check (entry={rule['entry_time']}, "
              f"vol≥{rule['vol_threshold']:.0%}, ref={rule['breakout_ref']})")
        existing = find_open_today(tk, today)
        if existing:
            print(f"  already open today, skip")
            continue
        prev_vol = fetch_prev_day_volume(token, tk, today)
        if prev_vol <= 0:
            print(f"  prev day volume unknown, skip")
            continue
        print(f"  prev day vol = {prev_vol:,}")
        df = fetch_today_minute(token, tk, today)
        if df.empty:
            print(f"  no minute K available yet")
            continue
        sig = detect_orb(df, prev_vol, rule)
        if not sig:
            print(f"  no ORB signal under rule")
            continue
        name = lookup_name(tk)
        row = {
            "trade_date": today.isoformat(),
            "ticker": tk, "name": name,
            "open5_high": round(sig["open5_high"], 2),
            "vol_ratio_15min": round(sig["vol_ratio"], 4),
            "prev_day_total_vol": prev_vol,
            "entry_time": sig["entry_time"],
            "entry_price": round(sig["entry_price"], 2),
            "status": "open",
            "logged_at": datetime.now().isoformat(timespec="seconds"),
        }
        append_ledger(row)
        msg = (f"🚨 **ORB 訊號觸發** {today.isoformat()}\n\n"
               f"**{tk} {name}** entry @ {sig['entry_price']:.2f} ({sig['entry_time']})\n"
               f"  ref_high ({rule['breakout_ref']}): {sig['open5_high']:.2f}\n"
               f"  vol_ratio (累積至 {sig['entry_time']} vs 昨總量): {sig['vol_ratio']:.1%} "
               f"(≥{rule['vol_threshold']:.0%} gate)\n"
               f"  目標出場: {EXIT_TIME} 強制平倉\n"
               f"  caveat: {rule['caveat']}\n"
               f"  成本: 0.34% / 筆\n\n"
               f"_paper trade only — 不下實單_")
        alerts.append(msg)
        print(f"  ✅ ORB triggered, ledger updated")
    for m in alerts:
        push_discord(m)
    if not alerts:
        print("\n無 ORB 訊號觸發")


def run_close(token: str, today: date) -> None:
    if not LEDGER_PATH.exists():
        print("ledger 不存在，無待平倉單")
        return
    df = pd.read_csv(LEDGER_PATH, dtype={"ticker": str})
    today_open = df[(df["trade_date"] == today.isoformat()) & (df["status"] == "open")]
    if today_open.empty:
        print(f"今日無待平倉單 ({today})")
        return

    summaries = []
    for _, r in today_open.iterrows():
        tk = r["ticker"]
        print(f"\n[{tk}] close out")
        day_df = fetch_today_minute(token, tk, today)
        if day_df.empty:
            print("  minute K 抓不到，跳過")
            continue
        ex = get_exit_price(day_df)
        if not ex:
            print("  exit price 找不到")
            continue
        exit_time, exit_price = ex
        entry_price = float(r["entry_price"])
        gross = (exit_price / entry_price - 1) * 100
        net = gross - COST_PCT
        is_winner = net > 0
        update_ledger_close(today, tk, exit_time, exit_price, gross, net, is_winner)
        emoji = "✅" if is_winner else "❌"
        msg = (f"📉 **ORB 平倉** {today.isoformat()}\n\n"
               f"**{tk} {r['name']}**\n"
               f"  entry @ {entry_price:.2f} → exit @ {exit_price:.2f} ({exit_time})\n"
               f"  gross: {gross:+.2f}% | 扣成本後 net: {net:+.2f}% {emoji}")
        summaries.append(msg)
        print(f"  {emoji} net {net:+.2f}%")

    # Aggregate ledger stats
    df_all = pd.read_csv(LEDGER_PATH)
    closed = df_all[df_all["status"] == "closed"]
    if len(closed) >= 1:
        n = len(closed)
        win_rate = (closed["is_winner"].astype(str).str.lower() == "true").mean() * 100
        mean_net = closed["net_return_pct"].mean()
        total_pnl = closed["net_return_pct"].sum()
        stats_msg = (f"\n📊 **ORB Paper Trade 累計** (n={n})\n"
                     f"  win rate: {win_rate:.1f}%\n"
                     f"  mean net: {mean_net:+.2f}%/筆\n"
                     f"  total: {total_pnl:+.2f}%（單筆等權加總）")
        summaries.append(stats_msg)

    for m in summaries:
        push_discord(m)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["morning", "close"], required=True)
    p.add_argument("--date", default=None, help="YYYY-MM-DD（預設今日）")
    args = p.parse_args()

    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_KEY") or ""
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    today = date.fromisoformat(args.date) if args.date else date.today()
    print(f"=== ORB Paper Trade {args.mode} mode | {today} ===")
    if args.mode == "morning":
        run_morning(token, today)
    else:
        run_close(token, today)


if __name__ == "__main__":
    main()
