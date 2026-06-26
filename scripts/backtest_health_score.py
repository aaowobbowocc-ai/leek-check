"""
韭菜健檢分數 — Backtest 驗證
================================

驗證 app/app.py:calc_composite_score 算出的 0-100 健檢分數
到底有沒有 alpha (vs 同期 0050 持有 baseline)。

方法:
1. 月度 rebalance:每月最後一個交易日,對 universe (流動性前 N 檔) 算分
2. 分組:HIGH (≥70) / MID (50-69) / LOW (<50)
3. Forward return:每組等權持有 20d / 60d / 120d
4. Baseline:同期間 0050 buy-and-hold

成本扣除:0.585% 來回 (個股 ETF 平均)
時間:2020-01 ~ 2026-06 (post-2020 alpha 視窗)
"""
from __future__ import annotations

import io
import sys
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
PX_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
INST_CACHE = ROOT / "data" / "cache" / "finmind" / "institutional"
FINMIND_CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"

COST = 0.585  # 個股來回成本 (參考 CLAUDE.md)
START_DATE = "2020-01-01"
END_DATE = "2026-06-01"
TOP_LIQUIDITY = 300       # universe 大小:成交量前 300 檔
HOLD_PERIODS = [20, 60, 120]


# ──────────────────────────────────────────────
# 資料載入
# ──────────────────────────────────────────────

@lru_cache(maxsize=3000)
def load_px(tk: str) -> pd.DataFrame:
    p = PX_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    # 預計算技術指標一次
    df = calc_tech_indicators(df)
    return df


def calc_tech_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """為 px DataFrame 加上 ma5/ma20/ma60/rsi14/k9/d9 欄。"""
    if df.empty or len(df) < 60:
        return df
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["vol20"] = df["volume"].rolling(20).mean()
    df["turnover20"] = (df["close"] * df["volume"]).rolling(20).mean()

    # RSI 14
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)

    # KD (KDJ stoch) — 9-day
    low9 = df["low"].rolling(9).min()
    high9 = df["high"].rolling(9).max()
    rsv = (df["close"] - low9) / (high9 - low9).replace(0, np.nan) * 100
    k = pd.Series(index=df.index, dtype=float)
    d = pd.Series(index=df.index, dtype=float)
    k.iloc[0] = 50.0
    d.iloc[0] = 50.0
    for i in range(1, len(df)):
        rsv_v = rsv.iloc[i] if not pd.isna(rsv.iloc[i]) else 50.0
        k.iloc[i] = (2 / 3) * k.iloc[i - 1] + (1 / 3) * rsv_v
        d.iloc[i] = (2 / 3) * d.iloc[i - 1] + (1 / 3) * k.iloc[i]
    df["k"] = k
    df["d"] = d
    return df


@lru_cache(maxsize=3000)
def load_inst_20d(tk: str) -> pd.DataFrame:
    """回傳每日 trailing 20d 外資+投信 net buy (張) DataFrame。"""
    p = INST_CACHE / f"{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    pivot = df.pivot_table(
        index="date", columns="name", values="buy",
        aggfunc="sum", fill_value=0,
    )
    sell = df.pivot_table(
        index="date", columns="name", values="sell",
        aggfunc="sum", fill_value=0,
    )
    net = pivot.sub(sell, fill_value=0)
    fi = net.get("Foreign_Investor", pd.Series(0, index=net.index))
    inv = net.get("Investment_Trust", pd.Series(0, index=net.index))
    daily = (fi + inv) / 1000.0  # 股 → 張
    rolled = daily.rolling(20).sum().reset_index()
    rolled.columns = ["date", "inst_20d"]
    return rolled


@lru_cache(maxsize=3000)
def load_per(tk: str) -> pd.DataFrame:
    p = FINMIND_CACHE / f"TaiwanStockPER_{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    keep_cols = ["date"]
    for c in ["PER", "dividend_yield"]:
        if c in df.columns:
            keep_cols.append(c)
    return df[keep_cols].sort_values("date").reset_index(drop=True)


@lru_cache(maxsize=3000)
def load_revenue_yoy(tk: str) -> pd.DataFrame:
    """月營收 YoY → daily forward-fill."""
    p = FINMIND_CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if df.empty or "revenue" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["rev_yoy"] = df["revenue"].pct_change(12) * 100
    # 公告 lag 10 天保守處理 (look-ahead bias 防止)
    df["effective_date"] = df["date"] + pd.Timedelta(days=10)
    return df[["effective_date", "rev_yoy"]].rename(columns={"effective_date": "date"})


