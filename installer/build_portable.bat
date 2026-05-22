@echo off
chcp 65001 >nul
echo ========================================
echo   A股K线智能分析系统 - 便携版打包工具
echo ========================================
echo.
echo 此工具将创建一个完整的便携版安装包
echo 客户只需解压后运行"安装程序.bat"即可
echo.

cd /d "%~dp0.."

echo [步骤 1/4] 创建临时打包目录...
if exist "dist_temp" rmdir /s /q "dist_temp"
mkdir "dist_temp"
mkdir "dist_temp\A股K线智能分析系统"

echo [步骤 2/4] 复制必要文件...

REM 主程序
echo   - 主程序文件
copy "streamlit_app.py" "dist_temp\A股K线智能分析系统\" >nul
copy "tdx_reader.py" "dist_temp\A股K线智能分析系统\" >nul 2>nul
copy "requirements.txt" "dist_temp\A股K线智能分析系统\" >nul
copy ".env.template" "dist_temp\A股K线智能分析系统\" >nul
if exist ".env" copy ".env" "dist_temp\A股K线智能分析系统\" >nul
if exist "watchlist.json" copy "watchlist.json" "dist_temp\A股K线智能分析系统\" >nul

REM 文档
echo   - 文档文件
copy "用户手册.md" "dist_temp\A股K线智能分析系统\" >nul 2>nul
copy "readme.md" "dist_temp\A股K线智能分析系统\" >nul 2>nul

REM 图标
echo   - 图标文件
copy "logo.png" "dist_temp\A股K线智能分析系统\" >nul 2>nul
copy "软件图标.jpg" "dist_temp\A股K线智能分析系统\" >nul 2>nul

REM 缓存数据
echo   - 缓存数据目录
if exist "cache" xcopy "cache" "dist_temp\A股K线智能分析系统\cache\" /E /I /Q >nul
if exist "securities_cache" xcopy "securities_cache" "dist_temp\A股K线智能分析系统\securities_cache\" /E /I /Q >nul
if exist "search_cache" xcopy "search_cache" "dist_temp\A股K线智能分析系统\search_cache\" /E /I /Q >nul
if exist "technical_cache" xcopy "technical_cache" "dist_temp\A股K线智能分析系统\technical_cache\" /E /I /Q >nul
if exist "weekly_cache" xcopy "weekly_cache" "dist_temp\A股K线智能分析系统\weekly_cache\" /E /I /Q >nul
if exist "monthly_cache" xcopy "monthly_cache" "dist_temp\A股K线智能分析系统\monthly_cache\" /E /I /Q >nul

REM 策略文件
if exist "strategy" xcopy "strategy" "dist_temp\A股K线智能分析系统\strategy\" /E /I /Q >nul

echo.
echo [步骤 3/4] 创建安装脚本...

REM 创建安装程序脚本
(
echo @echo off
echo chcp 65001 ^>nul
echo title A股K线智能分析系统 - 安装程序
echo echo ========================================
echo echo   A股K线智能分析系统 - 安装程序
echo echo ========================================
echo echo.
echo.
echo REM 获取脚本所在目录
echo set "APP_DIR=%%~dp0"
echo cd /d "%%APP_DIR%%"
echo.
echo echo [步骤 1/5] 检查Python环境...
echo python --version ^>nul 2^>^&1
echo if errorlevel 1 ^(
echo     echo.
echo     echo [提示] 未检测到Python，正在下载安装...
echo     echo.
echo     REM 下载Python安装包
echo     echo 正在下载Python 3.11...
echo     powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%%TEMP%%\python_installer.exe'"
echo.
echo     if not exist "%%TEMP%%\python_installer.exe" ^(
echo         echo [错误] Python下载失败！
echo         echo 请手动下载并安装Python 3.9+
echo         pause
echo         exit /b 1
echo     ^)
echo.
echo     echo 正在安装Python...
echo     "%%TEMP%%\python_installer.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
echo.
echo     echo Python安装完成！
echo     echo 请关闭此窗口，重新运行安装程序。
echo     pause
echo     exit /b 0
echo ^)
echo.
echo echo √ Python环境已就绪
echo.
echo echo.
echo echo [步骤 2/5] 创建虚拟环境...
echo if not exist "venv" ^(
echo     echo 正在创建虚拟环境...
echo     python -m venv venv
echo     if errorlevel 1 ^(
echo         echo [错误] 虚拟环境创建失败！
echo         pause
echo         exit /b 1
echo     ^)
echo ^)
echo echo √ 虚拟环境已准备
echo.
echo echo.
echo echo [步骤 3/5] 安装依赖包...
echo echo 这可能需要5-10分钟，请耐心等待...
echo echo.
echo.
echo call venv\Scripts\activate.bat
echo.
echo echo 升级pip...
echo python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple ^>nul
echo.
echo echo 安装依赖包...
echo pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
echo if errorlevel 1 ^(
echo     echo [警告] 部分依赖安装失败，尝试继续...
echo ^)
echo.
echo echo √ 依赖包安装完成
echo.
echo echo.
echo echo [步骤 4/5] 创建启动器...
echo.
echo REM 创建启动批处理文件
echo (
echo     echo @echo off
echo     echo chcp 65001 ^>nul
echo     echo cd /d "%%APP_DIR%%"
echo     echo call venv\Scripts\activate.bat
echo     echo echo ========================================
echo     echo echo   A股K线智能分析系统
echo     echo echo ========================================
echo     echo echo.
echo     echo echo 正在启动服务...
echo     echo echo 首次启动需要30秒初始化缓存
echo     echo echo 启动后请在浏览器访问: http://localhost:8501
echo     echo echo.
echo     echo echo 按Ctrl+C停止服务
echo     echo echo.
echo     echo start http://localhost:8501
echo     echo streamlit run streamlit_app.py --server.port=8501 --server.headless=true
echo ^) ^> "启动.bat"
echo.
echo echo √ 启动器创建完成
echo.
echo echo.
echo echo [步骤 5/5] 配置完成
echo echo.
echo echo ========================================
echo echo   安装完成！
echo echo ========================================
echo echo.
echo echo 使用方法：
echo echo   1. 双击 "启动.bat" 启动程序
echo echo   2. 程序会自动在浏览器中打开
echo echo   3. 默认地址: http://localhost:8501
echo echo.
echo echo 桌面快捷方式：
echo echo   右键点击 "启动.bat" -^> 发送到 -^> 桌面快捷方式
echo echo.
echo pause
) > "dist_temp\A股K线智能分析系统\安装程序.bat"

