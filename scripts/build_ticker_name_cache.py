"""
Build TaiwanStockInfo cache — FinMind 解封後一鍵建立 ticker → name 對照表。

存到：data/cache/finmind/finmind/TaiwanStockInfo.parquet
被 src/strategy/volume_anomaly_scanner.py:lookup_ticker_name() 使用
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

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

from src.data.finmind_client import FinMindClient  # noqa: E402

CACHE_DIR = ROOT / "data" / "cache" / "finmind" / "finmind"


def main() -> None:
    token = os.environ.get("FINMIND_TOKEN", "")
    if not token:
        print("❌ FINMIND_TOKEN not set"); return
    fc = FinMindClient(token=token, cache_dir=CACHE_DIR.parent)
    print("Fetching TaiwanStockInfo...")
    try:
        info = fc.get_all_listed_info()
    except Exception as e:
        print(f"❌ FinMind fetch failed: {e}")
        return
    if info is None or info.empty:
        print("❌ Empty response"); return
    # 標準化欄位
    if "stock_id" not in info.columns or "stock_name" not in info.columns:
        print(f"❌ Unexpected columns: {list(info.columns)}"); return

    out = CACHE_DIR / "TaiwanStockInfo.parquet"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    info.to_parquet(out, index=False)
    print(f"✅ 寫入 {out.relative_to(ROOT)}")
    print(f"   {len(info)} 檔")
    print(f"   sample: {info.head(3).to_dict(orient='records')}")


if __name__ == "__main__":
    main()
