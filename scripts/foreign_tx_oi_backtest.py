"""
外資台指期淨多空 → TAIEX forward return Backtest

Hypothesis：外資 TX 淨未平倉的極端值（z > +1.5 / z < -1.5）能預測 TAIEX 後續走勢

訊號：
  net_oi = long_oi_balance - short_oi_balance（外資 TX）
  z = (net_oi - rolling_60d_mean) / rolling_60d_std
  Long signal:  z > +1.5（外資轉多）
  Short signal: z < -1.5（外資轉空）

評估：訊號日後 5 / 10 / 20 / 60 日 TAIEX return vs baseline (全期 mean)

額外測試：
  - 不同 z 門檻（1.0 / 1.5 / 2.0 / 2.5）
  - 訊號 robustness over time (3 windows OOS)
  - 對應 strategy: long/short bias / DCA gate / crash hedge

資料：
  - TX 外資未平倉：data/cache/finmind/extras/futures_institutional.parquet
  - TAIEX：yfinance ^TWII
"""
from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "scripts" / "output"
OUT_DIR.mkdir(exist_ok=True, parents=True)

ROLLING_WINDOW = 60  # z-score lookback
HOLD_PERIODS = [5, 10, 20, 60]
Z_THRESHOLDS = [1.0, 1.5, 2.0, 2.5]


def load_foreign_tx_oi() -> pd.DataFrame:
    """載入外資 TX net OI"""
    path = ROOT / "data" / "cache" / "finmind" / "extras" / "futures_institutional.parquet"
    df = pd.read_parquet(path)
    df = df[(df["futures_id"] == "TX") & (df["institutional_investors"] == "外資")].copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["net_oi"] = (
        df["long_open_interest_balance_volume"] -
        df["short_open_interest_balance_volume"]
    )
    df["net_oi_ma"] = df["net_oi"].rolling(ROLLING_WINDOW).mean()
    df["net_oi_std"] = df["net_oi"].rolling(ROLLING_WINDOW).std()
    df["z"] = (df["net_oi"] - df["net_oi_ma"]) / df["net_oi_std"]
    return df[["date", "net_oi", "z"]].dropna()


def load_taiex_with_forward(start_date: pd.Timestamp) -> pd.DataFrame:
    """載入 TAIEX + 算 forward returns"""
    import yfinance as yf
    taiex = yf.Ticker("^TWII").history(period="3000d", auto_adjust=False)
    if taiex.empty:
        raise RuntimeError("yfinance 抓不到 ^TWII")
    df = pd.DataFrame({
        "date": pd.to_datetime(taiex.index).tz_localize(None),
        "close": taiex["Close"].values,
    })
    df = df[df["date"] >= start_date].sort_values("date").reset_index(drop=True)

    # Forward returns
    for hold in HOLD_PERIODS:
        df[f"fwd_{hold}d"] = (df["close"].shift(-hold) / df["close"] - 1) * 100
    return df


def event_study(merged: pd.DataFrame, z_thresh: float, hold: int) -> dict:
    """對 z > +thresh 和 z < -thresh 做事件研究"""
    long_signals = merged[merged["z"] > z_thresh]
    short_signals = merged[merged["z"] < -z_thresh]
    fwd_col = f"fwd_{hold}d"

    baseline_mean = merged[fwd_col].mean()
    baseline_std = merged[fwd_col].std()

    long_mean = long_signals[fwd_col].mean() if len(long_signals) > 0 else np.nan
    long_n = len(long_signals.dropna(subset=[fwd_col]))
    long_win = (long_signals[fwd_col] > 0).mean() * 100 if long_n > 0 else np.nan

    short_mean = short_signals[fwd_col].mean() if len(short_signals) > 0 else np.nan
    short_n = len(short_signals.dropna(subset=[fwd_col]))
    short_win = (short_signals[fwd_col] < 0).mean() * 100 if short_n > 0 else np.nan

    # alpha = signal mean - baseline mean
    long_alpha = long_mean - baseline_mean if not np.isnan(long_mean) else np.nan
    short_alpha = -short_mean + baseline_mean if not np.isnan(short_mean) else np.nan  # 取空頭方向

    # T-stat (rough)
    long_t = long_alpha / (baseline_std / np.sqrt(long_n)) if long_n > 30 else np.nan
    short_t = short_alpha / (baseline_std / np.sqrt(short_n)) if short_n > 30 else np.nan

    return {
        "z_thresh": z_thresh, "hold": hold,
        "baseline_mean": round(baseline_mean, 3),
        "long_n": long_n, "long_mean": round(long_mean, 3),
        "long_win_pct": round(long_win, 1),
        "long_alpha": round(long_alpha, 3),
        "long_t": round(long_t, 2) if not np.isnan(long_t) else None,
        "short_n": short_n, "short_mean": round(short_mean, 3),
        "short_win_pct": round(short_win, 1),
        "short_alpha": round(short_alpha, 3),
        "short_t": round(short_t, 2) if not np.isnan(short_t) else None,
    }


