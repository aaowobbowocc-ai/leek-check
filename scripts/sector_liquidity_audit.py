"""
Sector Liquidity Audit — 算每個族群成員的日成交額，標記當沖可行性。

當沖門檻（保守版）：
  - 日成交額 ≥ NT$ 5 億：✅ 流動性足，可當沖
  - 1 億 ~ 5 億：⚠️ 邊緣，每筆 size 要小
  - < 1 億：❌ 不適合當沖（一兩百萬一筆會被滑價吃掉）

對每個 sector 計算：
  - 各成員過去 60 日平均日成交額
  - 該 sector 「當沖可行 leader 數量」
  - 該 sector 整體標記
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import yaml

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"

# 門檻 (NT$)
TIER_HIGH = 500_000_000   # 5 億 ≥ 流動性高
TIER_MID = 100_000_000    # 1-5 億 ⚠️ 邊緣
# < 1 億 = 不適合


def compute_avg_turnover(ticker: str, n_days: int = 60) -> tuple[float, int]:
    """回傳 (平均日成交額 NT$, 樣本天數)。"""
    f = CACHE_YF / f"{ticker}.parquet"
    if not f.exists():
        return 0.0, 0
    try:
        df = pd.read_parquet(f)
    except Exception:
        return 0.0, 0
    if df.empty:
        return 0.0, 0
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").tail(n_days)
    if df.empty:
        return 0.0, 0
    # 成交額 = close × volume（yfinance 的 volume 是「股」數，1 張 = 1000 股）
    # 但對 turnover 而言用 share-level OK
    turnover = (df["close"].astype(float) * df["volume"].astype(float)).mean()
    return float(turnover), len(df)


def tier(turnover: float) -> str:
    if turnover >= TIER_HIGH:
        return "✅ 高"
    elif turnover >= TIER_MID:
        return "⚠️ 中"
    else:
        return "❌ 低"


def main() -> None:
    sl = yaml.safe_load((ROOT / "config" / "sector_leaders.yaml").read_text(encoding="utf-8"))

    sector_summary = []
    print("=" * 95)
    print("各 Sector Liquidity Audit (過去 60 日平均日成交額)")
    print("=" * 95)

    for sec_key, sec in sl["sectors"].items():
        print(f"\n## {sec_key} ({sec.get('name', '-')})")
        members = []
        for entry in sec.get("leader_chain", []):
            if "_" not in entry:
                continue
            code, name = entry.split("_", 1)
            if code == "?":
                continue
            turnover, n = compute_avg_turnover(code)
            members.append({
                "code": code, "name": name,
                "turnover_twd": turnover, "n_days": n,
                "tier": tier(turnover),
            })

        for m in members:
            t = m["turnover_twd"] / 1e8     # 億
            print(f"  {m['code']:<6} {m['name']:<10s} {t:>8.2f} 億   {m['tier']}   "
                  f"({m['n_days']} days sample)")

        # Sector 評等
        n_high = sum(1 for m in members if m["tier"] == "✅ 高")
        n_mid = sum(1 for m in members if m["tier"] == "⚠️ 中")
        n_low = sum(1 for m in members if m["tier"] == "❌ 低")
        n_no_data = sum(1 for m in members if m["n_days"] == 0)
        n_total = len(members)

        if n_high >= 2:
            sec_grade = "🟢 適合當沖（≥2 高流動 leader）"
        elif n_high + n_mid >= 2:
            sec_grade = "🟡 邊緣可當沖（leader 流動性中等）"
        else:
            sec_grade = "🔴 不適合當沖（流動性不足）"
        print(f"  → {sec_grade} | high={n_high} mid={n_mid} low={n_low} no_data={n_no_data}")

        sector_summary.append({
            "sector": sec_key, "name": sec.get("name", "-"),
            "n_total": n_total, "n_high": n_high, "n_mid": n_mid, "n_low": n_low,
            "n_no_data": n_no_data, "grade": sec_grade,
        })

    # 全表
    print("\n" + "=" * 95)
    print("Sector Summary（按可當沖排序）")
    print("=" * 95)
    df = pd.DataFrame(sector_summary)
    df["score"] = df["n_high"] * 2 + df["n_mid"]
    df = df.sort_values("score", ascending=False)
    print(f"  {'sector':<22s} {'高':>3} {'中':>3} {'低':>3} 評等")
    for _, r in df.iterrows():
        print(f"  {r['sector']:<22s} {r['n_high']:>3d} {r['n_mid']:>3d} {r['n_low']:>3d} {r['grade']}")

    out = ROOT / "logs" / "sector_liquidity_audit.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n寫入 {out.relative_to(ROOT)}")

    # 終極建議
    print("\n" + "=" * 95)
    print("當沖 sector 推薦")
    print("=" * 95)
    viable = df[df["grade"].str.startswith("🟢")]
    print(f"🟢 適合當沖的 sector: {len(viable)} 個")
    print(viable[["sector", "name", "n_high", "n_mid"]].to_string(index=False))


if __name__ == "__main__":
    main()
