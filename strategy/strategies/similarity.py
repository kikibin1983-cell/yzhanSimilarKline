"""
万向金枢 - 基于K线相似度的交易策略
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Dict, Optional
from strategy.base import BaseStrategy, Signal, Position


class SimilarityStrategy(BaseStrategy):
    """
    基于K线相似度的交易策略
    
    核心逻辑：
    1. 每日收盘前，搜索历史上与最近N天K线形态最相似的模式
    2. 统计这些相似模式后续的表现（上涨概率、平均收益）
    3. 如果历史上70%以上的相似案例后续上涨，则买入
    4. 设置动态止盈止损
    
    参数：
    - similarity_threshold: 最低相似度阈值
    - min_historical_samples: 最少需要的历史样本数
    - win_rate_threshold: 历史胜率阈值
    - holding_days: 最大持有天数
    - stop_loss: 止损比例
    - take_profit: 止盈比例
    """
    
    def __init__(self,
                 similarity_threshold: float = 0.75,
                 min_historical_samples: int = 5,
                 win_rate_threshold: float = 0.65,
                 avg_return_threshold: float = 0.03,
                 holding_days: int = 20,
                 stop_loss: float = 0.05,
                 take_profit: float = 0.10,
                 position_pct: float = 0.3,
                 window_size: int = 30,
                 **kwargs):
        
        super().__init__(name="SimilarityStrategy")
        
        # 策略参数
        self.similarity_threshold = similarity_threshold
        self.min_historical_samples = min_historical_samples
        self.win_rate_threshold = win_rate_threshold
        self.avg_return_threshold = avg_return_threshold
        self.holding_days = holding_days
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.position_pct = position_pct
        self.window_size = window_size
        
        # 运行时状态
        self.entry_prices = {}  # 记录买入价格
        
    def generate_signals(self, 
                        data: pd.DataFrame,
                        context: Dict) -> List[Signal]:
        """生成交易信号"""
        signals = []
        
        # 检查持仓止盈止损
        exit_signals = self._check_exit_conditions(data, context)
        signals.extend(exit_signals)
        
        # 检查是否已有持仓，有则不再开仓
        code = self._get_code_from_data(data)
        if code in context['positions']:
            return signals
        
        # 获取相似度分析结果
        try:
            from streamlit_app import find_similar_patterns
            
            # 使用最近window_size天的数据进行匹配
            recent_data = data.tail(self.window_size)
            if len(recent_data) < self.window_size:
                return signals
            
            similar_patterns = find_similar_patterns(
                recent_data,
                window_size=self.window_size,
                top_n=10,
                use_optimized=True,
                period_type='daily',
                search_across_market=False  # 只在当前股票历史中搜索
            )
            
        except Exception as e:
            print(f"相似度搜索失败: {e}")
            return signals
        
        if not similar_patterns:
            return signals
        
        # 统计历史表现
        stats = self._analyze_patterns(similar_patterns)
        
        # 判断是否符合买入条件
        if self._should_buy(stats):
            current_price = data['close'].iloc[-1]
            
            # 计算仓位大小
            position_value = context['portfolio_value'] * self.position_pct
            quantity = int(position_value / current_price / 100) * 100
            
            if quantity > 0:
                signals.append(Signal(
                    code=code,
                    direction='buy',
                    price=current_price,
                    size=quantity,
                    reason=f"相似度{stats['max_similarity']:.1%}, "
                           f"历史胜率{stats['win_rate']:.1%}, "
                           f"平均收益{stats['avg_return']:.1%}",
                    timestamp=context['current_date'],
                    confidence=stats['win_rate']
                ))
                
                # 记录买入价格
                self.entry_prices[code] = current_price
        
        return signals
    
    def _check_exit_conditions(self, 
                              data: pd.DataFrame, 
                              context: Dict) -> List[Signal]:
        """检查止盈止损条件"""
        signals = []
        code = self._get_code_from_data(data)
        
        if code not in context['positions']:
            return signals
        
        position = context['positions'][code]
        current_price = data['close'].iloc[-1]
        
        # 计算收益率
        if code in self.entry_prices:
            entry_price = self.entry_prices[code]
            return_pct = (current_price - entry_price) / entry_price
        else:
            return_pct = position.return_pct
        
        # 检查止损
        if return_pct <= -self.stop_loss:
            signals.append(Signal(
                code=code,
                direction='close',
                price=current_price,
                size=position.quantity,
                reason=f"触发止损 {-self.stop_loss:.1%}, 实际亏损 {return_pct:.1%}",
                timestamp=context['current_date'],
                confidence=1.0
            ))
            if code in self.entry_prices:
                del self.entry_prices[code]
            return signals
        
        # 检查止盈
        if return_pct >= self.take_profit:
            signals.append(Signal(
                code=code,
                direction='close',
                price=current_price,
                size=position.quantity,
                reason=f"触发止盈 {self.take_profit:.1%}, 实际盈利 {return_pct:.1%}",
                timestamp=context['current_date'],
                confidence=1.0
            ))
            if code in self.entry_prices:
                del self.entry_prices[code]
            return signals
        
        # 检查持有期满
        if position.holding_days >= self.holding_days:
            signals.append(Signal(
                code=code,
                direction='close',
                price=current_price,
                size=position.quantity,
                reason=f"持有期满 {self.holding_days} 天, 收益率 {return_pct:.1%}",
                timestamp=context['current_date'],
                confidence=0.5
            ))
            if code in self.entry_prices:
                del self.entry_prices[code]
            return signals
        
        return signals
    
    def _analyze_patterns(self, patterns: List[Dict]) -> Dict:
        """分析相似模式的历史表现"""
        if not patterns:
            return {
                'count': 0,
                'win_rate': 0,
                'avg_return': 0,
                'max_similarity': 0
            }
        
        # 计算每个模式的后续收益
        returns = []
        for pattern in patterns:
            future_data = pattern.get('future_data')
            if future_data is not None and not future_data.empty:
                start_price = pattern['pattern_data']['close'].iloc[-1]
                end_price = future_data['close'].iloc[-1]
                ret = (end_price - start_price) / start_price
                returns.append(ret)
        
        if not returns:
            return {
                'count': len(patterns),
                'win_rate': 0,
                'avg_return': 0,
                'max_similarity': max(p['similarity'] for p in patterns)
            }
        
        returns = np.array(returns)
        
        return {
            'count': len(patterns),
            'win_rate': np.sum(returns > 0) / len(returns),
            'avg_return': np.mean(returns),
            'max_similarity': max(p['similarity'] for p in patterns),
            'std_return': np.std(returns)
        }
    
    def _should_buy(self, stats: Dict) -> bool:
        """判断是否满足买入条件"""
        # 条件1：有足够的历史样本
        if stats['count'] < self.min_historical_samples:
            return False
        
        # 条件2：历史胜率达标
        if stats['win_rate'] < self.win_rate_threshold:
            return False
        
        # 条件3：平均收益为正且超过阈值
        if stats['avg_return'] < self.avg_return_threshold:
            return False
        
        # 条件4：最高相似度超过阈值
        if stats['max_similarity'] < self.similarity_threshold:
            return False
        
        return True
    
    def calculate_position_size(self, 
                               signal: Signal,
                               context: Dict) -> float:
        """计算仓位大小"""
        if signal.direction != 'buy':
            return signal.size
        
        # 使用预设的资金比例
        position_value = context['portfolio_value'] * self.position_pct
        quantity = int(position_value / signal.price / 100) * 100
        
        return quantity
    
    def _get_code_from_data(self, data: pd.DataFrame) -> str:
        """从数据中提取股票代码"""
        if 'code' in data.columns:
            return str(data['code'].iloc[0])
        return 'unknown'
    
    def reset(self):
        """重置策略状态"""
        super().reset()
        self.entry_prices = {}
