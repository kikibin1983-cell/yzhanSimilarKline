#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD底背离筛选模块
功能：全市场扫描MACD底背离形态的股票

业务逻辑：
1. 计算MACD指标 (12, 26, 9)
2. 识别下跌波段（绿柱区间 MACD<0 或 DIF<DEA）
3. 找到波段A（最近）和波段B（上一次）的最低价格和DIF
4. 背离判定：Price_A < Price_B 且 DIF_A > DIF_B
5. 确认条件：3日内有金叉（DIF上穿DEA）

作者：AI Assistant
日期：2025
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 导入项目现有功能
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@dataclass
class MACDDivergenceResult:
    """MACD底背离结果数据结构"""
    code: str
    name: str
    price_a: float  # 波段A最低价格
    price_b: float  # 波段B最低价格
    dif_a: float    # 波段A最低DIF
    dif_b: float    # 波段B最低DIF
    date_a: datetime  # 波段A日期
    date_b: datetime  # 波段B日期
    golden_cross_date: datetime  # 金叉日期
    confidence: float  # 背离可信度 (0-1)
    is_valid: bool     # 是否有效背离
    divergence_type: str  # '底背离' 或 '即将金叉'


def calculate_macd(df: pd.DataFrame, fast_period: int = 12, 
                   slow_period: int = 26, signal_period: int = 9) -> pd.DataFrame:
    """
    计算MACD指标
    
    参数：
        df: 包含收盘价的数据框
        fast_period: 快速EMA周期（默认12）
        slow_period: 慢速EMA周期（默认26）
        signal_period: 信号线周期（默认9）
    
    返回：
        包含DIF、DEA、MACD柱的数据框
    """
    if df is None or df.empty or 'close' not in df.columns:
        return None
    
    df = df.copy()
    
    # 计算EMA
    df['EMA_fast'] = df['close'].ewm(span=fast_period, adjust=False).mean()
    df['EMA_slow'] = df['close'].ewm(span=slow_period, adjust=False).mean()
    
    # 计算DIF（差离值）
    df['DIF'] = df['EMA_fast'] - df['EMA_slow']
    
    # 计算DEA（信号线）
    df['DEA'] = df['DIF'].ewm(span=signal_period, adjust=False).mean()
    
    # 计算MACD柱（DIF-DEA）
    df['MACD'] = df['DIF'] - df['DEA']
    
    # 保留trade_date列（如果存在）
    columns_to_keep = ['close', 'high', 'low', 'DIF', 'DEA', 'MACD']
    if 'trade_date' in df.columns:
        columns_to_keep.append('trade_date')
    
    return df[columns_to_keep]


def find_downtrend_waves(df: pd.DataFrame) -> List[Dict]:
    """
    识别下跌波段（绿柱区间）
    
    定义下跌波段：MACD柱 < 0 或 DIF < DEA
    
    返回：
        List[Dict]: 每个波段的开始索引、结束索引、最低价格、最低DIF
    """
    if df is None or df.empty:
        return []
    
    # 确定下跌条件：MACD < 0 或 DIF < DEA
    df['is_downtrend'] = (df['MACD'] < 0) | (df['DIF'] < df['DEA'])
    
    waves = []
    in_wave = False
    wave_start = 0
    
    for i in range(len(df)):
        if df['is_downtrend'].iloc[i]:
            if not in_wave:
                # 开始新波段
                in_wave = True
                wave_start = i
        else:
            if in_wave:
                # 结束当前波段
                wave_end = i
                wave_data = df.iloc[wave_start:wave_end]
                
                # 找到波段内的最低价格和最低DIF
                min_price_idx = wave_data['close'].idxmin()
                min_price = wave_data.loc[min_price_idx, 'close']
                min_dif = wave_data.loc[min_price_idx, 'DIF']
                
                waves.append({
                    'start_idx': wave_start,
                    'end_idx': wave_end - 1,
                    'min_price': min_price,
                    'min_price_idx': min_price_idx,
                    'min_dif': min_dif,
                    'date': min_price_idx
                })
                
                in_wave = False
    
    # 处理最后一个未结束的波段（如果正在进行）
    if in_wave:
        wave_data = df.iloc[wave_start:]
        if len(wave_data) > 0:
            min_price_idx = wave_data['close'].idxmin()
            min_price = wave_data.loc[min_price_idx, 'close']
            min_dif = wave_data.loc[min_price_idx, 'DIF']
            
            waves.append({
                'start_idx': wave_start,
                'end_idx': len(df) - 1,
                'min_price': min_price,
                'min_price_idx': min_price_idx,
                'min_dif': min_dif,
                'date': min_price_idx
            })
    
    return waves


