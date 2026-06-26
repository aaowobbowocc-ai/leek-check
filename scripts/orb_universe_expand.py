"""
ORB Universe 擴大 — 從 sector_leaders.yaml 抽出 ~50 新 ticker，backfill minute K。

選股標準（散戶熱、波動大、流動性足）:
  1. 不在現有 31 ticker cache 中
  2. 過去 60 日均成交量 >= 3,000 張
  3. 過去 60 日 ATR/price >= 1.5%
  4. 散戶話題股（題材：HBM/AI/矽光/重電/軍工等）

執行流程:
  1. 篩選候選 universe（用 yfinance daily K 預估 vol + ATR）
  2. backfill minute K via FinMind（依月分）
  3. 寫 progress 到 logs/

完成後即可跑 orb_param_sweep.py 重複 24 變體 sweep
"""
from __future__ import annotations

import io
import os
import sys
import time
import yaml
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

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

from src.strategy.volume_anomaly_scanner import load_ohlcv_cache, lookup_ticker_name  # noqa: E402

API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockKBar"
CACHE_MIN = ROOT / "data" / "cache" / "finmind" / "minute"
CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"

# 選股閾值
MIN_AVG_VOL_LOTS = 3_000
MIN_ATR_PCT = 1.5
N_TARGET = 50    # 擴大目標數
START = date(2024, 4, 1)
END = date(2026, 4, 25)

# 現有 cached 31 ticker
EXISTING = {p.stem.split("_")[0] for p in CACHE_MIN.glob("*.parquet")}


def load_sector_leaders() -> list[str]:
    """從 sector_leaders.yaml 抽出所有 ticker。"""
    yml = ROOT / "config" / "sector_leaders.yaml"
    if not yml.exists():
        print(f"❌ {yml} 不存在")
        return []
    data = yaml.safe_load(yml.read_text(encoding="utf-8"))
    tickers = []
    if isinstance(data, dict):
        for sector, info in data.items():
            if isinstance(info, dict):
                # 可能是 {leaders: [...], members: [...]}
                for k in ["leaders", "members", "tickers", "stocks"]:
                    if k in info and isinstance(info[k], list):
                        tickers.extend(str(t) for t in info[k] if t)
            elif isinstance(info, list):
                tickers.extend(str(t) for t in info if t)
    # 也補一些已知散戶熱題材股
    extra = [
        # HBM / AI server
        "3711", "6669", "5274", "4966", "3596", "6515", "6533",
        # 重電 / 軍工
        "1519", "1503", "1513", "3653", "1582",
        # 矽光 / CPO
        "2360", "6533", "3105", "2455",
        # 機殼 / 散熱
        "3149", "3596", "1597",
        # 通路 / 半導體支援
        "3036", "6125", "2347", "3037",
        # 其他散戶熱
        "2308", "6488", "8358", "3260", "2360",
        # 風電 / 綠電
        "1535", "3231",
    ]
    tickers.extend(extra)
    # 去重 + 排除現有
    out = sorted(set(t for t in tickers if t.isdigit() and t not in EXISTING))
    return out


def screen_universe(candidates: list[str]) -> pd.DataFrame:
    """用 yfinance daily K 篩選 vol + ATR。"""
    print(f"\n[1/3] 篩選 {len(candidates)} 候選 ticker（vol + ATR）...")
    rows = []
    for tk in candidates:
        df = load_ohlcv_cache(tk, CACHE_YF)
        if df.empty or len(df) < 60:
            continue
        df["date"] = pd.to_datetime(df["date"])
        recent = df.sort_values("date").tail(90).copy()
        avg_vol = recent["volume"].mean()
        recent["tr"] = recent.apply(
            lambda r: max(
                float(r["high"]) - float(r["low"]),
                abs(float(r["high"]) - float(r["close"])),
                abs(float(r["low"]) - float(r["close"])),
            ),
            axis=1,
        )
        atr14 = recent["tr"].tail(14).mean()
        last_close = float(recent.iloc[-1]["close"])
        atr_pct = (atr14 / last_close * 100) if last_close > 0 else 0
        if avg_vol >= MIN_AVG_VOL_LOTS and atr_pct >= MIN_ATR_PCT:
            rows.append({
                "ticker": tk,
                "name": lookup_ticker_name(tk) or "",
                "avg_vol": avg_vol,
                "atr_pct": atr_pct,
                "last_close": last_close,
                "score": avg_vol * atr_pct,
            })
    df = pd.DataFrame(rows).sort_values("score", ascending=False).head(N_TARGET)
    print(f"  通過 vol≥{MIN_AVG_VOL_LOTS}張 + ATR≥{MIN_ATR_PCT}%: {len(df)} 檔")
    return df


