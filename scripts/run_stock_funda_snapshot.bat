@echo off
REM 每日抓 MOPS 月營收 + TWSE PER → 算 YoY → 上傳 Supabase
REM Task Scheduler 排每天 03:00
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0\.."
REM 1. backfill 最新 MOPS(最近 3 個月更新)
python -m scripts.fetch_mops_revenue --backfill 3 >> data\logs\stock_funda.log 2>&1
REM 2. 順便更新 TWSE PER cache
python -m backend.jobs.twse_daily_etl >> data\logs\stock_funda.log 2>&1
REM 3. 上傳整包 funda snapshot
python scripts\update_stock_funda_snapshot.py >> data\logs\stock_funda.log 2>&1
