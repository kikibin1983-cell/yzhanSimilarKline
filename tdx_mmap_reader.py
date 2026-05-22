"""
内存映射文件读取器
直接mmap读取TDX数据文件，避免文件IO拷贝
"""

import os
import mmap
import struct
import numpy as np
import pandas as pd
from datetime import datetime


class TDXMemoryMapReader:
    """
    TDX文件内存映射读取器
    
    优势：
    1. 避免文件读取的系统调用开销
    2. 内核自动缓存热点数据
    3. 零拷贝读取
    """
    
    def __init__(self, tdx_path: str):
        self.tdx_path = tdx_path
        self._mmap_cache = {}  # 缓存已mmap的文件
    
    def _get_file_path(self, code: str) -> str:
        """获取文件路径"""
        if code.startswith('6') or code.startswith('5') or code.startswith('9'):
            market = 'sh'
        else:
            market = 'sz'
        return os.path.join(self.tdx_path, market, 'lday', f'{market}{code}.day')
    
    def read_day_file_mmap(self, code: str) -> pd.DataFrame:
        """
        使用内存映射读取.day文件
        
        TDX日线文件格式：
        每32字节一条记录：
        - date: 4字节 (YYYYMMDD)
        - open: 4字节 * 100
        - high: 4字节 * 100
        - low: 4字节 * 100
        - close: 4字节 * 100
        - amount: 4字节 (元)
        - volume: 4字节 (股)
        - reserved: 4字节
        """
        file_path = self._get_file_path(code)
        
        if not os.path.exists(file_path):
            return None
        
        try:
            with open(file_path, 'rb') as f:
                # 内存映射
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                
                # 计算记录数
                record_size = 32
                num_records = len(mm) // record_size
                
                # 预分配数组
                dates = []
                opens = []
                highs = []
                lows = []
                closes = []
                volumes = []
                amounts = []
                
                # 逐条解析
                for i in range(num_records):
                    offset = i * record_size
                    # 解包数据
                    record = struct.unpack('IIIIIfII', mm[offset:offset+record_size])
                    
                    date_int = record[0]
                    date = datetime(
                        date_int // 10000,
                        (date_int % 10000) // 100,
                        date_int % 100
                    )
                    
                    dates.append(date)
                    opens.append(record[1] / 100.0)
                    highs.append(record[2] / 100.0)
                    lows.append(record[3] / 100.0)
                    closes.append(record[4] / 100.0)
                    amounts.append(record[5])
                    volumes.append(record[6])
                
                mm.close()
                
                # 创建DataFrame
                df = pd.DataFrame({
                    'trade_date': dates,
                    'open': opens,
                    'high': highs,
                    'low': lows,
                    'close': closes,
                    'volume': volumes,
                    'amount': amounts
                })
                
                return df
                
        except Exception as e:
            print(f"读取失败 {code}: {e}")
            return None
    
    def batch_read_mmap(self, codes: list) -> dict:
        """
        批量内存映射读取
        """
        results = {}
        for code in codes:
            df = self.read_day_file_mmap(code)
            if df is not None:
                results[code] = df
        return results


# 使用示例
if __name__ == "__main__":
    reader = TDXMemoryMapReader(r"D:\new_tdx\vipdoc")
    
    # 测试读取
    import time
    codes = ['000001', '600000', '000858']
    
    start = time.time()
    for code in codes:
        df = reader.read_day_file_mmap(code)
        print(f"{code}: {len(df)} 条记录")
    print(f"耗时: {time.time()-start:.3f}秒")
