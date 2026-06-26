"""
Revenue YoY Liquidity Stratification (ChatGPT #7 深挖)

問題: DSR 分析顯示 Revenue YoY SR = 0.23（遠低於其他訊號）
最可能根因: alpha 集中在日均成交量小的股票 → 實際無法執行

方法:
  1. 重跑 Revenue YoY 事件掃描（raw YoY > 30%，不做市場 median 調整以簡化）
  2. 每個事件標記觸發前 60 日平均日成交金額（close × volume，TWD）
  3. 分四分位 Q1（最不流動）→ Q4（最流動）
  4. 各 bucket 計算: mean alpha, t-stat, n, win rate

若 Q4 alpha ≈ 0 → 確認 alpha 僅在不可執行的小股中（SR 低的根因）
若 Q4 alpha > 1% → Revenue YoY 在流動股仍有真實 alpha
"""
from __future__ import annotations

import io
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
REV_DIR = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
COST = 0.78
HOLD = 60
YOY_THRESHOLD = 30.0   # raw YoY > 30% (simplified trigger)
PRIOR_REV_MIN = 1e7    # 至少 1000 萬月營收才計算 YoY


def collect_events() -> pd.DataFrame:
    """掃描所有 ticker 的 Revenue YoY 信號事件，記錄 fwd return + 流動性指標。"""
    records = []
    files = list(REV_DIR.glob("TaiwanStockMonthRevenue_*.parquet"))

    for i, p in enumerate(files):
        tk = p.stem.replace("TaiwanStockMonthRevenue_", "")
        if not tk.isdigit() or len(tk) != 4:
            continue

        try:
            rev = pd.read_parquet(p)
        except Exception:
            continue
        if len(rev) < 24:
            continue

        rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
        rev["prior_revenue"] = rev["revenue"].shift(12)
        rev["yoy"] = (rev["revenue"] / rev["prior_revenue"] - 1) * 100

        px_path = TW_CACHE / f"{tk}.parquet"
        if not px_path.exists():
            continue
        try:
            px = pd.read_parquet(px_path)
        except Exception:
            continue
        if len(px) < HOLD + 65:
            continue
        px["date"] = pd.to_datetime(px["date"]).dt.date
        px = px.sort_values("date").reset_index(drop=True)
        if "open" not in px.columns:
            continue

        for ridx in range(12, len(rev)):
            row = rev.iloc[ridx]
            yoy = float(row["yoy"]) if pd.notna(row["yoy"]) else float("nan")
            prior = float(row.get("prior_revenue", 0) or 0)
            if pd.isna(yoy) or yoy < YOY_THRESHOLD or abs(yoy) > 500:
                continue
            if prior < PRIOR_REV_MIN:
                continue

            # Estimate announce date
            period_start = pd.to_datetime(row["date"]).date()
            ct = row.get("create_time", None)
            if ct and pd.notna(ct) and ct != "":
                try:
                    ann_dt = pd.to_datetime(ct).date()
                except Exception:
                    ann_dt = period_start + timedelta(days=14)
            else:
                ann_dt = period_start + timedelta(days=14)

            # Find entry row (first row >= ann_dt)
            px_after = px.index[px["date"] >= ann_dt]
            if len(px_after) == 0:
                continue
            trigger_idx = int(px_after[0])
            if trigger_idx + 1 >= len(px) or trigger_idx + HOLD >= len(px):
                continue

            entry = float(px.iloc[trigger_idx + 1]["open"])
            if entry <= 0:
                continue
            exit_p = float(px.iloc[trigger_idx + HOLD]["close"])
            fwd = (exit_p / entry - 1) * 100 - COST

            # Liquidity: avg daily dollar volume 60d BEFORE signal
            liq_start = max(0, trigger_idx - 60)
            liq_sub = px.iloc[liq_start:trigger_idx]
            if len(liq_sub) < 10:
                continue
            avg_dv = float((liq_sub["close"] * liq_sub["volume"]).mean())
            if avg_dv <= 0:
                continue

            records.append({
                "ticker": tk,
                "ann_date": ann_dt.isoformat(),
                "fwd_return": fwd,
                "avg_dollar_vol": avg_dv,
                "yoy": yoy,
            })

        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(files)}] events={len(records)}")

    return pd.DataFrame(records)


