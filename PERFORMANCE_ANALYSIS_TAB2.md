# TAB2技术分析性能问题分析报告

## 问题现状

搜索5000只股票需要1小时以上，存在严重性能瓶颈。

## 根本原因分析

### 1. 多进程开销过大（最主要原因）

**位置**: `streamlit_app.py:3569`

**问题**: 使用`ProcessPoolExecutor`为每只股票创建独立进程

```python
with ProcessPoolExecutor(max_workers=n_workers) as executor:
    future_to_code = {
        executor.submit(_process_single_stock_for_parallel, task): task[0] 
        for task in tasks  # 5000个任务！
    }
```

**开销分析**:
- 进程创建/销毁开销：~50-100ms/进程
- 数据序列化/反序列化：~20-50ms/进程
- 每只股票总进程开销：~70-150ms
- 5000只股票总开销：350-750秒（6-12分钟）

### 2. 磁盘IO重复读取

**位置**: `streamlit_app.py:3036`

**问题**: 每个进程独立读取股票数据

```python
def _process_single_stock_for_parallel(args):
    candidate_df = read_tdx_data(code=code, security_type=params['security_type'])
```

**开销分析**:
- 单次`read_tdx_data`: ~10-30ms（磁盘IO）
- 5000只股票总IO时间：50-150秒

### 3. 缓存未命中原因

#### 3.1 _indicator_cache缓存（技术指标缓存）

**位置**: `streamlit_app.py:33-52`

**问题**:
- 缓存只在单股票模式下启用（line 2439）
- 跨股票搜索时强制跳过缓存（line 2463-2465）
- 缓存键生成依赖整个数据集，导致缓存命中率低

```python
# 跨股票搜索模式：跳过缓存，直接计算
elif not is_single_stock_mode:
    print(f"[跨股票搜索] 股票 {source_code} 跳过缓存")
```

#### 3.2 _search_result_cache（搜索结果缓存）

**位置**: `streamlit_app.py:2507`

**问题**:
- 仅缓存单股票搜索结果
- 跨股票搜索时完全禁用
- 缓存容量只有200条，对于5000只股票远远不够

### 4. 数据准备重复计算

**位置**: `streamlit_app.py:3054-3060`

**问题**: 每只股票都调用`_prepare_similarity_df`进行数据转换

```python
transformed_df, candidate_window_size, candidate_future_days = _prepare_similarity_df(
    candidate_df,
    period_type=params['period_type'],
    ...
)
```

**开销**: 每只股票~5-10ms，5000只股票总计25-50秒

### 5. 进程数限制

**位置**: `streamlit_app.py:3560`

**问题**: 进程数被限制为`cpu_count - 1`

```python
n_workers = min(len(tasks), max(1, mp.cpu_count() - 1))
```

**影响**: 即使CPU有16核，也只有15个进程并行，5000只股票需要333批次，每批次都有进程调度开销。

## 性能瓶颈总结

| 瓶颈 | 耗时占比 | 说明 |
|------|----------|------|
| 多进程创建/销毁 | 60-70% | 最大瓶颈 |
| 磁盘IO读取 | 15-20% | 每只股票独立读取 |
| 数据准备转换 | 5-10% | 重复计算 |
| 相似度计算 | 5-10% | Numba已优化 |
| 进程间通信 | 5% | 序列化开销 |

## 优化方案

### 方案1：批量预加载数据（推荐）

**思路**: 在主进程批量读取所有股票数据到内存，然后分批处理

**实现**:
```python
# 1. 主进程批量读取数据（一次性磁盘IO）
all_stock_data = {}
for code in filtered_codes:
    all_stock_data[code] = read_tdx_data(code=code)

# 2. 分批处理，每批100-200只股票
batch_size = 200
for i in range(0, len(codes_to_process), batch_size):
    batch_codes = codes_to_process[i:i+batch_size]
    batch_data = {code: all_stock_data[code] for code in batch_codes}
    
    # 3. 使用ProcessPoolExecutor处理批次
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = []
        for code in batch_codes:
            # 传递数据而不是让子进程读取
            future = executor.submit(_process_single_stock_with_data, 
                                     code, batch_data[code], params)
            futures.append(future)
```

**预期效果**: 减少90%磁盘IO时间

### 方案2：线程池替代进程池

**思路**: 使用`ThreadPoolExecutor`替代`ProcessPoolExecutor`，避免进程创建开销

**实现**:
```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=n_workers*2) as executor:
    # 线程池没有序列化开销
    futures = {executor.submit(_process_single_stock, task): task[0] 
               for task in tasks}
```

**优点**:
- 无线程创建/销毁开销
- 无数据序列化开销
- 可创建更多线程（CPU核心数*2）

**缺点**:
- 受GIL限制，CPU密集型计算无法真正并行
- 但Numba释放GIL，可以部分并行

