"""TWSE 官方資料 daily ETL — 每天 14:30 跑(盤後 30 min TWSE 公布完整)

抓 4 筆資料寫進 data/cache/twse/:
  1. T86 三大法人(全市場個股買賣超)
  2. BWIBBU_d 個股 PER / PBR / 殖利率
  3. 寫一份 latest_snapshot.json 給 backend 快速 query

寫進 rolling parquet:
  - data/cache/twse/institutional_latest.parquet (最近 60 個交易日,append)
  - data/cache/twse/per_pbr_latest.parquet (最近 60 個交易日,append)

排程:
  - APScheduler in backend/main.py: 每天 14:30 (weekday)
  - Windows Task Scheduler 備援
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# 強制 stdout/stderr utf-8 防 Windows cp950 emoji crash
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.twse_client import TWSEClient  # noqa: E402

CACHE_DIR = ROOT / "data" / "cache" / "twse"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _log(msg: str):
    from datetime import datetime
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    with (LOG_DIR / "twse_etl.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _last_trading_day() -> date:
    """回最近一個交易日 — 週末跳過."""
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _append_rolling(new_df: pd.DataFrame, parquet_path: Path, keep_days: int = 60):
    """append new_df 到 rolling parquet,只保留最近 keep_days 個交易日."""
    if new_df.empty:
        return
    if parquet_path.exists():
        try:
            old = pd.read_parquet(parquet_path)
            combined = pd.concat([old, new_df], ignore_index=True)
            combined["date"] = pd.to_datetime(combined["date"])
            combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
            # 只留最近 keep_days 個獨立日期
            top_dates = sorted(combined["date"].unique())[-keep_days:]
            combined = combined[combined["date"].isin(top_dates)].sort_values(["date", "ticker"])
            combined.to_parquet(parquet_path, index=False)
            _log(f"  → rolling 寫入 {parquet_path.name}: {len(combined):,} rows, "
                 f"{combined['date'].nunique()} 個交易日")
        except Exception as e:
            _log(f"  ✗ rolling append fail: {e}, 改全覆寫")
            new_df.to_parquet(parquet_path, index=False)
    else:
        new_df.to_parquet(parquet_path, index=False)
        _log(f"  → 新建 {parquet_path.name}: {len(new_df):,} rows")


def run_etl(d: date | None = None) -> dict:
    """跑 TWSE ETL — 回傳 {成功項目: True/False}."""
    if d is None:
        d = _last_trading_day()
    _log(f"=== TWSE ETL 開始 | date={d} ===")

    client = TWSEClient(polite_delay=1.5)
    results = {}

    # ── 1) T86 法人 ──
    _log(f"[1/2] T86 法人 ({d}) ...")
    try:
        inst_df = client.get_institutional_day(d)
        if inst_df.empty:
            _log(f"  ✗ 空 — 可能 {d} 還沒公布(< 14:30)或假日")
            results["institutional"] = False
        else:
            _log(f"  ✓ TWSE 回 {len(inst_df):,} 檔法人資料")
            _append_rolling(inst_df, CACHE_DIR / "institutional_latest.parquet")
            results["institutional"] = True
    except Exception as e:
        _log(f"  ✗ 失敗: {e}")
        results["institutional"] = False

    # ── 2) PER / PBR / 殖利率 ──
    _log(f"[2/2] BWIBBU_d PER/PBR ({d}) ...")
    try:
        per_df = client.get_per_pbr_day(d)
        if per_df.empty:
            _log(f"  ✗ 空 — 可能未公布")
            results["per_pbr"] = False
        else:
            _log(f"  ✓ TWSE 回 {len(per_df):,} 檔 PER 資料")
            _append_rolling(per_df, CACHE_DIR / "per_pbr_latest.parquet")
            results["per_pbr"] = True
    except Exception as e:
        _log(f"  ✗ 失敗: {e}")
        results["per_pbr"] = False

    succ = sum(1 for v in results.values() if v)
    _log(f"=== TWSE ETL 結束 | {succ}/{len(results)} 成功 ===\n")
    return results


def main():
    results = run_etl()
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
