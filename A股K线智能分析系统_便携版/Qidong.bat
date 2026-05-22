@echo off
chcp 65001 >nul
cd /d "%~dp0"
call venv\Scripts\activate.bat
cls
echo ========================================
echo   Stock K-Line Analysis System
echo ========================================
echo.
echo Starting service...
echo First launch: 30 seconds to initialize
echo Please visit: http://localhost:8501
echo.
echo Press Ctrl+C to stop
echo.
start http://localhost:8501
streamlit run streamlit_app.py --server.port=8501 --server.headless=true
