@echo off
chcp 65001 >nul
echo ========================================
echo   A股K线智能分析系统 - 安装包构建工具
echo ========================================
echo.

REM 设置工作目录
cd /d "%~dp0.."

echo [步骤 1/6] 检查Inno Setup...
where iscc >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 未检测到Inno Setup编译器！
    echo.
    echo 请先安装Inno Setup:
    echo 下载地址: https://jrsoftware.org/isdl.php
    echo.
    echo 安装后请将Inno Setup添加到系统PATH，或修改此脚本中的路径
    echo.
    pause
    exit /b 1
)

echo ✓ Inno Setup 已安装

echo.
echo [步骤 2/6] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 未检测到Python！
    echo 请先安装Python 3.9或更高版本
    echo 下载地址: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo ✓ Python 环境正常

echo.
echo [步骤 3/6] 检查缓存数据...

set CACHE_FOUND=0
if exist "securities_cache" set CACHE_FOUND=1
if exist "technical_cache" set CACHE_FOUND=1
if exist "search_cache" set CACHE_FOUND=1
if exist "cache" set CACHE_FOUND=1

if "%CACHE_FOUND%"=="0" (
    echo.
    echo [警告] 未检测到缓存数据目录
    echo 安装包将不包含预缓存数据，用户首次使用需要等待数据加载
    echo.
    echo 是否继续构建？(Y/N)
    set /p CONTINUE=
    if /i not "%CONTINUE%"=="Y" exit /b 0
) else (
    echo ✓ 缓存数据已就绪
)

echo.
echo [步骤 4/6] 创建输出目录...
if not exist "installer\Output" mkdir "installer\Output"
echo ✓ 输出目录已创建

echo.
echo [步骤 5/6] 编译安装程序...
echo 这可能需要几分钟，请耐心等待...
echo.

cd installer
iscc "setup.iss"

if errorlevel 1 (
    echo.
    echo [错误] 安装程序编译失败！
    echo 请检查 setup.iss 文件中的路径配置
    echo.
    cd ..
    pause
    exit /b 1
)

cd ..

echo.
echo [步骤 6/6] 验证输出文件...
if exist "installer\Output\A股K线智能分析系统_安装版.exe" (
    echo.
    echo ========================================
    echo   构建成功！
    echo ========================================
    echo.
    echo 输出文件: installer\Output\A股K线智能分析系统_安装版.exe
    echo.for %%I in ("installer\Output\A股K线智能分析系统_安装版.exe") do echo 文件大小: %%~zI 字节
    echo.
    echo 请将此EXE文件发送给客户使用
    echo 客户双击运行即可自动安装
    echo.
) else (
    echo [错误] 未找到输出文件！
    echo 请检查编译日志查找错误原因
)

pause