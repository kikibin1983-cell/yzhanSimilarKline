@echo off
chcp 65001 >nul
echo ========================================
echo    A股K线智能分析系统 - plotly修复工具
echo ========================================
echo.
echo 问题: ModuleNotFoundError: No module named 'plotly'
echo 文件: O:\WXJS\yzhanSimilarKline\streamlit_app.py
echo 行号: 第9行 (import plotly.graph_objects as go)
echo.
echo 本工具将诊断并修复plotly模块缺失问题
echo 不修改任何项目源代码
echo.

echo [步骤1/5] 检查当前目录...
echo 当前目录: %cd%
echo.
echo 请确保您在项目根目录运行此脚本
echo 项目根目录应包含: streamlit_app.py 和 requirements.txt
echo.

echo [步骤2/5] 检查Python环境...
python --version 2>nul
if errorlevel 1 (
    echo [错误] 未找到Python
    echo 请先安装Python 3.8+ 并添加到系统PATH
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python版本: 
python --version

echo.
echo [步骤3/5] 检查plotly模块...
python -c "import plotly" 2>nul
if errorlevel 1 (
    echo [状态] plotly模块未安装
    set PLOTLY_STATUS=missing
) else (
    python -c "import plotly; print('[状态] plotly已安装，版本:', plotly.__version__)"
    set PLOTLY_STATUS=installed
)

echo.
echo [步骤4/5] 检查requirements.txt...
if exist requirements.txt (
    echo 找到requirements.txt
    for /f "tokens=2 delims==" %%i in ('findstr /i "plotly" requirements.txt') do (
        echo 要求的plotly版本: %%i
        set REQUIRED_VERSION=%%i
    )
) else (
    echo [警告] 未找到requirements.txt
    set REQUIRED_VERSION=5.24.1
)

echo.
echo [步骤5/5] 执行修复操作...
if "%PLOTLY_STATUS%"=="missing" (
    echo 正在安装plotly...
    echo 使用清华镜像源加速下载...
    python -m pip install plotly%REQUIRED_VERSION% -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
    echo 正在检查/更新plotly版本...
    python -m pip install --upgrade plotly%REQUIRED_VERSION% -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo.
echo ========================================
echo   验证修复结果
echo ========================================
echo.
python -c "import plotly; print('✓ plotly导入成功'); print('  版本:', plotly.__version__)"

echo.
echo 测试streamlit_app.py导入...
python -c "import sys; sys.path.insert(0, '.'); import streamlit_app; print('✓ streamlit_app.py导入成功')" 2>&1 | findstr /v "Traceback"

echo.
echo ========================================
echo   修复完成！
echo ========================================
echo.
echo 现在可以正常运行程序:
echo.
echo 方法1: 直接运行Python
echo   python streamlit_app.py
echo.
echo 方法2: 使用Streamlit (推荐)
echo   streamlit run streamlit_app.py
echo.
echo 方法3: 如果有虚拟环境
echo   venv\Scripts\activate
echo   python streamlit_app.py
echo.
echo 如果仍有问题，请检查:
echo 1. 确保在项目根目录运行
echo 2. 检查其他依赖包: pandas, numpy, scipy等
echo 3. 运行完整依赖安装:
echo     python -m pip install -r requirements.txt
echo.
pause