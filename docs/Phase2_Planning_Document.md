# 万向金枢 Phase 2 详细规划文档

## 一、量化交易模块详细设计

### 1.1 模块架构

```
strategy/
├── __init__.py
├── backtest.py          # 回测引擎核心
├── signals.py           # 信号生成器
├── portfolio.py         # 组合管理
├── risk_manager.py      # 风险管理
├── performance.py       # 绩效分析
└── strategies/          # 策略库
    ├── __init__.py
    ├── similarity_strategy.py    # 基于相似度的策略
    ├── multi_factor.py           # 多因子选股
    └── trend_following.py        # 趋势跟踪
```

### 1.2 回测引擎设计

#### 核心类设计

```python
class BacktestEngine:
    """
    回测引擎 - 支持事件驱动和向量化两种模式
    """
    
    def __init__(self, 
                 initial_capital: float = 100000.0,
                 commission_rate: float = 0.0003,  # 万3
                 slippage: float = 0.001,          # 滑点千1
                 position_limit: float = 1.0):      # 仓位上限100%
        
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage = slippage
        self.position_limit = position_limit
        
        # 账户状态
        self.cash = initial_capital
        self.positions = {}  # {code: Position}
        self.trades = []     # 交易记录
        self.daily_returns = []
        
    def run_backtest(self, 
                     strategy: BaseStrategy,
                     data: pd.DataFrame,
                     start_date: datetime,
                     end_date: datetime) -> BacktestResult:
        """执行回测"""
        
    def calculate_metrics(self) -> dict:
        """计算回测指标"""
        return {
            'total_return': self._calc_total_return(),
            'annualized_return': self._calc_annualized_return(),
            'max_drawdown': self._calc_max_drawdown(),
            'sharpe_ratio': self._calc_sharpe_ratio(),
            'sortino_ratio': self._calc_sortino_ratio(),
            'win_rate': self._calc_win_rate(),
            'profit_loss_ratio': self._calc_pl_ratio(),
            'calmar_ratio': self._calc_calmar_ratio(),
            'trade_count': len(self.trades)
        }
```

#### 策略基类

```python
class BaseStrategy(ABC):
    """策略基类"""
    
    @abstractmethod
    def generate_signals(self, 
                        data: pd.DataFrame,
                        context: dict) -> List[Signal]:
        """
        生成交易信号
        
        Args:
            data: 市场数据
            context: 上下文信息（当前持仓、账户信息等）
            
        Returns:
            List[Signal]: 信号列表
        """
        pass
    
    @abstractmethod
    def calculate_position_size(self, 
                               signal: Signal,
                               context: dict) -> float:
        """计算仓位大小"""
        pass

class Signal:
    """交易信号"""
    code: str
    direction: str  # 'buy' | 'sell' | 'close'
    price: float    # 目标价格（限价单）
    size: float     # 数量/比例
    reason: str     # 信号原因
    timestamp: datetime
    confidence: float  # 置信度 0-1
```

### 1.3 基于相似度的策略实现

```python
class SimilarityStrategy(BaseStrategy):
    """
    基于K线相似度的交易策略
    
    策略逻辑：
    1. 当发现高相似度(>80%)的历史模式时
    2. 参考该历史模式后续走势
    3. 如果历史上70%以上案例后续上涨，则买入
    4. 设置动态止盈止损
    """
    
    def __init__(self,
                 similarity_threshold: float = 0.75,
                 min_historical_samples: int = 5,
                 win_rate_threshold: float = 0.65,
                 holding_days: int = 20,
                 stop_loss: float = 0.05,
                 take_profit: float = 0.10):
        
        self.similarity_threshold = similarity_threshold
        self.min_historical_samples = min_historical_samples
        self.win_rate_threshold = win_rate_threshold
        self.holding_days = holding_days
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        
    def generate_signals(self, data, context):
        signals = []
        
        # 1. 获取相似度分析结果
        similar_patterns = find_similar_patterns(
            data, 
            window_size=30,
            top_n=10
        )
        
        if not similar_patterns:
            return signals
        
        # 2. 统计历史表现
        up_count = 0
        total_return = 0
        
        for pattern in similar_patterns:
            future_return = self._calc_future_return(pattern)
            if future_return > 0:
                up_count += 1
            total_return += future_return
        
        win_rate = up_count / len(similar_patterns)
        avg_return = total_return / len(similar_patterns)
        
        # 3. 生成买入信号
        if (win_rate >= self.win_rate_threshold and 
            avg_return > 0.03 and
            len(similar_patterns) >= self.min_historical_samples):
            
            signals.append(Signal(
                code=data['code'].iloc[0],
                direction='buy',
                price=data['close'].iloc[-1],
                size=self.calculate_position_size(
                    win_rate, avg_return, context
                ),
                reason=f"相似度{similar_patterns[0]['similarity']:.1%}, "
                       f"历史胜率{win_rate:.1%}, 平均收益{avg_return:.1%}",
                timestamp=datetime.now(),
                confidence=win_rate
            ))
        
        # 4. 检查止盈止损
        for code, position in context['positions'].items():
            current_price = data[data['code'] == code]['close'].iloc[-1]
            
            if position.holding_days >= self.holding_days:
                signals.append(Signal(
                    code=code, direction='close',
                    reason=f"持有期满{self.holding_days}天"
                ))
            elif current_price <= position.entry_price * (1 - self.stop_loss):
                signals.append(Signal(
                    code=code, direction='close',
                    reason=f"触发止损{-self.stop_loss:.1%}"
                ))
            elif current_price >= position.entry_price * (1 + self.take_profit):
                signals.append(Signal(
                    code=code, direction='close',
                    reason=f"触发止盈{self.take_profit:.1%}"
                ))
        
        return signals
```

