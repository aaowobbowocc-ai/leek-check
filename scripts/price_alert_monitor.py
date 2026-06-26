"""即時價格警示 monitor — Shioaji live quote + Discord push.

讀 config/price_alerts.yaml 規則,輪詢 Shioaji,觸發時 Discord push.

特性:
  - 同一規則當日只 push 一次 (state 存 logs/price_alert_state.json)
  - 收盤後自動停 (盤外不做事)
  - 5 分鐘輪詢一次 (Shioaji free tier 不卡 rate limit)

執行:
  python -m scripts.price_alert_monitor               # 持續執行直到 13:30
  python -m scripts.price_alert_monitor --once        # 跑一次就退
  python -m scripts.price_alert_monitor --test        # 模擬觸發 (debug)
"""
from __future__ import annotations
import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, time as dtime, timezone, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env", override=True)

import requests
import yaml

ALERTS_YAML = ROOT / "config" / "price_alerts.yaml"
ASSETS_JSON = ROOT / "data" / "assets.json"
STATE_FILE  = ROOT / "logs" / "price_alert_state.json"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

POLL_INTERVAL_S = 5 * 60  # 5 min between polls
TW_TZ = timezone(timedelta(hours=8))


def market_is_open(now_tw: datetime) -> bool:
    """TW market: 09:00-13:30 Mon-Fri."""
    if now_tw.weekday() >= 5:
        return False
    t = now_tw.time()
    return dtime(9, 0) <= t <= dtime(13, 30)


def load_rules() -> list[dict]:
    if not ALERTS_YAML.exists():
        return []
    with open(ALERTS_YAML, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("rules", [])


def load_holdings_costs() -> dict[str, float]:
    """ticker -> cost_incl_fee (for pct_from_cost rules)"""
    if not ASSETS_JSON.exists():
        return {}
    with open(ASSETS_JSON, encoding="utf-8") as f:
        a = json.load(f)
    out = {}
    for h in a["holdings"].get("long_term", []):
        out[h["ticker"]] = h.get("cost_incl_fee", h.get("cost", 0))
    return out


def load_holdings_shares() -> dict[str, int]:
    """ticker -> current shares (for skipping sold-out alerts)"""
    if not ASSETS_JSON.exists():
        return {}
    with open(ASSETS_JSON, encoding="utf-8") as f:
        a = json.load(f)
    out = {}
    for h in a["holdings"].get("long_term", []):
        out[h["ticker"]] = int(h.get("shares", 0))
    return out


# 賣出 / 停利 / 停損 / 鎖獲利 / 砍 → 持股 0 時 skip
# 2026-05-22 fix: 移除 "review"(太泛用,會誤判 buy-review 規則為賣出而靜默)
SELL_ACTION_KEYWORDS = ("賣", "停損", "停利", "鎖獲利", "砍", "出場", "減碼")
# 買進 / wait list / 加碼 → 即使持股 0 也照樣 fire
BUY_ACTION_KEYWORDS = ("買", "Wait List", "加碼", "撿", "進場")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"date": "", "fired": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"date": "", "fired": []}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def reset_state_if_new_day(state: dict, today: str) -> dict:
    if state.get("date") != today:
        return {"date": today, "fired": []}
    return state


def rule_id(rule: dict) -> str:
    return f"{rule['ticker']}|{rule['condition']}|{rule['price']}"


def evaluate_rule(rule: dict, current_price: float, cost: float | None) -> bool:
    cond = rule["condition"]
    target = rule["price"]
    if cond == "above":
        return current_price >= target
    elif cond == "below":
        return current_price <= target
    elif cond == "pct_from_cost":
        if not cost or cost <= 0:
            return False
        pct = (current_price / cost - 1) * 100
        if target < 0:
            return pct <= target  # threshold negative, current must be more negative
        else:
            return pct >= target
    return False


def push_discord(messages: list[str]):
    if not messages or not DISCORD_WEBHOOK:
        return
    text = "🔔 **即時價格警示** ({})\n\n".format(
        datetime.now(TW_TZ).strftime("%H:%M")
    ) + "\n".join(messages)
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": text}, timeout=10)
        print(f"  ✓ Discord pushed {len(messages)} alerts")
    except Exception as e:
        print(f"  ⚠️ Discord fail: {e}")


