@echo off
chcp 65001
cls
echo ==========================================
echo   RTX 4060 GPU加速环境安装脚本
echo   为您的i7-13620H + RTX 4060笔记本配置
echo ==========================================
echo.

echo [1/5] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.9+
    pause
    exit /b 1
)
python --version
echo.

echo [2/5] 升级Numba到支持CUDA的版本...
pip install --upgrade numba==0.58.1
if errorlevel 1 (
    echo [警告] Numba升级失败，继续尝试其他安装...
)
echo.

echo [3/5] 安装CUDA Toolkit支持...
echo 正在安装cudatoolkit（通过conda）...
conda install -c nvidia cuda-toolkit=11.8 -y 2>nul
if errorlevel 1 (
    echo [提示] 未安装conda，使用pip安装cuda-python...
    pip install cuda-python
)
echo.

echo [4/5] 安装NVIDIA RAPIDS（CuDF/CuPy）...
pip install cupy-cuda11x 2>nul
if errorlevel 1 (
    echo [提示] CuPy安装失败，将在运行时自动回退到CPU
)
echo.

echo [5/5] 验证GPU可用性...
python -c "from numba import cuda; print('✓ CUDA可用:', cuda.is_available()); print('✓ GPU数量:', len(cuda.list_devices())); [print('  -', d.name) for d in cuda.list_devices()]" 2>&1
if errorlevel 1 (
    echo [警告] GPU检测失败，可能缺少NVIDIA驱动
    echo 请访问: https://www.nvidia.cn/drivers/
    echo 下载并安装最新驱动
)
echo.

echo ==========================================
echo 安装完成！
echo ==========================================
echo.
echo 下一步：
echo 1. 重启命令行窗口
echo 2. 运行: python check_gpu.py
echo 3. 启动Streamlit: streamlit run streamlit_app.py
echo.
pause