REM 创建卸载脚本
(
echo @echo off
echo chcp 65001 ^>nul
echo echo ========================================
echo echo   A股K线智能分析系统 - 卸载程序
echo echo ========================================
echo echo.
echo.
echo REM 删除虚拟环境
echo if exist "venv" ^(
echo     echo 删除虚拟环境...
echo     rmdir /s /q "venv"
echo ^)
echo.
echo REM 删除缓存
echo if exist "__pycache__" rmdir /s /q "__pycache__"
echo if exist "logs" rmdir /s /q "logs"
echo.
echo echo.
echo echo 卸载完成！
echo echo 注意：缓存数据已保留，如需完全删除请手动删除整个文件夹
echo echo.
echo pause
) > "dist_temp\A股K线智能分析系统\卸载.bat"

REM 创建使用说明
(
echo A股K线智能分析系统 - 使用说明
echo ========================================
echo.
echo 安装步骤：
echo -----------
echo 1. 确保有Python环境（安装程序会自动检测）
echo 2. 双击运行 "安装程序.bat"
echo 3. 等待依赖安装完成
echo 4. 双击 "启动.bat" 运行程序
echo.
echo 启动程序：
echo -----------
echo 首次启动需要等待30秒初始化缓存
echo 程序会自动在浏览器打开
echo 默认地址: http://localhost:8501
echo.
echo 系统要求：
echo -----------
echo - Windows 10/11 (64位)
echo - 内存:建议 8GB以上
echo - 磁盘: 至少 500MB
echo - 网络: 需要网络连接
echo.
echo 技术支持：
echo -----------
echohttps://github.com/mantoufan/yzhanSimilarKline
) > "dist_temp\A股K线智能分析系统\使用说明.txt"

echo.
echo [步骤 4/4] 打包文件...

REM 检查是否有7-Zip或WinRAR
where 7z >nul 2>&1
if not errorlevel 1 (
    echo 使用7-Zip打包...
    7z a -tzip "A股K线智能分析系统_便携版.zip" ".\dist_temp\A股K线智能分析系统\*" -mx=5 >nul
    goto :done
)

where rar >nul 2>&1
if not errorlevel 1 (
    echo 使用WinRAR打包...
    rar a -r -ep1 "A股K线智能分析系统_便携版.zip" "dist_temp\A股K线智能分析系统\*" >nul
    goto :done
)

REM 使用PowerShell压缩
echo 使用PowerShell打包...
powershell -Command "Compress-Archive -Path 'dist_temp\A股K线智能分析系统\*' -DestinationPath 'A股K线智能分析系统_便携版.zip' -Force"

:done
echo.
echo ========================================
echo   打包完成！
echo ========================================
echo.
echo 输出文件: A股K线智能分析系统_便携版.zip
for %%I in ("A股K线智能分析系统_便携版.zip") do echo 文件大小: %%~zI 字节
echo.
echo 使用说明：
echo   1. 将ZIP文件发送给客户
echo   2. 客户解压到任意目录
echo   3. 双击运行"安装程序.bat"
echo   4. 安装完成后运行"启动.bat"
echo.

REM 清理临时目录
rmdir /s /q "dist_temp"

pause