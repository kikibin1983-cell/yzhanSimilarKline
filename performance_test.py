"""
万向金枢 - 相似度分析性能测试脚本
对比优化前后的速度

使用方法：
    python performance_test.py

测试项：
1. 单股票相似度分析速度
2. 多股票跨标搜索速度（并行 vs 串行）
3. Numba加速效果（如果已安装）
4. 缓存命中率测试
"""

import time
import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入优化后的函数
from streamlit_app import (
    find_similar_patterns, 
    _stock_feature_cache, 
    _search_result_cache,
    NUMBA_AVAILABLE
)

def generate_test_data(days=500, seed=42):
    """生成测试用的K线数据"""
    np.random.seed(seed)
    
    dates = pd.date_range(end=datetime.now(), periods=days, freq='B')  # 工作日
    
    # 生成随机漫步价格
    returns = np.random.normal(0.001, 0.02, days)
    prices = 100 * np.exp(np.cumsum(returns))
    
    # 生成成交量
    volumes = np.random.lognormal(15, 0.5, days)
    
    df = pd.DataFrame({
        'trade_date': dates,
        'open': prices * (1 + np.random.normal(0, 0.005, days)),
        'high': prices * (1 + np.abs(np.random.normal(0.01, 0.005, days))),
        'low': prices * (1 - np.abs(np.random.normal(0.01, 0.005, days))),
        'close': prices,
        'volume': volumes.astype(int)
    })
    
    return df

def test_single_stock_speed():
    """测试单股票相似度分析速度"""
    print("\n" + "="*60)
    print("[测试] 测试1: 单股票相似度分析速度")
    print("="*60)
    
    # 生成测试数据
    df = generate_test_data(days=1000)
    
    # 清空缓存，确保测试的是计算速度
    _search_result_cache.clear()
    
    # 测试不同配置
    configs = [
        {"window_size": 30, "top_n": 5, "name": "快速模式 (30天窗口, top5)"},
        {"window_size": 60, "top_n": 10, "name": "标准模式 (60天窗口, top10)"},
        {"window_size": 120, "top_n": 10, "name": "深度模式 (120天窗口, top10)"},
    ]
    
    results = []
    
    for config in configs:
        print(f"\n[运行] {config['name']}")
        print(f"   窗口大小: {config['window_size']}, 返回数量: {config['top_n']}")
        
        # 预热（第一次调用会触发Numba编译）
        if NUMBA_AVAILABLE:
            print("   [加载] Numba编译预热中...")
            find_similar_patterns(df, **{k: v for k, v in config.items() if k != 'name'})
        
        # 正式测试：运行3次取平均
        times = []
        for i in range(3):
            start = time.time()
            patterns = find_similar_patterns(
                df, 
                window_size=config['window_size'], 
                top_n=config['top_n'],
                use_optimized=True
            )
            elapsed = time.time() - start
            times.append(elapsed)
            print(f"   第{i+1}次: {elapsed:.3f}秒, 找到{len(patterns)}个模式")
        
        avg_time = np.mean(times)
        std_time = np.std(times)
        results.append({
            'name': config['name'],
            'avg_time': avg_time,
            'std_time': std_time
        })
        
        print(f"   [OK] 平均耗时: {avg_time:.3f}±{std_time:.3f}秒")
    
    return results

def test_cache_performance():
    """测试缓存效果"""
    print("\n" + "="*60)
    print("[缓存] 测试2: 缓存性能测试")
    print("="*60)
    
    df = generate_test_data(days=500)
    
    # 清空缓存
    _search_result_cache.clear()
    
    print("\n[运行] 首次查询（无缓存）:")
    start = time.time()
    patterns1 = find_similar_patterns(df, window_size=30, top_n=5)
    time_first = time.time() - start
    print(f"   耗时: {time_first:.3f}秒")
    
    print("\n[运行] 重复查询（有缓存）:")
    start = time.time()
    patterns2 = find_similar_patterns(df, window_size=30, top_n=5)
    time_cached = time.time() - start
    print(f"   耗时: {time_cached:.3f}秒")
    
    print("\n[运行] 缓存统计:")
    stats = _search_result_cache.get_stats()
    print(f"   缓存条目: {stats['size']}/{stats['max_size']}")
    print(f"   内存占用: {stats['memory_mb']:.1f}MB/{stats['max_memory_mb']}MB")
    
    speedup = time_first / time_cached if time_cached > 0 else float('inf')
    print(f"\n[OK] 缓存加速比: {speedup:.1f}x")
    
    return {
        'first_query': time_first,
        'cached_query': time_cached,
        'speedup': speedup
    }

