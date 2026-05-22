"""
数据预加载诊断工具
用于检测预加载服务卡住的原因

用法：
    python diagnose_preload.py
"""
import time
import threading
import sys

def diagnose():
    print("=" * 60)
    print("数据预加载诊断工具")
    print("=" * 60)
    print()
    
    # 1. 检查通达信路径
    print("[1/5] 检查通达信路径...")
    tdx_path = r"D:\new_tdx\vipdoc"
    
    import os
    if not os.path.exists(tdx_path):
        print(f"  ❌ 路径不存在: {tdx_path}")
        print("  请修改 stock_preload_service.py 中的 tdx_path")
        return
    
    # 统计文件数量
    sh_path = os.path.join(tdx_path, "lday", "sh")
    sz_path = os.path.join(tdx_path, "lday", "sz")
    
    if os.path.exists(sh_path):
        sh_files = len([f for f in os.listdir(sh_path) if f.endswith('.day')])
        print(f"  ✓ 上海股票: {sh_files} 只")
    else:
        sh_files = 0
        print(f"  ⚠️ 上海目录不存在")
    
    if os.path.exists(sz_path):
        sz_files = len([f for f in os.listdir(sz_path) if f.endswith('.day')])
        print(f"  ✓ 深圳股票: {sz_files} 只")
    else:
        sz_files = 0
        print(f"  ⚠️ 深圳目录不存在")
    
    total_files = sh_files + sz_files
    print(f"  ✓ 总计: {total_files} 只股票")
    print()
    
    # 2. 测试单只股票加载速度
    print("[2/5] 测试单只股票加载速度...")
    try:
        from tdx_reader import read_tdx_data
        
        test_code = "600000"  # 浦发银行
        start = time.time()
        df = read_tdx_data(code=test_code, security_type='stock')
        elapsed = time.time() - start
        
        if df is not None and not df.empty:
            print(f"  ✓ 加载 {test_code}: {len(df)} 天数据，耗时 {elapsed*1000:.0f}ms")
            avg_time = elapsed * 1000
        else:
            print(f"  ❌ 加载 {test_code} 失败")
            avg_time = 100  # 假设100ms
    except Exception as e:
        print(f"  ❌ 加载失败: {e}")
        avg_time = 100
    
    print()
    
    # 3. 预估总加载时间
    print("[3/5] 预估总加载时间...")
    estimated_time = total_files * avg_time / 1000 / 8  # 8线程并行
    print(f"  预估加载时间: {estimated_time:.0f} 秒 ({estimated_time/60:.1f} 分钟)")
    print(f"  平均每只股票: {avg_time:.0f}ms")
    print()
    
    # 4. 测试多线程加载
    print("[4/5] 测试多线程加载（前10只股票）...")
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        codes_to_test = ["600000", "000001", "600036", "000002", "600519",
                         "000858", "601318", "000333", "600276", "601166"]
        
        test_codes = [c for c in codes_to_test if os.path.exists(
            os.path.join(tdx_path, "lday", "sh" if c.startswith("6") else "sz", f"{c}.day")
        )]
        
        def load_one(code):
            start = time.time()
            df = read_tdx_data(code=code, security_type='stock')
            return code, time.time() - start, df is not None
        
        start_all = time.time()
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(load_one, code): code for code in test_codes[:5]}
            
            for future in as_completed(futures, timeout=30):
                code, elapsed, success = future.result()
                status = "✓" if success else "❌"
                print(f"  {status} {code}: {elapsed*1000:.0f}ms")
        
        total_elapsed = time.time() - start_all
        print(f"  ✓ 5只股票总耗时: {total_elapsed:.1f}秒")
        print(f"  ✓ 平均每只: {total_elapsed/5*1000:.0f}ms（并行）")
        
    except Exception as e:
        print(f"  ❌ 多线程测试失败: {e}")
    
    print()
    
    # 5. 给出建议
    print("[5/5] 诊断建议...")
    print()
    print("  如果加载时间过长，建议：")
    print()
    print("  1. 减少并行线程数")
    print("     在 stock_preload_service.py 中修改：")
    print("     max_workers: int = 16  ->  max_workers: int = 4")
    print()
    print("  2. 禁用预加载服务")
    print("     在 streamlit_app.py 中注释掉预加载初始化代码")
    print()
    print("  3. 使用磁盘缓存")
    print("     首次加载后会保存到 ./stock_cache/stock_data_cache.pkl")
    print("     下次启动会从缓存加载，速度快很多")
    print()
    
    # 6. 检查是否已在运行
    print("=" * 60)
    print("如果要手动启动预加载，可以运行：")
    print("  from stock_preload_service import get_preload_service")
    print("  service = get_preload_service()")
    print("  service.start(background=False)  # 同步加载")
    print("=" * 60)


if __name__ == "__main__":
    try:
        diagnose()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
    
    input("\n按回车键退出...")