def fetch_price(ticker: str) -> float | None:
    is_tw = ticker.isdigit() or (ticker.startswith("00") and len(ticker) <= 6)
    # Shioaji 只支援 TW
    if is_tw:
        try:
            from src.data.shioaji_quote import get_snapshot_price
            p = get_snapshot_price(ticker)
            if p and p > 0:
                return p
        except Exception:
            pass
    # yfinance fallback (handles both TW and US tickers)
    try:
        import yfinance as yf
        if is_tw:
            suffix = ".TWO" if ticker in {"6233", "4543"} else ".TW"
            yf_sym = f"{ticker}{suffix}"
        else:
            yf_sym = ticker  # US: e.g. TSM, NVDA, IBIT, GLD
        h = yf.Ticker(yf_sym).history(period="1d", interval="1m",
                                       auto_adjust=False)
        if h.empty:
            # Fallback to daily for US (1m may be intermittent)
            h = yf.Ticker(yf_sym).history(period="5d", auto_adjust=False)
        if not h.empty:
            return float(h["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return None


def scan_once(rules: list[dict], costs: dict[str, float], state: dict,
              verbose: bool = True) -> int:
    """Scan all rules, fire any that triggered. Returns count fired.

    2026-05-14: 加 holdings-aware filter — 賣/停損/停利 規則對「持股 = 0」
    的標的自動 skip,避免 user 已賣出後還推「停損」訊息。
    """
    fired_msgs = []
    fired_ids = set(state.get("fired", []))
    shares = load_holdings_shares()

    # Holdings-aware filter:篩掉已賣出標的的「賣出/停利/停損」rule
    def rule_relevant(r: dict) -> bool:
        action = r.get("action", "")
        is_buy = any(kw in action for kw in BUY_ACTION_KEYWORDS)
        is_sell = any(kw in action for kw in SELL_ACTION_KEYWORDS)
        if is_buy:
            return True   # 買進/wait list 永遠相關(即使 0 股)
        if is_sell:
            return shares.get(r["ticker"], 0) > 0   # 賣出 = 須持股
        return True   # 不確定的 keep alive

    rules = [r for r in rules if rule_relevant(r)]

    # Group rules by ticker (so we fetch each price only once)
    by_ticker: dict[str, list[dict]] = {}
    for r in rules:
        by_ticker.setdefault(r["ticker"], []).append(r)

    if verbose:
        now_str = datetime.now(TW_TZ).strftime("%H:%M:%S")
        print(f"[{now_str}] Polling {len(by_ticker)} tickers...")

    for tk, rs in by_ticker.items():
        price = fetch_price(tk)
        if price is None:
            if verbose:
                print(f"  {tk}: (no price)")
            continue
        if verbose:
            print(f"  {tk}: NT$ {price}")

        cost = costs.get(tk)
        for r in rs:
            rid = rule_id(r)
            if rid in fired_ids:
                continue
            if evaluate_rule(r, price, cost):
                cond = r["condition"]
                target = r["price"]
                if cond == "above":
                    detail = f"@ NT$ {price:.2f} ≥ NT$ {target:.2f}"
                elif cond == "below":
                    detail = f"@ NT$ {price:.2f} ≤ NT$ {target:.2f}"
                else:
                    pct = (price / cost - 1) * 100 if cost else 0
                    detail = f"@ NT$ {price:.2f} ({pct:+.1f}% from cost)"
                from src.strategy.volume_anomaly_scanner import lookup_ticker_name
                name = lookup_ticker_name(tk)
                msg = f"**{tk} {name}** {detail}\n  → {r['action']}"
                fired_msgs.append(msg)
                fired_ids.add(rid)
                if verbose:
                    print(f"    🔔 TRIGGERED: {r['action']}")

    if fired_msgs:
        push_discord(fired_msgs)
    state["fired"] = list(fired_ids)
    save_state(state)
    return len(fired_msgs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run one scan and exit")
    ap.add_argument("--test", action="store_true", help="Skip market hours check")
    args = ap.parse_args()

    rules = load_rules()
    if not rules:
        print(f"No rules in {ALERTS_YAML}")
        return

    costs = load_holdings_costs()
    print(f"Loaded {len(rules)} rules over {len({r['ticker'] for r in rules})} tickers")
    print(f"Holdings cost basis loaded for {len(costs)} tickers")

    while True:
        now_tw = datetime.now(TW_TZ)
        today_str = now_tw.strftime("%Y-%m-%d")
        state = reset_state_if_new_day(load_state(), today_str)

        if not args.test and not market_is_open(now_tw):
            if args.once:
                print(f"Market closed @ {now_tw.strftime('%H:%M')}, skip")
                return
            # Wait until next market open or sleep 30 min
            print(f"[{now_tw.strftime('%H:%M')}] Market closed, sleep 30min")
            time.sleep(1800)
            continue

        scan_once(rules, costs, state)

        if args.once:
            return
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
