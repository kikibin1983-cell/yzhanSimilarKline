@echo off
chcp 65001 >nul
echo ========================================
echo    修复 plotly 模块缺失问题
echo ========================================
echo.
echo 问题: ModuleNotFoundError: No module named 'plotly'
echo 原因: Python环境中缺少plotly模块
echo.
echo 本脚本将修复此问题，不修改项目代码
echo.

cd /d "%~dp0"

echo [步骤1/4] 检查当前Python环境...
python --version
if errorlevel 1 (
    echo 错误: 未找到Python
    echo 请先安装Python并添加到PATH
    pause
    exit /b 1
)

echo.
echo [步骤2/4] 检查是否已安装plotly...
python -c "import plotly" 2>nul
if errorlevel 1 (
    echo plotly未安装，正在安装...
) else (
    python -c "import plotly; print('当前plotly版本:', plotly.__version__)"
    echo plotly已安装，但可能版本不匹配
)

echo.
echo [步骤3/4] 安装/更新plotly到正确版本...
echo 使用清华镜像源加速...
python -m pip install --upgrade plotly==5.24.1 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo [步骤4/4] 验证安装...
python -c "import plotly; print('安装成功! plotly版本:', plotly.__version__)"

echo.
echo ========================================
echo   修复完成！
echo ========================================
echo.
echo 现在可以正常运行 streamlit_app.py:
echo.
echo 方法1: 直接运行
echo   python streamlit_app.py
echo.
echo 方法2: 使用Streamlit运行
echo   streamlit run streamlit_app.py
echo.
echo 方法3: 如果有虚拟环境，先激活:
echo   venv\Scripts\activate
echo   python streamlit_app.py
echo.
pause