def main():
    print("=" * 70)
    print("  Revenue YoY Liquidity Stratification")
    print("  目的: 確認 SR=0.23 根因 — alpha 在哪個流動性層？")
    print("=" * 70)

    print(f"\n  掃描 Revenue YoY 事件（raw YoY > {YOY_THRESHOLD}%）...")
    df = collect_events()

    if df.empty:
        print("  ❌ 無事件，請確認資料路徑")
        return

    print(f"\n  總事件數: {len(df):,}")
    t_all, p_all = stats.ttest_1samp(df["fwd_return"], 0, alternative="greater")
    print(f"  整體 alpha: mean={df['fwd_return'].mean():+.2f}%  "
          f"t={t_all:.2f}  p={p_all:.5f}  win={(df['fwd_return']>0).mean()*100:.1f}%")

    # 分四分位 by avg_dollar_vol
    q_labels = ["Q1-最不流動", "Q2", "Q3", "Q4-最流動"]
    df["liq_q"] = pd.qcut(df["avg_dollar_vol"], q=4, labels=q_labels)

    # 流動性門檻參考
    thresholds = df["avg_dollar_vol"].quantile([0.25, 0.5, 0.75]) / 1e8
    print(f"\n  流動性門檻（日均成交金額）:")
    print(f"    Q1/Q2 分界: {thresholds[0.25]:.1f} 億/日")
    print(f"    Q2/Q3 分界: {thresholds[0.50]:.1f} 億/日")
    print(f"    Q3/Q4 分界: {thresholds[0.75]:.1f} 億/日")
    print(f"    （Q4 平均: {df[df['liq_q']=='Q4-最流動']['avg_dollar_vol'].mean()/1e8:.1f} 億/日）")

    print(f"\n  {'Quartile':<16} {'n':>6}  {'mean fwd':>10}  {'t-stat':>8}  "
          f"{'p':>8}  {'win%':>6}  {'avg 億/日':>10}")
    print(f"  {'-'*16} {'-'*6}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*6}  {'-'*10}")

    results = []
    for q in q_labels:
        sub = df[df["liq_q"] == q]["fwd_return"].dropna()
        if len(sub) < 10:
            continue
        t, p = stats.ttest_1samp(sub, 0, alternative="greater")
        avg_dv = df[df["liq_q"] == q]["avg_dollar_vol"].mean() / 1e8
        win = (sub > 0).mean() * 100
        sig = "✅" if p < 0.05 else ("⚠️" if p < 0.2 else "❌")
        print(f"  {q:<16} {len(sub):>6,}  {sub.mean():>+10.2f}%  {t:>8.2f}  "
              f"{p:>8.5f}{sig}  {win:>6.1f}%  {avg_dv:>10.1f}")
        results.append({"q": q, "n": len(sub), "mean": sub.mean(), "t": t, "p": p,
                        "win": win, "avg_dv_bil": avg_dv})

    if len(results) < 2:
        print("  ❌ 資料不足，無法分析")
        return

    q1_alpha = results[0]["mean"]
    q4_alpha = results[-1]["mean"]
    q4_sig = results[-1]["p"] < 0.05

    print(f"\n  📊 Key Finding:")
    print(f"    Q1 (最不流動, ~{results[0]['avg_dv_bil']:.1f}億/日) alpha: {q1_alpha:+.2f}%")
    print(f"    Q4 (最流動,   ~{results[-1]['avg_dv_bil']:.1f}億/日) alpha: {q4_alpha:+.2f}%")

    if not q4_sig:
        print(f"\n  🚨 Q4 alpha p>{0.05:.2f}（統計不顯著）")
        print(f"     Revenue YoY alpha 幾乎不存在於流動股 — SR=0.23 根因確認")
        print(f"     alpha 縮減: {(1 - q4_alpha/q1_alpha)*100:.0f}% vs Q1")
        print(f"     結論: Revenue YoY 不應作為流動股的主力訊號，更適合小股策略")
    elif q4_alpha < q1_alpha * 0.5:
        print(f"\n  ⚠️ alpha 集中在不流動小股 ({(1-q4_alpha/q1_alpha)*100:.0f}% 縮減)")
        print(f"     可執行 alpha（Q4）: {q4_alpha:+.2f}%，大幅低於全樣本 {df['fwd_return'].mean():+.2f}%")
        print(f"     建議: 加入流動性 filter（日均成交 > {thresholds[0.75]:.0f}億），接受 alpha 縮減")
    else:
        print(f"\n  ✅ Q4 仍有顯著 alpha {q4_alpha:+.2f}% — Revenue YoY 在流動股有可執行 alpha")
        print(f"     SR 低的根因可能是分佈肥尾而非流動性問題")

    # 流動性 filter 後的實際可用 alpha
    q4_mask = df["liq_q"] == "Q4-最流動"
    q4_data = df[q4_mask]["fwd_return"]
    print(f"\n  流動性 filter 後（Q4 only, n={len(q4_data):,}）:")
    print(f"    mean alpha: {q4_data.mean():+.2f}%  vs 全樣本: {df['fwd_return'].mean():+.2f}%")
    n_ratio = len(q4_data) / len(df) * 100
    print(f"    樣本保留: {n_ratio:.0f}%（篩掉 {100-n_ratio:.0f}% 不流動事件）")


if __name__ == "__main__":
    main()
