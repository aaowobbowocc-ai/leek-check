"""
Discord webhook 連線測試 — 確認 .env 的 DISCORD_WEBHOOK_URL 能成功 post。

執行：python scripts/test_discord_webhook.py
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime
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
        print("❌ DISCORD_WEBHOOK_URL 未設定")
        print("   在 config/.env 加入：")
        print("     DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>")
        return

    print(f"Webhook URL: {url[:50]}...{url[-10:]}")

    notifier = DiscordNotifier(url)
    if not notifier.is_configured():
        print("❌ URL 格式錯誤")
        return

    ts = datetime.now().strftime("%H:%M:%S")
    msg = (
        f"🧪 **Discord webhook 連線測試 {ts}**\n\n"
        "如果你看到這則訊息，代表 webhook 設定成功！\n\n"
        "之後 INVEST 晨報會自動送來這。\n\n"
        "—— Test from `scripts/test_discord_webhook.py`"
    )
    ok = notifier.send(msg)
    if ok:
        print("✅ 訊息送出成功（請去 Discord 頻道確認看到）")
    else:
        print("❌ 送訊失敗，看 log 排查")


if __name__ == "__main__":
    main()
