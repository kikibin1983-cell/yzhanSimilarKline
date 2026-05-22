"""
数据预加载服务 - 启动时加载所有股票数据到内存

功能：
1. 启动时预加载所有股票数据（日线、周线、月线）
2. 内存缓存，支持快速访问
3. 后台线程异步加载，不阻塞UI
4. 支持增量更新

性能提升：
- 消除扫描时的磁盘IO
- TAB2扫描从10-15分钟降到2-3分钟
"""
import os
import time
import threading
import pickle
from pathlib import Path
from typing import Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pandas as pd
import numpy as np

# 全局缓存实例
_preload_service = None


class StockDataPreloadService:
    """
    股票数据预加载服务
    
    启动时异步加载所有股票数据到内存，扫描时直接从内存读取
    
    使用方式：
        service = StockDataPreloadService()
        service.start()  # 后台加载
        df = service.get_stock_data('600000')  # 从内存获取
    """
    
    def __init__(self, 
                 tdx_path: str = r"D:\new_tdx\vipdoc",
                 max_workers: int = 16,
                 cache_dir: str = "./stock_cache"):
        """
        初始化预加载服务
        
        参数：
            tdx_path: 通达信数据路径
            max_workers: 并行加载线程数
            cache_dir: 磁盘缓存目录
        """
        self.tdx_path = tdx_path
        self.max_workers = max_workers
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
        # 内存缓存
        self.daily_cache: Dict[str, pd.DataFrame] = {}
        self.weekly_cache: Dict[str, pd.DataFrame] = {}
        self.monthly_cache: Dict[str, pd.DataFrame] = {}
        
        # 元数据
        self.stock_list: List[Dict] = []
        self.load_stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'start_time': None,
            'end_time': None,
            'elapsed': 0
        }
        
        # 状态
        self.is_loaded = False
        self.is_loading = False
        self._lock = threading.Lock()
        
    def start(self, background: bool = True):
        """
        启动预加载
        
        参数：
            background: 是否后台加载（不阻塞UI）
        """
        if background:
            thread = threading.Thread(target=self._load_all_stocks, daemon=True)
            thread.start()
            print("[预加载服务] 后台加载已启动")
        else:
            self._load_all_stocks()
    
    def _load_all_stocks(self):
        """加载所有股票数据"""
        if self.is_loaded or self.is_loading:
            return
        
        with self._lock:
            self.is_loading = True
            self.load_stats['start_time'] = datetime.now()
        
        print(f"[预加载服务] 开始加载股票数据...")
        print(f"[预加载服务] 通达信路径: {self.tdx_path}")
        
        try:
            # 导入TDX读取器
            from tdx_reader import get_tdx_reader, read_tdx_data
            
            tdx_reader = get_tdx_reader(self.tdx_path)
            available = tdx_reader.get_available_codes()
            
            # 合并沪深股票代码
            all_codes = list(available.get('sh', [])) + list(available.get('sz', []))
            self.load_stats['total'] = len(all_codes)
            
            print(f"[预加载服务] 发现 {len(all_codes)} 只股票")
            
            # 尝试加载磁盘缓存
            cache_file = self.cache_dir / "stock_data_cache.pkl"
            if cache_file.exists():
                print(f"[预加载服务] 发现磁盘缓存，尝试加载...")
                try:
                    with open(cache_file, 'rb') as f:
                        cached_data = pickle.load(f)
                        if cached_data.get('codes') == set(all_codes[:100]):  # 验证部分代码
                            self.daily_cache = cached_data.get('daily', {})
                            self.weekly_cache = cached_data.get('weekly', {})
                            self.monthly_cache = cached_data.get('monthly', {})
                            self.load_stats['success'] = len(self.daily_cache)
                            print(f"[预加载服务] 从缓存加载 {self.load_stats['success']} 只股票")
                            self._finalize_loading()
                            return
                except Exception as e:
                    print(f"[预加载服务] 缓存加载失败: {e}，将从磁盘读取")
            
            # 并行加载股票数据
            # 适中的线程数，平衡性能和资源
            effective_workers = min(self.max_workers, 8)  # 最多8线程
            print(f"[预加载服务] 使用 {effective_workers} 个线程并行加载...")
            print(f"[预加载服务] 预计耗时: {len(all_codes) * 0.02 / effective_workers:.0f} 秒")
            
            failed_count = 0
            
            def load_single_stock(code):
                """加载单只股票数据"""
                try:
                    df = read_tdx_data(code=code, security_type='stock')
                    if df is not None and not df.empty:
                        # 确保trade_date是datetime类型
                        if 'trade_date' not in df.columns:
                            return code, None, "no_trade_date"
                        if not isinstance(df['trade_date'].iloc[0], pd.Timestamp):
                            df['trade_date'] = pd.to_datetime(df['trade_date'])
                        return code, df, None
                    return code, None, "empty_df"
                except Exception as e:
                    return code, None, str(e)[:50]  # 只返回前50个字符的错误
            
            # 使用线程池并行加载
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                futures = {executor.submit(load_single_stock, code): code 
                          for code in all_codes}
                
                completed = 0
                start_load_time = time.time()
                
                try:
                    for future in as_completed(futures, timeout=600):  # 10分钟总超时
                        try:
                            result = future.result(timeout=5)  # 每个股票5秒超时
                            code, df, error = result
                            
                            if df is not None:
                                self.daily_cache[code] = df
                                self.load_stats['success'] += 1
                            elif error:
                                failed_count += 1
                                if failed_count <= 10:  # 只显示前10个错误
                                    print(f"[预加载服务] 加载失败 {code}: {error}")
                        except TimeoutError:
                            failed_count += 1
                            code = futures[future]
                            if failed_count <= 10:
                                print(f"[预加载服务] 超时 {code} (>5秒)")
                        except Exception as e:
                            failed_count += 1
                            if failed_count <= 10:
                                print(f"[预加载服务] 异常 {code}: {str(e)[:50]}")
                        
                        completed += 1
                        
                        # 每100只显示一次进度，更频繁的反馈
                        if completed % 100 == 0 or completed == len(all_codes):
                            progress = completed / len(all_codes) * 100
                            elapsed = time.time() - start_load_time
                            avg_time = elapsed / completed if completed > 0 else 0
                            remaining = (len(all_codes) - completed) * avg_time
                            print(f"[预加载服务] 进度: {completed}/{len(all_codes)} ({progress:.1f}%) | 成功: {self.load_stats['success']} | 失败: {failed_count} | 剩余时间: {remaining:.0f}秒")
                except TimeoutError:
                    # 高层超时：取消未完成的futures
                    unfinished = [f for f in futures if not f.done()]
                    print(f"[预加载服务] 总超时，已完成 {completed}/{len(all_codes)}，{len(unfinished)} 个任务未完成")
                    for future in unfinished:
                        future.cancel()
                    failed_count += len(unfinished)
            
            # 保存到磁盘缓存
            print(f"[预加载服务] 保存缓存到磁盘...")
            try:
                cache_data = {
                    'daily': self.daily_cache,
                    'weekly': self.weekly_cache,
                    'monthly': self.monthly_cache,
                    'codes': list(self.daily_cache.keys()),
                    'timestamp': datetime.now().isoformat()
                }
                with open(cache_file, 'wb') as f:
                    pickle.dump(cache_data, f)
                print(f"[预加载服务] 缓存已保存")
            except Exception as e:
                print(f"[预加载服务] 缓存保存失败: {e}")
            
            self._finalize_loading()
            
        except Exception as e:
            print(f"[预加载服务] 加载失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            with self._lock:
                self.is_loading = False
    
    def _finalize_loading(self):
        """完成加载"""
        elapsed = (datetime.now() - self.load_stats['start_time']).total_seconds()
        self.load_stats['end_time'] = datetime.now()
        self.load_stats['elapsed'] = elapsed
        
        with self._lock:
            self.is_loaded = True
        
        memory_mb = sum(
            df.memory_usage(deep=True).sum() 
            for df in self.daily_cache.values()
        ) / (1024 * 1024)
        
        print(f"[预加载服务] 加载完成！")
        print(f"  成功: {self.load_stats['success']}/{self.load_stats['total']} 只股票")
        print(f"  耗时: {elapsed:.1f} 秒")
        print(f"  内存占用: {memory_mb:.1f} MB")
    
    def get_stock_data(self, 
                       code: str, 
                       period: str = 'daily') -> Optional[pd.DataFrame]:
        """
        获取股票数据（从内存）
        
        参数：
            code: 股票代码
            period: 周期 ('daily', 'weekly', 'monthly')
        
        返回：
            DataFrame或None
        """
        if period == 'daily':
            return self.daily_cache.get(code)
        elif period == 'weekly':
            if code not in self.weekly_cache and code in self.daily_cache:
                # 按需生成周线
                self.weekly_cache[code] = self._convert_to_weekly(self.daily_cache[code])
            return self.weekly_cache.get(code)
        elif period == 'monthly':
            if code not in self.monthly_cache and code in self.daily_cache:
                # 按需生成月线
                self.monthly_cache[code] = self._convert_to_monthly(self.daily_cache[code])
            return self.monthly_cache.get(code)
        return None
    
    def _convert_to_weekly(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        """日线转周线"""
        if daily_df is None or daily_df.empty:
            return None
        
        try:
            df = daily_df.copy()
            df.set_index('trade_date', inplace=True)
            weekly = df.resample('W').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            weekly = weekly.reset_index()
            return weekly
        except:
            return None
    
    def _convert_to_monthly(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        """日线转月线"""
        if daily_df is None or daily_df.empty:
            return None
        
        try:
            df = daily_df.copy()
            df.set_index('trade_date', inplace=True)
            monthly = df.resample('ME').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            monthly = monthly.reset_index()
            return monthly
        except:
            return None
    
    def get_all_codes(self) -> List[str]:
        """获取所有已加载的股票代码"""
        return list(self.daily_cache.keys())
    
    def get_stats(self) -> Dict:
        """获取加载统计信息"""
        return {
            **self.load_stats,
            'is_loaded': self.is_loaded,
            'is_loading': self.is_loading,
            'cache_size': len(self.daily_cache)
        }
    
    def search_stocks_by_pattern(self, 
                                  source_df: pd.DataFrame,
                                  window_size: int = 30,
                                  top_n: int = 10,
                                  threshold: float = 0.7) -> List[Dict]:
        """
        快速模式匹配（仅使用内存数据）
        
        参数：
            source_df: 源股票数据
            window_size: 窗口大小
            top_n: 返回前N个
            threshold: 相似度阈值
        
        返回：
            [{code, similarity, ...}, ...]
        """
        if not self.is_loaded:
            print("[预加载服务] 数据未加载完成")
            return []
        
        # 准备源数据
        source_close = source_df['close'].values[-window_size:]
        source_mean = np.mean(source_close)
        source_std = np.std(source_close)
        source_norm = (source_close - source_mean) / (source_std + 1e-10)
        
        # 批量计算相似度
        results = []
        for code, df in self.daily_cache.items():
            if df is None or len(df) < window_size:
                continue
            
            try:
                close = df['close'].values[-window_size:]
                mean = np.mean(close)
                std = np.std(close)
                norm = (close - mean) / (std + 1e-10)
                
                # 皮尔逊相关系数
                corr = np.sum(norm * source_norm) / window_size
                # 欧氏距离
                dist = np.sqrt(np.sum((norm - source_norm) ** 2))
                dist_score = 1.0 / (1.0 + dist / window_size)
                
                # 综合相似度
                similarity = 0.7 * (corr + 1.0) / 2.0 + 0.3 * dist_score
                
                if similarity >= threshold:
                    results.append({
                        'code': code,
                        'similarity': similarity,
                        'window_size': window_size
                    })
            except:
                continue
        
        # 排序取top_n
        results.sort(key=lambda x: x['similarity'], reverse=True)
        return results[:top_n]
    
    def clear_cache(self):
        """清空缓存"""
        with self._lock:
            self.daily_cache.clear()
            self.weekly_cache.clear()
            self.monthly_cache.clear()
            self.is_loaded = False
            print("[预加载服务] 缓存已清空")


def get_preload_service(tdx_path: str = None) -> StockDataPreloadService:
    """
    获取全局预加载服务实例
    
    参数：
        tdx_path: 通达信路径（可选，默认使用保存的路径）
    
    返回：
        StockDataPreloadService实例
    """
    global _preload_service
    
    if _preload_service is None:
        if tdx_path is None:
            tdx_path = r"D:\new_tdx\vipdoc"
        _preload_service = StockDataPreloadService(tdx_path=tdx_path)
    
    return _preload_service


# 测试代码
if __name__ == "__main__":
    print("=" * 60)
    print("数据预加载服务测试")
    print("=" * 60)
    
    service = StockDataPreloadService()
    
    # 同步加载（测试用）
    service.start(background=False)
    
    # 获取统计信息
    stats = service.get_stats()
    print(f"\n加载统计:")
    print(f"  成功: {stats['success']} 只股票")
    print(f"  耗时: {stats['elapsed']:.1f} 秒")
    print(f"  已加载: {stats['is_loaded']}")
    
    # 测试获取数据
    if stats['success'] > 0:
        codes = service.get_all_codes()
        print(f"\n获取前5只股票:")
        for code in codes[:5]:
            df = service.get_stock_data(code)
            print(f"  {code}: {len(df)} 天数据")
    
    print("\n" + "=" * 60)