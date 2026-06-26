"""
Kelly Position Sizing — 根據 backtest win rate / win-loss ratio 計算建議倉位。

公式:
  f_kelly = (p · b − q) / b   其中 p=勝率、q=1−p、b=avg_win/avg_loss
  f_half  = f_kelly / 2        （半 Kelly 為實務安全標準）

實務 cap:
  - 單策略上限 25%（避免過度集中）
  - 單策略下限 3%（再小就不值得執行）
  - 全策略加總上限 80%（保留 20% buffer）

用法:
  python scripts/kelly_position_sizing.py
"""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data" / "assets.json"
TRIGGERED = ROOT / "data" / "paper_trades" / "triggered_signals.csv"
CONFIG = ROOT / "config" / "backtest_expected.yaml"


def _load_config():
    try:
        import yaml
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {"strategies": {}, "config": {}}


_CFG = _load_config()
DYNAMIC_MIN_TRADES = _CFG.get("config", {}).get("dynamic_kelly_min_trades", 10)

STRATEGIES = _CFG.get("strategies", {})

# Cap
MAX_PER_STRAT = 0.25
MIN_PER_STRAT = 0.03
TOTAL_BUDGET = 0.80


def kelly(p, b):
    """Kelly fraction. b = avg_win/abs(avg_loss)."""
    q = 1 - p
    if b <= 0: return 0.0
    f = (p * b - q) / b
    return max(0.0, f)


def load_dynamic_stats():
    """從 triggered_signals.csv 抽出每策略的實單統計（rolling）"""
    if not TRIGGERED.exists():
        return {}
    try:
        import pandas as pd
        df = pd.read_csv(TRIGGERED, dtype=str)
    except Exception:
        return {}
    if df.empty or "status" not in df.columns:
        return {}
    closed = df[df["status"] == "closed"].copy()
    if closed.empty:
        return {}
    closed["net_pct"] = pd.to_numeric(closed["net_pct"], errors="coerce")
    closed = closed.dropna(subset=["net_pct"])
    stats = {}
    for strat, sub in closed.groupby("strategy"):
        n = len(sub)
        if n < DYNAMIC_MIN_TRADES:
            continue
        # 只取最近 30 筆（rolling）
        recent = sub.tail(30)
        wins = recent[recent["net_pct"] > 0]["net_pct"]
        losses = recent[recent["net_pct"] <= 0]["net_pct"]
        if len(wins) == 0 or len(losses) == 0:
            continue
        stats[strat] = {
            "n": len(recent),
            "win_rate": len(wins) / len(recent),
            "avg_win": float(wins.mean()),
            "avg_loss": float(losses.mean()),
            "source": f"paper ledger n={len(recent)}",
        }
    return stats


