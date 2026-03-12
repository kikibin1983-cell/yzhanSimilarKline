@echo off
title A股K线智能分析系统
cd /d "%~dp0"
echo 启动A股K线智能分析系统...
echo.
echo 注意：首次启动需要下载数据，可能需要等待30秒
echo.
echo 访问地址: http://localhost:8502
echo.
echo 按 Ctrl+C 可停止服务
echo.
echo 启动中...
call venv\Scripts\activate.bat
python -m streamlit run streamlit_app.py --server.headless=true --server.port=8502
pause
