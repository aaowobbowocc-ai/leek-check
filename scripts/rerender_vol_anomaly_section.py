"""
從 volume_anomaly_history.parquet 重新 render Vol Anomaly 區段（不重掃）。
讓我們在 FinMind ban 期間能驗證新格式（含 ticker name + Discord 對齊）。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategy.volume_anomaly import VolumeAnomalySignal  # noqa: E402
from src.strategy.volume_anomaly_scanner import render_anomaly_section  # noqa: E402

HISTORY = ROOT / "data" / "state" / "volume_anomaly_history.parquet"


def main() -> None:
    df = pd.read_parquet(HISTORY)
    print(f"history rows: {len(df)}")
    print(f"cols: {list(df.columns)}")
    # 取最新一天
    df["as_of"] = pd.to_datetime(df["as_of"]).dt.date
    latest = df["as_of"].max()
    today = df[df["as_of"] == latest]
    print(f"latest day: {latest}, signals: {len(today)}")

    # 重建 VolumeAnomalySignal objects
    signals = []
    for _, r in today.iterrows():
        sig = VolumeAnomalySignal(
            ticker=str(r["ticker"]),
            as_of=r["as_of"],
            board=str(r.get("board", "")),
            close=float(r.get("close", 0)),
            modified_z=float(r.get("modified_z", 0)),
            days_z_above_2=int(r.get("days_z_above_2", 0)),
            direction=str(r.get("direction", "unknown")),
            internal_external_ratio=float(r["internal_external_ratio"])
                if pd.notna(r.get("internal_external_ratio")) else None,
            price_change_5d_pct=float(r.get("price_change_5d_pct", 0)),
            above_200ma=bool(r.get("above_200ma", False)),
            score=float(r.get("score", 0)),
            triggered=bool(r.get("triggered", False)),
            volume_today=int(r.get("volume_today", 0)),
            volume_z=float(r.get("volume_z", 0)) if pd.notna(r.get("volume_z")) else 0,
        )
        signals.append(sig)

    md = render_anomaly_section(signals, top_n=10)
    out = ROOT / "logs" / "vol_anomaly_section_preview.md"
    out.write_text(md, encoding="utf-8")
    print(f"\n寫入 {out.relative_to(ROOT)}")
    print("\n" + "=" * 70)
    print("PREVIEW:")
    print("=" * 70)
    print(md)


if __name__ == "__main__":
    main()