# ──────────────────────────────────────────────
# 健檢分數 — 跟 app.py:calc_composite_score 對齊
# ──────────────────────────────────────────────

def calc_health_score(tech: dict, chip: dict, funda: dict) -> tuple[float, dict]:
    scores = {"tech": 50.0, "chip": 50.0, "funda": 50.0}

    # 技術 (40%)
    s = 50.0
    price = tech.get("price", 0)
    ma5 = tech.get("ma5", 0); ma20 = tech.get("ma20", 0); ma60 = tech.get("ma60", 0)
    if price and ma5 and ma20 and ma60:
        if price > ma5 > ma20 > ma60:
            s += 15
        elif price < ma60:
            s -= 15
    rsi = tech.get("rsi", 50)
    if 30 < rsi < 70: s += 5
    elif rsi > 80: s -= 10
    elif rsi < 20: s += 5
    k = tech.get("k", 50); d = tech.get("d", 50)
    if k > d and k < 80: s += 5
    elif k < d and k > 20: s -= 5
    scores["tech"] = max(0, min(100, s))

    # 籌碼 (30%)
    s = 50.0
    inst20 = chip.get("inst_20d", 0)
    if inst20 > 1000: s += 15
    elif inst20 > 0: s += 5
    elif inst20 < -1000: s -= 15
    elif inst20 < 0: s -= 5
    scores["chip"] = max(0, min(100, s))

    # 基本 (30%)
    s = 50.0
    per = funda.get("per", 30)
    if 0 < per < 15: s += 15
    elif per < 25: s += 5
    elif per > 40: s -= 10
    yoy = funda.get("rev_yoy", 0)
    if yoy > 20: s += 10
    elif yoy > 0: s += 5
    elif yoy < -10: s -= 10
    yld = funda.get("yield", 0)
    if yld > 4: s += 5
    scores["funda"] = max(0, min(100, s))

    composite = scores["tech"] * 0.4 + scores["chip"] * 0.3 + scores["funda"] * 0.3
    return composite, scores


# ──────────────────────────────────────────────
# Universe + rebalance
# ──────────────────────────────────────────────

def get_universe() -> list[str]:
    """所有有 px + inst + per + revenue cache 的個股。"""
    px_files = {p.stem for p in PX_CACHE.glob("*.parquet")}
    inst_files = {p.stem for p in INST_CACHE.glob("*.parquet")}
    per_files = {p.stem.replace("TaiwanStockPER_", "")
                 for p in FINMIND_CACHE.glob("TaiwanStockPER_*.parquet")}
    rev_files = {p.stem.replace("TaiwanStockMonthRevenue_", "")
                 for p in FINMIND_CACHE.glob("TaiwanStockMonthRevenue_*.parquet")}
    common = px_files & inst_files & per_files & rev_files
    # 排除 ETF (0開頭多半是 ETF)
    common = {t for t in common if not t.startswith("00")}
    return sorted(common)


def rebalance_dates(start: str, end: str) -> list[pd.Timestamp]:
    """每月最後一個交易日 (用 0050 日曆代理)."""
    px = load_px("0050")
    if px.empty:
        return []
    px = px[(px["date"] >= start) & (px["date"] <= end)].copy()
    px["ym"] = px["date"].dt.to_period("M")
    last_dates = px.groupby("ym")["date"].max().tolist()
    return last_dates