def main():
    print("=" * 80)
    print("  外資台指期淨多空 → TAIEX forward return Backtest")
    print("=" * 80)

    # Load data
    print("\n  載入外資 TX net OI...")
    foi = load_foreign_tx_oi()
    print(f"  Range: {foi['date'].min().date()} ~ {foi['date'].max().date()}, n={len(foi)}")
    print(f"  z stats: mean={foi['z'].mean():.3f}, std={foi['z'].std():.3f}, "
          f"min={foi['z'].min():.2f}, max={foi['z'].max():.2f}")

    print("\n  載入 TAIEX...")
    taiex = load_taiex_with_forward(foi["date"].min())
    print(f"  TAIEX bars: {len(taiex)}")

    # Merge
    merged = foi.merge(taiex, on="date", how="inner")
    print(f"\n  Merged: {len(merged)} rows after inner join")

    # ── Event Study Grid ──
    print("\n" + "=" * 80)
    print("  📊 Event Study: z 門檻 × hold period")
    print("=" * 80)
    rows = []
    for z in Z_THRESHOLDS:
        for h in HOLD_PERIODS:
            r = event_study(merged, z, h)
            rows.append(r)

    grid = pd.DataFrame(rows)
    print("\n  ▶ Long signals (z > +threshold):")
    print(grid.pivot_table(index="z_thresh", columns="hold",
                           values=["long_n", "long_alpha", "long_win_pct"],
                           aggfunc="first").to_string())
    print("\n  ▶ Short signals (z < -threshold):")
    print(grid.pivot_table(index="z_thresh", columns="hold",
                           values=["short_n", "short_alpha", "short_win_pct"],
                           aggfunc="first").to_string())

    # ── Best signal evaluation ──
    print("\n" + "=" * 80)
    print("  🏆 Top alpha signals")
    print("=" * 80)
    grid["abs_long_alpha"] = grid["long_alpha"].abs()
    grid["abs_short_alpha"] = grid["short_alpha"].abs()
    print("\n  Top 5 long signals (by |alpha|):")
    top_long = grid.sort_values("abs_long_alpha", ascending=False).head(5)
    print(top_long[["z_thresh", "hold", "long_n", "long_mean", "long_alpha",
                    "long_win_pct", "long_t"]].to_string(index=False))
    print("\n  Top 5 short signals (by |alpha|):")
    top_short = grid.sort_values("abs_short_alpha", ascending=False).head(5)
    print(top_short[["z_thresh", "hold", "short_n", "short_mean", "short_alpha",
                     "short_win_pct", "short_t"]].to_string(index=False))

    # ── Out-of-sample robustness ──
    print("\n" + "=" * 80)
    print("  ⏱️ OOS Robustness：split into 3 windows")
    print("=" * 80)
    n_total = len(merged)
    splits = [
        ("2018-2020", merged.iloc[:n_total // 3]),
        ("2021-2023", merged.iloc[n_total // 3 : 2 * n_total // 3]),
        ("2024-2026", merged.iloc[2 * n_total // 3:]),
    ]
    for label, sub in splits:
        if len(sub) < 100:
            continue
        print(f"\n  --- {label} (n={len(sub)}) ---")
        for h in [20]:  # 只看 20d
            for z in [1.5]:
                long_s = sub[sub["z"] > z]
                short_s = sub[sub["z"] < -z]
                fwd = f"fwd_{h}d"
                base = sub[fwd].mean()
                if len(long_s) > 0:
                    la = long_s[fwd].mean() - base
                    lw = (long_s[fwd] > 0).mean() * 100
                    print(f"    z>{z:.1f}, hold={h}d: n={len(long_s)}, "
                          f"long_alpha={la:+.3f}%, win={lw:.0f}%")
                if len(short_s) > 0:
                    sa = -short_s[fwd].mean() + base
                    sw = (short_s[fwd] < 0).mean() * 100
                    print(f"    z<-{z:.1f}, hold={h}d: n={len(short_s)}, "
                          f"short_alpha={sa:+.3f}%, win={sw:.0f}%")

    # 寫入 csv
    today = datetime.now().strftime("%Y%m%d")
    out_csv = OUT_DIR / f"foreign_tx_oi_backtest_{today}.csv"
    grid.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ Grid → {out_csv}")

    # ── Verdict ──
    print("\n" + "=" * 80)
    print("  🎯 VERDICT")
    print("=" * 80)
    best_long = grid.sort_values("abs_long_alpha", ascending=False).iloc[0]
    best_short = grid.sort_values("abs_short_alpha", ascending=False).iloc[0]
    print(f"\n  Best long: z>{best_long['z_thresh']}, hold={best_long['hold']}d "
          f"→ alpha {best_long['long_alpha']:+.2f}% (n={best_long['long_n']}, "
          f"win={best_long['long_win_pct']}%, t={best_long['long_t']})")
    print(f"  Best short: z<-{best_short['z_thresh']}, hold={best_short['hold']}d "
          f"→ alpha {best_short['short_alpha']:+.2f}% (n={best_short['short_n']}, "
          f"win={best_short['short_win_pct']}%, t={best_short['short_t']})")

    if abs(best_long["long_alpha"]) > 1.0 and (best_long["long_t"] or 0) > 1.5:
        print("\n  ✅ Long signal 有顯著 alpha，可整合到 INVEST")
    else:
        print("\n  ⚠️ Long signal alpha 不顯著或統計不顯著")

    if abs(best_short["short_alpha"]) > 1.0 and (best_short["short_t"] or 0) > 1.5:
        print("  ✅ Short signal 有顯著 alpha，可作 crash hedge trigger")
    else:
        print("  ⚠️ Short signal alpha 不顯著")


if __name__ == "__main__":
    main()
