"""
測試 DiscordNotifier.send_briefing — 用既有 logs/*.md 模擬完整推送。
"""
from __future__ import annotations

import io
import os
import sys
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


def main() -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        print("❌ DISCORD_WEBHOOK_URL 未設"); return

    # 找最近的 briefing markdown
    briefings = sorted(ROOT.glob("logs/2026-*.md"), reverse=True)
    if not briefings:
        print("❌ logs/ 找不到任何晨報 .md"); return

    latest = briefings[0]
    date_str = latest.stem
    print(f"使用 briefing: {latest.relative_to(ROOT)} ({latest.stat().st_size:,} bytes)")

    notifier = DiscordNotifier(url)
    ok = notifier.send_briefing(date_str, latest)
    if ok:
        print("✅ 推送成功 → 去 Discord 確認摘要 + 附件")
    else:
        print("❌ 推送失敗")


if __name__ == "__main__":
    main()