### 1.4 回测结果可视化

#### 界面设计

```
┌─────────────────────────────────────────────────────────────┐
│                    策略回测报告                               │
├─────────────────────────────────────────────────────────────┤
│  总收益率: +45.2%    年化收益: +23.5%    夏普比率: 1.85       │
│  最大回撤: -12.3%    胜率: 62.5%         交易次数: 156       │
├─────────────────────────────────────────────────────────────┤
│  [资金曲线图 - 与基准对比]                                    │
│  [回撤曲线图]                                                │
│  [月度收益热力图]                                            │
├─────────────────────────────────────────────────────────────┤
│  交易记录表格（可导出CSV）                                    │
├─────────────────────────────────────────────────────────────┤
│  [参数优化]  [蒙特卡洛模拟]  [多标的回测]                     │
└─────────────────────────────────────────────────────────────┘
```

### 1.5 关键指标计算

```python
class PerformanceAnalyzer:
    """绩效分析器"""
    
    @staticmethod
    def calc_sharpe_ratio(returns: pd.Series, 
                         risk_free_rate: float = 0.03) -> float:
        """夏普比率"""
        excess_returns = returns - risk_free_rate / 252
        return np.sqrt(252) * excess_returns.mean() / excess_returns.std()
    
    @staticmethod
    def calc_max_drawdown(equity_curve: pd.Series) -> float:
        """最大回撤"""
        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        return drawdown.min()
    
    @staticmethod
    def calc_calmar_ratio(returns: pd.Series, 
                         max_drawdown: float) -> float:
        """卡玛比率 = 年化收益 / 最大回撤"""
        annual_return = returns.mean() * 252
        return annual_return / abs(max_drawdown)
    
    @staticmethod
    def calc_var(returns: pd.Series, confidence: float = 0.05) -> float:
        """风险价值 VaR"""
        return np.percentile(returns, confidence * 100)
```

---

## 二、深度学习模块详细设计

### 2.1 技术选型

| 层级 | 技术 | 理由 |
|------|------|------|
| 框架 | PyTorch | 动态图，调试方便，社区活跃 |
| 特征工程 | Feature-engine / Custom | 灵活处理时序特征 |
| 模型版本 | MLflow / DVC | 模型管理和实验追踪 |
| 推理加速 | ONNX Runtime | 生产环境快速推理 |

### 2.2 特征工程系统

```python
class FeatureEngine:
    """特征工程系统"""
    
    def __init__(self):
        self.feature_configs = {
            # 价格特征
            'price_momentum': {
                'type': 'rolling',
                'windows': [5, 10, 20, 60],
                'funcs': ['returns', 'std', 'skew']
            },
            # 技术指标
            'technical': {
                'indicators': ['rsi', 'macd', 'kdj', 'bollinger']
            },
            # 相似度特征（核心）
            'similarity': {
                'top_n': 10,
                'features': [
                    'avg_similarity',
                    'max_similarity', 
                    'win_rate',
                    'avg_future_return',
                    'pattern_trend',
                    'volume_similarity'
                ]
            },
            # 市场特征
            'market': {
                'index_corr': True,  # 与指数相关性
                'sector_rank': True, # 行业内排名
                'market_state': True # 牛熊状态
            }
        }
    
    def generate_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成特征矩阵"""
        features = pd.DataFrame(index=df.index)
        
        # 价格动量
        for window in self.feature_configs['price_momentum']['windows']:
            features[f'return_{window}d'] = df['close'].pct_change(window)
            features[f'volatility_{window}d'] = df['close'].pct_change().rolling(window).std()
        
        # 技术指标
        features['rsi_14'] = ta.momentum.rsi(df['close'], window=14)
        features['macd'] = ta.trend.macd_diff(df['close'])
        
        # 相似度特征（与现有系统联动）
        similarity_result = self._get_similarity_features(df)
        features = pd.concat([features, similarity_result], axis=1)
        
        return features.dropna()
```

