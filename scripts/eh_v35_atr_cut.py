"""
EH v3.5 — A 路線：ATR-normalized cut + tighter hard stop。

A1. 用 ATR(14) at entry 取代固定 -10% 門檻：
     cut if ret_d30 < -k × (ATR_at_entry / entry_price × 100)
     k=1.5/2.0/2.5/3.0 sweep。

A2. Hard stop 200MA × 0.85 → 0.90/0.92/0.94 sweep。

兩個變數獨立測試，再 combine。

對比 v3.3-C OOS baseline +14.94pp。
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
WEEKLY_T50 = ROOT / "logs" / "weekly_trailing50.csv"


def simulate_with_atr_cut_and_hardstop(
    ohlcv: pd.DataFrame,
    entry_date: date,
    entry_price: float,
    cut_atr_k: float | None,    # None = no ATR cut, just baseline cut=30 ret<0
    cut_day: int = 30,
    hard_stop_ma_pct: float = 0.85,
    trailing_pp: float = 50.0,
) -> tuple[float, date, str]:
    """模擬：ATR-normalized cut + tighter hard stop + trailing -50。"""
    df = ohlcv.sort_values("date").reset_index(drop=True).copy()
    df["ma200"] = df["close"].rolling(200).mean()
    # ATR(14)
    df["tr"] = df.apply(
        lambda r: max(
            float(r["high"]) - float(r["low"]),
            abs(float(r["high"]) - float(r["close"])),
            abs(float(r["low"]) - float(r["close"])),
        ),
        axis=1,
    )
    df["atr14"] = df["tr"].rolling(14).mean()

    # Entry-day ATR / price
    entry_row = df[df["date"] <= entry_date].tail(1)
    if entry_row.empty or pd.isna(entry_row.iloc[0]["atr14"]):
        atr_pct_entry = 5.0
    else:
        atr_pct_entry = (float(entry_row.iloc[0]["atr14"]) / entry_price) * 100

    after = df[df["date"] >= entry_date].reset_index(drop=True)
    if len(after) < 2:
        return 0.0, entry_date, "no_data"

    peak = 0.0
    for i in range(1, min(1500, len(after))):
        c = float(after.iloc[i]["close"])
        ma = after.iloc[i]["ma200"]
        ret = (c / entry_price - 1) * 100
        if ret > peak:
            peak = ret

        # Hard stop (tighter)
        if pd.notna(ma) and c < float(ma) * hard_stop_ma_pct:
            return ret, after.iloc[i]["date"], "hard_stop"

        # ATR cut at day cut_day
        if i == cut_day:
            if cut_atr_k is None:
                # baseline: cut if ret < 0
                if ret < 0:
                    return ret, after.iloc[i]["date"], "early_cut"
            else:
                threshold = -cut_atr_k * atr_pct_entry
                if ret < threshold:
                    return ret, after.iloc[i]["date"], "atr_cut"

        # Trailing
        if peak >= 5.0 and (peak - ret) >= trailing_pp:
            return ret, after.iloc[i]["date"], "trailing"

    last = after.iloc[-1]
    return (float(last["close"]) / entry_price - 1) * 100, last["date"], "end_of_data"


def re_simulate_weekly(
    cut_atr_k: float | None,
    cut_day: int,
    hard_stop_ma_pct: float,
    trailing_pp: float,
) -> pd.DataFrame:
    weekly = pd.read_csv(ROOT / "logs" / "early_hunter_weekly_v2.csv")
    weekly["entry_date"] = pd.to_datetime(weekly["entry_date"]).dt.date
    weekly["ticker"] = weekly["ticker"].astype(str)

    rows = []
    for r in weekly.itertuples():
        ohlcv = load_ohlcv_cache(r.ticker, CACHE_YF)
        if ohlcv.empty:
            continue
        ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.date
        prior = ohlcv[ohlcv["date"] <= r.entry_date]
        if prior.empty:
            continue
        entry_price = float(prior.iloc[-1]["close"])
        ret, exit_d, reason = simulate_with_atr_cut_and_hardstop(
            ohlcv, r.entry_date, entry_price,
            cut_atr_k=cut_atr_k, cut_day=cut_day,
            hard_stop_ma_pct=hard_stop_ma_pct,
            trailing_pp=trailing_pp,
        )
        rows.append({
            "ticker": r.ticker,
            "entry_date": r.entry_date,
            "exit_date": exit_d,
            "gross_return_pct": round(ret, 2),
            "exit_reason": reason,
            "hold_days": (exit_d - r.entry_date).days,
        })
    return pd.DataFrame(rows)


def main() -> None:
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    print("=" * 70)
    print("v3.5 - A 路線：ATR cut + Hard stop sweep（weekly OOS only）")
    print("=" * 70)

    configs = [
        # (label, cut_atr_k, hard_stop_pct)
        ("v3.3-C ref (cut=30 ret<0, HS=0.85)",     None, 0.85),
        ("ATR k=1.5,            HS=0.85",          1.5,  0.85),
        ("ATR k=2.0,            HS=0.85",          2.0,  0.85),
        ("ATR k=2.5,            HS=0.85",          2.5,  0.85),
        ("ATR k=3.0,            HS=0.85",          3.0,  0.85),
        ("HS=0.90,              cut=30 ret<0",     None, 0.90),
        ("HS=0.92,              cut=30 ret<0",     None, 0.92),
        ("HS=0.94,              cut=30 ret<0",     None, 0.94),
        ("ATR k=2.0 + HS=0.92",                    2.0,  0.92),
        ("ATR k=2.5 + HS=0.92",                    2.5,  0.92),
    ]

    print(f"\n  {'config':<48} {'CAGR':>8} {'alpha':>8} {'n':>4}")
    for label, atr_k, hs in configs:
        df_w = re_simulate_weekly(atr_k, 30, hs, 50.0)
        df_w["entry_date"] = pd.to_datetime(df_w["entry_date"]).dt.date
        df_w["exit_date"] = pd.to_datetime(df_w["exit_date"]).dt.date
        df_w["ticker"] = df_w["ticker"].astype(str)
        df_w = filter_1_big_holder_slope(df_w, min_slope=-0.5)
        df_w["size_pct"] = 0.10
        start_d = df_w["entry_date"].min()
        end_d = df_w["exit_date"].max()
        res = run_v2_portfolio(
            df_w, prices_0050, start_d, end_d, use_size_col=True,
        )
        print(f"  {label:<48} {res['cagr']:>+7.2f}% {res['alpha']:>+7.2f}pp {res['n_trades']:>4}")


if __name__ == "__main__":
    main()
