"""
強制重抓行情快取 — 用於 GUI 一鍵更新或 cron 補資料。

用法:
  python scripts/refresh_quotes.py                  # 全部策略 ticker（預設）
  python scripts/refresh_quotes.py 2330 2308 6182   # 指定 ticker
  python scripts/refresh_quotes.py --full           # 含全市場 1962 檔（30+ 分鐘）
  python scripts/refresh_quotes.py --skip-global    # 跳過海外 ETF 與大盤指數

涵蓋策略：
  - 持股 P/L 顯示
  - 配對交易（2408-2344 / 2330-3711 / 2454-3711）
  - ORB paper trade（2408 / 2485）
  - Foreign 連買訊號（0050, 006208, 00881, 2308）
  - 部署排程 TW ETF（0050, 00881, 00947, 00646）
  - 部署排程 海外（EWY, DXJ, IAU, GLD）
  - DXJ 加碼 trigger（SPY, JPY=X）
  - 全球宏觀（^TWII, ^GSPC, ^VIX, USD/TWD）
  - Watchlist（config/watchlist.yaml）
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
GLOBAL_CACHE = ROOT / "data" / "cache" / "yfinance" / "global"
ASSETS = ROOT / "data" / "assets.json"
WATCHLIST = ROOT / "config" / "watchlist.yaml"

# === 策略需要的固定 ticker ===
TW_STRATEGY_TICKERS = [
    # 配對交易
    "2408", "2344", "2330", "3711", "2454",
    # ORB
    "2485",
    # Foreign 連買
    "0050", "006208", "00881", "2308",
    # 部署排程 TW ETF
    "00947", "00646", "00919",
    # 大型權值（多因子比對 + 集中度監控）
    "2317", "2412", "2891", "2882",
    # 個案追蹤（user 詢問過的）
    "6182",
]

# 海外 ticker（永豐金 + macro dashboard）
GLOBAL_TICKERS = [
    "EWY", "DXJ", "IAU", "GLD",      # 部署目標
    "SPY", "JPY=X",                   # DXJ trigger
    "TSM", "SOXX", "NVDA",            # 夜盤訊號
    "^GSPC", "^VIX",                  # 大盤
    "TWD=X",                          # 匯率
]

# TW 大盤指數（特殊處理 — yfinance ^TWII）
TW_INDICES = ["^TWII"]


def get_holdings_tickers():
    if not ASSETS.exists(): return []
    try:
        a = json.loads(ASSETS.read_text(encoding="utf-8"))
        return [h["ticker"] for h in a.get("holdings", {}).get("long_term", [])]
    except Exception:
        return []


def get_watchlist_tickers():
    if not WATCHLIST.exists(): return []
    try:
        wl = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8")) or {}
        out = []
        for sec in wl.values() if isinstance(wl, dict) else []:
            if isinstance(sec, list):
                for item in sec:
                    if isinstance(item, dict) and "ticker" in item:
                        out.append(str(item["ticker"]))
                    elif isinstance(item, str):
                        out.append(item)
        return out
    except Exception:
        return []


def refresh_tw(tk: str, days: int = 30) -> str:
    """TW 個股 / ETF：自動處理 .TW / .TWO suffix。"""
    cache_p = TW_CACHE / f"{tk}.parquet"
    raw = None
    used_suffix = ""
    for sfx in [".TW", ".TWO"]:
        try:
            r = yf.download(f"{tk}{sfx}", period=f"{days}d", progress=False, auto_adjust=True)
            if not r.empty:
                raw = r
                used_suffix = sfx
                break
        except Exception:
            continue
    if raw is None or raw.empty:
        return f"❌ {tk}: 抓不到（.TW / .TWO 都失敗）"

    if hasattr(raw.columns, "get_level_values"):
        raw.columns = raw.columns.get_level_values(0)
    df_new = raw.reset_index().rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    df_new["date"] = pd.to_datetime(df_new["date"]).dt.date

    if cache_p.exists():
        existing = pd.read_parquet(cache_p)
        existing["date"] = pd.to_datetime(existing["date"]).dt.date
        merged = pd.concat([existing, df_new], ignore_index=True)
        merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
    else:
        merged = df_new.sort_values("date").reset_index(drop=True)
    merged.to_parquet(cache_p, index=False)

    last = merged.iloc[-1]
    return f"✅ {tk}{used_suffix}: 最新 {last['date']} close={last['close']:.2f}"


def refresh_global(tk: str, days: int = 30) -> str:
    """海外 ticker / 大盤指數：直接用原代號。"""
    GLOBAL_CACHE.mkdir(parents=True, exist_ok=True)
    safe_name = tk.replace("=", "_").replace("^", "")
    cache_p = GLOBAL_CACHE / f"{safe_name}.parquet"
    try:
        raw = yf.download(tk, period=f"{days}d", progress=False, auto_adjust=True)
    except Exception as e:
        return f"❌ {tk}: {e}"
    if raw.empty:
        return f"❌ {tk}: 無資料"

    if hasattr(raw.columns, "get_level_values"):
        raw.columns = raw.columns.get_level_values(0)
    df_new = raw.reset_index().rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    df_new["date"] = pd.to_datetime(df_new["date"]).dt.date

    if cache_p.exists():
        existing = pd.read_parquet(cache_p)
        existing["date"] = pd.to_datetime(existing["date"]).dt.date
        merged = pd.concat([existing, df_new], ignore_index=True)
        merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
    else:
        merged = df_new.sort_values("date").reset_index(drop=True)
    merged.to_parquet(cache_p, index=False)
    last = merged.iloc[-1]
    return f"✅ {tk}: 最新 {last['date']} close={last['close']:.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--full", action="store_true",
                    help="全市場 1962 檔（30+ 分鐘，給 daily scanner 用）")
    ap.add_argument("--skip-global", action="store_true", help="跳過海外 + 大盤")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    today = date.today()

    if args.tickers:
        # 指定 ticker 模式
        tw_targets = [t for t in args.tickers if t.replace(".", "").isdigit() or len(t) <= 6]
        global_targets = [t for t in args.tickers if t not in tw_targets]
    else:
        # 預設：策略全套
        tw_targets = list(set(get_holdings_tickers() + TW_STRATEGY_TICKERS))
        global_targets = GLOBAL_TICKERS + TW_INDICES if not args.skip_global else []

    print(f"=== 重抓行情快取 ({today}) ===")
    print(f"  TW: {len(tw_targets)} 檔  Global: {len(global_targets)} 檔")

    print(f"\n[1/2] TW ticker:")
    for tk in sorted(tw_targets):
        print(f"  {refresh_tw(tk, days=args.days)}")

    if global_targets:
        print(f"\n[2/2] Global / 指數:")
        for tk in global_targets:
            print(f"  {refresh_global(tk, days=args.days)}")

    if args.full:
        print(f"\n[3/3] Full universe（1962 檔）— 跑 finmind_backfill...")
        import subprocess
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "finmind_backfill.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=3600,
            encoding="utf-8", errors="replace",
        )
        print(result.stdout[-2000:] if result.stdout else "no stdout")

    print(f"\n✅ 重抓完成")


if __name__ == "__main__":
    main()
