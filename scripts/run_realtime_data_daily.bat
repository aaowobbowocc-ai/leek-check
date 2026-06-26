@echo off
REM 每日 realtime 資料抓取（取代 FinMind 訂閱）
REM 排程：每日 18:30 跑一次

cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo [%date% %time%] === Daily realtime data fetch ===

REM 1. TWSE T86 法人買賣超（個股級）
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\twse_t86_realtime.py --backfill 3 >> logs\realtime_data.log 2>&1
echo [%date% %time%] TWSE T86 done >> logs\realtime_data.log

REM 2. TAIFEX 期貨法人未平倉（外資 TX z-score）
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" scripts\taifex_futures_inst_realtime.py --backfill 3 >> logs\realtime_data.log 2>&1
echo [%date% %time%] TAIFEX done >> logs\realtime_data.log

echo [%date% %time%] === Done === >> logs\realtime_data.log
