@echo off
echo ========================================
echo   一键安装所有依赖 (解决所有模块缺失)
echo ========================================
echo.

cd /d "%~dp0"

echo 检查Python...
python --version
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python
    pause
    exit /b 1
)

echo.
echo [开始] 一次性安装所有依赖...
echo 这可能需要5-10分钟，请耐心等待...
echo.

echo 步骤1: 升级pip...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo 步骤2: 安装requirements.txt中的所有依赖...
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo 步骤3: 安装额外的必需依赖...
python -m pip install python-dotenv altair pyarrow toml watchdog pygments click rich packaging -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo ========================================
echo   验证所有模块
echo ========================================
echo.

python -c "import streamlit; print('OK: streamlit')"
python -c "import pandas; print('OK: pandas')"
python -c "import numpy; print('OK: numpy')"
python -c "import plotly; print('OK: plotly')"
python -c "import scipy; print('OK: scipy')"
python -c "import sklearn; print('OK: sklearn')"
python -c "import jieba; print('OK: jieba')"
python -c "import akshare; print('OK: akshare')"
python -c "import requests; print('OK: requests')"

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 现在运行程序:
echo   python streamlit_app.py
echo.
echo 或使用streamlit:
echo   streamlit run streamlit_app.py
echo.
pause