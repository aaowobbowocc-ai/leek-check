"""
中優先度訊號 backtest

Signal 4: 期貨基差 sentiment
  basis = futures_close - taiex_spot_close
  basis_pct = basis / taiex * 100
  Hypothesis: basis 極端值（z > +2 / < -2） → 法人預期反映後 TAIEX forward return

Signal 6: 個股級行庫共識度
  consensus = 八大行庫中今天 net buy 該個股的銀行數
  Hypothesis: 6+ 銀行同時買 → 政策性訊號 → 個股 forward return alpha

跳過：
  Signal 5 (三因子組合)：需要 3 個資料 align，n 會極少
  Signal 7 (期貨跨期)：futures_daily 已含 contract_date，但需處理 roll，複雜
"""
from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = ROOT / "data" / "cache" / "finmind" / "extras"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
OUT_DIR = ROOT / "scripts" / "output"
OUT_DIR.mkdir(exist_ok=True, parents=True)

HOLD_PERIODS = [5, 10, 20, 60]


# ─── Signal 4: 期貨基差 ───
def signal_4_futures_basis():
    print("=" * 80)
    print("  ▶ Signal 4: 期貨基差 (TX 期貨 vs TAIEX 現貨)")
    print("=" * 80)

    fut_df = pd.read_parquet(EXTRAS / "futures_daily.parquet")
    fut_df = fut_df[fut_df["futures_id"] == "TX"].copy()
    fut_df["date"] = pd.to_datetime(fut_df["date"])
    # 取最近月合約（trading_session = 'position' 或最早 contract_date）
    fut_df = fut_df.sort_values(["date", "contract_date"])
    fut_near = fut_df.groupby("date").first().reset_index()
    print(f"  TX 期貨 daily: {len(fut_near)} rows")

    # 載 TAIEX
    import yfinance as yf
    h = yf.Ticker("^TWII").history(period="3000d", auto_adjust=False)
    spot = pd.DataFrame({
        "date": pd.to_datetime(h.index).tz_localize(None),
        "spot_close": h["Close"].values,
    })

    df = fut_near.merge(spot, on="date", how="inner")
    df["basis"] = df["close"] - df["spot_close"]
    df["basis_pct"] = df["basis"] / df["spot_close"] * 100
    df["basis_ma"] = df["basis_pct"].rolling(60).mean()
    df["basis_std"] = df["basis_pct"].rolling(60).std()
    df["basis_z"] = (df["basis_pct"] - df["basis_ma"]) / df["basis_std"]

    print(f"  Basis stats: mean={df['basis_pct'].mean():.2f}%, "
          f"std={df['basis_pct'].std():.2f}%")

    # Forward returns
    for h in HOLD_PERIODS:
        df[f"fwd_{h}d"] = (df["spot_close"].shift(-h) / df["spot_close"] - 1) * 100

    df = df.dropna(subset=["basis_z"]).copy()

    # Event study
    rows = []
    for z in [1.0, 1.5, 2.0, 2.5]:
        for h in HOLD_PERIODS:
            fwd = f"fwd_{h}d"
            base_mean = df[fwd].mean()
            base_std = df[fwd].std()

            long_s = df[df["basis_z"] > z]
            short_s = df[df["basis_z"] < -z]

            for direction, sub in [("long", long_s), ("short", short_s)]:
                n = len(sub.dropna(subset=[fwd]))
                if n < 30: continue
                mean = sub[fwd].mean()
                alpha = mean - base_mean
                # short 訊號：預期 fwd 跌，alpha = -mean + base
                if direction == "short":
                    alpha = base_mean - mean
                t = alpha / (base_std / np.sqrt(n)) if base_std > 0 else None
                win = (sub[fwd] > 0 if direction == "long" else sub[fwd] < 0).mean() * 100
                rows.append({
                    "z_thresh": z, "hold": h, "direction": direction,
                    "n": n, "mean": round(mean, 2),
                    "alpha": round(alpha, 2),
                    "win_pct": round(win, 1),
                    "t_stat": round(t, 2) if t else None,
                })

    grid = pd.DataFrame(rows)
    print("\n  Top 5 long signals (basis z > +threshold = 期貨升水/contango):")
    print(grid[grid["direction"] == "long"].sort_values("alpha", ascending=False).head(5).to_string(index=False))
    print("\n  Top 5 short signals (basis z < -threshold = 期貨貼水/backwardation):")
    print(grid[grid["direction"] == "short"].sort_values("alpha", ascending=False).head(5).to_string(index=False))

    grid.to_csv(OUT_DIR / f"futures_basis_{datetime.now():%Y%m%d}.csv", index=False, encoding="utf-8-sig")
    return grid


