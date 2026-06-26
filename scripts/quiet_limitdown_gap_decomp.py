"""
Gemini Finding #6: 量縮跌停反彈 alpha 拆解

問題：+8.55% / 20d alpha 是不是大部分來自 T+1 開盤跳空？
若是 → 散戶根本搶不到，alpha 是「執行幻覺」

拆解：
  total_20d = T+1_gap + T+1_intraday + T+2_to_T+20
  T+1_gap        = (next_open / signal_close - 1) * 100   # 跳空 (隔夜變化)
  T+1_intraday   = (next_close / next_open - 1) * 100      # T+1 盤中
  T+2_to_T+20    = (T+20_close / next_close - 1) * 100     # 真正可捕捉部分

如果 gap > 50% of total → alpha 不可靠，散戶搶不到
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"


def collect_quiet_limitdown(direction="down"):
    rows = []
    files = list(TW_CACHE.glob("*.parquet"))
    for i, p in enumerate(files):
        if p.stat().st_size < 500: continue
        try:
            px = pd.read_parquet(p)
        except Exception:
            continue
        if len(px) < 80: continue
        px["date"] = pd.to_datetime(px["date"])
        px = px.sort_values("date").reset_index(drop=True)
        px["pct"] = px["close"].pct_change() * 100
        px["vol_ma60"] = px["volume"].rolling(60).mean()
        px["vol_ratio"] = px["volume"] / px["vol_ma60"]

        if direction == "down":
            triggers = px[(px["pct"] <= -9.5) & (px["vol_ratio"] < 0.8)]
        else:
            triggers = px[(px["pct"] >= 9.5) & (px["vol_ratio"] < 0.8)]

        for tidx in triggers.index:
            if tidx + 20 >= len(px): continue
            sig_close = px.loc[tidx, "close"]
            t1_open = px.loc[tidx + 1, "open"]
            t1_close = px.loc[tidx + 1, "close"]
            t20_close = px.loc[tidx + 20, "close"]
            if sig_close <= 0 or t1_open <= 0 or t1_close <= 0: continue

            gap = (t1_open / sig_close - 1) * 100
            t1_intraday = (t1_close / t1_open - 1) * 100
            t2_to_t20 = (t20_close / t1_close - 1) * 100
            total = (t20_close / sig_close - 1) * 100

            rows.append({
                "ticker": p.stem, "date": px.loc[tidx, "date"],
                "gap": gap, "t1_intraday": t1_intraday,
                "t2_to_t20": t2_to_t20, "total_20d": total,
            })
        if (i + 1) % 400 == 0:
            print(f"  [{i+1}/{len(files)}] events={len(rows)}")
    return pd.DataFrame(rows)


def report(df, label):
    print(f"\n{'='*78}\n  {label} (n={len(df)})\n{'='*78}")
    if df.empty:
        print("  No data")
        return
    parts = ["gap", "t1_intraday", "t2_to_t20", "total_20d"]
    print(f"\n  {'Component':<18} {'Mean %':>9} {'Median %':>10} {'Win >0%':>9}")
    for c in parts:
        m = df[c].mean()
        med = df[c].median()
        win = (df[c] > 0).mean() * 100
        print(f"  {c:<18} {m:+9.2f} {med:+10.2f} {win:8.1f}")

    total = df["total_20d"].mean()
    gap_share = df["gap"].mean() / total * 100 if total != 0 else 0
    intraday_share = df["t1_intraday"].mean() / total * 100 if total != 0 else 0
    holding_share = df["t2_to_t20"].mean() / total * 100 if total != 0 else 0
    print(f"\n  📊 Component share of total {total:+.2f}%:")
    print(f"    T+1 gap:        {df['gap'].mean():+6.2f}% ({gap_share:+5.1f}%)")
    print(f"    T+1 intraday:   {df['t1_intraday'].mean():+6.2f}% ({intraday_share:+5.1f}%)")
    print(f"    T+2~T+20 hold:  {df['t2_to_t20'].mean():+6.2f}% ({holding_share:+5.1f}%)")

    if abs(gap_share) > 50:
        print(f"\n  🚨 警告: gap 占 {gap_share:.0f}% — alpha 主要來自跳空，散戶搶不到 → 執行幻覺")
    elif abs(gap_share) > 30:
        print(f"\n  ⚠️ gap 占 {gap_share:.0f}% — 部分 alpha 在跳空，需嚴格控制下單時機")
    else:
        print(f"\n  ✅ gap 僅占 {gap_share:.0f}% — 大部分 alpha 在 hold 期，可捕捉")


def main():
    print("=" * 78)
    print("  量縮跌停 / 量縮漲停 — Gap 分解 (Gemini Finding #6)")
    print("=" * 78)

    print("\n[1/2] Quiet Limitdown...")
    df_down = collect_quiet_limitdown("down")
    report(df_down, "Quiet Limitdown")

    print("\n[2/2] Quiet Limitup...")
    df_up = collect_quiet_limitdown("up")
    report(df_up, "Quiet Limitup")

    # 存 CSV
    out = ROOT / "logs" / "quiet_limit_gap_decomp.csv"
    out.parent.mkdir(exist_ok=True)
    pd.concat([df_down.assign(signal="limitdown"),
               df_up.assign(signal="limitup")]).to_csv(out, index=False)
    print(f"\n  💾 Saved {out}")


if __name__ == "__main__":
    main()
