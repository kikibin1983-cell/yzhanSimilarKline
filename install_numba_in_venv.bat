@echo off
echo ========================================
echo 在虚拟环境中安装 Numba
echo ========================================
cd /d "%~dp0"

echo.
echo [1/3] 激活虚拟环境...
call venv\Scripts\activate.bat

echo.
echo [2/3] 安装 Numba...
pip install numba

echo.
echo [3/3] 验证安装...
python -c "import numba; print('Numba 版本:', numba.__version__)"

echo.
echo ========================================
echo 安装完成！
echo ========================================
pause
