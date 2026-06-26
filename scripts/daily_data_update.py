"""Daily data updater — fetches latest TWSE inst, PER, MOPS revenue.

Replaces FinMind subscription post-2026-05-20.

Run:
  python -m scripts.daily_data_update           # today only
  python -m scripts.daily_data_update --backfill 7  # last 7 days
"""
from __future__ import annotations
import argparse, subprocess, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run(*args):
    cmd = [PY, "-m"] + list(args)
    print(f"\n>>> {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(ROOT), check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, default=0)
    args = parser.parse_args()

    today = date.today()
    if args.backfill > 0:
        # TWSE inst + PER over last N days
        run("scripts.fetch_twse_inst",  "--backfill", str(args.backfill))
        run("scripts.fetch_twse_per",   "--backfill", str(args.backfill))
        # MOPS revenue: last 3 months (covers YoY needs)
        run("scripts.fetch_mops_revenue", "--backfill", "3")
    else:
        run("scripts.fetch_twse_inst")
        run("scripts.fetch_twse_per")
        # MOPS only after 10th of month (when most revenues released)
        if today.day >= 10:
            run("scripts.fetch_mops_revenue")
    # Bounce candidate scanner (短線反彈) — runs on every update
    run("scripts.bounce_candidate_scanner", "--discord")


if __name__ == "__main__":
    main()
