#!/usr/bin/env python3
"""
万向金枢 - 参数优化工具
网格搜索最优策略参数组合

使用方法:
    python optimize_params.py --code 000001 --start 2020-01-01 --end 2023-12-31
    python optimize_params.py --code 000001 --metric sharpe_ratio --top 10
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import pandas as pd
import numpy as np
from datetime import datetime
from itertools import product
from typing import List, Dict, Tuple
from tqdm import tqdm
import json

from strategy.backtest import BacktestEngine
from strategy.strategies.similarity import SimilarityStrategy
from tdx_reader import read_tdx_data


class ParameterOptimizer:
    """参数优化器 - 网格搜索"""
    
    def __init__(self, 
                 code: str,
                 security_type: str = 'stock',
                 start_date: str = '2020-01-01',
                 end_date: str = '2023-12-31',
                 metric: str = 'sharpe_ratio'):
        """
        初始化优化器
        
        Args:
            code: 股票代码
            security_type: 证券类型
            start_date: 开始日期
            end_date: 结束日期
            metric: 优化目标指标 ('sharpe_ratio', 'total_return', 'calmar_ratio', etc.)
        """
        self.code = code
        self.security_type = security_type
        self.start_date = start_date
        self.end_date = end_date
        self.metric = metric
        
        # 加载数据
        self.data = read_tdx_data(code, security_type)
        if self.data is None or self.data.empty:
            raise ValueError(f"无法获取股票 {code} 的数据")
        
        print(f"已加载 {code} 的数据: {len(self.data)} 条记录")
        print(f"回测区间: {start_date} ~ {end_date}")
        print(f"优化目标: {metric}")
    
    def grid_search(self,
                   param_grid: Dict[str, List],
                   top_n: int = 10) -> pd.DataFrame:
        """
        网格搜索最优参数
        
        Args:
            param_grid: 参数网格
            top_n: 返回前N个最优结果
        
        Returns:
            DataFrame: 参数组合及结果
        """
        # 生成所有参数组合
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(product(*values))
        
        print(f"\n共有 {len(combinations)} 种参数组合需要测试")
        print("开始网格搜索...\n")
        
        results = []
        
        for combo in tqdm(combinations, desc="优化进度"):
            params = dict(zip(keys, combo))
            
            try:
                # 运行回测
                result = self._run_backtest(params)
                
                if result:
                    results.append({
                        **params,
                        **result
                    })
            except Exception as e:
                print(f"参数组合失败: {params}, 错误: {e}")
                continue
        
        if not results:
            raise ValueError("所有参数组合均失败")
        
        # 转换为DataFrame
        df = pd.DataFrame(results)
        
        # 按优化指标排序
        if self.metric in df.columns:
            df = df.sort_values(self.metric, ascending=False)
        
        print(f"\n✅ 完成 {len(results)} 组参数测试")
        
        return df.head(top_n)
    
    def _run_backtest(self, params: Dict) -> Dict:
        """运行单次回测"""
        # 创建策略
        strategy = SimilarityStrategy(**params)
        
        # 创建引擎
        engine = BacktestEngine(
            initial_capital=100000.0,
            commission_rate=0.0003,
            slippage=0.001
        )
        
        # 运行回测
        result = engine.run(
            strategy=strategy,
            data=self.data,
            start_date=pd.to_datetime(self.start_date),
            end_date=pd.to_datetime(self.end_date)
        )
        
        return {
            'total_return': result.total_return,
            'annualized_return': result.annualized_return,
            'max_drawdown': result.max_drawdown,
            'sharpe_ratio': result.sharpe_ratio,
            'sortino_ratio': result.sortino_ratio,
            'calmar_ratio': result.calmar_ratio,
            'win_rate': result.win_rate,
            'total_trades': result.total_trades,
            'profit_loss_ratio': result.profit_loss_ratio
        }
    
    def random_search(self,
                     param_distributions: Dict[str, Tuple],
                     n_iter: int = 100,
                     top_n: int = 10) -> pd.DataFrame:
        """
        随机搜索参数
        
        Args:
            param_distributions: 参数分布 (min, max) 元组
            n_iter: 迭代次数
            top_n: 返回前N个结果
        
        Returns:
            DataFrame: 参数组合及结果
        """
        print(f"\n随机搜索 {n_iter} 组参数...\n")
        
        results = []
        
        for _ in tqdm(range(n_iter), desc="随机搜索"):
            # 随机采样参数
            params = {}
            for key, (min_val, max_val) in param_distributions.items():
                if isinstance(min_val, int):
                    params[key] = np.random.randint(min_val, max_val + 1)
                else:
                    params[key] = np.random.uniform(min_val, max_val)
            
            try:
                result = self._run_backtest(params)
                if result:
                    results.append({
                        **params,
                        **result
                    })
            except Exception as e:
                continue
        
        df = pd.DataFrame(results)
        
        if self.metric in df.columns:
            df = df.sort_values(self.metric, ascending=False)
        
        return df.head(top_n)


def generate_report(results_df: pd.DataFrame, output_file: str = None):
    """生成优化报告"""
    print("\n" + "="*80)
    print("参数优化报告")
    print("="*80)
    
    # 最佳参数
    print("\n🏆 最佳参数组合:")
    best = results_df.iloc[0]
    
    param_cols = [c for c in results_df.columns if c not in [
        'total_return', 'annualized_return', 'max_drawdown', 
        'sharpe_ratio', 'sortino_ratio', 'calmar_ratio',
        'win_rate', 'total_trades', 'profit_loss_ratio'
    ]]
    
    for col in param_cols:
        print(f"  {col}: {best[col]}")
    
    print("\n📊 最佳结果:")
    print(f"  总收益率: {best['total_return']:.2%}")
    print(f"  年化收益: {best['annualized_return']:.2%}")
    print(f"  最大回撤: {best['max_drawdown']:.2%}")
    print(f"  夏普比率: {best['sharpe_ratio']:.2f}")
    print(f"  卡玛比率: {best['calmar_ratio']:.2f}")
    print(f"  胜率: {best['win_rate']:.1%}")
    print(f"  交易次数: {int(best['total_trades'])}")
    
    # Top 10
    print("\n📈 Top 10 参数组合:")
    display_cols = param_cols + ['sharpe_ratio', 'total_return', 'max_drawdown']
    top10 = results_df[display_cols].head(10)
    print(top10.to_string())
    
    # 参数敏感性分析
    print("\n🔍 参数敏感性分析:")
    for col in param_cols:
        if results_df[col].dtype in ['int64', 'float64']:
            correlation = results_df[col].corr(results_df['sharpe_ratio'])
            print(f"  {col} 与夏普比率相关性: {correlation:.3f}")
    
    # 导出
    if output_file:
        results_df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"\n✅ 详细结果已导出到: {output_file}")
    
    print("="*80)


def main():
    parser = argparse.ArgumentParser(
        description='万向金枢 - 策略参数优化工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础网格搜索
  python optimize_params.py --code 000001
  
  # 优化特定指标
  python optimize_params.py --code 000001 --metric calmar_ratio
  
  # 自定义参数范围
  python optimize_params.py --code 000001 --similarity-range 0.70 0.90 --holding-range 10 40
  
  # 随机搜索
  python optimize_params.py --code 000001 --method random --n-iter 200
        """
    )
    
    # 基础参数
    parser.add_argument('--code', type=str, required=True, help='股票代码')
    parser.add_argument('--start', type=str, default='2020-01-01', help='开始日期')
    parser.add_argument('--end', type=str, default='2023-12-31', help='结束日期')
    parser.add_argument('--metric', type=str, default='sharpe_ratio',
                       choices=['sharpe_ratio', 'total_return', 'calmar_ratio', 'sortino_ratio'],
                       help='优化目标指标')
    parser.add_argument('--top', type=int, default=10, help='返回前N个结果')
    parser.add_argument('--output', type=str, default='optimization_results.csv', help='输出文件')
    
    # 搜索方法
    parser.add_argument('--method', type=str, default='grid', 
                       choices=['grid', 'random'],
                       help='搜索方法')
    parser.add_argument('--n-iter', type=int, default=100, 
                       help='随机搜索迭代次数')
    
    # 参数范围
    parser.add_argument('--similarity-range', nargs=2, type=float, default=[0.70, 0.85],
                       help='相似度阈值范围')
    parser.add_argument('--win-rate-range', nargs=2, type=float, default=[0.60, 0.75],
                       help='胜率阈值范围')
    parser.add_argument('--holding-range', nargs=2, type=int, default=[10, 30],
                       help='持有天数范围')
    parser.add_argument('--stop-loss-range', nargs=2, type=float, default=[0.03, 0.08],
                       help='止损范围')
    parser.add_argument('--take-profit-range', nargs=2, type=float, default=[0.08, 0.15],
                       help='止盈范围')
    
    args = parser.parse_args()
    
    # 创建优化器
    optimizer = ParameterOptimizer(
        code=args.code,
        start_date=args.start,
        end_date=args.end,
        metric=args.metric
    )
    
    if args.method == 'grid':
        # 网格搜索参数
        param_grid = {
            'similarity_threshold': np.arange(args.similarity_range[0], args.similarity_range[1], 0.05).round(2),
            'win_rate_threshold': np.arange(args.win_rate_range[0], args.win_rate_range[1], 0.05).round(2),
            'holding_days': list(range(args.holding_range[0], args.holding_range[1] + 1, 5)),
            'stop_loss': np.arange(args.stop_loss_range[0], args.stop_loss_range[1], 0.01).round(2),
            'take_profit': np.arange(args.take_profit_range[0], args.take_profit_range[1], 0.02).round(2),
            'window_size': [20, 30, 40]
        }
        
        results = optimizer.grid_search(param_grid, top_n=args.top)
    
    else:  # random
        # 随机搜索参数分布
        param_distributions = {
            'similarity_threshold': (args.similarity_range[0], args.similarity_range[1]),
            'win_rate_threshold': (args.win_rate_range[0], args.win_rate_range[1]),
            'holding_days': (args.holding_range[0], args.holding_range[1]),
            'stop_loss': (args.stop_loss_range[0], args.stop_loss_range[1]),
            'take_profit': (args.take_profit_range[0], args.take_profit_range[1]),
            'window_size': (20, 40)
        }
        
        results = optimizer.random_search(param_distributions, n_iter=args.n_iter, top_n=args.top)
    
    # 生成报告
    generate_report(results, args.output)


if __name__ == '__main__':
    main()
