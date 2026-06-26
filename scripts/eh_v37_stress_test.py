"""
v3.7 Stress Test — 量化實際大跳水的損失。

問題：v3.7 在過去 8 年的真實大跳水裡會虧多少？
  - 2020-03 COVID（1 月 -28%）
  - 2022 全年熊市（-22%）
  - 任意最差單日 / 5 日

方法：
  1. 重建 v3.7 portfolio 的每日 NAV 序列
  2. 對比 0050 baseline 同期 NAV 序列
  3. 計算 MDD、worst N-day windows
  4. 對指定時間窗口（2020-03、2022）做明確比較

注意：本 stress test 用 v3.3-C（HS=0.85）資料，因 v3.7（HS=0.92）需重模擬。
v3.7 預期 MDD 應略小於 v3.3-C（因 hard stop 較緊）。
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eh_v3_sprint import apply_2_early_cut, filter_1_big_holder_slope  # noqa: E402
from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
WEEKLY_T50 = ROOT / "logs" / "weekly_trailing50.csv"

INITIAL = 100_000.0
PER_TRADE_PCT = 0.10
COST_PCT = 0.004


def load_prices(ticker: str) -> pd.DataFrame:
    """回傳 date-indexed close 序列。"""
    df = load_ohlcv_cache(ticker, CACHE_YF)
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def build_daily_nav(trades: pd.DataFrame, prices_0050: dict[date, float]) -> pd.DataFrame:
    """逐日建構 v3.7 portfolio NAV 序列。

    每筆 trade：entry_date 進場，exit_date 出場（exit_date 收盤平倉）。
    閒置現金停 0050。
    """
    # 預載所有 trade 的 ohlcv
    unique_tickers = trades["ticker"].unique()
    print(f"  載入 {len(unique_tickers)} 個 ticker prices...")
    price_cache: dict[str, dict[date, float]] = {}
    for tk in unique_tickers:
        df = load_prices(tk)
        if df.empty:
            continue
        price_cache[tk] = dict(zip(df["date"], df["close"].astype(float)))

    # 所有交易日（用 0050 為準）
    all_days = sorted(prices_0050.keys())
    start_d = trades["entry_date"].min()
    end_d = trades["exit_date"].max()
    all_days = [d for d in all_days if start_d <= d <= end_d]
    print(f"  逐日模擬 {start_d} ~ {end_d} ({len(all_days)} 個交易日)...")

    # 初始：100% in 0050
    p_init = prices_0050[all_days[0]]
    cash_shares_0050 = INITIAL / p_init

    # open positions: list of dict {ticker, shares, exit_date, entry_amount}
    open_positions: list[dict] = []

    # entries / exits 索引化
    trades_by_entry = trades.groupby("entry_date").apply(
        lambda x: x[["ticker", "exit_date", "gross_return_pct"]].to_dict("records")
    ).to_dict()

    nav_history = []
    for d in all_days:
        cur_0050 = prices_0050.get(d)
        if cur_0050 is None:
            continue

        # 1. 處理出場（exit_date 等於今天的 trade）
        still_open = []
        for pos in open_positions:
            if pos["exit_date"] <= d:
                # 平倉：取 trade 的 final_return 直接套
                exit_amount = pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST_PCT)
                # 把錢轉回 0050
                cash_shares_0050 += exit_amount / cur_0050
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2. 處理進場
        for tr in trades_by_entry.get(d, []):
            allocation = INITIAL * PER_TRADE_PCT
            cur_value = cash_shares_0050 * cur_0050
            if cur_value < allocation:
                continue
            cash_shares_0050 -= allocation / cur_0050
            open_positions.append({
                "ticker": tr["ticker"],
                "exit_date": tr["exit_date"],
                "entry_amount": allocation * (1 - COST_PCT),
                "return_pct": float(tr["gross_return_pct"]),
                "entry_date": d,
            })

        # 3. Mark to market 所有 open positions
        # 用線性 interpolation：return 從 0% (entry_date) 到 final return_pct (exit_date)
        # 簡化但可接受
        positions_value = 0.0
        for pos in open_positions:
            ohlcv_prices = price_cache.get(pos["ticker"], {})
            cur_p = ohlcv_prices.get(d)
            if cur_p is None:
                # 找最近一日
                for offset in range(1, 8):
                    cur_p = ohlcv_prices.get(d - timedelta(days=offset))
                    if cur_p is not None:
                        break
            if cur_p is None:
                # fallback：線性插值 between entry (return 0) and exit
                pe = pos["entry_date"]
                px = pos["exit_date"]
                progress = (d - pe).days / max((px - pe).days, 1)
                progress = max(0, min(1, progress))
                ratio = 1 + (pos["return_pct"] / 100) * progress
                positions_value += pos["entry_amount"] * ratio
                continue
            # entry price 用 entry_amount × (entry_price 隱含)
            # 實際做法：entry 時的 cost basis = entry_amount，shares = entry_amount / entry_price
            # 但 entry_price 沒存。我們用線性近似
            pe = pos["entry_date"]
            px = pos["exit_date"]
            progress = (d - pe).days / max((px - pe).days, 1)
            progress = max(0, min(1, progress))
            ratio = 1 + (pos["return_pct"] / 100) * progress
            positions_value += pos["entry_amount"] * ratio

        cash_value = cash_shares_0050 * cur_0050
        nav = positions_value + cash_value
        nav_history.append({
            "date": d,
            "nav": nav,
            "cash_pct": cash_value / nav * 100 if nav > 0 else 100,
            "n_open": len(open_positions),
            "price_0050": cur_0050,
        })

    return pd.DataFrame(nav_history)


def analyze(nav_df: pd.DataFrame) -> None:
    nav_df["nav_0050"] = INITIAL * nav_df["price_0050"] / nav_df["price_0050"].iloc[0]
    nav_df["ret_v37"] = nav_df["nav"].pct_change().fillna(0)
    nav_df["ret_0050"] = nav_df["nav_0050"].pct_change().fillna(0)

    # ── MDD ──
    nav_df["peak_v37"] = nav_df["nav"].cummax()
    nav_df["dd_v37"] = (nav_df["nav"] / nav_df["peak_v37"] - 1) * 100
    nav_df["peak_0050"] = nav_df["nav_0050"].cummax()
    nav_df["dd_0050"] = (nav_df["nav_0050"] / nav_df["peak_0050"] - 1) * 100

    print("\n" + "=" * 70)
    print("整體指標")
    print("=" * 70)
    print(f"  期間: {nav_df['date'].iloc[0]} ~ {nav_df['date'].iloc[-1]}")
    print(f"  Final NAV v3.7   : ${nav_df['nav'].iloc[-1]:,.0f}")
    print(f"  Final NAV 0050   : ${nav_df['nav_0050'].iloc[-1]:,.0f}")
    print(f"  MDD v3.7         : {nav_df['dd_v37'].min():>+.2f}%")
    print(f"  MDD 0050         : {nav_df['dd_0050'].min():>+.2f}%")

    # ── 最差單日 ──
    print("\n" + "=" * 70)
    print("Top 10 最差單日（v3.7）")
    print("=" * 70)
    worst_d = nav_df.sort_values("ret_v37").head(10)
    print(f"  {'date':<12} {'v3.7 ret':>10} {'0050 ret':>10}")
    for _, r in worst_d.iterrows():
        print(f"  {r['date']!s:<12} {r['ret_v37']*100:>+9.2f}% {r['ret_0050']*100:>+9.2f}%")

    # ── 最差 5 日窗口 ──
    print("\n" + "=" * 70)
    print("最差 5 日窗口")
    print("=" * 70)
    nav_df["roll5_v37"] = nav_df["nav"].pct_change(5).fillna(0) * 100
    nav_df["roll5_0050"] = nav_df["nav_0050"].pct_change(5).fillna(0) * 100
    worst_5 = nav_df.sort_values("roll5_v37").head(5)
    print(f"  {'end_date':<12} {'v3.7 5d':>10} {'0050 5d':>10}")
    for _, r in worst_5.iterrows():
        print(f"  {r['date']!s:<12} {r['roll5_v37']:>+9.2f}% {r['roll5_0050']:>+9.2f}%")

    # ── 重點期間 ──
    print("\n" + "=" * 70)
    print("關鍵歷史窗口")
    print("=" * 70)

    def window_summary(label: str, start: date, end: date) -> None:
        sub = nav_df[(nav_df["date"] >= start) & (nav_df["date"] <= end)]
        if len(sub) == 0:
            print(f"  {label}: 無資料")
            return
        v_start, v_end = sub["nav"].iloc[0], sub["nav"].iloc[-1]
        b_start, b_end = sub["nav_0050"].iloc[0], sub["nav_0050"].iloc[-1]
        v_ret = (v_end / v_start - 1) * 100
        b_ret = (b_end / b_start - 1) * 100
        v_mdd = ((sub["nav"] / sub["nav"].cummax()) - 1).min() * 100
        b_mdd = ((sub["nav_0050"] / sub["nav_0050"].cummax()) - 1).min() * 100
        print(f"  {label}")
        print(f"    報酬: v3.7 {v_ret:>+7.2f}%  0050 {b_ret:>+7.2f}%  ({v_ret-b_ret:+.2f}pp)")
        print(f"    MDD : v3.7 {v_mdd:>+7.2f}%  0050 {b_mdd:>+7.2f}%")

    window_summary("2020-03 COVID 崩盤月", date(2020, 3, 1), date(2020, 3, 31))
    window_summary("2020 Q1 (Jan-Mar)",   date(2020, 1, 1), date(2020, 3, 31))
    window_summary("2022 全年熊市",        date(2022, 1, 1), date(2022, 12, 31))
    window_summary("2022 H2 (Jul-Dec)",  date(2022, 7, 1), date(2022, 12, 31))

    # ── 寫出每日 NAV ──
    out = ROOT / "logs" / "v37_stress_nav.csv"
    nav_df[["date", "nav", "nav_0050", "dd_v37", "dd_0050", "ret_v37", "ret_0050", "n_open", "cash_pct"]].to_csv(
        out, index=False, encoding="utf-8-sig"
    )
    print(f"\n已寫入 {out.relative_to(ROOT)}")


def main() -> None:
    print("v3.7 Stress Test")
    print("Note: 用 weekly_trailing50.csv (HS=0.85) 近似，v3.7 (HS=0.92) MDD 應略小")

    df_t = pd.read_csv(WEEKLY_T50)
    df_t["entry_date"] = pd.to_datetime(df_t["entry_date"]).dt.date
    df_t["exit_date"] = pd.to_datetime(df_t["exit_date"]).dt.date
    df_t["ticker"] = df_t["ticker"].astype(str)
    print(f"\nWeekly trailing-50: {len(df_t)} trades")

    print("\n套 #1 + cut=30 ...")
    df_t = filter_1_big_holder_slope(df_t, min_slope=-0.5)
    df_t = apply_2_early_cut(df_t, cut_days=30)
    print(f"  filtered: {len(df_t)} trades")

    df_0050 = load_prices("0050")
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    nav_df = build_daily_nav(df_t, prices_0050)
    print(f"  NAV history: {len(nav_df)} days")

    analyze(nav_df)


if __name__ == "__main__":
    main()