### 2.3 模型架构

#### 方案A：XGBoost（推荐先实现）

```python
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit

class XGBoostPredictor:
    """XGBoost预测模型"""
    
    def __init__(self):
        self.model = None
        self.feature_importance = None
        
    def train(self, 
             X_train: pd.DataFrame, 
             y_train: pd.Series,
             X_val: pd.DataFrame = None,
             y_val: pd.Series = None):
        """
        训练模型
        
        任务：预测未来N日收益率是否超过阈值
        """
        params = {
            'objective': 'binary:logistic',  # 二分类：涨/跌
            'eval_metric': ['auc', 'logloss'],
            'max_depth': 6,
            'learning_rate': 0.05,
            'n_estimators': 500,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'reg_alpha': 0.1,
            'reg_lambda': 1.0,
            'random_state': 42
        }
        
        self.model = xgb.XGBClassifier(**params)
        
        eval_set = [(X_train, y_train)]
        if X_val is not None:
            eval_set.append((X_val, y_val))
        
        self.model.fit(
            X_train, y_train,
            eval_set=eval_set,
            early_stopping_rounds=50,
            verbose=100
        )
        
        # 特征重要性
        self.feature_importance = pd.DataFrame({
            'feature': X_train.columns,
            'importance': self.model.feature_importances_
        }).sort_values('importance', ascending=False)
    
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """预测上涨概率"""
        return self.model.predict_proba(X)[:, 1]
    
    def explain_prediction(self, X: pd.DataFrame, idx: int) -> dict:
        """使用SHAP解释预测结果"""
        import shap
        
        explainer = shap.TreeExplainer(self.model)
        shap_values = explainer.shap_values(X.iloc[idx:idx+1])
        
        return {
            'base_value': explainer.expected_value,
            'shap_values': shap_values[0],
            'features': X.columns.tolist()
        }
```

#### 方案B：LSTM时序模型（进阶）

```python
import torch
import torch.nn as nn

class LSTMPredictor(nn.Module):
    """LSTM预测模型"""
    
    def __init__(self, 
                 input_size: int = 50,  # 特征数
                 hidden_size: int = 128,
                 num_layers: int = 2,
                 output_size: int = 3,   # 涨/平/跌
                 dropout: float = 0.2):
        super().__init__()
        
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout,
            bidirectional=True
        )
        
        self.fc = nn.Sequential(
            nn.Linear(hidden_size * 2, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, output_size)
        )
        
    def forward(self, x):
        # x: (batch, seq_len, features)
        lstm_out, (hidden, cell) = self.lstm(x)
        
        # 取最后时刻的隐藏状态
        last_hidden = lstm_out[:, -1, :]
        
        output = self.fc(last_hidden)
        return output

class StockDataset(torch.utils.data.Dataset):
    """股票数据集"""
    
    def __init__(self, 
                 features: np.ndarray,
                 labels: np.ndarray,
                 seq_length: int = 30):
        self.features = features
        self.labels = labels
        self.seq_length = seq_length
        
    def __len__(self):
        return len(self.features) - self.seq_length
    
    def __getitem__(self, idx):
        x = self.features[idx:idx+self.seq_length]
        y = self.labels[idx+self.seq_length-1]
        return torch.FloatTensor(x), torch.LongTensor([y])[0]
```

### 2.4 训练流程

