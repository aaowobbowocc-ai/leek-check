"""寫桌面 bat（CRLF + 純 ASCII bat 內容）。"""
from pathlib import Path

PROJECT = r"C:\Users\USER\Desktop\INVEST"
PYTHONW = r"C:\Users\USER\AppData\Local\Programs\Python\Python312\pythonw.exe"
PYTHON = r"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe"

# 主入口 — GUI 儀表板（涵蓋所有功能）
# 用實際安裝的 Python（避免 WindowsApps stub 靜默失敗）
# 失敗時切回 console 模式顯示錯誤
GUI_BAT = (
    "@echo off\r\n"
    "chcp 65001 >nul 2>&1\r\n"
    "title INVEST Dashboard\r\n"
    f'cd /d "{PROJECT}"\r\n'
    f'"{PYTHONW}" scripts\\dashboard_gui.py\r\n'
    "if errorlevel 1 (\r\n"
    "  echo.\r\n"
    "  echo GUI failed. Showing errors with console:\r\n"
    f'  "{PYTHON}" scripts\\dashboard_gui.py\r\n'
    "  pause\r\n"
    ")\r\n"
)

# CLI 備援（GUI 開不了用）— rich-based 文字儀表板
CLI_BAT = (
    "@echo off\r\n"
    "chcp 65001 >nul 2>&1\r\n"
    "title INVEST CLI fallback\r\n"
    f'cd /d "{PROJECT}"\r\n'
    f'"{PYTHON}" scripts\\holdings_status.py\r\n'
    "echo.\r\n"
    "pause\r\n"
)

desktop = Path(r"C:\Users\USER\Desktop")

# 清掉舊的
for old in ["INVEST 開盤儀表板.bat", "INVEST 看一眼.bat", "INVEST 立刻執行.bat"]:
    p = desktop / old
    if p.exists():
        p.unlink()
        print(f"  removed {old}")

(desktop / "INVEST 儀表板.bat").write_bytes(GUI_BAT.encode("utf-8"))
(desktop / "INVEST CLI 備援.bat").write_bytes(CLI_BAT.encode("utf-8"))
print("  wrote INVEST 儀表板.bat (GUI)")
print("  wrote INVEST CLI 備援.bat (cmd fallback)")
print("\n完成。雙擊 INVEST 儀表板.bat 開 GUI。")
