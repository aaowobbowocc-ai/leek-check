"""
多因子組合 backtest

兩個已驗證 alpha 同時觸發的疊加效應：
  A. 月營收 Relative YoY (excess > +30%)         → 60d alpha +3.95%
  B. 妖股 #1 (連漲 + 法人連買)                    → 60d alpha +11.23pp
  C. 妖股多因子 S1+S3 (散戶低 + 量爆)            → 60d alpha +8.13pp

組合測試：
  Combo 1: A AND B  (月營收 + 連漲法人)
  Combo 2: A AND C  (月營收 + 散戶低量爆)
  Combo 3: B AND C  (連漲法人 + 散戶低量爆)
  Combo 4: A AND B AND C (三重共識)

時間窗口: A 公告後 30 日內 B 或 C 觸發 → 算 trigger
評估: forward 60d alpha vs same-ticker random baseline
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
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
OUT_DIR = ROOT / "scripts" / "output"
OUT_DIR.mkdir(exist_ok=True, parents=True)

HOLD_DAYS = 60
LARGE_CAP_EXCLUDE = {"2330", "2317", "2454", "2412", "2891", "2882", "2002", "1303", "1301", "2308"}
COMBO_WINDOW = 30  # signal A 後 N 日內 B/C 也觸發才算 combo


def load_universe():
    return sorted(p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
                  for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))


def load_price(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists() or p.stat().st_size < 500: return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except: return pd.DataFrame()
    if df.empty: return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["pct"] = df["close"].pct_change() * 100
    df["vol_ma"] = df["volume"].rolling(60).mean()
    df["vol_std"] = df["volume"].rolling(60).std()
    df["vol_z"] = (df["volume"] - df["vol_ma"]) / df["vol_std"]
    return df


def load_inst(tk: str) -> pd.DataFrame:
    p = CACHE / f"TaiwanStockInstitutionalInvestorsBuySell_{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except: return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df["net"] = df["buy"] - df["sell"]
    return df.sort_values("date")


def load_holding(tk: str) -> pd.DataFrame:
    p = CACHE / f"TaiwanStockHoldingSharesPer_{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except: return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")


def find_signal_a(tk: str, market_median: dict) -> list:
    """Signal A: 月營收 Relative YoY > +30%"""
    rev_path = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
    if not rev_path.exists(): return []
    try:
        rev = pd.read_parquet(rev_path)
    except: return []
    if rev.empty or len(rev) < 24: return []
    rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
    rev["prior"] = rev["revenue"].shift(12)
    rev["yoy"] = (rev["revenue"] / rev["prior"] - 1) * 100
    rev["date"] = pd.to_datetime(rev["date"])
    rev["ym"] = rev["date"].dt.to_period("M")
    rev["mkt_med"] = rev["ym"].map(market_median)
    rev["excess"] = rev["yoy"] - rev["mkt_med"]
    triggers = rev[
        (rev["excess"] > 30) &
        (rev["yoy"] < 200) &
        (rev["yoy"].notna()) &
        (rev["prior"] > 1e7)
    ]
    return triggers["date"].tolist()


def find_signal_b(tk: str, prices: pd.DataFrame) -> list:
    """Signal B: 妖股 #1 (3 日內 ≥2 次漲幅 ≥9% AND 法人買 +200 張)"""
    if prices.empty or len(prices) < 5: return []
    inst = load_inst(tk)
    if inst.empty: return []

    # 3 日內漲幅 ≥9% 的天數
    prices = prices.copy()
    prices["limit_up"] = (prices["pct"] >= 9.0).astype(int)
    prices["lu_3d"] = prices["limit_up"].rolling(3).sum()

    # 對齊法人 daily total
    inst_daily = inst.groupby("date")["net"].sum().reset_index()
    inst_daily.rename(columns={"net": "inst_net"}, inplace=True)
    merged = prices.merge(inst_daily, on="date", how="left").fillna(0)

    triggers = merged[(merged["lu_3d"] >= 2) & (merged["inst_net"] >= 200000)]
    return triggers["date"].tolist()


