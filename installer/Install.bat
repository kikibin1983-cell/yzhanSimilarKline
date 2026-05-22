@echo off
chcp 65001 >nul
title Stock Analysis System - Installer
echo ========================================
echo   Stock K-Line Analysis System
echo   Installation Program
echo ========================================
echo.

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

echo [Step 1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [INFO] Python not found. Downloading...
    echo.
    echo Downloading Python 3.11...
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\python_installer.exe'"
    if not exist "%TEMP%\python_installer.exe" (
        echo [ERROR] Download failed!
        echo Please download Python manually:
        echo https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo Installing Python (please wait)...
    "%TEMP%\python_installer.exe"
    echo.
    echo Please run this installer again.
    pause
    exit /b 0
)

python --version
echo [OK] Python found

echo.
echo [Step 2/5] Creating virtual environment...
if not exist "venv" (
    echo Creating venv...
    python -m venv venv
)
echo [OK] venv ready

echo.
echo [Step 3/5] Installing dependencies...
echo This may take 5-10 minutes, please wait...
echo.
call venv\Scripts\activate.bat
echo Upgrading pip...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple >nul
echo Installing packages...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo [OK] Dependencies installed

echo.
echo [Step 4/5] Creating launcher...
(
    echo @echo off
    echo chcp 65001 ^>nul
    echo cd /d "%APP_DIR%"
    echo call venv\Scripts\activate.bat
    echo cls
    echo echo ========================================
    echo echo   Stock K-Line Analysis System
    echo echo ========================================
    echo echo.
    echo echo Starting service...
    echo echo First launch: 30 seconds to initialize
    echo echo Please visit: http://localhost:8501
    echo echo.
    echo echo Press Ctrl+C to stop
    echo echo.
    echo start http://localhost:8501
    echo streamlit run streamlit_app.py --server.port=8501 --server.headless=true
) > "Qidong.bat"
echo [OK] Launcher ready

echo.
echo ========================================
echo   INSTALLATION COMPLETE!
echo ========================================
echo.
echo How to use:
echo   1. Double-click "Qidong.bat" to start
echo   2. Browser will open automatically
echo   3. Default: http://localhost:8501
echo.
echo Read "README_CN.txt" for Chinese instructions.
echo.
pause
