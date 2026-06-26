"""
每月策略 health check — 對所有上線策略對照 backtest vs 真實 paper trade。

跑時機：每月 1 號（自動）
功能：
  1. 對 unified_paper_ledger 中各策略：
     - 累計 paper trade mean / win
     - vs backtest 預期 alpha 比較
     - 如果 mean < backtest mean × 50% → Discord 警報「策略可能失效」
  2. 對 ORB ledger 同樣處理
  3. 對 transactions（真實單）統計實現損益
  4. Push monthly summary to Discord
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

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

UNIFIED_LEDGER = ROOT / "data" / "paper_trades" / "unified_ledger.csv"
ORB_LEDGER = ROOT / "data" / "paper_trades" / "orb_ledger.csv"
TX_LOG = ROOT / "data" / "transactions.jsonl"

EXPECTED_ALPHA = {
    "pair_2408_2344": 3.16,
    "0050_dealer_buy_3d": 1.23,
    "ORB_2408": 0.99,
    "ORB_2485": 1.58,
}


def health_check_unified():
    """檢查 unified_paper_ledger 各策略表現"""
    if not UNIFIED_LEDGER.exists():
        return {"status": "no_data", "strategies": {}}

    df = pd.read_csv(UNIFIED_LEDGER, dtype=str)
    if df.empty:
        return {"status": "empty", "strategies": {}}

    closed = df[df["status"] == "closed"].copy()
    if closed.empty:
        return {"status": "no_closed", "open_count": (df["status"] == "open").sum(),
                "strategies": {}}

    closed["net_pct"] = pd.to_numeric(closed["net_pct"], errors="coerce")
    closed["expected_alpha"] = pd.to_numeric(closed["expected_alpha"], errors="coerce")

    out = {}
    for strat, sub in closed.groupby("strategy"):
        n = len(sub)
        if n < 1: continue
        actual_mean = sub["net_pct"].mean()
        expected = sub["expected_alpha"].iloc[0]
        wins = (sub["net_pct"] > 0).sum()
        win_rate = wins / n * 100
        ratio = actual_mean / expected if expected != 0 else 0
        status = "✅ ok" if ratio > 0.5 else ("⚠️ degraded" if ratio > 0.2 else "🔴 failed")
        out[strat] = {
            "n": n, "actual_mean": actual_mean, "expected": expected,
            "win_rate": win_rate, "ratio": ratio, "status": status,
        }
    return {"status": "ok", "strategies": out}


def health_check_orb():
    """檢查 ORB ledger"""
    if not ORB_LEDGER.exists():
        return {"status": "no_data", "n": 0}
    df = pd.read_csv(ORB_LEDGER)
    closed = df[df["status"] == "closed"] if "status" in df.columns else df
    if closed.empty:
        return {"status": "no_closed", "n": 0}
    closed["net_return_pct"] = pd.to_numeric(closed["net_return_pct"], errors="coerce")
    return {
        "status": "ok",
        "n": len(closed),
        "mean": closed["net_return_pct"].mean(),
        "win_rate": (closed["is_winner"].astype(str).str.lower() == "true").mean() * 100,
        "expected": 0.99,
    }


def real_transactions_summary():
    """真實交易紀錄統計"""
    if not TX_LOG.exists():
        return {"n": 0, "realized_pnl": 0}
    realized = []
    n_buy = 0
    n_sell = 0
    for line in TX_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        try:
            t = json.loads(line)
            if t["action"] == "buy":
                n_buy += 1
            elif t["action"] == "sell":
                n_sell += 1
                realized.append(t.get("realized_pnl", 0))
        except Exception:
            continue
    return {
        "n_buy": n_buy, "n_sell": n_sell,
        "n_total": n_buy + n_sell,
        "realized_pnl": sum(realized),
    }


def main():
    today = date.today()
    print(f"=== Monthly Health Check ({today}) ===")

    unified = health_check_unified()
    orb = health_check_orb()
    tx = real_transactions_summary()

    print(f"\n[Unified Paper Ledger]")
    if unified["status"] == "ok" and unified["strategies"]:
        for strat, info in unified["strategies"].items():
            print(f"  {strat}")
            print(f"    n={info['n']}, mean={info['actual_mean']:+.2f}%, "
                  f"expected={info['expected']:+.2f}%, win={info['win_rate']:.0f}%, "
                  f"ratio={info['ratio']:.2f} {info['status']}")
    else:
        print(f"  {unified['status']}")

    print(f"\n[ORB Paper Trade]")
    if orb["status"] == "ok":
        print(f"  n={orb['n']}, mean={orb['mean']:+.2f}%, "
              f"expected={orb['expected']:+.2f}%, win={orb['win_rate']:.0f}%")
    else:
        print(f"  {orb['status']}")

    print(f"\n[真實交易記錄]")
    print(f"  buy: {tx['n_buy']}, sell: {tx['n_sell']}, "
          f"realized PnL: {tx['realized_pnl']:+,.0f}")

    # Discord push
    discord_msg = f"📊 **{today.strftime('%Y-%m')} 月策略 Health Check**\n\n"
    if unified["status"] == "ok" and unified["strategies"]:
        discord_msg += "**Paper trade 策略:**\n"
        for strat, info in unified["strategies"].items():
            discord_msg += (f"- {strat}: {info['actual_mean']:+.2f}% "
                            f"(預期 {info['expected']:+.2f}%) {info['status']} "
                            f"(n={info['n']})\n")
    else:
        discord_msg += "📋 Paper ledger 尚無 closed 紀錄\n"
    if orb["status"] == "ok":
        discord_msg += f"\n**ORB:** mean {orb['mean']:+.2f}%, win {orb['win_rate']:.0f}% (n={orb['n']})\n"
    discord_msg += f"\n**真實交易:** {tx['n_total']} 筆，已實現 P&L {tx['realized_pnl']:+,.0f}\n"

    try:
        from src.notify.discord_client import DiscordNotifier
        url = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if url:
            DiscordNotifier(url).send(discord_msg)
            print(f"\n✅ Discord 推播完成")
    except Exception as e:
        print(f"\nDiscord 失敗: {e}")


if __name__ == "__main__":
    main()
