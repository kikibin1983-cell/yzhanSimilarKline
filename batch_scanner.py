"""
批量数据读取器 - 减少IO开销
"""
import os
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

def batch_read_stocks(stock_codes, tdx_path, max_workers=16):
    """
    批量并行读取多只股票数据
    
    参数：
        stock_codes: 股票代码列表
        tdx_path: TDX数据路径
        max_workers: 并行读取线程数
    
    返回：
        dict: {code: df}
    """
    from tdx_reader import read_tdx_data
    
    results = {}
    
    def read_single(code):
        try:
            df = read_tdx_data(code=code, security_type='stock')
            return code, df
        except:
            return code, None
    
    # 并行读取
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(read_single, code): code for code in stock_codes}
        
        for future in futures:
            code, df = future.result()
            if df is not None:
                results[code] = df
    
    return results


class StreamingScanner:
    """
    流式扫描器 - 边读边处理，不等待全部读取完成
    """
    
    def __init__(self, batch_size=50):
        self.batch_size = batch_size
        self.results = []
        
    def scan_streaming(self, stock_codes, process_func, max_workers=8):
        """
        流式扫描
        
        参数：
            stock_codes: 股票代码列表
            process_func: 处理函数 (code, df) -> result
            max_workers: 并行数
        """
        from concurrent.futures import ProcessPoolExecutor, as_completed
        
        # 分批处理
        batches = [stock_codes[i:i+self.batch_size] 
                  for i in range(0, len(stock_codes), self.batch_size)]
        
        total_processed = 0
        
        for batch_idx, batch in enumerate(batches):
            # 并行处理这一批
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(process_func, code): code 
                    for code in batch
                }
                
                for future in as_completed(futures):
                    result = future.result()
                    total_processed += 1
                    
                    if result:
                        self.results.append(result)
                    
                    # 每处理完一批，更新进度
                    if total_processed % 50 == 0:
                        print(f"已处理 {total_processed} 只，发现 {len(self.results)} 只符合条件的股票")
        
        return self.results


if __name__ == "__main__":
    print("批量读取器和流式扫描器已加载")
