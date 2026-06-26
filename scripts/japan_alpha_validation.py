"""
日股 DXJ alpha 驗證 — 免費 yfinance 資料。

要驗證的 claim：
  「DXJ 10 年 +359%, alpha vs EWJ +8.3pp/yr」（commit ac63af5 紙上談兵）

驗證項目：
  1. DXJ vs EWJ vs 0050 過去 15 年 CAGR / Sharpe / MDD
  2. DXJ 拆解：DXJ (hedged) vs EWJ (unhedged JPY) → currency hedge effect
  3. DXJ vs 1306.T (純 TOPIX ETF) → governance reform effect
  4. Rolling 5-year alpha 穩定性
  5. 跟 0050 的相關性（測「分散」claim）

決策影響：
  - 若 alpha 真且穩 → 8% DXJ 配置合理，可考慮提高
  - 若 alpha 假或不穩 → 需重新檢視全球配置
  - 若 alpha 完全來自 currency → 日圓反轉時消失，謹慎
"""
from __future__ import annotations

import io
import sys
from datetime import date
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

TICKERS = {
    "DXJ":     "WisdomTree Japan Hedged",       # 日圓 hedged 日股
    "EWJ":     "iShares MSCI Japan",            # 日圓 unhedged 日股大盤
    "1306.T":  "Nomura TOPIX ETF (JPY)",        # 日本本土 TOPIX (JPY 計價)
    "0050.TW": "元大台 50",                     # TW baseline
    "TSM":     "TSMC ADR (USD)",                # TSMC USD 計價
    "SPY":     "S&P 500",                       # 美股大盤
    "USDJPY=X": "USD/JPY",                      # 匯率（看日圓走勢）
}

START = "2010-01-01"
END = date.today().isoformat()


def load_or_fetch(ticker: str) -> pd.DataFrame:
    cache_p = CACHE / f"{ticker.replace('.', '_').replace('=', '_')}.parquet"
    if cache_p.exists():
        return pd.read_parquet(cache_p)
    import yfinance as yf
    print(f"  fetching {ticker}...")
    df = yf.download(ticker, start=START, end=END, auto_adjust=True, progress=False)
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


def cagr(prices: pd.Series, dates: pd.Series) -> float:
    if len(prices) < 2:
        return 0.0
    n_years = (dates.iloc[-1] - dates.iloc[0]).days / 365.25
    if n_years <= 0:
        return 0.0
    return ((prices.iloc[-1] / prices.iloc[0]) ** (1 / n_years) - 1) * 100


def sharpe(returns: pd.Series) -> float:
    if returns.std() == 0:
        return 0.0
    return returns.mean() / returns.std() * np.sqrt(252)


def mdd(prices: pd.Series) -> float:
    peak = prices.cummax()
    return ((prices - peak) / peak).min() * 100


