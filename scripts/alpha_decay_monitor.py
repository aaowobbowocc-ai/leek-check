"""
Alpha Decay Monitor — 比對 paper trade 實際表現 vs backtest 期望，drift > 30% 警報。

每週執行（Cron 五 14:30），用法：
  python scripts/alpha_decay_monitor.py            — 印報告
  python scripts/alpha_decay_monitor.py --discord  — 同時推 Discord

判定規則（per strategy）：
  - n < 10        : 樣本不足，不評估
  - drift < -30%  : ⚠️ Alpha decay 警報
  - drift < -50%  : 🚨 嚴重衰退，建議停用
  - drift > -30%  : ✅ 健康
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout is not None and getattr(sys.stdout, "encoding", None) \
        and sys.stdout.encoding.lower() != "utf-8" \
        and hasattr(sys.stdout, "buffer"):
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "paper_trades" / "triggered_signals.csv"
CONFIG = ROOT / "config" / "backtest_expected.yaml"


def _load_config():
    """讀 config/backtest_expected.yaml — 單一來源真相"""
    try:
        import yaml
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        return cfg
    except Exception:
        return {"strategies": {}, "config": {}}


_CFG = _load_config()
EXPECTED = _CFG.get("strategies", {})
MIN_SAMPLES = _CFG.get("config", {}).get("min_samples_for_eval", 10)
DRIFT_WARN = _CFG.get("config", {}).get("drift_warn_threshold", -0.30) * 100
DRIFT_SEVERE = _CFG.get("config", {}).get("drift_severe_threshold", -0.50) * 100


def load_closed_signals():
    if not LEDGER.exists():
        return pd.DataFrame()
    df = pd.read_csv(LEDGER, dtype=str)
    if df.empty or "status" not in df.columns:
        return pd.DataFrame()
    closed = df[df["status"] == "closed"].copy()
    if closed.empty:
        return pd.DataFrame()
    closed["net_pct"] = pd.to_numeric(closed["net_pct"], errors="coerce")
    closed = closed.dropna(subset=["net_pct"])
    return closed


def evaluate_strategy(name, sub):
    n = len(sub)
    actual_mean = float(sub["net_pct"].mean())
    actual_win = float((sub["net_pct"] > 0).mean())
    actual_std = float(sub["net_pct"].std()) if n > 1 else 0.0

    exp = EXPECTED.get(name, {})
    exp_mean = exp.get("mean_net", None)
    exp_win = exp.get("win_rate", None)
    source = exp.get("source", "n/a")

    if exp_mean is None:
        status = "❓ unknown"
        drift = None
        verdict = "no baseline"
    elif n < MIN_SAMPLES:
        status = "⏳ pending"
        drift = None
        verdict = f"need {MIN_SAMPLES - n} more samples"
    else:
        drift = (actual_mean - exp_mean) / abs(exp_mean) * 100
        if drift < DRIFT_SEVERE:
            status = "🚨 SEVERE"
            verdict = "建議停用"
        elif drift < DRIFT_WARN:
            status = "⚠️ DECAY"
            verdict = "alpha 衰退警報"
        elif drift > +30:
            status = "🌟 OUTPERFORM"
            verdict = "實單超預期"
        else:
            status = "✅ healthy"
            verdict = "符合預期"

    return {
        "strategy": name,
        "n": n,
        "actual_mean": actual_mean,
        "actual_win": actual_win,
        "actual_std": actual_std,
        "exp_mean": exp_mean,
        "exp_win": exp_win,
        "drift_pct": drift,
        "status": status,
        "verdict": verdict,
        "source": source,
    }


def render_report(rows):
    lines = []
    lines.append("=" * 80)
    lines.append(f"📉 Alpha Decay Monitor — {date.today()}")
    lines.append("=" * 80)

    if not rows:
        lines.append("\n⚪ 尚無 closed 訊號可評估")
        lines.append("  (paper ledger 仍在累積中，此監控等首批 trades 平倉後才有意義)")
        return "\n".join(lines)

    # 先列警報
    alerts = [r for r in rows if r["status"] in ("🚨 SEVERE", "⚠️ DECAY")]
    if alerts:
        lines.append(f"\n⚠️ 警報 ({len(alerts)}):")
        for r in alerts:
            lines.append(f"  {r['status']} {r['strategy']}: "
                         f"實際 {r['actual_mean']:+.2f}% vs 預期 {r['exp_mean']:+.2f}% "
                         f"(drift {r['drift_pct']:+.0f}%) → {r['verdict']}")

    # 全部明細
    lines.append(f"\n📊 全策略表現:")
    lines.append(f"  {'策略':<28} {'n':>4} {'實際%':>8} {'預期%':>8} {'勝率':>7} {'狀態':<14}")
    for r in sorted(rows, key=lambda x: x.get("drift_pct") or 0):
        exp_str = f"{r['exp_mean']:+.2f}" if r['exp_mean'] is not None else "  -  "
        win_str = f"{r['actual_win']:.0%}"
        lines.append(f"  {r['strategy']:<28} {r['n']:>4} "
                     f"{r['actual_mean']:>+7.2f} {exp_str:>8} {win_str:>7} {r['status']:<14}")

    lines.append(f"\n判定規則: n>={MIN_SAMPLES} 才評估 | drift<-30% 警報 | drift<-50% 嚴重")
    return "\n".join(lines)


def push_discord(text):
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        try:
            url = (ROOT / ".discord_webhook").read_text(encoding="utf-8").strip()
        except Exception:
            print("  ⚠️ DISCORD_WEBHOOK_URL 未設定，跳過推播")
            return
    try:
        import requests
        # Discord 限 2000 字
        chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
        for c in chunks:
            requests.post(url, json={"content": f"```\n{c}\n```"}, timeout=10)
        print("  ✅ Discord 推播成功")
    except Exception as e:
        print(f"  ⚠️ Discord 推播失敗: {e}")


def render_briefing_section() -> str:
    """晨報專用版（簡化、無 header）"""
    closed = load_closed_signals()
    if closed.empty:
        return "## 📉 Alpha Decay 監控\n- ⚪ 尚無 closed 訊號（paper ledger 累積中，n=0）\n"

    rows = []
    for strat, sub in closed.groupby("strategy"):
        rows.append(evaluate_strategy(strat, sub))
    if not rows:
        return "## 📉 Alpha Decay 監控\n- ⚪ 尚無 closed 訊號\n"

    lines = ["## 📉 Alpha Decay 監控"]
    alerts = [r for r in rows if r["status"] in ("🚨 SEVERE", "⚠️ DECAY")]
    if alerts:
        lines.append(f"\n⚠️ **警報 ({len(alerts)})**:")
        for r in alerts:
            lines.append(f"- {r['status']} **{r['strategy']}**: "
                         f"實際 {r['actual_mean']:+.2f}% vs 預期 {r['exp_mean']:+.2f}% "
                         f"(drift {r['drift_pct']:+.0f}%)")

    pending = [r for r in rows if r["status"] == "⏳ pending"]
    healthy = [r for r in rows if r["status"] in ("✅ healthy", "🌟 OUTPERFORM")]

    if healthy:
        lines.append(f"\n✅ **健康** ({len(healthy)}):")
        for r in healthy:
            lines.append(f"- {r['strategy']}: n={r['n']}, 實際 {r['actual_mean']:+.2f}% "
                         f"vs 預期 {r['exp_mean']:+.2f}% (drift {r['drift_pct']:+.0f}%)")
    if pending:
        lines.append(f"\n⏳ **累積中** ({len(pending)}):")
        for r in pending:
            lines.append(f"- {r['strategy']}: n={r['n']} → {r['verdict']}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discord", action="store_true")
    args = ap.parse_args()

    closed = load_closed_signals()
    rows = []
    if not closed.empty:
        for strat, sub in closed.groupby("strategy"):
            rows.append(evaluate_strategy(strat, sub))

    report = render_report(rows)
    print(report)

    if args.discord:
        push_discord(report)


if __name__ == "__main__":
    main()
