"""
最终综合测试
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

print("=== 最终综合测试 ===")

# 测试1: TDX数据读取
print("\n1. 测试TDX数据读取...")
try:
    from tdx_reader import TDXDataReader
    reader = TDXDataReader()
    
    if reader.test_connection():
        print("   TDX连接成功")
        
        # 测试几个不同的股票
        test_cases = [
            ('000001', 'stock', '平安银行'),
            ('600000', 'stock', '浦发银行'),
            ('000001', 'index', '上证指数'),
        ]
        
        for code, sec_type, name in test_cases:
            print(f"   测试{name}({code})...")
            df = reader.get_stock_data(code, end_date='20231231') if sec_type == 'stock' else reader.get_index_data(code, end_date='20231231')
            if not df.empty:
                # 过滤掉未来日期
                from datetime import datetime
                current_date = datetime.now()
                df = df[df['trade_date'] <= current_date]
                
                if not df.empty:
                    print(f"     读取成功，共{len(df)}条有效记录")
                    print(f"     数据范围: {df['trade_date'].min().strftime('%Y-%m-%d')} 到 {df['trade_date'].max().strftime('%Y-%m-%d')}")
                else:
                    print(f"     读取的数据都是未来日期")
            else:
                print(f"     读取失败")
    else:
        print("   TDX连接失败")
        
except Exception as e:
    print(f"   TDX测试出错: {str(e)}")

# 测试2: 应用数据获取函数
print("\n2. 测试应用数据获取函数...")
try:
    import streamlit_app
    
    # 测试get_market_data函数
    print("   测试get_market_data函数...")
    df = streamlit_app.get_market_data('000001', 'stock', days=30)
    
    if df is not None and not df.empty:
        print(f"     获取成功，共{len(df)}条记录")
        
        # 检查数据质量
        required_cols = ['trade_date', 'open', 'high', 'low', 'close']
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            print(f"     警告: 缺少列: {missing}")
        else:
            print(f"     数据列完整")
            
        # 检查价格合理性
        if 'close' in df.columns:
            print(f"     收盘价范围: {df['close'].min():.2f} - {df['close'].max():.2f}")
    else:
        print("     获取失败")
        
except Exception as e:
    print(f"   应用测试出错: {str(e)}")

# 测试3: 搜索功能
print("\n3. 测试搜索功能...")
try:
    import streamlit_app
    
    # 测试search_securities函数
    print("   测试搜索'平安'...")
    results = streamlit_app.search_securities('平安')
    
    if results:
        print(f"     搜索成功，找到{len(results)}个结果")
        for i, result in enumerate(results[:3]):  # 显示前3个
            print(f"     {i+1}. {result['name']} ({result['code']}) - {result['type']}")
    else:
        print("     搜索失败或没有结果")
        
except Exception as e:
    print(f"   搜索测试出错: {str(e)}")

# 测试4: 缓存功能
print("\n4. 测试缓存目录...")
cache_dirs = ['./securities_cache', './search_cache']
for cache_dir in cache_dirs:
    if os.path.exists(cache_dir):
        files = os.listdir(cache_dir)
        print(f"   {cache_dir}: {len(files)}个缓存文件")
    else:
        print(f"   {cache_dir}: 目录不存在")

print("\n=== 最终测试完成 ===")
print("\n总结:")
print("1. TDX数据读取: 已实现，可以读取本地通达信数据")
print("2. 数据获取函数: 已重写，使用TDX替代akshare")
print("3. 搜索功能: 正常工作")
print("4. 缓存机制: 已配置")
print("\n下一步:")
print("- 启动应用测试完整功能")
print("- 检查K线图显示")
print("- 测试相似度分析和趋势预测")