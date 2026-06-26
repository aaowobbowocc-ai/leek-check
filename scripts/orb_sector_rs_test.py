"""
ORB + 族群 RS 動態 exit 測試。

對 15 檔 ORB universe 定義 sector：
  面板：3481, 2409
  DRAM/IC：2337, 2344, 2408, 2485
  晶圓代工：2303, 6770
  電子組件：2367, 2313, 6443
  化學/化工：1303, 1802, 1717, 1815

對每個 ORB 進場訊號 (logs/orb_signals.csv)，計算：
  - 同 sector 其他成員在 09:15 的累積報酬（從當日 open）
  - 持有期間 sector 平均報酬變化

測試 5 個變體：
  V1. Baseline（無族群訊號）
  V2. Entry filter：族群其他成員 09:15 平均 ret > 0
  V3. Entry filter：族群其他成員 09:15 平均 ret > +0.5%
  V4. Entry V2 + Exit if 持有中族群 ret 下降 > 0.5pp
  V5. 純 Exit on sector reversal（無 entry filter）
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

from src.backtest.cost_model import CostConfig  # noqa: E402

CACHE_MIN = ROOT / "data" / "cache" / "finmind" / "minute"
ORB_CSV = ROOT / "logs" / "orb_signals.csv"
COST = CostConfig(tax_rate_discount=0.5).total_cost_ratio() * 100   # 0.49%

SECTORS = {
    "面板":     ["3481", "2409"],
    "DRAM/IC":  ["2337", "2344", "2408", "2485"],
    "晶圓代工": ["2303", "6770"],
    "電子組件": ["2367", "2313", "6443"],
    "化學":     ["1303", "1802", "1717", "1815"],
}
TICKER_TO_SECTOR = {tk: sec for sec, tks in SECTORS.items() for tk in tks}


def load_day(ticker: str, d: date) -> pd.DataFrame:
    """讀某 ticker 某天 minute K。"""
    cache_p = CACHE_MIN / f"{ticker}_{d.strftime('%Y%m')}.parquet"
    if not cache_p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(cache_p)
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] == d].copy()
    df["minute_str"] = df["dt"].dt.strftime("%H:%M:%S")
    return df.sort_values("dt").reset_index(drop=True)


def get_sector_return_at(d: date, sector: str, exclude_ticker: str, time_str: str) -> float | None:
    """同族群其他成員在 time_str 時刻的平均累積報酬（從當日 open）。"""
    members = [tk for tk in SECTORS[sector] if tk != exclude_ticker]
    rets = []
    for tk in members:
        day = load_day(tk, d)
        if day.empty:
            continue
        # 開盤價（09:00 close 或第一筆）
        open_bar = day[day["minute_str"] == "09:00:00"]
        if open_bar.empty:
            open_bar = day.head(1)
        open_p = float(open_bar.iloc[0]["close"])
        if open_p <= 0:
            continue
        # 指定時刻 close
        target_bar = day[day["minute_str"] == time_str]
        if target_bar.empty:
            target_bar = day[day["minute_str"] <= time_str].tail(1)
        if target_bar.empty:
            continue
        cur_p = float(target_bar.iloc[0]["close"])
        rets.append((cur_p / open_p - 1) * 100)
    return sum(rets) / len(rets) if rets else None


def simulate_with_sector(
    signals: pd.DataFrame,
    entry_filter: float | None = None,    # 進場 sector_ret 門檻
    exit_drop_pp: float | None = None,    # 持有中 sector_ret 下降幅度
) -> pd.DataFrame:
    """
    回傳每筆 trade 的 net return + reason。
    """
    out = []
    skipped_filter = 0
    for _, sig in signals.iterrows():
        ticker = str(sig["ticker"])
        d = pd.to_datetime(sig["date"]).date()
        sector = TICKER_TO_SECTOR.get(ticker)
        if sector is None:
            continue

        # Entry filter
        sec_ret_915 = get_sector_return_at(d, sector, ticker, "09:15:00")
        if entry_filter is not None:
            if sec_ret_915 is None or sec_ret_915 < entry_filter:
                skipped_filter += 1
                continue

        # 沒 entry filter 通過則進場
        entry_price = float(sig["entry_price"])

        # 載入自己的 day data
        own_day = load_day(ticker, d)
        if own_day.empty:
            continue
        bars_after = own_day[own_day["minute_str"] >= "09:15:00"].reset_index(drop=True)
        if bars_after.empty:
            continue

        # 模擬持有：每 15 分鐘檢查 sector 是否反轉
        exit_reason = "default_1320"
        exit_price = None
        check_times = ["09:30:00", "09:45:00", "10:00:00", "10:30:00",
                       "11:00:00", "11:30:00", "12:00:00", "12:30:00",
                       "13:00:00", "13:20:00"]
        for t in check_times:
            bar = own_day[own_day["minute_str"] == t]
            if bar.empty:
                bar = own_day[own_day["minute_str"] <= t].tail(1)
            if bar.empty:
                continue
            c = float(bar.iloc[0]["close"])

            # 持有中 sector 反轉
            if exit_drop_pp is not None and sec_ret_915 is not None:
                sec_ret_now = get_sector_return_at(d, sector, ticker, t)
                if sec_ret_now is not None:
                    drop = sec_ret_915 - sec_ret_now
                    if drop > exit_drop_pp:
                        exit_price = c
                        exit_reason = f"sector_reversal_{t}"
                        break

            # 13:20 強制
            if t == "13:20:00":
                exit_price = c
                exit_reason = "default_1320"
                break

        if exit_price is None:
            # fallback: last bar
            exit_price = float(own_day.iloc[-1]["close"])

        gross = (exit_price / entry_price - 1) * 100
        net = gross - COST
        out.append({
            "ticker": ticker, "date": d, "sector": sector,
            "sec_ret_915": sec_ret_915,
            "gross": gross, "net": net,
            "reason": exit_reason,
        })

    df = pd.DataFrame(out)
    return df, skipped_filter


def report(label: str, df: pd.DataFrame, skipped: int) -> None:
    if df.empty:
        print(f"  {label:<50} (0 trades — all filtered)")
        return
    win = (df["net"] > 0).mean() * 100
    mean = df["net"].mean()
    median = df["net"].median()
    flag = " ⭐" if win >= 55 and mean >= 0.3 else ""
    print(
        f"  {label:<50} n={len(df):>3}  win {win:>5.1f}%  "
        f"mean {mean:>+6.2f}%  median {median:>+6.2f}%{flag}"
    )
    if skipped > 0:
        print(f"     (skipped by filter: {skipped})")


def main() -> None:
    sigs = pd.read_csv(ORB_CSV)
    sigs["date"] = pd.to_datetime(sigs["date"]).dt.date
    print(f"ORB 訊號: {len(sigs)} 個")
    print(f"摩擦成本: {COST:.3f}%")

    print("\n" + "=" * 80)
    print("ORB + 族群 RS 5 個變體")
    print("=" * 80)

    # V1: baseline (no filter, no sector exit)
    df, sk = simulate_with_sector(sigs, entry_filter=None, exit_drop_pp=None)
    report("V1 baseline (no sector signal)", df, sk)

    # V2: entry filter, sector_ret > 0
    df, sk = simulate_with_sector(sigs, entry_filter=0.0, exit_drop_pp=None)
    report("V2 entry filter sector_ret > 0%", df, sk)

    # V3: entry filter, sector_ret > 0.5%
    df, sk = simulate_with_sector(sigs, entry_filter=0.5, exit_drop_pp=None)
    report("V3 entry filter sector_ret > +0.5%", df, sk)

    # V4: entry V2 + exit on sector reversal -0.5pp
    df, sk = simulate_with_sector(sigs, entry_filter=0.0, exit_drop_pp=0.5)
    report("V4 entry sec>0 + exit on sec drop 0.5pp", df, sk)

    # V5: exit only (no entry filter)
    df, sk = simulate_with_sector(sigs, entry_filter=None, exit_drop_pp=0.5)
    report("V5 exit only on sec drop 0.5pp", df, sk)

    # V6: V4 with stricter entry (sec > 1%)
    df, sk = simulate_with_sector(sigs, entry_filter=1.0, exit_drop_pp=0.5)
    report("V6 entry sec>1% + exit on sec drop 0.5pp", df, sk)

    # V7: very strict entry, no exit
    df, sk = simulate_with_sector(sigs, entry_filter=1.0, exit_drop_pp=None)
    report("V7 entry sec>1% only", df, sk)

    # V8: very loose exit
    df, sk = simulate_with_sector(sigs, entry_filter=None, exit_drop_pp=1.0)
    report("V8 exit only on sec drop 1.0pp", df, sk)

    print("\n" + "=" * 80)
    print("注意：'sector reversal' 算法 = 持有中族群 avg ret 從進場時下降 > X pp 即出")


if __name__ == "__main__":
    main()
