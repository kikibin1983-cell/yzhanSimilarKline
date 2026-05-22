@echo off
chcp 65001 >nul
echo ========================================
echo   A股K线智能分析系统 - 卸载程序
echo ========================================
echo.
echo 正在卸载...
echo.

REM 获取脚本所在目录
set "APP_DIR=%~dp0"

REM 删除虚拟环境
if exist "%APP_DIR%venv" (
    echo 删除虚拟环境...
    rmdir /s /q "%APP_DIR%venv"
)

REM 删除缓存
if exist "%APP_DIR%__pycache__" (
    echo 删除缓存文件...
    rmdir /s /q "%APP_DIR%__pycache__"
)

if exist "%APP_DIR%logs" (
    rmdir /s /q "%APP_DIR%logs"
)

REM 删除临时文件
del /q "%APP_DIR%*.pyc"2>nul
del /q "%APP_DIR%*.log" 2>nul

echo.
echo 卸载完成！
echo.
echo 注意：以下数据和文件已保留：
echo - 缓存数据目录（cache、securities_cache等）
echo - 配置文件（.env）
echo - 用户数据
echo.
echo 如需完全删除，请手动删除整个安装目录
echo.
pause