# ─── Signal 6: 個股級行庫共識度 ───
def signal_6_govbank_consensus():
    print("\n" + "=" * 80)
    print("  ▶ Signal 6: 個股級行庫共識度（多少銀行同時 net buy）")
    print("=" * 80)

    print("  載入八大行庫 13M rows...")
    gb = pd.read_parquet(EXTRAS / "government_bank_buysell.parquet")
    gb["date"] = pd.to_datetime(gb["date"])
    gb["net"] = gb["buy_amount"] - gb["sell_amount"]
    gb["bought"] = (gb["net"] > 0).astype(int)
    print(f"  rows={len(gb):,}, dates={gb['date'].nunique()}, stocks={gb['stock_id'].nunique()}")

    # 計算每個 (date, stock) 多少銀行 net buy
    consensus = gb.groupby(["date", "stock_id"])["bought"].sum().reset_index()
    consensus.rename(columns={"bought": "n_banks_buy"}, inplace=True)
    print(f"  Consensus stats: mean={consensus['n_banks_buy'].mean():.2f}, "
          f"5+_pct={(consensus['n_banks_buy'] >= 5).mean()*100:.1f}%, "
          f"7+_pct={(consensus['n_banks_buy'] >= 7).mean()*100:.1f}%")

    # 對 5+ / 6+ / 7+ banks consensus 做 forward return analysis
    rows = []
    for thresh in [5, 6, 7, 8]:
        triggers = consensus[consensus["n_banks_buy"] >= thresh].copy()
        print(f"\n  Threshold ≥ {thresh} banks: {len(triggers):,} events")

        # 對每個 trigger 計算 forward return
        all_rets = {h: [] for h in HOLD_PERIODS}
        all_baseline = {h: [] for h in HOLD_PERIODS}

        # Group by ticker for efficiency
        for tk in triggers["stock_id"].unique()[:500]:  # 限制 500 檔避免太久
            tk_triggers = triggers[triggers["stock_id"] == tk]["date"].tolist()
            if not tk_triggers: continue

            # Load price
            p = TW_CACHE / f"{tk}.parquet"
            if not p.exists() or p.stat().st_size < 500: continue
            try:
                px = pd.read_parquet(p)
            except Exception:
                continue
            if px.empty or len(px) < 100: continue
            px["date"] = pd.to_datetime(px["date"])
            px_idx = px.set_index("date")["close"]

            # Forward returns for triggers
            for sig_d in tk_triggers:
                future = px_idx[px_idx.index > sig_d]
                if len(future) <= max(HOLD_PERIODS): continue
                entry = future.iloc[0]
                if entry <= 0: continue
                for h in HOLD_PERIODS:
                    if h < len(future):
                        all_rets[h].append((future.iloc[h] / entry - 1) * 100)

            # Baseline (random)
            if len(px_idx) < max(HOLD_PERIODS) + 60: continue
            rng = np.random.RandomState(hash(tk) % (2**32))
            n_base = min(20, len(px_idx) - max(HOLD_PERIODS) - 60)
            if n_base <= 0: continue
            base_idx = rng.choice(range(60, len(px_idx) - max(HOLD_PERIODS)),
                                  size=n_base, replace=False)
            for j in base_idx:
                if px_idx.iloc[j] > 0:
                    for h in HOLD_PERIODS:
                        all_baseline[h].append((px_idx.iloc[j + h] / px_idx.iloc[j] - 1) * 100)

        for h in HOLD_PERIODS:
            if len(all_rets[h]) < 100: continue
            n = len(all_rets[h])
            mean = np.mean(all_rets[h])
            base_mean = np.mean(all_baseline[h]) if all_baseline[h] else 0
            base_std = np.std(all_baseline[h]) if all_baseline[h] else 0
            alpha = mean - base_mean
            win = sum(1 for r in all_rets[h] if r > 0) / n * 100
            t = alpha / (base_std / np.sqrt(n)) if base_std > 0 else None
            rows.append({
                "threshold": thresh, "hold": h, "n": n,
                "mean": round(mean, 2), "baseline": round(base_mean, 2),
                "alpha": round(alpha, 2), "win_pct": round(win, 1),
                "t_stat": round(t, 2) if t else None,
            })
            t_str = f"{t:+.2f}" if t else "n/a"
            print(f"    hold={h}d: n={n}, alpha={alpha:+.2f}%, "
                  f"win={win:.1f}%, t={t_str}")

    grid = pd.DataFrame(rows)
    grid.to_csv(OUT_DIR / f"govbank_consensus_{datetime.now():%Y%m%d}.csv",
                index=False, encoding="utf-8-sig")
    return grid


def main():
    print("=" * 80)
    print("  中優先度訊號 backtest (4: 期貨基差, 6: 行庫共識度)")
    print("=" * 80)

    g4 = signal_4_futures_basis()
    g6 = signal_6_govbank_consensus()

    print("\n" + "=" * 80)
    print("  🎯 完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
