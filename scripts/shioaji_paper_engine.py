"""
Shioaji Paper Trade Engine — 自動掃訊號 + 模擬下單 + 追蹤部位

4 個策略 paralle 執行:

1. Pair: 2408-2344 DRAM (z>2.5 進, z<0.5 出, max 25d hold)
2. Revenue YoY Deploy-Ready (scanner_hits.csv 抓 deploy_ready=True, hold 60d)
3. 0050 dealer 連買 3d (daily_state.csv inst_0050_consec_buy >= 3, hold 20d)
4. CRASH watcher (regime=CRASH 觸發, 一次部署現金 50% 0050 + 50% 00631L)

每天跑一次（接 cron 14:00 之後），讀取訊號 → 模擬單 → 追蹤倉位。

執行方式:
  python scripts/shioaji_paper_engine.py

輸出:
  data/paper_trades/shioaji_positions.csv (open positions)
  data/paper_trades/shioaji_trades.csv (closed trades)
  data/paper_trades/shioaji_log.jsonl (decision log)
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env", override=True)

import pandas as pd

PAPER_DIR = ROOT / "data" / "paper_trades"
PAPER_DIR.mkdir(parents=True, exist_ok=True)
POSITIONS_CSV = PAPER_DIR / "shioaji_positions.csv"
TRADES_CSV = PAPER_DIR / "shioaji_trades.csv"
LOG_JSONL = PAPER_DIR / "shioaji_log.jsonl"

# Position size per slot
DEFAULT_SIZE = 30_000  # NT$30K per slot

# Strategy parameters
PAIR_Z_ENTRY = 2.5
PAIR_Z_EXIT = 0.5
PAIR_TIMEOUT = 25
DEALER_HOLD = 20
RYY_HOLD = 60
CRASH_HOLD = 60

POSITION_COLUMNS = [
    "id", "strategy", "leg_a_ticker", "leg_a_shares", "leg_a_entry",
    "leg_b_ticker", "leg_b_shares", "leg_b_entry",
    "entry_date", "scheduled_exit", "status", "notes",
]
TRADE_COLUMNS = POSITION_COLUMNS + [
    "exit_date", "leg_a_exit", "leg_b_exit",
    "gross_pct", "net_pct", "pnl_twd",
]


def log_event(event: dict):
    event["ts"] = datetime.now().isoformat(timespec="seconds")
    with open(LOG_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_positions() -> pd.DataFrame:
    if not POSITIONS_CSV.exists():
        return pd.DataFrame(columns=POSITION_COLUMNS)
    return pd.read_csv(POSITIONS_CSV)


def save_positions(df: pd.DataFrame):
    df.to_csv(POSITIONS_CSV, index=False, encoding="utf-8-sig")


def load_trades() -> pd.DataFrame:
    if not TRADES_CSV.exists():
        return pd.DataFrame(columns=TRADE_COLUMNS)
    return pd.read_csv(TRADES_CSV)


def save_trades(df: pd.DataFrame):
    df.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")


def get_price_at(ticker: str, when: date) -> float:
    """從 OHLCV cache 抓某日收盤價"""
    p = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv" / f"{ticker}.parquet"
    if not p.exists():
        return 0.0
    try:
        df = pd.read_parquet(p, columns=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        # Find closest date <= when
        sub = df[df["date"] <= pd.Timestamp(when)]
        if sub.empty:
            return 0.0
        return float(sub["close"].iloc[-1])
    except Exception:
        return 0.0


def shares_for_size(ticker: str, size_twd: float, on_date: date) -> int:
    price = get_price_at(ticker, on_date)
    if price <= 0:
        return 0
    return int(size_twd / price)


# ════════════════════════════════════════════════════════════════
# Strategy 1: Pair Trading 2408-2344 (DRAM)
# ════════════════════════════════════════════════════════════════
def check_pair_signal(today: date) -> dict | None:
    """從 daily_state.csv 抓最新 z-score"""
    p = PAPER_DIR / "daily_state.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p)
        if df.empty:
            return None
        latest = df.iloc[-1]
        z = float(latest.get("pair_2408_2344_z", 0))
        return {"z": z, "date": latest.get("date")}
    except Exception:
        return None


def open_pair_trade(positions: pd.DataFrame, today: date, z: float) -> pd.DataFrame:
    """進場 pair: |z|>2.5"""
    if abs(z) < PAIR_Z_ENTRY:
        return positions
    # Check if already in pair position
    open_pair = positions[(positions["strategy"] == "pair_2408_2344") & (positions["status"] == "open")]
    if not open_pair.empty:
        return positions

    direction = "short_a_long_b" if z > 0 else "long_a_short_b"
    a_shares = shares_for_size("2408", DEFAULT_SIZE, today)
    b_shares = shares_for_size("2344", DEFAULT_SIZE, today)
    a_price = get_price_at("2408", today)
    b_price = get_price_at("2344", today)
    if a_shares == 0 or b_shares == 0:
        return positions

    a_sign = -a_shares if direction == "short_a_long_b" else a_shares
    b_sign = b_shares if direction == "short_a_long_b" else -b_shares

    new_id = f"pair_{today.isoformat()}_{int(z*100)}"
    new = {
        "id": new_id, "strategy": "pair_2408_2344",
        "leg_a_ticker": "2408", "leg_a_shares": a_sign, "leg_a_entry": a_price,
        "leg_b_ticker": "2344", "leg_b_shares": b_sign, "leg_b_entry": b_price,
        "entry_date": today.isoformat(),
        "scheduled_exit": (today + timedelta(days=int(PAIR_TIMEOUT * 1.4))).isoformat(),
        "status": "open",
        "notes": f"z={z:+.2f} {direction}",
    }
    print(f"  📈 OPEN pair_2408_2344 {direction} z={z:+.2f}: 2408 {a_sign}股@{a_price} | 2344 {b_sign}股@{b_price}")
    log_event({"type": "open", "id": new_id, "strategy": "pair_2408_2344", "z": z, "direction": direction})
    return pd.concat([positions, pd.DataFrame([new])], ignore_index=True)


def close_pair_if_due(positions: pd.DataFrame, today: date, z: float) -> tuple[pd.DataFrame, list[dict]]:
    closed_records = []
    open_pair = positions[(positions["strategy"] == "pair_2408_2344") & (positions["status"] == "open")]
    for idx, row in open_pair.iterrows():
        entry_dt = pd.to_datetime(row["entry_date"]).date()
        days_held = (today - entry_dt).days
        z_revert = abs(z) < PAIR_Z_EXIT
        timeout = days_held >= PAIR_TIMEOUT * 1.4
        if z_revert or timeout:
            a_exit = get_price_at("2408", today)
            b_exit = get_price_at("2344", today)
            if a_exit == 0 or b_exit == 0:
                continue
            a_pnl = (a_exit - row["leg_a_entry"]) * row["leg_a_shares"]  # signed shares
            b_pnl = (b_exit - row["leg_b_entry"]) * row["leg_b_shares"]
            total_pnl = a_pnl + b_pnl
            cost = (abs(row["leg_a_shares"]) * row["leg_a_entry"]
                    + abs(row["leg_b_shares"]) * row["leg_b_entry"])
            net_pct = total_pnl / cost * 100 if cost > 0 else 0
            closed_records.append({
                **row.to_dict(),
                "exit_date": today.isoformat(),
                "leg_a_exit": a_exit, "leg_b_exit": b_exit,
                "gross_pct": round(net_pct, 2),
                "net_pct": round(net_pct - 0.34, 2),  # cost adj
                "pnl_twd": round(total_pnl, 0),
            })
            positions.at[idx, "status"] = "closed"
            print(f"  📤 CLOSE pair_2408_2344: gross {net_pct:+.2f}% pnl NT${total_pnl:+,.0f} ({'z revert' if z_revert else 'timeout'})")
            log_event({"type": "close", "id": row["id"], "pnl": total_pnl, "reason": "z_revert" if z_revert else "timeout"})
    return positions, closed_records


# ════════════════════════════════════════════════════════════════
# Strategy 2: Revenue YoY Deploy-Ready
# ════════════════════════════════════════════════════════════════
def check_ryy_signals(today: date) -> list[dict]:
    """從 scanner_hits.csv 抓今日 Revenue YoY deploy-ready"""
    p = PAPER_DIR / "scanner_hits.csv"
    if not p.exists():
        return []
    try:
        df = pd.read_csv(p)
        if df.empty:
            return []
        df["scan_date"] = pd.to_datetime(df["scan_date"]).dt.date
        today_hits = df[
            (df["scan_date"] == today)
            & (df["signal"] == "revenue_relative_yoy")
        ]
        if "deploy_ready" in today_hits.columns:
            today_hits = today_hits[
                today_hits["deploy_ready"].astype(str).str.lower().isin(["true", "1"])
            ]
        return today_hits.to_dict("records")
    except Exception:
        return []


def open_ryy_position(positions: pd.DataFrame, today: date, signal: dict) -> pd.DataFrame:
    tk = str(signal.get("ticker", ""))
    if not tk:
        return positions
    # 同 ticker 同 strategy 不重複進場
    open_same = positions[
        (positions["strategy"] == "revenue_yoy")
        & (positions["leg_a_ticker"].astype(str) == tk)
        & (positions["status"] == "open")
    ]
    if not open_same.empty:
        return positions
    shares = shares_for_size(tk, DEFAULT_SIZE, today)
    price = get_price_at(tk, today)
    if shares == 0 or price <= 0:
        return positions
    new_id = f"ryy_{today.isoformat()}_{tk}"
    new = {
        "id": new_id, "strategy": "revenue_yoy",
        "leg_a_ticker": tk, "leg_a_shares": shares, "leg_a_entry": price,
        "leg_b_ticker": "", "leg_b_shares": 0, "leg_b_entry": 0,
        "entry_date": today.isoformat(),
        "scheduled_exit": (today + timedelta(days=int(RYY_HOLD * 1.4))).isoformat(),
        "status": "open",
        "notes": f"YoY +{signal.get('yoy_pct', 0):.1f}% / {signal.get('avg_dv_60d_yi', 0):.1f}億",
    }
    print(f"  📈 OPEN revenue_yoy {tk}: {shares}股@{price} (YoY +{signal.get('yoy_pct', 0):.1f}%)")
    log_event({"type": "open", "id": new_id, "strategy": "revenue_yoy", "ticker": tk})
    return pd.concat([positions, pd.DataFrame([new])], ignore_index=True)


def close_ryy_if_due(positions: pd.DataFrame, today: date) -> tuple[pd.DataFrame, list[dict]]:
    closed_records = []
    open_ryy = positions[(positions["strategy"] == "revenue_yoy") & (positions["status"] == "open")]
    for idx, row in open_ryy.iterrows():
        entry_dt = pd.to_datetime(row["entry_date"]).date()
        target = entry_dt + timedelta(days=int(RYY_HOLD * 1.4))
        if today < target:
            continue
        tk = str(row["leg_a_ticker"])
        exit_p = get_price_at(tk, today)
        if exit_p == 0:
            continue
        gross = (exit_p / row["leg_a_entry"] - 1) * 100
        pnl = (exit_p - row["leg_a_entry"]) * row["leg_a_shares"]
        closed_records.append({
            **row.to_dict(),
            "exit_date": today.isoformat(),
            "leg_a_exit": exit_p, "leg_b_exit": 0,
            "gross_pct": round(gross, 2),
            "net_pct": round(gross - 0.78, 2),
            "pnl_twd": round(pnl, 0),
        })
        positions.at[idx, "status"] = "closed"
        print(f"  📤 CLOSE revenue_yoy {tk}: {gross:+.2f}% pnl NT${pnl:+,.0f}")
        log_event({"type": "close", "id": row["id"], "pnl": pnl, "reason": "hold expired"})
    return positions, closed_records


# ════════════════════════════════════════════════════════════════
# Strategy 3: 0050 Dealer 連買 3d
# ════════════════════════════════════════════════════════════════
def check_dealer_signal(today: date) -> dict | None:
    p = PAPER_DIR / "daily_state.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p)
        if df.empty: return None
        latest = df.iloc[-1]
        consec = int(latest.get("inst_0050_consec_buy", 0))
        return {"consec": consec, "date": latest.get("date")}
    except Exception:
        return None


def open_dealer_position(positions: pd.DataFrame, today: date, consec: int) -> pd.DataFrame:
    if consec < 3:
        return positions
    open_dealer = positions[(positions["strategy"] == "dealer_0050") & (positions["status"] == "open")]
    if not open_dealer.empty:
        return positions
    shares = shares_for_size("0050", DEFAULT_SIZE, today)
    price = get_price_at("0050", today)
    if shares == 0:
        return positions
    new_id = f"dealer_{today.isoformat()}"
    new = {
        "id": new_id, "strategy": "dealer_0050",
        "leg_a_ticker": "0050", "leg_a_shares": shares, "leg_a_entry": price,
        "leg_b_ticker": "", "leg_b_shares": 0, "leg_b_entry": 0,
        "entry_date": today.isoformat(),
        "scheduled_exit": (today + timedelta(days=int(DEALER_HOLD * 1.4))).isoformat(),
        "status": "open",
        "notes": f"自營商連買 {consec}d",
    }
    print(f"  📈 OPEN dealer_0050: {shares}股@{price} (連買 {consec}d)")
    log_event({"type": "open", "id": new_id, "strategy": "dealer_0050", "consec": consec})
    return pd.concat([positions, pd.DataFrame([new])], ignore_index=True)


def close_dealer_if_due(positions: pd.DataFrame, today: date) -> tuple[pd.DataFrame, list[dict]]:
    closed = []
    open_dealer = positions[(positions["strategy"] == "dealer_0050") & (positions["status"] == "open")]
    for idx, row in open_dealer.iterrows():
        entry_dt = pd.to_datetime(row["entry_date"]).date()
        target = entry_dt + timedelta(days=int(DEALER_HOLD * 1.4))
        if today < target:
            continue
        exit_p = get_price_at("0050", today)
        if exit_p == 0: continue
        gross = (exit_p / row["leg_a_entry"] - 1) * 100
        pnl = (exit_p - row["leg_a_entry"]) * row["leg_a_shares"]
        closed.append({
            **row.to_dict(),
            "exit_date": today.isoformat(),
            "leg_a_exit": exit_p, "leg_b_exit": 0,
            "gross_pct": round(gross, 2), "net_pct": round(gross - 0.78, 2),
            "pnl_twd": round(pnl, 0),
        })
        positions.at[idx, "status"] = "closed"
        print(f"  📤 CLOSE dealer_0050: {gross:+.2f}% pnl NT${pnl:+,.0f}")
        log_event({"type": "close", "id": row["id"], "pnl": pnl})
    return positions, closed


# ════════════════════════════════════════════════════════════════
# Strategy 4: CRASH Watcher
# ════════════════════════════════════════════════════════════════
def check_crash_regime(today: date) -> str:
    try:
        from src.report.regime_section import compute_current_regime
        r = compute_current_regime()
        return r.regime if r else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def open_crash_position(positions: pd.DataFrame, today: date, regime: str) -> pd.DataFrame:
    if regime != "CRASH":
        return positions
    open_crash = positions[(positions["strategy"] == "crash_watcher") & (positions["status"] == "open")]
    if not open_crash.empty:
        return positions
    # 一次部署：60K 0050 + 60K 00631L
    crash_size = DEFAULT_SIZE * 2
    p_0050 = get_price_at("0050", today)
    p_lev = get_price_at("00631L", today)
    s_0050 = int(crash_size / p_0050) if p_0050 > 0 else 0
    s_lev = int(crash_size / p_lev) if p_lev > 0 else 0
    if s_0050 == 0 or s_lev == 0:
        return positions
    new = {
        "id": f"crash_{today.isoformat()}",
        "strategy": "crash_watcher",
        "leg_a_ticker": "0050", "leg_a_shares": s_0050, "leg_a_entry": p_0050,
        "leg_b_ticker": "00631L", "leg_b_shares": s_lev, "leg_b_entry": p_lev,
        "entry_date": today.isoformat(),
        "scheduled_exit": (today + timedelta(days=int(CRASH_HOLD * 1.4))).isoformat(),
        "status": "open",
        "notes": "CRASH regime triggered",
    }
    print(f"  🚨 CRASH ENTER: 0050 {s_0050}股@{p_0050} + 00631L {s_lev}股@{p_lev}")
    log_event({"type": "open", "id": new["id"], "strategy": "crash_watcher"})
    return pd.concat([positions, pd.DataFrame([new])], ignore_index=True)


def close_crash_if_due(positions: pd.DataFrame, today: date, regime: str) -> tuple[pd.DataFrame, list[dict]]:
    closed = []
    open_crash = positions[(positions["strategy"] == "crash_watcher") & (positions["status"] == "open")]
    for idx, row in open_crash.iterrows():
        entry_dt = pd.to_datetime(row["entry_date"]).date()
        target = entry_dt + timedelta(days=int(CRASH_HOLD * 1.4))
        # Exit on hold expire OR regime exits CRASH for >5 days
        if today < target and regime == "CRASH":
            continue
        ex_a = get_price_at("0050", today)
        ex_b = get_price_at("00631L", today)
        if ex_a == 0 or ex_b == 0: continue
        pnl_a = (ex_a - row["leg_a_entry"]) * row["leg_a_shares"]
        pnl_b = (ex_b - row["leg_b_entry"]) * row["leg_b_shares"]
        total_pnl = pnl_a + pnl_b
        cost = abs(row["leg_a_shares"]) * row["leg_a_entry"] + abs(row["leg_b_shares"]) * row["leg_b_entry"]
        gross = total_pnl / cost * 100 if cost > 0 else 0
        closed.append({
            **row.to_dict(),
            "exit_date": today.isoformat(),
            "leg_a_exit": ex_a, "leg_b_exit": ex_b,
            "gross_pct": round(gross, 2), "net_pct": round(gross - 0.34, 2),
            "pnl_twd": round(total_pnl, 0),
        })
        positions.at[idx, "status"] = "closed"
        print(f"  📤 CLOSE crash_watcher: {gross:+.2f}% pnl NT${total_pnl:+,.0f}")
        log_event({"type": "close", "id": row["id"], "pnl": total_pnl})
    return positions, closed


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════
def main():
    today = date.today()
    print("=" * 72)
    print(f"  Shioaji Paper Trade Engine — {today}")
    print("=" * 72)

    positions = load_positions()
    trades = load_trades()
    print(f"  載入: {len(positions)} positions, {len(trades)} historical trades")
    open_count = len(positions[positions["status"] == "open"]) if not positions.empty else 0
    print(f"  Open positions: {open_count}")

    # 1. Pair trading 2408-2344
    print(f"\n[1/4] Pair Trading 2408-2344")
    pair = check_pair_signal(today)
    if pair is not None:
        z = pair["z"]
        print(f"  z-score: {z:+.2f}")
        positions, closed = close_pair_if_due(positions, today, z)
        for c in closed:
            trades = pd.concat([trades, pd.DataFrame([c])], ignore_index=True)
        positions = open_pair_trade(positions, today, z)
    else:
        print("  ⚠️ daily_state.csv 缺資料")

    # 2. Revenue YoY Deploy-Ready
    print(f"\n[2/4] Revenue YoY Deploy-Ready")
    ryy_signals = check_ryy_signals(today)
    print(f"  今日 deploy-ready: {len(ryy_signals)} 檔")
    positions, closed_r = close_ryy_if_due(positions, today)
    for c in closed_r:
        trades = pd.concat([trades, pd.DataFrame([c])], ignore_index=True)
    for sig in ryy_signals:
        positions = open_ryy_position(positions, today, sig)

    # 3. 0050 Dealer 連買
    print(f"\n[3/4] 0050 Dealer 連買 3d")
    dealer = check_dealer_signal(today)
    if dealer is not None:
        consec = dealer["consec"]
        print(f"  自營商連買 {consec} 天")
        positions, closed_d = close_dealer_if_due(positions, today)
        for c in closed_d:
            trades = pd.concat([trades, pd.DataFrame([c])], ignore_index=True)
        positions = open_dealer_position(positions, today, consec)

    # 4. CRASH Watcher
    print(f"\n[4/4] CRASH Watcher")
    regime = check_crash_regime(today)
    print(f"  目前 regime: {regime}")
    positions, closed_c = close_crash_if_due(positions, today, regime)
    for c in closed_c:
        trades = pd.concat([trades, pd.DataFrame([c])], ignore_index=True)
    if regime == "CRASH":
        positions = open_crash_position(positions, today, regime)

    # Save
    save_positions(positions)
    save_trades(trades)
    print(f"\n  ✅ 寫入 {POSITIONS_CSV.name} ({len(positions)} 筆)")
    print(f"  ✅ 寫入 {TRADES_CSV.name} ({len(trades)} 筆)")

    # Summary
    closed = trades if not trades.empty else None
    if closed is not None and not closed.empty:
        closed["pnl_twd"] = pd.to_numeric(closed["pnl_twd"], errors="coerce")
        total_pnl = closed["pnl_twd"].sum()
        win = (closed["pnl_twd"] > 0).mean() * 100
        print(f"\n  === 累計表現 (n={len(closed)}) ===")
        print(f"    Total PnL: NT${total_pnl:+,.0f}")
        print(f"    Win rate: {win:.1f}%")
        print(f"    Best: NT${closed['pnl_twd'].max():+,.0f}")
        print(f"    Worst: NT${closed['pnl_twd'].min():+,.0f}")


if __name__ == "__main__":
    main()
