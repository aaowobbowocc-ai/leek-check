"""
EH v3.4 — Smart Cut（基於 trajectory 診斷）。

新 cut 規則（兩個觸發條件，OR）：
  (a) day 20 還虧 -10%+ → 早砍（85.7% 精準，0 誤殺 winners）
  (b) day 30 還虧 AND 收盤跌破 MA20 -3% 以上 → 砍（86.7% 精準）

對比 v3.2 baseline（cut=30）+ v3.3-C（cut=30, trail=50, size=10%）。

驗證：
  - Monthly 96 trades 是否真的優於 v3.3-C？
  - Weekly 10182 OOS 是否 hold？
"""
from __future__ import annotations

import io
import sys
from datetime import date
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
    filter_1_big_holder_slope,
    run_v2_portfolio,
)
from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
RAW_CSV = ROOT / "logs" / "early_hunter_20260425_160432.csv"
WEEKLY_CSV = ROOT / "logs" / "early_hunter_weekly_v2.csv"


def apply_smart_cut(trades: pd.DataFrame, mode: str = "v34") -> pd.DataFrame:
    """
    v34: (a) ret_d20 < -10%  OR  (b) ret_d30 < 0 AND below MA20 -3%
    v34b: (a) ret_d20 < -10%  OR  (b) ret_d30 < 0  ← keeps full recall + early sweep
    """
    out = []
    cut_a_count = 0
    cut_b_count = 0
    for _, t in trades.iterrows():
        ohlcv = load_ohlcv_cache(t["ticker"], CACHE_YF)
        if ohlcv.empty:
            out.append(t.to_dict())
            continue
        ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
        ohlcv = ohlcv.sort_values("date").reset_index(drop=True).copy()
        ohlcv["ma20"] = ohlcv["close"].rolling(20).mean()

        after = ohlcv[ohlcv["date"] >= t["entry_date"]].reset_index(drop=True)
        if len(after) < 31:
            out.append(t.to_dict())
            continue

        entry_close = float(after.iloc[0]["close"])

        # 條件 (a) day 20 ret < -10%
        c20 = float(after.iloc[20]["close"]) if len(after) > 20 else None
        triggered_a = (
            c20 is not None and (c20 / entry_close - 1) * 100 < -10.0
        )

        # 條件 (b)
        c30 = float(after.iloc[30]["close"])
        ma20_d30 = after.iloc[30]["ma20"]
        ret_d30 = (c30 / entry_close - 1) * 100
        triggered_b = False
        if mode == "v34":
            # 嚴：ret_d30 < 0 AND below MA20 -3%（降 recall）
            if pd.notna(ma20_d30) and ma20_d30 > 0:
                below_ma = (c30 / float(ma20_d30) - 1) * 100
                triggered_b = ret_d30 < 0 and below_ma < -3
        else:
            # v34b：純 ret_d30 < 0（full recall + early sweep）
            triggered_b = ret_d30 < 0

        new = t.to_dict()
        if triggered_a:
            ret = (c20 / entry_close - 1) * 100
            cut_date = after.iloc[20]["date"]
            if cut_date < t["exit_date"]:
                new["exit_date"] = cut_date
                new["gross_return_pct"] = round(ret, 2)
                new["exit_reason"] = "smart_cut_d20_ret"
                cut_a_count += 1
        elif triggered_b:
            ret = (c30 / entry_close - 1) * 100
            cut_date = after.iloc[30]["date"]
            if cut_date < t["exit_date"]:
                new["exit_date"] = cut_date
                new["gross_return_pct"] = round(ret, 2)
                new["exit_reason"] = "smart_cut_d30_ma"
                cut_b_count += 1
        out.append(new)

    print(f"    smart_cut: a={cut_a_count}, b={cut_b_count}")
    return pd.DataFrame(out)


