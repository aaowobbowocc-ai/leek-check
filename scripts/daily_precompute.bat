@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM 韭菜健檢 — 策略結果 pre-compute + 自動推上 GitHub
REM 雙擊或 Windows Task Scheduler 排程都可
REM ============================================================

REM 切到 repo root(這個 .bat 在 scripts/ 內)
cd /d "%~dp0.."

echo ============================================================
echo  🩺 韭菜健檢 — 策略 pre-compute 開始
echo  Time:  %date% %time%
echo ============================================================
echo.

REM 1. 跑 Python script
echo [1/3] 跑 scripts/precompute_strategy_results.py ...
python scripts\precompute_strategy_results.py
if errorlevel 1 (
    echo.
    echo ❌ Python script 跑失敗,中止
    pause
    exit /b 1
)
echo.

REM 2. Git add + 偵測是否有變化
echo [2/3] Git stage strategy_results.json ...
git add data/strategy_results.json

REM 檢查暫存區有沒有變更(--quiet 沒變化 = exit 0,有變化 = exit 1)
git diff --cached --quiet
if errorlevel 1 (
    echo    ✅ 偵測到變更,準備 commit
) else (
    echo    📋 沒新變化(資料跟上次一樣),跳過 commit
    echo.
    echo ============================================================
    echo  ✅ 完成 — 無需推送
    echo ============================================================
    if "%1"=="auto" exit /b 0
    pause
    exit /b 0
)
echo.

REM 3. Commit + push
echo [3/3] Git commit + push ...
git commit -m "data: daily strategy precompute %date% %time%"
if errorlevel 1 (
    echo ❌ git commit 失敗
    pause
    exit /b 1
)

git push origin master
if errorlevel 1 (
    echo ❌ git push 失敗(可能網路問題,或 remote 有衝突)
    echo    可手動 git pull --rebase + git push 後重跑
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  ✅ 完成!Cloud rebuild 中,~2 分鐘後策略市集自動更新
echo ============================================================
echo.

REM 排程模式(Task Scheduler 呼叫時帶 "auto" arg)不 pause
if "%1"=="auto" exit /b 0
pause
