"""
Exhaustion Short — 停損機制比較。

固定進場規則（取上次 sweep 表現較好的中庸組合）:
  pump 2.0% / vol 30% / retreat 0.5% / take_profit 2.0%

比較 5 種停損機制:
  A. limit_strict  — 限價單 = 前高（無緩衝），平淡時 0 滑價，急漲時可能綁死
  B. limit_buf     — 限價單 = 前高 + 0.5% buffer，多 0.5% 滑價但成交機率高
  C. market        — 市價單（v2），下根 open 成交，保證出場
  D. hybrid        — 1 分內限價 = 前高+0.3%，未成交 → 升級到 +1%；3 分後仍未成 → 市價
  E. velocity      — D 同邏輯，加上「短倉開倉後 5 分內 cum ret > +1.5% 直接砍」（搶在前高之前先撤）

額外指標:
  - fill_rate（限價類）: % 觸發後成功在預設限價成交
  - worst_loss: 單筆最大虧損
  - limitup_count: 觸到漲停 (+9% 以上) 的次數（被嘎空風險）

驗收:
  哪個 mode 在 win / mean / worst_loss / fill_rate 綜合最佳
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

# 固定進場規則（用 v1 best variant: pump 3% 確認真實停損下 alpha 是否還在）
PUMP = 0.030
VOL_R = 0.30
RETREAT = 0.005
TP = 0.020
SCAN_END = "11:30"
EXIT_TIME = "13:20"

STOP_BUF_STRICT = 0.000
STOP_BUF_BUFFER = 0.005
STOP_BUF_HYBRID_INITIAL = 0.003
STOP_BUF_HYBRID_ESCALATE = 0.010
VELOCITY_BAIL_RET = 0.015     # entry 後 5 分內 cum ret > +1.5%（短倉看漲）→ pre-cut
VELOCITY_BARS = 5             # 看 5 分鐘


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


def build_features(df: pd.DataFrame, prev_map: dict) -> pd.DataFrame:
    out = []
    for d, sub in df.groupby("date_only"):
        sub = sub.sort_values("dt").reset_index(drop=True)
        sub = sub[sub["minute_str"] >= "09:00"].copy()
        if sub.empty:
            continue
        prev_total = prev_map.get(d, 0)
        if prev_total <= 0:
            continue
        open_p = float(sub.iloc[0]["open"])
        sub["cum_ret"] = sub["close"].astype(float) / open_p - 1
        sub["cum_vol"] = sub["volume"].astype(float).cumsum()
        sub["intraday_high"] = sub["high"].astype(float).cummax()
        sub["retreat"] = sub["intraday_high"] / sub["close"].astype(float) - 1
        sub["vol_ratio"] = sub["cum_vol"] / prev_total
        out.append(sub)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def find_entries(day_df: pd.DataFrame) -> int | None:
    """回傳第一次滿足進場條件的 row index（在 day_df 內）。"""
    mask = (
        (day_df["minute_str"] >= "09:05") &
        (day_df["minute_str"] <= SCAN_END) &
        (day_df["cum_ret"] >= PUMP) &
        (day_df["vol_ratio"] >= VOL_R) &
        (day_df["retreat"] >= RETREAT) &
        (day_df["retreat"] <= RETREAT + 0.02)
    )
    cands = day_df[mask]
    if cands.empty:
        return None
    return int(cands.index[0])


def exit_limit_strict(rest: pd.DataFrame, stop_price: float, target_price: float):
    """限價 strict: stop = 前高（無 buffer），target = entry × (1-tp)"""
    fill_kind = None  # stop/target/time/no_fill
    fill_min = None
    fill_price = None
    triggered_stop = False
    triggered_target = False
    for i, b in rest.iterrows():
        high_b = float(b["high"])
        low_b = float(b["low"])
        # short stop: high 觸到 stop_price = 觸發
        if not triggered_stop and high_b >= stop_price:
            triggered_stop = True
        # 觸發後若 bar 區間覆蓋 stop_price → 限價可成交
        if triggered_stop:
            if low_b <= stop_price <= high_b:
                fill_kind = "stop"
                fill_min = str(b["minute_str"])
                fill_price = stop_price
                return fill_kind, fill_min, fill_price
            # 若 low > stop_price → 賣盤已被掃光，本根沒成交
        # target 觸發
        if not triggered_target and low_b <= target_price:
            triggered_target = True
        if triggered_target:
            if low_b <= target_price <= high_b:
                fill_kind = "target"
                fill_min = str(b["minute_str"])
                fill_price = target_price
                return fill_kind, fill_min, fill_price
    # 沒成交（綁死）→ 13:20 強制市價
    if not rest.empty:
        last = rest.iloc[-1]
        return "no_fill_forced", str(last["minute_str"]), float(last["close"])
    return "time", None, None


def exit_limit_buf(rest: pd.DataFrame, stop_price_base: float, target_price: float, buf: float):
    """限價 with buffer: stop = 前高 × (1+buf)"""
    stop_limit = stop_price_base * (1 + buf)
    fill_kind = None
    for i, b in rest.iterrows():
        high_b = float(b["high"])
        low_b = float(b["low"])
        # 短倉 stop 觸發：當前高被穿過（用 base 為 trigger）
        if high_b >= stop_price_base:
            # 若本根區間覆蓋 stop_limit → 限價成交
            if low_b <= stop_limit <= high_b:
                return "stop", str(b["minute_str"]), stop_limit
            # 若 low > stop_limit (市場已過 buffer) → 沒成交本根
        if low_b <= target_price <= high_b:
            return "target", str(b["minute_str"]), target_price
    # 沒成交 → forced
    if not rest.empty:
        last = rest.iloc[-1]
        return "no_fill_forced", str(last["minute_str"]), float(last["close"])
    return "time", None, None


def exit_market(rest: pd.DataFrame, stop_price: float, target_price: float):
    """市價：觸發後下一根 open 成交"""
    rest = rest.reset_index(drop=True)
    for i, b in rest.iterrows():
        high_b = float(b["high"])
        low_b = float(b["low"])
        if high_b >= stop_price:
            if i + 1 < len(rest):
                return "stop", str(rest.iloc[i + 1]["minute_str"]), float(rest.iloc[i + 1]["open"])
            return "stop", str(b["minute_str"]), float(b["close"])
        if low_b <= target_price:
            if i + 1 < len(rest):
                return "target", str(rest.iloc[i + 1]["minute_str"]), float(rest.iloc[i + 1]["open"])
            return "target", str(b["minute_str"]), float(b["close"])
    if not rest.empty:
        last = rest.iloc[-1]
        return "time", str(last["minute_str"]), float(last["close"])
    return "time", None, None


def exit_hybrid(rest: pd.DataFrame, stop_price_base: float, target_price: float):
    """
    Hybrid: 觸發後 1min 內限價 = base+0.3%
            未成 → 升級到 base+1.0%（再等 2min）
            未成 → 市價（下一根 open）
    """
    rest = rest.reset_index(drop=True)
    initial_limit = stop_price_base * (1 + STOP_BUF_HYBRID_INITIAL)
    escalate_limit = stop_price_base * (1 + STOP_BUF_HYBRID_ESCALATE)
    triggered_at = None
    for i, b in rest.iterrows():
        high_b = float(b["high"])
        low_b = float(b["low"])
        # target 一律限價（target 用 entry × (1-tp)，price 較不會跳過下方）
        if low_b <= target_price <= high_b:
            return "target", str(b["minute_str"]), target_price
        if triggered_at is None and high_b >= stop_price_base:
            triggered_at = i
        if triggered_at is not None:
            elapsed = i - triggered_at
            if elapsed == 0 or elapsed == 1:
                # 1 分內試 initial_limit
                if low_b <= initial_limit <= high_b:
                    return "stop_limit_initial", str(b["minute_str"]), initial_limit
            elif elapsed == 2 or elapsed == 3:
                # 升級
                if low_b <= escalate_limit <= high_b:
                    return "stop_limit_escalate", str(b["minute_str"]), escalate_limit
            else:
                # 市價 (用本根 close 模擬下一根 open)
                if i + 1 < len(rest):
                    return "stop_market", str(rest.iloc[i + 1]["minute_str"]), float(rest.iloc[i + 1]["open"])
                return "stop_market", str(b["minute_str"]), float(b["close"])
    if not rest.empty:
        last = rest.iloc[-1]
        return "time", str(last["minute_str"]), float(last["close"])
    return "time", None, None


def exit_velocity(rest: pd.DataFrame, entry_price: float, stop_price_base: float,
                  target_price: float):
    """
    Velocity pre-cut: entry 後 VELOCITY_BARS 分內 cum ret (price/entry-1) > VELOCITY_BAIL_RET → 直接市價
    其餘同 hybrid。
    """
    rest = rest.reset_index(drop=True)
    initial_limit = stop_price_base * (1 + STOP_BUF_HYBRID_INITIAL)
    escalate_limit = stop_price_base * (1 + STOP_BUF_HYBRID_ESCALATE)
    triggered_at = None
    for i, b in rest.iterrows():
        high_b = float(b["high"])
        low_b = float(b["low"])
        close_b = float(b["close"])
        # ── velocity pre-cut: entry 後前 N 分內，價格漲超過閾值就直接砍
        if i < VELOCITY_BARS:
            up_pct = close_b / entry_price - 1
            if up_pct > VELOCITY_BAIL_RET:
                # 市價砍 → 下一根 open
                if i + 1 < len(rest):
                    return "velocity_cut", str(rest.iloc[i + 1]["minute_str"]), float(rest.iloc[i + 1]["open"])
                return "velocity_cut", str(b["minute_str"]), close_b
        # target
        if low_b <= target_price <= high_b:
            return "target", str(b["minute_str"]), target_price
        # stop hybrid
        if triggered_at is None and high_b >= stop_price_base:
            triggered_at = i
        if triggered_at is not None:
            elapsed = i - triggered_at
            if elapsed == 0 or elapsed == 1:
                if low_b <= initial_limit <= high_b:
                    return "stop_limit_initial", str(b["minute_str"]), initial_limit
            elif elapsed == 2 or elapsed == 3:
                if low_b <= escalate_limit <= high_b:
                    return "stop_limit_escalate", str(b["minute_str"]), escalate_limit
            else:
                if i + 1 < len(rest):
                    return "stop_market", str(rest.iloc[i + 1]["minute_str"]), float(rest.iloc[i + 1]["open"])
                return "stop_market", str(b["minute_str"]), close_b
    if not rest.empty:
        last = rest.iloc[-1]
        return "time", str(last["minute_str"]), float(last["close"])
    return "time", None, None


def simulate_ticker(ticker_data: pd.DataFrame, mode: str) -> pd.DataFrame:
    rows = []
    for d, day_df in ticker_data.groupby("date_only"):
        day_df = day_df.sort_values("dt").reset_index(drop=True)
        idx = find_entries(day_df)
        if idx is None:
            continue
        e = day_df.iloc[idx]
        entry_price = float(e["close"])
        entry_high = float(e["intraday_high"])
        rest = day_df.iloc[idx + 1:].reset_index(drop=True)
        rest = rest[rest["minute_str"] <= EXIT_TIME].reset_index(drop=True)
        if rest.empty:
            continue

        target_price = entry_price * (1 - TP)

        if mode == "A_limit_strict":
            kind, exit_min, exit_price = exit_limit_strict(
                rest, entry_high, target_price)
        elif mode == "B_limit_buf":
            kind, exit_min, exit_price = exit_limit_buf(
                rest, entry_high, target_price, STOP_BUF_BUFFER)
        elif mode == "C_market":
            kind, exit_min, exit_price = exit_market(
                rest, entry_high, target_price)
        elif mode == "D_hybrid":
            kind, exit_min, exit_price = exit_hybrid(
                rest, entry_high, target_price)
        elif mode == "E_velocity":
            kind, exit_min, exit_price = exit_velocity(
                rest, entry_price, entry_high, target_price)
        else:
            continue

        if exit_price is None:
            continue
        gross = (entry_price - exit_price) / entry_price * 100
        net = gross - COST
        # 漲停判定：exit_price > entry_price × 1.09 (粗略 9% 以上)
        is_limitup = exit_price >= entry_price * 1.09
        rows.append({
            "date": pd.Timestamp(d),
            "entry_min": str(e["minute_str"]),
            "entry_price": entry_price,
            "intraday_high": entry_high,
            "exit_kind": kind,
            "exit_min": exit_min,
            "exit_price": exit_price,
            "gross_pct": gross,
            "net_pct": net,
            "is_limitup_squeeze": is_limitup,
        })
    return pd.DataFrame(rows)


def stats_for_mode(df_trades: pd.DataFrame) -> dict:
    n = len(df_trades)
    if n == 0:
        return {"n": 0, "mean": np.nan, "win": np.nan, "worst": np.nan,
                "ci_low": np.nan, "ci_high": np.nan, "fill_rate": np.nan,
                "limitup_count": 0, "test_n": 0, "test_mean": np.nan, "test_win": np.nan,
                "kind_breakdown": ""}
    rets = df_trades["net_pct"].values
    test = df_trades[df_trades["date"] >= CUTOFF]

    rng = np.random.default_rng(SEED)
    boot = np.array([rng.choice(rets, size=n, replace=True).mean() for _ in range(N_BOOT)])
    ci_low, ci_high = np.percentile(boot, [2.5, 97.5])

    # fill rate: 對於有限價的 mode，no_fill_forced 算 fill 失敗
    no_fill = (df_trades["exit_kind"] == "no_fill_forced").sum()
    velocity_cut = (df_trades["exit_kind"] == "velocity_cut").sum()
    fill_rate = (n - no_fill) / n * 100

    kinds = df_trades["exit_kind"].value_counts(normalize=True) * 100
    kind_breakdown = " ".join(f"{k}={v:.0f}%" for k, v in kinds.items())

    return {
        "n": n,
        "mean": rets.mean(),
        "win": (rets > 0).mean() * 100,
        "worst": rets.min(),
        "ci_low": ci_low, "ci_high": ci_high,
        "fill_rate": fill_rate,
        "limitup_count": int(df_trades["is_limitup_squeeze"].sum()),
        "no_fill_count": int(no_fill),
        "velocity_cut_count": int(velocity_cut),
        "test_n": len(test),
        "test_mean": test["net_pct"].mean() if len(test) else np.nan,
        "test_win": (test["net_pct"] > 0).mean() * 100 if len(test) else np.nan,
        "kind_breakdown": kind_breakdown,
    }


def main() -> None:
    tickers = sorted({p.stem.split("_")[0] for p in CACHE.glob("*.parquet")})
    print(f"=== 停損機制比較 | {len(tickers)} ticker × 5 modes ===")
    print(f"進場規則: pump>={PUMP:.0%} vol>={VOL_R:.0%} retreat>={RETREAT:.1%} tp={TP:.0%}")

    print("\n[1/3] 載入 + 計算 features...")
    t0 = time.time()
    feat_data: dict[str, pd.DataFrame] = {}
    for tk in tickers:
        df = load_minute(tk)
        if df.empty:
            continue
        daily_vol = df.groupby("date_only")["volume"].sum().sort_index().shift(1).fillna(0)
        prev_map = daily_vol.to_dict()
        feat = build_features(df, prev_map)
        if not feat.empty:
            feat_data[tk] = feat
    print(f"  {len(feat_data)} ticker 載入 {time.time()-t0:.1f}s")

    modes = ["A_limit_strict", "B_limit_buf", "C_market", "D_hybrid", "E_velocity"]
    print(f"\n[2/3] 跑 {len(feat_data)} ticker × {len(modes)} modes...")
    rows = []
    for mode in modes:
        t1 = time.time()
        for tk, fd in feat_data.items():
            trades = simulate_ticker(fd, mode)
            st = stats_for_mode(trades)
            if st["n"] >= 8:
                rows.append({"ticker": tk, "name": lookup_ticker_name(tk),
                             "mode": mode, **st})
        print(f"  ✅ {mode} ({time.time()-t1:.1f}s)")

    res = pd.DataFrame(rows)
    out_csv = ROOT / "logs" / "exhaustion_stop_modes.csv"
    res.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[3/3] 寫入 {out_csv.relative_to(ROOT)} ({len(res)} rows)")

    # ── Summary 1: 每 mode aggregate ──
    print("\n" + "=" * 90)
    print("各 mode 跨全 ticker 平均（aggregate）")
    print("=" * 90)
    agg = res.groupby("mode").agg(
        n_tickers=("ticker", "count"),
        avg_n=("n", "mean"),
        avg_mean=("mean", "mean"),
        avg_win=("win", "mean"),
        avg_worst=("worst", "mean"),
        avg_fill=("fill_rate", "mean"),
        total_limitup=("limitup_count", "sum"),
        total_no_fill=("no_fill_count", "sum"),
    ).round(2)
    print(agg.to_string())

    # ── Summary 2: 各 mode 下 best ticker ──
    print("\n" + "=" * 90)
    print("各 mode 下 OOS test_mean 最佳的 5 ticker")
    print("=" * 90)
    for mode in modes:
        sub = res[res["mode"] == mode].sort_values("test_mean", ascending=False).head(5)
        if sub.empty:
            continue
        print(f"\n  [{mode}]")
        for _, r in sub.iterrows():
            print(f"    {r['ticker']} {r['name'][:6]:<6}: "
                  f"n={r['n']:>3} OOS {r['test_mean']:>+5.2f}%/{r['test_win']:>3.0f}% "
                  f"(test n={r['test_n']:>2}) worst {r['worst']:>+6.2f}% "
                  f"CI [{r['ci_low']:>+5.2f},{r['ci_high']:>+5.2f}] "
                  f"fill {r['fill_rate']:>5.1f}% limitup={r['limitup_count']}")

    # ── Summary 3: 看 3231 / 2376 / 6125 等 cluster ticker 各 mode 表現 ──
    for tk in ["3231", "2376", "6125", "3017", "2409", "2615"]:
        print("\n" + "=" * 90)
        print(f"{tk} {lookup_ticker_name(tk)} 在各 mode 對比")
        print("=" * 90)
        sub = res[res["ticker"] == tk].sort_values("mode")
        if sub.empty:
            print("  (無資料 — 該 ticker 可能 n<8)")
            continue
        print(f"  {'mode':<18} {'n':>4} {'mean':>7} {'win':>5} "
              f"{'worst':>7} {'OOS m/w':>14} {'CI':>17} {'fill':>6} {'lu':>3}")
        for _, r in sub.iterrows():
            print(f"  {r['mode']:<18} {r['n']:>4} {r['mean']:>+6.2f}% {r['win']:>4.0f}% "
                  f"{r['worst']:>+6.2f}% {r['test_mean']:>+5.2f}%/{r['test_win']:>3.0f}% "
                  f"[{r['ci_low']:>+5.2f},{r['ci_high']:>+5.2f}] "
                  f"{r['fill_rate']:>5.1f}% {r['limitup_count']:>3}")

    # ── Markdown ──
    md = ["# Exhaustion Short — 停損機制對比\n",
          f"進場規則: pump>={PUMP:.0%} vol>={VOL_R:.0%} retreat>={RETREAT:.1%} tp={TP:.0%}",
          f"成本: {COST}% / 筆\n",
          "## 各 mode aggregate\n",
          agg.to_markdown(),
          "\n## 3231 緯創 各 mode 對比\n"]
    sub_3231 = res[res["ticker"] == "3231"].sort_values("mode")
    if not sub_3231.empty:
        cols = ["mode", "n", "mean", "win", "worst", "test_mean", "test_win",
                "ci_low", "ci_high", "fill_rate", "limitup_count"]
        md.append(sub_3231[cols].to_markdown(index=False, floatfmt=".2f"))
    out_md = ROOT / "logs" / "exhaustion_stop_modes_summary.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"\n寫入 {out_md.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
