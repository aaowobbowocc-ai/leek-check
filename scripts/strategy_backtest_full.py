"""
完整策略回測 — 2020-01-02 起 NT$100,000 嚴格照系統執行到 2026-05-05

策略組成:
1. 5-regime classifier (V2)
2. 8-bucket regime barbell:
   CRASH: 33% TW + 15% leverage + 18% US + 10% gold + 5% JP + 9% cash + 5% legacy
   BEAR: 33/0/18/10/5/24/5/0 (legacy 0)
   SIDEWAYS: 30/0/18/10/5/15/5/12 (12% Revenue YoY satellite)
   BULL_TREND: 33/5/18/10/5/19/5/0
   STRONG_BULL: 28/0/15/10/5/32/5/0
3. Hedge signal cash tilts (Foreign TX OI z<-2, VIX>30, VIX/VIX3M>1.05) → +5-25pp cash

執行規則 (簡化以利 backtest):
- 每月底 rebalance
- 交易成本: 0.5% one-way (買賣各 0.25%)
- 槓桿 ETF (00631L) 用於 CRASH / BULL_TREND
- Revenue YoY satellite 簡化用 0050 替代 (因實際 portfolio 太複雜)
  → 這會 understate strategy alpha (Revenue YoY 在 SIDEWAYS L4 +25.7%/yr 比 0050 強)

對照 baseline:
- 100% 0050 BTH
- 60% 0050 + 40% 現金 (保守)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
GLOBAL_CACHE = ROOT / "data" / "cache" / "yfinance" / "global"

START = "2020-01-02"
END = "2026-05-05"
INITIAL_CAPITAL = 100_000  # NT$
TRADE_COST = 0.005  # 0.5% one-way per trade

# Asset universe → ticker mapping
ASSETS = {
    "core_tw":       "0050",
    "us_00646":      "00646",
    "leverage":      "00631L",
    "gold":          "00635U",  # TW listed gold ETF
    "japan_dxj":     "DXJ",     # USD denominated, treat as TWD for simplicity
    "satellite":     "0050",    # Revenue YoY satellite — use 0050 as proxy (conservative)
    "legacy":        "0050",    # legacy individual stocks → use 0050 proxy
}

# 5-regime allocation table (normalize to exactly 100% — drift buffer 加回 cash)
ALLOCATION = {
    "CRASH":       {"core_tw": 33, "leverage": 15, "us_00646": 18, "gold": 10, "japan_dxj": 5, "satellite": 0,  "cash": 14, "legacy": 5},
    "BEAR":        {"core_tw": 33, "leverage":  0, "us_00646": 18, "gold": 10, "japan_dxj": 5, "satellite": 0,  "cash": 29, "legacy": 5},
    "SIDEWAYS":    {"core_tw": 30, "leverage":  0, "us_00646": 18, "gold": 10, "japan_dxj": 5, "satellite": 12, "cash": 20, "legacy": 5},
    "BULL_TREND":  {"core_tw": 33, "leverage":  5, "us_00646": 18, "gold": 10, "japan_dxj": 5, "satellite": 0,  "cash": 24, "legacy": 5},
    "STRONG_BULL": {"core_tw": 28, "leverage":  0, "us_00646": 15, "gold": 10, "japan_dxj": 5, "satellite": 0,  "cash": 37, "legacy": 5},
}
# 確認每個 regime 加總 = 100
for r, alloc in ALLOCATION.items():
    s = sum(alloc.values())
    assert s == 100, f"{r} sums to {s}, must be 100"


def classify_regime(dist_ma200: float, vol_30d: float, ret_60d: float) -> str:
    if pd.isna(dist_ma200) or pd.isna(vol_30d) or pd.isna(ret_60d):
        return "UNKNOWN"
    if ret_60d < -15 and vol_30d > 25:
        return "CRASH"
    if dist_ma200 < -5 and ret_60d < 0:
        return "BEAR"
    if dist_ma200 > 20:
        return "STRONG_BULL"
    if abs(dist_ma200) < 5:
        return "SIDEWAYS"
    if dist_ma200 > 0:
        return "BULL_TREND"
    return "SIDEWAYS"


def load_prices(ticker: str) -> pd.DataFrame:
    paths = [TW_CACHE / f"{ticker}.parquet", GLOBAL_CACHE / f"{ticker}.parquet"]
    for p in paths:
        if p.exists():
            df = pd.read_parquet(p)
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            df = df.sort_values("date").reset_index(drop=True)
            return df[["date", "close"]]
    return pd.DataFrame()


def build_regime_calendar() -> pd.DataFrame:
    twii = load_prices("^TWII")
    if twii.empty:
        # try GSPC fallback
        return pd.DataFrame()
    twii = twii.rename(columns={"close": "twii"})
    twii["log_ret"] = np.log(twii["twii"] / twii["twii"].shift(1))
    twii["ret_60d"] = twii["twii"].pct_change(60) * 100
    twii["ma200"] = twii["twii"].rolling(200).mean()
    twii["dist_ma200"] = (twii["twii"] / twii["ma200"] - 1) * 100
    twii["vol_30d"] = twii["log_ret"].rolling(30).std() * np.sqrt(252) * 100
    twii["regime"] = twii.apply(
        lambda r: classify_regime(r["dist_ma200"], r["vol_30d"], r["ret_60d"]),
        axis=1,
    )
    return twii[["date", "twii", "regime", "dist_ma200"]]


def get_month_ends(start: str, end: str, calendar: pd.DataFrame) -> list[pd.Timestamp]:
    mask = (calendar["date"] >= pd.to_datetime(start)) & (calendar["date"] <= pd.to_datetime(end))
    sub = calendar[mask].copy()
    sub["year_month"] = sub["date"].dt.to_period("M")
    return [g["date"].iloc[-1] for _, g in sub.groupby("year_month")]


def asof_price(prices: pd.DataFrame, target: pd.Timestamp) -> float:
    if prices.empty:
        return 0.0
    sub = prices[prices["date"] <= target]
    return float(sub["close"].iloc[-1]) if not sub.empty else 0.0


def main():
    print("=" * 80)
    print("  完整策略回測 — 2020-01-02 NT$100K → 2026-05-05")
    print("=" * 80)

    # Load all asset prices
    asset_prices: dict[str, pd.DataFrame] = {}
    for bucket, ticker in ASSETS.items():
        if ticker in asset_prices:
            continue
        df = load_prices(ticker)
        if df.empty:
            print(f"  ⚠️ 找不到 {ticker} 的價格資料")
            return
        asset_prices[ticker] = df
        print(f"  載入 {ticker}: {len(df)} rows ({df['date'].min().date()} ~ {df['date'].max().date()})")

    # Build regime calendar from TAIEX
    calendar = build_regime_calendar()
    if calendar.empty:
        print("  ❌ 無法建立 regime calendar (^TWII 缺)")
        return

    rebalance_dates = get_month_ends(START, END, calendar)
    print(f"\n  Rebalance 日期數: {len(rebalance_dates)} (每月底)")
    print(f"  期間: {rebalance_dates[0].date()} ~ {rebalance_dates[-1].date()}")

    # Initialize portfolio
    nav = float(INITIAL_CAPITAL)
    positions: dict[str, float] = {ticker: 0.0 for ticker in set(ASSETS.values())}
    cash = nav

    # Track history
    history = []
    regime_changes = []

    for i, dt in enumerate(rebalance_dates):
        # Lookup regime for this date
        cal_row = calendar[calendar["date"] == dt]
        if cal_row.empty:
            cal_row = calendar[calendar["date"] <= dt].tail(1)
        regime = cal_row["regime"].iloc[0] if not cal_row.empty else "UNKNOWN"

        if regime == "UNKNOWN":
            history.append({
                "date": dt, "regime": regime, "nav": nav,
                "twii": cal_row["twii"].iloc[0] if not cal_row.empty else 0,
            })
            continue

        target_alloc = ALLOCATION.get(regime, ALLOCATION["SIDEWAYS"])

        # Compute current value (mark to market)
        current_value = cash
        for ticker, shares in positions.items():
            price = asof_price(asset_prices[ticker], dt)
            current_value += shares * price
        nav = current_value

        # Compute target shares per asset
        target_value = {}
        for bucket, pct in target_alloc.items():
            ticker = ASSETS.get(bucket, None)
            if ticker is None or pct == 0:
                continue
            target_value[ticker] = target_value.get(ticker, 0) + pct / 100 * nav

        # Rebalance with cost
        new_positions: dict[str, float] = {ticker: 0.0 for ticker in set(ASSETS.values())}
        total_cost = 0.0
        for ticker, tgt_val in target_value.items():
            price = asof_price(asset_prices[ticker], dt)
            if price <= 0:
                continue
            current_val = positions[ticker] * price
            trade_amount = abs(tgt_val - current_val)
            total_cost += trade_amount * TRADE_COST
            new_positions[ticker] = tgt_val / price

        nav -= total_cost
        # Re-allocate after cost
        positions = {}
        for ticker in set(ASSETS.values()):
            tgt = target_value.get(ticker, 0)
            price = asof_price(asset_prices[ticker], dt)
            positions[ticker] = (tgt / price) if price > 0 else 0
        cash = target_alloc.get("cash", 0) / 100 * nav

        # Track regime change
        if i == 0 or (history and history[-1].get("regime") != regime):
            regime_changes.append((dt, regime))

        history.append({
            "date": dt, "regime": regime, "nav": nav,
            "twii": cal_row["twii"].iloc[0] if not cal_row.empty else 0,
            "cost": total_cost,
        })

    df_hist = pd.DataFrame(history)
    if df_hist.empty:
        print("  ❌ 無歷史紀錄")
        return

    final_nav = df_hist["nav"].iloc[-1]
    print(f"\n  ===== 結果 =====")
    print(f"  起始資本: NT${INITIAL_CAPITAL:,.0f}")
    print(f"  最終 NAV: NT${final_nav:,.0f}")
    print(f"  總報酬: {(final_nav/INITIAL_CAPITAL - 1) * 100:+.1f}%")
    yrs = (df_hist["date"].iloc[-1] - df_hist["date"].iloc[0]).days / 365.25
    cagr = (final_nav / INITIAL_CAPITAL) ** (1 / yrs) - 1
    print(f"  CAGR: {cagr*100:+.1f}%/yr")

    running_max = df_hist["nav"].cummax()
    dd = (df_hist["nav"] / running_max - 1).min()
    print(f"  Max DD: {dd*100:.1f}%")

    # Compare vs 0050 BTH
    print(f"\n  ===== 對照 baseline =====")
    px_0050 = load_prices("0050")
    px_0050_filt = px_0050[(px_0050["date"] >= pd.to_datetime(START)) & (px_0050["date"] <= df_hist["date"].iloc[-1])].reset_index(drop=True)
    if not px_0050_filt.empty:
        bth_start = px_0050_filt["close"].iloc[0]
        bth_end = px_0050_filt["close"].iloc[-1]
        bth_total = (bth_end / bth_start - 1) * 100
        bth_cagr = (bth_end / bth_start) ** (1 / yrs) - 1
        bth_max_dd = (px_0050_filt["close"] / px_0050_filt["close"].cummax() - 1).min()
        bth_final = INITIAL_CAPITAL * (bth_end / bth_start)
        print(f"  100% 0050 BTH:")
        print(f"    最終 NAV: NT${bth_final:,.0f}")
        print(f"    總報酬: {bth_total:+.1f}%")
        print(f"    CAGR: {bth_cagr*100:+.1f}%/yr")
        print(f"    Max DD: {bth_max_dd*100:.1f}%")
        print(f"\n  策略 vs 0050 BTH:")
        print(f"    報酬差距: {(final_nav - bth_final):+,.0f}（{(final_nav/bth_final - 1)*100:+.1f}%）")
        print(f"    CAGR 差距: {(cagr - bth_cagr)*100:+.1f}pp")
        print(f"    DD 差距: {(dd - bth_max_dd)*100:+.1f}pp")

    # 60/40 baseline
    if not px_0050_filt.empty:
        bth60_final = INITIAL_CAPITAL * 0.6 * (bth_end / bth_start) + INITIAL_CAPITAL * 0.4
        print(f"\n  60% 0050 + 40% 現金 (保守):")
        print(f"    最終 NAV: NT${bth60_final:,.0f}")
        print(f"    總報酬: {(bth60_final/INITIAL_CAPITAL - 1)*100:+.1f}%")

    # Year by year
    print(f"\n  ===== 年度表現 =====")
    df_hist["year"] = df_hist["date"].dt.year
    yearly = df_hist.groupby("year").agg(
        start_nav=("nav", "first"),
        end_nav=("nav", "last"),
    )
    yearly["return"] = (yearly["end_nav"] / yearly["start_nav"] - 1) * 100
    print(f"  {'Year':<6} {'NAV (年底)':>15} {'年報酬':>10}")
    for yr, row in yearly.iterrows():
        print(f"  {yr:<6} NT${row['end_nav']:>12,.0f}  {row['return']:>+9.1f}%")

    # Regime distribution
    print(f"\n  ===== Regime 出現天數 =====")
    regime_counts = df_hist["regime"].value_counts()
    for r, n in regime_counts.items():
        print(f"  {r:<14} {n:>3} 個月")

    # First / last few regime changes
    print(f"\n  ===== 重要 regime 切換 (前 5 + 後 5) =====")
    for dt, r in regime_changes[:5]:
        print(f"  {dt.date()} → {r}")
    if len(regime_changes) > 10:
        print("  ...")
    for dt, r in regime_changes[-5:]:
        print(f"  {dt.date()} → {r}")

    # Save NAV trajectory
    out = ROOT / "logs" / "strategy_backtest_nav.csv"
    out.parent.mkdir(exist_ok=True)
    df_hist.to_csv(out, index=False)
    print(f"\n  ✅ NAV 軌跡寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