def find_signal_c(tk: str, prices: pd.DataFrame) -> list:
    """Signal C: 妖股多因子 S1+S3 (散戶低 < p20 + vol z >= 2.5)"""
    if tk in LARGE_CAP_EXCLUDE: return []
    if prices.empty: return []
    holding = load_holding(tk)
    if holding.empty: return []

    # 散戶比例 (1-50000 級)
    retail_levels = ["1-999", "1,000-5,000", "5,001-10,000", "10,001-15,000",
                     "15,001-20,000", "20,001-30,000", "30,001-40,000", "40,001-50,000"]
    holding = holding.copy()
    holding["is_retail"] = holding["HoldingSharesLevel"].isin(retail_levels)
    grp = holding.groupby(["date", "is_retail"])["percent"].sum().unstack(fill_value=0)
    if True not in grp.columns: return []
    retail_df = pd.DataFrame({"date": grp.index, "retail_pct": grp[True].values}).sort_values("date")

    # 對每天計算 252 日 20% 分位
    # FIX 2026-05-04: 對齊 production scan_signal_2 用 252 (5 年)，原 60 (14 月) 是 backtest/live mismatch
    retail_df["p20"] = retail_df["retail_pct"].rolling(252, min_periods=60).quantile(0.20)
    retail_df["s1"] = (retail_df["retail_pct"] < retail_df["p20"]).astype(int)

    # 對齊
    prices2 = prices[["date", "vol_z"]].copy()
    merged = prices2.merge(retail_df[["date", "s1", "retail_pct"]], on="date", how="left")
    merged["s1"] = merged["s1"].ffill()

    triggers = merged[(merged["s1"] == 1) & (merged["vol_z"] >= 2.5)]
    return triggers["date"].tolist()


def compute_market_median():
    """全市場 monthly median yoy"""
    print("  計算市場 median YoY...")
    all_yoy = []
    for p in CACHE.glob("TaiwanStockMonthRevenue_*.parquet"):
        try:
            rev = pd.read_parquet(p)
            if len(rev) < 24: continue
            rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
            rev["prior"] = rev["revenue"].shift(12)
            rev["yoy"] = (rev["revenue"] / rev["prior"] - 1) * 100
            rev = rev[rev["prior"] > 1e7]
            if rev.empty: continue
            rev["date"] = pd.to_datetime(rev["date"])
            rev_v = rev[rev["yoy"].abs() < 500][["date", "yoy"]]
            all_yoy.append(rev_v)
        except: continue
    df = pd.concat(all_yoy, ignore_index=True)
    df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym")["yoy"].median().to_dict()


def event_alpha(prices: pd.DataFrame, signal_dates: list) -> tuple:
    """forward return + baseline"""
    if prices.empty or not signal_dates: return [], []
    px_idx = prices.set_index("date")["close"]
    rets = []
    for sd in signal_dates:
        future = px_idx[px_idx.index > sd]
        if len(future) <= HOLD_DAYS: continue
        entry = future.iloc[0]
        if entry > 0:
            rets.append((future.iloc[HOLD_DAYS] / entry - 1) * 100)
    # baseline
    if len(px_idx) < HOLD_DAYS + 60: return rets, []
    rng = np.random.RandomState(hash(prices.iloc[0]["date"].toordinal()) % (2**32))
    n_base = min(50, len(px_idx) - HOLD_DAYS - 60)
    base_idx = rng.choice(range(60, len(px_idx) - HOLD_DAYS), size=n_base, replace=False)
    baseline = []
    for i in base_idx:
        if px_idx.iloc[i] > 0:
            baseline.append((px_idx.iloc[i + HOLD_DAYS] / px_idx.iloc[i] - 1) * 100)
    return rets, baseline


def find_combos(dates_a: list, dates_b: list, dates_c: list, window_days: int = 30):
    """找 A 觸發後 30 日內 B/C 也觸發的日期

    FIX #11 (2026-05-04): 兩個改進
      1. 去除 double-count: combo entry date 不重複出現在 B_only 和 C_only
      2. 不對稱 window: A→B 的 B-after-A，不算 B-then-A（避免 double counting）
    """
    a_set = sorted([pd.Timestamp(d) for d in dates_a])
    b_set = sorted([pd.Timestamp(d) for d in dates_b])
    c_set = sorted([pd.Timestamp(d) for d in dates_c])

    combo_ab = set()
    combo_ac = set()
    combo_bc = set()
    combo_abc = set()

    for a in a_set:
        end = a + pd.Timedelta(days=window_days)
        b_in = [b for b in b_set if a < b <= end]  # strict > a
        c_in = [c for c in c_set if a < c <= end]
        if b_in: combo_ab.add(min(b_in))
        if c_in: combo_ac.add(min(c_in))
        if b_in and c_in:
            combo_abc.add(min(min(b_in), min(c_in)))

    # BC: B then C only (不重複算 C then B)
    for b in b_set:
        end = b + pd.Timedelta(days=window_days)
        c_in = [c for c in c_set if b < c <= end]
        if c_in: combo_bc.add(min(c_in))

    return {
        "AB": sorted(combo_ab),
        "AC": sorted(combo_ac),
        "BC": sorted(combo_bc),
        "ABC": sorted(combo_abc),
    }


