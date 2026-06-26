"""
假跌破反轉做多 backtest。

邏輯：
  1. 監控分鐘 K 跌破前 N 分鐘低點（支撐）
  2. 跌破時量爆（單分鐘量 > 過去 N 分鐘均量 × M）
  3. 後續 1-3 分鐘 V 反轉收回支撐線上方 → 大戶洗盤完
  4. 在反轉確認的下一根進場做多
  5. 出場：13:20 強制 OR trailing -1%

收割「乖乖設止損」被洗掉的散戶。

Sweep:
  N (前 N 分支撐): 30, 60, 120
  M (量爆倍數):    2x, 3x, 5x
  Recovery window: 1, 2, 3 分鐘內收回
  Exit:            1320, trail -1.0%

成本：當沖 0.49% per round-trip
驗收：win rate ≥ 55% AND mean net ≥ +0.3%
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
COST = CostConfig(tax_rate_discount=0.5).total_cost_ratio() * 100


def load_minute(ticker: str) -> pd.DataFrame:
    files = sorted(CACHE_MIN.glob(f"{ticker}_*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["minute_str"] = df["dt"].dt.strftime("%H:%M:%S")
    return df.sort_values("dt").reset_index(drop=True)


def detect_fake_breakdown(
    day_df: pd.DataFrame,
    support_window_min: int = 60,
    vol_burst_mult: float = 3.0,
    recovery_window_min: int = 2,
) -> list[dict]:
    """
    找該交易日所有「假跌破反轉」訊號。
    回傳每個訊號的 {entry_time, entry_price, breakdown_time, support_level}。
    """
    bars = day_df.reset_index(drop=True)
    if len(bars) < support_window_min + recovery_window_min + 5:
        return []

    signals = []
    last_signal_idx = -100

    # 開盤 15 分內不抓（雜訊太大）
    for i in range(support_window_min + 15, len(bars) - recovery_window_min - 1):
        if i < last_signal_idx + 30:    # cooldown：30 分內不重複
            continue
        # 13:00 後不抓（接近收盤）
        if bars.iloc[i]["minute_str"] > "13:00:00":
            break
        # 09:00 前不抓
        if bars.iloc[i]["minute_str"] < "09:00:00":
            continue

        # 過去 N 分支撐 = 過去 N 分 low 的最低值
        window = bars.iloc[i - support_window_min:i]
        support = float(window["low"].min())
        avg_vol = float(window["volume"].mean())
        if avg_vol <= 0 or support <= 0:
            continue

        # 當前 bar 跌破支撐 + 量爆
        cur = bars.iloc[i]
        cur_low = float(cur["low"])
        cur_close = float(cur["close"])
        cur_vol = float(cur["volume"])

        if cur_low > support:
            continue
        if cur_vol < avg_vol * vol_burst_mult:
            continue

        # 後 N 分鐘內收回支撐線上方？
        recovered = False
        recovery_idx = -1
        for j in range(1, recovery_window_min + 1):
            if i + j >= len(bars):
                break
            future_close = float(bars.iloc[i + j]["close"])
            if future_close > support:
                recovered = True
                recovery_idx = i + j
                break
        if not recovered:
            continue

        # 反轉確認的下一根進場
        entry_idx = recovery_idx + 1
        if entry_idx >= len(bars):
            continue
        entry_bar = bars.iloc[entry_idx]
        if entry_bar["minute_str"] > "13:20:00":
            continue

        signals.append({
            "breakdown_time": cur["minute_str"],
            "support_level": support,
            "entry_time": entry_bar["minute_str"],
            "entry_price": float(entry_bar["close"]),
            "entry_idx": entry_idx,
        })
        last_signal_idx = i

    return signals


def simulate_exit(
    day_df: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    exit_strategy: str = "1320",
) -> tuple[float, str, str]:
    bars = day_df.iloc[entry_idx:].reset_index(drop=True)
    if exit_strategy == "1320":
        last = bars[bars["minute_str"] <= "13:20:00"].tail(1)
        if last.empty:
            last = bars.tail(1)
        exit_price = float(last.iloc[0]["close"])
        return (exit_price / entry_price - 1) * 100, last.iloc[0]["minute_str"], "force_1320"
    elif exit_strategy.startswith("trail_"):
        trail_pct = float(exit_strategy.replace("trail_", ""))
        peak = entry_price
        for _, bar in bars.iterrows():
            c = float(bar["close"])
            peak = max(peak, c)
            from_peak = (c / peak - 1) * 100
            if from_peak <= trail_pct:
                return (c / entry_price - 1) * 100, bar["minute_str"], "trail"
            if bar["minute_str"] >= "13:20:00":
                return (c / entry_price - 1) * 100, bar["minute_str"], "force_1320"
        last = bars.tail(1)
        return (float(last.iloc[0]["close"]) / entry_price - 1) * 100, last.iloc[0]["minute_str"], "end_of_data"
    return 0.0, "n/a", "no_strategy"


def run(
    ticker: str,
    full_df: pd.DataFrame,
    support_window: int,
    vol_burst: float,
    recovery_window: int,
    exit_strategy: str,
) -> pd.DataFrame:
    days = sorted(full_df["date"].unique())
    results = []
    for d in days:
        day_df = full_df[full_df["date"] == d]
        if len(day_df) < 100:
            continue
        signals = detect_fake_breakdown(day_df, support_window, vol_burst, recovery_window)
        for sig in signals:
            ret, exit_t, reason = simulate_exit(
                day_df, sig["entry_idx"], sig["entry_price"], exit_strategy
            )
            results.append({
                "ticker": ticker, "date": d,
                "support": sig["support_level"],
                "entry_time": sig["entry_time"],
                "entry_price": sig["entry_price"],
                "exit_time": exit_t, "reason": reason,
                "gross": ret, "net": ret - COST,
            })
    return pd.DataFrame(results)


def main() -> None:
    print(f"摩擦成本: {COST:.3f}%/round-trip\n")

    # 用 25 檔 cached minute K
    minute_files = sorted(CACHE_MIN.glob("*.parquet"))
    tickers = sorted({f.stem.split("_")[0] for f in minute_files})
    print(f"Universe: {len(tickers)} tickers from minute cache")
    for tk in tickers:
        print(f"  - {tk}")

    print("\n載入 minute K...")
    data: dict[str, pd.DataFrame] = {}
    for tk in tickers:
        data[tk] = load_minute(tk)
        if not data[tk].empty:
            print(f"  {tk}: {len(data[tk]):,} rows")

    # Sweep
    sweep = [
        # (support_window, vol_burst_mult, recovery_window, exit)
        (30,  2.0, 2, "1320"),
        (30,  3.0, 2, "1320"),
        (60,  2.0, 2, "1320"),
        (60,  3.0, 2, "1320"),
        (60,  3.0, 3, "1320"),
        (60,  5.0, 2, "1320"),
        (120, 3.0, 2, "1320"),
        (60,  3.0, 2, "trail_-1.0"),
        (60,  3.0, 2, "trail_-2.0"),
    ]

    print("\n" + "=" * 90)
    print(f"假跌破反轉 sweep ({len(sweep)} configs)")
    print("=" * 90)
    print(f"  {'config':<48} {'n':>4} {'win%':>6} {'mean':>7} {'median':>7}")

    all_summary = []
    for support, vb, rw, exit_s in sweep:
        all_trades = []
        for tk, full_df in data.items():
            if full_df.empty:
                continue
            trades = run(tk, full_df, support, vb, rw, exit_s)
            all_trades.append(trades)
        df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
        if df.empty:
            continue
        win = (df["net"] > 0).mean() * 100
        mean = df["net"].mean()
        median = df["net"].median()
        flag = " ⭐" if win >= 55 and mean >= 0.3 else ""
        label = f"sup={support}min vol={vb}x rec={rw}min exit={exit_s}"
        print(f"  {label:<48} {len(df):>4} {win:>5.1f}% {mean:>+6.2f}% {median:>+6.2f}%{flag}")
        all_summary.append({
            "config": label, "n": len(df), "win_pct": win,
            "mean_net": mean, "median_net": median,
        })

    summary_df = pd.DataFrame(all_summary)
    out = ROOT / "logs" / "fake_breakdown_summary.csv"
    summary_df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")

    print("\n" + "=" * 90)
    pass_configs = summary_df[(summary_df["win_pct"] >= 55) & (summary_df["mean_net"] >= 0.3)]
    if len(pass_configs) > 0:
        print(f"✅ {len(pass_configs)} 個配置過 gate")
        print(pass_configs.to_string(index=False))
    else:
        print("❌ 無配置過 gate")
        if not summary_df.empty:
            print("\n最佳 5 個 (按 mean_net):")
            print(summary_df.nlargest(5, "mean_net").to_string(index=False))


if __name__ == "__main__":
    main()
