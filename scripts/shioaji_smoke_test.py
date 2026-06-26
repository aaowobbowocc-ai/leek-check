"""
Shioaji 連線 smoke test — 驗證 API Key + Secret 可用。

執行步驟：
  1. 確認 config/.env 有 SHIOAJI_API_KEY 和 SHIOAJI_SECRET_KEY
  2. python scripts/shioaji_smoke_test.py
  3. 看到「✅ 連線成功」+ 抓到 2330 報價 = OK

第一次跑前先安裝套件：
  pip install shioaji
"""
from __future__ import annotations

import io
import os
import sys
from datetime import date, timedelta
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

from src.data.shioaji_client import ShioajiClient  # noqa: E402


def main() -> None:
    print("=" * 60)
    print("Shioaji Smoke Test")
    print("=" * 60)

    # 環境檢查
    api_key = os.environ.get("SHIOAJI_API_KEY", "")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY", "")
    if not api_key or not secret_key:
        print("❌ SHIOAJI_API_KEY 或 SHIOAJI_SECRET_KEY 未設定")
        print("   請在 config/.env 加入：")
        print("     SHIOAJI_API_KEY=你的 API Key")
        print("     SHIOAJI_SECRET_KEY=你的 Secret Key")
        return
    print(f"  API Key    : {api_key[:6]}...{api_key[-4:]} (length {len(api_key)})")
    print(f"  Secret Key : {secret_key[:4]}...{secret_key[-4:]} (length {len(secret_key)})")

    # 套件檢查
    try:
        import shioaji as sj  # noqa: F401
        print("  shioaji 套件 : ✅ installed")
    except ImportError:
        print("  ❌ shioaji 未安裝。執行：pip install shioaji")
        return

    # 第一次：simulation 環境
    print("\n[1/3] 連線 SIMULATION 環境...")
    client = ShioajiClient(simulation=True)
    if not client.connect():
        print("  ❌ 連線失敗（檢查 token 是否正確）")
        return
    print("  ✅ 連線成功")

    # 抓 2330 即時 snapshot
    print("\n[2/3] 抓 2330 (TSMC) 即時 snapshot...")
    snap = client.get_snapshot("2330")
    if snap is None:
        print("  ❌ snapshot 失敗（盤後可能無資料；盤中再試）")
    else:
        print(f"  ✅ 收盤={snap['close']}  量={snap['volume']}  "
              f"bid={snap['bid']}  ask={snap['ask']}")
        print(f"     total_vol={snap['total_volume']:,}  amount={snap['amount']:,.0f}")

    # 抓最近一週的 1 分鐘 K 線
    print("\n[3/3] 抓 2330 最近 7 日 1 分鐘 K 線...")
    end = date.today()
    start = end - timedelta(days=7)
    kbars = client.get_kbars("2330", start, end)
    if kbars:
        print(f"  ✅ 共 {len(kbars)} 筆 minute K")
        print(f"     第一筆: {kbars[0]}")
        print(f"     最後筆: {kbars[-1]}")
    else:
        print("  ❌ kbars 失敗或 0 筆")

    client.disconnect()
    print("\n" + "=" * 60)
    print("✅ Smoke test 完成")
    print("=" * 60)
    print("下一步：")
    print("  1. 確認以上都成功")
    print("  2. 等 ORB Step 2 跑完看結果")
    print("  3. 若通過 gate → 用 Shioaji 啟動 tick streaming daemon 累積資料")


if __name__ == "__main__":
    main()
