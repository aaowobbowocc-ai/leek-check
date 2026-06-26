"""
Gemini Finding #3: 參數敏感度檢測

Hypothesis: 'vr < 0.8' 是 p-hacked sharp peak？
測試: 對 vr threshold 從 0.5 ~ 1.5 步長 0.1, hold = 5/10/20/40/60d
畫 alpha vs threshold heatmap

判定:
  - 平滑曲線 (alpha 隨 vr 增加平緩變化) → robust
  - 在 vr=0.8 出現尖峰 → over-fit
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


def collect_all_events(direction="down"):
    """掃全部漲停/跌停事件 + vol_ratio + fwd returns"""
    rows = []
    files = list(TW_CACHE.glob("*.parquet"))
    for i, p in enumerate(files):
        if p.stat().st_size < 500: continue
        try: px = pd.read_parquet(p)
        except: continue
        if len(px) < 80: continue
        px["date"] = pd.to_datetime(px["date"])
        px = px.sort_values("date").reset_index(drop=True)
        px["pct"] = px["close"].pct_change() * 100
        px["vol_ma60"] = px["volume"].rolling(60).mean()
        px["vol_ratio"] = px["volume"] / px["vol_ma60"]

        if direction == "down":
            triggers = px[px["pct"] <= -9.5]
        else:
            triggers = px[px["pct"] >= 9.5]

        for tidx in triggers.index:
            if tidx + 60 >= len(px): continue
            vr = px.loc[tidx, "vol_ratio"]
            if pd.isna(vr): continue
            entry = px.loc[tidx + 1, "open"] if tidx + 1 < len(px) else None  # next-day open
            if entry is None or entry <= 0: continue
            row_data = {"vol_ratio": vr}
            for h in [5, 10, 20, 40, 60]:
                if tidx + h < len(px):
                    exit_p = px.loc[tidx + h, "close"]
                    row_data[f"fwd_{h}"] = (exit_p / entry - 1) * 100
            rows.append(row_data)
        if (i+1) % 400 == 0:
            print(f"  [{i+1}/{len(files)}] events={len(rows)}")
    return pd.DataFrame(rows)


def sweep(df, label):
    print(f"\n{'='*80}\n  {label} — vol_ratio threshold sweep\n{'='*80}")
    print(f"  Total events: {len(df)}")
    print(f"\n  vr_max | n      | fwd_5  | fwd_10 | fwd_20 | fwd_40 | fwd_60")
    print(f"  -------+--------+--------+--------+--------+--------+--------")
    thresholds = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 99]
    for thr in thresholds:
        sub = df[df["vol_ratio"] < thr]
        means = []
        for h in [5, 10, 20, 40, 60]:
            col = f"fwd_{h}"
            if col in sub.columns:
                means.append(f"{sub[col].mean():+6.2f}")
            else:
                means.append("   n/a")
        thr_str = f"{thr:.2f}" if thr < 99 else "  ∞ "
        print(f"  {thr_str:>5}  | {len(sub):>6} | {' | '.join(means)}")


def main():
    print("=" * 80)
    print("  Gemini #3: vol_ratio threshold sensitivity (sharp peak vs robust)")
    print("=" * 80)

    print("\n[1/2] Limitdown events...")
    df_down = collect_all_events("down")
    sweep(df_down, "Limitdown")

    print("\n[2/2] Limitup events...")
    df_up = collect_all_events("up")
    sweep(df_up, "Limitup")

    # Robust 判定: 鄰近 threshold (0.7, 0.8, 0.9) 應該 mean fwd_20 差距 < 1pp
    for label, df in [("Limitdown", df_down), ("Limitup", df_up)]:
        v07 = df[df["vol_ratio"] < 0.7]["fwd_20"].mean()
        v08 = df[df["vol_ratio"] < 0.8]["fwd_20"].mean()
        v09 = df[df["vol_ratio"] < 0.9]["fwd_20"].mean()
        v10 = df[df["vol_ratio"] < 1.0]["fwd_20"].mean()
        diff_around_08 = max(abs(v07 - v08), abs(v08 - v09))
        print(f"\n  {label} fwd_20 robust check:")
        print(f"    vr<0.7: {v07:+.2f}% / vr<0.8: {v08:+.2f}% / vr<0.9: {v09:+.2f}% / vr<1.0: {v10:+.2f}%")
        if diff_around_08 < 1.0:
            print(f"    ✅ 平滑 (max diff {diff_around_08:.2f}pp) — vr=0.8 不是過擬合 sharp peak")
        else:
            print(f"    ⚠️ 鄰近 thr 差 {diff_around_08:.2f}pp — 可能 over-fit")


if __name__ == "__main__":
    main()