```python
class ModelTrainer:
    """模型训练管理器"""
    
    def __init__(self, model_dir: str = './models'):
        self.model_dir = model_dir
        os.makedirs(model_dir, exist_ok=True)
        
    def train_with_cross_validation(self,
                                   data: pd.DataFrame,
                                   feature_cols: List[str],
                                   target_col: str,
                                   n_splits: int = 5) -> dict:
        """
        时间序列交叉验证训练
        
        Args:
            data: 完整数据集
            feature_cols: 特征列
            target_col: 目标列（未来N日是否上涨）
            n_splits: 交叉验证折数
        """
        results = []
        
        # 时间序列分割
        tscv = TimeSeriesSplit(n_splits=n_splits)
        
        for fold, (train_idx, val_idx) in enumerate(tscv.split(data)):
            print(f"\n=== Fold {fold + 1}/{n_splits} ===")
            
            X_train = data.iloc[train_idx][feature_cols]
            y_train = data.iloc[train_idx][target_col]
            X_val = data.iloc[val_idx][feature_cols]
            y_val = data.iloc[val_idx][target_col]
            
            # 训练模型
            model = XGBoostPredictor()
            model.train(X_train, y_train, X_val, y_val)
            
            # 评估
            val_pred = model.predict_proba(X_val)
            auc = roc_auc_score(y_val, val_pred)
            
            print(f"Fold {fold+1} AUC: {auc:.4f}")
            
            results.append({
                'fold': fold + 1,
                'model': model,
                'val_auc': auc,
                'feature_importance': model.feature_importance
            })
        
        # 保存最优模型
        best_fold = max(results, key=lambda x: x['val_auc'])
        self._save_model(best_fold['model'], 'best_xgb_model.pkl')
        
        return {
            'avg_auc': np.mean([r['val_auc'] for r in results]),
            'best_model': best_fold['model'],
            'feature_importance': best_fold['feature_importance']
        }
```

### 2.5 模型集成到主系统

```python
# 在 similarity search 结果中加入ML预测

def enhanced_similarity_analysis(df, **kwargs):
    """增强版相似度分析 - 融合ML预测"""
    
    # 1. 原有相似度搜索
    similar_patterns = find_similar_patterns(df, **kwargs)
    
    # 2. 加载预训练ML模型
    ml_model = load_model('best_xgb_model.pkl')
    
    # 3. 生成特征
    features = feature_engine.generate_features(df)
    latest_features = features.iloc[-1:]
    
    # 4. ML预测
    ml_proba = ml_model.predict_proba(latest_features)[0]
    
    # 5. 融合相似度和ML结果
    similarity_win_rate = calculate_win_rate(similar_patterns)
    
    # 加权融合（可配置权重）
    final_proba = (
        0.6 * similarity_win_rate + 
        0.4 * ml_proba
    )
    
    return {
        'similar_patterns': similar_patterns,
        'similarity_win_rate': similarity_win_rate,
        'ml_prediction': ml_proba,
        'ensemble_prediction': final_proba,
        'confidence': max(similarity_win_rate, ml_proba)
    }
```

---

## 三、技术架构建议

### 3.1 目录结构规划

```
万向金枢/
├── v1.0/                      # v1.0稳定版
├── v2.0/                      # v2.0开发版
│   ├── src/
│   │   ├── core/              # 核心算法
│   │   │   ├── similarity.py  # 相似度计算
│   │   │   ├── patterns.py    # 模式识别
│   │   │   └── utils.py
│   │   ├── data/              # 数据层
│   │   │   ├── tdx_reader.py
│   │   │   ├── data_loader.py
│   │   │   └── cache_manager.py
│   │   ├── strategy/          # 量化策略
│   │   │   ├── backtest.py
│   │   │   ├── signals.py
│   │   │   ├── portfolio.py
│   │   │   └── strategies/
│   │   ├── ml/                # 机器学习
│   │   │   ├── features.py
│   │   │   ├── models/
│   │   │   │   ├── xgboost_model.py
│   │   │   │   └── lstm_model.py
│   │   │   ├── trainer.py
│   │   │   └── inference.py
│   │   ├── api/               # 对外接口
│   │   │   ├── rest_api.py
│   │   │   └── websocket.py
│   │   └── web/               # Web界面
│   │       └── streamlit_app.py
│   ├── tests/                 # 测试
│   ├── models/                # 模型存储
│   ├── data/                  # 数据存储
│   └── notebooks/             # 实验notebook
└── docs/                      # 文档
```

### 3.2 数据库选型

| 数据类型 | 推荐方案 | 理由 |
|---------|---------|------|
| 行情数据 | InfluxDB / ClickHouse | 时序数据高性能写入和查询 |
| 特征数据 | PostgreSQL + TimescaleDB | 关系型+时序扩展 |
| 模型元数据 | SQLite / PostgreSQL | 轻量或企业级 |
| 缓存 | Redis | 实时数据缓存 |

