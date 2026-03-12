"""
测试优化后的K线图功能
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

print("=== 测试优化后的K线图功能 ===")

try:
    # 导入必要的函数
    import streamlit_app
    
    # 测试数据获取
    print("1. 测试获取测试数据...")
    
    # 创建测试数据
    dates = pd.date_range(start='2024-01-01', end='2024-12-31', freq='D')
    np.random.seed(42)
    
    # 创建价格数据（模拟股票价格）
    base_price = 100
    returns = np.random.normal(0.0005, 0.02, len(dates))
    prices = base_price * np.exp(np.cumsum(returns))
    
    # 创建OHLC数据
    opens = prices * (1 + np.random.normal(0, 0.01, len(dates)))
    highs = np.maximum(opens, prices) * (1 + np.random.uniform(0, 0.02, len(dates)))
    lows = np.minimum(opens, prices) * (1 - np.random.uniform(0, 0.02, len(dates)))
    closes = prices
    
    # 创建成交量数据
    volumes = np.random.randint(1000000, 10000000, len(dates))
    
    # 创建测试DataFrame
    test_df = pd.DataFrame({
        'trade_date': dates,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes
    })
    
    print(f"   创建测试数据成功，共{len(test_df)}条记录")
    print(f"   数据范围: {test_df['trade_date'].min()} 到 {test_df['trade_date'].max()}")
    
    # 测试K线空缺填充函数
    print("\n2. 测试K线空缺填充功能...")
    try:
        filled_df = streamlit_app.fill_trading_dates(test_df)
        print(f"   [OK] K线空缺填充成功")
        print(f"   填充后数据行数: {len(filled_df)}")
        print(f"   原始数据行数: {len(test_df)}")
        
        # 检查是否有填充的数据
        missing_days = len(filled_df) - len(test_df)
        if missing_days > 0:
            print(f"   填充了{missing_days}个非交易日")
    except Exception as e:
        print(f"   [ERROR] K线空缺填充失败: {str(e)}")
    
    # 测试MACD计算函数
    print("\n3. 测试MACD指标计算...")
    try:
        macd_data = streamlit_app.calculate_macd(test_df)
        if macd_data is not None:
            print(f"   [OK] MACD计算成功")
            print(f"   MACD数据形状: {macd_data.shape}")
            print(f"   MACD列: {macd_data.columns.tolist()}")
            
            # 检查MACD值
            if 'MACD' in macd_data.columns:
                print(f"   MACD值范围: {macd_data['MACD'].min():.4f} - {macd_data['MACD'].max():.4f}")
        else:
            print("   [WARN] MACD计算返回None")
    except Exception as e:
        print(f"   [ERROR] MACD计算失败: {str(e)}")
    
    # 测试KDJ计算函数
    print("\n4. 测试KDJ指标计算...")
    try:
        kdj_data = streamlit_app.calculate_kdj(test_df)
        if kdj_data is not None:
            print(f"   [OK] KDJ计算成功")
            print(f"   KDJ数据形状: {kdj_data.shape}")
            print(f"   KDJ列: {kdj_data.columns.tolist()}")
            
            # 检查KDJ值范围
            if 'K' in kdj_data.columns and 'D' in kdj_data.columns and 'J' in kdj_data.columns:
                print(f"   K值范围: {kdj_data['K'].min():.2f} - {kdj_data['K'].max():.2f}")
                print(f"   D值范围: {kdj_data['D'].min():.2f} - {kdj_data['D'].max():.2f}")
                print(f"   J值范围: {kdj_data['J'].min():.2f} - {kdj_data['J'].max():.2f}")
        else:
            print("   [WARN] KDJ计算返回None")
    except Exception as e:
        print(f"   [ERROR] KDJ计算失败: {str(e)}")
    
    # 测试价格相似度计算
    print("\n5. 测试价格相似度计算...")
    try:
        # 创建两个测试窗口
        window1 = test_df.iloc[:30]
        window2 = test_df.iloc[30:60]
        
        price_sim = streamlit_app.calculate_price_similarity(window1, window2)
        print(f"   [OK] 价格相似度计算成功: {price_sim:.4f}")
    except Exception as e:
        print(f"   [ERROR] 价格相似度计算失败: {str(e)}")
    
    # 测试成交量相似度计算
    print("\n6. 测试成交量相似度计算...")
    try:
        volume_sim = streamlit_app.calculate_volume_similarity(
            window1['volume'].values,
            window2['volume'].values
        )
        print(f"   [OK] 成交量相似度计算成功: {volume_sim:.4f}")
    except Exception as e:
        print(f"   [ERROR] 成交量相似度计算失败: {str(e)}")
    
    # 测试技术指标相似度计算
    print("\n7. 测试技术指标相似度计算...")
    try:
        technical_sim = streamlit_app.calculate_technical_similarity(window1, window2)
        print(f"   [OK] 技术指标相似度计算成功: {technical_sim:.4f}")
    except Exception as e:
        print(f"   [ERROR] 技术指标相似度计算失败: {str(e)}")
    
    # 测试形态相似度计算
    print("\n8. 测试形态相似度计算...")
    try:
        pattern_sim = streamlit_app.calculate_pattern_similarity(window1, window2)
        print(f"   [OK] 形态相似度计算成功: {pattern_sim:.4f}")
    except Exception as e:
        print(f"   [ERROR] 形态相似度计算失败: {str(e)}")
    
    # 测试综合相似度计算
    print("\n9. 测试综合相似度计算...")
    try:
        comprehensive_sim = streamlit_app.calculate_comprehensive_similarity(window1, window2)
        print(f"   [OK] 综合相似度计算成功: {comprehensive_sim:.4f}")
    except Exception as e:
        print(f"   [ERROR] 综合相似度计算失败: {str(e)}")
    
    # 测试优化后的相似模式查找
    print("\n10. 测试优化后的相似模式查找...")
    try:
        # 使用优化算法
        similar_patterns = streamlit_app.find_similar_patterns(
            test_df, 
            window_size=20, 
            top_n=3,
            use_optimized=True
        )
        
        print(f"   [OK] 优化算法找到{len(similar_patterns)}个相似模式")
        
        if similar_patterns:
            for i, pattern in enumerate(similar_patterns, 1):
                print(f"   模式{i}: 相似度={pattern['similarity']:.4f}, "
                      f"时间段={pattern['start_date'].strftime('%Y-%m-%d')}到{pattern['end_date'].strftime('%Y-%m-%d')}")
        
        # 使用原始算法对比
        print("\n11. 对比原始算法...")
        original_patterns = streamlit_app.find_similar_patterns(
            test_df,
            window_size=20,
            top_n=3,
            use_optimized=False
        )
        
        print(f"   原始算法找到{len(original_patterns)}个相似模式")
        
        if original_patterns:
            for i, pattern in enumerate(original_patterns, 1):
                print(f"   模式{i}: 相似度={pattern['similarity']:.4f}, "
                      f"时间段={pattern['start_date'].strftime('%Y-%m-%d')}到{pattern['end_date'].strftime('%Y-%m-%d')}")
    
    except Exception as e:
        print(f"   [ERROR] 相似模式查找失败: {str(e)}")
        import traceback
        traceback.print_exc()
    
    print("\n=== 优化功能测试完成 ===")
    
except Exception as e:
    print(f"测试过程中出错: {str(e)}")
    import traceback
    traceback.print_exc()