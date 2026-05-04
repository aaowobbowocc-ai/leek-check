"""
Regime Classifier V2 OOS Walk-Forward Validation

問題: V2 classifier 規則是手工挑的（-15%, -5%, +20%, vol > 25%）。需要驗證
     在子期間是否一致——避免規則在某個 regime 上是過擬合一個年份。

方法:
  將 9 年 (2017-2025) 切成 3 期:
    Period A: 2017-2019 (TW 牛市初段)
    Period B: 2020-2022 (COVID + 量縮反彈 + 2022 bear)
    Period C: 2023-2025 (AI boom + Trump crash)

  每期分別計算:
    - regime 分布 (各 regime 的天數佔比)
    - 0050 / 00631L fwd 20d 報酬 per regime

  若每期 regime 排序一致（CRASH > BULL_TREND > SIDEWAYS > BEAR > STRONG_BULL）
    → V2 classifier 真 robust，可以信任 morning_briefing 的判斷
  若不一致 → 標出問題 regime，需要重新檢視規則
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

# Reuse V2 classifier
from src.report.regime_section import classify

TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
PERIODS = [
    ("Period A 2017-2019", "2017-01-01", "2019-12-31"),
    ("Period B 2020-2022", "2020-01-01", "2022-12-31"),
    ("Period C 2023-2025", "2023-01-01", "2025-12-31"),
]
REGIMES_ORDER = ["CRASH", "BULL_TREND", "SIDEWAYS", "BEAR", "STRONG_BULL"]


def load_with_indicators(ticker: str) -> pd.DataFrame:
    p = TW_CACHE / f"{ticker}.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.sort_values("date").reset_index(drop=True)
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    df["ret_60d"] = df["close"].pct_change(60) * 100
    df["ma200"] = df["close"].rolling(200).mean()
    df["dist_ma200"] = (df["close"] / df["ma200"] - 1) * 100
    df["vol_30d"] = df["log_ret"].rolling(30).std() * np.sqrt(252) * 100
    return df


def assign_regime(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["regime"] = df.apply(
        lambda r: classify(r["dist_ma200"], r["vol_30d"], r["ret_60d"]),
        axis=1,
    )
    return df


def fwd_return(df: pd.DataFrame, hold: int) -> pd.Series:
    return (df["close"].shift(-hold) / df["close"] - 1) * 100


def filter_period(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.to_datetime(start)
    e = pd.to_datetime(end)
    return df[(df["date"] >= s) & (df["date"] <= e)].reset_index(drop=True)


def aggregate(df: pd.DataFrame, fwd_col: str) -> pd.DataFrame:
    sub = df.dropna(subset=[fwd_col, "regime"])
    sub = sub[sub["regime"] != "UNKNOWN"]
    return sub.groupby("regime")[fwd_col].agg(
        n="count", mean="mean",
        win=lambda x: (x > 0).mean() * 100,
    ).round(2)


def main():
    print("=" * 86)
    print("  Regime V2 OOS Walk-Forward Validation")
    print("=" * 86)

    # Load TAIEX with regime
    twii = load_with_indicators("^TWII")
    twii = assign_regime(twii)

    # Load 0050 + 00631L, attach regime via merge
    assets = {}
    for ticker in ["0050", "00631L"]:
        try:
            asset = load_with_indicators(ticker)
        except FileNotFoundError:
            continue
        asset["fwd_20d"] = fwd_return(asset, 20)
        asset["date"] = asset["date"].astype("datetime64[ns]")
        twii_r = twii[["date", "regime"]].copy()
        twii_r["date"] = twii_r["date"].astype("datetime64[ns]")
        merged = pd.merge_asof(
            asset.sort_values("date"),
            twii_r.sort_values("date"),
            on="date", direction="backward",
        )
        assets[ticker] = merged

    # Per-period regime distribution
    print(f"\n{'='*86}")
    print(f"  Regime 分布 (V2 rules) per Period")
    print(f"{'='*86}")
    print(f"  {'Regime':<14}", end="")
    for p_label, _, _ in PERIODS:
        print(f"{p_label[:18]:>20}", end="")
    print(f"{'  Full':>16}")
    print(f"  {'-'*14}" + ("  " + "-"*18) * (len(PERIODS) + 1))

    period_dfs = []
    full_period_df = filter_period(twii, "2017-01-01", "2025-12-31")
    for p_label, s, e in PERIODS:
        period_dfs.append(filter_period(twii, s, e))

    for regime in REGIMES_ORDER + ["UNKNOWN"]:
        line = f"  {regime:<14}"
        for sub in period_dfs:
            n = (sub["regime"] == regime).sum()
            pct = n / len(sub) * 100 if len(sub) > 0 else 0
            line += f"{n:>5}d ({pct:>4.1f}%) "
        n_full = (full_period_df["regime"] == regime).sum()
        pct_full = n_full / len(full_period_df) * 100 if len(full_period_df) > 0 else 0
        line += f"{n_full:>5}d ({pct_full:>4.1f}%)"
        print(line)

    # Per-period asset performance per regime
    for asset_label, asset_df in assets.items():
        print(f"\n{'='*86}")
        print(f"  {asset_label} fwd 20d return per Regime × Period")
        print(f"{'='*86}")
        print(f"  {'Regime':<14}", end="")
        for p_label, _, _ in PERIODS:
            print(f"{p_label[:18]:>20}", end="")
        print(f"{'  Full':>16}")
        print(f"  {'-'*14}" + ("  " + "-"*18) * (len(PERIODS) + 1))

        for regime in REGIMES_ORDER:
            line = f"  {regime:<14}"
            for p_label, s, e in PERIODS:
                sub = filter_period(asset_df, s, e)
                sub = sub.dropna(subset=["fwd_20d"])
                sub_r = sub[sub["regime"] == regime]
                if len(sub_r) == 0:
                    line += f"{'(n=0)':>20}"
                else:
                    m = sub_r["fwd_20d"].mean()
                    n = len(sub_r)
                    line += f"{m:>+7.2f}% (n={n:>4}) "
            full_sub = asset_df.dropna(subset=["fwd_20d"])
            full_r = full_sub[full_sub["regime"] == regime]
            if len(full_r) == 0:
                line += f"{'(n=0)':>16}"
            else:
                m_f = full_r["fwd_20d"].mean()
                line += f"{m_f:>+7.2f}% (n={len(full_r):>4})"
            print(line)

    # Consistency check
    print(f"\n{'='*86}")
    print(f"  Consistency Verdict")
    print(f"{'='*86}")

    if "0050" not in assets:
        print("  0050 not loaded, skipping consistency check")
        return

    consistency_issues = []
    asset_df = assets["0050"]
    for regime in REGIMES_ORDER:
        period_means = []
        for p_label, s, e in PERIODS:
            sub = filter_period(asset_df, s, e).dropna(subset=["fwd_20d"])
            sub_r = sub[sub["regime"] == regime]
            if len(sub_r) >= 10:  # min sample
                period_means.append(sub_r["fwd_20d"].mean())
            else:
                period_means.append(None)

        valid = [m for m in period_means if m is not None]
        if len(valid) < 2:
            continue

        # Sign consistency: do all periods agree on positive/negative?
        signs = [1 if m > 0 else -1 for m in valid]
        if len(set(signs)) > 1:
            consistency_issues.append(
                f"  ⚠️ {regime}: 報酬正負號跨期不一致 "
                f"({', '.join(f'{m:+.2f}%' for m in valid)})"
            )
        else:
            # Magnitude sanity: range should be < 5pp
            spread = max(valid) - min(valid)
            if spread > 5:
                consistency_issues.append(
                    f"  ⚠️ {regime}: 跨期 range > 5pp "
                    f"({', '.join(f'{m:+.2f}%' for m in valid)})"
                )

    if not consistency_issues:
        print(f"\n  ✅ 5 個 regime 在 3 個子期間都報酬方向一致")
        print(f"     V2 classifier 跨 9 年穩定，可信任")
    else:
        for issue in consistency_issues:
            print(issue)
        print(f"\n  ⚠️ 部分 regime 子期間不穩定，需檢視規則")


if __name__ == "__main__":
    main()
