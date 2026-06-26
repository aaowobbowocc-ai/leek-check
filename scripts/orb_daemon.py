"""
ORB Paper Trade Daemon — 全 Python 版本（取代 bat 內的 loop 邏輯）。

每秒檢查時間：
  09:20 → 跑 morning ORB 偵測
  09:20 ~ 13:25 → 每 5 分鐘 refresh 儀表板
  13:25 → 跑 close 平倉
  13:30 → 顯示最終 snapshot 後結束（waitForKey）

使用：python scripts/orb_daemon.py
（bat 只負責 chcp + cd + 呼叫此檔）
"""
from __future__ import annotations

import io
import subprocess
import sys
import time as time_module
from datetime import datetime, time as dt_time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

DASHBOARD = ROOT / "scripts" / "holdings_status.py"
ORB_PAPER = ROOT / "scripts" / "orb_paper_trade.py"

MORNING_TIME = dt_time(9, 20)
CLOSE_TIME = dt_time(13, 25)
END_TIME = dt_time(13, 30)
REFRESH_MINUTES = 5


def run_python(script: Path, *args: str) -> None:
    """同步呼叫 python script，輸出直接顯示。"""
    subprocess.run([PYTHON, str(script), *args], check=False)


def show_dashboard() -> None:
    print("\033[2J\033[H", end="")  # 清螢幕 + 游標歸零（ANSI）
    run_python(DASHBOARD)


def main() -> None:
    morning_done = False
    close_done = False
    last_refresh_minute = -1

    print("\n" + "=" * 70)
    print("  INVEST ORB Paper Trade Daemon")
    print("  Whitelist: 2408, 2485")
    print(f"  Schedule:  {MORNING_TIME.strftime('%H:%M')} morning  /  "
          f"{CLOSE_TIME.strftime('%H:%M')} close")
    print("=" * 70 + "\n")

    show_dashboard()

    try:
        while True:
            now = datetime.now()
            now_t = now.time()

            # 假日直接結束
            if now.weekday() >= 5:
                print(f"\n[{now.strftime('%H:%M:%S')}] 週末，daemon 結束")
                break

            # 結束時間
            if now_t >= END_TIME:
                print(f"\n[{now.strftime('%H:%M:%S')}] 13:30 後，本日結束")
                show_dashboard()
                break

            # 13:25 close
            if now_t >= CLOSE_TIME and not close_done:
                print(f"\n{'='*70}\n  [{now.strftime('%H:%M:%S')}] RUN: close 平倉\n{'='*70}")
                run_python(ORB_PAPER, "--mode", "close")
                close_done = True
                last_refresh_minute = -1  # 強制下次 refresh
                continue

            # 09:20 morning
            if now_t >= MORNING_TIME and not morning_done:
                print(f"\n{'='*70}\n  [{now.strftime('%H:%M:%S')}] RUN: morning ORB 偵測\n{'='*70}")
                run_python(ORB_PAPER, "--mode", "morning")
                morning_done = True
                last_refresh_minute = -1
                continue

            # 每 N 分鐘 refresh 儀表板
            cur_min = now.minute
            if cur_min % REFRESH_MINUTES == 0 and cur_min != last_refresh_minute:
                show_dashboard()
                if not morning_done:
                    delta = (datetime.combine(now.date(), MORNING_TIME) - now).total_seconds() / 60
                    print(f"\n[{now.strftime('%H:%M:%S')}] 等待 09:20 morning 偵測... "
                          f"還有 {delta:.0f} 分")
                elif not close_done:
                    delta = (datetime.combine(now.date(), CLOSE_TIME) - now).total_seconds() / 60
                    print(f"\n[{now.strftime('%H:%M:%S')}] 等待 13:25 close 平倉... "
                          f"還有 {delta:.0f} 分")
                last_refresh_minute = cur_min

            time_module.sleep(30)

    except KeyboardInterrupt:
        print("\n\n[使用者中斷]")

    print("\n按 Enter 關閉...")
    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass


if __name__ == "__main__":
    main()