def detect_macd_divergence(df: pd.DataFrame, min_wave_length: int = 3, max_cross_days: int = 10) -> Optional[MACDDivergenceResult]:
    """
    检测MACD底背离
    
    背离判定条件：
    - Price_A < Price_B（价格创新低）
    - DIF_A > DIF_B（指标未创新低）
    
    确认条件：
    - 在最近的3个交易日内，发生DIF上穿DEA的金叉行为
    
    参数：
        df: 包含OHLCV数据的DataFrame
        min_wave_length: 最小波段长度
    
    返回：
        MACDDivergenceResult 或 None
    """
    if df is None or len(df) < 30:  # 至少需要30天数据
        return None
    
    # 计算MACD
    if all(col in df.columns for col in ['DIF', 'DEA', 'MACD']):
        df_macd = df.copy()
    else:
        df_macd = calculate_macd(df)
        if df_macd is None:
            return None
    
    # 识别下跌波段
    waves = find_downtrend_waves(df_macd)
    
    if len(waves) < 2:
        return None
    
    # 获取最近两个波段
    wave_a = waves[-1]  # 最近的波段
    wave_b = waves[-2]  # 上一次的波段
    
    # 检查波段长度
    wave_a_length = wave_a['end_idx'] - wave_a['start_idx'] + 1
    wave_b_length = wave_b['end_idx'] - wave_b['start_idx'] + 1
    
    if wave_a_length < min_wave_length or wave_b_length < min_wave_length:
        return None
    
    # 背离判定
    price_a = wave_a['min_price']
    price_b = wave_b['min_price']
    dif_a = wave_a['min_dif']
    dif_b = wave_b['min_dif']
    
    # 价格创新低且DIF未创新低
    price_condition = price_a < price_b * 0.995  # 允许0.5%的误差
    dif_condition = dif_a > dif_b * 1.005  # 允许0.5%的误差
    
    if not (price_condition and dif_condition):
        return None
    
    # 查找金叉确认 - 必须在最近1-max_cross_days天内，且涨幅不过大
    golden_cross_date = None
    golden_cross_idx = None
    days_since_cross = None
    
    # 检查最近(max_cross_days+10)天内的金叉
    check_days = max_cross_days + 10
    recent_data = df_macd.iloc[-check_days:]
    
    for i in range(len(recent_data) - 1, 0, -1):
        prev_dif = recent_data['DIF'].iloc[i-1]
        prev_dea = recent_data['DEA'].iloc[i-1]
        curr_dif = recent_data['DIF'].iloc[i]
        curr_dea = recent_data['DEA'].iloc[i]
        
        # 金叉条件：前一天DIF<DEA，当天DIF>DEA
        if prev_dif < prev_dea and curr_dif > curr_dea:
            # 计算金叉距离现在的天数
            days_since_cross = len(recent_data) - i
            # 接受1-max_cross_days天内的金叉（新鲜的信号）
            if 1 <= days_since_cross <= max_cross_days:
                golden_cross_date = recent_data.index[i]
                golden_cross_idx = i
            break
    
    # 检查金叉后的涨幅（避免已经大幅拉升的股票）
    if golden_cross_date is not None and golden_cross_idx is not None:
        # 金叉当天的价格
        cross_price = recent_data['close'].iloc[golden_cross_idx]
        # 当前价格
        current_price = df_macd['close'].iloc[-1]
        # 计算涨幅
        price_increase = (current_price - cross_price) / cross_price
        
        # 如果涨幅超过20%，认为是已经拉升过的，不新鲜了
        if price_increase > 0.20:
            return None
    
    is_valid = golden_cross_date is not None
    
    # 计算背离可信度
    price_decline = (price_b - price_a) / price_b if price_b > 0 else 0
    dif_improvement = (dif_a - dif_b) / abs(dif_b) if dif_b != 0 else 0
    confidence = min(1.0, (price_decline + dif_improvement) / 2)
    confidence = max(0.0, confidence)
    
    return MACDDivergenceResult(
        code="",
        name="",
        price_a=price_a,
        price_b=price_b,
        dif_a=dif_a,
        dif_b=dif_b,
        date_a=wave_a['date'],
        date_b=wave_b['date'],
        golden_cross_date=golden_cross_date,
        confidence=confidence,
        is_valid=is_valid,
        divergence_type='底背离' if is_valid else '潜在底背离'
    )


