"""
全球國家股 ETF audit — 找有潛力的分散標的。

研究問題：
  1. 哪些國家過去 10 年 CAGR > 0050？
  2. 哪些國家跟 0050 相關性低（真分散）？
  3. 哪些國家近 1-3 年表現好（趨勢中）？
  4. 哪些值得加進你的全球配置？

涵蓋：
  - 已知配置：US, Japan, India, Vietnam（已研究過 DXJ）
  - 新增候選：Korea, Indonesia, Mexico, Brazil, Saudi, Germany, EM 主題
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

COUNTRIES = [
    # (ticker, name, region, note)
    ("EWY",  "韓國 (iShares MSCI Korea)",   "亞洲", "三星 + SK海力士主導，跟 TW 結構類似"),
    ("EWS",  "新加坡 (iShares MSCI Singapore)", "亞洲", "金融中心 + 防禦"),
    ("EWH",  "香港 (iShares MSCI HK)",      "亞洲", "中國 proxy + 金融"),
    ("FXI",  "中國大盤 (iShares China Large-Cap)", "亞洲", "監管+地緣風險高"),
    ("MCHI", "中國 MSCI",                    "亞洲", "更廣中國，含 TENCENT/BABA"),
    ("INDA", "印度 (iShares MSCI India)",   "亞洲", "已配 8%"),
    ("VNM",  "越南 (VanEck Vietnam)",        "亞洲", "已配 4%，小盤"),
    ("EIDO", "印尼 (iShares MSCI Indonesia)", "亞洲", "內需 + 鎳礦"),
    ("EWW",  "墨西哥 (iShares MSCI Mexico)", "美洲", "nearshoring 受惠"),
    ("EWZ",  "巴西 (iShares MSCI Brazil)",   "美洲", "商品出口 + 高息"),
    ("KSA",  "沙烏地 (iShares MSCI KSA)",    "中東", "石油 + Vision 2030"),
    ("EWG",  "德國 (iShares MSCI Germany)", "歐洲", "製造業，AI 衝擊"),
    ("EWL",  "瑞士 (iShares MSCI Swiss)",   "歐洲", "防禦 + 醫藥"),
    ("EWU",  "英國 (iShares MSCI UK)",      "歐洲", "金融 + 能源"),
    # Theme
    ("EMQQ", "新興市場網路電商",            "主題", "EM Internet & E-commerce"),
    ("EEM",  "新興市場大盤",                "主題", "broad EM, 含中國"),
    ("IEMG", "新興市場 ex-China",            "主題", "去中國 EM"),
    # Baselines
    ("0050.TW", "元大台 50",                "TW",  "baseline"),
    ("SPY",  "S&P 500",                    "美國", "美股 baseline"),
    ("DXJ",  "WisdomTree Japan Hedged",    "日本", "已驗證"),
    ("VOO",  "Vanguard S&P 500",           "美國", "美股 baseline 2"),
]

START = "2015-01-01"


def load_or_fetch(ticker: str) -> pd.DataFrame:
    cache_p = CACHE / f"{ticker.replace('.', '_')}.parquet"
    if cache_p.exists():
        return pd.read_parquet(cache_p)
    import yfinance as yf
    print(f"  fetching {ticker}...")
    df = yf.download(ticker, start=START, end=date.today().isoformat(),
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


def sharpe(df: pd.DataFrame) -> float:
    rets = df["close"].pct_change().dropna()
    if len(rets) < 30 or rets.std() == 0:
        return 0.0
    return rets.mean() / rets.std() * np.sqrt(252)


def mdd(df: pd.DataFrame) -> float:
    peak = df["close"].cummax()
    return ((df["close"] - peak) / peak).min() * 100


def main() -> None:
    print("=" * 95)
    print("全球國家股 ETF audit (2015-2026, ~11 年)")
    print("=" * 95)

    print("\n[1/3] 載入...")
    data = {}
    for tk, name, region, note in COUNTRIES:
        df = load_or_fetch(tk)
        if df.empty:
            print(f"  ❌ {tk}")
            continue
        data[tk] = {"name": name, "region": region, "note": note, "df": df}

    # 對齊期間
    common_start = max(d["df"]["date"].min() for d in data.values())
    common_end = min(d["df"]["date"].max() for d in data.values())
    print(f"\n共同期間: {common_start} ~ {common_end}")

    aligned = {}
    for tk, info in data.items():
        df = info["df"][(info["df"]["date"] >= common_start)
                       & (info["df"]["date"] <= common_end)].reset_index(drop=True)
        if len(df) < 100:
            continue
        aligned[tk] = {**info, "df": df}

    # 相關性 baseline = 0050
    if "0050.TW" not in aligned:
        print("❌ 缺 0050"); return
    tw50_rets = aligned["0050.TW"]["df"].set_index("date")["close"].pct_change().dropna()

    # 計算
    rows = []
    print(f"\n[2/3] 全期績效 + 相關性")
    print(f"  {'代號':<8} {'名稱':<30} {'區':<5} {'CAGR':>7} {'Sharpe':>7} "
          f"{'MDD':>7} {'corr 0050':>10} {'1y':>7}")
    print("  " + "-" * 95)

    one_year = date.today() - timedelta(days=365)

    sorted_data = sorted(aligned.items(),
                         key=lambda x: cagr(x[1]["df"]),
                         reverse=True)

    for tk, info in sorted_data:
        df = info["df"]
        c = cagr(df)
        s = sharpe(df)
        m = mdd(df)
        # 1y CAGR
        sub = df[df["date"] >= one_year].copy()
        c_1y = cagr(sub) if len(sub) > 30 else 0
        # corr with 0050
        rets = df.set_index("date")["close"].pct_change().dropna()
        common = rets.index.intersection(tw50_rets.index)
        corr = rets.loc[common].corr(tw50_rets.loc[common]) if len(common) > 30 else 0

        rows.append({
            "ticker": tk, "name": info["name"], "region": info["region"],
            "cagr": c, "sharpe": s, "mdd": m,
            "corr_0050": corr, "cagr_1y": c_1y,
        })
        flag = ""
        if tk == "0050.TW":
            flag = " ⭐"
        elif c > 8 and corr < 0.5:
            flag = " ✨ alpha+分散"
        elif corr < 0.3:
            flag = " 🟢 真分散"
        print(f"  {tk:<8} {info['name']:<30s} {info['region']:<5s} "
              f"{c:>+6.2f}% {s:>7.2f} {m:>+6.1f}% {corr:>10.3f} {c_1y:>+6.1f}%{flag}")

    df_out = pd.DataFrame(rows)
    out = ROOT / "logs" / "global_country_audit.csv"
    df_out.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")

    # 推薦
    print("\n" + "=" * 95)
    print("推薦：高 CAGR + 低相關性（vs 0050）的潛力標的")
    print("=" * 95)
    df_out_sorted = df_out[
        (df_out["cagr"] >= 8) & (df_out["corr_0050"] <= 0.5)
        & (~df_out["ticker"].isin(["0050.TW"]))
    ].sort_values("cagr", ascending=False)
    print(df_out_sorted[["ticker", "name", "region", "cagr",
                          "sharpe", "corr_0050", "cagr_1y"]].to_string(index=False))

    # 1y 表現好的（可能上升趨勢）
    print("\n" + "=" * 95)
    print("近期 1 年趨勢強的（可能 momentum entry）")
    print("=" * 95)
    df_out_1y = df_out[df_out["cagr_1y"] >= 15].sort_values("cagr_1y", ascending=False)
    print(df_out_1y[["ticker", "name", "region", "cagr",
                      "cagr_1y", "corr_0050"]].head(8).to_string(index=False))


if __name__ == "__main__":
    main()
