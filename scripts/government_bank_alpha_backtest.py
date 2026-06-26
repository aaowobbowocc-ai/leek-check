"""
政府基金（八大行庫）→ TAIEX forward return Backtest

Hypothesis：
  「八大行庫」（兆豐/合庫/第一/臺銀/華南/土銀/台企銀/彰銀）是政府透過行庫護盤的工具，
  累計買超強勢時通常代表市場過度恐慌，常是底部訊號。

訊號設計：
  net_amount = sum(buy_amount - sell_amount) 全市場全行庫 daily
  z = (net_amount - rolling_60d_mean) / rolling_60d_std
  Long signal:  z > +1.5（行庫大買，預期反彈）
  Short signal: z < -1.5（行庫大賣，少見，可能撤資）

評估：訊號日後 5/10/20/60 日 TAIEX forward return vs baseline

OOS：split into 2 windows（資料只 4.8 年）
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

ROLLING_WINDOW = 60
HOLD_PERIODS = [5, 10, 20, 60]
Z_THRESHOLDS = [1.0, 1.5, 2.0, 2.5]


def load_gov_bank_daily() -> pd.DataFrame:
    """聚合八大行庫 daily net_amount"""
    path = ROOT / "data" / "cache" / "finmind" / "extras" / "government_bank_buysell.parquet"
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    df["net_amount"] = df["buy_amount"] - df["sell_amount"]
    daily = df.groupby("date")["net_amount"].sum().reset_index()
    daily = daily.sort_values("date").reset_index(drop=True)
    daily["net_amount_b"] = daily["net_amount"] / 1e8  # 億
    daily["ma"] = daily["net_amount"].rolling(ROLLING_WINDOW).mean()
    daily["std"] = daily["net_amount"].rolling(ROLLING_WINDOW).std()
    daily["z"] = (daily["net_amount"] - daily["ma"]) / daily["std"]
    return daily.dropna(subset=["z"])


def load_taiex_with_forward(start_date: pd.Timestamp) -> pd.DataFrame:
    import yfinance as yf
    taiex = yf.Ticker("^TWII").history(period="3000d", auto_adjust=False)
    df = pd.DataFrame({
        "date": pd.to_datetime(taiex.index).tz_localize(None),
        "close": taiex["Close"].values,
    })
    df = df[df["date"] >= start_date].sort_values("date").reset_index(drop=True)
    for hold in HOLD_PERIODS:
        df[f"fwd_{hold}d"] = (df["close"].shift(-hold) / df["close"] - 1) * 100
    return df


def event_study(merged: pd.DataFrame, z_thresh: float, hold: int) -> dict:
    long_signals = merged[merged["z"] > z_thresh]
    short_signals = merged[merged["z"] < -z_thresh]
    fwd = f"fwd_{hold}d"
    base_mean = merged[fwd].mean()
    base_std = merged[fwd].std()

    long_n = len(long_signals.dropna(subset=[fwd]))
    long_mean = long_signals[fwd].mean() if long_n > 0 else np.nan
    long_win = (long_signals[fwd] > 0).mean() * 100 if long_n > 0 else np.nan
    long_alpha = long_mean - base_mean if not np.isnan(long_mean) else np.nan
    long_t = long_alpha / (base_std / np.sqrt(long_n)) if long_n > 30 else np.nan

    short_n = len(short_signals.dropna(subset=[fwd]))
    short_mean = short_signals[fwd].mean() if short_n > 0 else np.nan
    short_win = (short_signals[fwd] < 0).mean() * 100 if short_n > 0 else np.nan
    short_alpha = -short_mean + base_mean if not np.isnan(short_mean) else np.nan
    short_t = short_alpha / (base_std / np.sqrt(short_n)) if short_n > 30 else np.nan

    return {
        "z_thresh": z_thresh, "hold": hold,
        "base_mean": round(base_mean, 3),
        "long_n": long_n, "long_mean": round(long_mean, 3),
        "long_win_pct": round(long_win, 1) if not np.isnan(long_win) else None,
        "long_alpha": round(long_alpha, 3) if not np.isnan(long_alpha) else None,
        "long_t": round(long_t, 2) if not np.isnan(long_t) else None,
        "short_n": short_n, "short_mean": round(short_mean, 3) if not np.isnan(short_mean) else None,
        "short_win_pct": round(short_win, 1) if not np.isnan(short_win) else None,
        "short_alpha": round(short_alpha, 3) if not np.isnan(short_alpha) else None,
        "short_t": round(short_t, 2) if not np.isnan(short_t) else None,
    }


def main():
    print("=" * 80)
    print("  政府基金（八大行庫）→ TAIEX forward return Backtest")
    print("=" * 80)

    print("\n  載入八大行庫 daily aggregate...")
    daily = load_gov_bank_daily()
    print(f"  Range: {daily['date'].min().date()} ~ {daily['date'].max().date()}, n={len(daily)}")
    print(f"  net_amount stats (億): mean={daily['net_amount_b'].mean():.1f}, "
          f"std={daily['net_amount_b'].std():.1f}, "
          f"min={daily['net_amount_b'].min():.1f}, max={daily['net_amount_b'].max():.1f}")
    print(f"  z stats: mean={daily['z'].mean():.3f}, std={daily['z'].std():.3f}")

    print("\n  載入 TAIEX...")
    taiex = load_taiex_with_forward(daily["date"].min())
    merged = daily.merge(taiex, on="date", how="inner")
    print(f"  Merged: {len(merged)} rows")

    # ── Event Study ──
    print("\n" + "=" * 80)
    print("  📊 Event Study: z 門檻 × hold period")
    print("=" * 80)
    rows = [event_study(merged, z, h) for z in Z_THRESHOLDS for h in HOLD_PERIODS]
    grid = pd.DataFrame(rows)
    print("\n  ▶ Long signals (z > +threshold) — 行庫大買後 TAIEX 表現:")
    print(grid.pivot_table(index="z_thresh", columns="hold",
                           values=["long_n", "long_alpha", "long_t"],
                           aggfunc="first").to_string())
    print("\n  ▶ Short signals (z < -threshold) — 行庫大賣後 TAIEX 表現:")
    print(grid.pivot_table(index="z_thresh", columns="hold",
                           values=["short_n", "short_alpha", "short_t"],
                           aggfunc="first").to_string())

    # ── Top alpha ──
    print("\n" + "=" * 80)
    print("  🏆 Top alpha signals")
    print("=" * 80)
    g = grid.copy()
    g["abs_long"] = g["long_alpha"].abs()
    g["abs_short"] = g["short_alpha"].abs()

    print("\n  Top 5 long signals:")
    print(g.sort_values("abs_long", ascending=False).head(5)[
        ["z_thresh", "hold", "long_n", "long_mean", "long_alpha", "long_win_pct", "long_t"]
    ].to_string(index=False))

    print("\n  Top 5 short signals:")
    print(g.sort_values("abs_short", ascending=False).head(5)[
        ["z_thresh", "hold", "short_n", "short_mean", "short_alpha", "short_win_pct", "short_t"]
    ].to_string(index=False))

    # ── OOS：split 2 windows ──
    print("\n" + "=" * 80)
    print("  ⏱️ OOS Robustness：split into 2 windows")
    print("=" * 80)
    n = len(merged)
    splits = [
        ("2021H2-2023", merged.iloc[:n // 2]),
        ("2024-2026", merged.iloc[n // 2:]),
    ]
    for label, sub in splits:
        if len(sub) < 100:
            continue
        print(f"\n  --- {label} (n={len(sub)}) ---")
        for h in [10, 20]:
            for z in [1.5, 2.0]:
                long_s = sub[sub["z"] > z]
                short_s = sub[sub["z"] < -z]
                fwd = f"fwd_{h}d"
                base = sub[fwd].mean()
                if len(long_s) > 5:
                    la = long_s[fwd].mean() - base
                    lw = (long_s[fwd] > 0).mean() * 100
                    print(f"    z>{z:.1f}, hold={h}d: n={len(long_s)}, "
                          f"long_alpha={la:+.3f}%, win={lw:.0f}%")
                if len(short_s) > 5:
                    sa = -short_s[fwd].mean() + base
                    sw = (short_s[fwd] < 0).mean() * 100
                    print(f"    z<-{z:.1f}, hold={h}d: n={len(short_s)}, "
                          f"short_alpha={sa:+.3f}%, win={sw:.0f}%")

    today = datetime.now().strftime("%Y%m%d")
    out_csv = OUT_DIR / f"government_bank_alpha_{today}.csv"
    grid.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ Grid → {out_csv}")

    # ── Verdict ──
    print("\n" + "=" * 80)
    print("  🎯 VERDICT")
    print("=" * 80)
    best_long = g.sort_values("abs_long", ascending=False).iloc[0]
    best_short = g.sort_values("abs_short", ascending=False).iloc[0]
    print(f"\n  Best long: z>{best_long['z_thresh']}, hold={best_long['hold']}d "
          f"→ alpha {best_long['long_alpha']:+.2f}% (n={best_long['long_n']}, "
          f"win={best_long['long_win_pct']}%, t={best_long['long_t']})")
    print(f"  Best short: z<-{best_short['z_thresh']}, hold={best_short['hold']}d "
          f"→ alpha {best_short['short_alpha']:+.2f}% (n={best_short['short_n']}, "
          f"win={best_short['short_win_pct']}%, t={best_short['short_t']})")

    if abs(best_long["long_alpha"] or 0) > 1.0 and (best_long["long_t"] or 0) > 1.5:
        print("\n  ✅ Long signal 有顯著 alpha — 八大行庫大買 = 底部訊號（可整合）")
    else:
        print("\n  ⚠️ Long signal 不顯著或統計信心不足")


if __name__ == "__main__":
    main()