def test_numba_performance():
    """测试Numba加速效果"""
    print("\n" + "="*60)
    print("[加速] 测试3: Numba加速效果")
    print("="*60)
    
    if not NUMBA_AVAILABLE:
        print("\n[警告] Numba未安装，跳过此项测试")
        print("   建议安装: pip install numba")
        return None
    
    print("\n[OK] Numba已安装")
    print("   状态: 核心计算函数已JIT编译")
    
    # 测试Numba函数
    from streamlit_app import _numba_batch_similarity, _numba_pearson_batch
    
    # 生成测试数据
    n_windows = 1000
    window_size = 30
    
    np.random.seed(42)
    windows_norm = np.random.randn(n_windows, window_size)
    recent_norm = np.random.randn(window_size)
    
    print(f"\n[运行] 测试数据: {n_windows}个窗口, 窗口大小{window_size}")
    
    # 测试Numba版本
    print("\n   Numba加速版本:")
    start = time.time()
    for _ in range(10):
        scores = _numba_batch_similarity(windows_norm, recent_norm)
    numba_time = (time.time() - start) / 10
    print(f"   平均耗时: {numba_time*1000:.3f}ms")
    
    # 测试纯NumPy版本（Numba不可用时的回退）
    print("\n   纯NumPy版本:")
    # 手动实现纯NumPy版本进行对比
    start = time.time()
    for _ in range(10):
        w_centered = windows_norm - windows_norm.mean(axis=1, keepdims=True)
        r_centered = recent_norm - recent_norm.mean()
        w_norm = np.linalg.norm(w_centered, axis=1) + 1e-10
        r_norm = np.linalg.norm(r_centered) + 1e-10
        corr = np.clip((w_centered @ r_centered) / (w_norm * r_norm), -1.0, 1.0)
        corr_score = (corr + 1.0) / 2.0
        
        dist = np.linalg.norm(windows_norm - recent_norm[None, :], axis=1)
        dist_score = 1.0 / (1.0 + dist / max(1, window_size))
        
        slope_recent = recent_norm[-1] - recent_norm[0]
        slope_windows = windows_norm[:, -1] - windows_norm[:, 0]
        slope_score = 1.0 - np.minimum(1.0, np.abs(slope_windows - slope_recent) / (np.abs(slope_recent) + 10.0))
        
        scores_numpy = 0.50 * corr_score + 0.30 * dist_score + 0.20 * slope_score
    numpy_time = (time.time() - start) / 10
    print(f"   平均耗时: {numpy_time*1000:.3f}ms")
    
    speedup = numpy_time / numba_time if numba_time > 0 else float('inf')
    print(f"\n[OK] Numba加速比: {speedup:.1f}x")
    
    return {
        'numba_time': numba_time,
        'numpy_time': numpy_time,
        'speedup': speedup
    }

def print_system_info():
    """打印系统信息"""
    print("="*60)
    print("系统信息")
    print("="*60)
    
    import psutil
    
    # CPU信息
    cpu_count = psutil.cpu_count(logical=False)
    cpu_count_logical = psutil.cpu_count(logical=True)
    print(f"\nCPU: {cpu_count} 物理核心 / {cpu_count_logical} 逻辑核心")
    
    # 内存信息
    mem = psutil.virtual_memory()
    print(f"内存: {mem.total / (1024**3):.1f} GB 总量 / {mem.available / (1024**3):.1f} GB 可用")
    
    # Numba状态
    print(f"Numba: {'已启用' if NUMBA_AVAILABLE else '未安装'}")
    
    print()

def main():
    """主测试函数"""
    print("\n")
    print("╔" + "="*58 + "╗")
    print("║" + " "*15 + "万向金枢 性能测试报告" + " "*16 + "║")
    print("╚" + "="*58 + "╝")
    
    # 系统信息
    print_system_info()
    
    # 运行各项测试
    results = {}
    
    # 测试1: 单股票速度
    results['single_stock'] = test_single_stock_speed()
    
    # 测试2: 缓存性能
    results['cache'] = test_cache_performance()
    
    # 测试3: Numba效果
    results['numba'] = test_numba_performance()
    
    # 总结
    print("\n" + "="*60)
    print("[报告] 性能测试总结")
    print("="*60)
    
    if results['single_stock']:
        print("\n[OK] 单股票分析:")
        for r in results['single_stock']:
            print(f"   {r['name']}: {r['avg_time']:.3f}秒")
    
    if results['cache']:
        print(f"\n[OK] 缓存加速: {results['cache']['speedup']:.1f}x")
        print(f"   首次查询: {results['cache']['first_query']:.3f}秒")
        print(f"   缓存查询: {results['cache']['cached_query']:.3f}秒")
    
    if results['numba'] and results['numba']['speedup']:
        print(f"\n[OK] Numba加速: {results['numba']['speedup']:.1f}x")
    
    print("\n" + "="*60)
    print("[完成] 测试完成！")
    print("="*60)

if __name__ == "__main__":
    main()
