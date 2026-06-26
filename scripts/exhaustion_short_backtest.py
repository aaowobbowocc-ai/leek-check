"""
爆量退勢空 (Exhaustion Short) Backtest — 散戶最後進場後的反向操作。

策略邏輯：
  1. 09:00-11:30 內，找「先大漲 + 爆量 + 開始示弱」的 ticker
     - cumret_from_open >= pump_threshold (已漲一波)
     - cumvol >= prev_day_total * vol_ratio (爆量 → 散戶最後一波)
     - 當前價格已從 intraday high 回 retreat 區間（漲不動 → 確認退勢）
  2. 進場時點 = 第一次滿足上述所有條件的 minute
  3. 進場：short @ minute close
  4. 停損：intraday_high × (1 + stop_buffer)（防守前高，買回平倉）
  5. 停利：entry × (1 - take_profit)（反彈幅度達標出場）
  6. 兜底：13:20 強制平倉

成本：0.34% / 筆（手續費月退淨 + 當沖減半稅）

變體（3 × 2 × 2 × 2 × 3 = 72 組合）:
  pump_threshold: 1.5% / 2.0% / 3.0%
  vol_ratio:      30% / 50%（cum vol vs prev day total）
  retreat:        0.5% / 1.0%（從 high 回多少才算示弱）
  stop_buffer:    0% / 0.5%（停損 = high × (1+stop_buffer)）
  take_profit:    1.0% / 1.5% / 2.0%（向下達標）

驗收：
  Tier A — ticker × variant: OOS mean>0 AND CI low>0 AND test_n>=10
  Tier B — OOS mean>0 但 CI 跨 0
"""
from __future__ import annotations

import io
import sys
import time
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

CACHE = ROOT / "data" / "cache" / "finmind" / "minute"
COST = 0.34
CUTOFF = pd.Timestamp("2025-06-01")
SEED = 42
N_BOOT = 500

EXIT_TIME = "13:20"
SCAN_END = "11:30"      # 進場必須在此之前

# 變體
PUMP_THRESHOLDS = [0.015, 0.020, 0.030]
VOL_RATIOS = [0.30, 0.50]
RETREATS = [0.005, 0.010]
STOP_BUFFERS = [0.000, 0.005]
TAKE_PROFITS = [0.010, 0.015, 0.020]


