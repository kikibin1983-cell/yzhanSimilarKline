@echo off
chcp 65001 >nul
echo ========================================
echo    A股K线智能分析系统 (手动安装版)
echo ========================================
echo.
echo 正在启动应用...
echo 请稍候...
echo.
echo 应用将在浏览器中打开
echo 如果未自动打开，请手动访问: http://localhost:8501
echo.
echo 按 Ctrl+C 停止应用
echo ========================================
echo.

REM 检查虚拟环境是否存在
if not exist venv\Scripts\python.exe (
    echo 错误: 虚拟环境不存在或未正确安装
    echo 请先运行安装器或按手动安装指南操作
    pause
    exit /b 1
)

REM 启动应用
venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.port 8501 --server.address localhost --browser.gatherUsageStats false
pause