def main() -> None:
    print("=" * 80)
    print("日股 DXJ alpha 驗證")
    print("=" * 80)

    # 1. 載入資料
    print("\n[1/5] 載入資料...")
    data: dict[str, pd.DataFrame] = {}
    for tk, desc in TICKERS.items():
        df = load_or_fetch(tk)
        if df.empty:
            print(f"  ❌ {tk} ({desc}): 無資料")
            continue
        data[tk] = df
        print(f"  ✅ {tk:<10} {desc:<25}: {len(df)} rows, {df['date'].min()} ~ {df['date'].max()}")

    if "DXJ" not in data or "EWJ" not in data:
        print("\n❌ DXJ 或 EWJ 缺資料，無法驗證")
        return

    # 2. 對齊期間 + 計算 daily return
    print("\n[2/5] 對齊期間 + 計算 daily return...")
    common_start = max(data[tk]["date"].min() for tk in data)
    common_end = min(data[tk]["date"].max() for tk in data)
    print(f"  共同期間: {common_start} ~ {common_end}")

    aligned: dict[str, pd.DataFrame] = {}
    for tk in data:
        df = data[tk][(data[tk]["date"] >= common_start) & (data[tk]["date"] <= common_end)].copy()
        df["daily_ret"] = df["close"].pct_change()
        aligned[tk] = df

    # 3. 整體指標
    print("\n[3/5] 各標的全期 CAGR / Sharpe / MDD")
    print(f"  {'標的':<10} {'CAGR':>8} {'Sharpe':>7} {'MDD':>8} {'累積報酬':>12}")
    print("  " + "-" * 55)
    summary = {}
    for tk in TICKERS:
        if tk not in aligned:
            continue
        df = aligned[tk]
        c = cagr(df["close"], df["date"])
        s = sharpe(df["daily_ret"].dropna())
        m = mdd(df["close"])
        total = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
        summary[tk] = {"cagr": c, "sharpe": s, "mdd": m, "total": total}
        print(f"  {tk:<10} {c:>+7.2f}% {s:>7.2f} {m:>+7.2f}% {total:>+11.1f}%")

    # 4. DXJ alpha 拆解
    print("\n[4/5] DXJ alpha 拆解")
    print("  ─" * 40)
    if "DXJ" in summary and "EWJ" in summary:
        alpha_vs_ewj = summary["DXJ"]["cagr"] - summary["EWJ"]["cagr"]
        print(f"  DXJ - EWJ (hedged - unhedged)  : {alpha_vs_ewj:+.2f}pp/yr")
        print(f"     → 此差異 = currency hedge 貢獻（日圓貶值順風）")
    if "DXJ" in summary and "1306.T" in summary:
        alpha_vs_tpx = summary["DXJ"]["cagr"] - summary["1306.T"]["cagr"]
        print(f"  DXJ - 1306.T (USD - JPY 計價)   : {alpha_vs_tpx:+.2f}pp/yr")
        print(f"     → 兩者都是日股，差異主要為 currency translation")
    if "DXJ" in summary and "0050.TW" in summary:
        alpha_vs_tw = summary["DXJ"]["cagr"] - summary["0050.TW"]["cagr"]
        print(f"  DXJ - 0050     (vs 台股大盤)   : {alpha_vs_tw:+.2f}pp/yr")
        print(f"     → 全球配置決策關鍵：日股是否值得分散到 8%")
    if "EWJ" in summary and "1306.T" in summary:
        diff = summary["EWJ"]["cagr"] - summary["1306.T"]["cagr"]
        print(f"  EWJ - 1306.T (USD - JPY 計價)  : {diff:+.2f}pp/yr")
        print(f"     → 兩者都是 unhedged 日股，差異 ≈ JPY/USD 漲跌")

    # 5. Rolling 5-year alpha
    print("\n[5/5] Rolling 5-year alpha (DXJ vs 0050)")
    print("  ─" * 40)
    if "DXJ" in aligned and "0050.TW" in aligned:
        # 對齊
        dxj = aligned["DXJ"][["date", "close"]].rename(columns={"close": "dxj"})
        tw = aligned["0050.TW"][["date", "close"]].rename(columns={"close": "tw"})
        merged = pd.merge(dxj, tw, on="date", how="inner").sort_values("date")
        merged["dxj_ret"] = merged["dxj"].pct_change()
        merged["tw_ret"] = merged["tw"].pct_change()

        # 5y rolling annualized return
        window = 252 * 5
        if len(merged) > window:
            merged["dxj_5y_cagr"] = ((merged["dxj"] / merged["dxj"].shift(window)) ** (1 / 5) - 1) * 100
            merged["tw_5y_cagr"] = ((merged["tw"] / merged["tw"].shift(window)) ** (1 / 5) - 1) * 100
            merged["alpha"] = merged["dxj_5y_cagr"] - merged["tw_5y_cagr"]
            valid = merged.dropna(subset=["alpha"])
            print(f"  Rolling 5y alpha 統計（n={len(valid)} days）:")
            print(f"    平均 alpha :   {valid['alpha'].mean():+.2f}pp/yr")
            print(f"    中位數 alpha:  {valid['alpha'].median():+.2f}pp/yr")
            print(f"    最大 alpha :   {valid['alpha'].max():+.2f}pp/yr")
            print(f"    最小 alpha :   {valid['alpha'].min():+.2f}pp/yr")
            print(f"    alpha > 0  :   {(valid['alpha'] > 0).mean() * 100:.1f}% 的時間 DXJ 贏 0050")

    # 相關性（看「分散」claim）
    print("\n[相關性]")
    print("  ─" * 40)
    for pair_a, pair_b in [("DXJ", "0050.TW"), ("EWJ", "0050.TW"), ("DXJ", "EWJ"), ("DXJ", "USDJPY=X")]:
        if pair_a in aligned and pair_b in aligned:
            a = aligned[pair_a][["date", "daily_ret"]].rename(columns={"daily_ret": "a"})
            b = aligned[pair_b][["date", "daily_ret"]].rename(columns={"daily_ret": "b"})
            m = pd.merge(a, b, on="date", how="inner").dropna()
            if len(m) > 100:
                corr = m["a"].corr(m["b"])
                print(f"  {pair_a:<10} vs {pair_b:<10} corr = {corr:+.3f}")

    # 寫出 summary
    out = ROOT / "logs" / "japan_alpha_summary.csv"
    pd.DataFrame(summary).T.to_csv(out, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
