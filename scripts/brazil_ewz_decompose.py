"""
巴西 EWZ 拆解 — 真分散還是 currency play？

關鍵問題：
  1. EWZ 12% CAGR 來自巴西企業還是巴西雷亞爾？
  2. 巴西本土指數（IBOV）跟 EWZ 差距多少？
  3. EWZ 跟 USDBRL 相關性多高？
  4. 與 0050 corr 0.119 是否穩定？
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
    ("EWZ",       "iShares MSCI Brazil"),
    ("^BVSP",     "Bovespa Index (BRL)"),       # 巴西本土指數（雷亞爾計價）
    ("VALE",      "Vale ADR"),                  # 巴西最大礦業
    ("PBR",       "Petrobras ADR"),             # 國家石油
    ("ITUB",      "Itau Unibanco ADR"),         # 巴西最大銀行
    ("USDBRL=X",  "USD/BRL"),                   # 美元/雷亞爾
    ("0050.TW",   "元大台 50"),
    ("EWY",       "韓國 MSCI"),
]


def load(ticker: str) -> pd.DataFrame:
    cache_p = CACHE / f"{ticker.replace('.', '_').replace('=', '_').replace('^', '')}.parquet"
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
    print("巴西 EWZ 拆解 — 真分散還是 currency play？")
    print("=" * 90)

    print("\n[1/4] 載入...")
    data = {}
    for tk, name in TARGETS:
        df = load(tk)
        if df.empty:
            print(f"  ❌ {tk}")
            continue
        data[tk] = {"name": name, "df": df}
        print(f"  ✅ {tk:<10} {name}: {len(df)} rows {df['date'].min()} ~ {df['date'].max()}")

    common_start = max(d["df"]["date"].min() for d in data.values())
    common_end = min(d["df"]["date"].max() for d in data.values())
    print(f"\n共同期間: {common_start} ~ {common_end}")

    aligned = {}
    for tk, info in data.items():
        df = info["df"][(info["df"]["date"] >= common_start)
                       & (info["df"]["date"] <= common_end)].reset_index(drop=True)
        aligned[tk] = {**info, "df": df}

    if "EWZ" not in aligned:
        print("❌ no EWZ"); return
    ewz_rets = aligned["EWZ"]["df"].set_index("date")["close"].pct_change().dropna()

    # 全期 + 1y CAGR
    print("\n[2/4] CAGR 對比 + 跟 EWZ 相關性")
    print(f"  {'Ticker':<10} {'名稱':<28} {'全期 CAGR':>10} {'1y CAGR':>10} {'corr EWZ':>10}")
    print("  " + "-" * 80)
    rows = []
    for tk, info in aligned.items():
        df = info["df"]
        c = cagr(df)
        c_1y = cagr(df, period_days=252)
        rets = df.set_index("date")["close"].pct_change().dropna()
        common = rets.index.intersection(ewz_rets.index)
        corr = rets.loc[common].corr(ewz_rets.loc[common]) if len(common) > 30 else 0
        rows.append({"ticker": tk, "name": info["name"],
                     "cagr": c, "cagr_1y": c_1y, "corr_ewz": corr})
        flag = " ⭐" if tk == "EWZ" else ""
        print(f"  {tk:<10} {info['name']:<28s} {c:>+9.2f}% {c_1y:>+9.2f}% {corr:>+9.3f}{flag}")

    # USDBRL 對 EWZ 的迴歸
    print("\n[3/4] USDBRL（雷亞爾貶值）對 EWZ 的影響")
    print("  " + "-" * 60)
    if "USDBRL=X" in aligned:
        brl = aligned["USDBRL=X"]["df"].set_index("date")["close"].pct_change().dropna()
        common_brl = brl.index.intersection(ewz_rets.index)
        if len(common_brl) > 100:
            x = brl.loc[common_brl].values
            y = ewz_rets.loc[common_brl].values
            beta = np.cov(x, y)[0, 1] / np.var(x)
            r2 = np.corrcoef(x, y)[0, 1] ** 2
            print(f"  EWZ_USD_ret = α + β × USDBRL_ret")
            print(f"    β = {beta:.3f}（USDBRL +1% → EWZ {beta*100:+.2f}%）")
            print(f"    R² = {r2:.3f}（{r2*100:.1f}% EWZ 變動由 USDBRL 解釋）")
            print(f"    解讀: USDBRL 上升 = 雷亞爾貶值 = EWZ {('漲' if beta > 0 else '跌')}")

    # EWZ vs IBOV (純巴西本土雷亞爾計價)
    print("\n[4/4] EWZ (USD) vs ^BVSP (BRL) — currency 拆解")
    print("  " + "-" * 60)
    if "^BVSP" in aligned:
        ewz_cagr = cagr(aligned["EWZ"]["df"])
        bvsp_cagr = cagr(aligned["^BVSP"]["df"])
        diff = ewz_cagr - bvsp_cagr
        print(f"  EWZ (USD 計價):     {ewz_cagr:+.2f}% CAGR")
        print(f"  IBOV (BRL 計價):    {bvsp_cagr:+.2f}% CAGR")
        print(f"  差異:               {diff:+.2f}pp/yr")
        print(f"  → 此差異 = currency translation effect")
        print(f"  → 若 EWZ - IBOV 為負，表示 BRL 貶值拖累 USD 報酬")

    # 個股拆解
    print("\n  EWZ 主要持股表現（11 年 CAGR）：")
    for tk in ["VALE", "PBR", "ITUB"]:
        if tk in aligned:
            c = cagr(aligned[tk]["df"])
            print(f"    {tk:<8s} {aligned[tk]['name']:<25s} {c:+.2f}%")

    # 1y 拆解
    print("\n  過去 1 年表現：")
    for tk in ["EWZ", "^BVSP", "VALE", "PBR", "ITUB", "USDBRL=X", "0050.TW", "EWY"]:
        if tk in aligned:
            c = cagr(aligned[tk]["df"], period_days=252)
            print(f"    {tk:<10} {aligned[tk]['name']:<28s} {c:+.2f}%")


if __name__ == "__main__":
    main()
