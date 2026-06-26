"""
ORB Step 2：Opening Range Breakout 信號量化（半天工程）。

對 10-15 檔 × 2 年的 minute 資料，偵測 ORB 訊號 + 模擬出場 + 扣摩擦成本。

進場規則：
  09:00-09:15 cumulative volume > 昨日全天 volume × 30%
  AND 09:15 close > 09:00-09:05 high (突破前 5 分高點)
  → 09:15 進場（close price）

出場規則：
  13:20 強制虛擬出場（13:20 close 或 13:00 close fallback）

成本：
  使用 src/backtest/cost_model.py CostConfig（tax_rate_discount=0.5 = 當沖降稅）
  total_cost ≈ 0.49% per round-trip

Go/no-go gate（Step 2 → Step 3）：
  扣成本後 win rate ≥ 55% AND mean net return ≥ +0.3%/筆
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date, datetime, timedelta
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

from src.backtest.cost_model import CostConfig  # noqa: E402
from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockKBar"
CACHE = ROOT / "data" / "cache" / "finmind" / "minute"
CACHE.mkdir(parents=True, exist_ok=True)
CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"

# Universe 選股：交易量夠大 + 波動夠大
# 從 EH weekly 訊號 unique tickers 中，挑選日均成交量 + ATR 都達門檻的
MIN_AVG_VOLUME_LOTS = 5_000        # 日均量 ≥ 5000 張
MIN_AVG_ATR_PCT = 2.0              # 日均 ATR / price ≥ 2%
MAX_PICK = 15                      # 最多挑 15 檔（控制 API 規模）

START = date(2024, 4, 1)
END = date(2026, 4, 24)


def select_liquid_volatile_universe() -> list[str]:
    """從 EH weekly entries 篩選交易量大 + 波動大的 ticker。"""
    eh_csv = ROOT / "logs" / "early_hunter_weekly_v2.csv"
    if not eh_csv.exists():
        # fallback：用幾個已知大型流動性股
        return ["2330", "2317", "2454", "2891", "0050"]

    eh = pd.read_csv(eh_csv)
    candidates = eh["ticker"].astype(str).unique().tolist()
    print(f"  EH unique candidates: {len(candidates)}")

    rows = []
    for tk in candidates:
        df = load_ohlcv_cache(tk, CACHE_YF)
        if df.empty or len(df) < 60:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date")
        # 過去 60 天平均
        recent = df.tail(60)
        avg_vol = recent["volume"].mean()
        # ATR(14) / close
        recent = recent.copy()
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
        rows.append({
            "ticker": tk,
            "avg_vol_60d": avg_vol,
            "atr_pct": atr_pct,
            "last_close": last_close,
        })

    pool = pd.DataFrame(rows)
    pool = pool[
        (pool["avg_vol_60d"] >= MIN_AVG_VOLUME_LOTS)
        & (pool["atr_pct"] >= MIN_AVG_ATR_PCT)
    ]
    # 按 vol × atr 雙指標排序（同時兼顧交易量與波動）
    pool["score"] = pool["avg_vol_60d"] * pool["atr_pct"]
    pool = pool.sort_values("score", ascending=False).head(MAX_PICK)
    print(f"  pre-filter (vol≥{MIN_AVG_VOLUME_LOTS}張, ATR≥{MIN_AVG_ATR_PCT}%): {len(pool)} 檔")
    print(f"\n  挑選 universe（按 vol × ATR score 排序）:")
    print(pool[["ticker", "avg_vol_60d", "atr_pct", "last_close"]].round(2).to_string(index=False))
    return pool["ticker"].astype(str).tolist()


def _cache_path(ticker: str, d: date) -> Path:
    """每個 ticker × month 一個 parquet（month-level cache 較有效率）。"""
    return CACHE / f"{ticker}_{d.strftime('%Y%m')}.parquet"


def fetch_minute_day(token: str, ticker: str, d: date) -> pd.DataFrame:
    """單日 minute K 抓取，含 retry。"""
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
                # parse datetime
                df["dt"] = pd.to_datetime(
                    df["date"].astype(str) + " " + df["minute"].astype(str)
                )
                return df
        except Exception as e:
            if retry == 2:
                print(f"    {ticker} {d} fetch failed: {e}")
        time.sleep(0.5)
    return pd.DataFrame()


def get_or_fetch_month(token: str, ticker: str, year: int, month: int) -> pd.DataFrame:
    """以月為單位 cache。讀 cache，缺則 fetch 該月所有交易日。"""
    cache_p = _cache_path(ticker, date(year, month, 1))
    if cache_p.exists():
        return pd.read_parquet(cache_p)
    # fetch 該月每一天
    cur = date(year, month, 1)
    next_month = date(year + (month == 12), (month % 12) + 1, 1)
    frames = []
    while cur < next_month:
        if cur.weekday() < 5 and START <= cur <= END:
            df = fetch_minute_day(token, ticker, cur)
            if not df.empty:
                frames.append(df)
            time.sleep(0.05)
        cur += timedelta(days=1)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out.to_parquet(cache_p, index=False)
    return out


def detect_orb_signal(day_df: pd.DataFrame, prev_day_total_vol: int) -> dict | None:
    """
    回傳 None = 無訊號 / dict = 有訊號（含 entry_price, exit_price, gross_return）。

    時間切分（台股交易：09:00-13:30）：
      09:00-09:05  = 開盤 5 分高點
      09:00-09:15  = 累積量檢測
      09:15        = 進場時點（用 09:15 minute 的 close）
      13:20        = 強制出場時點
    """
    if day_df.empty or prev_day_total_vol <= 0:
        return None
    day_df = day_df.copy()
    day_df["minute_str"] = day_df["dt"].dt.strftime("%H:%M:%S")

    # 切分
    open_5min = day_df[day_df["minute_str"] <= "09:04:00"]
    open_15min = day_df[day_df["minute_str"] <= "09:14:00"]
    if open_5min.empty or open_15min.empty:
        return None

    # 開盤 5 分高
    open5_high = float(open_5min["high"].max())

    # 09:00-09:15 累積量
    cum_vol_15min = float(open_15min["volume"].sum())
    vol_ratio = cum_vol_15min / prev_day_total_vol

    # 09:15 minute close
    bar_915 = day_df[day_df["minute_str"] == "09:15:00"]
    if bar_915.empty:
        # fallback：09:14 或 09:16
        for fallback in ["09:14:00", "09:16:00", "09:13:00"]:
            bar_915 = day_df[day_df["minute_str"] == fallback]
            if not bar_915.empty:
                break
        if bar_915.empty:
            return None

    close_915 = float(bar_915["close"].iloc[0])

    # 進場條件
    if vol_ratio < 0.30:
        return None
    if close_915 <= open5_high:
        return None

    # 出場：13:20 close（fallback 13:19 / 13:21 / 13:25）
    exit_price = None
    for tt in ["13:20:00", "13:19:00", "13:21:00", "13:25:00", "13:30:00"]:
        bar = day_df[day_df["minute_str"] == tt]
        if not bar.empty:
            exit_price = float(bar["close"].iloc[0])
            break
    if exit_price is None:
        # last bar of day
        exit_price = float(day_df.iloc[-1]["close"])

    gross_return = (exit_price / close_915 - 1) * 100
    return {
        "entry_price": close_915,
        "exit_price": exit_price,
        "gross_return_pct": gross_return,
        "vol_ratio_15min": vol_ratio,
        "open5_high": open5_high,
    }


def main() -> None:
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_KEY") or ""
    if not token:
        print("❌ FINMIND_TOKEN not set"); return

    cost_cfg = CostConfig(tax_rate_discount=0.5)   # 當沖降稅
    total_cost_pct = cost_cfg.total_cost_ratio() * 100
    print(f"當沖摩擦成本：{total_cost_pct:.3f}% per round-trip")
    print(f"  -> 每筆 gross_return 必須 > {total_cost_pct:.3f}% 才算淨賺")

    print(f"\n選 universe（pre-filter: 量大 + 波動大）...")
    sample_tickers = select_liquid_volatile_universe()
    print(f"\n最終 Universe: {len(sample_tickers)} tickers, {START} ~ {END}")

    # ── 月度 cache 抓資料（節省呼叫）──
    print("\n[1/3] 抓取分鐘資料（月度 cache）...")
    months = []
    cur = date(START.year, START.month, 1)
    while cur <= END:
        months.append((cur.year, cur.month))
        nxt_m = (cur.month % 12) + 1
        nxt_y = cur.year + (cur.month == 12)
        cur = date(nxt_y, nxt_m, 1)

    t0 = time.time()
    fetch_idx = 0
    total_fetches = len(sample_tickers) * len(months)
    minute_data: dict[str, pd.DataFrame] = {}
    for tk in sample_tickers:
        frames = []
        for y, m in months:
            fetch_idx += 1
            df_month = get_or_fetch_month(token, tk, y, m)
            if not df_month.empty:
                frames.append(df_month)
            if fetch_idx % 20 == 0:
                elapsed = time.time() - t0
                print(f"    [{fetch_idx}/{total_fetches}] elapsed {elapsed/60:.1f}m")
        full = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not full.empty:
            full["date"] = pd.to_datetime(full["date"]).dt.date
            full = full.sort_values("dt").reset_index(drop=True)
        minute_data[tk] = full
        print(f"    {tk}: {len(full):,} rows total")

    # ── 偵測 ORB 訊號 ──
    print("\n[2/3] 偵測 ORB 訊號 + 模擬出場...")
    all_signals = []
    for tk, full_df in minute_data.items():
        if full_df.empty:
            continue
        # 計算每日 total volume（前一日參考）
        daily_vol = full_df.groupby("date")["volume"].sum().to_dict()
        # 跑每天
        unique_days = sorted(full_df["date"].unique())
        for i, d in enumerate(unique_days):
            if i == 0:
                continue
            prev_day = unique_days[i - 1]
            prev_total = daily_vol.get(prev_day, 0)
            day_df = full_df[full_df["date"] == d]
            sig = detect_orb_signal(day_df, prev_total)
            if sig:
                all_signals.append({
                    "ticker": tk, "date": d,
                    **sig,
                    "prev_day_total_vol": prev_total,
                })

    df_sig = pd.DataFrame(all_signals)
    if df_sig.empty:
        print("  ❌ 0 個 ORB 訊號 — 條件太嚴或資料不夠")
        return

    print(f"  ✓ 觸發 {len(df_sig)} 個訊號 across {df_sig['ticker'].nunique()} tickers")

    # 扣成本後淨報酬
    df_sig["net_return_pct"] = df_sig["gross_return_pct"] - total_cost_pct
    df_sig["is_net_winner"] = df_sig["net_return_pct"] > 0

    out_csv = ROOT / "logs" / "orb_signals.csv"
    df_sig.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"  寫入 {out_csv.relative_to(ROOT)}")

    # ── 統計 + Go/No-go ──
    print("\n" + "=" * 70)
    print("[3/3] 結果統計")
    print("=" * 70)
    n = len(df_sig)
    win_rate = df_sig["is_net_winner"].mean() * 100
    mean_gross = df_sig["gross_return_pct"].mean()
    mean_net = df_sig["net_return_pct"].mean()
    median_net = df_sig["net_return_pct"].median()
    std_net = df_sig["net_return_pct"].std()

    print(f"  訊號數                : {n}")
    print(f"  訊號 / 日              : {n / 250:.2f}（樣本約 250 個交易日）")
    print(f"  Mean gross return     : {mean_gross:+.3f}%")
    print(f"  Mean net return (扣 {total_cost_pct:.2f}%): {mean_net:+.3f}%")
    print(f"  Median net return     : {median_net:+.3f}%")
    print(f"  Std net return        : {std_net:.3f}%")
    print(f"  Win rate (net > 0)    : {win_rate:.1f}%")
    print(f"  最佳單筆 net          : {df_sig['net_return_pct'].max():+.2f}%")
    print(f"  最差單筆 net          : {df_sig['net_return_pct'].min():+.2f}%")

    # 拆按 ticker
    print("\n  按 ticker 拆解：")
    by_tk = df_sig.groupby("ticker").agg(
        n=("net_return_pct", "count"),
        win=("is_net_winner", lambda x: x.mean() * 100),
        mean_net=("net_return_pct", "mean"),
    ).sort_values("mean_net", ascending=False)
    print(by_tk.round(3).to_string())

    # 報酬分布
    print("\n  Net return 分布：")
    bins = [-100, -2, -1, -0.5, 0, 0.5, 1, 2, 100]
    labels = ["<-2%", "-2~-1%", "-1~-0.5%", "-0.5~0%", "0~0.5%", "0.5~1%", "1~2%", ">2%"]
    df_sig["bin"] = pd.cut(df_sig["net_return_pct"], bins=bins, labels=labels)
    for label, count in df_sig["bin"].value_counts().sort_index().items():
        bar = "█" * int(count / max(1, n) * 50)
        print(f"    {label:<10} {count:>4}  {bar}")

    # Go/No-go
    print("\n" + "=" * 70)
    print("Go/No-go 判決")
    print("=" * 70)
    pass_win = win_rate >= 55
    pass_mean = mean_net >= 0.3
    print(f"  win rate ≥ 55%   : {'✅' if pass_win else '❌'} ({win_rate:.1f}%)")
    print(f"  mean net ≥ +0.3% : {'✅' if pass_mean else '❌'} ({mean_net:+.3f}%)")
    if pass_win and pass_mean:
        print("\n  ✅ 通過 — 可進 Step 3 (擴大 sample + walk-forward + EH 相關性)")
    else:
        print("\n  ❌ 未通過 — ORB 策略在此 sample 下無 alpha")
        print("     建議：放棄當沖模組，回到 v3.7 paper trading")


if __name__ == "__main__":
    main()
