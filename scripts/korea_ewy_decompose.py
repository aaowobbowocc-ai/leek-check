"""
韓國 EWY 拆解 — 1 年 +187% 是 HBM 周期還是 Samsung 一檔拉？

關鍵問題：
  1. EWY top holdings 集中度多高？（如果三星 + SK Hynix > 50% → 高度集中）
  2. EWY 跟 005930.KS Samsung 的相關性？
  3. EWY 跟 000660.KS SK Hynix 的相關性？
  4. 過去 11 年 EWY 表現是 broad Korea 還是 mega-cap 拉？
  5. Rolling 5y alpha 穩定性

對標：
  - 005930.KS Samsung Electronics
  - 000660.KS SK Hynix
  - EWY 整體
  - vs 0050.TW

決策：
  - 若 EWY ≈ Samsung → 重押 005930.KS 直接買股 vs ETF
  - 若 EWY 真分散 → 8% 配置 OK
  - 若 1y +187% 是 HBM 周期峰值 → 警告過熱
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

CACHE = ROOT / "data" / "cache" / "yfinance" / "global"
CACHE.mkdir(parents=True, exist_ok=True)

TARGETS = [
    ("EWY",       "EWY iShares MSCI Korea"),
    ("005930.KS", "Samsung Electronics"),       # 三星電子
    ("000660.KS", "SK Hynix"),                  # SK 海力士
    ("207940.KS", "Samsung Biologics"),         # Samsung Biologics
    ("373220.KS", "LG Energy Solution"),        # LG 能源
    ("035420.KS", "Naver"),                     # Naver
    ("0050.TW",   "元大台 50"),
    ("2330.TW",   "TSMC"),
    ("USDKRW=X",  "USD/KRW"),                   # 韓元匯率
]


def load(ticker: str) -> pd.DataFrame:
    cache_p = CACHE / f"{ticker.replace('.', '_').replace('=', '_')}.parquet"
    if cache_p.exists():
        return pd.read_parquet(cache_p)
    import yfinance as yf
    print(f"  fetching {ticker}...")
    df = yf.download(ticker, start="2010-01-01", end=date.today().isoformat(),
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


def cagr(df: pd.DataFrame, period_days: int | None = None) -> float:
    if period_days:
        df = df.tail(period_days)
    if len(df) < 2:
        return 0.0
    n_years = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    if n_years <= 0.05:
        return 0.0
    return ((df["close"].iloc[-1] / df["close"].iloc[0]) ** (1 / n_years) - 1) * 100


def main() -> None:
    print("=" * 90)
    print("EWY 韓國拆解 — 是 Samsung 一檔拉，還是真的整體上漲？")
    print("=" * 90)

    print("\n[1/4] 載入...")
    data = {}
    for tk, name in TARGETS:
        df = load(tk)
        if df.empty:
            print(f"  ❌ {tk}")
            continue
        data[tk] = {"name": name, "df": df}
        print(f"  ✅ {tk} {name}: {len(df)} rows {df['date'].min()} ~ {df['date'].max()}")

    # 對齊
    common_start = max(d["df"]["date"].min() for d in data.values())
    common_end = min(d["df"]["date"].max() for d in data.values())
    print(f"\n共同期間: {common_start} ~ {common_end}")

    aligned = {}
    for tk, info in data.items():
        df = info["df"][(info["df"]["date"] >= common_start)
                       & (info["df"]["date"] <= common_end)].reset_index(drop=True)
        aligned[tk] = {**info, "df": df}

    # 全期 + 1y CAGR
    print("\n[2/4] CAGR 對比")
    print(f"  {'Ticker':<10} {'名稱':<28} {'全期 CAGR':>10} {'1y CAGR':>10} {'corr EWY':>10}")
    print("  " + "-" * 80)
    if "EWY" not in aligned:
        print("❌ no EWY"); return
    ewy_rets = aligned["EWY"]["df"].set_index("date")["close"].pct_change().dropna()

    rows = []
    for tk, info in aligned.items():
        df = info["df"]
        c = cagr(df)
        c_1y = cagr(df, period_days=252)
        # corr to EWY
        rets = df.set_index("date")["close"].pct_change().dropna()
        common = rets.index.intersection(ewy_rets.index)
        corr = rets.loc[common].corr(ewy_rets.loc[common]) if len(common) > 30 else 0
        rows.append({
            "ticker": tk, "name": info["name"],
            "cagr": c, "cagr_1y": c_1y, "corr_ewy": corr,
        })
        flag = " ⭐" if tk == "EWY" else ""
        print(f"  {tk:<10} {info['name']:<28s} {c:>+9.2f}% {c_1y:>+9.2f}% {corr:>10.3f}{flag}")

    # Samsung / SK 對 EWY 的影響度（以日報酬迴歸）
    print("\n[3/4] Samsung 對 EWY 的「驅動程度」")
    print("  ─" * 40)
    if "005930.KS" in aligned:
        ss = aligned["005930.KS"]["df"].set_index("date")["close"].pct_change().dropna()
        common_ss = ss.index.intersection(ewy_rets.index)
        # 簡單 OLS: EWY_ret = alpha + beta × Samsung_ret
        if len(common_ss) > 100:
            x = ss.loc[common_ss].values
            y = ewy_rets.loc[common_ss].values
            # 簡化 beta 計算
            beta = np.cov(x, y)[0, 1] / np.var(x)
            r2 = np.corrcoef(x, y)[0, 1] ** 2
            print(f"  EWY = α + β × Samsung")
            print(f"    β = {beta:.3f}（Samsung 漲 1% → EWY 漲 {beta:.2f}%）")
            print(f"    R² = {r2:.3f}（{r2*100:.1f}% EWY 變動由 Samsung 解釋）")

    if "000660.KS" in aligned:
        sk = aligned["000660.KS"]["df"].set_index("date")["close"].pct_change().dropna()
        common_sk = sk.index.intersection(ewy_rets.index)
        if len(common_sk) > 100:
            x = sk.loc[common_sk].values
            y = ewy_rets.loc[common_sk].values
            beta = np.cov(x, y)[0, 1] / np.var(x)
            r2 = np.corrcoef(x, y)[0, 1] ** 2
            print(f"  EWY = α + β × SK Hynix")
            print(f"    β = {beta:.3f}")
            print(f"    R² = {r2:.3f}（{r2*100:.1f}% EWY 變動由 SK Hynix 解釋）")

    # Rolling 5y alpha vs 0050
    print("\n[4/4] EWY rolling 5y alpha vs 0050")
    print("  ─" * 40)
    if "0050.TW" in aligned:
        ewy_df = aligned["EWY"]["df"][["date", "close"]].rename(columns={"close": "ewy"})
        tw50 = aligned["0050.TW"]["df"][["date", "close"]].rename(columns={"close": "tw50"})
        merged = pd.merge(ewy_df, tw50, on="date", how="inner").sort_values("date")
        window = 252 * 5
        if len(merged) > window:
            merged["ewy_5y"] = ((merged["ewy"] / merged["ewy"].shift(window)) ** (1 / 5) - 1) * 100
            merged["tw50_5y"] = ((merged["tw50"] / merged["tw50"].shift(window)) ** (1 / 5) - 1) * 100
            merged["alpha"] = merged["ewy_5y"] - merged["tw50_5y"]
            valid = merged.dropna(subset=["alpha"])
            print(f"  Rolling 5y alpha 統計（n={len(valid)}）")
            print(f"    平均: {valid['alpha'].mean():+.2f}pp")
            print(f"    中位數: {valid['alpha'].median():+.2f}pp")
            print(f"    最大: {valid['alpha'].max():+.2f}pp")
            print(f"    最小: {valid['alpha'].min():+.2f}pp")
            print(f"    EWY > 0050 比例: {(valid['alpha'] > 0).mean()*100:.1f}%")

    # 推估 Samsung 對 1y +187% 的貢獻
    print("\n[5/5] 過去 1 年拆解（誰在拉 EWY？）")
    print("  ─" * 40)
    one_year = date.today() - timedelta(days=365)
    print(f"  {'標的':<28} {'1y CAGR':>10}")
    for tk in ["EWY", "005930.KS", "000660.KS", "035420.KS", "207940.KS", "0050.TW"]:
        if tk not in aligned:
            continue
        df = aligned[tk]["df"]
        sub = df[df["date"] >= one_year]
        if len(sub) > 30:
            c = cagr(sub)
            print(f"  {aligned[tk]['name']:<28s} {c:>+9.2f}%")


if __name__ == "__main__":
    main()
