"""
磁盘IO速度诊断工具
用于测试通达信数据读取速度，帮助优化预加载参数

用法：
    python diagnose_disk_io.py
"""
import time
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

def test_single_read(code, security_type='stock'):
    """测试单只股票读取速度"""
    from tdx_reader import read_tdx_data
    start = time.time()
    df = read_tdx_data(code=code, security_type=security_type)
    elapsed = time.time() - start
    return code, elapsed, df is not None and not df.empty

def diagnose():
    print("=" * 60)
    print("磁盘IO速度诊断工具")
    print("=" * 60)
    print()
    
    # 测试单线程速度
    print("[1/4] 测试单线程读取速度...")
    test_codes = ['600000', '000001', '600036', '000002', '600519']
    single_times = []
    
    for code in test_codes:
        start = time.time()
        _, elapsed, success = test_single_read(code)
        if success:
            single_times.append(elapsed)
            print(f"  股票 {code}: {elapsed*1000:.1f}ms")
        else:
            print(f"  股票 {code}: 读取失败")
    
    if single_times:
        avg_single = sum(single_times) / len(single_times)
        print(f"\n  单线程平均: {avg_single*1000:.1f}ms/只")
        print(f"  预估5000只单线程: {avg_single*5000/60:.1f}分钟")
    else:
        print("  无法读取测试数据，请检查通达信路径")
        return
    
    print()
    
    # 测试多线程速度
    print("[2/4] 测试不同线程数的读取速度...")
    for n_workers in [2, 4, 8]:
        print(f"\n  测试 {n_workers} 线程...")
        start = time.time()
        success_count = 0
        
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(test_single_read, code): code for code in test_codes}
            for future in as_completed(futures, timeout=30):
                try:
                    _, elapsed, success = future.result(timeout=10)
                    if success:
                        success_count += 1
                except Exception as e:
                    pass
        
        elapsed = time.time() - start
        avg_parallel = elapsed / len(test_codes)
        speedup = avg_single / avg_parallel if avg_parallel > 0 else 0
        
        print(f"    平均: {avg_parallel*1000:.1f}ms/只 | 加速比: {speedup:.1f}x | 预估5000只: {avg_parallel*5000/60:.1f}分钟")
    
    print()
    
    # 检查文件系统类型
    print("[3/4] 检查数据存储...")
    tdx_path = r"D:\new_tdx\vipdoc"
    if os.path.exists(tdx_path):
        print(f"  数据路径: {tdx_path}")
        
        # 统计文件数量和大小
        sh_path = os.path.join(tdx_path, "lday", "sh")
        sz_path = os.path.join(tdx_path, "lday", "sz")
        
        total_files = 0
        total_size = 0
        
        for path in [sh_path, sz_path]:
            if os.path.exists(path):
                files = [f for f in os.listdir(path) if f.endswith('.day')]
                total_files += len(files)
                for f in files[:100]:  # 只检查前100个文件的大小
                    try:
                        total_size += os.path.getsize(os.path.join(path, f))
                    except:
                        pass
        
        avg_file_size = (total_size / min(100, total_files)) / 1024 if total_files > 0 else 0
        print(f"  文件数量: {total_files} 只")
        print(f"  平均文件大小: {avg_file_size:.1f}KB")
        
        # 估算总数据量
        total_data_mb = (total_files * avg_file_size) / 1024
        print(f"  总数据量: ~{total_data_mb:.0f}MB")
    else:
        print(f"  路径不存在: {tdx_path}")
    
    print()
    
    # 给出建议
    print("[4/4] 优化建议...")
    print()
    
    if avg_single * 1000 < 50:
        print("  ✅ 磁盘性能良好！建议配置：")
        print("     - 预加载服务线程数: 4-8")
        print("     - 搜索时线程数: 4-8")
        print("     - 预加载预计时间: 2-5分钟")
    elif avg_single * 1000 < 100:
        print("  ⚠️  磁盘性能一般。建议配置：")
        print("     - 预加载服务线程数: 2-4")
        print("     - 搜索时线程数: 2-4")
        print("     - 预加载预计时间: 5-10分钟")
        print("     - 建议: 使用SSD或等待预加载完成后再搜索")
    else:
        print("  ❌ 磁盘性能较差。建议配置：")
        print("     - 预加载服务线程数: 2")
        print("     - 搜索时线程数: 2")
        print("     - 预加载预计时间: 10-20分钟")
        print("     - 建议: 1) 使用SSD 2) 首次启动后等待预加载完成")
        print("             3) 考虑禁用实时搜索，使用后台预计算")
    
    print()
    print("=" * 60)
    print("修改线程数的方法:")
    print("  1. stock_preload_service.py 第138行: max_workers=4 改为 2")
    print("  2. streamlit_app.py 第6461行: max_workers=4 改为 2")
    print("  3. streamlit_app.py 第3741行: effective_workers = min(4, ...) 改为 min(2, ...)")
    print("=" * 60)

if __name__ == "__main__":
    try:
        diagnose()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
    
    input("\n按回车键退出...")
