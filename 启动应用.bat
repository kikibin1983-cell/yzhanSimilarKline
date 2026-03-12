@echo off
chcp 65001 >nul
echo ========================================
echo    A股K线智能分析系统 - 启动脚本
echo ========================================
echo.

REM 设置工作目录
cd /d "%~dp0"

REM 清除代理设置，避免网络连接问题
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=

REM 检查Python环境
echo [1/4] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python，请确保已安装Python并添加到PATH
    pause
    exit /b 1
)

REM 检查streamlit
echo [2/4] 检查Streamlit...
python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo 错误: 未安装streamlit，正在尝试安装...
    pip install streamlit
    if errorlevel 1 (
        echo 安装streamlit失败
        pause
        exit /b 1
    )
)

REM 检查akshare
echo [3/4] 检查akshare...
python -c "import akshare" >nul 2>&1
if errorlevel 1 (
    echo 警告: akshare未安装，正在尝试安装...
    pip install akshare
)

REM 清理旧缓存（可选）
echo [4/4] 准备启动应用...
if exist securities_cache\*.json (
    echo 发现旧缓存文件，是否清理？(y/n)
    set /p clean_cache=
    if /i "%clean_cache%"=="y" (
        echo 清理缓存...
        del /q securities_cache\*.json >nul 2>&1
        del /q search_cache\*.json >nul 2>&1
    )
)

REM 创建必要的目录
if not exist securities_cache mkdir securities_cache
if not exist search_cache mkdir search_cache

REM 设置防火墙规则（需要管理员权限）
echo.
echo 正在配置防火墙规则...
netsh advfirewall firewall show rule name="Streamlit Port 8501" >nul 2>&1
if errorlevel 1 (
    echo 添加防火墙规则允许端口8501...
    netsh advfirewall firewall add rule name="Streamlit Port 8501" dir=in action=allow protocol=TCP localport=8501
)

REM 启动应用
echo.
echo ========================================
echo   正在启动A股K线智能分析系统...
echo ========================================
echo.
echo 应用将在浏览器中打开，如果未自动打开，请手动访问：
echo 本地访问: http://localhost:8501
echo 网络访问: http://192.168.50.249:8501
echo.
echo 按 Ctrl+C 停止应用
echo ========================================
echo.

REM 启动Streamlit应用
python -m streamlit run streamlit_app.py --server.port 8501 --server.address 0.0.0.0 --browser.serverAddress localhost

pause