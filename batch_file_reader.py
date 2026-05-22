"""
批量文件读取优化器
通过并行IO减少文件读取等待时间
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional
import pandas as pd


class BatchFileReader:
    """
    批量文件并行读取器
    
    优化点：
    1. 并行读取多个文件（IO密集型操作）
    2. 预加载文件列表到内存
    3. 异步预读取
    """
    
    def __init__(self, max_workers: int = 16):
        self.max_workers = max_workers
        self._file_cache = {}  # 文件路径缓存
        
    def preload_file_list(self, tdx_path: str, stock_codes: List[str]):
        """
        预加载文件路径，避免运行时重复拼接路径
        
        参数：
            tdx_path: TDX安装路径
            stock_codes: 股票代码列表
        """
        for code in stock_codes:
            # 判断市场
            if code.startswith('6') or code.startswith('5') or code.startswith('9'):
                market = 'sh'
            else:
                market = 'sz'
            
            file_path = os.path.join(tdx_path, market, 'lday', f'{market}{code}.day')
            self._file_cache[code] = file_path
    
    def read_single_file(self, code: str, read_func) -> tuple:
        """
        读取单个文件（供并行调用）
        
        参数：
            code: 股票代码
            read_func: 读取函数
        
        返回：
            (code, df) 元组
        """
        try:
            df = read_func(code=code, security_type='stock')
            return code, df
        except Exception as e:
            return code, None
    
    def batch_read(self, stock_codes: List[str], read_func, 
                   batch_size: int = 100) -> Dict[str, pd.DataFrame]:
        """
        批量并行读取多只股票数据
        
        参数：
            stock_codes: 股票代码列表
            read_func: 读取函数（如read_tdx_data）
            batch_size: 每批读取数量（避免内存爆炸）
        
        返回：
            dict: {code: df}
        """
        results = {}
        total = len(stock_codes)
        
        # 分批处理，避免一次性创建过多线程
        for i in range(0, total, batch_size):
            batch = stock_codes[i:i+batch_size]
            
            # 并行读取这一批
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_code = {
                    executor.submit(self.read_single_file, code, read_func): code 
                    for code in batch
                }
                
                for future in as_completed(future_to_code):
                    code, df = future.result()
                    if df is not None:
                        results[code] = df
            
            # 进度提示
            if (i + len(batch)) % 500 == 0:
                print(f"已读取 {min(i + batch_size, total)}/{total} 只股票")
        
        return results


class AsyncDataLoader:
    """
    异步数据预加载器
    
    在扫描一批股票的同时，后台预加载下一批数据
    """
    
    def __init__(self, read_func, preload_size: int = 50):
        self.read_func = read_func
        self.preload_size = preload_size
        self._preload_queue = []
        self._executor = ThreadPoolExecutor(max_workers=4)
        
    def preload(self, stock_codes: List[str]):
        """
        预加载下一批数据
        """
        self._preload_queue = []
        codes_to_load = stock_codes[:self.preload_size]
        
        for code in codes_to_load:
            future = self._executor.submit(self.read_func, code=code, security_type='stock')
            self._preload_queue.append((code, future))
    
    def get(self, code: str) -> Optional[pd.DataFrame]:
        """
        获取数据（优先从预加载队列）
        """
        # 检查是否在预加载队列
        for i, (c, future) in enumerate(self._preload_queue):
            if c == code:
                try:
                    df = future.result(timeout=5)
                    self._preload_queue.pop(i)
                    return df
                except:
                    return None
        
        # 不在队列，直接读取
        try:
            return self.read_func(code=code, security_type='stock')
        except:
            return None
    
    def close(self):
        """关闭线程池"""
        self._executor.shutdown(wait=False)


# 使用示例
if __name__ == "__main__":
    print("批量文件读取优化器已加载")
    print("用法：")
    print("  reader = BatchFileReader(max_workers=16)")
    print("  data = reader.batch_read(stock_codes, read_tdx_data)")
