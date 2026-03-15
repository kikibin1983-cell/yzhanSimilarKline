@echo off
echo Starting installation...
echo.

cd /d "%~dp0"

echo Step 1: Check Python
python --version
if errorlevel 1 (
    echo ERROR: Python not found
    echo Please install Python first
    pause
    exit /b 1
)

echo.
echo Step 2: Create virtual environment
if not exist venv (
    python -m venv venv
    echo Virtual environment created
) else (
    echo Virtual environment exists
)

echo.
echo Step 3: Install packages
echo This will take a few minutes...
venv\Scripts\pip.exe install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
venv\Scripts\pip.exe install streamlit==1.54.0 -i https://pypi.tuna.tsinghua.edu.cn/simple
venv\Scripts\pip.exe install pandas==2.2.3 -i https://pypi.tuna.tsinghua.edu.cn/simple
venv\Scripts\pip.exe install numpy==1.26.4 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo Step 4: Create launcher
(
echo @echo off
echo venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.port 8501 --server.address localhost
echo pause
) > run_app.bat

echo.
echo Installation complete!
echo.
echo To run the app:
echo 1. Double-click run_app.bat
echo 2. Open browser to http://localhost:8501
echo.
echo Press any key to test...
pause

echo Testing...
venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.port 8501 --server.address localhost --browser.gatherUsageStats false