def fetch_minute_day(token: str, ticker: str, d: date) -> pd.DataFrame:
    params = {
        "dataset": DATASET, "data_id": ticker,
        "start_date": d.isoformat(), "end_date": d.isoformat(),
        "token": token,
    }
    for retry in range(3):
        try:
            resp = requests.get(API_URL, params=params, timeout=30)
            payload = resp.json()
            if payload.get("status") == 200:
                rows = payload.get("data") or []
                if not rows:
                    return pd.DataFrame()
                df = pd.DataFrame(rows)
                df["dt"] = pd.to_datetime(
                    df["date"].astype(str) + " " + df["minute"].astype(str)
                )
                return df
        except Exception:
            pass
        time.sleep(0.5)
    return pd.DataFrame()


def cache_path(ticker: str, d: date) -> Path:
    return CACHE_MIN / f"{ticker}_{d.strftime('%Y%m')}.parquet"


def backfill_minute(token: str, ticker: str) -> int:
    """單 ticker backfill 全期間。回傳成功月份數。"""
    months = []
    cur = date(START.year, START.month, 1)
    while cur <= END:
        months.append((cur.year, cur.month))
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)

    success = 0
    for y, m in months:
        cp = cache_path(ticker, date(y, m, 1))
        if cp.exists():
            success += 1
            continue
        cur = date(y, m, 1)
        next_m = date(y + (m == 12), (m % 12) + 1, 1)
        frames = []
        while cur < next_m:
            if cur.weekday() < 5 and START <= cur <= END:
                df = fetch_minute_day(token, ticker, cur)
                if not df.empty:
                    frames.append(df)
                time.sleep(0.05)
            cur += timedelta(days=1)
        if frames:
            out = pd.concat(frames, ignore_index=True)
            out.to_parquet(cp, index=False)
            success += 1
    return success


def main() -> None:
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_KEY") or ""
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    print(f"=== ORB Universe 擴大（現有 {len(EXISTING)} ticker → 目標新增 {N_TARGET} 檔）===")

    candidates = load_sector_leaders()
    print(f"  從 sector_leaders.yaml + 散戶熱題材 抽出 {len(candidates)} 候選")

    pool = screen_universe(candidates)
    out_csv = ROOT / "logs" / "orb_universe_expand_pool.csv"
    pool.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"  寫入 {out_csv.relative_to(ROOT)}")

    print(f"\n  選定的新 universe:")
    print(pool[["ticker", "name", "avg_vol", "atr_pct", "last_close"]].round(2).to_string(index=False))

    # ── Backfill minute K ──
    print(f"\n[2/3] Backfill {len(pool)} ticker × 25 月 minute K...")
    print(f"     估計 ~{len(pool)*1.5:.0f} 分鐘（FinMind Sponsor Pro）")
    t0 = time.time()
    for i, r in pool.iterrows():
        tk = str(r["ticker"])
        sm = backfill_minute(token, tk)
        elapsed = time.time() - t0
        print(f"  [{i+1:>2}/{len(pool)}] {tk} {r['name'][:6]}: {sm}/25 月 cached "
              f"(elapsed {elapsed/60:.1f}m)")

    print(f"\n[3/3] 完成 — 接著跑 orb_param_sweep.py 對全 universe sweep")


if __name__ == "__main__":
    main()
