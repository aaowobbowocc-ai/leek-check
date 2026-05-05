"""
TX 基差 OOS Walk-Forward Validation

驗證 deep discount / deep premium 的 fwd 20d alpha 是否跨期穩定。

Split:
  Period A: 2018-2020 (TW 牛市初 + COVID)
  Period B: 2021-2023 (流動性 + 2022 bear)
  Period C: 2024-2026 (AI boom + 2025-04 crash + 後 V 反彈)

跨期一致 → robust deploy
跨期翻轉 → over-fit
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
SIGNAL_CSV = ROOT / "logs" / "tx_basis_signal.csv"


def main():
    print("=" * 84)
    print("  TX 基差 OOS Walk-Forward Validation")
    print("=" * 84)

    df = pd.read_csv(SIGNAL_CSV)
    df["date"] = pd.to_datetime(df["date"])

    splits = [
        ("Period A 2018-2020", "2018-01-01", "2020-12-31"),
        ("Period B 2021-2023", "2021-01-01", "2023-12-31"),
        ("Period C 2024-2026", "2024-01-01", "2026-12-31"),
    ]

    bucks = [
        ("Deep discount (z<-2)", df["basis_z_60"] < -2),
        ("Mild discount (-2~-1)", (df["basis_z_60"] >= -2) & (df["basis_z_60"] < -1)),
        ("Normal (-1~+1)", (df["basis_z_60"] >= -1) & (df["basis_z_60"] < 1)),
        ("Mild premium (+1~+2)", (df["basis_z_60"] >= 1) & (df["basis_z_60"] < 2)),
        ("Deep premium (z>+2)", df["basis_z_60"] >= 2),
    ]

    print(f"\n  {'Bucket':<24}", end="")
    for p_label, _, _ in splits:
        print(f"{p_label[:18]:>20}", end="")
    print(f"{'  Full':>14}")
    print(f"  {'-'*24}" + ("  " + "-"*18) * (len(splits) + 1))

    rows = []
    for label, mask in bucks:
        line = f"  {label:<24}"
        period_means = []
        for p_label, s, e in splits:
            sub = df[mask & (df["date"] >= pd.to_datetime(s)) & (df["date"] <= pd.to_datetime(e))]
            sub_ret = sub["fwd_20d"].dropna()
            n = len(sub_ret)
            if n < 5:
                line += f"{'(n<5)':>20}"
                period_means.append(None)
            else:
                m = sub_ret.mean()
                t, _ = stats.ttest_1samp(sub_ret, 0, alternative="two-sided")
                sig = "✅" if abs(t) > 2 else ""
                line += f"  {m:>+6.2f}%(n={n:>3}){sig:>2}"
                period_means.append(m)
        # Full
        full_sub = df[mask]["fwd_20d"].dropna()
        if len(full_sub) >= 5:
            m_full = full_sub.mean()
            t_full, _ = stats.ttest_1samp(full_sub, 0, alternative="two-sided")
            line += f"  {m_full:>+5.2f}%(n={len(full_sub):>3}){'✅' if abs(t_full) > 2 else ''}"
        else:
            line += f"{'(small)':>14}"
        print(line)
        rows.append({"bucket": label, "periods": period_means, "full_mean": full_sub.mean() if len(full_sub) >= 5 else 0})

    # Excess vs normal (within each period)
    print(f"\n  === Incremental Alpha vs Normal (-1~+1) bucket ===")
    print(f"  {'Bucket':<24}", end="")
    for p_label, _, _ in splits:
        print(f"{p_label[:18]:>20}", end="")
    print()
    print(f"  {'-'*24}" + ("  " + "-"*18) * len(splits))
    for label, mask in bucks:
        if "Normal" in label:
            continue
        line = f"  {label:<24}"
        for p_label, s, e in splits:
            sub_target = df[mask & (df["date"] >= pd.to_datetime(s)) & (df["date"] <= pd.to_datetime(e))]
            sub_normal = df[
                (df["basis_z_60"] >= -1) & (df["basis_z_60"] < 1)
                & (df["date"] >= pd.to_datetime(s)) & (df["date"] <= pd.to_datetime(e))
            ]
            target_ret = sub_target["fwd_20d"].dropna()
            normal_ret = sub_normal["fwd_20d"].dropna()
            if len(target_ret) < 5 or len(normal_ret) < 30:
                line += f"{'(n<5)':>20}"
                continue
            excess = target_ret.mean() - normal_ret.mean()
            t, p = stats.ttest_ind(target_ret, normal_ret, equal_var=False)
            sig = "✅" if abs(t) > 2 else ""
            line += f"  {excess:>+5.2f}pp(t={t:>+4.1f}){sig:>2}"
        print(line)

    # Verdict
    print(f"\n  === Verdict ===")
    deep_disc_periods = [r["periods"] for r in rows if "Deep discount" in r["bucket"]][0]
    deep_prem_periods = [r["periods"] for r in rows if "Deep premium" in r["bucket"]][0]

    valid_disc = [m for m in deep_disc_periods if m is not None]
    valid_prem = [m for m in deep_prem_periods if m is not None]

    if valid_disc and all(m > 0 for m in valid_disc):
        print(f"  ✅ Deep discount 跨期一致正報酬: {valid_disc}")
    elif valid_disc and any(m < 0 for m in valid_disc):
        print(f"  ⚠️ Deep discount 跨期不一致: {valid_disc}")
    if valid_prem and all(m > 0 for m in valid_prem):
        print(f"  ✅ Deep premium 跨期一致正報酬: {valid_prem}")
    elif valid_prem and any(m < 0 for m in valid_prem):
        print(f"  ⚠️ Deep premium 跨期不一致: {valid_prem}")


if __name__ == "__main__":
    main()
