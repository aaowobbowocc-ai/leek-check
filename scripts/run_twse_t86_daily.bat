@echo off
REM TWSE T86 daily backfill — 排程在每天 18:30 跑
REM 抓最新 1 天（如果 weekend 自動跳過）

cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

REM 抓最近 3 天（含當日 + 補缺）
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\twse_t86_realtime.py --backfill 3 >> logs\twse_t86_daily.log 2>&1

echo [%date% %time%] T86 daily backfill done >> logs\twse_t86_daily.log
