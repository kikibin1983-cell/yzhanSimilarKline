"""
MACD内存缓存 - 启动时预加载，扫描时零延迟

性能优化：
1. 启动时一次性加载所有缓存到内存
2. 扫描时直接从内存获取（纳秒级）
3. 避免每次扫描都重复磁盘IO

适用场景：全市场扫描需要反复使用MACD数据
"""
import pickle
from pathlib import Path
from typing import Dict, Optional
import time


class MACDMemoryCache:
    """
    MACD内存缓存管理器
    
    启动时加载所有缓存到内存，运行时直接访问内存
    牺牲启动时间（约5-15秒），换取扫描时的极速体验
    """
    
    def __init__(self, cache_dir: str = "./macd_precompute_cache"):
        self.cache_dir = Path(cache_dir)
        self.memory_cache: Dict[str, dict] = {}  # {code: macd_data}
        self.loaded_count = 0
        self.load_time = 0
        
    def preload_all(self, show_progress: bool = True) -> int:
        """
        预加载所有MACD缓存到内存
        
        参数：
            show_progress: 是否显示进度
        
        返回：
            成功加载的股票数量
        """
        if show_progress:
            print(f"[内存缓存] 开始加载所有MACD缓存...")
        
        start = time.time()
        loaded = 0
        
        # 遍历所有.pkl文件
        if self.cache_dir.exists():
            pkl_files = list(self.cache_dir.rglob("*.pkl"))
            total = len(pkl_files)
            
            for i, pkl_file in enumerate(pkl_files):
                try:
                    code = pkl_file.stem
                    
                    # 优化：只保留文件名中的股票代码部分
                    # 例如: "D:\d\yzhanSimilarKline\macd_precompute_cache\6\600000.pkl" -> "600000"
                    code_parts = code.split('\\')
                    if len(code_parts) > 0:
                        code = code_parts[-1]  # 取最后一部分
                    
                    with open(pkl_file, 'rb') as f:
                        try:
                            data = pickle.load(f)
                            self.memory_cache[code] = data
                            loaded += 1
                            
                            # 进度显示
                            if show_progress and (loaded % 1000 == 0 or loaded == total):
                                print(f"[内存缓存] 加载进度: {loaded}/{total} 只股票")
                        except Exception as e:
                            if show_progress:
                                print(f"[内存缓存] 加载失败 {pkl_file}: {e}")
                except Exception as e:
                    if show_progress:
                        print(f"[内存缓存] 跳过文件 {pkl_file}: {e}")
        
        self.loaded_count = loaded
        self.load_time = time.time() - start
        
        if show_progress:
            print(f"[内存缓存] 加载完成！{loaded} 只股票，耗时 {self.load_time:.1f} 秒")
            print(f"[内存缓存] 内存占用约: {loaded * 0.3:.1f} MB")  # 每只股票约0.3MB
        
        return loaded
    
    def get(self, code: str) -> Optional[dict]:
        """
        从内存获取MACD数据（纳秒级）
        
        参数：
            code: 股票代码
        
        返回：
            MACD数据字典或None
        """
        return self.memory_cache.get(code)
    
    def has(self, code: str) -> bool:
        """检查缓存是否存在"""
        return code in self.memory_cache
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            'loaded_count': self.loaded_count,
            'load_time': self.load_time,
            'memory_cache_size': len(self.memory_cache),
            'estimated_memory_mb': len(self.memory_cache) * 0.3  # 粗略估计
        }
    
    def clear(self):
        """清空内存缓存"""
        self.memory_cache.clear()
        self.loaded_count = 0


# 全局单例
_macd_memory_cache: Optional[MACDMemoryCache] = None


def get_macd_memory_cache(cache_dir: str = "./macd_precompute_cache", 
                          preload: bool = True,
                          show_progress: bool = True) -> MACDMemoryCache:
    """
    获取全局内存缓存实例
    
    参数：
        cache_dir: 缓存目录
        preload: 是否预加载所有数据
        show_progress: 是否显示加载进度
    
    返回：
        MACDMemoryCache实例
    """
    global _macd_memory_cache
    
    if _macd_memory_cache is None:
        _macd_memory_cache = MACDMemoryCache(cache_dir)
        
        if preload:
            _macd_memory_cache.preload_all(show_progress=show_progress)
    
    return _macd_memory_cache


def preload_macd_cache_async():
    """
    异步预加载（用于Streamlit启动）
    
    返回加载的缓存数量
    """
    cache = get_macd_memory_cache(preload=True, show_progress=True)
    return cache.loaded_count


if __name__ == "__main__":
    # 测试
    import sys
    
    print("=" * 60)
    print("MACD内存缓存测试")
    print("=" * 60)
    
    # 加载缓存
    cache = get_macd_memory_cache()
    
    # 统计信息
    stats = cache.get_stats()
    print(f"\n缓存统计:")
    print(f"  已加载: {stats['loaded_count']} 只股票")
    print(f"  加载时间: {stats['load_time']:.1f} 秒")
    print(f"  内存占用: {stats['estimated_memory_mb']:.1f} MB")
    
    # 测试访问速度
    if stats['loaded_count'] > 0:
        test_code = list(cache.memory_cache.keys())[0]
        
        import time
        start = time.time()
        for _ in range(10000):
            _ = cache.get(test_code)
        elapsed = time.time() - start
        
        print(f"\n访问速度测试:")
        print(f"  10000次访问耗时: {elapsed*1000:.2f} 毫秒")
        print(f"  平均每次访问: {elapsed*1000000/10000:.2f} 纳秒")
    
    print("\n" + "=" * 60)