"""
ORB Exit Signal Sweep — 重用 Step 2 的 289 個進場訊號，sweep 不同 exit 規則。

對每個 ORB 訊號（已 cache 在 logs/orb_signals.csv），重新模擬：
  A. Hard stop -1.0%/-1.5%/-2.0%
  B. Trailing intraday -0.5%/-1.0% from peak
  C. Time stop 10:30/11:00/12:00/13:00
  D. VWAP cross (跌破即出)
  E. Volume decay (近 30min 量 < 開盤 15min 30%)
  F. Composite: hard stop + trail + time

Inspect cached minute parquets to evaluate intraday exits properly.
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

from src.backtest.cost_model import CostConfig  # noqa: E402

CACHE_MIN = ROOT / "data" / "cache" / "finmind" / "minute"
ORB_CSV = ROOT / "logs" / "orb_signals.csv"
COST = CostConfig(tax_rate_discount=0.5).total_cost_ratio() * 100   # 0.49%


def load_minute_day(ticker: str, d: date) -> pd.DataFrame:
    """讀某 ticker 某天 minute K（從 monthly cache）。"""
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


def simulate_exit(
    day_df: pd.DataFrame,
    entry_price: float,
    hard_stop_pct: float | None = None,    # e.g. -1.5
    trail_pct: float | None = None,        # e.g. -1.0
    time_stop: str | None = None,          # e.g. "11:00:00"
    vwap_exit: bool = False,
    vol_decay: bool = False,
) -> tuple[float, str, str]:
    """模擬出場，回傳 (gross_return%, exit_time, reason)。"""
    bars_after_entry = day_df[day_df["minute_str"] >= "09:15:00"].reset_index(drop=True)
    if bars_after_entry.empty:
        return 0.0, "n/a", "no_data"

    peak = entry_price
    cum_volume_15min = float(
        day_df[day_df["minute_str"] <= "09:14:00"]["volume"].sum()
    )

    for _, bar in bars_after_entry.iterrows():
        c = float(bar["close"])
        ret = (c / entry_price - 1) * 100
        peak = max(peak, c)

        # Hard stop
        if hard_stop_pct is not None and ret <= hard_stop_pct:
            return ret, bar["minute_str"], "hard_stop"

        # Trailing
        if trail_pct is not None:
            from_peak = (c / peak - 1) * 100
            if from_peak <= trail_pct:
                return ret, bar["minute_str"], "trailing"

        # Time stop
        if time_stop is not None and bar["minute_str"] >= time_stop:
            return ret, bar["minute_str"], "time_stop"

        # VWAP cross (累積 amount / volume)
        if vwap_exit:
            so_far = day_df[day_df["dt"] <= bar["dt"]]
            cum_v = so_far["volume"].sum()
            cum_amt = (so_far["close"] * so_far["volume"]).sum()
            if cum_v > 0:
                vwap = cum_amt / cum_v
                if c < vwap and bar["minute_str"] > "09:30:00":
                    return ret, bar["minute_str"], "vwap_cross"

        # Volume decay
        if vol_decay and bar["minute_str"] > "09:45:00":
            recent_30 = day_df[
                (day_df["minute_str"] > "09:15:00")
                & (day_df["dt"] <= bar["dt"])
            ].tail(30)
            recent_v = recent_30["volume"].sum()
            if cum_volume_15min > 0 and recent_v < cum_volume_15min * 0.30:
                return ret, bar["minute_str"], "vol_decay"

    # 沒觸發任何 exit → 13:20 強制出
    last = day_df[day_df["minute_str"] <= "13:20:00"].tail(1)
    if last.empty:
        last = day_df.tail(1)
    final = float(last.iloc[0]["close"])
    return (final / entry_price - 1) * 100, last.iloc[0]["minute_str"], "default_1320"


def sweep(orb_signals: pd.DataFrame, configs: list[dict]) -> pd.DataFrame:
    results = []
    for cfg in configs:
        label = cfg["label"]
        rets = []
        for _, sig in orb_signals.iterrows():
            day_df = load_minute_day(sig["ticker"], pd.to_datetime(sig["date"]).date())
            if day_df.empty:
                continue
            entry = float(sig["entry_price"])
            ret, exit_t, reason = simulate_exit(
                day_df, entry,
                hard_stop_pct=cfg.get("hard_stop"),
                trail_pct=cfg.get("trail"),
                time_stop=cfg.get("time"),
                vwap_exit=cfg.get("vwap", False),
                vol_decay=cfg.get("vol_decay", False),
            )
            rets.append({
                "ticker": sig["ticker"], "date": sig["date"],
                "gross": ret, "net": ret - COST,
                "exit_t": exit_t, "reason": reason,
            })
        df_r = pd.DataFrame(rets)
        if df_r.empty:
            continue
        results.append({
            "config": label,
            "n": len(df_r),
            "win_rate": (df_r["net"] > 0).mean() * 100,
            "mean_net": df_r["net"].mean(),
            "median_net": df_r["net"].median(),
            "std_net": df_r["net"].std(),
            "max_net": df_r["net"].max(),
            "min_net": df_r["net"].min(),
        })
    return pd.DataFrame(results)


def main() -> None:
    print(f"摩擦成本: {COST:.3f}%")
    if not ORB_CSV.exists():
        print(f"❌ 找不到 {ORB_CSV}，請先跑 orb_signal_diagnostic.py")
        return
    orb = pd.read_csv(ORB_CSV)
    orb["date"] = pd.to_datetime(orb["date"]).dt.date
    print(f"ORB 訊號: {len(orb)}")

    configs = [
        {"label": "baseline (13:20 force exit)"},
        # Hard stop only
        {"label": "hard -1.0%", "hard_stop": -1.0},
        {"label": "hard -1.5%", "hard_stop": -1.5},
        {"label": "hard -2.0%", "hard_stop": -2.0},
        # Trailing only
        {"label": "trail -0.5%", "trail": -0.5},
        {"label": "trail -1.0%", "trail": -1.0},
        {"label": "trail -1.5%", "trail": -1.5},
        # Time stop
        {"label": "time 10:30", "time": "10:30:00"},
        {"label": "time 11:00", "time": "11:00:00"},
        {"label": "time 12:00", "time": "12:00:00"},
        {"label": "time 13:00", "time": "13:00:00"},
        # Volume / VWAP
        {"label": "vwap cross", "vwap": True},
        {"label": "vol decay", "vol_decay": True},
        # Composite
        {"label": "hard -1.5 + trail -1.0", "hard_stop": -1.5, "trail": -1.0},
        {"label": "hard -1.5 + time 12:00", "hard_stop": -1.5, "time": "12:00:00"},
        {"label": "hard -1.5 + trail -1.0 + time 12:30",
         "hard_stop": -1.5, "trail": -1.0, "time": "12:30:00"},
        {"label": "hard -2.0 + vwap + vol_decay",
         "hard_stop": -2.0, "vwap": True, "vol_decay": True},
    ]

    print("\n" + "=" * 80)
    print(f"Sweep {len(configs)} 個 exit 規則 over {len(orb)} ORB signals")
    print("=" * 80)

    summary = sweep(orb, configs)
    summary = summary.sort_values("mean_net", ascending=False)

    print(f"\n  {'config':<42} {'n':>4} {'win%':>6} {'mean':>7} {'median':>7} {'std':>6}")
    for _, r in summary.iterrows():
        flag = " ⭐" if r["win_rate"] >= 55 and r["mean_net"] >= 0.3 else ""
        print(
            f"  {r['config']:<42} {r['n']:>4} "
            f"{r['win_rate']:>5.1f}% {r['mean_net']:>+6.3f}% {r['median_net']:>+6.3f}% "
            f"{r['std_net']:>5.2f}{flag}"
        )

    out = ROOT / "logs" / "orb_exit_sweep.csv"
    summary.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")

    # Go/no-go
    pass_configs = summary[
        (summary["win_rate"] >= 55) & (summary["mean_net"] >= 0.3)
    ]
    print("\n" + "=" * 80)
    if len(pass_configs) > 0:
        print(f"✅ 找到 {len(pass_configs)} 個過 gate 的 exit 配置 — ORB 值得 OOS 驗證")
        print(pass_configs[["config", "n", "win_rate", "mean_net"]].to_string(index=False))
    else:
        print("❌ 無 exit 配置過 gate (win ≥ 55% AND mean ≥ +0.3%) — ORB 真的死了")
        print("   建議：放棄當沖路線，回 v3.7 / v3.8 retail cut")


if __name__ == "__main__":
    main()
