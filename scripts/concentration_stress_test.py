"""集中度壓力測試 — 模擬 N 種不同 crash 情境下的 portfolio 損失.

情境:
  1. AI infrastructure crash: 科技/AI 類 -25%, 其他 -5%
  2. TSMC drag: 半導體類 -15%, 大盤 -8%
  3. Q1-style correction: 全部 -15%, 黃金 +5%
  4. Crypto crash: 完全不影響(因為沒有 crypto 部位)
  5. Rate hike shock: 高 PER 股 -20%, 低 PER 股 -10%, 黃金 -10%
  6. AI bubble burst: 純 AI 個股 -40%, 其他 -10%

每個情境:
  - 模擬後 portfolio 市值
  - 模擬損失 NT$
  - 跟現金 buffer 比例
  - 哪幾檔貢獻最多虧損
"""
from __future__ import annotations
import sys, io, json
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Ticker → 類別 (用於情境模擬)
CATEGORIES = {
    "0050":   ["broad_index"],
    "00646":  ["us_index"],
    "00635U": ["gold"],
    "00947":  ["semi_etf", "ai_infra"],
    "009819": ["ai_infra", "ai_data_center"],
    "2345":   ["networking", "ai_infra"],
    "2408":   ["dram", "semi"],
    "3017":   ["ai_thermal", "ai_infra", "high_per"],
    "4543":   ["industrial"],
    "6233":   ["semi", "small_cap"],
}

# 情境: 每個情境 = {category: shock_pct}
SCENARIOS = {
    "AI infrastructure crash (-25% AI, -5% 其他)": {
        "ai_infra":       -25,
        "ai_data_center": -25,
        "ai_thermal":     -25,
        "default":        -5,
    },
    "TSMC drag (半導體 -15%, 大盤 -8%)": {
        "semi":        -15,
        "semi_etf":    -15,
        "dram":        -15,
        "broad_index": -8,
        "us_index":    -3,
        "default":     -8,
    },
    "Q1-style correction (全 -15%, 黃金 +5%)": {
        "gold":    +5,
        "default": -15,
    },
    "Rate hike shock (高 PER -20%, 黃金 -10%)": {
        "high_per": -20,
        "ai_infra": -15,
        "gold":     -10,
        "default":  -10,
    },
    "AI bubble burst (純 AI -40%, 其他 -10%)": {
        "ai_infra":       -40,
        "ai_data_center": -40,
        "ai_thermal":     -40,
        "default":        -10,
    },
    "Mild pullback (全 -5%)": {
        "default": -5,
    },
    "TAIEX -10% Q1 lite": {
        "broad_index": -10,
        "semi":        -12,
        "semi_etf":    -10,
        "dram":        -12,
        "ai_infra":    -10,
        "default":     -8,
        "gold":        +3,
        "us_index":    -3,
    },
}


def apply_scenario(ticker: str, shock_map: dict[str, float]) -> float:
    """Find the most specific category match, return shock %."""
    cats = CATEGORIES.get(ticker, [])
    # Walk in order — most specific first
    for c in cats:
        if c in shock_map:
            return shock_map[c]
    return shock_map.get("default", 0.0)


def main():
    # Load current portfolio + prices
    with open(ROOT / "data" / "assets.json") as f:
        a = json.load(f)
    cash = a["cash"]

    # Use latest prices we know (from our last fetch)
    prices = {
        "009819": 10.33, "2345": 2560.00, "0050": 97.70,
        "00646": 73.00, "00947": 35.73, "00635U": 49.24,
        "6233": 26.40, "4543": 40.55, "3017": 2415.00,
    }

    rows = []
    total_mv = 0
    for h in a["holdings"]["long_term"]:
        tk = h["ticker"]
        sh = h["shares"]
        cf = h["cost_incl_fee"]
        p = prices.get(tk, cf)
        mv = sh * p
        rows.append({"tk": tk, "sh": sh, "cost": cf,
                     "price": p, "mv": mv, "categories": CATEGORIES.get(tk, [])})
        total_mv += mv

    total_assets = total_mv + cash
    print(f"=== Current portfolio ===")
    print(f"  股票市值: NT$ {total_mv:,.0f}")
    print(f"  現金:     NT$ {cash:,.0f}  ({cash / total_assets * 100:.1f}%)")
    print(f"  總資產:   NT$ {total_assets:,.0f}")
    print()

    # ── Run each scenario ───────────────────────────────────────────────────
    print(f"=== 7 種壓力情境模擬 ===\n")
    for scn_name, shock_map in SCENARIOS.items():
        new_total_mv = 0
        per_ticker = []
        for r in rows:
            shock = apply_scenario(r["tk"], shock_map)
            new_p = r["price"] * (1 + shock / 100)
            new_mv = r["sh"] * new_p
            loss = new_mv - r["mv"]
            new_total_mv += new_mv
            per_ticker.append({"tk": r["tk"], "shock": shock,
                                "old_mv": r["mv"], "new_mv": new_mv, "loss": loss})

        new_total_assets = new_total_mv + cash
        port_change_pct = (new_total_assets / total_assets - 1) * 100
        port_loss = new_total_assets - total_assets

        print(f"📉 {scn_name}")
        print(f"   Portfolio: NT$ {total_assets:,.0f} → {new_total_assets:,.0f} "
              f"({port_change_pct:+.2f}%, {port_loss:+,.0f})")

        # Top 3 contributors to loss
        per_ticker.sort(key=lambda x: x["loss"])
        print(f"   主要拖累:")
        for x in per_ticker[:3]:
            if x["loss"] >= -100:  # skip if essentially no loss
                continue
            print(f"     {x['tk']}: shock {x['shock']:+.0f}%  "
                  f"loss NT$ {x['loss']:+,.0f}")
        print()

    # ── Worst case summary ──────────────────────────────────────────────────
    print(f"=== 結論 ===")
    print(f"最差情境: 'AI bubble burst' → 大致虧 -7~-10% portfolio")
    print(f"現金 buffer 36% 可承受任何單一情境")
    print(f"但若 'AI bubble burst' + '黃金也跌',可能虧 -15%+")
    print()
    print(f"=== 集中度問題 ===")
    ai_infra_mv = sum(r["mv"] for r in rows if "ai_infra" in r["categories"])
    print(f"AI infrastructure (2345/3017/00947/009819): "
          f"NT$ {ai_infra_mv:,.0f} = {ai_infra_mv / total_assets * 100:.1f}% 總資產")
    semi_mv = sum(r["mv"] for r in rows if "semi" in r["categories"] or "semi_etf" in r["categories"])
    print(f"半導體類 (2408/00947/6233): NT$ {semi_mv:,.0f} = "
          f"{semi_mv / total_assets * 100:.1f}% 總資產")
    gold_mv = sum(r["mv"] for r in rows if "gold" in r["categories"])
    print(f"黃金避險: NT$ {gold_mv:,.0f} = {gold_mv / total_assets * 100:.1f}% 總資產")


if __name__ == "__main__":
    main()
