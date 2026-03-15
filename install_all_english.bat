@echo off
cd /d "%~dp0"
echo ========================================
echo   Install ALL Dependencies at Once
echo ========================================
echo.

echo Step 1: Upgrade pip...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo Step 2: Install all from requirements.txt...
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo Step 3: Install extra packages...
python -m pip install python-dotenv altair pyarrow toml watchdog pygments click rich packaging -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo ========================================
echo   Verify All Modules
echo ========================================
echo.

python -c "import streamlit" && echo OK: streamlit
python -c "import pandas" && echo OK: pandas
python -c "import numpy" && echo OK: numpy
python -c "import plotly" && echo OK: plotly
python -c "import scipy" && echo OK: scipy
python -c "import sklearn" && echo OK: sklearn
python -c "import jieba" && echo OK: jieba
python -c "import akshare" && echo OK: akshare
python -c "import requests" && echo OK: requests

echo.
echo ========================================
echo   DONE! All installed!
echo ========================================
echo.
echo Now run: python streamlit_app.py
echo.
pause