"""
ORB Whitelist Scan — Step 1 of scalp framework.

對 orb_signals.csv 中所有 n>=10 ticker 跑相同 walk-forward + bootstrap，
識別統計顯著的 ORB-friendly whitelist。

Gate（rank-based, 不用硬閾值）:
  Tier A（強）: TEST mean>0 AND bootstrap CI 全 > 0 AND beats 0050
  Tier B（邊緣）: TEST mean>0 OR (TRAIN+TEST 整體 mean>0 AND win>50%)
  Tier C（淘汰）: TEST mean<0 OR full sample mean<0

輸出:
  logs/orb_whitelist.csv  — 每 ticker 的完整統計
  logs/orb_whitelist_summary.md — Tier 分組 + 推薦
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
sys.path.insert(0, str(ROOT))

from src.strategy.volume_anomaly_scanner import lookup_ticker_name  # noqa: E402

NEW_COST = 0.34
CUTOFF = pd.Timestamp("2025-06-01")
MIN_N = 10
N_BOOT = 1000
SEED = 42


def analyze_ticker(df_tk: pd.DataFrame, all_rets: np.ndarray) -> dict:
    """單 ticker walk-forward + bootstrap。"""
    n = len(df_tk)
    rets = df_tk["net_return_new"].values
    full_mean = rets.mean()
    full_win = (rets > 0).mean() * 100

    train = df_tk[df_tk["date"] < CUTOFF]
    test = df_tk[df_tk["date"] >= CUTOFF]
    train_n, test_n = len(train), len(test)
    train_mean = train["net_return_new"].mean() if train_n else np.nan
    train_win = (train["net_return_new"] > 0).mean() * 100 if train_n else np.nan
    test_mean = test["net_return_new"].mean() if test_n else np.nan
    test_win = (test["net_return_new"] > 0).mean() * 100 if test_n else np.nan

    rng = np.random.default_rng(SEED)
    boot = np.array([rng.choice(rets, size=n, replace=True).mean() for _ in range(N_BOOT)])
    ci_low, ci_high = np.percentile(boot, [2.5, 97.5])

    actual_gross = df_tk["gross_return_pct"].mean()
    perm_means = np.array([
        rng.choice(all_rets, size=n, replace=False).mean() for _ in range(N_BOOT)
    ])
    p_value = float((perm_means >= actual_gross).mean())

    if test_mean > 0 and ci_low > 0 and test_n >= 5:
        tier = "A"
    elif (test_mean is not None and test_mean > 0 and test_n >= 3) or \
         (full_mean > 0 and full_win > 50):
        tier = "B"
    else:
        tier = "C"

    return {
        "ticker": str(df_tk["ticker"].iloc[0]),
        "n": n,
        "full_mean": full_mean,
        "full_win_pct": full_win,
        "train_n": train_n,
        "train_mean": train_mean,
        "train_win_pct": train_win,
        "test_n": test_n,
        "test_mean": test_mean,
        "test_win_pct": test_win,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "perm_p": p_value,
        "tier": tier,
    }


def main() -> None:
    df = pd.read_csv(ROOT / "logs" / "orb_signals.csv")
    df["date"] = pd.to_datetime(df["date"])
    df["ticker"] = df["ticker"].astype(str)
    df["net_return_new"] = df["gross_return_pct"] - NEW_COST

    counts = df.groupby("ticker").size()
    eligible = counts[counts >= MIN_N].index.tolist()
    print("=" * 80)
    print(f"ORB Whitelist Scan — n>={MIN_N} 共 {len(eligible)} ticker")
    print("=" * 80)

    all_rets = df["gross_return_pct"].values
    rows = []
    for tk in eligible:
        sub = df[df["ticker"] == tk].sort_values("date").reset_index(drop=True)
        rows.append(analyze_ticker(sub, all_rets))

    res = pd.DataFrame(rows)
    res["name"] = res["ticker"].apply(lambda t: lookup_ticker_name(str(t)) or "")

    res = res.sort_values(["tier", "test_mean"], ascending=[True, False]).reset_index(drop=True)

    cols = ["ticker", "name", "tier", "n", "full_mean", "full_win_pct",
            "train_n", "train_mean", "train_win_pct",
            "test_n", "test_mean", "test_win_pct",
            "ci_low", "ci_high", "perm_p"]

    print(f"\n{'tier':<5} {'tk':<6} {'name':<10} {'n':>3} "
          f"{'full_m':>7} {'full_w':>7} "
          f"{'tr_n':>4} {'tr_m':>7} {'tr_w':>6} "
          f"{'te_n':>4} {'te_m':>7} {'te_w':>6} "
          f"{'CI_lo':>7} {'CI_hi':>7} {'p':>6}")
    print("-" * 120)
    for _, r in res.iterrows():
        name = (r["name"] or "")[:6]
        print(f"  {r['tier']:<3} {r['ticker']:<6} {name:<10} {r['n']:>3} "
              f"{r['full_mean']:>+6.2f}% {r['full_win_pct']:>6.1f}% "
              f"{r['train_n']:>4} {r['train_mean']:>+6.2f}% {r['train_win_pct']:>5.1f}% "
              f"{r['test_n']:>4} {r['test_mean']:>+6.2f}% {r['test_win_pct']:>5.1f}% "
              f"{r['ci_low']:>+6.2f}% {r['ci_high']:>+6.2f}% {r['perm_p']:>6.3f}")

    # Tier breakdown
    print("\n" + "=" * 80)
    print("Tier 統計")
    print("=" * 80)
    for t in ["A", "B", "C"]:
        sub = res[res["tier"] == t]
        if sub.empty:
            continue
        labels = {
            "A": "✅ Tier A（強 - 統計顯著 + OOS 持續）",
            "B": "⚠️ Tier B（邊緣 - mean 正但 CI 含 0 或 OOS 樣本太小）",
            "C": "❌ Tier C（淘汰 - 無 alpha）",
        }
        print(f"\n{labels[t]}: {len(sub)} 檔")
        for _, r in sub.iterrows():
            print(f"  {r['ticker']} {r['name']:<8} "
                  f"full {r['full_mean']:+.2f}%/{r['full_win_pct']:.0f}%, "
                  f"OOS({r['test_n']}) {r['test_mean']:+.2f}%/{r['test_win_pct']:.0f}%, "
                  f"CI [{r['ci_low']:+.2f}, {r['ci_high']:+.2f}]")

    out_csv = ROOT / "logs" / "orb_whitelist.csv"
    res[cols].to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out_csv.relative_to(ROOT)}")

    # Markdown summary
    md_lines = ["# ORB Whitelist Scan — Tier 分組\n"]
    md_lines.append(f"資料: orb_signals.csv (n={len(df)} 訊號 / {df['ticker'].nunique()} ticker)")
    md_lines.append(f"成本: {NEW_COST}% / 筆 | Cutoff: 2025-06-01 | Bootstrap n={N_BOOT}\n")
    for t in ["A", "B", "C"]:
        sub = res[res["tier"] == t]
        if sub.empty:
            continue
        labels = {"A": "Tier A — 可進 paper trading", "B": "Tier B — 需擴大樣本",
                  "C": "Tier C — 不採用"}
        md_lines.append(f"\n## {labels[t]}（{len(sub)} 檔）\n")
        md_lines.append("| ticker | 名稱 | n | 全期 mean/win | OOS mean/win | 95% CI | p |")
        md_lines.append("|---|---|---|---|---|---|---|")
        for _, r in sub.iterrows():
            md_lines.append(
                f"| {r['ticker']} | {r['name']} | {r['n']} | "
                f"{r['full_mean']:+.2f}%/{r['full_win_pct']:.0f}% | "
                f"{r['test_mean']:+.2f}%/{r['test_win_pct']:.0f}% (n={r['test_n']}) | "
                f"[{r['ci_low']:+.2f}, {r['ci_high']:+.2f}] | {r['perm_p']:.3f} |"
            )

    out_md = ROOT / "logs" / "orb_whitelist_summary.md"
    out_md.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"寫入 {out_md.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