def find_combos_exclusive(dates_a, dates_b, dates_c, window_days=30):
    """Mutually exclusive combo categorization (no double counting):
      - ABC: A and (B or C) and (the other) all within window
      - AB: A then B (no C in window)
      - AC: A then C (no B in window)
      - A_only: A with no B/C follow-up
      - 等等

    Returns dict with mutually exclusive combo dates
    """
    a_set = sorted([pd.Timestamp(d) for d in dates_a])
    b_set = sorted([pd.Timestamp(d) for d in dates_b])
    c_set = sorted([pd.Timestamp(d) for d in dates_c])

    a_only_dates = []
    ab_only_dates = []
    ac_only_dates = []
    abc_dates = []

    for a in a_set:
        end = a + pd.Timedelta(days=window_days)
        b_in = [b for b in b_set if a < b <= end]
        c_in = [c for c in c_set if a < c <= end]
        if b_in and c_in:
            abc_dates.append(min(min(b_in), min(c_in)))
        elif b_in:
            ab_only_dates.append(min(b_in))
        elif c_in:
            ac_only_dates.append(min(c_in))
        else:
            a_only_dates.append(a)

    return {
        "A_only": a_only_dates,
        "AB_only": ab_only_dates,
        "AC_only": ac_only_dates,
        "ABC": abc_dates,
    }


def main():
    print("=" * 80)
    print("  多因子組合 backtest")
    print("=" * 80)

    universe = load_universe()
    print(f"  Universe: {len(universe)} tickers")

    market_median = compute_market_median()
    print(f"  Market median months: {len(market_median)}")

    # 收集每個 combo 的 returns
    rets_by_combo = {"A_only": [], "B_only": [], "C_only": [],
                     "AB": [], "AC": [], "BC": [], "ABC": []}
    base_by_combo = {k: [] for k in rets_by_combo}

    for i, tk in enumerate(universe):
        prices = load_price(tk)
        if prices.empty or len(prices) < 100: continue

        a_dates = find_signal_a(tk, market_median)
        b_dates = find_signal_b(tk, prices)
        c_dates = find_signal_c(tk, prices)

        # Single signal
        for name, dates in [("A_only", a_dates), ("B_only", b_dates), ("C_only", c_dates)]:
            r, base = event_alpha(prices, dates)
            rets_by_combo[name].extend(r)
            if base: base_by_combo[name].extend(base)

        # Combos
        combos = find_combos(a_dates, b_dates, c_dates, COMBO_WINDOW)
        for combo, dates in combos.items():
            if dates:
                r, base = event_alpha(prices, dates)
                rets_by_combo[combo].extend(r)
                if base: base_by_combo[combo].extend(base)

        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(universe)}] events: A={len(rets_by_combo['A_only'])}, "
                  f"AB={len(rets_by_combo['AB'])}, ABC={len(rets_by_combo['ABC'])}")

    # Summary
    print("\n" + "=" * 80)
    print("  📊 Combo Results")
    print("=" * 80)
    print(f"  {'combo':<10} {'n':<8} {'mean':<10} {'baseline':<10} {'alpha':<10} {'win%':<8} {'t':<8}")
    print(f"  {'-'*65}")
    for combo, rets in rets_by_combo.items():
        if len(rets) < 30:
            print(f"  {combo:<10} n={len(rets)} (太少)")
            continue
        n = len(rets)
        mean = np.mean(rets)
        base = base_by_combo[combo]
        base_mean = np.mean(base) if base else 0
        base_std = np.std(base) if base else 0
        alpha = mean - base_mean
        win = sum(1 for r in rets if r > 0) / n * 100
        t = alpha / (base_std / np.sqrt(n)) if base_std > 0 else None
        t_str = f"{t:+.2f}" if t else "n/a"
        verdict = "✅" if abs(alpha) > 5 and abs(t or 0) > 3 else "⚠️"
        print(f"  {combo:<10} {n:<8} {mean:+.2f}%    {base_mean:+.2f}%    "
              f"{alpha:+.2f}%    {win:.1f}%    {t_str}  {verdict}")


if __name__ == "__main__":
    main()
