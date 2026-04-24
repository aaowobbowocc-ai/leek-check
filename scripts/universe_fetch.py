"""
Universe Fetcher (Phase 16 基礎設施) — 從 FinMind 拉台股全市場 ticker 清單。

輸出：
  config/universe_all.yaml  — 全部上市 + 上櫃股票（約 1800 檔）
  config/universe_mid_cap.yaml — 中型股（市值 100-1000 億，約 200-300 檔）

下游：
  scripts/quality_momentum_backtest.py（Phase 16 下一步）會讀 mid_cap 跑月度掃描。

注意：
  FinMind TaiwanStockInfo 不含市值，市值篩選需要用 TaiwanStockInfoWithWarrant
  或結合 yfinance 拿 market cap。MVP 階段先輸出 all，市值過濾留 backtest 階段做。

用法：
  python scripts/universe_fetch.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

from src.data.finmind_client import FinMindClient


def main() -> None:
    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("[錯誤] FINMIND_TOKEN 未設定，請先在 config/.env 填入")
        sys.exit(1)

    print("[1/2] 從 FinMind 抓 TaiwanStockInfo...")
    client = FinMindClient(token=token)
    df = client.get_all_listed_info()
    if df.empty:
        print("[錯誤] 回傳空資料，可能是 API 額度或 token 問題")
        sys.exit(1)

    print(f"    拉到 {len(df)} 檔（含上市 + 上櫃）")
    # 分類統計
    if "type" in df:
        print("    類型分布：")
        for typ, cnt in df["type"].value_counts().items():
            print(f"      {typ}: {cnt}")

    # 過濾：排除 ETF / 特別股 / 權證 等非普通股
    # 台股 stock_id 通常 4 碼：
    #   1000-3999 + 某些特殊範圍 = 普通股
    #   50xx / 006xx = ETF
    #   5xxxx = 興櫃
    print("[2/2] 過濾：保留普通上市 / 上櫃股（4 碼代號、數字開頭）")
    normal = df[df["stock_id"].str.len() == 4].copy()
    normal = normal[normal["stock_id"].str[0].str.isdigit()]

    # 排除明顯 ETF / 特殊
    etf_prefixes = ("00",)         # 台股 ETF 固定 00 開頭
    etfs = normal[normal["stock_id"].str.startswith(etf_prefixes)]
    stocks = normal[~normal["stock_id"].str.startswith(etf_prefixes)]

    print(f"    普通股：{len(stocks)} 檔")
    print(f"    ETF：{len(etfs)} 檔")

    # 寫出兩份 yaml
    all_path = ROOT / "config" / "universe_all.yaml"
    tickers = sorted(stocks["stock_id"].unique())
    all_path.write_text(
        yaml.safe_dump(
            {
                "description": "台股全市場普通股（Phase 16 Quality Momentum universe）",
                "count": len(tickers),
                "tickers": tickers,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    print(f"\n寫入：{all_path} ({len(tickers)} 檔)")

    # 依產業分組（給未來可能的產業過濾用）
    if "industry_category" in stocks:
        by_industry: dict[str, list[str]] = {}
        for _, row in stocks.iterrows():
            cat = row.get("industry_category", "其他") or "其他"
            by_industry.setdefault(cat, []).append(row["stock_id"])
        industry_path = ROOT / "config" / "universe_by_industry.yaml"
        industry_path.write_text(
            yaml.safe_dump(
                {cat: sorted(ts) for cat, ts in by_industry.items()},
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        print(f"寫入：{industry_path}（{len(by_industry)} 個產業）")

    print("\n✅ 完成。下一步（Phase 16 下次執行）：")
    print("  1. 建 scripts/quality_momentum_backtest.py 讀這份 universe")
    print("  2. 對每檔跑 compute_ticker_factors() → 橫斷面 z-score 合成")
    print("  3. 月選 top 20，回測 2020-2026 比較 0050")


if __name__ == "__main__":
    main()
