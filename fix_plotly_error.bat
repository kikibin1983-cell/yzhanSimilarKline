@echo off
echo ========================================
echo   FIX: ModuleNotFoundError: No module named 'plotly'
echo ========================================
echo.
echo Error: File "O:\WXJS\yzhanSimilarKline\streamlit_app.py", line 9
echo        import plotly.graph_objects as go
echo.
echo This script will fix the plotly module issue.
echo.

cd /d "%~dp0"

echo Step 1: Check Python
python --version
if errorlevel 1 (
    echo ERROR: Python not found
    echo Install Python from: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo.
echo Step 2: Check plotly
python -c "import plotly" 2>nul
if errorlevel 1 (
    echo Installing plotly...
    python -m pip install plotly==5.24.1
) else (
    echo plotly already installed
    python -c "import plotly; print('Version:', plotly.__version__)"
)

echo.
echo Step 3: Install all dependencies (optional)
echo Install all packages from requirements.txt? (Y/N)
set /p choice=
if /i "%choice%"=="Y" (
    if exist requirements.txt (
        echo Installing all dependencies...
        python -m pip install -r requirements.txt
    ) else (
        echo requirements.txt not found
    )
)

echo.
echo Step 4: Test the fix
echo Testing plotly import...
python -c "import plotly.graph_objects as go; print('SUCCESS: plotly.graph_objects imported')"

echo.
echo ========================================
echo   FIX COMPLETE
echo ========================================
echo.
echo Now run your app with:
echo   python streamlit_app.py
echo.
echo Or with streamlit:
echo   streamlit run streamlit_app.py
echo.
pause