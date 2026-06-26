@echo off
REM 每日 AI cache 生成 — 排 3 次 (07:30, 14:00, 20:30 台灣時間)
REM 用法: Task Scheduler 新增 3 個 trigger 排這支 .bat
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0\.."
python -m backend.jobs.daily_ai_cache >> data\logs\daily_ai_cache.log 2>&1
