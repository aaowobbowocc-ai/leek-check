"""
Scalp Portfolio Review — 用較低 gate 重看所有 ORB / Pair / Fake breakdown / Sector FOMO 結果。

新 gate（精準成本下）：
  - Mean net > +0.05%（每筆淨賺即 OK）
  - Win > 52%（接近 50/50 但有正期望值）
  - n > 15（避免 sample 太小）

對既有 logs/ 中所有 sweep CSV 跑統一篩選 → 列出所有合格 config。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]

# 校正成本：原本 0.49%，新 0.34%（3 折手續費 + 當沖稅）
OLD_COST = 0.49
NEW_COST = 0.34
COST_RECOVERY = OLD_COST - NEW_COST  # 0.15pp

# 新 gate
MEAN_GATE = 0.05
WIN_GATE = 52.0
N_GATE = 15


def adjust(df: pd.DataFrame, mean_col: str = "mean_net") -> pd.DataFrame:
    """加回 0.15pp 成本（補回精準成本下的 mean）。"""
    df = df.copy()
    df[mean_col + "_adj"] = df[mean_col] + COST_RECOVERY
    return df


def filter_config(df: pd.DataFrame, mean_col: str = "mean_net_adj",
                  win_col: str = "win_pct", n_col: str = "n") -> pd.DataFrame:
    if df.empty:
        return df
    return df[
        (df[mean_col] > MEAN_GATE)
        & (df[win_col] > WIN_GATE)
        & (df[n_col] > N_GATE)
    ].copy()


def main() -> None:
    print("=" * 100)
    print(f"Scalp Portfolio Review")
    print(f"  原成本 {OLD_COST}% → 精準成本 {NEW_COST}%（修正 +{COST_RECOVERY}pp）")
    print(f"  Gate: mean_net > +{MEAN_GATE}% AND win > {WIN_GATE}% AND n > {N_GATE}")
    print("=" * 100)

    sources = [
        ("logs/orb_signals.csv", "ORB single ticker"),
        ("logs/pair_lag_trade_summary.csv", "Pair Lag Long v1"),
        ("logs/pair_lag_trade_v2_summary.csv", "Pair Lag Long v2 (tighter)"),
        ("logs/sector_fomo_summary.csv", "Sector FOMO v3"),
        ("logs/pair_lag_short_summary.csv", "Pair Short v1"),
        ("logs/pair_lag_short_v2_summary.csv", "Pair Short v2 FOMO"),
        ("logs/orb_exit_sweep.csv", "ORB Exit Sweep (universe-level)"),
        ("logs/fake_breakdown_summary.csv", "Fake Breakdown Reversal"),
    ]

    survivors_total = []
    for path_rel, label in sources:
        path = ROOT / path_rel
        if not path.exists():
            print(f"\n  ❌ {label}: 找不到 {path_rel}")
            continue
        df = pd.read_csv(path)
        if df.empty:
            continue

        # ORB signals 比較特殊（per-trade rows，不是 per-config）— 處理 ticker level
        if "orb_signals" in path_rel:
            print(f"\n=== {label} (per-ticker) ===")
            agg = df.groupby("ticker").agg(
                n=("net_return_pct", "count"),
                win_pct=("net_return_pct", lambda x: (x > 0).mean() * 100),
                mean_net=("net_return_pct", "mean"),
            ).reset_index()
            agg["mean_net_adj"] = agg["mean_net"] + COST_RECOVERY
            survivors = filter_config(agg, "mean_net_adj", "win_pct", "n")
            if not survivors.empty:
                print(survivors.round(3).to_string(index=False))
                survivors["source"] = label
                survivors_total.append(survivors[["source", "ticker", "n", "win_pct", "mean_net", "mean_net_adj"]])
            else:
                print("  無符合的 ticker")
            continue

        # 一般 sweep 結果
        df = adjust(df)
        survivors = filter_config(df)

        print(f"\n=== {label} ===")
        if survivors.empty:
            print(f"  總 {len(df)} configs，無符合（mean+0.34% > +0.05%, win>52%, n>15）")
        else:
            print(f"  總 {len(df)} configs，**{len(survivors)} 個符合**：")
            cols_to_show = [c for c in ["pair", "sector", "config", "lag", "exit", "vol_mult",
                                          "stop_buf", "trail_tp", "n", "win_pct", "mean_net", "mean_net_adj"]
                           if c in survivors.columns]
            print(survivors[cols_to_show].round(3).to_string(index=False))
            survivors["source"] = label
            survivors_total.append(survivors)

    # 合併
    if survivors_total:
        print("\n" + "=" * 100)
        print(f"📊 合計：{sum(len(s) for s in survivors_total)} 個 config 在新 gate 下生存")
        print("=" * 100)
        out = ROOT / "logs" / "scalp_portfolio_survivors.csv"
        all_df = pd.concat(survivors_total, ignore_index=True)
        all_df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"寫入 {out.relative_to(ROOT)}")

        # Top 10 by mean_net_adj
        if "mean_net_adj" in all_df.columns:
            print("\nTop 10 by mean_net (調整後):")
            print(all_df.sort_values("mean_net_adj", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main()
