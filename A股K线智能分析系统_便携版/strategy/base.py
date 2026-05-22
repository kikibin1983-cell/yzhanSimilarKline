"""
万向金枢 - 量化策略模块基类
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional
import pandas as pd
import numpy as np


@dataclass
class Signal:
    """交易信号"""
    code: str
    direction: str  # 'buy' | 'sell' | 'close'
    price: float
    size: float  # 数量或比例
    reason: str
    timestamp: datetime
    confidence: float = 0.0  # 置信度 0-1
    
    def __post_init__(self):
        assert self.direction in ['buy', 'sell', 'close']
        assert 0 <= self.confidence <= 1


@dataclass 
class Position:
    """持仓信息"""
    code: str
    entry_price: float
    current_price: float
    quantity: int
    entry_date: datetime
    holding_days: int = 0
    
    @property
    def market_value(self) -> float:
        return self.current_price * self.quantity
    
    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.quantity
    
    @property
    def return_pct(self) -> float:
        return (self.current_price - self.entry_price) / self.entry_price


class BaseStrategy(ABC):
    """策略基类 - 所有策略必须继承此类"""
    
    def __init__(self, name: str = "BaseStrategy"):
        self.name = name
        self.signals: List[Signal] = []
        self.positions: Dict[str, Position] = {}
        
    @abstractmethod
    def generate_signals(self, 
                        data: pd.DataFrame,
                        context: Dict) -> List[Signal]:
        """
        生成交易信号
        
        Args:
            data: 当前股票数据（OHLCV）
            context: 上下文信息，包含：
                - current_date: 当前日期
                - current_price: 当前价格
                - positions: 当前持仓 {code: Position}
                - cash: 可用现金
                - portfolio_value: 组合总市值
        
        Returns:
            List[Signal]: 交易信号列表
        """
        pass
    
    @abstractmethod
    def calculate_position_size(self,
                               signal: Signal,
                               context: Dict) -> float:
        """
        计算仓位大小
        
        Args:
            signal: 交易信号
            context: 上下文
        
        Returns:
            float: 建议买入数量（股）
        """
        pass
    
    def on_trade(self, signal: Signal, execution_price: float):
        """
        交易执行后的回调
        
        Args:
            signal: 触发的信号
            execution_price: 实际成交价格
        """
        pass
    
    def on_bar(self, data: pd.DataFrame, context: Dict):
        """
        每根K线结束后的回调（可用于更新状态）
        
        Args:
            data: 当前K线数据
            context: 上下文
        """
        pass
    
    def get_state(self) -> Dict:
        """获取策略状态（用于保存）"""
        return {
            'name': self.name,
            'signals_count': len(self.signals),
            'positions': {code: {
                'entry_price': pos.entry_price,
                'quantity': pos.quantity,
                'entry_date': pos.entry_date.isoformat()
            } for code, pos in self.positions.items()}
        }
    
    def reset(self):
        """重置策略状态"""
        self.signals = []
        self.positions = {}
