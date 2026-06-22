"""
策略結果 pre-compute 腳本(在本機跑,需要完整 1.1GB cache)。

跑法:
    python scripts/precompute_strategy_results.py

輸出:
    data/strategy_results.json

之後 git add / commit / push → Streamlit Cloud 自動讀此 JSON,
策略市集全市場結果秒出。

7 天內的 JSON 才會被 Cloud 採用(避免過期資料誤導)。
建議:每 1-3 天跑一次,或設 Windows Task Scheduler 排程。
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
TW_OHLCV_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
FINMIND_CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
OUT_PATH = ROOT / "data" / "strategy_results.json"


# ────────────────────────────────────────────
# 7 個 scanner — 跟 app.py 邏輯一致,純 plain function
# ────────────────────────────────────────────
def scan_revenue_yoy(min_yoy=30.0, max_yoy=300.0, min_value_yi=1.0,
                      min_prev_revenue=1e7, top_n=12) -> list[dict]:
    import numpy as np
    rev_files = list(FINMIND_CACHE.glob("TaiwanStockMonthRevenue_*.parquet"))
    hits = []
    for f in rev_files:
        tk = f.stem.replace("TaiwanStockMonthRevenue_", "")
        try:
            rev = pd.read_parquet(f)
            rev["date"] = pd.to_datetime(rev["date"])
            rev = rev.sort_values("date")
            if len(rev) < 13:
                continue
            latest_rev = float(rev["revenue"].iloc[-1])
            prev_year_rev = float(rev["revenue"].iloc[-13])
            if prev_year_rev < min_prev_revenue or latest_rev <= 0:
                continue
            yoy = (latest_rev / prev_year_rev - 1) * 100
            if not np.isfinite(yoy):
                continue
            if yoy < min_yoy or yoy > max_yoy:
                continue
            ohlcv_p = TW_OHLCV_CACHE / f"{tk}.parquet"
            if not ohlcv_p.exists():
                continue
            ohlcv = pd.read_parquet(ohlcv_p)
            if len(ohlcv) < 20:
                continue
            recent = ohlcv.tail(20)
            avg_value_yi = float((recent["close"] * recent["volume"]).mean() / 1e8)
            if avg_value_yi < min_value_yi:
                continue
            hits.append({
                "tk": tk, "yoy": float(yoy),
                "avg_value_yi": avg_value_yi,
                "latest_rev_yi": latest_rev / 1e8,
            })
        except Exception:
            continue
    hits.sort(key=lambda x: x["yoy"], reverse=True)
    return hits[:top_n]


RETAIL_LEVELS = (
    "1-999", "1,000-5,000", "5,001-10,000",
    "10,001-15,000", "15,001-20,000",
    "20,001-30,000", "30,001-40,000", "40,001-50,000",
)


def scan_retail_pct(min_pct: float, max_pct: float, reverse_sort: bool,
                      min_value_yi: float, top_n: int = 12) -> list[dict]:
    files = list(FINMIND_CACHE.glob("TaiwanStockHoldingSharesPer_*.parquet"))
    hits = []
    for f in files:
        tk = f.stem.replace("TaiwanStockHoldingSharesPer_", "")
        try:
            df = pd.read_parquet(f)
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            latest_d = df["date"].max()
            sub = df[df["date"] == latest_d]
            retail_pct = float(
                sub[sub["HoldingSharesLevel"].isin(list(RETAIL_LEVELS))]["percent"].sum()
            )
            if retail_pct < min_pct or retail_pct > max_pct:
                continue
            ohlcv_p = TW_OHLCV_CACHE / f"{tk}.parquet"
            if not ohlcv_p.exists():
                continue
            oh = pd.read_parquet(ohlcv_p).tail(20)
            avg_value_yi = float((oh["close"] * oh["volume"]).mean() / 1e8)
            if avg_value_yi < min_value_yi:
                continue
            hits.append({"tk": tk, "retail_pct": retail_pct,
                          "avg_value_yi": avg_value_yi})
        except Exception:
            continue
    hits.sort(key=lambda x: x["retail_pct"], reverse=reverse_sort)
    return hits[:top_n]


def scan_ohlcv_pattern(chg_filter, vr_max=0.8, top_n=12) -> list[dict]:
    files = list(TW_OHLCV_CACHE.glob("*.parquet"))
    hits = []
    for f in files:
        tk = f.stem
        try:
            df = pd.read_parquet(f)
            if len(df) < 25:
                continue
            last3 = df.tail(3)
            for _, row in last3.iterrows():
                if row["close"] <= 0 or row["open"] <= 0:
                    continue
                chg = (row["close"] / row["open"] - 1) * 100
                if not chg_filter(chg):
                    continue
                idx_pos = df.index[df["date"] == row["date"]][0]
                if idx_pos < 20:
                    continue
                avg_vol_20 = df.iloc[idx_pos-20:idx_pos]["volume"].mean()
                if avg_vol_20 <= 0:
                    continue
                vr = row["volume"] / avg_vol_20
                if vr >= vr_max:
                    continue
                hits.append({"tk": tk, "date": str(row["date"])[:10],
                              "chg": float(chg), "vr": float(vr),
                              "close": float(row["close"])})
                break
        except Exception:
            continue
    hits.sort(key=lambda x: x["date"], reverse=True)
    return hits[:top_n]


def scan_ab_consensus(top_n=12) -> list[dict]:
    files = list(FINMIND_CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet"))
    hits = []
    for f in files:
        tk = f.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
        try:
            df = pd.read_parquet(f)
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            last20 = df["date"].unique()[-20:]
            sub = df[df["date"].isin(last20)].copy()
            sub["net"] = sub["buy"] - sub["sell"]
            agg = sub.groupby("name")["net"].sum() / 1000
            f20 = int(agg.get("Foreign_Investor", 0))
            it20 = int(agg.get("Investment_Trust", 0))
            if f20 < 5000 or it20 < 500:
                continue
            ohlcv_p = TW_OHLCV_CACHE / f"{tk}.parquet"
            if not ohlcv_p.exists():
                continue
            oh = pd.read_parquet(ohlcv_p).tail(20)
            avg_value_yi = float((oh["close"] * oh["volume"]).mean() / 1e8)
            if avg_value_yi < 1:
                continue
            hits.append({"tk": tk, "f20": f20, "it20": it20,
                          "avg_value_yi": avg_value_yi})
        except Exception:
            continue
    hits.sort(key=lambda x: x["f20"] + x["it20"], reverse=True)
    return hits[:top_n]


def scan_govbank_reverse(top_n=12) -> list[dict]:
    bank_file = ROOT / "data" / "cache" / "finmind" / "extras" / "government_bank_buysell.parquet"
    if not bank_file.exists():
        return []
    try:
        df = pd.read_parquet(bank_file)
        df["date"] = pd.to_datetime(df["date"])
        recent = df[df["date"] >= df["date"].max() - pd.Timedelta(days=30)]
        if recent.empty:
            return []
        bank_buy = recent.copy()
        bank_buy["net"] = bank_buy["buy"] - bank_buy["sell"]
        daily_bank_count = (bank_buy[bank_buy["net"] > 0]
                            .groupby(["date", "stock_id"])["bank"]
                            .nunique().reset_index())
        flagged = daily_bank_count[daily_bank_count["bank"] >= 5]
        if flagged.empty:
            return []
        hits_raw = (flagged.sort_values("date", ascending=False)
                     .drop_duplicates("stock_id"))
        result = []
        for _, row in hits_raw.head(top_n).iterrows():
            result.append({"tk": str(row["stock_id"]),
                            "date": str(row["date"])[:10],
                            "bank_count": int(row["bank"])})
        return result
    except Exception:
        return []


# ────────────────────────────────────────────
# Main
# ────────────────────────────────────────────
def main():
    print("=" * 60)
    print("策略結果 pre-compute - 用本機 cache 跑全市場")
    print("=" * 60)

    if not TW_OHLCV_CACHE.exists():
        print(f"❌ 找不到本機 cache: {TW_OHLCV_CACHE}")
        print("這個 script 需要在 dev 機跑(完整 1.1GB FinMind cache)")
        return

    n_ohlcv = len(list(TW_OHLCV_CACHE.glob("*.parquet")))
    n_inst = len(list(FINMIND_CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet")))
    n_hold = len(list(FINMIND_CACHE.glob("TaiwanStockHoldingSharesPer_*.parquet")))
    n_rev = len(list(FINMIND_CACHE.glob("TaiwanStockMonthRevenue_*.parquet")))
    print(f"📊 本機 cache: OHLCV {n_ohlcv} · 法人 {n_inst} · 散戶 {n_hold} · 月營收 {n_rev}")
    print()

    results = {}

    print("[1/7] 📈 月營收 YoY 真 alpha...", flush=True)
    results["rev_yoy"] = scan_revenue_yoy()
    print(f"  ✅ {len(results['rev_yoy'])} 檔命中")

    print("[2/7] 👥 AB 雙重共識(外資+投信)...", flush=True)
    results["ab_consensus"] = scan_ab_consensus()
    print(f"  ✅ {len(results['ab_consensus'])} 檔命中")

    print("[3/7] 🎯 量縮漲停...", flush=True)
    results["limitup_quiet"] = scan_ohlcv_pattern(lambda chg: chg >= 9.5)
    print(f"  ✅ {len(results['limitup_quiet'])} 檔命中")

    print("[4/7] 📉 量縮跌停反彈...", flush=True)
    results["limitdown_bounce"] = scan_ohlcv_pattern(lambda chg: chg <= -9.5)
    print(f"  ✅ {len(results['limitdown_bounce'])} 檔命中")

    print("[5/7] 🧠 散戶最少(法人主導)...", flush=True)
    results["low_retail"] = scan_retail_pct(0.01, 100, reverse_sort=False, min_value_yi=1.0)
    print(f"  ✅ {len(results['low_retail'])} 檔命中")

    print("[6/7] 🥬 韭菜聚集警示...", flush=True)
    results["high_retail"] = scan_retail_pct(60, 100, reverse_sort=True, min_value_yi=0.5)
    print(f"  ✅ {len(results['high_retail'])} 檔命中")

    print("[7/7] 🏦 行庫共識度反向...", flush=True)
    results["govbank_reverse"] = scan_govbank_reverse()
    print(f"  ✅ {len(results['govbank_reverse'])} 檔命中")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out_data = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "version": 1,
        "results": results,
    }
    OUT_PATH.write_text(
        json.dumps(out_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"💾 已寫入 {OUT_PATH}")
    print()
    print("下一步:")
    print("  git add data/strategy_results.json")
    print("  git commit -m 'daily strategy precompute'")
    print("  git push")
    print()
    print("Cloud 自動讀此 JSON,策略市集秒出全市場結果。")


if __name__ == "__main__":
    main()
