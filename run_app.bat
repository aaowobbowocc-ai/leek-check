@echo off
REM Streamlit App launcher
cd /d C:\Users\USER\Desktop\INVEST
echo Starting INVEST Streamlit App...
echo URL will open at http://localhost:8501
echo Press Ctrl+C to stop
echo.
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" -m streamlit run app/app.py