def detect_upcoming_golden_cross(df: pd.DataFrame, days: int = 3) -> bool:
    """
    检测底背离+即将金叉形态
    
    条件：
    1. 有底背离形态（价格创新低，DIF未创新低）
    2. MACD绿柱在减少（即将金叉）
    3. DIF正在向DEA靠近
    
    参数：
        df: 包含OHLCV数据的DataFrame
        days: 预测天数（默认3天）
    
    返回：
        bool: 是否符合底背离+即将金叉
    """
    if df is None or len(df) < 30:
        return False
    
    # 如果DataFrame已经包含MACD列，直接复用，避免重复计算
    if all(col in df.columns for col in ['DIF', 'DEA', 'MACD']):
        df_macd = df
    else:
        # 计算MACD
        df_macd = calculate_macd(df)
        if df_macd is None:
            return False
    
    # 步骤1：检查是否有底背离形态
    waves = find_downtrend_waves(df_macd)
    if len(waves) < 2:
        return False
    
    # 获取最近两个波段
    wave_a = waves[-1]  # 最近的波段
    wave_b = waves[-2]  # 上一次的波段
    
    # 检查波段长度
    wave_a_length = wave_a['end_idx'] - wave_a['start_idx'] + 1
    wave_b_length = wave_b['end_idx'] - wave_b['start_idx'] + 1
    
    if wave_a_length < 3 or wave_b_length < 3:
        return False
    
    # 背离判定
    price_a = wave_a['min_price']
    price_b = wave_b['min_price']
    dif_a = wave_a['min_dif']
    dif_b = wave_b['min_dif']
    
    # 价格创新低且DIF未创新低
    price_condition = price_a < price_b * 0.995
    dif_condition = dif_a > dif_b * 1.005
    
    if not (price_condition and dif_condition):
        return False
    
    # 步骤2：检查当前是否处于下跌波段（DIF < DEA）
    latest = df_macd.iloc[-1]
    if latest['DIF'] >= latest['DEA']:
        return False
    
    # 步骤3：检查MACD绿柱是否在减少（即将金叉）
    # 获取最近5天的MACD柱状图
    recent_macd = df_macd['MACD'].iloc[-5:].values
    
    # 绿柱在减少：最近几天的MACD值在增加（从负数向0靠近）
    # 检查最近3天绿柱是否连续减少
    if len(recent_macd) >= 3:
        # 最近3天MACD值
        macd_3d = recent_macd[-3]
        macd_2d = recent_macd[-2]
        macd_1d = recent_macd[-1]
        
        # 绿柱在减少：MACD值在增加（从负向0靠近）
        green_reducing = (macd_1d > macd_2d) and (macd_2d > macd_3d)
        
        if not green_reducing:
            return False
    
    # 步骤4：预测是否即将金叉
    recent_data = df_macd.iloc[-5:]
    
    # 计算DIF和DEA的斜率
    dif_slope = np.polyfit(range(len(recent_data)), recent_data['DIF'].values, 1)[0]
    dea_slope = np.polyfit(range(len(recent_data)), recent_data['DEA'].values, 1)[0]
    
    current_dif = latest['DIF']
    current_dea = latest['DEA']
    dif_dea_diff = current_dif - current_dea
    
    # DIF上升速度比DEA快，且差距在缩小，预计3天内金叉
    if dif_slope > dea_slope and dif_dea_diff < 0 and abs(dif_dea_diff) < abs(dif_slope) * days:
        return True
    
    return False


