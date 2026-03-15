"""
测试K线数据显示
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

print("=== 测试K线数据显示功能 ===")

try:
    # 导入必要的函数
    import streamlit_app
    
    # 测试数据获取
    print("1. 测试获取平安银行(000001)的K线数据...")
    df = streamlit_app.get_market_data('000001', 'stock', days=365)
    
    if df is not None and not df.empty:
        print(f"   获取成功，共{len(df)}条记录")
        print(f"   数据范围: {df['trade_date'].min()} 到 {df['trade_date'].max()}")
        
        # 检查数据质量
        print(f"   数据列: {df.columns.tolist()}")
        print(f"   缺失值检查:")
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                missing = df[col].isna().sum()
                print(f"     {col}: {missing}个缺失值")
        
        # 检查价格合理性
        print(f"   价格范围检查:")
        if 'close' in df.columns:
            print(f"     收盘价范围: {df['close'].min():.2f} - {df['close'].max():.2f}")
            print(f"     最新收盘价: {df.iloc[-1]['close']:.2f}")
        
        # 测试标准化函数
        print("\n2. 测试价格序列标准化...")
        if 'close' in df.columns and len(df) >= 10:
            window = df['close'].iloc[-10:]  # 取最近10个收盘价
            normalized = streamlit_app.normalize_window(window)
            if normalized is not None:
                print(f"   标准化成功，窗口大小: {len(window)}")
                print(f"   标准化值范围: {normalized.min():.2f} - {normalized.max():.2f}")
            else:
                print("   标准化失败")
        
        # 测试相似度计算
        print("\n3. 测试相似度计算...")
        if 'close' in df.columns and len(df) >= 20:
            window1 = df['close'].iloc[-20:-10]  # 前一个窗口
            window2 = df['close'].iloc[-10:]     # 后一个窗口
            
            similarity = streamlit_app.calculate_similarity(window1, window2)
            print(f"   相似度计算成功: {similarity:.4f}")
        
        # 检查数据是否适合显示K线图
        print("\n4. 检查K线图显示要求...")
        required_columns = ['trade_date', 'open', 'high', 'low', 'close']
        missing_for_chart = [col for col in required_columns if col not in df.columns]
        
        if not missing_for_chart:
            print("   [OK] 所有K线图所需列都存在")
            
            # 检查数据时间顺序
            if df['trade_date'].is_monotonic_increasing:
                print("   [OK] 数据按时间顺序排列")
            else:
                print("   [WARN] 数据未按时间顺序排列")
                
            # 检查数据完整性
            date_range = (df['trade_date'].max() - df['trade_date'].min()).days
            expected_records = min(365, date_range)
            completeness = len(df) / expected_records if expected_records > 0 else 0
            
            print(f"   数据完整性: {completeness:.1%} ({len(df)}/{expected_records} 条记录)")
        else:
            print(f"   [WARN] 缺少K线图所需列: {missing_for_chart}")
    
    else:
        print("   获取数据失败")
        
    print("\n=== K线数据显示测试完成 ===")
    
except Exception as e:
    print(f"测试过程中出错: {str(e)}")
    import traceback
    traceback.print_exc()