### 3.3 部署架构

**开发阶段**：单机部署
```
[Streamlit App] ←→ [Python Backend] ←→ [TDX Local Files]
                          ↓
                    [Local SQLite]
```

**生产阶段**：微服务架构（远期）
```
[Nginx] ←→ [Streamlit Frontend]
              ↓
         [API Gateway]
              ↓
    ┌─────────┼─────────┐
    ↓         ↓         ↓
[Data Svc] [ML Svc] [Strategy Svc]
    ↓         ↓         ↓
[ClickHouse] [Redis] [PostgreSQL]
```

### 3.4 依赖升级

```txt
# requirements-v2.txt

# 基础
streamlit>=1.28.0
pandas>=2.0.0
numpy>=1.24.0
plotly>=5.15.0

# 数据处理
pyarrow>=12.0.0          # 高性能数据交换
polars>=0.19.0           # 比pandas更快的DataFrame

# 机器学习
xgboost>=2.0.0
lightgbm>=4.0.0
scikit-learn>=1.3.0
shap>=0.42.0             # 模型可解释性

# 深度学习（可选）
torch>=2.0.0
pytorch-lightning>=2.0.0  # 简化训练流程

# 特征工程
ta-lib>=0.4.0            # 技术指标
feature-engine>=1.6.0

# 数据库
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0   # PostgreSQL
redis>=5.0.0

# 模型管理
mlflow>=2.8.0
onnx>=1.15.0
onnxruntime>=1.16.0

# 任务调度
apscheduler>=3.10.0
celery>=5.3.0

# 监控
prometheus-client>=0.18.0
```

---

## 四、实施路线图（建议）

### Phase 2.1: 量化回测核心（2-3周）
**目标**：用户可以验证相似度信号的有效性

**里程碑**：
- [ ] 回测引擎基础框架
- [ ] 基于相似度的策略实现
- [ ] 回测结果可视化界面
- [ ] 策略参数优化功能

**产出**：
- 策略回测报告
- 历史信号表现统计
- 用户可配置的策略模板

### Phase 2.2: 特征工程（1-2周）
**目标**：构建高质量特征库

**里程碑**：
- [ ] 100+技术指标自动计算
- [ ] 相似度特征工程
- [ ] 特征重要性分析
- [ ] 特征存储系统

**产出**：
- 特征库文档
- 特征质量评估报告

### Phase 2.3: ML模型集成（3-4周）
**目标**：智能化上涨概率预测

**里程碑**：
- [ ] XGBoost基准模型
- [ ] 模型训练流程自动化
- [ ] 模型解释性可视化
- [ ] A/B测试框架

**产出**：
- 生产级预测模型
- 模型性能监控看板

### Phase 2.4: 实时信号系统（2周）
**目标**：从分析工具进化为交易助手

**里程碑**：
- [ ] 定时扫描任务
- [ ] 信号预警推送
- [ ] 信号历史追踪
- [ ] 模拟交易账户

**产出**：
- 实时信号监控界面
- 信号推送通知

---

## 五、风险评估与建议

### 技术风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 模型过拟合 | 高 | 高 | 严格的时间序列CV，正则化 |
| 数据泄露 | 中 | 高 | 特征只用历史数据，前向填充检查 |
| 回测过拟合 | 高 | 中 | 样本外测试，蒙特卡洛模拟 |
| 性能瓶颈 | 中 | 中 | 多进程并行，缓存优化 |

### 建议的MVP策略

**最简可行产品（2-3周可交付）**：
1. 仅实现XGBoost模型（比深度学习更快出效果）
2. 回测仅支持单股票（简化复杂度）
3. 特征只扩展20个核心指标（避免过度工程）
4. 信号推送先用邮件/系统通知（不做微信集成）

---

## 六、下一步行动

**本周可以开始的工作**：

1. **代码重构**：将当前streamlit_app.py按模块拆分
2. **数据准备**：整理历史相似模式数据作为训练集
3. **环境准备**：安装Phase 2所需的依赖包
4. **基线测试**：记录当前v1.0的基准性能（搜索速度、准确率）

**您需要决策的事项**：

1. **数据规模**：是否需要接入在线实时行情？（成本：每年约3000-10000元）
2. **硬件预算**：是否需要GPU？（深度学习训练需要，推理不需要）
3. **首个MVP**：先做量化回测还是ML预测？（建议先量化回测，立即可见效果）

**请告诉我您想先从哪个模块开始？** 我可以立即提供详细的代码框架和实现步骤。