def load_minute(ticker: str) -> pd.DataFrame:
    files = sorted(CACHE.glob(f"{ticker}_*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date_only"] = df["dt"].dt.date
    df["minute_str"] = df["dt"].dt.strftime("%H:%M")
    return df.sort_values("dt").reset_index(drop=True)


def build_intraday_features(df: pd.DataFrame, prev_day_total_vol: dict) -> pd.DataFrame:
    """
    對每筆 minute bar 計算：
      cum_ret, cum_vol, intraday_high_so_far, retreat_from_high,
      vol_ratio (cum_vol / prev_day_total)
    """
    out = []
    for d, sub in df.groupby("date_only"):
        sub = sub.sort_values("dt").reset_index(drop=True)
        sub = sub[sub["minute_str"] >= "09:00"].copy()
        if sub.empty:
            continue
        prev_total = prev_day_total_vol.get(d, 0)
        if prev_total <= 0:
            continue
        open_p = float(sub.iloc[0]["open"])
        sub["cum_ret"] = sub["close"].astype(float) / open_p - 1
        sub["cum_vol"] = sub["volume"].astype(float).cumsum()
        sub["intraday_high"] = sub["high"].astype(float).cummax()
        sub["retreat"] = sub["intraday_high"] / sub["close"].astype(float) - 1
        sub["vol_ratio"] = sub["cum_vol"] / prev_total
        sub["prev_day_vol"] = prev_total
        out.append(sub)
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


def simulate_short(
    ticker_data: pd.DataFrame,
    pump: float,
    vol_r: float,
    retreat: float,
    stop_buf: float,
    tp: float,
) -> pd.DataFrame:
    """單變體在 ticker 上的所有 short trades。"""
    rows = []
    for d, day_df in ticker_data.groupby("date_only"):
        day_df = day_df.sort_values("dt").reset_index(drop=True)
        # 進場條件 mask
        scan_mask = (
            (day_df["minute_str"] >= "09:05") &
            (day_df["minute_str"] <= SCAN_END) &
            (day_df["cum_ret"] >= pump) &
            (day_df["vol_ratio"] >= vol_r) &
            (day_df["retreat"] >= retreat) &
            (day_df["retreat"] <= retreat + 0.02)  # 不能回太多（已過了反彈點）
        )
        candidates = day_df[scan_mask]
        if candidates.empty:
            continue
        entry_row = candidates.iloc[0]
        entry_idx = entry_row.name
        entry_price = float(entry_row["close"])
        entry_min = str(entry_row["minute_str"])
        entry_high = float(entry_row["intraday_high"])

        # 出場掃描 — 雲端下單市價停損模型：觸發後用下一根 open 成交
        rest = day_df.iloc[entry_idx + 1:].reset_index(drop=True)
        rest = rest[rest["minute_str"] <= EXIT_TIME].reset_index(drop=True)

        stop_price = entry_high * (1 + stop_buf)
        target_price = entry_price * (1 - tp)

        exit_price = None
        exit_min = None
        exit_kind = None
        for i, b in rest.iterrows():
            high_b = float(b["high"])
            low_b = float(b["low"])
            # 短倉先看 stop（high 觸停損 = 觸發市價單）
            if high_b >= stop_price:
                # 雲端下單市價成交 = 下一根 open；最後一根則用本根 close
                if i + 1 < len(rest):
                    exit_price = float(rest.iloc[i + 1]["open"])
                    exit_min = str(rest.iloc[i + 1]["minute_str"])
                else:
                    exit_price = float(b["close"])
                    exit_min = str(b["minute_str"])
                exit_kind = "stop"
                break
            # 停利同樣用市價（用戶下單機制一致）
            if low_b <= target_price:
                if i + 1 < len(rest):
                    exit_price = float(rest.iloc[i + 1]["open"])
                    exit_min = str(rest.iloc[i + 1]["minute_str"])
                else:
                    exit_price = float(b["close"])
                    exit_min = str(b["minute_str"])
                exit_kind = "target"
                break
        if exit_price is None:
            # 13:20 強制平倉
            last = rest.iloc[-1] if not rest.empty else day_df.iloc[-1]
            exit_price = float(last["close"])
            exit_min = str(last["minute_str"])
            exit_kind = "time"

        # short pnl: (entry - exit) / entry
        gross = (entry_price - exit_price) / entry_price * 100
        net = gross - COST
        rows.append({
            "date": pd.Timestamp(d),
            "entry_min": entry_min,
            "entry_price": entry_price,
            "intraday_high_at_entry": entry_high,
            "stop_price": stop_price,
            "target_price": target_price,
            "exit_min": exit_min,
            "exit_price": exit_price,
            "exit_kind": exit_kind,
            "gross_pct": gross,
            "net_pct": net,
        })
    return pd.DataFrame(rows)


def stats_walk_forward(df_trades: pd.DataFrame) -> dict:
    n = len(df_trades)
    if n == 0:
        return {"n": 0, "full_mean": np.nan, "full_win": np.nan,
                "train_n": 0, "train_mean": np.nan,
                "test_n": 0, "test_mean": np.nan, "test_win": np.nan,
                "ci_low": np.nan, "ci_high": np.nan,
                "stop_pct": np.nan, "target_pct": np.nan, "time_pct": np.nan}
    rets = df_trades["net_pct"].values
    train = df_trades[df_trades["date"] < CUTOFF]
    test = df_trades[df_trades["date"] >= CUTOFF]
    rng = np.random.default_rng(SEED)
    if n >= 5:
        boot = np.array([rng.choice(rets, size=n, replace=True).mean() for _ in range(N_BOOT)])
        ci_low, ci_high = np.percentile(boot, [2.5, 97.5])
    else:
        ci_low, ci_high = np.nan, np.nan
    kinds = df_trades["exit_kind"].value_counts(normalize=True) * 100
    return {
        "n": n,
        "full_mean": rets.mean(),
        "full_win": (rets > 0).mean() * 100,
        "train_n": len(train),
        "train_mean": train["net_pct"].mean() if len(train) else np.nan,
        "test_n": len(test),
        "test_mean": test["net_pct"].mean() if len(test) else np.nan,
        "test_win": (test["net_pct"] > 0).mean() * 100 if len(test) else np.nan,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "stop_pct": kinds.get("stop", 0),
        "target_pct": kinds.get("target", 0),
        "time_pct": kinds.get("time", 0),
    }


def main() -> None:
    tickers = sorted({p.stem.split("_")[0] for p in CACHE.glob("*.parquet")})
    n_variants = (len(PUMP_THRESHOLDS) * len(VOL_RATIOS) * len(RETREATS)
                  * len(STOP_BUFFERS) * len(TAKE_PROFITS))
    print(f"=== Exhaustion Short Backtest | {len(tickers)} ticker × {n_variants} variants ===")

    # 載入 + 預計算 features
    print("\n[1/3] 載入 + 計算 intraday features...")
    t0 = time.time()
    feature_data: dict[str, pd.DataFrame] = {}
    for tk in tickers:
        df = load_minute(tk)
        if df.empty:
            continue
        # 先算 prev day total vol map
        daily_vol = df.groupby("date_only")["volume"].sum().sort_index()
        daily_vol_shift = daily_vol.shift(1).fillna(0)
        prev_map = daily_vol_shift.to_dict()
        feat = build_intraday_features(df, prev_map)
        if feat.empty:
            continue
        feature_data[tk] = feat
        print(f"  ✅ {tk} {lookup_ticker_name(tk)}: {feat['date_only'].nunique()} days")
    print(f"  載入 {time.time()-t0:.1f}s")

    # 跑變體
    print(f"\n[2/3] 跑 {len(feature_data)} ticker × {n_variants} variants...")
    rows = []
    variant_id = 0
    for pump in PUMP_THRESHOLDS:
        for vr in VOL_RATIOS:
            for rt in RETREATS:
                for sb in STOP_BUFFERS:
                    for tp in TAKE_PROFITS:
                        variant_id += 1
                        v_t0 = time.time()
                        v_n_passers = 0
                        for tk, fd in feature_data.items():
                            trades = simulate_short(fd, pump, vr, rt, sb, tp)
                            st = stats_walk_forward(trades)
                            if st["n"] >= 8:
                                rows.append({
                                    "ticker": tk,
                                    "name": lookup_ticker_name(tk),
                                    "pump_pct": pump * 100,
                                    "vol_ratio_pct": vr * 100,
                                    "retreat_pct": rt * 100,
                                    "stop_buf_pct": sb * 100,
                                    "tp_pct": tp * 100,
                                    **st,
                                })
                                if st["test_n"] >= 10 and st["test_mean"] > 0 and st["ci_low"] > 0:
                                    v_n_passers += 1
                        print(f"  [{variant_id:>2}/{n_variants}] pump={pump:.1%} vol={vr:.0%} "
                              f"retreat={rt:.1%} stopbuf={sb:.1%} tp={tp:.1%} "
                              f"Tier-A={v_n_passers} ({time.time()-v_t0:.1f}s)")

    if not rows:
        print("❌ 無結果"); return
    res = pd.DataFrame(rows)

    def tier(r):
        if r["test_n"] >= 10 and r["test_mean"] > 0 and r["ci_low"] > 0:
            return "A"
        elif r["test_n"] >= 5 and r["test_mean"] > 0:
            return "B"
        elif r["full_mean"] > 0 and r["full_win"] > 50:
            return "B-"
        return "C"
    res["tier"] = res.apply(tier, axis=1)

    out_csv = ROOT / "logs" / "exhaustion_short.csv"
    res.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[3/3] 寫入 {out_csv.relative_to(ROOT)} ({len(res)} rows)")

    for t in ["A", "B"]:
        sub = res[res["tier"] == t].sort_values("test_mean", ascending=False)
        if sub.empty:
            continue
        labels = {"A": "✅ Tier A", "B": "⚠️ Tier B"}
        print(f"\n{labels[t]} ({len(sub)})")
        print(f"  {'tk':<5} {'name':<8} {'pump':>5} {'vol':>5} {'rtrt':>5} "
              f"{'sbuf':>5} {'tp':>5} {'n':>4} {'OOS m/w':>14} {'kind% s/t/T':>12}")
        for _, r in sub.head(25).iterrows():
            print(f"  {r['ticker']:<5} {r['name'][:6]:<8} "
                  f"{r['pump_pct']:>4.1f}% {r['vol_ratio_pct']:>4.0f}% {r['retreat_pct']:>4.1f}% "
                  f"{r['stop_buf_pct']:>4.1f}% {r['tp_pct']:>4.1f}% "
                  f"{r['n']:>4} {r['test_mean']:>+5.2f}%/{r['test_win']:>4.0f}% "
                  f"{r['stop_pct']:>3.0f}/{r['target_pct']:>3.0f}/{r['time_pct']:>3.0f}")

    a_pairs = res[res["tier"] == "A"]["ticker"].unique()
    print(f"\n通過 Tier A 的 ticker: {len(a_pairs)}")
    for tk in a_pairs:
        best = res[(res["ticker"] == tk) & (res["tier"] == "A")].sort_values("test_mean", ascending=False).iloc[0]
        print(f"  {tk} {best['name']}: pump={best['pump_pct']:.1f}% vol={best['vol_ratio_pct']:.0f}% "
              f"retreat={best['retreat_pct']:.1f}% sbuf={best['stop_buf_pct']:.1f}% tp={best['tp_pct']:.1f}%, "
              f"OOS {best['test_mean']:+.2f}%/{best['test_win']:.0f}% (n={best['n']})")

    # Markdown
    md = ["# Exhaustion Short Backtest — Whitelist\n",
          f"Universe: {len(feature_data)} ticker × {n_variants} variants | Cost: {COST}% / 筆\n"]
    for t in ["A", "B"]:
        sub = res[res["tier"] == t].sort_values("test_mean", ascending=False)
        if sub.empty:
            continue
        md.append(f"\n## Tier {t} ({len(sub)})\n")
        md.append("| ticker | name | pump | vol | retreat | sbuf | tp | n | OOS mean/win | CI | exit kind% |")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for _, r in sub.head(60).iterrows():
            md.append(
                f"| {r['ticker']} | {r['name']} | {r['pump_pct']:.1f}% | {r['vol_ratio_pct']:.0f}% | "
                f"{r['retreat_pct']:.1f}% | {r['stop_buf_pct']:.1f}% | {r['tp_pct']:.1f}% | "
                f"{r['n']} | {r['test_mean']:+.2f}%/{r['test_win']:.0f}% (n={r['test_n']}) | "
                f"[{r['ci_low']:+.2f}, {r['ci_high']:+.2f}] | "
                f"s{r['stop_pct']:.0f}/t{r['target_pct']:.0f}/T{r['time_pct']:.0f} |"
            )
    out_md = ROOT / "logs" / "exhaustion_short_summary.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"\n寫入 {out_md.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
