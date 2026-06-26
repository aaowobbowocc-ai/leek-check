@echo off
REM Streamlit web dashboard — accessible from phone via LAN.
cd /d C:\Users\USER\Desktop\INVEST
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo.
echo ============================================
echo INVEST Web Dashboard
echo ============================================
echo.
echo Local: http://localhost:8501
echo LAN:   http://%COMPUTERNAME%.local:8501
echo.
echo Find your IP: ipconfig | findstr IPv4
echo Phone same WiFi: http://YOUR_IP:8501
echo.
echo Press Ctrl+C to stop
echo ============================================
echo.

"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" -m streamlit run scripts\web_dashboard.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
