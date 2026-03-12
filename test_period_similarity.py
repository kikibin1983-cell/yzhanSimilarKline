#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试多周期相似度功能
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
import streamlit_app

def test_period_similarity():
    """测试不同周期的相似度计算"""
    print("=== 测试多周期相似度功能 ===")
    
    # 1. 创建测试数据
    print("1. 创建测试数据...")
    dates = pd.date_range(start='2024-01-01', end='2025-03-01', freq='B')
    n_days = len(dates)
    
    # 生成模拟价格数据
    np.random.seed(42)
    base_price = 100
    returns = np.random.normal(0.001, 0.02, n_days)
    prices = base_price * np.exp(np.cumsum(returns))
    
    # 添加成交量
    volumes = np.random.randint(1000000, 10000000, n_days)
    
    test_df = pd.DataFrame({
        'trade_date': dates,
        'open': prices * (1 + np.random.normal(0, 0.01, n_days)),
        'high': prices * (1 + np.abs(np.random.normal(0, 0.015, n_days))),
        'low': prices * (1 - np.abs(np.random.normal(0, 0.015, n_days))),
        'close': prices,
        'volume': volumes
    })
    
    print(f"   创建了 {len(test_df)} 个交易日数据")
    
    # 2. 测试日线相似度
    print("\n2. 测试日线相似度...")
    daily_patterns = streamlit_app.find_similar_patterns(
        test_df, 
        window_size=30, 
        top_n=3, 
        use_optimized=True,
        period_type='daily'
    )
    print(f"   找到 {len(daily_patterns)} 个日线相似模式")
    if daily_patterns:
        print(f"   第一个模式相似度: {daily_patterns[0]['similarity']:.4f}")
        print(f"   周期类型: {daily_patterns[0].get('period_type', '未设置')}")
        print(f"   未来数据天数: {len(daily_patterns[0]['future_data'])}")
    
    # 3. 测试周线相似度
    print("\n3. 测试周线相似度...")
    weekly_patterns = streamlit_app.find_similar_patterns(
        test_df, 
        window_size=30,  # 会被覆盖为6周
        top_n=3, 
        use_optimized=True,
        period_type='weekly'
    )
    print(f"   找到 {len(weekly_patterns)} 个周线相似模式")
    if weekly_patterns:
        print(f"   第一个模式相似度: {weekly_patterns[0]['similarity']:.4f}")
        print(f"   周期类型: {weekly_patterns[0].get('period_type', '未设置')}")
        print(f"   未来数据周数: {len(weekly_patterns[0]['future_data'])}")
        print(f"   模式数据周数: {len(weekly_patterns[0]['pattern_data'])}")
    
    # 4. 测试月线相似度
    print("\n4. 测试月线相似度...")
    monthly_patterns = streamlit_app.find_similar_patterns(
        test_df, 
        window_size=30,  # 会被覆盖为2个月
        top_n=3, 
        use_optimized=True,
        period_type='monthly'
    )
    print(f"   找到 {len(monthly_patterns)} 个月线相似模式")
    if monthly_patterns:
        print(f"   第一个模式相似度: {monthly_patterns[0]['similarity']:.4f}")
        print(f"   周期类型: {monthly_patterns[0].get('period_type', '未设置')}")
        print(f"   未来数据月数: {len(monthly_patterns[0]['future_data'])}")
        print(f"   模式数据月数: {len(monthly_patterns[0]['pattern_data'])}")
    
    # 5. 测试趋势分析
    print("\n5. 测试趋势分析...")
    if weekly_patterns:
        weekly_stats = streamlit_app.analyze_future_trends(weekly_patterns)
        if weekly_stats:
            print(f"   周线趋势分析成功")
            print(f"   分析周期数: {len(weekly_stats['up'])}")
    
    # 6. 测试持仓收益分析
    print("\n6. 测试持仓收益分析...")
    if weekly_patterns:
        holding_stats = streamlit_app.analyze_holding_returns(weekly_patterns)
        if holding_stats:
            print(f"   周线持仓收益分析成功")
            print(f"   分析持有期数: {len(holding_stats)}")
            for period, stats in list(holding_stats.items())[:3]:
                if stats.get('trade_count', 0) > 0:
                    print(f"     {stats.get('label', period)}: {stats.get('avg_return', 0):.2f}% (胜率: {stats.get('win_rate', 0)*100:.1f}%)")
    
    print("\n=== 测试完成 ===")
    
    # 总结
    all_passed = True
    if not daily_patterns:
        print("警告: 未找到日线相似模式")
        all_passed = False
    if not weekly_patterns:
        print("警告: 未找到周线相似模式")
        all_passed = False
    if not monthly_patterns:
        print("警告: 未找到月线相似模式")
        all_passed = False
    
    if all_passed:
        print("所有测试通过!")
    else:
        print("部分测试未通过，请检查数据或算法")
    
    return all_passed

if __name__ == "__main__":
    test_period_similarity()