# A股K线智能分析系统 - 一键启动脚本

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   A股K线智能分析系统 - 启动中..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 设置工作目录
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# 检查虚拟环境是否存在
if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "虚拟环境不存在，正在创建..." -ForegroundColor Yellow
    python -m venv venv
}

# 激活虚拟环境并启动
Write-Host "访问地址: http://localhost:8502" -ForegroundColor Green
Write-Host "按 Ctrl+C 可停止服务" -ForegroundColor Gray
Write-Host ""
Write-Host "启动中..." -ForegroundColor Cyan
Write-Host ""

& "venv\Scripts\python.exe" -m streamlit run streamlit_app.py --server.headless=true --server.port=8502