def scan_stock_for_divergence(code: str, name: str, get_data_func, 
                              days: int = 120) -> Optional[MACDDivergenceResult]:
    """
    扫描单只股票的MACD底背离
    
    参数：
        code: 股票代码
        name: 股票名称
        get_data_func: 获取股票数据的函数
        days: 获取历史数据天数
    
    返回：
        MACDDivergenceResult 或 None
    """
    try:
        # 获取股票数据
        df = get_data_func(code, days=days)
        
        if df is None or len(df) < 30:
            return None
        
        # 检测底背离
        result = detect_macd_divergence(df)
        
        if result is not None:
            result.code = code
            result.name = name
            return result
        
        return None
        
    except Exception as e:
        print(f"扫描股票 {code} 时出错: {e}")
        return None


def batch_scan_stocks(stock_list: List[Dict], get_data_func, 
                     max_workers: int = 10, progress_callback=None) -> List[MACDDivergenceResult]:
    """
    批量扫描多只股票
    
    参数：
        stock_list: 股票列表 [{'code': '000001', 'name': '平安银行'}, ...]
        get_data_func: 获取股票数据的函数
        max_workers: 并发线程数
        progress_callback: 进度回调函数 (completed, total)
    
    返回：
        List[MACDDivergenceResult]: 检测到的底背离结果列表
    """
    results = []
    completed = 0
    total = len(stock_list)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_stock = {
            executor.submit(scan_stock_for_divergence, 
                          stock['code'], stock['name'], get_data_func): stock
            for stock in stock_list
        }
        
        # 收集结果
        for future in as_completed(future_to_stock):
            completed += 1
            
            if progress_callback:
                progress_callback(completed, total)
            
            try:
                result = future.result()
                if result is not None and result.is_valid:
                    results.append(result)
            except Exception as e:
                stock = future_to_stock[future]
                print(f"处理股票 {stock['code']} 时出错: {e}")
    
    # 按可信度排序
    results.sort(key=lambda x: x.confidence, reverse=True)
    
    return results


def create_divergence_chart(result: MACDDivergenceResult, df: pd.DataFrame, days: int = 120) -> go.Figure:
    """
    创建MACD底背离可视化图表
    
    参数：
        result: 底背离结果
        df: 股票K线数据
        days: 显示最近多少天的数据（默认120日）
    
    返回：
        plotly.graph_objects.Figure
    """
    # 计算MACD
    df_macd = calculate_macd(df)
    
    # 只显示最近N天的数据
    if len(df_macd) > days:
        df_macd = df_macd.tail(days)
    
    # 创建子图
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        row_heights=[0.65, 0.35],
        subplot_titles=(f'{result.name} ({result.code}) - 最近{days}日', 'MACD')
    )
    
    # K线图
    fig.add_trace(go.Candlestick(
        x=df_macd.index,
        open=df_macd['open'] if 'open' in df_macd.columns else df_macd['close'],
        high=df_macd['high'] if 'high' in df_macd.columns else df_macd['close'],
        low=df_macd['low'] if 'low' in df_macd.columns else df_macd['close'],
        close=df_macd['close'],
        name='K线',
        increasing=dict(line=dict(color='#ff4d4d'), fillcolor='#ff4d4d'),
        decreasing=dict(line=dict(color='#26a69a'), fillcolor='#26a69a')
    ), row=1, col=1)
    
    # 标记波段A和波段B的最低点
    if result.date_a in df_macd.index and result.date_b in df_macd.index:
        # 处理日期格式（可能是整数索引或datetime）
        date_a_str = result.date_a.strftime("%m-%d") if hasattr(result.date_a, 'strftime') else str(result.date_a)
        date_b_str = result.date_b.strftime("%m-%d") if hasattr(result.date_b, 'strftime') else str(result.date_b)
        
        fig.add_trace(go.Scatter(
            x=[result.date_a],
            y=[result.price_a],
            mode='markers',
            marker=dict(size=15, color='red', symbol='circle'),
            name=f'波段A低点 ({date_a_str})'
        ), row=1, col=1)
        
        fig.add_trace(go.Scatter(
            x=[result.date_b],
            y=[result.price_b],
            mode='markers',
            marker=dict(size=15, color='orange', symbol='circle'),
            name=f'波段B低点 ({date_b_str})'
        ), row=1, col=1)
    
    # MACD图
    fig.add_trace(go.Scatter(
        x=df_macd.index,
        y=df_macd['DIF'],
        mode='lines',
        name='DIF',
        line=dict(color='#ff9900', width=1.5)
    ), row=2, col=1)
    
    fig.add_trace(go.Scatter(
        x=df_macd.index,
        y=df_macd['DEA'],
        mode='lines',
        name='DEA',
        line=dict(color='#0099ff', width=1.5)
    ), row=2, col=1)
    
    # MACD柱状图
    macd_colors = ['#ff4d4d' if val >= 0 else '#26a69a' for val in df_macd['MACD']]
    fig.add_trace(go.Bar(
        x=df_macd.index,
        y=df_macd['MACD'],
        name='MACD柱',
        marker_color=macd_colors,
        opacity=0.6
    ), row=2, col=1)
    
    # 标记金叉
    if result.golden_cross_date and result.golden_cross_date in df_macd.index:
        fig.add_vline(
            x=result.golden_cross_date,
            line=dict(color='green', width=2, dash='dash'),
            annotation_text='金叉确认',
            annotation_position='top',
            row=2, col=1
        )
    
    # 更新布局
    fig.update_layout(
        height=500,
        showlegend=True,
        title_text=f'MACD底背离分析 (可信度: {result.confidence:.1%})',
        hovermode='x unified'
    )
    
    fig.update_xaxes(rangeslider_visible=False)
    
    return fig


