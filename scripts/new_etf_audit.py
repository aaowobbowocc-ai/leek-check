"""
新 ETF 研究 — 2023-2025 發行的熱門 ETF vs 0050 baseline。

研究問題：
  1. 真實績效 vs 0050（自上市起 + 過去 1 年）
  2. AUM 趨勢（人氣動向）
  3. 配息率
  4. 跟 0050 / 00881 相關性（分散度）
  5. 哪些值得加進 portfolio

方法：用 yfinance 抓 daily（不用 FinMind，避開 ban）
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "cache" / "yfinance" / "etf_audit"
CACHE.mkdir(parents=True, exist_ok=True)

# 新 ETF 清單（按上市日期）
NEW_ETFS = [
    # (ticker, name, ipo_date, theme)
    ("00929.TW", "復華台灣科技優息",  "2023-06-19", "科技+月配息"),
    ("00919.TW", "群益台灣精選高息",  "2023-10-19", "高息（季配）"),
    ("00936.TW", "台新永續高息中小",  "2024-01-22", "中小+永續+高息"),
    ("00939.TW", "統一台灣高息動能",  "2024-03-20", "高息+動能"),
    ("00940.TW", "元大臺灣價值高息",  "2024-03-20", "史上最多募資"),
    ("00946.TW", "群益台灣半導體收益", "2024-08-13", "半導體高息"),
    ("00947.TW", "中信成長關鍵半導體", "2024-09-19", "半導體"),
    ("00961.TW", "台新台灣科技龍頭",  "2025-04-22", "科技龍頭"),
]

# Baselines for comparison
BASELINES = [
    ("0050.TW",  "元大台 50"),
    ("00881.TW", "國泰台灣 5G+"),
    ("0056.TW",  "元大高股息（老牌）"),
    ("00878.TW", "國泰永續高股息"),
]


def load_or_fetch(ticker: str) -> pd.DataFrame:
    cache_p = CACHE / f"{ticker.replace('.', '_')}.parquet"
    if cache_p.exists():
        return pd.read_parquet(cache_p)
    import yfinance as yf
    print(f"  fetching {ticker}...")
    df = yf.download(ticker, start="2020-01-01", end=date.today().isoformat(),
                     auto_adjust=True, progress=False)
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df.to_parquet(cache_p, index=False)
    return df


def cagr(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    n_years = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    if n_years <= 0.05:
        return 0.0
    return ((df["close"].iloc[-1] / df["close"].iloc[0]) ** (1 / n_years) - 1) * 100


def annualized_vol(df: pd.DataFrame) -> float:
    rets = df["close"].pct_change().dropna()
    if len(rets) < 30:
        return 0.0
    return rets.std() * np.sqrt(252) * 100


def sharpe(df: pd.DataFrame) -> float:
    rets = df["close"].pct_change().dropna()
    if len(rets) < 30 or rets.std() == 0:
        return 0.0
    return rets.mean() / rets.std() * np.sqrt(252)


def mdd(df: pd.DataFrame) -> float:
    peak = df["close"].cummax()
    return ((df["close"] - peak) / peak).min() * 100


def main() -> None:
    print("=" * 90)
    print("新 ETF 研究 (2023-2025 發行) vs 0050 baseline")
    print("=" * 90)

    # 1. Load all
    print("\n[1/4] 載入資料...")
    data: dict[str, dict] = {}
    for tk, name in [(t, n) for t, n, _, _ in NEW_ETFS] + BASELINES:
        df = load_or_fetch(tk)
        if df.empty:
            print(f"  ❌ {tk} ({name}): 無資料")
            continue
        data[tk] = {"name": name, "df": df}

    # 2. 全期績效（自上市起）
    print("\n[2/4] 自上市起績效")
    print(f"  {'代號':<10} {'名稱':<20s} {'起始日':<12} {'天':>4} {'CAGR':>8} {'vol':>6} {'Sharpe':>7} {'MDD':>8}")
    print("  " + "-" * 85)
    rows = []
    for tk, info in data.items():
        df = info["df"]
        ipo = df["date"].iloc[0]
        n = len(df)
        c = cagr(df)
        v = annualized_vol(df)
        s = sharpe(df)
        m = mdd(df)
        rows.append({"ticker": tk, "name": info["name"], "ipo": ipo,
                     "n_days": n, "cagr": c, "vol": v, "sharpe": s, "mdd": m})
        print(f"  {tk:<10} {info['name']:<20s} {ipo!s:<12} {n:>4} {c:>+7.2f}% "
              f"{v:>5.1f}% {s:>7.2f} {m:>+7.2f}%")

    # 3. 過去 1 年績效（更近期）
    print("\n[3/4] 過去 1 年績效")
    one_year_ago = date.today() - timedelta(days=365)
    print(f"  {'代號':<10} {'名稱':<20s} {'1y CAGR':>8} {'1y Sharpe':>10} {'1y MDD':>8}")
    print("  " + "-" * 70)
    for tk, info in data.items():
        df = info["df"]
        sub = df[df["date"] >= one_year_ago].copy()
        if len(sub) < 30:
            continue
        c = cagr(sub)
        s = sharpe(sub)
        m = mdd(sub)
        print(f"  {tk:<10} {info['name']:<20s} {c:>+7.2f}% {s:>10.2f} {m:>+7.2f}%")

    # 4. 跟 0050 / 00881 相關性 + alpha 對比
    print("\n[4/4] 與 0050 / 00881 對比 (自上市起)")
    print(f"  {'代號':<10} {'名稱':<20s} {'vs 0050':>9} {'vs 00881':>10} {'corr 0050':>11}")
    print("  " + "-" * 75)
    if "0050.TW" not in data or "00881.TW" not in data:
        print("  ❌ 缺 0050 或 00881 資料")
    else:
        for tk, info in data.items():
            if tk in ("0050.TW", "00881.TW", "0056.TW", "00878.TW"):
                continue
            df = info["df"]
            ipo = df["date"].iloc[0]
            # 對齊 0050 同期間
            tw50 = data["0050.TW"]["df"]
            tw50_aligned = tw50[tw50["date"] >= ipo].copy()
            tw88 = data["00881.TW"]["df"]
            tw88_aligned = tw88[tw88["date"] >= ipo].copy()
            if len(tw50_aligned) < 30 or len(tw88_aligned) < 30:
                continue
            etf_cagr = cagr(df)
            tw50_cagr = cagr(tw50_aligned)
            tw88_cagr = cagr(tw88_aligned)
            alpha_50 = etf_cagr - tw50_cagr
            alpha_88 = etf_cagr - tw88_cagr
            # 相關性（每日 return）
            r_etf = df.set_index("date")["close"].pct_change().dropna()
            r_50 = tw50_aligned.set_index("date")["close"].pct_change().dropna()
            common = r_etf.index.intersection(r_50.index)
            corr = r_etf.loc[common].corr(r_50.loc[common]) if len(common) > 30 else 0
            print(f"  {tk:<10} {info['name']:<20s} {alpha_50:>+8.2f}pp "
                  f"{alpha_88:>+9.2f}pp {corr:>10.3f}")

    # 寫出
    df_out = pd.DataFrame(rows)
    out = ROOT / "logs" / "new_etf_audit.csv"
    df_out.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
