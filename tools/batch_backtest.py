#!/usr/bin/env python3
"""
万向金枢 - 批量回测工具
命令行批量测试多只股票策略表现

使用方法:
    python batch_backtest.py --codes 000001 000002 600000 --start 2020-01-01 --end 2023-12-31
    python batch_backtest.py --file stock_list.txt --output results.csv
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Dict
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from tqdm import tqdm

from strategy.backtest import BacktestEngine
from strategy.strategies.similarity import SimilarityStrategy
from tdx_reader import get_tdx_reader, read_tdx_data


def run_single_backtest(
    code: str,
    security_type: str = 'stock',
    start_date: str = '2020-01-01',
    end_date: str = '2023-12-31',
    strategy_params: Dict = None
) -> Dict:
    """
    对单只股票进行回测
    
    Args:
        code: 股票代码
        security_type: 证券类型
        start_date: 开始日期
        end_date: 结束日期
        strategy_params: 策略参数字典
    
    Returns:
        回测结果字典
    """
    try:
        # 加载数据
        data = read_tdx_data(code, security_type)
        
        if data is None or data.empty:
            return {
                'code': code,
                'status': 'error',
                'error': '无法获取数据'
            }
        
        # 创建策略
        if strategy_params is None:
            strategy_params = {
                'similarity_threshold': 0.75,
                'win_rate_threshold': 0.65,
                'holding_days': 20,
                'stop_loss': 0.05,
                'take_profit': 0.10,
                'window_size': 30
            }
        
        strategy = SimilarityStrategy(**strategy_params)
        
        # 创建引擎
        engine = BacktestEngine(
            initial_capital=100000.0,
            commission_rate=0.0003,
            slippage=0.001
        )
        
        # 运行回测
        result = engine.run(
            strategy=strategy,
            data=data,
            start_date=pd.to_datetime(start_date),
            end_date=pd.to_datetime(end_date)
        )
        
        return {
            'code': code,
            'status': 'success',
            'total_return': result.total_return,
            'annualized_return': result.annualized_return,
            'max_drawdown': result.max_drawdown,
            'sharpe_ratio': result.sharpe_ratio,
            'calmar_ratio': result.calmar_ratio,
            'win_rate': result.win_rate,
            'total_trades': result.total_trades,
            'avg_profit': result.avg_profit,
            'avg_loss': result.avg_loss,
            'profit_loss_ratio': result.profit_loss_ratio
        }
        
    except Exception as e:
        return {
            'code': code,
            'status': 'error',
            'error': str(e)
        }


def batch_backtest(
    codes: List[str],
    start_date: str = '2020-01-01',
    end_date: str = '2023-12-31',
    strategy_params: Dict = None,
    max_workers: int = 4,
    security_type: str = 'stock'
) -> pd.DataFrame:
    """
    批量回测多只股票
    
    Args:
        codes: 股票代码列表
        start_date: 开始日期
        end_date: 结束日期
        strategy_params: 策略参数
        max_workers: 并行进程数
        security_type: 证券类型
    
    Returns:
        DataFrame: 回测结果汇总
    """
    results = []
    
    print(f"开始批量回测 {len(codes)} 只股票...")
    print(f"回测区间: {start_date} ~ {end_date}")
    print(f"策略参数: {strategy_params}")
    print()
    
    # 使用进程池并行处理
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_code = {
            executor.submit(
                run_single_backtest, 
                code, 
                security_type,
                start_date, 
                end_date, 
                strategy_params
            ): code 
            for code in codes
        }
        
        # 收集结果
        for future in tqdm(as_completed(future_to_code), total=len(codes)):
            result = future.result()
            results.append(result)
    
    # 转换为DataFrame
    df = pd.DataFrame(results)
    
    # 排序：按总收益率降序
    if 'total_return' in df.columns:
        df = df.sort_values('total_return', ascending=False)
    
    return df


def generate_report(results_df: pd.DataFrame, output_file: str = None):
    """
    生成回测报告
    
    Args:
        results_df: 回测结果DataFrame
        output_file: 输出文件路径
    """
    print("\n" + "="*80)
    print("批量回测报告")
    print("="*80)
    
    # 成功统计
    success_df = results_df[results_df['status'] == 'success']
    error_df = results_df[results_df['status'] == 'error']
    
    print(f"\n测试总数: {len(results_df)}")
    print(f"成功: {len(success_df)}")
    print(f"失败: {len(error_df)}")
    
    if len(error_df) > 0:
        print(f"\n失败股票:")
        for _, row in error_df.iterrows():
            print(f"  {row['code']}: {row.get('error', 'Unknown error')}")
    
    if len(success_df) > 0:
        # 统计指标
        print(f"\n收益统计:")
        print(f"  平均总收益: {success_df['total_return'].mean():.2%}")
        print(f"  中位数总收益: {success_df['total_return'].median():.2%}")
        print(f"  最高收益: {success_df['total_return'].max():.2%} ({success_df.loc[success_df['total_return'].idxmax(), 'code']})")
        print(f"  最低收益: {success_df['total_return'].min():.2%} ({success_df.loc[success_df['total_return'].idxmin(), 'code']})")
        print(f"  正收益比例: {(success_df['total_return'] > 0).mean():.1%}")
        
        print(f"\n风险统计:")
        print(f"  平均最大回撤: {success_df['max_drawdown'].mean():.2%}")
        print(f"  平均夏普比率: {success_df['sharpe_ratio'].mean():.2f}")
        print(f"  平均卡玛比率: {success_df['calmar_ratio'].mean():.2f}")
        
        print(f"\n交易统计:")
        print(f"  平均交易次数: {success_df['total_trades'].mean():.1f}")
        print(f"  平均胜率: {success_df['win_rate'].mean():.1%}")
        print(f"  平均盈亏比: {success_df['profit_loss_ratio'].mean():.2f}")
        
        # Top 10
        print(f"\n收益Top 10:")
        top10 = success_df.nlargest(10, 'total_return')[['code', 'total_return', 'sharpe_ratio', 'win_rate']]
        for idx, row in top10.iterrows():
            print(f"  {row['code']}: 收益{row['total_return']:>7.1%}  夏普{row['sharpe_ratio']:>5.2f}  胜率{row['win_rate']:>5.1%}")
        
        # 导出结果
        if output_file:
            results_df.to_csv(output_file, index=False, encoding='utf-8-sig')
            print(f"\n✅ 结果已导出到: {output_file}")
    
    print("="*80)


def load_stock_list(file_path: str) -> List[str]:
    """从文件加载股票列表"""
    codes = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            code = line.strip()
            if code and not code.startswith('#'):
                codes.append(code)
    return codes


def main():
    parser = argparse.ArgumentParser(
        description='万向金枢 - 批量回测工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 测试3只指定股票
  python batch_backtest.py --codes 000001 000002 600000
  
  # 从文件加载股票列表
  python batch_backtest.py --file stock_list.txt --output results.csv
  
  # 测试整个主板
  python batch_backtest.py --sector main_board --workers 8
  
  # 自定义策略参数
  python batch_backtest.py --codes 000001 --similarity 0.80 --stop-loss 0.03
        """
    )
    
    # 股票选择
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--codes', nargs='+', help='指定股票代码列表')
    group.add_argument('--file', type=str, help='股票列表文件路径')
    group.add_argument('--sector', choices=['main_board', 'gem', 'star', 'all'], 
                      help='测试整个板块')
    
    # 回测参数
    parser.add_argument('--start', type=str, default='2020-01-01', help='开始日期')
    parser.add_argument('--end', type=str, default='2023-12-31', help='结束日期')
    parser.add_argument('--workers', type=int, default=4, help='并行进程数')
    parser.add_argument('--output', type=str, default='backtest_results.csv', help='输出文件')
    
    # 策略参数
    parser.add_argument('--similarity', type=float, default=0.75, help='相似度阈值')
    parser.add_argument('--win-rate', type=float, default=0.65, help='胜率阈值')
    parser.add_argument('--holding', type=int, default=20, help='持有天数')
    parser.add_argument('--stop-loss', type=float, default=0.05, help='止损比例')
    parser.add_argument('--take-profit', type=float, default=0.10, help='止盈比例')
    parser.add_argument('--window', type=int, default=30, help='匹配窗口')
    
    args = parser.parse_args()
    
    # 获取股票列表
    if args.codes:
        codes = args.codes
    elif args.file:
        codes = load_stock_list(args.file)
    else:
        # 从TDX读取板块股票
        print(f"正在获取{args.sector}板块股票列表...")
        reader = get_tdx_reader()
        available = reader.get_available_codes()
        codes = list(available.get('sh', [])) + list(available.get('sz', []))
        print(f"共 {len(codes)} 只股票")
    
    # 策略参数
    strategy_params = {
        'similarity_threshold': args.similarity,
        'win_rate_threshold': args.win_rate,
        'holding_days': args.holding,
        'stop_loss': args.stop_loss,
        'take_profit': args.take_profit,
        'window_size': args.window
    }
    
    # 运行批量回测
    results_df = batch_backtest(
        codes=codes,
        start_date=args.start,
        end_date=args.end,
        strategy_params=strategy_params,
        max_workers=args.workers
    )
    
    # 生成报告
    generate_report(results_df, args.output)


if __name__ == '__main__':
    main()
