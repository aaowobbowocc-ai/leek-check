"""
#4 法人買賣超 velocity — 速度突變 vs 累積值，哪個更能預測 winners？

我們已知 T13 = 30 日累積淨買 / 30 日均量 → spearman -0.116（反向）
意思「跟著法人買 = 反而表現差」。

重新測試「動態」訊號：
  V1. 5 日累積淨買 / 30 日累積淨買（短中期 velocity ratio）
  V2. 進場日法人淨買 / 30 日均淨買（單日突變 z-score）
  V3. 連續法人買超天數（外資 + 投信 + 自營）
  V4. 法人淨買加速度（5d 平均 - 30d 平均）
  V5. 三大法人「共同買」連續天數（外+投+自 同日皆淨買）
  V6. 進場前 5 日 vs 進場前 30 日 法人淨買比例

對 weekly 10182 sample 測 Spearman 相關性 + win rate lift。
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_FM = ROOT / "data" / "cache" / "finmind" / "finmind"
WEEKLY_CSV = ROOT / "logs" / "early_hunter_weekly_v2.csv"


def compute_inst_velocity_features(ticker: str, entry_date: date) -> dict:
    inst_path = CACHE_FM / f"TaiwanStockInstitutionalInvestorsBuySell_{ticker}.parquet"
    if not inst_path.exists():
        return {}
    try:
        inst = pd.read_parquet(inst_path)
    except Exception:
        return {}
    if inst.empty:
        return {}

    inst["date"] = pd.to_datetime(inst["date"]).dt.date
    inst = inst[inst["date"] <= entry_date].copy()
    if len(inst) < 30:
        return {}

    # 計算每日各法人淨買
    inst["net_buy"] = inst["buy"] - inst["sell"]
    daily_net = inst.groupby(["date", "name"])["net_buy"].sum().unstack(fill_value=0)
    if daily_net.empty:
        return {}

    # 三大法人：外資 / 投信 / 自營商（英文 column names）
    foreign_col = next((c for c in daily_net.columns if "Foreign" in c), None)
    inv_col = next((c for c in daily_net.columns if "Investment_Trust" in c), None)
    self_col = next((c for c in daily_net.columns if "Dealer" in c), None)
    cols = [c for c in [foreign_col, inv_col, self_col] if c]
    if not cols:
        return {}
    daily_net["total"] = daily_net[cols].sum(axis=1)

    # 排序日期升冪
    daily_net = daily_net.sort_index()
    last_30 = daily_net.tail(30)
    last_5 = daily_net.tail(5)

    out = {}
    # V1. 5 日 / 30 日 velocity ratio
    sum_30 = last_30["total"].sum()
    sum_5 = last_5["total"].sum()
    if abs(sum_30) > 1:
        out["V1_5d_30d_ratio"] = sum_5 / sum_30
    # V2. 進場日 / 30 日均（z-score）
    last_day_net = float(last_30.iloc[-1]["total"]) if len(last_30) else 0
    mean_30 = last_30["total"].mean()
    std_30 = last_30["total"].std()
    if std_30 > 0:
        out["V2_entry_z"] = (last_day_net - mean_30) / std_30
    # V3. 連續法人 total > 0 天數
    streak = 0
    for v in last_30["total"].values[::-1]:
        if v > 0:
            streak += 1
        else:
            break
    out["V3_consec_buy"] = streak
    # V4. 加速度
    out["V4_accel"] = last_5["total"].mean() - last_30["total"].mean()
    # V5. 三大法人同時買的天數
    if len(cols) == 3:
        all_buy = (last_30[cols] > 0).all(axis=1)
        out["V5_all_buy_days"] = int(all_buy.sum())
        # consecutive
        streak = 0
        for v in all_buy.values[::-1]:
            if v:
                streak += 1
            else:
                break
        out["V5b_all_buy_streak"] = streak
    # V6. 5d 累積 / 30d 累積（差異版）
    if abs(sum_30) > 1:
        out["V6_5d_30d_share"] = sum_5 / sum_30 if sum_30 != 0 else 0

    return out


def main() -> None:
    df = pd.read_csv(WEEKLY_CSV)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["ticker"] = df["ticker"].astype(str)
    print(f"Sample: {len(df)} EH weekly trades")

    rows = []
    for i, t in enumerate(df.itertuples(), 1):
        feats = compute_inst_velocity_features(t.ticker, t.entry_date)
        if not feats:
            continue
        rows.append({
            "ticker": t.ticker,
            "entry_date": t.entry_date,
            "final_return": float(t.gross_return_pct),
            **feats,
        })
        if i % 1000 == 0:
            print(f"  [{i}/{len(df)}]  rows={len(rows)}")

    fdf = pd.DataFrame(rows)
    print(f"\n計算完成: {len(fdf)} trades 有法人資料")

    feat_cols = [c for c in fdf.columns if c.startswith("V")]
    rows_corr = []
    for c in feat_cols:
        valid = fdf[[c, "final_return"]].dropna()
        if len(valid) < 100:
            continue
        spear = valid[c].rank().corr(valid["final_return"].rank())
        q = valid[c].quantile([0.25, 0.5, 0.75]).values
        bot = valid[valid[c] <= q[0]]
        top = valid[valid[c] >= q[2]]
        rows_corr.append({
            "feature": c,
            "n": len(valid),
            "spearman": spear,
            "bot_q_win%": (bot["final_return"] > 0).mean() * 100,
            "top_q_win%": (top["final_return"] > 0).mean() * 100,
            "win_lift": (
                (top["final_return"] > 0).mean()
                - (bot["final_return"] > 0).mean()
            ) * 100,
            "bot_q_mean": bot["final_return"].mean(),
            "top_q_mean": top["final_return"].mean(),
        })

    out = pd.DataFrame(rows_corr).sort_values("win_lift", ascending=False)
    print(f"\n  {'feature':<24} {'n':>5} {'spear':>7} {'bot win':>8} {'top win':>8} "
          f"{'lift':>7} {'bot mean':>9} {'top mean':>9}")
    for _, r in out.iterrows():
        print(
            f"  {r['feature']:<24} {r['n']:>5} {r['spearman']:>+6.3f} "
            f"{r['bot_q_win%']:>7.1f}% {r['top_q_win%']:>7.1f}% "
            f"{r['win_lift']:>+6.1f}pp {r['bot_q_mean']:>+8.2f}% {r['top_q_mean']:>+8.2f}%"
        )

    out.to_csv(ROOT / "logs" / "eh_inst_velocity_corr.csv", index=False)
    print(f"\n寫入 logs/eh_inst_velocity_corr.csv")

    # Spot check：top quartile 的 V2 / V3 / V5b 對應的 final return 分布
    print("\n" + "=" * 70)
    print("Top winners 的 inst velocity 樣貌")
    print("=" * 70)
    big_winners = fdf[fdf["final_return"] > 50]
    print(f"  N big winners (>+50%): {len(big_winners)}")
    if len(big_winners) > 0:
        for c in feat_cols:
            if c in big_winners.columns:
                print(f"    {c:<22} mean {big_winners[c].mean():>+7.2f}, "
                      f"median {big_winners[c].median():>+7.2f}")


if __name__ == "__main__":
    main()
