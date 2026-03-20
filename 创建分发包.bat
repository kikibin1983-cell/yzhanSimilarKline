@echo off
chcp 65001 >nul
title 创建客户分发包
echo ╔════════════════════════════════════════════════════════════╗
echo ║        创建客户分发包                                      ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

REM 设置版本号
set VERSION=2.0.0
set DIST_NAME=A股K线智能分析系统_v%VERSION%_便携版

REM 创建发布目录
if exist "release" rmdir /s /q "release"
mkdir "release\%DIST_NAME%"
mkdir "release\%DIST_NAME%\app"

echo [步骤 1/4] 复制核心文件...
copy "streamlit_app.py" "release\%DIST_NAME%\app\" >nul
copy "tdx_reader.py" "release\%DIST_NAME%\app\" >nul
copy "requirements.txt" "release\%DIST_NAME%\app\" >nul
copy "一键启动.bat" "release\%DIST_NAME%\" >nul

echo [步骤 2/4] 复制缓存模块...
xcopy "technical_cache" "release\%DIST_NAME%\app\technical_cache\" /E /I /Y >nul

echo [步骤 3/4] 创建必要目录...
mkdir "release\%DIST_NAME%\app\cache\technical" >nul 2>&1
mkdir "release\%DIST_NAME%\app\cache\kline\weekly" >nul 2>&1
mkdir "release\%DIST_NAME%\app\cache\kline\monthly" >nul 2>&1

echo [步骤 4/4] 创建使用说明...
(
echo A股K线智能分析系统 v%VERSION%
echo ================================
echo.
echo 【系统要求】
echo - 操作系统: Windows 10/11 ^(64位^)
echo - 内存: 建议8GB以上
echo - 网络: 需要网络连接获取股票数据
echo.
echo 【安装步骤】
echo 1. 确保已安装 Python 3.9+
echo    下载地址: https://www.python.org/downloads/
echo    安装时务必勾选 "Add Python to PATH"
echo.
echo 2. 双击运行 "一键启动.bat"
echo.
echo 3. 首次运行会自动安装依赖，需要等待几分钟
echo.
echo 4. 浏览器自动打开 http://localhost:8501
echo.
echo 【常见问题】
echo Q: 提示"未检测到Python"
echo A: 请先安装Python 3.9+，并勾选 "Add Python to PATH"
echo.
echo Q: 浏览器没有自动打开
echo A: 手动访问 http://localhost:8501
echo.
echo Q: 启动很慢
echo A: 首次启动需要下载依赖和初始化缓存，是正常现象
echo.
echo 【技术支持】
echo 如有问题请联系技术支持
echo.
echo ================================
echo 版本: %VERSION%
echo 更新日期: %date%
) > "release\%DIST_NAME%\使用说明.txt"

echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║  分发包创建完成！                                          ║
echo ╠════════════════════════════════════════════════════════════╣
echo ║  目录: release\%DIST_NAME%\                               ║
echo ║                                                              ║
echo ║  下一步操作:                                                ║
echo ║  1. 进入 release\%DIST_NAME% 目录                           ║
echo ║  2. 将整个文件夹打包为ZIP                                    ║
echo ║  3. 发送给客户                                              ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

REM 询问是否打开目录
set /p OPEN_DIR="是否打开发布目录？(Y/N): "
if /i "%OPEN_DIR%"=="Y" (
    explorer "release\%DIST_NAME%"
)

pause