def main() -> None:
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    print("=" * 70)
    print("v3.4 Smart Cut — Monthly in-sample")
    print("=" * 70)

    # Monthly: 用 trailing -50 重模擬作為 base
    from early_hunter_trailing_resim import simulate_trailing_exit
    raw = pd.read_csv(RAW_CSV)
    raw["entry_date"] = pd.to_datetime(raw["entry_date"]).dt.date
    raw["entry_price"] = raw["entry_price"].astype(float)
    raw["ticker"] = raw["ticker"].astype(str)

    rows = []
    for _, r in raw.iterrows():
        ohlcv = load_ohlcv_cache(r["ticker"], CACHE_YF)
        if ohlcv.empty:
            continue
        ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
        ret, exit_d, reason = simulate_trailing_exit(
            ohlcv, r["entry_date"], r["entry_price"], trailing_pp=50.0
        )
        rows.append({
            "ticker": r["ticker"],
            "entry_date": r["entry_date"],
            "exit_date": exit_d,
            "gross_return_pct": round(ret, 2),
            "exit_reason": reason,
            "hold_days": (exit_d - r["entry_date"]).days,
        })
    df_m = pd.DataFrame(rows)

    # 套 #1 + smart cut + size=10%, trail=50
    df_m = filter_1_big_holder_slope(df_m, min_slope=-0.5)

    for mode in ["v34", "v34b"]:
        df_m_smart = apply_smart_cut(df_m.copy(), mode=mode)
        df_m_smart["size_pct"] = 0.10
        start_d = df_m_smart["entry_date"].min()
        end_d = df_m_smart["exit_date"].max()
        res = run_v2_portfolio(
            df_m_smart, prices_0050, start_d, end_d, use_size_col=True,
        )
        print(f"\n  {mode}: CAGR {res['cagr']:+.2f}%  alpha {res['alpha']:+.2f}pp")

    # 對比 v3.3-C（cut=30 普通 + trail=50 + size=10%）
    print("\n  Reference v3.3-C in-sample (cut=30, trail=50, size=10%): alpha +6.42pp")

    # ── Weekly OOS ──
    print("\n" + "=" * 70)
    print("v3.4 Weekly OOS Validation")
    print("=" * 70)
    print("[1/2] 計算 weekly trailing -50pp ...（已有 in cache from v33_weekly_oos）")
    weekly_t50_path = ROOT / "logs" / "weekly_trailing50.csv"
    if weekly_t50_path.exists():
        df_w = pd.read_csv(weekly_t50_path)
    else:
        # Re-run weekly trailing 50 simulation
        weekly = pd.read_csv(WEEKLY_CSV)
        weekly["entry_date"] = pd.to_datetime(weekly["entry_date"]).dt.date
        weekly["ticker"] = weekly["ticker"].astype(str)
        rows = []
        for i, r in enumerate(weekly.itertuples(), 1):
            ohlcv = load_ohlcv_cache(r.ticker, CACHE_YF)
            if ohlcv.empty:
                continue
            ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
            prior = ohlcv[ohlcv["date"] <= r.entry_date]
            if prior.empty:
                continue
            entry_price = float(prior.iloc[-1]["close"])
            ret, exit_d, reason = simulate_trailing_exit(
                ohlcv, r.entry_date, entry_price, trailing_pp=50.0
            )
            rows.append({
                "ticker": r.ticker,
                "entry_date": r.entry_date,
                "exit_date": exit_d,
                "gross_return_pct": round(ret, 2),
                "exit_reason": reason,
                "hold_days": (exit_d - r.entry_date).days,
            })
            if i % 2000 == 0:
                print(f"    [{i}/{len(weekly)}]")
        df_w = pd.DataFrame(rows)
        df_w.to_csv(weekly_t50_path, index=False, encoding="utf-8-sig")

    df_w["entry_date"] = pd.to_datetime(df_w["entry_date"]).dt.date
    df_w["exit_date"] = pd.to_datetime(df_w["exit_date"]).dt.date
    df_w["ticker"] = df_w["ticker"].astype(str)
    print(f"    weekly trailing -50: {len(df_w)} trades")

    print("[2/2] 套 #1 + smart cut")
    df_w_filt = filter_1_big_holder_slope(df_w, min_slope=-0.5)

    for mode in ["v34", "v34b"]:
        df_w_smart = apply_smart_cut(df_w_filt.copy(), mode=mode)
        df_w_smart["size_pct"] = 0.10

        start_d = df_w_smart["entry_date"].min()
        end_d = df_w_smart["exit_date"].max()
        res = run_v2_portfolio(
            df_w_smart, prices_0050, start_d, end_d, use_size_col=True,
        )
        print(f"\n    {mode}: OOS Weekly CAGR {res['cagr']:+.2f}%  alpha {res['alpha']:+.2f}pp")
    print("\n    Reference v3.3-C OOS: alpha +14.94pp")


if __name__ == "__main__":
    main()
