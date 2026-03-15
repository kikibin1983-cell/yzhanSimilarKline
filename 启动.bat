@echo off
chcp 65001 >nul
echo ========================================
echo    A股K线智能分析系统
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

venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.port 8501 --server.address localhost --browser.gatherUsageStats false
pause
