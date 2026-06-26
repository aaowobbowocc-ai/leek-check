"""
EH v3.8 — In-trade retail cut signal。

新規則：持倉中每週檢查 retail_pct 變化，觸發即模擬出場。

Cut 條件（OR）：
  (a) 4 週 retail_pct 累積上升 > X pp  → 散戶湧入加速
  (b) 4 週 retail z-score > Y          → 突發異常

訊號實作：
  - 從 entry_date+7d 開始，每 7 天檢查一次 retail metrics
  - 觸發時於該檢查日 close 強制出場（重算 gross_return）
  - 不觸發則保持原 trailing/cut 邏輯

對 weekly 10182 sample 跑 v3.3-C 框架（HS=0.85, trail=50, cut=30, size=10%）+ retail cut。
驗證：alpha 是否從 +14.94pp 提升。

Sweep:
  X (4w retail rise threshold): 1.0, 2.0, 3.0
  Y (z-score threshold):        1.0, 1.5, 2.0
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eh_v3_sprint import (  # noqa: E402
    apply_2_early_cut,
    filter_1_big_holder_slope,
    run_v2_portfolio,
)
from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_FM = ROOT / "data" / "cache" / "finmind" / "finmind"
WEEKLY_T50 = ROOT / "logs" / "weekly_trailing50.csv"


def load_retail_series(ticker: str) -> pd.DataFrame:
    """回傳 (date, retail_pct) sorted by date。"""
    path = CACHE_FM / f"TaiwanStockHoldingSharesPer_{ticker}.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()
    df = df[df["HoldingSharesLevel"] == "more than 1,000,001"].copy()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df["retail_pct"] = 100.0 - df["percent"].astype(float)
    return df[["date", "retail_pct"]]


def apply_retail_in_trade_cut(
    trades: pd.DataFrame,
    rise_threshold: float = 2.0,
    z_threshold: float = 1.5,
) -> pd.DataFrame:
    """
    對每筆 trade，從 entry+7d 起每 7 天檢查 retail_pct 變化。
    觸發時於檢查日 close 出場，重算 gross_return。
    """
    out = []
    cut_count = 0
    for _, t in trades.iterrows():
        retail = load_retail_series(t["ticker"])
        if retail.empty:
            out.append(t.to_dict())
            continue

        ohlcv = load_ohlcv_cache(t["ticker"], CACHE_YF)
        if ohlcv.empty:
            out.append(t.to_dict())
            continue
        ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
        ohlcv = ohlcv.sort_values("date").reset_index(drop=True)

        # entry close
        entry_close_row = ohlcv[ohlcv["date"] <= t["entry_date"]].tail(1)
        if entry_close_row.empty:
            out.append(t.to_dict())
            continue
        entry_close = float(entry_close_row.iloc[0]["close"])

        # 從 entry+7d 起每 7 天檢查
        check_date = t["entry_date"] + timedelta(days=7)
        triggered = False
        new_data = t.to_dict()

        while check_date < t["exit_date"]:
            # 截至 check_date 的 retail series
            r_so_far = retail[retail["date"] <= check_date]
            if len(r_so_far) < 2:
                check_date += timedelta(days=7)
                continue
            cur_retail = float(r_so_far.iloc[-1]["retail_pct"])

            # 4 週前
            r_4w_ago = retail[retail["date"] <= check_date - timedelta(weeks=4)]
            if r_4w_ago.empty:
                check_date += timedelta(days=7)
                continue
            past_retail = float(r_4w_ago.iloc[-1]["retail_pct"])
            rise = cur_retail - past_retail

            # 8 週 z-score
            r_8w = retail[
                (retail["date"] <= check_date)
                & (retail["date"] > check_date - timedelta(weeks=8))
            ]
            z = None
            if len(r_8w) >= 4:
                std = r_8w["retail_pct"].std()
                if std > 0:
                    z = (cur_retail - r_8w["retail_pct"].mean()) / std

            # 觸發條件
            if rise > rise_threshold or (z is not None and z > z_threshold):
                # 找該日 close
                cut_row = ohlcv[ohlcv["date"] <= check_date].tail(1)
                if cut_row.empty:
                    check_date += timedelta(days=7)
                    continue
                cut_close = float(cut_row.iloc[0]["close"])
                cut_actual_date = cut_row.iloc[0]["date"]
                ret = (cut_close / entry_close - 1) * 100

                new_data["exit_date"] = cut_actual_date
                new_data["gross_return_pct"] = round(ret, 2)
                new_data["exit_reason"] = "retail_cut"
                triggered = True
                cut_count += 1
                break

            check_date += timedelta(days=7)

        out.append(new_data)

    print(f"    retail cut triggered: {cut_count}")
    return pd.DataFrame(out)


def main() -> None:
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    df_w = pd.read_csv(WEEKLY_T50)
    df_w["entry_date"] = pd.to_datetime(df_w["entry_date"]).dt.date
    df_w["exit_date"] = pd.to_datetime(df_w["exit_date"]).dt.date
    df_w["ticker"] = df_w["ticker"].astype(str)
    print(f"Weekly trailing-50: {len(df_w)} trades")

    # 先套 #1 + cut=30（v3.3-C baseline）
    df_base = filter_1_big_holder_slope(df_w, min_slope=-0.5)
    df_base = apply_2_early_cut(df_base, cut_days=30)
    print(f"After #1 + cut=30: {len(df_base)} trades")

    # baseline：v3.3-C 已知 alpha +14.94pp
    print("\n" + "=" * 70)
    print("v3.8 — In-trade retail cut sweep")
    print("=" * 70)

    # baseline
    df_t = df_base.copy()
    df_t["size_pct"] = 0.10
    res = run_v2_portfolio(
        df_t, prices_0050, df_t["entry_date"].min(), df_t["exit_date"].max(),
        use_size_col=True,
    )
    print(f"  baseline (v3.3-C, no retail cut)  CAGR {res['cagr']:+.2f}%  alpha {res['alpha']:+.2f}pp")

    # sweep
    print(f"\n  {'config':<40} {'CAGR':>8} {'alpha':>8} {'cut':>5}")
    sweep_configs = [
        (1.0, 1.0),
        (2.0, 1.5),
        (3.0, 2.0),
        (1.5, 1.0),
        (2.0, 99.0),    # 只用 rise threshold
        (99.0, 1.5),    # 只用 z threshold
    ]
    for rise_thr, z_thr in sweep_configs:
        label = f"rise>{rise_thr}pp OR z>{z_thr}"
        df_t = apply_retail_in_trade_cut(df_base, rise_thr, z_thr)
        df_t["size_pct"] = 0.10
        res = run_v2_portfolio(
            df_t, prices_0050, df_t["entry_date"].min(), df_t["exit_date"].max(),
            use_size_col=True,
        )
        print(f"  {label:<40} {res['cagr']:>+7.2f}% {res['alpha']:>+7.2f}pp")


if __name__ == "__main__":
    main()