def create_golden_cross_chart(stock_info: dict, df: pd.DataFrame, days: int = 120) -> go.Figure:
    """
    创建即将金叉股票的MACD图表
    
    参数：
        stock_info: 股票信息 {'code': 'xxx', 'name': 'xxx', ...}
        df: 股票K线数据
        days: 显示最近多少天的数据（默认120日）
    
    返回：
        plotly.graph_objects.Figure
    """
    # 计算MACD
    df_macd = calculate_macd(df)
    
    # 只显示最近N天的数据
    if len(df_macd) > days:
        df_macd = df_macd.tail(days)
    
    # 创建子图
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        row_heights=[0.65, 0.35],
        subplot_titles=(f'{stock_info["name"]} ({stock_info["code"]}) - 最近{days}日', 'MACD')
    )
    
    # K线图
    fig.add_trace(go.Candlestick(
        x=df_macd.index,
        open=df_macd['open'] if 'open' in df_macd.columns else df_macd['close'],
        high=df_macd['high'] if 'high' in df_macd.columns else df_macd['close'],
        low=df_macd['low'] if 'low' in df_macd.columns else df_macd['close'],
        close=df_macd['close'],
        name='K线',
        increasing=dict(line=dict(color='#ff4d4d'), fillcolor='#ff4d4d'),
        decreasing=dict(line=dict(color='#26a69a'), fillcolor='#26a69a')
    ), row=1, col=1)
    
    # MACD图
    fig.add_trace(go.Scatter(
        x=df_macd.index,
        y=df_macd['DIF'],
        mode='lines',
        name='DIF',
        line=dict(color='#ff9900', width=1.5)
    ), row=2, col=1)
    
    fig.add_trace(go.Scatter(
        x=df_macd.index,
        y=df_macd['DEA'],
        mode='lines',
        name='DEA',
        line=dict(color='#0099ff', width=1.5)
    ), row=2, col=1)
    
    # MACD柱状图
    macd_colors = ['#ff4d4d' if val >= 0 else '#26a69a' for val in df_macd['MACD']]
    fig.add_trace(go.Bar(
        x=df_macd.index,
        y=df_macd['MACD'],
        name='MACD柱',
        marker_color=macd_colors,
        opacity=0.6
    ), row=2, col=1)
    
    # 更新布局
    fig.update_layout(
        height=500,
        showlegend=True,
        title_text=f'即将金叉预测 - {stock_info["name"]} ({stock_info["code"]})',
        hovermode='x unified'
    )
    
    fig.update_xaxes(rangeslider_visible=False)
    
    return fig


# 导出主要函数
__all__ = [
    'MACDDivergenceResult',
    'calculate_macd',
    'detect_macd_divergence',
    'detect_upcoming_golden_cross',
    'scan_stock_for_divergence',
    'batch_scan_stocks',
    'create_divergence_chart',
    'create_golden_cross_chart'
]
