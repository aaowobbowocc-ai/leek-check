"""
Demo new format（ticker name + Discord 對齊）— hand-craft 幾筆訊號重 render。
"""
from __future__ import annotations

import io
import os
import sys
from datetime import date
from pathlib import Path

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
from src.strategy.volume_anomaly import VolumeAnomalySignal  # noqa: E402
from src.strategy.volume_anomaly_scanner import render_anomaly_section  # noqa: E402

# 從 logs/2026-04-26.md 抽出的 10 筆，hand-craft 成 VolumeAnomalySignal
DEMO = [
    ("1570", "TWSE", 36.66, 3.09, 9, "unknown", 13.7, True, 88),
    ("2340", "TWSE", 36.21, 3.10, 7, "unknown", 14.1, True, 88),
    ("2476", "TWSE", 121.39, 3.39, 10, "unknown", 7.4, True, 88),
    ("3312", "TWSE", 38.05, 3.31, 5, "unknown", 2.0, True, 88),
    ("4566", "OTC", 90.55, 3.04, 6, "unknown", -2.1, True, 88),
    ("8016", "OTC", 75.31, 3.00, 7, "unknown", 6.7, True, 88),
    ("1568", "TWSE", 38.27, 3.15, 7, "unknown", 9.7, True, 83),
    ("2459", "TWSE", 23.65, 3.02, 5, "unknown", 2.7, True, 83),
    ("3272", "TWSE", 22.37, 3.43, 5, "unknown", 5.7, False, 73),
    ("6589", "OTC", 49.21, 3.06, 6, "unknown", -9.9, False, 73),
]


def main() -> None:
    signals = []
    for tk, board, close, mz, dz2, direction, p5d, above_ma, score in DEMO:
        sig = VolumeAnomalySignal(
            ticker=tk, as_of=date(2026, 4, 26), board=board,
            modified_z=mz, median_log_vol_60d=10.0, mad_log_vol_60d=1.0,
            days_z_above_2=dz2, days_z_above_3=2, max_z_recent_10d=mz,
            inner_ratio=None, direction=direction,
            close=close, price_change_5d_pct=p5d, above_200ma=above_ma,
            avg_volume_60d=10000, market_cap_btw=None,
            is_ex_dividend_window=False,
            score=score, triggered=True,
        )
        signals.append(sig)

    md = render_anomaly_section(signals, top_n=10)
    out = ROOT / "logs" / "vol_anomaly_preview.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n寫入 {out}")

    # Push to Discord
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if url:
        notifier = DiscordNotifier(url)
        msg = "🔄 **新格式預覽**（含 ticker 名稱 + 等寬對齊）\n\n" + md
        ok = notifier.send(msg)
        print(f"\nDiscord push: {'✅' if ok else '❌'}")


if __name__ == "__main__":
    main()
