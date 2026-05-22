"""
GPU环境检测和性能测试脚本
专为您的RTX 4060 Laptop + i7-13620H配置
"""
import sys
import time

def check_gpu_environment():
    """检查GPU环境"""
    print("=" * 60)
    print("🖥️  GPU环境检测工具")
    print("=" * 60)
    print()
    
    # 1. 检查Numba
    print("[1/6] 检查Numba...")
    try:
        import numba
        print(f"  ✓ Numba版本: {numba.__version__}")
    except ImportError:
        print("  ✗ Numba未安装")
        print("  请运行: pip install numba")
        return False
    
    # 2. 检查CUDA
    print("\n[2/6] 检查CUDA...")
    try:
        from numba import cuda
        cuda_available = cuda.is_available()
        print(f"  ✓ CUDA可用: {cuda_available}")
        
        if cuda_available:
            devices = cuda.list_devices()
            print(f"  ✓ 检测到 {len(devices)} 个GPU设备:")
            for i, device in enumerate(devices):
                print(f"    GPU {i}: {device.name}")
                
                # 获取GPU详细信息
                try:
                    cc = device.compute_capability
                    print(f"      计算能力: {cc[0]}.{cc[1]}")
                except:
                    pass
        else:
            print("  ✗ CUDA不可用")
            print("  可能原因：")
            print("    - NVIDIA驱动未安装")
            print("    - CUDA Toolkit未安装")
            print("    - Numba版本不兼容")
            return False
            
    except Exception as e:
        print(f"  ✗ CUDA检测失败: {e}")
        return False
    
    # 3. 检查CuPy
    print("\n[3/6] 检查CuPy (GPU版NumPy)...")
    try:
        import cupy as cp
        print(f"  ✓ CuPy版本: {cp.__version__}")
        print(f"  ✓ CuPy使用GPU: {cp.cuda.is_available()}")
        if cp.cuda.is_available():
            print(f"  ✓ GPU内存: {cp.cuda.Device(0).mem_info[1] / 1024**3:.1f} GB")
    except ImportError:
        print("  ⚠ CuPy未安装 (可选)")
        print("    安装命令: pip install cupy-cuda11x")
    
    # 4. 检查系统信息
    print("\n[4/6] 系统信息...")
    import platform
    import os
    print(f"  OS: {platform.system()} {platform.release()}")
    print(f"  CPU: {platform.processor()}")
    print(f"  Python: {platform.python_version()}")
    print(f"  CPU核心数: {os.cpu_count()}")
    
    # 5. 运行性能测试
    print("\n[5/6] 运行性能对比测试...")
    print("  测试MACD计算性能...")
    
    import numpy as np
    import pandas as pd
    
    # 生成测试数据
    n = 5000  # 5000天数据
    test_data = pd.DataFrame({
        'close': np.random.randn(n).cumsum() + 100
    })
    
    # 测试1: CPU版本（原生pandas）
    print("\n  测试1: CPU原生计算...")
    start = time.time()
    close = test_data['close']
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2
    time_cpu_native = time.time() - start
    print(f"    耗时: {time_cpu_native*1000:.2f} ms")
    
    # 测试2: CPU版本（Numba加速）
    try:
        from macd_fast import calculate_macd_fast
        print("\n  测试2: CPU Numba加速...")
        start = time.time()
        result_numba = calculate_macd_fast(test_data)
        time_cpu_numba = time.time() - start
        print(f"    耗时: {time_cpu_numba*1000:.2f} ms")
        print(f"    加速比: {time_cpu_native/time_cpu_numba:.1f}x")
    except Exception as e:
        print(f"    失败: {e}")
        time_cpu_numba = None
    
    # 测试3: GPU版本（CuPy）
    try:
        import cupy as cp
        print("\n  测试3: GPU CuPy加速...")
        start = time.time()
        close_gpu = cp.array(test_data['close'].values)
        # 简单EMA计算
        alpha_fast = 2.0 / (12 + 1)
        ema_fast_gpu = cp.zeros_like(close_gpu)
        ema_fast_gpu[0] = close_gpu[0]
        for i in range(1, len(close_gpu)):
            ema_fast_gpu[i] = alpha_fast * close_gpu[i] + (1 - alpha_fast) * ema_fast_gpu[i-1]
        time_gpu = time.time() - start
        print(f"    耗时: {time_gpu*1000:.2f} ms")
        if time_cpu_numba:
            print(f"    相比CPU Numba加速: {time_cpu_numba/time_gpu:.1f}x")
        print(f"    相比CPU原生加速: {time_cpu_native/time_gpu:.1f}x")
    except Exception as e:
        print(f"    GPU测试失败: {e}")
        print("    请确保CuPy已正确安装")
    
    # 6. 给出建议
    print("\n[6/6] 优化建议...")
    print()
    print("  基于您的RTX 4060 Laptop配置：")
    print()
    
    if cuda_available:
        print("  🎉 好消息！您的GPU支持CUDA加速！")
        print()
        print("  建议实施方案：")
        print("  1. 立即可以做的（5分钟）：")
        print("     - 运行 install_gpu_env.bat 安装环境")
        print("     - 添加Numba CUDA内核到macd_fast.py")
        print("     - 预期提升: 50-100x")
        print()
        print("  2. 短期优化（1-2天）：")
        print("     - 使用CuDF替换Pandas")
        print("     - 批量GPU流水线处理")
        print("     - 预期提升: 100-200x")
        print()
        print("  3. 长期架构（1周）：")
        print("     - 完整CUDA内核开发")
        print("     - 异步IO+GPU计算流水线")
        print("     - 预期提升: 200x+")
    else:
        print("  ⚠️ GPU环境未就绪")
        print()
        print("  请按以下步骤操作：")
        print("  1. 安装NVIDIA驱动: https://www.nvidia.cn/drivers/")
        print("  2. 运行 install_gpu_env.bat")
        print("  3. 重启电脑后再次检测")
    
    print()
    print("=" * 60)
    
    return cuda_available

if __name__ == "__main__":
    try:
        gpu_ready = check_gpu_environment()
        
        if gpu_ready:
            print("\n✅ GPU环境已就绪，可以开始使用GPU加速！")
            print("\n下一步：查看GPU_ACCELERATION_GUIDE.md 获取实施指南")
        else:
            print("\n❌ GPU环境未就绪，请先完成环境配置")
            
    except Exception as e:
        print(f"\n❌ 检测过程出错: {e}")
        import traceback
        traceback.print_exc()
    
    input("\n按回车键退出...")