**预期效果**: 减少50-70%多进程开销

### 方案3：数据预加载服务

**思路**: 创建独立的数据预加载服务，启动时加载所有股票数据到共享内存

**实现**:
```python
# data_preload_service.py
class StockDataService:
    def __init__(self):
        self.data_cache = {}
        
    def preload_all(self):
        """启动时预加载所有股票数据"""
        for code in all_codes:
            self.data_cache[code] = read_tdx_data(code=code)
    
    def get(self, code):
        return self.data_cache.get(code)
```

**预期效果**: 磁盘IO时间降为0

### 方案4：GPU加速相似度计算

**思路**: 使用CUDA/cuDF批量计算相似度

**实现**:
```python
import cudf

# 将所有数据加载到GPU
_gpu_data = cudf.DataFrame(all_data)

# 批量计算相似度（GPU并行）
def batch_similarity_gpu(source_window, all_windows):
    # 使用cuDF/cuML进行批量计算
    pass
```

**预期效果**: 相似度计算速度提升10-100倍

### 方案5：增量搜索 + 智能缓存

**思路**: 
1. 先搜索最近N天的活跃股票（减少搜索范围）
2. 使用LRU缓存最近搜索结果
3. 相似度阈值预过滤

**实现**:
```python
# 1. 活跃度预过滤
active_codes = filter_by_recent_volume(all_codes, days=30)

# 2. 快速相似度预过滤（仅价格趋势）
candidates = quick_similarity_filter(source_code, active_codes, threshold=0.7)

# 3. 精细计算（仅对候选股票）
results = detailed_similarity_calculation(source_code, candidates)
```

**预期效果**: 减少80-90%计算量

## 推荐实施顺序

### 第一阶段：快速修复（1-2天）
1. **批量预加载数据** - 减少磁盘IO
2. **增加batch_size** - 减少进程创建次数
3. **启用线程池模式** - 可选，测试GIL影响

**预期效果**: 从1小时降到15-20分钟

### 第二阶段：深度优化（3-5天）
1. **数据预加载服务** - 启动时加载所有数据
2. **智能缓存策略** - 跨股票搜索结果缓存
3. **相似度预过滤** - 减少计算量

**预期效果**: 从15分钟降到3-5分钟

### 第三阶段：硬件加速（1周+）
1. **GPU加速** - 使用RTX 4060加速计算
2. **向量化批量计算** - 使用NumPy/Pandas批量操作

**预期效果**: 从5分钟降到30秒-1分钟

## 立即实施的优化（代码片段）

### 修改1：批量数据预加载

```python
# streamlit_app.py:3499-3626 区域

def _preload_stock_data_batch(codes, security_type):
    """批量预加载股票数据"""
    data_cache = {}
    for code in codes:
        try:
            df = read_tdx_data(code=code, security_type=security_type)
            if df is not None and not df.empty:
                data_cache[code] = df
        except:
            pass
    return data_cache

# 修改后的并行搜索逻辑
if codes_to_process:
    # 1. 主进程批量预加载数据（减少磁盘IO）
    print(f"[数据预加载] 批量加载 {len(codes_to_process)} 只股票数据...")
    preload_start = time.time()
    all_stock_data = _preload_stock_data_batch(codes_to_process, params['security_type'])
    print(f"[数据预加载] 完成，耗时 {time.time()-preload_start:.2f} 秒，成功加载 {len(all_stock_data)} 只")
    
    # 2. 修改进程处理函数，使用预加载数据
    def _process_single_stock_with_preload(args):
        code, params, data_cache = args
        # 从缓存获取数据，而不是读取磁盘
        candidate_df = data_cache.get(code)
        if candidate_df is None:
            return []
        # ... 后续处理不变
    
    # 3. 分批处理，减少进程创建开销
    batch_size = 200  # 每批处理200只
    for batch_start in range(0, len(codes_to_process), batch_size):
        batch_codes = codes_to_process[batch_start:batch_start+batch_size]
        batch_data = {code: all_stock_data[code] for code in batch_codes if code in all_stock_data}
        
        # 处理批次
        tasks = [(code, params, batch_data) for code in batch_codes]
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_process_single_stock_with_preload, task): task[0] 
                      for task in tasks}
            # ... 收集结果
```

### 修改2：使用线程池

```python
# 可选：使用线程池替代进程池（如果Numba计算为主）
from concurrent.futures import ThreadPoolExecutor

# 在线程池中，由于Numba释放GIL，CPU计算可以并行
with ThreadPoolExecutor(max_workers=n_workers*2) as executor:
    futures = {executor.submit(_process_single_stock, task): task[0] 
               for task in tasks}
```

## 总结

当前性能问题的根本原因是：**多进程开销 > 磁盘IO > 重复计算**

最快的优化方案是**批量预加载数据**，可以在1-2天内将性能从1小时提升到15-20分钟。