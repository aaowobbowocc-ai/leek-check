"""
ADR 隔日跳空 Alpha Backtest。

研究問題：
  1. TSMC ADR (TSM) / NVDA 夜盤大漲跌 → 隔日台股開盤跳空？
  2. 跳空後 09:30+ 動能延續率？
  3. 是否存在「跟 ADR 進場」的 alpha？

策略候選：
  V1. ADR ≥ +X% → 隔日 long 2330 開盤買 / 13:20 平倉
  V2. ADR ≤ -X% → 隔日 short 2330 (限 0050 對沖)
  V3. ADR 漲跌 > X% → fade open（散戶 over-react）

驗收：
  - OOS test mean > 0 + CI low > 0 + n >= 10 → Tier A
  - 否則繼續探索其他 trigger
"""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
COST = 0.34
CUTOFF = pd.Timestamp("2025-06-01")
SEED = 42
N_BOOT = 1000


def fetch_adr_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """抓 ADR 日線（含 open/close）。"""
    t = yf.Ticker(symbol)
    df = t.history(start=start, end=end, auto_adjust=False)
    if df.empty:
        return df
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.date
    return df[["date", "open", "high", "low", "close"]]


def load_tw_stock(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def compute_alpha(
    adr_df: pd.DataFrame,
    tw_df: pd.DataFrame,
    pump_threshold: float,
    direction: str = "long",  # "long" or "short"
) -> pd.DataFrame:
    """
    對齊：ADR 收盤 (T 日美東晚) → TW 隔日 (T+1) 開盤
    台股 09:00 開 ≈ 美東前一晚 21:00 收

    所以 ADR T 日 close (美時間) → TW T+1 day open (台時間)
    """
    adr_df = adr_df.copy().sort_values("date").reset_index(drop=True)
    adr_df["adr_ret"] = adr_df["close"].pct_change()
    # 隔日台股對應 = T+1 (美東 T 收盤 = 台北 T+1 早上)
    adr_df["tw_match_date"] = adr_df["date"]  # ADR T 對應 TW T+1
    # 但 yfinance ADR date = 美東 trading date，對應台股 T+1

    # 把 adr_df 的 date 後移 1 天 = 對應的台股交易日
    adr_df["match_date"] = pd.to_datetime(adr_df["date"]) + pd.Timedelta(days=1)
    adr_df["match_date"] = adr_df["match_date"].dt.date

    # tw_df 計算 open vs close
    tw_df = tw_df.copy()
    tw_df["tw_open"] = tw_df["open"].astype(float)
    tw_df["tw_close"] = tw_df["close"].astype(float)
    tw_df["prev_close"] = tw_df["close"].shift(1)
    tw_df["gap"] = (tw_df["tw_open"] / tw_df["prev_close"] - 1) * 100  # 開盤跳空 %
    tw_df["intraday_ret"] = (tw_df["tw_close"] / tw_df["tw_open"] - 1) * 100  # 開到收

    merged = pd.merge(
        adr_df[["match_date", "adr_ret"]].rename(columns={"match_date": "date"}),
        tw_df[["date", "tw_open", "tw_close", "prev_close", "gap", "intraday_ret"]],
        on="date", how="inner"
    )
    merged["adr_ret_pct"] = merged["adr_ret"] * 100

    # 篩 trigger
    if direction == "long":
        triggered = merged[merged["adr_ret_pct"] >= pump_threshold].copy()
        triggered["gross_pct"] = triggered["intraday_ret"]  # 開盤買、收盤賣
    else:  # short
        triggered = merged[merged["adr_ret_pct"] <= -pump_threshold].copy()
        triggered["gross_pct"] = -triggered["intraday_ret"]  # 開盤短、收盤平

    triggered["net_pct"] = triggered["gross_pct"] - COST
    return triggered


def stats_walk_forward(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {"n": 0, "full_mean": np.nan, "full_win": np.nan,
                "test_n": 0, "test_mean": np.nan, "test_win": np.nan,
                "ci_low": np.nan, "ci_high": np.nan}
    rets = df["net_pct"].values
    test = df[pd.to_datetime(df["date"]) >= CUTOFF]
    rng = np.random.default_rng(SEED)
    if n >= 5:
        boot = np.array([rng.choice(rets, size=n, replace=True).mean() for _ in range(N_BOOT)])
        ci_low, ci_high = np.percentile(boot, [2.5, 97.5])
    else:
        ci_low, ci_high = np.nan, np.nan
    return {
        "n": n,
        "full_mean": rets.mean(),
        "full_win": (rets > 0).mean() * 100,
        "test_n": len(test),
        "test_mean": test["net_pct"].mean() if len(test) else np.nan,
        "test_win": (test["net_pct"] > 0).mean() * 100 if len(test) else np.nan,
        "ci_low": ci_low,
        "ci_high": ci_high,
    }


def main():
    print("=" * 80)
    print("ADR 隔日跳空 Alpha Backtest")
    print("=" * 80)

    # 抓 ADR 資料
    print("\n[1/4] 抓 ADR 歷史 (TSM, NVDA)...")
    tsm = fetch_adr_history("TSM", "2024-01-01", "2026-04-26")
    nvda = fetch_adr_history("NVDA", "2024-01-01", "2026-04-26")
    print(f"  TSM:  {len(tsm)} days  ({tsm['date'].min()} ~ {tsm['date'].max()})")
    print(f"  NVDA: {len(nvda)} days ({nvda['date'].min()} ~ {nvda['date'].max()})")

    # 載入台股
    print("\n[2/4] 載入台股 cache...")
    tw_targets = {
        "2330": "台積電",
        "0050": "台灣 50",
    }
    tw_data = {}
    for tk, name in tw_targets.items():
        df = load_tw_stock(tk)
        if df.empty:
            print(f"  ❌ {tk}: 無 cache"); continue
        df = df[df["date"] >= date(2024, 1, 1)]
        tw_data[tk] = df
        print(f"  ✅ {tk} {name}: {len(df)} days")

    # 跑變體
    print("\n[3/4] 跑變體...")
    variants = []
    for adr_name, adr_df in [("TSM", tsm), ("NVDA", nvda)]:
        for tk, tw_df in tw_data.items():
            for pump in [0.5, 1.0, 1.5, 2.0, 3.0]:
                for direction in ["long", "short"]:
                    triggered = compute_alpha(adr_df, tw_df, pump, direction)
                    st = stats_walk_forward(triggered)
                    if st["n"] >= 5:
                        variants.append({
                            "adr": adr_name,
                            "tw_ticker": tk,
                            "tw_name": tw_targets[tk],
                            "pump_threshold": pump,
                            "direction": direction,
                            **st,
                        })

    res = pd.DataFrame(variants)
    if res.empty:
        print("❌ 無結果"); return

    # 分 Tier
    def tier(r):
        if r["test_n"] >= 10 and r["test_mean"] > 0 and r["ci_low"] > 0:
            return "A"
        if r["test_n"] >= 5 and r["test_mean"] > 0:
            return "B"
        return "C"
    res["tier"] = res.apply(tier, axis=1)

    out_csv = ROOT / "logs" / "adr_overnight_alpha.csv"
    res.sort_values("test_mean", ascending=False).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[4/4] 寫入 {out_csv.relative_to(ROOT)}")

    # Summary
    print("\n" + "=" * 80)
    print("結果統計")
    print("=" * 80)

    tier_a = res[res["tier"] == "A"]
    tier_b = res[res["tier"] == "B"]
    print(f"\nTier A: {len(tier_a)}")
    print(f"Tier B: {len(tier_b)}")
    print(f"Tier C: {len(res) - len(tier_a) - len(tier_b)}")

    for t_label in ["A", "B"]:
        sub = res[res["tier"] == t_label].sort_values("test_mean", ascending=False)
        if sub.empty:
            continue
        print(f"\n=== Tier {t_label} ===")
        print(f"  {'ADR':<5} {'TW':<6} {'pump':>5} {'dir':<6} "
              f"{'n':>4} {'OOS m/w':>14} {'CI':>20}")
        for _, r in sub.head(20).iterrows():
            print(f"  {r['adr']:<5} {r['tw_ticker']:<6} "
                  f"{r['pump_threshold']:>4.1f}% {r['direction']:<6} "
                  f"{r['n']:>4} {r['test_mean']:>+5.2f}%/{r['test_win']:>3.0f}% "
                  f"[{r['ci_low']:>+5.2f}, {r['ci_high']:>+5.2f}]")


if __name__ == "__main__":
    main()