def render_table(cash, regime_active=True):
    print("=" * 90)
    print(f"💰 Kelly Position Sizing — {date.today()}")
    print(f"   可用現金: NT${cash:,}  |  Total Budget: {TOTAL_BUDGET:.0%}  |  Half-Kelly")
    print("=" * 90)

    dynamic_stats = load_dynamic_stats()
    if dynamic_stats:
        print(f"\n  📊 動態模式：以下策略已累積 ≥{DYNAMIC_MIN_TRADES} 筆，改用實單統計")
        for name in dynamic_stats:
            print(f"     - {name}: n={dynamic_stats[name]['n']}")

    rows = []
    for name, s in STRATEGIES.items():
        # 嘗試找對應的 dynamic stats（容許名稱微差，例如 pair_2408_2344_DRAM vs pair_2408_2344）
        dyn = None
        for dyn_name, dyn_s in dynamic_stats.items():
            if dyn_name in name or name.startswith(dyn_name):
                dyn = dyn_s
                break
        if dyn:
            p = dyn["win_rate"]
            avg_win = dyn["avg_win"]
            avg_loss = dyn["avg_loss"]
            data_source = dyn["source"]
        else:
            p = s["win_rate"]
            avg_win = s["avg_win"]
            avg_loss = s["avg_loss"]
            data_source = "backtest"
        b = avg_win / abs(avg_loss)
        f_kelly = kelly(p, b)
        f_half = f_kelly / 2

        # Regime 暫停的策略：先計算但備註會 zero-out
        active = (not s["regime_dep"]) or regime_active

        # Cap
        f_capped = min(f_half, MAX_PER_STRAT)
        if f_capped < MIN_PER_STRAT:
            f_final_pre = 0.0
            note = f"<{MIN_PER_STRAT:.0%} 太小"
        else:
            f_final_pre = f_capped if active else 0.0
            note = "" if active else "regime 暫停"

        # Edge metric: kelly × n_per_year (annual edge proxy)
        annual_edge = (p * avg_win + (1-p) * avg_loss) * s["n_per_year"]

        full_note = note or s["note"]
        if dyn:
            full_note = f"[實單] {full_note}"

        rows.append({
            "name": name,
            "p": p, "b": b,
            "f_kelly": f_kelly,
            "f_half": f_half,
            "f_final": f_final_pre,
            "active": active,
            "annual_edge": annual_edge,
            "data_source": data_source,
            "note": full_note,
        })

    # 總配置 > 80% 時，按比例縮放
    total_pre = sum(r["f_final"] for r in rows)
    scale = 1.0
    if total_pre > TOTAL_BUDGET:
        scale = TOTAL_BUDGET / total_pre
        for r in rows:
            r["f_final"] = r["f_final"] * scale

    # 排序：active 優先，by annual_edge 高到低
    rows.sort(key=lambda r: (not r["active"], -r["annual_edge"]))

    print(f"\n  {'策略':<28} {'勝率':>5} {'賠率':>5} {'Kelly':>6} {'½K':>5} "
          f"{'年期望%':>8} {'建議倉位':>10} {'NT$':>10}  備註")
    print(f"  {'-'*28} {'-'*5} {'-'*5} {'-'*6} {'-'*5} {'-'*8} {'-'*10} {'-'*10}  {'-'*40}")
    for r in rows:
        cash_alloc = int(cash * r["f_final"])
        print(f"  {r['name']:<28} "
              f"{r['p']*100:>4.0f}% "
              f"{r['b']:>4.2f}x "
              f"{r['f_kelly']*100:>5.1f}% "
              f"{r['f_half']*100:>4.1f}% "
              f"{r['annual_edge']:>+7.1f}% "
              f"{r['f_final']*100:>9.1f}% "
              f"{cash_alloc:>10,}  "
              f"{r['note']}")

    total_final = sum(r["f_final"] for r in rows)
    print(f"\n  全策略加總: {total_final*100:.1f}%  |  保留 buffer: {(1-total_final)*100:.1f}%")
    if scale < 1.0:
        print(f"  ⚠️ Pre-scale 總和 {total_pre*100:.0f}% > {TOTAL_BUDGET:.0%}，已按 {scale:.2f} 比例縮放")

    print()
    print("  公式: f_kelly = (p·b − q)/b  ;  half-Kelly = f_kelly / 2")
    print("  Cap: 單策略 max 25% / min 3% / 全策略 max 80%")
    print()


def main():
    cash = 0
    if ASSETS.exists():
        cash = json.loads(ASSETS.read_text(encoding="utf-8")).get("cash", 0)
    if cash <= 0:
        cash = 500000  # fallback

    # 簡單 regime gate: 若 LATE_BULL 則 regime_dep=True 的策略歸零
    regime_active = True
    try:
        sys.path.insert(0, str(ROOT))
        from src.risk.strategy_regime_gate import detect_current_regime
        r = detect_current_regime()
        if r.cycle == "late_bull" or r.trend == "bear":
            regime_active = False
            print(f"\n⚠️ 當前 regime: cycle={r.cycle}, trend={r.trend} "
                  f"→ regime-dep 策略全部暫停")
    except Exception as e:
        print(f"  (regime gate 讀取失敗: {e})")

    render_table(cash, regime_active=regime_active)


if __name__ == "__main__":
    main()
