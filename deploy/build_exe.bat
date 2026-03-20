@echo off
chcp 65001 >nul
echo ========================================
echo   A股K线智能分析系统 - 一键打包工具
echo ========================================
echo.

REM 检查Python环境
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python，请先安装Python 3.9+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 检查虚拟环境
if not exist "..\venv\Scripts\activate.bat" (
    echo [信息] 创建虚拟环境...
    python -m venv ..\venv
)

REM 激活虚拟环境
call ..\venv\Scripts\activate.bat

REM 升级pip
echo [信息] 升级pip...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

REM 安装依赖
echo [信息] 安装依赖包...
pip install -r ..\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

REM 安装PyInstaller
echo [信息] 安装PyInstaller...
pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple

REM 清理旧的构建文件
if exist "..\build" rmdir /s /q "..\build"
if exist "..\dist" rmdir /s /q "..\dist"
if exist "..\*.spec" del /q "..\*.spec"

REM 创建PyInstaller配置文件
echo [信息] 创建打包配置...
(
echo # -*- mode: python ; coding: utf-8 -*-
echo.
echo block_cipher = None
echo.
echo a = Analysis(
echo     ['../streamlit_app.py'],
echo     pathex=['..'],
echo     binaries=[],
echo     datas=[
echo         ('../technical_cache', 'technical_cache'),
echo         ('../cache', 'cache'),
echo         ('../weekly_cache', 'weekly_cache'),
echo         ('../requirements.txt', '.'),
echo     ],
echo     hiddenimports=[
echo         'streamlit',
echo         'pandas',
echo         'numpy',
echo         'adata',
echo         'plotly',
echo         'scipy',
echo         'scikit-learn',
echo         'openai',
echo         'jieba',
echo         'numba',
echo         'torch',
echo         'pefile',
echo     ],
echo     hookspath=[],
echo     hooksconfig={},
echo     runtime_hooks=[],
echo     excludes=['tkinter', 'matplotlib', 'notebook', 'jupyter'],
echo     win_no_prefer_redirects=False,
echo     win_private_assemblies=False,
echo     cipher=block_cipher,
echo     noarchive=False,
echo )
echo.
echo pyz = PYZ(a.pure, a.datas, cipher=block_cipher)
echo.
echo exe = EXE(
echo     pyz,
echo     a.scripts,
echo     a.binaries,
echo     a.datas,
echo     [],
echo     name='A股K线分析系统',
echo     debug=False,
echo     bootloader_ignore_signals=False,
echo     strip=False,
echo     upx=True,
echo     upx_exclude=[],
echo     runtime_tmpdir=None,
echo     console=True,  # 改为False可隐藏控制台
echo     disable_windowed_traceback=False,
echo     argv_emulation=False,
echo     target_arch=None,
echo     codesign_identity=None,
echo     entitlements_file=None,
echo     icon=None,
echo )
) > ..\app.spec

REM 执行打包
echo [信息] 开始打包...
cd ..
pyinstaller app.spec --clean

if exist "dist\A股K线分析系统.exe" (
    echo.
    echo ========================================
    echo   打包成功！
    echo ========================================
    echo.
    echo 输出文件: dist\A股K线分析系统.exe
    echo.
    
    REM 创建分发目录
    if not exist "release" mkdir "release"
    
    REM 复制必要文件
    copy "dist\A股K线分析系统.exe" "release\" >nul
    copy "requirements.txt" "release\" >nul
    
    REM 创建启动脚本
    (
        echo @echo off
        echo chcp 65001 ^>nul
        echo echo 正在启动A股K线分析系统...
        echo A股K线分析系统.exe
        echo pause
    ) > "release\启动.bat"
    
    REM 创建说明文件
    (
        echo A股K线智能分析系统
        echo =====================
        echo.
        echo 使用说明:
        echo 1. 双击"启动.bat"运行程序
        echo 2. 程序会自动在浏览器中打开
        echo 3. 默认访问地址: http://localhost:8501
        echo.
        echo 系统要求:
        echo - Windows 10/11 64位
        echo - 内存: 建议8GB以上
        echo - 网络: 需要网络连接获取股票数据
        echo.
        echo 首次运行可能需要等待30秒初始化缓存
    ) > "release\使用说明.txt"
    
    echo 分发目录: release\
    echo.
    echo 请将 release 文件夹打包发给您客户
) else (
    echo [错误] 打包失败，请检查错误信息
)

pause