def score_universe_at(date: pd.Timestamp, universe: list[str], n_top: int) -> pd.DataFrame:
    """在指定日對全 universe 計分,回前 n_top liquidity 的。"""
    rows = []
    for tk in universe:
        px = load_px(tk)
        if px.empty or len(px) < 80:
            continue
        # 截至 date 為止 (look-ahead bias 防護)
        sub = px[px["date"] <= date]
        if len(sub) < 80:
            continue
        last = sub.iloc[-1]
        if pd.isna(last.get("ma60")):
            continue
        turnover20 = last.get("turnover20", 0)
        if pd.isna(turnover20) or turnover20 <= 0:
            continue

        # 籌碼
        inst_df = load_inst_20d(tk)
        if inst_df.empty:
            continue
        inst_sub = inst_df[inst_df["date"] <= date]
        inst20 = float(inst_sub["inst_20d"].iloc[-1]) if not inst_sub.empty else 0.0

        # 基本面
        per_df = load_per(tk)
        per_v = 0.0; yld_v = 0.0
        if not per_df.empty:
            per_sub = per_df[per_df["date"] <= date]
            if not per_sub.empty:
                last_per = per_sub.iloc[-1]
                per_v = float(last_per.get("PER", 0) or 0)
                if "dividend_yield" in per_sub.columns:
                    yld_v = float(last_per.get("dividend_yield", 0) or 0)

        rev_df = load_revenue_yoy(tk)
        yoy_v = 0.0
        if not rev_df.empty:
            rev_sub = rev_df[rev_df["date"] <= date]
            if not rev_sub.empty:
                yoy_raw = rev_sub.iloc[-1]["rev_yoy"]
                if pd.notna(yoy_raw) and np.isfinite(yoy_raw):
                    yoy_v = float(yoy_raw)

        tech = {
            "price": float(last["close"]),
            "ma5": float(last["ma5"]), "ma20": float(last["ma20"]), "ma60": float(last["ma60"]),
            "rsi": float(last.get("rsi", 50) or 50),
            "k": float(last.get("k", 50) or 50), "d": float(last.get("d", 50) or 50),
        }
        chip = {"inst_20d": inst20}
        funda = {"per": per_v, "yield": yld_v, "rev_yoy": yoy_v}

        comp, sub_s = calc_health_score(tech, chip, funda)

        rows.append({
            "ticker": tk,
            "date": date,
            "score": comp,
            "tech_s": sub_s["tech"], "chip_s": sub_s["chip"], "funda_s": sub_s["funda"],
            "turnover20": turnover20,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("turnover20", ascending=False).head(n_top)
    return df.reset_index(drop=True)


def forward_return(tk: str, entry_date: pd.Timestamp, hold_days: int) -> float | None:
    """從 entry_date 隔日 open 進場,持有 hold_days 個交易日 close 出場。"""
    px = load_px(tk)
    if px.empty:
        return None
    idx = px.index[px["date"] >= entry_date]
    if len(idx) < hold_days + 2:
        return None
    entry_i = idx[0] + 1  # next-day open
    exit_i = entry_i + hold_days
    if exit_i >= len(px):
        return None
    entry = float(px.iloc[entry_i]["open"])
    exit_p = float(px.iloc[exit_i]["close"])
    if entry <= 0:
        return None
    return (exit_p / entry - 1) * 100 - COST


def benchmark_return(entry_date: pd.Timestamp, hold_days: int) -> float | None:
    """0050 同期持有 — baseline。"""
    return forward_return("0050", entry_date, hold_days)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    print("=" * 72)
    print("健檢分數 alpha 驗證 backtest")
    print(f"  Universe:成交量前 {TOP_LIQUIDITY} 檔個股 (排除 00 開頭 ETF)")
    print(f"  期間:{START_DATE} ~ {END_DATE}")
    print(f"  Hold:{HOLD_PERIODS} 個交易日")
    print(f"  成本:{COST}%/筆 (來回)")
    print("=" * 72)

    universe = get_universe()
    print(f"\n[1/3] Universe size (有完整 cache):{len(universe)} 檔")

    dates = rebalance_dates(START_DATE, END_DATE)
    print(f"[2/3] Rebalance 日期:{len(dates)} 個月")

    all_results = []
    for i, d in enumerate(dates):
        print(f"  [{i+1}/{len(dates)}] {d.strftime('%Y-%m-%d')} 計分中...", flush=True)
        scored = score_universe_at(d, universe, TOP_LIQUIDITY)
        if scored.empty:
            continue
        # 分組
        scored["bucket"] = pd.cut(
            scored["score"],
            bins=[-1, 50, 70, 101],
            labels=["LOW(<50)", "MID(50-69)", "HIGH(>=70)"],
        )

        for hold in HOLD_PERIODS:
            for tk in scored["ticker"]:
                ret = forward_return(tk, d, hold)
                if ret is None or not np.isfinite(ret):
                    continue
                row = scored[scored["ticker"] == tk].iloc[0]
                all_results.append({
                    "date": d, "ticker": tk, "score": row["score"],
                    "bucket": str(row["bucket"]),
                    "hold": hold, "ret": ret,
                })

        # benchmark
        for hold in HOLD_PERIODS:
            bench = benchmark_return(d, hold)
            if bench is not None and np.isfinite(bench):
                all_results.append({
                    "date": d, "ticker": "0050", "score": np.nan,
                    "bucket": "0050_BENCHMARK", "hold": hold, "ret": bench,
                })

    if not all_results:
        print("\n[X] 無有效結果")
        return

    df = pd.DataFrame(all_results)

    print(f"\n[3/3] 收集 events:{len(df)} 筆")
    print("\n" + "=" * 72)
    print(" 分組績效 (mean ± std, n, win%, alpha vs 0050)")
    print("=" * 72)

    out_lines = []
    for hold in HOLD_PERIODS:
        sub = df[df["hold"] == hold]
        bench_ret = sub[sub["bucket"] == "0050_BENCHMARK"]["ret"].mean()
        print(f"\nHold {hold} 日 (0050 baseline mean: {bench_ret:+.2f}%)")
        print(f"  {'Bucket':<20} {'mean':>8} {'std':>8} {'n':>6} {'win%':>7} {'alpha':>9}")
        out_lines.append(f"\n# Hold {hold} 日")
        out_lines.append(f"# 0050 baseline: {bench_ret:+.2f}%")
        out_lines.append("bucket,mean,std,n,win_pct,alpha_vs_0050")

        for bucket in ["LOW(<50)", "MID(50-69)", "HIGH(>=70)"]:
            seg = sub[sub["bucket"] == bucket]
            if seg.empty:
                continue
            m = seg["ret"].mean()
            s = seg["ret"].std()
            n = len(seg)
            win = (seg["ret"] > 0).mean() * 100
            alpha = m - bench_ret
            print(f"  {bucket:<20} {m:+8.2f}% {s:8.2f} {n:6} {win:6.1f}% {alpha:+8.2f}pp")
            out_lines.append(f"{bucket},{m:.4f},{s:.4f},{n},{win:.2f},{alpha:.4f}")

    # 月度 portfolio (HIGH 組等權持有,看實際 CAGR)
    print("\n" + "=" * 72)
    print(" 月度 portfolio 模擬:每月 HIGH 組等權,60d hold")
    print("=" * 72)
    p60 = df[(df["hold"] == 60) & (df["bucket"] == "HIGH(>=70)")]
    if not p60.empty:
        monthly = p60.groupby("date")["ret"].agg(["mean", "count"]).reset_index()
        monthly.columns = ["date", "port_ret", "n_holdings"]
        # 算簡單累積 (假設每月再投入)
        monthly["cum"] = (1 + monthly["port_ret"] / 100).cumprod() - 1
        bench_m = df[(df["hold"] == 60) & (df["bucket"] == "0050_BENCHMARK")][["date", "ret"]]
        bench_m.columns = ["date", "bench_ret"]
        bench_m["bench_cum"] = (1 + bench_m["bench_ret"] / 100).cumprod() - 1
        merged = monthly.merge(bench_m, on="date", how="inner")
        if not merged.empty:
            n_months = len(merged)
            port_final = merged["cum"].iloc[-1]
            bench_final = merged["bench_cum"].iloc[-1]
            years = n_months / 12
            port_cagr = ((1 + port_final) ** (1 / max(years, 0.1)) - 1) * 100 if port_final > -1 else float("nan")
            bench_cagr = ((1 + bench_final) ** (1 / max(years, 0.1)) - 1) * 100 if bench_final > -1 else float("nan")
            print(f"  期間:{n_months} 個月 ({years:.2f} 年)")
            print(f"  HIGH 組 portfolio:累積 {port_final*100:+.1f}% / CAGR {port_cagr:+.2f}%")
            print(f"  0050 baseline:    累積 {bench_final*100:+.1f}% / CAGR {bench_cagr:+.2f}%")
            print(f"  Alpha:            {(port_cagr - bench_cagr):+.2f} pp/yr")

    # 存 CSV
    out_path = ROOT / "data" / "backtest" / "health_score_backtest.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n[OK] events 寫入 {out_path}")

    out_summary = ROOT / "data" / "backtest" / "health_score_summary.txt"
    out_summary.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"[OK] summary 寫入 {out_summary}")


if __name__ == "__main__":
    main()
