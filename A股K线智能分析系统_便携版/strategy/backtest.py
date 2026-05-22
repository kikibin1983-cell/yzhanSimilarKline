"""
万向金枢 - 回测引擎
事件驱动回测框架
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Callable
import pandas as pd
import numpy as np
from .base import BaseStrategy, Signal, Position


@dataclass
class Trade:
    """成交记录"""
    timestamp: datetime
    code: str
    direction: str  # 'buy' | 'sell'
    price: float
    quantity: int
    commission: float
    slippage: float
    reason: str


@dataclass
class BacktestResult:
    """回测结果"""
    # 基础信息
    strategy_name: str
    start_date: datetime
    end_date: datetime
    
    # 收益指标
    initial_capital: float
    final_value: float
    total_return: float  # 总收益率
    annualized_return: float  # 年化收益率
    
    # 风险指标
    max_drawdown: float  # 最大回撤
    max_drawdown_duration: int  # 最大回撤持续天数
    volatility: float  # 波动率
    
    # 风险调整收益
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    
    # 交易统计
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_profit: float  # 平均盈利
    avg_loss: float  # 平均亏损
    profit_loss_ratio: float  # 盈亏比
    
    # 时间序列数据
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)  # 资金曲线
    drawdown_curve: pd.DataFrame = field(default_factory=pd.DataFrame)  # 回撤曲线
    daily_returns: pd.Series = field(default_factory=pd.Series)  # 日收益率
    trades: List[Trade] = field(default_factory=list)  # 成交记录
    positions_history: pd.DataFrame = field(default_factory=pd.DataFrame)  # 持仓历史


class BacktestEngine:
    """
    事件驱动回测引擎
    
    特点：
    - 支持分钟级和日级回测
    - 滑点和手续费模拟
    - 支持多股票组合
    """
    
    def __init__(self,
                 initial_capital: float = 100000.0,
                 commission_rate: float = 0.0003,  # 万3
                 slippage: float = 0.001,  # 滑点千1
                 min_commission: float = 5.0,  # 最低手续费5元
                 position_limit: float = 1.0):  # 仓位上限100%
        
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage = slippage
        self.min_commission = min_commission
        self.position_limit = position_limit
        
        # 账户状态
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        
        # 历史记录
        self.equity_history = []
        self.daily_returns = []
        
        # 当前日期
        self.current_date = None
        
    def run(self,
            strategy: BaseStrategy,
            data: pd.DataFrame,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None,
            progress_callback: Optional[Callable] = None) -> BacktestResult:
        """
        执行回测
        
        Args:
            strategy: 策略实例
            data: 行情数据（必须包含 OHLCV 列）
            start_date: 回测开始日期
            end_date: 回测结束日期
            progress_callback: 进度回调函数 (current, total)
        
        Returns:
            BacktestResult: 回测结果
        """
        # 重置状态
        self._reset()
        
        # 筛选时间范围
        if start_date:
            data = data[data['trade_date'] >= start_date]
        if end_date:
            data = data[data['trade_date'] <= end_date]
        
        if data.empty:
            raise ValueError("筛选后的数据为空")
        
        # 按日期排序
        data = data.sort_values('trade_date')
        dates = data['trade_date'].unique()
        
        # 逐日回测
        for i, date in enumerate(dates):
            self.current_date = pd.to_datetime(date)
            
            # 当日数据
            day_data = data[data['trade_date'] == date]
            
            # 更新持仓价格
            self._update_positions(day_data)
            
            # 生成信号
            context = self._create_context(day_data)
            signals = strategy.generate_signals(day_data, context)
            
            # 执行信号
            for signal in signals:
                self._execute_signal(signal)
            
            # 策略回调
            strategy.on_bar(day_data, context)
            
            # 记录资金
            self._record_equity()
            
            # 进度回调
            if progress_callback:
                progress_callback(i + 1, len(dates))
        
        # 计算绩效指标
        return self._calculate_performance(strategy.name, dates[0], dates[-1])
    
    def _reset(self):
        """重置引擎状态"""
        self.cash = self.initial_capital
        self.positions = {}
        self.trades = []
        self.equity_history = []
        self.daily_returns = []
        self.current_date = None
    
    def _update_positions(self, day_data: pd.DataFrame):
        """更新持仓价格和持有天数"""
        for code, position in self.positions.items():
            # 查找当前价格
            stock_data = day_data[day_data.get('code', '') == code]
            if not stock_data.empty:
                position.current_price = stock_data['close'].iloc[-1]
            
            # 更新持有天数
            if position.entry_date < self.current_date:
                position.holding_days = (self.current_date - position.entry_date).days
    
    def _create_context(self, day_data: pd.DataFrame) -> Dict:
        """创建策略上下文"""
        portfolio_value = self._calc_portfolio_value()
        
        return {
            'current_date': self.current_date,
            'current_price': day_data['close'].iloc[-1] if not day_data.empty else 0,
            'positions': self.positions.copy(),
            'cash': self.cash,
            'portfolio_value': portfolio_value,
            'available_cash': self.cash * self.position_limit
        }
    
    def _execute_signal(self, signal: Signal):
        """执行交易信号"""
        if signal.direction == 'buy':
            self._execute_buy(signal)
        elif signal.direction == 'sell':
            self._execute_sell(signal)
        elif signal.direction == 'close':
            self._execute_close(signal)
    
    def _execute_buy(self, signal: Signal):
        """执行买入"""
        # 计算可买数量
        max_cost = min(self.cash * self.position_limit, signal.price * signal.size)
        
        # 考虑滑点
        execution_price = signal.price * (1 + self.slippage)
        
        # 计算手续费
        commission = max(max_cost * self.commission_rate, self.min_commission)
        
        # 实际可用资金
        available_cash = max_cost - commission
        quantity = int(available_cash / execution_price / 100) * 100  # 整手
        
        if quantity <= 0:
            return
        
        # 执行交易
        cost = execution_price * quantity + commission
        self.cash -= cost
        
        # 创建持仓
        if signal.code in self.positions:
            # 加仓 - 更新平均成本
            pos = self.positions[signal.code]
            total_cost = pos.entry_price * pos.quantity + execution_price * quantity
            pos.quantity += quantity
            pos.entry_price = total_cost / pos.quantity
        else:
            # 新建仓
            self.positions[signal.code] = Position(
                code=signal.code,
                entry_price=execution_price,
                current_price=execution_price,
                quantity=quantity,
                entry_date=self.current_date
            )
        
        # 记录交易
        self.trades.append(Trade(
            timestamp=self.current_date,
            code=signal.code,
            direction='buy',
            price=execution_price,
            quantity=quantity,
            commission=commission,
            slippage=self.slippage * signal.price * quantity,
            reason=signal.reason
        ))
    
    def _execute_sell(self, signal: Signal):
        """执行卖出"""
        if signal.code not in self.positions:
            return
        
        position = self.positions[signal.code]
        quantity = min(int(signal.size), position.quantity)
        
        if quantity <= 0:
            return
        
        # 考虑滑点
        execution_price = signal.price * (1 - self.slippage)
        
        # 计算手续费
        turnover = execution_price * quantity
        commission = max(turnover * self.commission_rate, self.min_commission)
        
        # 更新资金
        proceeds = turnover - commission
        self.cash += proceeds
        
        # 更新持仓
        position.quantity -= quantity
        if position.quantity <= 0:
            del self.positions[signal.code]
        
        # 记录交易
        self.trades.append(Trade(
            timestamp=self.current_date,
            code=signal.code,
            direction='sell',
            price=execution_price,
            quantity=quantity,
            commission=commission,
            slippage=self.slippage * signal.price * quantity,
            reason=signal.reason
        ))
    
    def _execute_close(self, signal: Signal):
        """清仓"""
        if signal.code in self.positions:
            position = self.positions[signal.code]
            signal_copy = Signal(
                code=signal.code,
                direction='sell',
                price=position.current_price,
                size=position.quantity,
                reason=signal.reason,
                timestamp=signal.timestamp,
                confidence=signal.confidence
            )
            self._execute_sell(signal_copy)
    
    def _calc_portfolio_value(self) -> float:
        """计算组合总市值"""
        position_value = sum(
            pos.market_value for pos in self.positions.values()
        )
        return self.cash + position_value
    
    def _record_equity(self):
        """记录资金曲线"""
        portfolio_value = self._calc_portfolio_value()
        self.equity_history.append({
            'date': self.current_date,
            'equity': portfolio_value,
            'cash': self.cash,
            'positions_value': portfolio_value - self.cash,
            'positions_count': len(self.positions)
        })
    
    def _calculate_performance(self, strategy_name: str, 
                              start_date: datetime, 
                              end_date: datetime) -> BacktestResult:
        """计算回测绩效"""
        equity_df = pd.DataFrame(self.equity_history)
        if equity_df.empty:
            raise ValueError("回测结果为空")
        
        equity_df.set_index('date', inplace=True)
        equity_df.index = pd.to_datetime(equity_df.index)
        
        # 日收益率
        daily_returns = equity_df['equity'].pct_change().dropna()
        
        # 基础指标
        final_value = equity_df['equity'].iloc[-1]
        total_return = (final_value - self.initial_capital) / self.initial_capital
        
        # 年化收益率
        trading_days = len(equity_df)
        years = trading_days / 252
        annualized_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
        
        # 最大回撤
        cummax = equity_df['equity'].cummax()
        drawdown = (equity_df['equity'] - cummax) / cummax
        max_drawdown = drawdown.min()
        
        # 最大回撤持续期
        is_drawdown = drawdown < 0
        drawdown_starts = is_drawdown & (~is_drawdown.shift(1).fillna(False))
        drawdown_ends = (~is_drawdown) & is_drawdown.shift(1).fillna(False)
        
        max_duration = 0
        current_duration = 0
        for is_dd in is_drawdown:
            if is_dd:
                current_duration += 1
                max_duration = max(max_duration, current_duration)
            else:
                current_duration = 0
        
        # 波动率（年化）
        volatility = daily_returns.std() * np.sqrt(252)
        
        # 夏普比率（假设无风险利率3%）
        risk_free_rate = 0.03
        excess_returns = daily_returns - risk_free_rate / 252
        sharpe_ratio = np.sqrt(252) * excess_returns.mean() / daily_returns.std() if daily_returns.std() != 0 else 0
        
        # 索提诺比率（只考虑下行波动）
        downside_returns = daily_returns[daily_returns < 0]
        downside_std = downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else 0
        sortino_ratio = (annualized_return - risk_free_rate) / downside_std if downside_std != 0 else 0
        
        # 卡玛比率
        calmar_ratio = annualized_return / abs(max_drawdown) if max_drawdown != 0 else 0
        
        # 交易统计
        total_trades = len(self.trades)
        if total_trades > 0:
            # 计算每笔交易的盈亏
            trade_pnl = []
            buy_trades = {}
            
            for trade in self.trades:
                if trade.direction == 'buy':
                    buy_trades[trade.code] = trade
                else:
                    if trade.code in buy_trades:
                        buy_trade = buy_trades[trade.code]
                        pnl = (trade.price - buy_trade.price) * trade.quantity
                        pnl -= trade.commission + buy_trade.commission
                        trade_pnl.append(pnl)
            
            winning_trades = sum(1 for pnl in trade_pnl if pnl > 0)
            losing_trades = sum(1 for pnl in trade_pnl if pnl <= 0)
            win_rate = winning_trades / len(trade_pnl) if trade_pnl else 0
            
            profits = [pnl for pnl in trade_pnl if pnl > 0]
            losses = [pnl for pnl in trade_pnl if pnl <= 0]
            
            avg_profit = np.mean(profits) if profits else 0
            avg_loss = np.mean(losses) if losses else 0
            profit_loss_ratio = abs(avg_profit / avg_loss) if avg_loss != 0 else float('inf')
        else:
            winning_trades = losing_trades = 0
            win_rate = avg_profit = avg_loss = profit_loss_ratio = 0
        
        return BacktestResult(
            strategy_name=strategy_name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_value=final_value,
            total_return=total_return,
            annualized_return=annualized_return,
            max_drawdown=max_drawdown,
            max_drawdown_duration=max_duration,
            volatility=volatility,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            calmar_ratio=calmar_ratio,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            profit_loss_ratio=profit_loss_ratio,
            equity_curve=equity_df,
            drawdown_curve=pd.DataFrame({'drawdown': drawdown}),
            daily_returns=daily_returns,
            trades=self.trades,
            positions_history=pd.DataFrame(self.equity_history)
        )
