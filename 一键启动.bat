@echo off
chcp 65001 >nul
title A股K线智能分析系统 - 一键安装启动器
color 0A

echo ╔════════════════════════════════════════════════════════════╗
echo ║        A股K线智能分析系统 - 一键启动器                    ║
echo ║              版本: 2.0 稳定版                             ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

REM 设置项目根目录
set "PROJECT_DIR=%~dp0.."
cd /d "%PROJECT_DIR%"

REM 检查Python
echo [步骤 1/5] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ╔════════════════════════════════════════════════════════════╗
    echo ║  [错误] 未检测到Python环境！                               ║
    echo ╠════════════════════════════════════════════════════════════╣
    echo ║  请按照以下步骤安装Python:                                ║
    echo ║  1. 访问 https://www.python.org/downloads/                ║
    echo ║  2. 下载 Python 3.9 或更高版本                            ║
    echo ║  3. 安装时勾选 "Add Python to PATH"                       ║
    echo ║  4. 重新运行此脚本                                        ║
    echo ╚════════════════════════════════════════════════════════════╝
    echo.
    pause
    exit /b 1
)

REM 显示Python版本
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYTHON_VERSION=%%v
echo [√] Python %PYTHON_VERSION% 已安装

REM 创建虚拟环境
echo.
echo [步骤 2/5] 配置Python虚拟环境...
if not exist "venv\Scripts\activate.bat" (
    echo [*] 创建虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败！
        pause
        exit /b 1
    )
)
echo [√] 虚拟环境已就绪

REM 激活虚拟环境
call venv\Scripts\activate.bat

REM 检查并安装依赖
echo.
echo [步骤 3/5] 检查并安装依赖包...
pip show streamlit >nul 2>&1
if errorlevel 1 (
    echo [*] 首次运行，正在安装依赖包（使用国内镜像加速）...
    echo     这可能需要几分钟，请耐心等待...
    echo.
    
    REM 升级pip
    python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
    
    REM 安装requirements.txt中的依赖
    if exist "requirements.txt" (
        pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    ) else (
        echo [警告] requirements.txt 不存在，安装核心依赖...
        pip install streamlit pandas numpy plotly scipy scikit-learn openai jieba numba adata -i https://pypi.tuna.tsinghua.edu.cn/simple
    )
    
    echo.
    echo [√] 依赖包安装完成
) else (
    echo [√] 依赖包已安装
)

REM 创建必要的目录
echo.
echo [步骤 4/5] 初始化缓存目录...
if not exist "cache" mkdir "cache"
if not exist "cache\technical" mkdir "cache\technical"
if not exist "cache\kline\weekly" mkdir "cache\kline\weekly"
if not exist "cache\kline\monthly" mkdir "cache\kline\monthly"
echo [√] 缓存目录已创建

REM 启动应用
echo.
echo [步骤 5/5] 启动应用...
echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║  系统正在启动，请稍候...                                    ║
echo ║  首次启动可能需要30秒初始化                                ║
echo ║  启动后浏览器会自动打开 http://localhost:8501              ║
echo ╠════════════════════════════════════════════════════════════╣
echo ║  按 Ctrl+C 可停止服务                                      ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

REM 启动Streamlit
streamlit run streamlit_app.py --server.headless true --browser.gatherUsageStats false

REM 如果Streamlit意外退出，暂停显示错误
echo.
echo [信息] 应用已退出
pause