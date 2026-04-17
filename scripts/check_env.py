"""
環境檢查腳本 — 在首次設定或排程出問題時執行，一次確認所有依賴是否就緒。

執行方式：python scripts/check_env.py
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "config" / ".env")
except ImportError:
    pass

OK   = "[OK]"
WARN = "[!!]"
FAIL = "[XX]"

def check(label: str, ok: bool, detail: str = "") -> bool:
    icon = OK if ok else FAIL
    msg = f"  {icon} {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return ok

def section(title: str) -> None:
    print(f"\n{'─'*40}\n{title}\n{'─'*40}")

all_ok = True

# ── Python 版本 ──────────────────────────
section("Python")
py_ok = sys.version_info >= (3, 10)
all_ok &= check(f"Python {sys.version.split()[0]}", py_ok,
                "" if py_ok else "需要 3.10+")

# ── 套件 ────────────────────────────────
section("套件依賴")
REQUIRED = [
    "pandas", "numpy", "pydantic", "yaml", "feedparser",
    "anthropic", "jinja2", "dotenv", "pyarrow", "requests",
    "yfinance",
]
for pkg in REQUIRED:
    mod_name = "yaml" if pkg == "yaml" else ("dotenv" if pkg == "dotenv" else pkg)
    try:
        mod = importlib.import_module(mod_name)
        ver = getattr(mod, "__version__", "?")
        all_ok &= check(pkg, True, ver)
    except ImportError:
        all_ok &= check(pkg, False, "未安裝（pip install " + pkg + "）")

# ── 設定檔案 ─────────────────────────────
section("設定檔案")
CONFIG_FILES = [
    ROOT / "config" / "strategy.yaml",
    ROOT / "config" / "watchlist.yaml",
    ROOT / "config" / "sector_map.yaml",
    ROOT / "config" / "day_trader_brokers.yaml",
    ROOT / "config" / "news_keywords.yaml",
]
for p in CONFIG_FILES:
    all_ok &= check(p.name, p.exists())

assets_json = ROOT / "data" / "assets.json"
assets_ok = assets_json.exists()
all_ok &= check("data/assets.json", assets_ok,
                "" if assets_ok else "請從 data/assets.json.example 複製並填寫")

# ── 環境變數 ─────────────────────────────
section("環境變數 (.env)")
anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
check("ANTHROPIC_API_KEY", bool(anthropic_key),
      "已設定" if anthropic_key else "未設定（情緒評分將跳過）")

finmind = os.environ.get("FINMIND_TOKEN", "")
check("FINMIND_TOKEN", bool(finmind),
      "已設定" if finmind else "未設定（籌碼資料需要此 token）")

fugle = os.environ.get("FUGLE_API_KEY", "")
check("FUGLE_API_KEY", True,
      "已設定" if fugle else "未設定（將降級為 yfinance 即時報價）")

uuid = os.environ.get("USER_UUID", "")
check("USER_UUID", True,
      "已設定（隱私模式啟用）" if uuid else "未設定（金額將明文顯示）")

# ── 目錄 ─────────────────────────────────
section("目錄結構")
DIRS = [
    ROOT / "data" / "cache",
    ROOT / "data" / "state",
    ROOT / "logs",
    ROOT / "src" / "report" / "templates",
]
for d in DIRS:
    all_ok &= check(str(d.relative_to(ROOT)), d.exists())

# ── API 連通測試（選用）──────────────────
section("API 快速連通測試")
print(f"  {WARN} 跳過 (避免乾運行耗用 API 額度)")
print("  執行以下指令可手動測試：")
print("    python scripts/morning_briefing.py --dry-run --date $(date +%Y-%m-%d)")

# ── 排程狀態 ─────────────────────────────
section("Windows Task Scheduler")
import subprocess
result = subprocess.run(
    ["schtasks", "/Query", "/TN", "TWStockMorningBrief", "/FO", "LIST"],
    capture_output=True, text=True
)
task_ok = result.returncode == 0
check("TWStockMorningBrief 排程", task_ok,
      "已建立" if task_ok else "未建立（執行 scripts/setup_windows_task.ps1）")
if task_ok:
    for line in result.stdout.splitlines():
        if "下次執行" in line or "Next Run" in line or "Status" in line or "狀態" in line:
            print(f"    {line.strip()}")

# ── 總結 ─────────────────────────────────
print("\n" + "═"*40)
if all_ok:
    print("[OK] 所有必要項目均通過，可以執行晨報。")
else:
    print("[XX] 有項目未通過，請依上方提示修正後再試。")
print("═"*40)
