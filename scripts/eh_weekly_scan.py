"""
EH 週度訊號掃描 — 把 monthly cadence 改成每週 1 號，sample 96 → ~400。

不做 portfolio 模擬（後續 eh_v3_sprint.py 會跑 V2 portfolio）。
只負責：
  1. 對每個 weekly date 掃描全 universe 找 EarlyHunter 訊號
  2. 對每個訊號跑 Trailing -25pp 出場模擬
  3. 輸出 logs/early_hunter_weekly_v2.csv（同 schema 為 trailing_v2.csv）
"""
from __future__ import annotations

import io
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategy.early_hunter import scan_ticker  # noqa: E402

# 重用 backtest 的 loader
sys.path.insert(0, str(ROOT / "scripts"))
from early_hunter_backtest import load_ohlcv, load_revenue, load_universe  # noqa: E402
from early_hunter_trailing_resim import simulate_trailing_exit  # noqa: E402

OUT_CSV = ROOT / "logs" / "early_hunter_weekly_v2.csv"

START = date(2019, 1, 1)
END = date(2026, 4, 24)
THRESHOLD = 60.0
COOLDOWN_WEEKS = 8   # 同 ticker 觸發後 8 週不再觸發


def weekly_dates(start: date, end: date) -> list[date]:
    """每週一 (Monday) 為一個 scan date。"""
    out = []
    cur = start
    while cur.weekday() != 0:
        cur += timedelta(days=1)
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def main() -> None:
    universe = load_universe()
    print(f"Universe: {len(universe)}")

    # Pre-load
    print("[1/3] 載入資料 ...")
    bundles = {}
    skipped = 0
    for i, tk in enumerate(universe, 1):
        ohlcv = load_ohlcv(str(tk))
        if ohlcv.empty:
            skipped += 1
            continue
        bundles[str(tk)] = {"ohlcv": ohlcv, "revenue": load_revenue(str(tk))}
        if i % 500 == 0:
            print(f"    [{i}/{len(universe)}]")
    print(f"    OK: {len(bundles)}, skipped: {skipped}")

    dates = weekly_dates(START, END)
    print(f"\n[2/3] 週度掃描：{len(dates)} 個 scan date，threshold={THRESHOLD}")

    # 同 ticker cooldown 控制
    last_triggered: dict[str, date] = {}
    entries = []
    t0 = time.time()
    for i, d in enumerate(dates, 1):
        for tk, b in bundles.items():
            if tk in last_triggered and (d - last_triggered[tk]).days < COOLDOWN_WEEKS * 7:
                continue
            ohlcv = b["ohlcv"]
            # 必須至少有 365 天歷史才能掃
            past = ohlcv[ohlcv["date"] <= d]
            if len(past) < 252:
                continue
            sig = scan_ticker(tk, past, b["revenue"], d, threshold=THRESHOLD)
            if sig is None or not sig.triggered:
                continue
            entry_price = float(past.iloc[-1]["close"])
            entries.append({
                "ticker": tk,
                "entry_date": d,
                "entry_price": entry_price,
                "entry_score": sig.score,
            })
            last_triggered[tk] = d
        if i % 30 == 0 or i == len(dates):
            elapsed = time.time() - t0
            print(
                f"    [{i:>3}/{len(dates)}] entries={len(entries):>4}  "
                f"elapsed={elapsed/60:.1f}m"
            )

    print(f"\n  總訊號數: {len(entries)}")

    # Trailing exit
    print("\n[3/3] Trailing -25pp 出場模擬 ...")
    rows = []
    for sig in entries:
        ohlcv = bundles[sig["ticker"]]["ohlcv"]
        ret, exit_d, reason = simulate_trailing_exit(
            ohlcv, sig["entry_date"], sig["entry_price"]
        )
        rows.append({
            "ticker": sig["ticker"],
            "entry_date": sig["entry_date"],
            "exit_date": exit_d,
            "gross_return_pct": round(ret, 2),
            "exit_reason": reason,
            "hold_days": (exit_d - sig["entry_date"]).days,
            "entry_score": sig["entry_score"],
        })
    out = pd.DataFrame(rows)
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"  → {OUT_CSV.relative_to(ROOT)} ({len(out)} rows)")

    # Summary
    print("\n=== Summary ===")
    print(f"  N entries: {len(out)}")
    print(f"  Win rate : {(out['gross_return_pct'] > 0).mean() * 100:.1f}%")
    print(f"  Mean ret : {out['gross_return_pct'].mean():+.2f}%")
    print(f"  Median   : {out['gross_return_pct'].median():+.2f}%")
    print(f"  Avg hold : {out['hold_days'].mean():.0f} d")
    print(f"\n  Top 5:")
    print(out.sort_values("gross_return_pct", ascending=False)
          .head(5).to_string(index=False))


if __name__ == "__main__":
    main()
