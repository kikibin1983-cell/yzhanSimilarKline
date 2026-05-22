@echo off
chcp 65001 >nul
echo ========================================
echo   Stock K-Line Analysis System
echo   Uninstall Program
echo ========================================
echo.
echo Uninstalling...
echo.

if exist "venv" (
    echo Removing virtual environment...
    rmdir /s /q "venv"
)

if exist "__pycache__" rmdir /s /q "__pycache__"
if exist "logs" rmdir /s /q "logs"
del /q "*.pyc" 2>nul
del /q "*.log" 2>nul

echo.
echo Uninstall complete!
echo.
echo Note: Cache data preserved in:
echo %CD%
echo.
echo To fully remove, delete this folder manually.
echo.
pause
