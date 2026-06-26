"""
ChatGPT Finding #1: Overlapping holding period → t-stat 虛增

問題：同一檔股票在 20d hold window 內可能連續觸發
→ 觀測值不獨立 → t-stat / n 被高估 → MCPT 也被污染

測試：
  版本 A (overlapping, 現狀): 所有觸發都計算
  版本 B (non-overlapping):  同 ticker 持有期間禁止再觸發（只取 first signal）

若 alpha / t-stat 明顯下降 → 現狀 n 是虛增的
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
COST = 0.78


def collect_events(direction="down", hold=20):
    overlapping, non_overlap = [], []
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

        last_trigger_idx = -hold - 1  # 追蹤上次觸發位置（non-overlap 用）

        for idx in range(len(px) - hold - 1):
            pct = px.loc[idx, "pct"]
            vr = px.loc[idx, "vol_ratio"]
            if pd.isna(pct) or pd.isna(vr): continue

            is_signal = ((pct <= -9.5) if direction == "down" else (pct >= 9.5)) and vr < 0.8
            if not is_signal: continue

            entry = px.loc[idx + 1, "open"]
            if entry <= 0: continue
            fwd = (px.loc[idx + hold, "close"] / entry - 1) * 100 - COST

            # Version A: overlapping (全部)
            overlapping.append(fwd)

            # Version B: non-overlapping (同 ticker 持倉期間只取第一個)
            if idx - last_trigger_idx > hold:
                non_overlap.append(fwd)
                last_trigger_idx = idx

        if (i + 1) % 400 == 0:
            print(f"  [{i+1}/{len(files)}] overlap={len(overlapping)} non_overlap={len(non_overlap)}")

    return np.array(overlapping), np.array(non_overlap)


def report(ov, no, label, hold):
    print(f"\n{'='*78}\n  {label} (hold={hold}d)\n{'='*78}")

    def stats_row(arr, name):
        t, p = stats.ttest_1samp(arr, 0, alternative="greater")
        print(f"  {name:<30} n={len(arr):>6,}  mean={arr.mean():+6.2f}%  "
              f"t={t:+6.2f}  p={p:.5f}  win={( arr>0).mean()*100:.1f}%")
        return t

    t_ov = stats_row(ov, "Overlapping (現狀)")
    t_no = stats_row(no, "Non-overlapping (修正)")

    n_reduction = (1 - len(no) / len(ov)) * 100
    t_reduction = (1 - abs(t_no) / abs(t_ov)) * 100
    alpha_diff = ov.mean() - no.mean()

    print(f"\n  📊 影響分析:")
    print(f"    樣本數縮減:   {len(ov):,} → {len(no):,}  (-{n_reduction:.0f}%)")
    print(f"    t-stat 縮減:  {t_ov:+.2f} → {t_no:+.2f}  (-{t_reduction:.0f}%)")
    print(f"    alpha 差異:   {alpha_diff:+.2f}pp")

    if t_reduction > 30:
        print(f"  🚨 t-stat 縮減 {t_reduction:.0f}% — overlapping 顯著高估統計顯著性")
    elif t_reduction > 15:
        print(f"  ⚠️ t-stat 縮減 {t_reduction:.0f}% — 有 modest inflation")
    else:
        print(f"  ✅ t-stat 縮減僅 {t_reduction:.0f}% — overlapping 影響小，bias 輕微")

    if abs(alpha_diff) > 1.0:
        print(f"  ⚠️ alpha 差距 {alpha_diff:+.2f}pp — non-overlapping 版本更保守")


def main():
    print("=" * 78)
    print("  ChatGPT #1: Overlapping hold period → t-stat 虛增檢驗")
    print("=" * 78)

    for direction, label, hold in [
        ("down", "量縮跌停 (5d hold)", 5),
        ("down", "量縮跌停 (20d hold)", 20),
        ("up",   "量縮漲停 (20d hold)", 20),
    ]:
        print(f"\n[{direction}] {label}...")
        ov, no = collect_events(direction, hold)
        report(ov, no, label, hold)


if __name__ == "__main__":
    main()
