"""
EH 散戶比例分析。

問題：散戶持股比例（= 1 - 大戶持股%）能否預測 / 識別 winner / loser？

對 weekly 10182 sample，每筆 trade at entry_date 計算：
  R1. retail_pct          = 100 - big_holder_pct (絕對值)
  R2. retail_pct_4w_chg   = 4 週前 retail_pct 變化（散戶湧入速度）
  R3. retail_pct_z_8w     = 8 週 z-score（突發程度）

按 final return 分組（big_winners / modest / losers）看分佈差異。

結論影響：
  若散戶 metrics 跟 final return 有顯著差異 → 可作為 cut signal（不是 entry filter，
  因為已驗證 V2 framework 下 entry filter 都退化）。
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE_FM = ROOT / "data" / "cache" / "finmind" / "finmind"
WEEKLY_CSV = ROOT / "logs" / "early_hunter_weekly_v2.csv"


def compute_retail_features(ticker: str, entry_date: date) -> dict:
    """從 HoldingSharesPer cache 計算散戶比例 features。"""
    path = CACHE_FM / f"TaiwanStockHoldingSharesPer_{ticker}.parquet"
    if not path.exists():
        return {}
    try:
        df = pd.read_parquet(path)
    except Exception:
        return {}
    df = df[df["HoldingSharesLevel"] == "more than 1,000,001"].copy()
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date")
    df["retail_pct"] = 100.0 - df["percent"].astype(float)

    # entry_date 之前最近一筆
    prior = df[df["date"] <= entry_date]
    if prior.empty:
        return {}
    cur_retail = float(prior.iloc[-1]["retail_pct"])

    # 4 週前
    pre4w = df[df["date"] <= entry_date - timedelta(weeks=4)]
    chg_4w = (cur_retail - float(pre4w.iloc[-1]["retail_pct"])) if not pre4w.empty else None

    # 8 週 z-score
    win8w = df[
        (df["date"] <= entry_date)
        & (df["date"] > entry_date - timedelta(weeks=8))
    ]
    z = None
    if len(win8w) >= 4:
        std = win8w["retail_pct"].std()
        if std > 0:
            z = (cur_retail - win8w["retail_pct"].mean()) / std

    return {
        "R1_retail_pct": cur_retail,
        "R2_retail_4w_chg": chg_4w,
        "R3_retail_z_8w": z,
    }


def main() -> None:
    df = pd.read_csv(WEEKLY_CSV)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["ticker"] = df["ticker"].astype(str)
    print(f"Sample: {len(df)} EH weekly trades")

    rows = []
    for i, t in enumerate(df.itertuples(), 1):
        feats = compute_retail_features(t.ticker, t.entry_date)
        if not feats:
            continue
        rows.append({
            "ticker": t.ticker,
            "entry_date": t.entry_date,
            "final_return": float(t.gross_return_pct),
            "hold_days": int(t.hold_days),
            **feats,
        })
        if i % 1500 == 0:
            print(f"  [{i}/{len(df)}] valid={len(rows)}")

    rd = pd.DataFrame(rows)
    print(f"\n計算完成: {len(rd)} trades 有大戶持股資料")

    # ── 1. 全 sample 相關性 ──
    print("\n" + "=" * 70)
    print("1. 全 sample Spearman 相關性 + win rate lift")
    print("=" * 70)
    feat_cols = ["R1_retail_pct", "R2_retail_4w_chg", "R3_retail_z_8w"]
    print(f"  {'feature':<22} {'n':>5} {'spear':>7} {'bot win%':>9} {'top win%':>9} {'lift':>7}")
    for c in feat_cols:
        valid = rd[[c, "final_return"]].dropna()
        if len(valid) < 100:
            continue
        spear = valid[c].rank().corr(valid["final_return"].rank())
        q25, q75 = valid[c].quantile([0.25, 0.75]).values
        bot = valid[valid[c] <= q25]
        top = valid[valid[c] >= q75]
        bot_win = (bot["final_return"] > 0).mean() * 100
        top_win = (top["final_return"] > 0).mean() * 100
        print(
            f"  {c:<22} {len(valid):>5} {spear:>+6.3f} "
            f"{bot_win:>8.1f}% {top_win:>8.1f}% {top_win-bot_win:>+6.1f}pp"
        )

    # ── 2. 按 outcome 分組 ──
    print("\n" + "=" * 70)
    print("2. 按 final_return 分組看散戶 metrics 分佈")
    print("=" * 70)
    rd["outcome"] = pd.cut(
        rd["final_return"],
        bins=[-100, -20, 0, 50, 1500],
        labels=["big_loser(<-20)", "loser(-20~0)", "modest_winner(0~50)", "big_winner(>50)"],
    )

    summary = rd.groupby("outcome", observed=True).agg(
        n=("final_return", "count"),
        retail_pct_mean=("R1_retail_pct", "mean"),
        retail_pct_med=("R1_retail_pct", "median"),
        retail_4w_chg_mean=("R2_retail_4w_chg", "mean"),
        retail_z_mean=("R3_retail_z_8w", "mean"),
    ).round(2)
    print(summary.to_string())

    # ── 3. 大 winners vs 大 losers t-test ──
    print("\n" + "=" * 70)
    print("3. Big winners (>+50%) vs Big losers (<-20%) 對比")
    print("=" * 70)
    big_w = rd[rd["final_return"] > 50]
    big_l = rd[rd["final_return"] < -20]
    print(f"  N big winners: {len(big_w)}, N big losers: {len(big_l)}")
    for c in feat_cols:
        w_mean = big_w[c].mean() if c in big_w.columns else None
        l_mean = big_l[c].mean() if c in big_l.columns else None
        if w_mean is None or pd.isna(w_mean):
            continue
        diff = w_mean - l_mean
        # crude t-test
        from scipy import stats as scipy_stats
        try:
            t_stat, p_val = scipy_stats.ttest_ind(
                big_w[c].dropna(), big_l[c].dropna(), equal_var=False,
            )
        except Exception:
            t_stat, p_val = 0, 1
        sig = "***" if p_val < 0.01 else "**" if p_val < 0.05 else "*" if p_val < 0.10 else ""
        print(
            f"  {c:<22} winner_mean={w_mean:>+7.3f}  "
            f"loser_mean={l_mean:>+7.3f}  diff={diff:>+7.3f}  "
            f"t={t_stat:>+5.2f}  p={p_val:.4f} {sig}"
        )

    # ── 4. 散戶比例極端區（top 10% / bottom 10%）的 outcome ──
    print("\n" + "=" * 70)
    print("4. 散戶比例極端區的 outcome 分佈")
    print("=" * 70)
    for c in feat_cols:
        valid = rd[[c, "final_return"]].dropna()
        if len(valid) < 200:
            continue
        q10, q90 = valid[c].quantile([0.10, 0.90]).values
        low = valid[valid[c] <= q10]
        high = valid[valid[c] >= q90]
        print(f"\n  {c}:")
        print(
            f"    bottom 10% (n={len(low)}): mean_return {low['final_return'].mean():>+6.2f}%  "
            f"win_rate {(low['final_return']>0).mean()*100:>5.1f}%"
        )
        print(
            f"    top    10% (n={len(high)}): mean_return {high['final_return'].mean():>+6.2f}%  "
            f"win_rate {(high['final_return']>0).mean()*100:>5.1f}%"
        )

    # 寫出
    out = ROOT / "logs" / "eh_retail_features.csv"
    rd.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
