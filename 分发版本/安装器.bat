@echo off
REM 设置UTF-8编码
chcp 65001 >nul

echo ========================================
echo    A股K线智能分析系统 - 便携版安装器
echo ========================================
echo.

REM 设置工作目录
cd /d "%~dp0"

REM 检查是否已安装Python
echo [1/5] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo 未找到Python，正在下载便携版Python...
    
    REM 下载便携版Python
    echo 正在下载Python 3.11.9 便携版...
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' -OutFile 'python-portable.zip'"
    
    if not exist python-portable.zip (
        echo 下载失败，请手动下载Python并安装
        echo 访问: https://www.python.org/downloads/
        pause >nul
        exit /b 1
    )
    
    echo 正在解压Python...
    powershell -Command "Expand-Archive -Path 'python-portable.zip' -DestinationPath 'python-portable' -Force"
    
    if not exist python-portable\python.exe (
        echo 解压失败
        pause >nul
        exit /b 1
    )
    
    REM 设置便携版Python路径
    set PYTHONPATH=%~dp0python-portable\python.exe
    set PIP_PATH=%~dp0python-portable\Scripts\pip.exe
    
    echo 便携版Python安装完成
) else (
    echo 已找到Python
    set PYTHONPATH=python
    set PIP_PATH=pip
)

REM 检查pip
echo [2/5] 检查pip...
%PYTHONPATH% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo 正在安装pip...
    %PYTHONPATH% -m ensurepip --upgrade
)

REM 安装依赖包
echo [3/5] 安装依赖包...
echo 这可能需要一些时间，请耐心等待...

REM 创建虚拟环境（可选）
if not exist venv (
    echo 创建虚拟环境...
    %PYTHONPATH% -m venv venv
    set VENV_PYTHON=venv\Scripts\python.exe
    set VENV_PIP=venv\Scripts\pip.exe
) else (
    set VENV_PYTHON=venv\Scripts\python.exe
    set VENV_PIP=venv\Scripts\pip.exe
)

REM 使用国内镜像源加速安装
echo 使用清华镜像源加速安装...
%VENV_PIP% install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 安装streamlit...
%VENV_PIP% install streamlit==1.54.0 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 安装pandas...
%VENV_PIP% install pandas==2.2.3 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 安装numpy...
%VENV_PIP% install numpy==1.26.4 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 安装plotly...
%VENV_PIP% install plotly==5.24.1 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 安装scipy...
%VENV_PIP% install scipy==1.13.1 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 安装scikit-learn...
%VENV_PIP% install scikit-learn==1.5.2 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 安装jieba...
%VENV_PIP% install jieba==0.42.1 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 安装akshare...
%VENV_PIP% install akshare==1.13.42 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 安装requests...
%VENV_PIP% install requests==2.32.3 -i https://pypi.tuna.tsinghua.edu.cn/simple

REM 创建必要的目录
echo [4/5] 创建必要的目录...
if not exist securities_cache mkdir securities_cache
if not exist search_cache mkdir search_cache
if not exist weekly_cache mkdir weekly_cache
if not exist monthly_cache mkdir monthly_cache

REM 创建启动脚本
echo [5/5] 创建启动脚本...
(
echo @echo off
echo chcp 65001 ^>nul
echo echo ========================================
echo echo    A股K线智能分析系统
echo echo ========================================
echo echo.
echo echo 正在启动应用...
echo echo 请稍候...
echo echo.
echo echo 应用将在浏览器中打开
echo echo 如果未自动打开，请手动访问: http://localhost:8501
echo echo.
echo echo 按 Ctrl+C 停止应用
echo echo ========================================
echo echo.
echo venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.port 8501 --server.address localhost --browser.gatherUsageStats false
echo pause
) > 启动.bat

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 使用方法：
echo 1. 双击"启动.bat"运行应用
echo 2. 等待浏览器打开（或手动访问 http://localhost:8501）
echo 3. 按 Ctrl+C 停止应用
echo.
echo 注意：
echo - 首次运行需要下载数据，请耐心等待
echo - 确保网络连接正常
echo - 防火墙可能会提示，请允许访问
echo.
echo 按任意键启动应用...
pause >nul

REM 启动应用
start 启动.bat