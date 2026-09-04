"""fadatsai (crypto perp bot) 晨報整合 — 顯示 fadatsai 表現 vs INVEST。

用法：在 morning_briefing.py 中呼叫 render_fadatsai_section(project_root)。

讀取 fadatsai 跑出的 trade journal + position 檔案,顯示:
  - 7d / 30d / since-inception 表現
  - 持倉數 + USDT equity
  - vs INVEST 同期相關性
  - 警示 (Sharpe < 3 / DD > -10% / API error)

如果 fadatsai 未啟動 (paper / live 都沒跑) 則顯示 "等待部署" 訊息。
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import numpy as np

# fadatsai 專案路徑 (跟 INVEST 同層)
FADATSAI_ROOT = Path("C:/Users/USER/Desktop/fadatsai")

# Audit-derived expected metrics (deployment baseline)
EXPECTED_SHARPE_BULL = 18  # bull market
EXPECTED_SHARPE_BEAR = 8   # bear market
EXPECTED_SHARPE_BLEND = 11 # 80% bull + 20% bear
EXPECTED_MDD = -20         # tolerance for bear

# Alert thresholds (from PRODUCTION_SPEC.md)
WARN_SHARPE_30D = 3
PAGE_SHARPE_30D = 1
WARN_DD = -10
PAGE_DD = -15


def _load_trade_journal() -> pd.DataFrame:
    """Load fadatsai trade journal (live or paper)."""
    candidates = [
        FADATSAI_ROOT / "logs" / "trade_journal.csv",
        FADATSAI_ROOT / "logs" / "paper_trades.csv",
    ]
    for f in candidates:
        if f.exists():
            try:
                df = pd.read_csv(f)
                if not df.empty and "exit_ts" in df.columns:
                    df["exit_ts"] = pd.to_datetime(df["exit_ts"], utc=True, errors="coerce")
                    df = df.dropna(subset=["exit_ts"])
                    return df
            except Exception:
                continue
    return pd.DataFrame()


def _load_open_positions() -> int:
    f = FADATSAI_ROOT / "logs" / "positions.json"
    if not f.exists():
        return 0
    try:
        data = json.loads(f.read_text())
        if isinstance(data, dict):
            return len(data.get("positions", data))
        if isinstance(data, list):
            return len(data)
    except Exception:
        pass
    return 0


def _load_equity() -> float:
    """Read latest equity from daily_report.csv if available."""
    f = FADATSAI_ROOT / "logs" / "daily_report.csv"
    if not f.exists():
        return 0.0
    try:
        df = pd.read_csv(f)
        if "equity_usdt" in df.columns and not df.empty:
            return float(df["equity_usdt"].iloc[-1])
    except Exception:
        pass
    return 0.0


def _compute_window_metrics(df: pd.DataFrame, days: int) -> dict | None:
    """Sharpe / WR / MDD over trailing `days`."""
    if df.empty:
        return None
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    window = df[df["exit_ts"] >= cutoff]
    if len(window) < 5:
        return None
    if "pnl" not in window.columns:
        return None
    pnls = window["pnl"].astype(float)

    # Group by exit_date for Sharpe calculation
    window = window.copy()
    window["exit_date"] = window["exit_ts"].dt.tz_convert("UTC").dt.date
    if "sig_id" in window.columns:
        daily = window.groupby(["exit_date", "sig_id"])["pnl"].sum().reset_index()
        pivot = daily.pivot(index="exit_date", columns="sig_id", values="pnl").fillna(0.0)
    else:
        pivot = window.groupby("exit_date")["pnl"].sum().to_frame()
    if pivot.empty:
        return None
    port_daily = pivot.mean(axis=1)
    if port_daily.std() == 0:
        return None

    ann_days = 365
    sharpe = float(port_daily.mean() * ann_days / (port_daily.std() * np.sqrt(ann_days)))
    equity = (1 + port_daily / 100).cumprod()
    dd = (equity - equity.cummax()) / equity.cummax() * 100
    return {
        "n_trades": len(window),
        "wr":       float((pnls > 0).mean() * 100),
        "sharpe":   sharpe,
        "mdd":      float(dd.min()),
        "total_pnl_pct": float(equity.iloc[-1] - 1) * 100,
    }


def render_fadatsai_section(project_root: Path) -> str:
    """晨報區段 — fadatsai 狀態 + 對照 audit 期望"""
    lines = ["## 🤖 [Paper] fadatsai 加密永續引擎\n"]

    if not FADATSAI_ROOT.exists():
        lines.append("> fadatsai 專案目錄未找到。")
        return "\n".join(lines)

    df = _load_trade_journal()
    open_pos = _load_open_positions()
    equity = _load_equity()

    # 描述
    lines.append("<details><summary>📖 fadatsai 是什麼 (點開)</summary>\n")
    lines.append("**Binance USDT-M 永續合約量化機器人**:")
    lines.append(f"- 22 檔妖幣 + 信號組合 (tight_comp_short / fund_*_sq) 共 ~52 strategies")
    lines.append(f"- 8-layer audit 後預期 Sharpe **{EXPECTED_SHARPE_BULL} (牛)** / **{EXPECTED_SHARPE_BEAR} (熊)** / "
                 f"**{EXPECTED_SHARPE_BLEND} (混合長期)**")
    lines.append(f"- vs BTC 相關性 **-0.22 (天然 hedge)**")
    lines.append(f"- 同時持倉上限 100,leverage 3x,Daily DD -5% 強制止損")
    lines.append(f"- 部署規格詳見 `fadatsai/PRODUCTION_SPEC.md`")
    lines.append("\n</details>\n")

    # Equity / position 概況
    lines.append("### 部署狀態\n")
    if df.empty and open_pos == 0:
        lines.append("> ⏳ **尚未部署** — paper trading 未產生 trades。等待 audit-deploy gate 通過。")
        lines.append("\n```")
        lines.append(f"預期 Sharpe (mixed):  {EXPECTED_SHARPE_BLEND}")
        lines.append(f"建議部署比例:          25% (NT$148K of NT$594K)")
        lines.append(f"Cold wallet split:    20% of crypto (NT$30K)")
        lines.append(f"```")
        return "\n".join(lines) + "\n"

    lines.append(f"- 開倉部位數: **{open_pos}** / 100 cap")
    if equity > 0:
        lines.append(f"- 帳戶 equity: **${equity:,.2f}**")
    lines.append("")

    # 表現
    lines.append("### 表現 vs Audit 期望\n")
    lines.append("| 期間 | Trades | WR | Sharpe | MDD | 期望 Sharpe | 警示 |")
    lines.append("|---|---:|---:|---:|---:|---:|:---:|")
    for label, days in [("7d", 7), ("30d", 30), ("90d", 90)]:
        m = _compute_window_metrics(df, days)
        if m is None:
            lines.append(f"| {label} | <5 | — | — | — | {EXPECTED_SHARPE_BLEND} | — |")
            continue
        warn = ""
        if m["sharpe"] < PAGE_SHARPE_30D and days >= 30:
            warn = "🔴 alpha 崩潰"
        elif m["sharpe"] < WARN_SHARPE_30D and days >= 30:
            warn = "🟡 alpha 衰退"
        if m["mdd"] < PAGE_DD:
            warn = "🔴 DD 過大"
        elif m["mdd"] < WARN_DD:
            warn = warn or "🟡 DD 警戒"
        lines.append(f"| {label} | {m['n_trades']:,} | {m['wr']:.1f}% | "
                     f"**{m['sharpe']:.2f}** | {m['mdd']:.2f}% | "
                     f"{EXPECTED_SHARPE_BLEND} | {warn or '✅'} |")
    lines.append("")

    # Tail-risk reminder
    m_30 = _compute_window_metrics(df, 30)
    if m_30 and m_30["sharpe"] < WARN_SHARPE_30D:
        lines.append("> ⚠️ **30d Sharpe 低於警戒線**。檢查:")
        lines.append("> 1. 是否進入 bear regime? (BTC 30d return < -10% 時 fund_low_sq 應自動 disable)")
        lines.append("> 2. Funding rate 結構是否變化?")
        lines.append("> 3. 是否有特定 coin 訊號失效? 重跑 `additional_audits.py` 看 per-coin")
        lines.append("")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(render_fadatsai_section(Path(".")))
