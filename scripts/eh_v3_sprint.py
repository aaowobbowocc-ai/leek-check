"""
Early Hunter v3 Sprint — 6 個 filter 逐個堆疊測試。

每個 filter 是 (trades_df) → trades_df_filtered/modified
跑同一份 V2 portfolio 對比 baseline，CAGR 退化就退回該 filter。

Baseline (Trailing -25pp): CAGR 29.15%, alpha vs 0050 +2.17pp
驗收：每加一層 filter，CAGR ≥ 上輪 + 0.3pp 才保留。

依序測試：
  #1 大戶持股 pre-filter
  #2 60 天早砍 + 量能衰減 (修改 exit)
  #3 族群 RS gate
  #4 Conviction 加權
  #6 進場日 dynamic
  #8 ATR-based size
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

from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_FM = ROOT / "data" / "cache" / "finmind" / "finmind"
INPUT_TRAILING = ROOT / "logs" / "early_hunter_trailing_v2.csv"
INPUT_RAW = ROOT / "logs" / "early_hunter_20260425_160432.csv"

INITIAL = 100_000.0
PER_TRADE_PCT = 0.10
COST_PCT = 0.004


# ──────────────────────────────────────────────
# V2 Portfolio (固定)
# ──────────────────────────────────────────────
def nearest_price(prices: dict, target: date) -> float | None:
    for i in range(7):
        d = target - timedelta(days=i)
        if d in prices:
            return prices[d]
    return None


def run_v2_portfolio(
    trades: pd.DataFrame,
    core_prices: dict[date, float],
    start_date: date,
    end_date: date,
    use_size_col: bool = False,
) -> dict:
    """trades 必須有: ticker, entry_date, exit_date, gross_return_pct
    若 use_size_col=True，使用 'size_pct' 欄位作為單筆 % 倉位。"""
    init_price = nearest_price(core_prices, start_date)
    if init_price is None:
        return {"error": "no init price"}

    shares_core = INITIAL / init_price
    open_positions = []
    closed = []
    skipped = 0

    trades = trades.sort_values("entry_date").reset_index(drop=True)
    all_events = sorted(set(trades["entry_date"]) | set(trades["exit_date"]))

    for d in all_events:
        cur_core = nearest_price(core_prices, d)
        if cur_core is None:
            continue

        still_open = []
        for pos in open_positions:
            if pos["exit_date"] <= d:
                exit_amount = pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST_PCT)
                shares_core += exit_amount / cur_core
                closed.append(pos)
            else:
                still_open.append(pos)
        open_positions = still_open

        for _, t in trades.iterrows():
            if t["entry_date"] != d:
                continue
            size_pct = float(t["size_pct"]) if use_size_col else PER_TRADE_PCT
            allocation = INITIAL * size_pct
            if shares_core * cur_core >= allocation:
                shares_core -= allocation / cur_core
                open_positions.append({
                    "ticker": t["ticker"],
                    "entry_date": d,
                    "entry_amount": allocation * (1 - COST_PCT),
                    "exit_date": t["exit_date"],
                    "return_pct": float(t["gross_return_pct"]),
                })
            else:
                skipped += 1

    end_price = nearest_price(core_prices, end_date) or init_price
    portfolio_value = shares_core * end_price
    for pos in open_positions:
        portfolio_value += pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST_PCT)

    years = (end_date - start_date).days / 365.25
    cagr = ((portfolio_value / INITIAL) ** (1 / years) - 1) * 100 if years > 0 else 0

    # baseline 同期
    p_start = nearest_price(core_prices, start_date)
    p_end = nearest_price(core_prices, end_date)
    bh_cagr = ((p_end / p_start) ** (1 / years) - 1) * 100 if p_start and years > 0 else 0

    return {
        "final_value": portfolio_value,
        "total_return_pct": (portfolio_value / INITIAL - 1) * 100,
        "cagr": cagr,
        "alpha": cagr - bh_cagr,
        "n_trades": len(closed) + len(open_positions),
        "n_input": len(trades),
        "skipped": skipped,
    }


# ──────────────────────────────────────────────
# Filters
# ──────────────────────────────────────────────
def filter_1_big_holder_slope(trades: pd.DataFrame, min_slope: float = 0.0) -> pd.DataFrame:
    """大戶持股 (≥1000 張) 4 週斜率 > min_slope pp/wk 才保留。"""
    keep = []
    for _, t in trades.iterrows():
        path = CACHE_FM / f"TaiwanStockHoldingSharesPer_{t['ticker']}.parquet"
        if not path.exists():
            keep.append(False)
            continue
        df = pd.read_parquet(path)
        df = df[df["HoldingSharesLevel"] == "more than 1,000,001"].copy()
        if df.empty:
            keep.append(False)
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        # 取 entry_date 之前 4 週與當日的差
        entry = t["entry_date"]
        before = df[df["date"] <= entry - timedelta(weeks=4)].sort_values("date")
        now = df[df["date"] <= entry].sort_values("date")
        if before.empty or now.empty:
            keep.append(False)
            continue
        slope_pp = float(now.iloc[-1]["percent"]) - float(before.iloc[-1]["percent"])
        keep.append(slope_pp > min_slope)
    return trades[keep].reset_index(drop=True)


def filter_3_sector_rs(trades: pd.DataFrame, min_rs_pp: float = 5.0) -> pd.DataFrame:
    """個股 30 天報酬 - 族群 30 天報酬 > min_rs_pp 才保留。
    族群定義：FinMind industry_category（覆蓋全市場 4089 檔）。
    """
    info_path = CACHE_FM / "TaiwanStockInfo.parquet"
    if info_path.exists():
        info = pd.read_parquet(info_path)
    else:
        # fallback: 透過 FinMind API
        import os
        from dotenv import load_dotenv
        load_dotenv(ROOT / "config" / ".env")
        from src.data.finmind_client import FinMindClient
        fc = FinMindClient(token=os.environ.get("FINMIND_TOKEN", ""), cache_dir=CACHE_FM)
        info = fc.get_all_listed_info()

    info["stock_id"] = info["stock_id"].astype(str)
    ticker_to_sector = dict(zip(info["stock_id"], info["industry_category"]))
    sector_to_tickers: dict[str, list[str]] = {}
    for tk, sec in ticker_to_sector.items():
        sector_to_tickers.setdefault(sec, []).append(tk)

    # cache sector mean returns
    def stock_30d_return(ticker: str, entry: date) -> float | None:
        df = load_ohlcv_cache(ticker, CACHE_YF)
        if df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date")
        win_end = df[df["date"] <= entry]
        win_start = df[df["date"] <= entry - timedelta(days=30)]
        if win_end.empty or win_start.empty:
            return None
        c0 = float(win_start.iloc[-1]["close"])
        c1 = float(win_end.iloc[-1]["close"])
        if c0 <= 0:
            return None
        return (c1 / c0 - 1) * 100

    # 族群 30d 平均報酬 cache（同 entry_date 下的同族群只算一次）
    sector_ret_cache: dict[tuple[str, date], float] = {}

    def sector_mean(sec: str, entry: date) -> float | None:
        key = (sec, entry)
        if key in sector_ret_cache:
            return sector_ret_cache[key]
        sec_tickers = sector_to_tickers.get(sec, [])
        # sample 上限避免太慢：超過 30 檔取等距 30 檔
        if len(sec_tickers) > 30:
            step = max(1, len(sec_tickers) // 30)
            sec_tickers = sec_tickers[::step][:30]
        rets = []
        for st in sec_tickers:
            r = stock_30d_return(st, entry)
            if r is not None:
                rets.append(r)
        result = sum(rets) / len(rets) if rets else None
        sector_ret_cache[key] = result
        return result

    keep = []
    for _, t in trades.iterrows():
        sec = ticker_to_sector.get(t["ticker"])
        if sec is None or pd.isna(sec):
            keep.append(True)  # 未知族群不懲罰
            continue
        sm = sector_mean(sec, t["entry_date"])
        if sm is None:
            keep.append(True)
            continue
        own = stock_30d_return(t["ticker"], t["entry_date"])
        if own is None:
            keep.append(False)
            continue
        keep.append(own - sm > min_rs_pp)
    return trades[keep].reset_index(drop=True)


def apply_2_early_cut(trades: pd.DataFrame, cut_days: int = 60) -> pd.DataFrame:
    """已存在的 trade，若 hold_days > cut_days 且持倉中報酬 < 0
    ， 改為在第 cut_days 日強制出場（重算 exit）。
    """
    import copy
    out = []
    for _, t in trades.iterrows():
        ticker = t["ticker"]
        entry = t["entry_date"]
        exit_d = t["exit_date"]
        hold = (exit_d - entry).days
        gross = float(t["gross_return_pct"])
        # 只動「最終是負且持很久」的：那些原本拖到 hard_stop 的
        if hold > cut_days and gross < 0:
            df = load_ohlcv_cache(ticker, CACHE_YF)
            if df.empty:
                out.append(t.to_dict())
                continue
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df.sort_values("date")
            after_entry = df[df["date"] >= entry].reset_index(drop=True)
            if len(after_entry) < cut_days + 1:
                out.append(t.to_dict())
                continue
            cut_row = after_entry.iloc[cut_days]
            entry_price_row = after_entry.iloc[0]
            ret_at_cut = (
                float(cut_row["close"]) / float(entry_price_row["close"]) - 1
            ) * 100
            # 只在 cut 點是負才提早出（避免砍掉之後翻紅的）
            if ret_at_cut < 0:
                new = t.to_dict()
                new["exit_date"] = cut_row["date"]
                new["gross_return_pct"] = round(ret_at_cut, 2)
                new["exit_reason"] = "early_cut_60d"
                out.append(new)
                continue
        out.append(t.to_dict())
    return pd.DataFrame(out)


def apply_6_dynamic_entry(trades: pd.DataFrame, lookahead: int = 10) -> pd.DataFrame:
    """進場日 dynamic：從原 entry_date 起找首個「量 > 5d avg × 1.2 且 close > 昨日 +5%」日，
    沒找到就保持原 entry_date。並重算 gross_return_pct (用新 entry → 原 exit)。"""
    out = []
    for _, t in trades.iterrows():
        df = load_ohlcv_cache(t["ticker"], CACHE_YF)
        if df.empty:
            out.append(t.to_dict())
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date").reset_index(drop=True)
        after = df[df["date"] >= t["entry_date"]].reset_index(drop=True)
        if len(after) < 6:
            out.append(t.to_dict())
            continue
        new_entry_idx = 0
        for i in range(1, min(lookahead, len(after) - 1)):
            v_avg = after.iloc[max(0, i - 5):i]["volume"].mean()
            if v_avg <= 0:
                continue
            v = after.iloc[i]["volume"]
            c = float(after.iloc[i]["close"])
            c_prev = float(after.iloc[i - 1]["close"])
            if v > v_avg * 1.2 and c > c_prev * 1.05:
                new_entry_idx = i
                break
        new = t.to_dict()
        if new_entry_idx > 0:
            new_entry_date = after.iloc[new_entry_idx]["date"]
            if new_entry_date < t["exit_date"]:
                new["entry_date"] = new_entry_date
                new_entry_price = float(after.iloc[new_entry_idx]["close"])
                # 找原 exit 對應的 close
                exit_row = df[df["date"] <= t["exit_date"]].tail(1)
                if not exit_row.empty:
                    exit_price = float(exit_row.iloc[0]["close"])
                    new["gross_return_pct"] = round(
                        (exit_price / new_entry_price - 1) * 100, 2
                    )
        out.append(new)
    return pd.DataFrame(out)


def apply_8_atr_size(trades: pd.DataFrame, target_vol_pct: float = 5.0) -> pd.DataFrame:
    """ATR-based size: size = base × min(target_vol / vol_pct, 1.5)。
    若 trades 已有 size_pct 欄位，乘上 ATR 調整因子。"""
    out_sizes = []
    for _, t in trades.iterrows():
        base = float(t["size_pct"]) if "size_pct" in t.index else PER_TRADE_PCT
        df = load_ohlcv_cache(t["ticker"], CACHE_YF)
        if df.empty:
            out_sizes.append(base)
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date")
        prior = df[df["date"] <= t["entry_date"]].tail(15)
        if len(prior) < 14:
            out_sizes.append(base)
            continue
        # ATR(14)
        prior = prior.copy()
        prior["tr"] = prior.apply(
            lambda r: max(
                float(r["high"]) - float(r["low"]),
                abs(float(r["high"]) - float(r["close"])),
                abs(float(r["low"]) - float(r["close"])),
            ),
            axis=1,
        )
        atr = prior["tr"].tail(14).mean()
        last_close = float(prior.iloc[-1]["close"])
        if last_close <= 0:
            out_sizes.append(base)
            continue
        vol_pct = (atr / last_close) * 100
        if vol_pct <= 0:
            out_sizes.append(base)
            continue
        adj = min(target_vol_pct / vol_pct, 1.5)
        out_sizes.append(base * adj)
    out = trades.copy()
    out["size_pct"] = out_sizes
    return out


def apply_4_conviction_weight(trades: pd.DataFrame) -> pd.DataFrame:
    """根據原始 EH entry_score 做加權：≥80 → 1.5x，70-79 → 1.2x。"""
    raw = pd.read_csv(INPUT_RAW)
    raw["ticker"] = raw["ticker"].astype(str)
    score_map = dict(zip(
        raw["ticker"] + "_" + raw["entry_date"].astype(str),
        raw["entry_score"].astype(float),
    ))
    out = trades.copy()
    sizes = []
    for _, t in out.iterrows():
        key = f"{t['ticker']}_{t['entry_date']}"
        sc = score_map.get(key, 60.0)
        if sc >= 80:
            sizes.append(PER_TRADE_PCT * 1.5)
        elif sc >= 70:
            sizes.append(PER_TRADE_PCT * 1.2)
        else:
            sizes.append(PER_TRADE_PCT)
    out["size_pct"] = sizes
    return out


# ──────────────────────────────────────────────
# Sprint Runner
# ──────────────────────────────────────────────
def main() -> None:
    df = pd.read_csv(INPUT_TRAILING)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.date
    df["ticker"] = df["ticker"].astype(str)

    # 0050 prices
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    start_date = df["entry_date"].min()
    end_date = df["exit_date"].max()

    print("=" * 70)
    print(f"EH v3 Sprint — Baseline {len(df)} trades, {start_date} ~ {end_date}")
    print("=" * 70)

    # ── Baseline ──
    base = run_v2_portfolio(df, prices_0050, start_date, end_date)
    print(f"\n[BASELINE] {base['n_trades']} trades  CAGR {base['cagr']:+.2f}%  "
          f"alpha {base['alpha']:+.2f}pp")

    cur_df = df.copy()
    cur_cagr = base["cagr"]
    summary = [("baseline", base["cagr"], base["alpha"], len(cur_df))]

    # ── #1 大戶持股 pre-filter ──
    print("\n" + "─" * 70)
    print("[#1] 大戶持股 pre-filter (slope > -0.5 容忍輕微減)")
    new_df = filter_1_big_holder_slope(cur_df, min_slope=-0.5)
    res = run_v2_portfolio(new_df, prices_0050, start_date, end_date)
    print(f"  {len(new_df)}/{len(cur_df)} trades survived  "
          f"CAGR {res['cagr']:+.2f}%  alpha {res['alpha']:+.2f}pp  "
          f"Δ {res['cagr'] - cur_cagr:+.2f}pp")
    if res["cagr"] >= cur_cagr + 0.3:
        cur_df = new_df
        cur_cagr = res["cagr"]
        print("  ✅ 保留")
    else:
        print("  ❌ 退化或無改善 → 退回")
    summary.append(("#1 big_holder", res["cagr"], res["alpha"], len(new_df)))

    # ── #2 60 天早砍 ──
    print("\n" + "─" * 70)
    print("[#2] 60 天早砍 (持 >60d 且當下虧 → 出場)")
    new_df = apply_2_early_cut(cur_df, cut_days=60)
    res = run_v2_portfolio(new_df, prices_0050, start_date, end_date)
    n_changed = (new_df["exit_reason"] == "early_cut_60d").sum() if "exit_reason" in new_df.columns else 0
    print(f"  {n_changed} trades 提早出場  "
          f"CAGR {res['cagr']:+.2f}%  alpha {res['alpha']:+.2f}pp  "
          f"Δ {res['cagr'] - cur_cagr:+.2f}pp")
    if res["cagr"] >= cur_cagr + 0.3:
        cur_df = new_df
        cur_cagr = res["cagr"]
        print("  ✅ 保留")
    else:
        print("  ❌ 退化或無改善 → 退回")
    summary.append(("#2 early_cut_60d", res["cagr"], res["alpha"], len(new_df)))

    # ── #3 族群 RS gate ──
    print("\n" + "─" * 70)
    print("[#3] 族群 RS gate (個股 30d - 族群 30d > 5pp)")
    try:
        new_df = filter_3_sector_rs(cur_df, min_rs_pp=5.0)
        res = run_v2_portfolio(new_df, prices_0050, start_date, end_date)
        print(f"  {len(new_df)}/{len(cur_df)} trades survived  "
              f"CAGR {res['cagr']:+.2f}%  alpha {res['alpha']:+.2f}pp  "
              f"Δ {res['cagr'] - cur_cagr:+.2f}pp")
        if res["cagr"] >= cur_cagr + 0.3:
            cur_df = new_df
            cur_cagr = res["cagr"]
            print("  ✅ 保留")
        else:
            print("  ❌ 退化或無改善 → 退回")
        summary.append(("#3 sector_rs", res["cagr"], res["alpha"], len(new_df)))
    except Exception as e:
        print(f"  ⚠️  族群 RS 失敗：{e}")
        summary.append(("#3 sector_rs", None, None, None))

    # ── #4 Conviction 加權 ──
    print("\n" + "─" * 70)
    print("[#4] Conviction 加權 (score≥80→1.5x, ≥70→1.2x)")
    try:
        new_df = apply_4_conviction_weight(cur_df)
        res = run_v2_portfolio(new_df, prices_0050, start_date, end_date, use_size_col=True)
        big = (new_df["size_pct"] > PER_TRADE_PCT).sum()
        print(f"  {big}/{len(new_df)} trades 加權  "
              f"CAGR {res['cagr']:+.2f}%  alpha {res['alpha']:+.2f}pp  "
              f"Δ {res['cagr'] - cur_cagr:+.2f}pp")
        if res["cagr"] >= cur_cagr + 0.3:
            cur_df = new_df
            cur_cagr = res["cagr"]
            print("  ✅ 保留")
        else:
            print("  ❌ 退化或無改善 → 退回")
        summary.append(("#4 conviction", res["cagr"], res["alpha"], len(new_df)))
    except Exception as e:
        print(f"  ⚠️  Conviction 失敗：{e}")
        summary.append(("#4 conviction", None, None, None))

    # ── #6 進場日 dynamic ──
    print("\n" + "─" * 70)
    print("[#6] 進場日 dynamic (從 entry 起找量大+漲5%日)")
    try:
        new_df = apply_6_dynamic_entry(cur_df, lookahead=10)
        n_changed = (new_df["entry_date"] != cur_df.reset_index(drop=True)["entry_date"]).sum()
        res = run_v2_portfolio(
            new_df, prices_0050, start_date, end_date,
            use_size_col="size_pct" in new_df.columns,
        )
        print(f"  {n_changed} trades 改進場日  CAGR {res['cagr']:+.2f}%  "
              f"alpha {res['alpha']:+.2f}pp  Δ {res['cagr'] - cur_cagr:+.2f}pp")
        if res["cagr"] >= cur_cagr + 0.3:
            cur_df = new_df
            cur_cagr = res["cagr"]
            print("  ✅ 保留")
        else:
            print("  ❌ 退化或無改善 → 退回")
        summary.append(("#6 dynamic_entry", res["cagr"], res["alpha"], len(new_df)))
    except Exception as e:
        print(f"  ⚠️  失敗：{e}")
        summary.append(("#6 dynamic_entry", None, None, None))

    # ── #8 ATR-based size ──
    print("\n" + "─" * 70)
    print("[#8] ATR-based size (target vol 5%)")
    try:
        new_df = apply_8_atr_size(cur_df, target_vol_pct=5.0)
        res = run_v2_portfolio(
            new_df, prices_0050, start_date, end_date, use_size_col=True,
        )
        avg_size = new_df["size_pct"].mean()
        print(f"  avg size_pct={avg_size:.3f}  "
              f"CAGR {res['cagr']:+.2f}%  alpha {res['alpha']:+.2f}pp  "
              f"Δ {res['cagr'] - cur_cagr:+.2f}pp")
        if res["cagr"] >= cur_cagr + 0.3:
            cur_df = new_df
            cur_cagr = res["cagr"]
            print("  ✅ 保留")
        else:
            print("  ❌ 退化或無改善 → 退回")
        summary.append(("#8 atr_size", res["cagr"], res["alpha"], len(new_df)))
    except Exception as e:
        print(f"  ⚠️  失敗：{e}")
        summary.append(("#8 atr_size", None, None, None))

    # ── 總結 ──
    print("\n" + "=" * 70)
    print("Sprint 總結")
    print("=" * 70)
    print(f"  {'階段':<24} {'CAGR':>8} {'alpha':>8} {'n':>6}")
    for name, cagr, alpha, n in summary:
        if cagr is None:
            print(f"  {name:<24} {'fail':>8} {'fail':>8} {'-':>6}")
        else:
            print(f"  {name:<24} {cagr:>+7.2f}% {alpha:>+7.2f}pp {n:>6}")


if __name__ == "__main__":
    main()
