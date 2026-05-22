@echo off
chcp 936 >nul
echo ================================
echo TAB2技术分析 - 性能优化版
echo ================================
echo.
echo [已实施的优化]
echo 1. 数据预加载服务（启动时加载所有股票到内存）
echo 2. 批量处理优化（每批200只股票，减少进程开销）
echo 3. ThreadPoolExecutor替代ProcessPoolExecutor（Windows兼容性）
echo 4. 超时机制（每只股票30秒，每批5分钟）
echo.
echo [预期性能]
echo - 首次预加载：1-2分钟
echo - 扫描5000只股票：2-5分钟
echo.
echo [问题排查]
echo 如果仍然卡住，请：
echo 1. 检查控制台输出
echo 2. 运行: python diagnose_preload.py
echo 3. 查看: TAB2_OPTIMIZATION_SUMMARY.md
echo.
pause
echo.
echo 正在启动Streamlit应用...
streamlit run streamlit_app.py --server.port 8501
