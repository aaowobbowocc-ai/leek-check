"""
反向訊號 + 月營收 + 信用餘額 多策略 batch backtest。

5 個策略：
  1. 法人連續賣超後反彈
  2. 連續漲停冷卻 reversal
  3. 月營收公布日 effect (10 號)
  4. 融資餘額激增 + 反彈
  5. VIX spike → TW 隔日跳空 + 24h mean revert
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_INST = ROOT / "data" / "cache" / "finmind" / "institutional"
CACHE_REV = ROOT / "data" / "cache" / "finmind" / "revenue"
CACHE_REV.mkdir(parents=True, exist_ok=True)
CACHE_MARGIN = ROOT / "data" / "cache" / "finmind" / "margin"
CACHE_MARGIN.mkdir(parents=True, exist_ok=True)
SEED = 42
N_BOOT = 500


def load_ohlcv(ticker: str) -> pd.DataFrame:
    p = CACHE_YF / f"{ticker}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def random_window_returns(ohlcv: pd.DataFrame, hold_days: int) -> np.ndarray:
    rets = []
    for i in range(len(ohlcv) - hold_days - 2):
        entry = float(ohlcv.iloc[i + 1]["open"])
        exit_p = float(ohlcv.iloc[i + 1 + hold_days]["close"])
        rets.append((exit_p / entry - 1) * 100)
    return np.array(rets)


def compute_alpha(sig_rets: list, rand_rets: np.ndarray) -> dict:
    if not sig_rets or len(rand_rets) == 0:
        return {"sigma": 0, "alpha": 0, "n": 0}
    sig_mean = np.mean(sig_rets)
    rand_mean = rand_rets.mean()
    rand_std = rand_rets.std()
    alpha = sig_mean - rand_mean
    sigma = (alpha / (rand_std / np.sqrt(len(sig_rets)))) if rand_std > 0 else 0
    return {"n": len(sig_rets), "sig_mean": sig_mean, "rand_mean": rand_mean,
            "alpha": alpha, "sigma": sigma}


# ════════════════════════════════════
# Strategy 1: 法人連續賣超後反彈
# ════════════════════════════════════
def strat_inst_sell_reverse():
    print("\n" + "=" * 80)
    print("【1. 三大法人連續賣超後反彈】")
    print("=" * 80)

    NAME_MAP = {"foreign": "Foreign_Investor",
                "investment_trust": "Investment_Trust",
                "dealer": "Dealer_self"}

    targets = ["2330", "2317", "2454", "2308", "2376", "2382",
               "3231", "3037", "3017", "2344", "2408", "0050",
               "00881", "006208"]

    rows = []
    for tk in targets:
        ohlcv = load_ohlcv(tk)
        inst_p = CACHE_INST / f"{tk}.parquet"
        if not inst_p.exists() or ohlcv.empty:
            continue
        inst = pd.read_parquet(inst_p)
        inst["date"] = pd.to_datetime(inst["date"]).dt.date

        for investor, name_col in NAME_MAP.items():
            for n_consec in [3, 5, 7]:
                for hold in [3, 5, 10]:
                    pivot = inst.pivot_table(index="date", columns="name",
                                              values="net_buy", aggfunc="sum"
                                              ).reset_index()
                    pivot.columns.name = None
                    pivot = pivot.sort_values("date").reset_index(drop=True)
                    if name_col not in pivot.columns:
                        continue
                    pivot["is_sell"] = pivot[name_col] < 0
                    pivot["consec"] = pivot["is_sell"].astype(int).rolling(n_consec).sum()
                    pivot["trigger"] = pivot["consec"] == n_consec
                    sig_dates = pivot[pivot["trigger"]]["date"].tolist()

                    # 隔日進場 long, hold N 天 close
                    o_dates = list(ohlcv["date"])
                    sig_rets = []
                    for d in sig_dates:
                        if d not in o_dates:
                            continue
                        idx = o_dates.index(d)
                        if idx + 1 + hold >= len(o_dates):
                            continue
                        entry = float(ohlcv.iloc[idx + 1]["open"])
                        exit_p = float(ohlcv.iloc[idx + 1 + hold]["close"])
                        sig_rets.append((exit_p / entry - 1) * 100)
                    if len(sig_rets) < 10:
                        continue
                    rand = random_window_returns(ohlcv, hold)
                    a = compute_alpha(sig_rets, rand)
                    if a["n"] < 10:
                        continue
                    rows.append({"ticker": tk, "investor": investor,
                                 "n_consec": n_consec, "hold": hold,
                                 **a})

    res = pd.DataFrame(rows)
    if res.empty:
        print("無結果"); return
    res = res.sort_values("alpha", ascending=False)
    print(f"\n{'tk':<7} {'investor':<18} {'consec':>6} {'hold':>4} "
          f"{'n':>4} {'alpha':>8} {'sigma':>7} {'sig':>8} {'rand':>8}")
    a_count = 0
    for _, r in res.head(15).iterrows():
        marker = "⭐" if r["sigma"] > 1.96 else ("⚠️" if r["sigma"] > 1 else "")
        if r["sigma"] > 1.96: a_count += 1
        print(f"  {r['ticker']:<7} {r['investor']:<18} {r['n_consec']:>5}d "
              f"{r['hold']:>3}d {r['n']:>4} {r['alpha']:>+6.2f}% "
              f"{r['sigma']:>+6.2f} {r['sig_mean']:>+6.2f}% {r['rand_mean']:>+6.2f}% {marker}")
    print(f"\n  總 sigma>1.96: {(res['sigma']>1.96).sum()}")
    out = ROOT / "logs" / "inst_sell_reverse.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")


# ════════════════════════════════════
# Strategy 2: 連續漲停冷卻 reversal
# ════════════════════════════════════
def strat_consecutive_limitup_cooldown():
    print("\n" + "=" * 80)
    print("【2. 連續漲停 N 日後冷卻 reversal】")
    print("=" * 80)

    tickers = sorted({p.stem for p in CACHE_YF.glob("*.parquet")
                      if p.stem.isdigit() and 4 <= len(p.stem) <= 6})
    print(f"Universe: {len(tickers)}")

    rows = []
    for tk in tickers:
        df = load_ohlcv(tk)
        if df.empty or len(df) < 100:
            continue
        df = df.copy()
        df["prev_close"] = df["close"].shift(1)
        df["limitup"] = df["close"] / df["prev_close"] >= 1.095
        df["consec_lu"] = df["limitup"].astype(int).rolling(3).sum()  # 過去 3 日漲停數

        for n_consec in [2, 3]:
            df_t = df[df["consec_lu"] >= n_consec].copy()
            if len(df_t) < 5:
                continue
            for hold in [1, 3, 5]:
                # short 隔日 close 進場，hold 天後 close 平倉
                rets = []
                for idx in df_t.index:
                    if idx + 1 + hold >= len(df):
                        continue
                    entry = float(df.iloc[idx + 1]["close"])
                    exit_p = float(df.iloc[idx + 1 + hold]["close"])
                    rets.append((entry - exit_p) / entry * 100)  # short
                if len(rets) < 5:
                    continue
                rand = random_window_returns(df, hold)
                a = compute_alpha(rets, -rand)  # short = -long
                if a["n"] < 5:
                    continue
                rows.append({"ticker": tk, "n_consec_lu": n_consec, "hold": hold, **a})

    res = pd.DataFrame(rows)
    if res.empty:
        print("無結果"); return
    res = res[res["sigma"] > 1.96].sort_values("alpha", ascending=False)
    print(f"\n{'tk':<7} {'consec':>6} {'hold':>4} {'n':>4} {'alpha':>8} {'sigma':>7}")
    print(f"sigma>1.96 tier A: {len(res)}")
    for _, r in res.head(15).iterrows():
        print(f"  {r['ticker']:<7} {r['n_consec_lu']:>5}d {r['hold']:>3}d {r['n']:>4} "
              f"{r['alpha']:>+6.2f}% {r['sigma']:>+6.2f}")
    out = ROOT / "logs" / "consec_limitup_cooldown.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")


# ════════════════════════════════════
# Strategy 3: 月營收公布日 effect
# ════════════════════════════════════
def strat_monthly_revenue():
    print("\n" + "=" * 80)
    print("【3. 月營收公布日效應 (TW 每月 10 號前公布)】")
    print("=" * 80)

    import requests
    token = os.environ.get("FINMIND_TOKEN", "")
    targets = ["2330", "2317", "2454", "2308", "2376", "3231", "3037",
               "2408", "00881", "0050"]

    rows = []
    for tk in targets:
        cp = CACHE_REV / f"{tk}.parquet"
        if cp.exists():
            df = pd.read_parquet(cp)
        else:
            try:
                r = requests.get("https://api.finmindtrade.com/api/v4/data",
                                 params={"dataset": "TaiwanStockMonthRevenue",
                                         "data_id": tk, "start_date": "2017-01-01",
                                         "end_date": "2026-04-26", "token": token},
                                 timeout=20)
                p = r.json()
                if p.get("status") != 200 or not p.get("data"):
                    continue
                df = pd.DataFrame(p["data"])
                df.to_parquet(cp, index=False)
            except Exception as e:
                continue
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        # YoY 成長
        df = df.sort_values("date").reset_index(drop=True)
        rev_col = "revenue" if "revenue" in df.columns else "Revenue"
        df["rev_yoy"] = df[rev_col].pct_change(12) * 100  # 12 個月前同期

        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            continue
        o_dates = list(ohlcv["date"])

        for hold in [3, 5, 10, 20]:
            for thresh in [10, 30, 50]:
                hi_rev = df[df["rev_yoy"] > thresh]
                rets = []
                for d in hi_rev["date"]:
                    if d not in o_dates:
                        # 找下一個交易日
                        future = [x for x in o_dates if x >= d]
                        if not future: continue
                        d = future[0]
                    idx = o_dates.index(d)
                    if idx + 1 + hold >= len(o_dates):
                        continue
                    entry = float(ohlcv.iloc[idx + 1]["open"])
                    exit_p = float(ohlcv.iloc[idx + 1 + hold]["close"])
                    rets.append((exit_p / entry - 1) * 100)
                if len(rets) < 5:
                    continue
                rand = random_window_returns(ohlcv, hold)
                a = compute_alpha(rets, rand)
                rows.append({"ticker": tk, "yoy_thresh": thresh,
                             "hold": hold, **a})

    res = pd.DataFrame(rows)
    if res.empty:
        print("無結果"); return
    res = res.sort_values("alpha", ascending=False)
    a_filter = res[(res["sigma"] > 1.96) & (res["n"] >= 10)]
    print(f"\nsigma>1.96 + n>=10: {len(a_filter)}")
    print(f"{'tk':<7} {'YoY>':>5} {'hold':>4} {'n':>3} {'alpha':>8} {'sigma':>7}")
    for _, r in a_filter.head(15).iterrows():
        print(f"  {r['ticker']:<7} {r['yoy_thresh']:>4}% {r['hold']:>3}d {r['n']:>3} "
              f"{r['alpha']:>+6.2f}% {r['sigma']:>+6.2f}")
    out = ROOT / "logs" / "monthly_revenue.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")


# ════════════════════════════════════
# Strategy 4: 融資餘額激增反彈
# ════════════════════════════════════
def strat_margin_surge():
    print("\n" + "=" * 80)
    print("【4. 融資餘額激增 → 反彈 (散戶 leverage 訊號)】")
    print("=" * 80)

    import requests
    token = os.environ.get("FINMIND_TOKEN", "")
    targets = ["2330", "2317", "2454", "2308", "2376", "3231", "0050", "00881"]

    rows = []
    for tk in targets:
        cp = CACHE_MARGIN / f"{tk}.parquet"
        if cp.exists():
            df = pd.read_parquet(cp)
        else:
            try:
                r = requests.get("https://api.finmindtrade.com/api/v4/data",
                                 params={"dataset": "TaiwanStockMarginPurchaseShortSale",
                                         "data_id": tk, "start_date": "2017-01-01",
                                         "end_date": "2026-04-26", "token": token},
                                 timeout=20)
                p = r.json()
                if p.get("status") != 200 or not p.get("data"):
                    continue
                df = pd.DataFrame(p["data"])
                df.to_parquet(cp, index=False)
            except Exception as e:
                continue
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date").reset_index(drop=True)
        # 融資餘額 = MarginPurchaseTodayBalance
        bal_col = None
        for c in ["MarginPurchaseTodayBalance", "MarginPurchaseBalance",
                  "margin_purchase_today_balance"]:
            if c in df.columns:
                bal_col = c; break
        if not bal_col:
            continue
        df["bal_change_pct"] = df[bal_col].pct_change(5) * 100  # 5 日變化

        ohlcv = load_ohlcv(tk)
        if ohlcv.empty:
            continue
        o_dates = list(ohlcv["date"])

        # 訊號：融資餘額 5 日內激增 > 20% (散戶大量借錢買 → 通常逆向)
        for thresh in [15, 25, 40]:
            for hold in [5, 10, 20]:
                surge = df[df["bal_change_pct"] > thresh]
                rets = []
                for d in surge["date"]:
                    if d not in o_dates: continue
                    idx = o_dates.index(d)
                    if idx + 1 + hold >= len(o_dates): continue
                    # 融資激增後 short
                    entry = float(ohlcv.iloc[idx + 1]["close"])
                    exit_p = float(ohlcv.iloc[idx + 1 + hold]["close"])
                    rets.append((entry - exit_p) / entry * 100)
                if len(rets) < 10:
                    continue
                rand = random_window_returns(ohlcv, hold)
                a = compute_alpha(rets, -rand)
                rows.append({"ticker": tk, "thresh_pct": thresh, "hold": hold, **a})

    res = pd.DataFrame(rows)
    if res.empty:
        print("無結果"); return
    res = res.sort_values("alpha", ascending=False)
    a_filter = res[(res["sigma"] > 1.5) & (res["n"] >= 10)]
    print(f"\nsigma>1.5 + n>=10: {len(a_filter)}")
    print(f"{'tk':<7} {'thresh':>6} {'hold':>4} {'n':>3} {'alpha':>8} {'sigma':>7}")
    for _, r in a_filter.head(15).iterrows():
        print(f"  {r['ticker']:<7} {r['thresh_pct']:>4}% {r['hold']:>3}d {r['n']:>3} "
              f"{r['alpha']:>+6.2f}% {r['sigma']:>+6.2f}")
    out = ROOT / "logs" / "margin_surge.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")


# ════════════════════════════════════
# Strategy 5: VIX spike 24h mean revert
# ════════════════════════════════════
def strat_vix_spike():
    print("\n" + "=" * 80)
    print("【5. VIX spike 後台股 24h mean revert】")
    print("=" * 80)

    vix = yf.Ticker("^VIX").history(start="2017-01-01", end="2026-04-26", auto_adjust=False)
    vix = vix.reset_index()
    vix.columns = [c.lower() for c in vix.columns]
    vix["date"] = pd.to_datetime(vix["date"]).dt.tz_localize(None).dt.date
    vix["vix_change"] = vix["close"].pct_change() * 100
    vix["vix_close"] = vix["close"]

    # VIX 跳升 (T 日)、台股 T+1 跳低、T+2 收復？
    spy = yf.Ticker("0050.TW").history(start="2017-01-01", end="2026-04-26", auto_adjust=False)
    spy = spy.reset_index()
    spy.columns = [c.lower() for c in spy.columns]
    spy["date"] = pd.to_datetime(spy["date"]).dt.tz_localize(None).dt.date
    spy = spy.sort_values("date").reset_index(drop=True)

    # vix T 日 對 0050 T+1 的影響 + T+2 reversal
    spy["match_date"] = pd.to_datetime(spy["date"]) - pd.Timedelta(days=1)
    spy["match_date"] = spy["match_date"].dt.date
    merged = pd.merge(vix[["date", "vix_change", "vix_close"]].rename(columns={"date": "match_date"}),
                      spy[["date", "open", "close", "match_date"]].rename(columns={"date": "tw_date"}),
                      on="match_date", how="inner")
    merged["t1_open"] = merged["open"]
    merged["t1_close"] = merged["close"]
    merged["t2_close"] = merged["close"].shift(-1)
    merged["t1_open_to_close"] = (merged["t1_close"] / merged["t1_open"] - 1) * 100
    merged["t1_to_t2"] = (merged["t2_close"] / merged["t1_close"] - 1) * 100
    merged = merged.dropna()

    print(f"\nVIX 級別 (T 日) → 0050 T+1 表現")
    print(f"{'VIX 變化':<20} {'天數':>5} {'T+1 open->close':>17} {'T+1 close->T+2':>17}")
    bins = [(-100, -10, "VIX 大跌 < -10%"),
            (-10, 5, "VIX 小幅 -10~+5%"),
            (5, 15, "VIX 跳升 +5~15%"),
            (15, 30, "VIX 急升 +15-30%"),
            (30, 100, "VIX 暴升 > +30%")]
    for lo, hi, label in bins:
        sub = merged[(merged["vix_change"] >= lo) & (merged["vix_change"] < hi)]
        if len(sub) < 5: continue
        m1 = sub["t1_open_to_close"].mean()
        m2 = sub["t1_to_t2"].mean()
        print(f"  {label:<19} {len(sub):>5} {m1:>+15.2f}% {m2:>+15.2f}%")


def main():
    print("=" * 80)
    print("反向訊號 + 月營收 + 信用 + VIX 多策略 batch")
    print("=" * 80)

    strat_inst_sell_reverse()
    strat_consecutive_limitup_cooldown()
    strat_monthly_revenue()
    strat_margin_surge()
    strat_vix_spike()

    print("\n" + "=" * 80)
    print("全 5 策略完成。看上方各 section 結果。")


if __name__ == "__main__":
    main()
