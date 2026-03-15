"""
测试TDX数据集成
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from datetime import datetime, timedelta

# 测试TDX读取器
print("=== 测试TDX数据读取器 ===")

try:
    from tdx_reader import TDXDataReader, read_tdx_data
    
    # 创建读取器
    reader = TDXDataReader()
    
    # 测试连接
    print("1. 测试TDX连接...")
    if reader.test_connection():
        print("   ✓ TDX连接成功")
    else:
        print("   ✗ TDX连接失败")
        exit(1)
    
    # 测试读取平安银行(000001)
    print("\n2. 测试读取平安银行(000001)...")
    df_stock = reader.get_stock_data('000001', end_date='20231231')
    if not df_stock.empty:
        print(f"   ✓ 读取成功，共{len(df_stock)}条记录")
        print(f"   数据范围: {df_stock['trade_date'].min()} 到 {df_stock['trade_date'].max()}")
        print(f"   最新记录: {df_stock.iloc[-1]['trade_date'].strftime('%Y-%m-%d')}, "
              f"收盘价: {df_stock.iloc[-1]['close']:.2f}")
    else:
        print("   ✗ 读取失败")
    
    # 测试读取上证指数(000001)
    print("\n3. 测试读取上证指数(000001)...")
    df_index = reader.get_index_data('000001', end_date='20231231')
    if not df_index.empty:
        print(f"   ✓ 读取成功，共{len(df_index)}条记录")
        print(f"   数据范围: {df_index['trade_date'].min()} 到 {df_index['trade_date'].max()}")
    else:
        print("   ✗ 读取失败")
    
    # 测试读取ETF(510300)
    print("\n4. 测试读取ETF(510300)...")
    df_etf = reader.get_etf_data('510300', end_date='20231231')
    if not df_etf.empty:
        print(f"   ✓ 读取成功，共{len(df_etf)}条记录")
        print(f"   数据范围: {df_etf['trade_date'].min()} 到 {df_etf['trade_date'].max()}")
    else:
        print("   ✗ 读取失败")
    
    # 测试便捷函数
    print("\n5. 测试便捷函数read_tdx_data()...")
    df_test = read_tdx_data('000001', 'stock', end_date='20231231')
    if not df_test.empty:
        print(f"   ✓ 读取成功，共{len(df_test)}条记录")
    
    # 获取可用代码
    print("\n6. 获取可用代码列表...")
    available_codes = reader.get_available_codes()
    print(f"   上海交易所: {len(available_codes['sh'])} 只股票")
    print(f"   深圳交易所: {len(available_codes['sz'])} 只股票")
    
    # 测试几个样本
    print(f"   上海样本: {available_codes['sh'][:3]}")
    print(f"   深圳样本: {available_codes['sz'][:3]}")
    
    print("\n=== TDX数据读取测试完成 ===")
    
except Exception as e:
    print(f"测试过程中出错: {str(e)}")
    import traceback
    traceback.print_exc()

# 测试streamlit_app中的get_market_data函数
print("\n=== 测试streamlit_app中的get_market_data函数 ===")

try:
    # 模拟streamlit_app环境
    from datetime import datetime, timedelta
    import pandas as pd
    
    # 导入修改后的函数
    exec(open('streamlit_app.py').read())
    
    # 测试函数
    print("1. 测试get_market_data函数...")
    
    # 测试股票
    print("   测试股票000001...")
    df = get_market_data('000001', 'stock', days=30)
    if df is not None and not df.empty:
        print(f"   ✓ 获取成功，共{len(df)}条记录")
        print(f"   数据范围: {df['trade_date'].min()} 到 {df['trade_date'].max()}")
    else:
        print("   ✗ 获取失败")
    
    # 测试指数
    print("\n   测试指数000001...")
    df = get_market_data('000001', 'index', days=30)
    if df is not None and not df.empty:
        print(f"   ✓ 获取成功，共{len(df)}条记录")
    else:
        print("   ✗ 获取失败")
    
    print("\n=== get_market_data函数测试完成 ===")
    
except Exception as e:
    print(f"测试过程中出错: {str(e)}")
    import traceback
    traceback.print_exc()