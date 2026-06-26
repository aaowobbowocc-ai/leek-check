"""
2408 南亞科 ORB Walk-Forward Validation。

問題：scalp_portfolio_review 顯示 2408 ORB 19 trades / 2 年
      win 63%、mean +1.38% (調整後)。是真 alpha 還是 sample bias？

方法：
  1. 時間切分：2024 train / 2025-2026 test
  2. Bootstrap 1000 次重抽樣計算 CI
  3. 對比 0050 同期 baseline
  4. Permutation test: shuffle 後 mean 是否仍正

驗收：
  - Test 期 mean > 0% 且 win > 50% → 有真 alpha
  - Bootstrap 95% CI 不跨 0 → 統計顯著
  - 否則承認 sample 太小不能上線
"""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 校正成本（精準版）
NEW_COST = 0.34


def main() -> None:
    df = pd.read_csv(ROOT / "logs" / "orb_signals.csv")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # 篩 2408 + 重新算 net_return（用新成本）
    df_2408 = df[df["ticker"] == 2408].copy()
    df_2408["net_return_new"] = df_2408["gross_return_pct"] - NEW_COST
    df_2408["is_winner_new"] = df_2408["net_return_new"] > 0

    print("=" * 80)
    print(f"2408 南亞科 ORB Walk-Forward (n={len(df_2408)})")
    print("=" * 80)

    print(f"\n所有 trades 時間軸：")
    for _, t in df_2408.iterrows():
        flag = "✅" if t["is_winner_new"] else "❌"
        print(f"  {t['date'].date()} entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} "
              f"net={t['net_return_new']:+.2f}% {flag}")

    # Year split
    cutoff = pd.Timestamp("2025-06-01")
    train = df_2408[df_2408["date"] < cutoff]
    test = df_2408[df_2408["date"] >= cutoff]

    print(f"\n[1/4] 時間切分（cutoff = 2025-06）")
    for label, sub in [("TRAIN (2024-2025/05)", train), ("TEST  (2025/06-2026)", test)]:
        if sub.empty:
            print(f"  {label}: empty"); continue
        win = sub["is_winner_new"].mean() * 100
        mean = sub["net_return_new"].mean()
        median = sub["net_return_new"].median()
        std = sub["net_return_new"].std()
        print(f"  {label} n={len(sub):>3} "
              f"win={win:>5.1f}% mean={mean:>+6.2f}% median={median:>+6.2f}% std={std:.2f}")

    # Bootstrap 95% CI
    print(f"\n[2/4] Bootstrap 95% CI (1000 次重抽樣)")
    rets = df_2408["net_return_new"].values
    np.random.seed(42)
    boot_means = []
    for _ in range(1000):
        sample = np.random.choice(rets, size=len(rets), replace=True)
        boot_means.append(sample.mean())
    boot_means = np.array(boot_means)
    ci_low = np.percentile(boot_means, 2.5)
    ci_high = np.percentile(boot_means, 97.5)
    print(f"  全 sample mean: {rets.mean():+.3f}%")
    print(f"  Bootstrap mean: {boot_means.mean():+.3f}%")
    print(f"  95% CI: [{ci_low:+.3f}%, {ci_high:+.3f}%]")
    print(f"  CI 跨 0?  {'❌ 不顯著（CI 含 0）' if ci_low <= 0 else '✅ 顯著（CI > 0）'}")

    # Permutation test
    print(f"\n[3/4] Permutation Test (shuffle ORB 訊號的 entry/exit 對應)")
    # 用全 ORB universe 的 returns shuffle，看 2408 配對下的隨機 mean 是否能達到觀察值
    all_rets = df["gross_return_pct"].values
    n_2408 = len(df_2408)
    actual_mean = df_2408["gross_return_pct"].mean()
    perm_means = []
    for _ in range(1000):
        sample = np.random.choice(all_rets, size=n_2408, replace=False)
        perm_means.append(sample.mean())
    perm_means = np.array(perm_means)
    p_value = (perm_means >= actual_mean).mean()
    print(f"  觀察值（gross mean）: {actual_mean:+.3f}%")
    print(f"  隨機抽樣 mean 分布: 中位 {np.median(perm_means):+.3f}%, 95th {np.percentile(perm_means, 95):+.3f}%")
    print(f"  p-value (one-sided): {p_value:.3f}  {'✅ 顯著' if p_value < 0.05 else '⚠️ 邊緣' if p_value < 0.10 else '❌ 不顯著'}")

    # 4. 對比 0050 同期
    print(f"\n[4/4] 0050 同期對比")
    cache_yf = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
    tw50 = pd.read_parquet(cache_yf / "0050.parquet")
    tw50["date"] = pd.to_datetime(tw50["date"])
    # 對每個 2408 ORB 訊號日，0050 當日 + 隔日 return 對照
    for label, sub in [("TRAIN", train), ("TEST", test)]:
        if sub.empty: continue
        zero50_rets = []
        for _, t in sub.iterrows():
            d = t["date"]
            day = tw50[tw50["date"] == d]
            next_day_idx = tw50[tw50["date"] > d].head(1)
            if not day.empty and not next_day_idx.empty:
                ret = (next_day_idx.iloc[0]["close"] / day.iloc[0]["close"] - 1) * 100
                zero50_rets.append(ret)
        if zero50_rets:
            print(f"  {label}: 2408 ORB mean {sub['net_return_new'].mean():+.2f}%, "
                  f"0050 同期 next-day mean {np.mean(zero50_rets):+.2f}% "
                  f"(diff {sub['net_return_new'].mean() - np.mean(zero50_rets):+.2f}pp)")


if __name__ == "__main__":
    main()
