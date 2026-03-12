@echo off
chcp 65001 >nul
echo ========================================
echo   A股K线智能分析系统 - 简易启动
echo ========================================
echo.

cd /d "%~dp0"

REM 清除代理设置
set HTTP_PROXY=
set HTTPS_PROXY=

echo 正在启动应用...
echo.
echo 如果无法访问，请检查：
echo 1. Windows防火墙是否阻止了端口8501
echo 2. 浏览器是否支持localhost访问
echo.
echo 访问地址：
echo - http://localhost:8501
echo - http://127.0.0.1:8501
echo - http://192.168.50.249:8501
echo.
echo 按 Ctrl+C 停止应用
echo ========================================
echo.

python -m streamlit run streamlit_app.py --server.port 8501 --server.address 0.0.0.0

pause