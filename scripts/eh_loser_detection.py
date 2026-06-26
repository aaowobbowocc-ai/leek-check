"""
診斷：我們的 30 天早砍真的準嗎？能更早更準嗎？

兩個維度：
  1. Trajectory analysis — 每筆 trade 在 day {10,20,30,45,60} 的位置 vs 最終結果
  2. Multi-feature — 進場後 N 天的 (return, volume_ratio, vs_MA20) 三特徵聯合

判決問題：
  Q1. cut=30 是否誤殺 winners？(在 day 30 時是負的，但最終 +50%+)
  Q2. cut 點 day 15/20/25 是不是其實更準？
  Q3. 加上 volume / MA position 能否提升早期判斷準度？
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
EH_CSV = ROOT / "logs" / "early_hunter_trailing_v2.csv"


def trade_trajectory(ticker: str, entry_date: date, days_list: list[int]) -> dict:
    """回傳每筆 trade 在指定 day 的 (ret%, vol_ratio, vs_ma20%)。"""
    df = load_ohlcv_cache(ticker, CACHE_YF)
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df["ma20"] = df["close"].rolling(20).mean()
    df["vol_30d"] = df["volume"].rolling(30).mean()

    after = df[df["date"] >= entry_date].reset_index(drop=True)
    if len(after) < max(days_list) + 1:
        return {}

    entry_close = float(after.iloc[0]["close"])
    entry_vol = float(after.iloc[0]["vol_30d"]) if pd.notna(after.iloc[0]["vol_30d"]) else 1
    out = {"entry_close": entry_close}
    for d in days_list:
        if d >= len(after):
            continue
        c = float(after.iloc[d]["close"])
        ma20 = after.iloc[d]["ma20"]
        vol_30d = after.iloc[d]["vol_30d"]
        out[f"ret_d{d}"] = (c / entry_close - 1) * 100
        out[f"vol_ratio_d{d}"] = (
            float(vol_30d) / entry_vol if pd.notna(vol_30d) and entry_vol > 0 else 1.0
        )
        out[f"vs_ma20_d{d}"] = (
            (c / float(ma20) - 1) * 100 if pd.notna(ma20) and ma20 > 0 else 0
        )
    return out


def main() -> None:
    df = pd.read_csv(EH_CSV)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["ticker"] = df["ticker"].astype(str)
    print(f"Sample: {len(df)} trades")

    days_list = [10, 15, 20, 30, 45, 60, 90]

    # 計算每筆 trajectory
    rows = []
    for _, t in df.iterrows():
        traj = trade_trajectory(t["ticker"], t["entry_date"], days_list)
        if not traj:
            continue
        rows.append({
            "ticker": t["ticker"],
            "entry_date": t["entry_date"],
            "final_return": float(t["gross_return_pct"]),
            "hold_days": int(t["hold_days"]),
            **traj,
        })
    tj = pd.DataFrame(rows)
    print(f"成功計算 trajectory: {len(tj)} / {len(df)}")

    # ── Q1. day 30 還虧的，最終是什麼下場？ ──
    print("\n" + "=" * 70)
    print("Q1. 30 天還虧（current cut rule）— 是不是誤殺 winners？")
    print("=" * 70)
    neg_d30 = tj[tj["ret_d30"] < 0]
    pos_d30 = tj[tj["ret_d30"] >= 0]
    print(f"  全 sample (n={len(tj)}):")
    print(f"    win rate {(tj['final_return']>0).mean()*100:.1f}%, "
          f"mean final {tj['final_return'].mean():+.2f}%")
    print(f"  day 30 still 虧 (n={len(neg_d30)}):")
    print(f"    final win rate {(neg_d30['final_return']>0).mean()*100:.1f}%, "
          f"mean final {neg_d30['final_return'].mean():+.2f}%")
    print(f"    最終 >+50% 的有: {(neg_d30['final_return']>50).sum()} 筆 (「誤殺」winners)")
    print(f"  day 30 已賺 (n={len(pos_d30)}):")
    print(f"    final win rate {(pos_d30['final_return']>0).mean()*100:.1f}%, "
          f"mean final {pos_d30['final_return'].mean():+.2f}%")

    # ── Q2. 不同 day 的早期斷點哪個最準？ ──
    print("\n" + "=" * 70)
    print("Q2. 不同 cut 點的精準度（基於 ret < threshold）")
    print("=" * 70)
    print(f"  {'規則':<22} {'砍掉':>5} {'砍中真loser%':>14} {'誤殺winners(>+50%)':>22}")
    for d in [10, 15, 20, 30, 45, 60, 90]:
        col = f"ret_d{d}"
        if col not in tj.columns:
            continue
        for thr in [0, -10, -20]:
            cut = tj[tj[col] < thr]
            if len(cut) == 0:
                continue
            true_loser = (cut["final_return"] < 0).sum()
            misskilled = (cut["final_return"] > 50).sum()
            print(
                f"  ret_d{d} < {thr}%        {len(cut):>5}  "
                f"{true_loser/len(cut)*100:>12.1f}%  {misskilled:>20}"
            )

    # ── Q3. 多特徵聯合 — day 30 三特徵 ──
    print("\n" + "=" * 70)
    print("Q3. day 30 多特徵聯合 — 能否更精準")
    print("=" * 70)
    if "ret_d30" in tj.columns and "vol_ratio_d30" in tj.columns:
        # 規則：ret<0 且 vol_ratio<0.7（量能也衰退） vs ret<0 alone
        cut_simple = tj[tj["ret_d30"] < 0]
        cut_combo = tj[(tj["ret_d30"] < 0) & (tj["vol_ratio_d30"] < 0.7)]
        cut_below_ma = tj[(tj["ret_d30"] < 0) & (tj["vs_ma20_d30"] < -3)]
        for name, c in [
            ("ret<0 only", cut_simple),
            ("ret<0 + vol<0.7", cut_combo),
            ("ret<0 + below MA20 -3%", cut_below_ma),
        ]:
            if len(c) == 0:
                continue
            tl = (c["final_return"] < 0).mean() * 100
            mk = (c["final_return"] > 50).sum()
            mean = c["final_return"].mean()
            print(
                f"  {name:<28} n={len(c):>3}  真loser% {tl:>5.1f}%  "
                f"誤殺 +50% {mk:>2}  mean final {mean:>+6.1f}%"
            )

    # ── Q4. winners 的 day-30 特徵反向：他們在 day 30 看起來如何？──
    print("\n" + "=" * 70)
    print("Q4. 大 winners (final >+50%) 在 day 30 的樣貌")
    print("=" * 70)
    winners = tj[tj["final_return"] > 50]
    print(f"  N winners: {len(winners)}")
    if len(winners) > 0:
        print(f"  day 30 ret    mean {winners['ret_d30'].mean():+.1f}%, "
              f"median {winners['ret_d30'].median():+.1f}%, "
              f"<0 占比 {(winners['ret_d30']<0).mean()*100:.1f}%")
        print(f"  day 30 vol_r  mean {winners['vol_ratio_d30'].mean():.2f}x")
        print(f"  day 30 vs_ma  mean {winners['vs_ma20_d30'].mean():+.2f}%")

    losers_big = tj[tj["final_return"] < -20]
    print(f"\n  N losers (final<-20%): {len(losers_big)}")
    if len(losers_big) > 0:
        print(f"  day 30 ret    mean {losers_big['ret_d30'].mean():+.1f}%, "
              f"median {losers_big['ret_d30'].median():+.1f}%, "
              f"<0 占比 {(losers_big['ret_d30']<0).mean()*100:.1f}%")
        print(f"  day 30 vol_r  mean {losers_big['vol_ratio_d30'].mean():.2f}x")
        print(f"  day 30 vs_ma  mean {losers_big['vs_ma20_d30'].mean():+.2f}%")


if __name__ == "__main__":
    main()
