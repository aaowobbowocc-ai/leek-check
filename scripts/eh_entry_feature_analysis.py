"""
B 路線 step 1：找出真正能預測 winners 的 entry-time features。

對 monthly 96 trades，每筆計算下列 entry-time 特徵：
  T1. vol_5d / vol_60d（短期量能爆發）
  T2. vol_30d / vol_252d（中期量能擴張，原 EH factor）
  T3. close / ma5 - 1（與 5MA 距離）
  T4. close / ma20 - 1
  T5. close / ma60 - 1
  T6. close / ma200 - 1（原 EH 用過）
  T7. ma5 / ma20 - 1（短中期均線排列）
  T8. ma20 / ma60 - 1（中長期均線排列）
  T9. 過去 30d max(close)/min(close) - 1（30 日波動範圍）
  T10. 過去 60d return（中期 momentum）
  T11. 過去 120d return（半年 momentum）
  T12. 過去 5d 紅 K 比例
  T13. 法人累積買超 / 流通張數（30 日）— 需 institutional cache
  T14. 連續紅 K 天數（5d 內）

對每個特徵，計算與 final_return 的 Spearman 相關性。
找 |corr| > 0.15 的特徵當作新 score 候選。
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
sys.path.insert(0, str(ROOT / "scripts"))

from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_FM = ROOT / "data" / "cache" / "finmind" / "finmind"
EH_CSV = ROOT / "logs" / "early_hunter_trailing_v2.csv"
WEEKLY_CSV = ROOT / "logs" / "early_hunter_weekly_v2.csv"


def compute_entry_features(ticker: str, entry_date: date) -> dict:
    df = load_ohlcv_cache(ticker, CACHE_YF)
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)

    # 截至 entry_date 為止
    prior = df[df["date"] <= entry_date]
    if len(prior) < 252:
        return {}

    c = float(prior.iloc[-1]["close"])
    closes = prior["close"].astype(float)
    vols = prior["volume"].astype(float)

    out = {}
    # T1
    if vols.tail(60).mean() > 0:
        out["T1_vol_5_60"] = vols.tail(5).mean() / vols.tail(60).mean()
    # T2
    if vols.tail(252).mean() > 0:
        out["T2_vol_30_252"] = vols.tail(30).mean() / vols.tail(252).mean()
    # T3-T6 MAs
    ma5 = closes.tail(5).mean()
    ma20 = closes.tail(20).mean()
    ma60 = closes.tail(60).mean()
    ma200 = closes.tail(200).mean()
    out["T3_close_vs_ma5"] = (c / ma5 - 1) * 100 if ma5 > 0 else 0
    out["T4_close_vs_ma20"] = (c / ma20 - 1) * 100 if ma20 > 0 else 0
    out["T5_close_vs_ma60"] = (c / ma60 - 1) * 100 if ma60 > 0 else 0
    out["T6_close_vs_ma200"] = (c / ma200 - 1) * 100 if ma200 > 0 else 0
    out["T7_ma5_vs_ma20"] = (ma5 / ma20 - 1) * 100 if ma20 > 0 else 0
    out["T8_ma20_vs_ma60"] = (ma20 / ma60 - 1) * 100 if ma60 > 0 else 0
    # T9: 30 日波動範圍
    win30 = prior.tail(30)
    out["T9_range_30d"] = (
        (win30["close"].astype(float).max() / win30["close"].astype(float).min() - 1) * 100
        if win30["close"].astype(float).min() > 0 else 0
    )
    # T10/T11
    if len(prior) >= 60:
        out["T10_ret_60d"] = (c / float(prior.iloc[-60]["close"]) - 1) * 100
    if len(prior) >= 120:
        out["T11_ret_120d"] = (c / float(prior.iloc[-120]["close"]) - 1) * 100
    if len(prior) >= 252:
        out["T11b_ret_252d"] = (c / float(prior.iloc[-252]["close"]) - 1) * 100
    # T12: 過去 5d 紅 K 比例
    last5 = prior.tail(6)
    pcs = last5["close"].astype(float).pct_change().dropna()
    if len(pcs) > 0:
        out["T12_red_k_ratio_5d"] = (pcs > 0).sum() / len(pcs)
    # T14: 連續紅 K 天數
    streak = 0
    for r in pcs.values[::-1]:
        if r > 0:
            streak += 1
        else:
            break
    out["T14_red_streak"] = streak

    # T13: 法人累積買超 / 流通張數（30 日）
    inst_path = CACHE_FM / f"TaiwanStockInstitutionalInvestorsBuySell_{ticker}.parquet"
    if inst_path.exists():
        try:
            inst = pd.read_parquet(inst_path)
            inst["date"] = pd.to_datetime(inst["date"]).dt.date
            inst30 = inst[
                (inst["date"] <= entry_date)
                & (inst["date"] > entry_date - pd.Timedelta(days=45))
            ]
            net = (inst30["buy"].sum() - inst30["sell"].sum())
            avg_vol_30 = vols.tail(30).mean()
            if avg_vol_30 > 0:
                # 30 日累積淨買 / 30 日平均量
                out["T13_inst_net_30d_vol_ratio"] = net / (avg_vol_30 * 30)
        except Exception:
            pass

    return out


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(EH_CSV))
    args = parser.parse_args()
    df = pd.read_csv(args.csv)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["ticker"] = df["ticker"].astype(str)
    print(f"Sample: {len(df)} EH monthly trades")

    rows = []
    for i, t in enumerate(df.itertuples(), 1):
        feats = compute_entry_features(t.ticker, t.entry_date)
        if not feats:
            continue
        rows.append({
            "ticker": t.ticker,
            "entry_date": t.entry_date,
            "final_return": float(t.gross_return_pct),
            **feats,
        })
        if i % 30 == 0:
            print(f"  [{i}/{len(df)}]")

    fdf = pd.DataFrame(rows)
    print(f"\n計算完成: {len(fdf)} trades")

    # 與 final_return 的 Spearman 相關性
    print("\n" + "=" * 70)
    print("Entry-time features vs final_return 相關性")
    print("=" * 70)

    feat_cols = [c for c in fdf.columns if c.startswith("T")]
    rows_corr = []
    for c in feat_cols:
        valid = fdf[[c, "final_return"]].dropna()
        if len(valid) < 30:
            continue
        spear = valid[c].rank().corr(valid["final_return"].rank())
        # win rate by quartile
        q = valid[c].quantile([0.25, 0.5, 0.75]).values
        bot = valid[valid[c] <= q[0]]
        top = valid[valid[c] >= q[2]]
        bot_win = (bot["final_return"] > 0).mean() * 100 if len(bot) else 0
        top_win = (top["final_return"] > 0).mean() * 100 if len(top) else 0
        rows_corr.append({
            "feature": c,
            "n": len(valid),
            "spearman": spear,
            "bot_q_win%": bot_win,
            "top_q_win%": top_win,
            "win_lift": top_win - bot_win,
        })

    out = pd.DataFrame(rows_corr).sort_values("win_lift", ascending=False)
    print(f"\n  {'feature':<28} {'n':>4} {'spear':>7} {'bot_q win%':>11} {'top_q win%':>11} {'lift':>7}")
    for _, r in out.iterrows():
        print(
            f"  {r['feature']:<28} {r['n']:>4} {r['spearman']:>+6.3f} "
            f"{r['bot_q_win%']:>10.1f}% {r['top_q_win%']:>10.1f}% "
            f"{r['win_lift']:>+6.1f}pp"
        )

    out.to_csv(ROOT / "logs" / "eh_entry_feature_corr.csv", index=False)
    print(f"\n已寫入 logs/eh_entry_feature_corr.csv")


if __name__ == "__main__":
    main()
