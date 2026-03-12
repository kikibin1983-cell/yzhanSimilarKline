"""
通达信(TDX)数据读取模块
用于读取本地通达信数据文件，替代网络API
"""

import struct
import os
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import numpy as np


class TDXDataReader:
    """通达信数据读取器"""
    
    def __init__(self, tdx_data_dir: str = r"D:\new_tdx\vipdoc"):
        """
        初始化TDX数据读取器
        
        Args:
            tdx_data_dir: 通达信数据目录路径
        """
        self.tdx_data_dir = tdx_data_dir
        self._validate_tdx_directory()
        
    def _validate_tdx_directory(self):
        """验证通达信数据目录结构"""
        required_dirs = ['sz/lday', 'sh/lday']
        for dir_path in required_dirs:
            full_path = os.path.join(self.tdx_data_dir, dir_path)
            if not os.path.exists(full_path):
                raise FileNotFoundError(f"通达信数据目录不存在: {full_path}")
            if not os.listdir(full_path):
                raise ValueError(f"通达信数据目录为空: {full_path}")
                
    def _get_tdx_file_path(self, code: str) -> Optional[str]:
        """
        根据股票代码获取对应的TDX文件路径
        
        Args:
            code: 股票代码 (如: '000001', '600000')
            
        Returns:
            文件路径或None
        """
        # 判断交易所
        if code.startswith('6'):
            # 上海交易所
            exchange = 'sh'
        elif code.startswith('0') or code.startswith('3'):
            # 深圳交易所
            exchange = 'sz'
        else:
            # 无法识别
            return None
            
        # 构建文件路径
        file_name = f"{exchange}{code}.day"
        file_path = os.path.join(self.tdx_data_dir, exchange, 'lday', file_name)
        
        return file_path if os.path.exists(file_path) else None
        
    def read_day_file(self, file_path: str, start_date: Optional[str] = None, 
                      end_date: Optional[str] = None) -> pd.DataFrame:
        """
        读取通达信日线数据文件
        
        Args:
            file_path: .day文件路径
            start_date: 开始日期 (YYYYMMDD格式)
            end_date: 结束日期 (YYYYMMDD格式)
            
        Returns:
            DataFrame with columns: ['trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
            
        records = []
        
        with open(file_path, 'rb') as f:
            file_size = os.path.getsize(file_path)
            record_size = 32  # 每个记录32字节
            num_records = file_size // record_size
            
            for _ in range(num_records):
                data = f.read(record_size)
                if len(data) < record_size:
                    break
                    
                # 解析二进制数据
                # 格式: date(int), open(int), high(int), low(int), close(int), amount(float), volume(int), reserved(int)
                date_int, open_int, high_int, low_int, close_int, amount_float, volume_int, _ = \
                    struct.unpack('<IIIIIfII', data)
                
                # 转换日期
                date_str = str(date_int)
                if len(date_str) != 8:
                    continue
                    
                # 检查日期是否合理（不能是未来日期）
                # 获取当前年份
                from datetime import datetime
                current_year = datetime.now().year
                year = int(date_str[:4])
                
                # 过滤掉明显不合理的未来日期（超过当前年份+1）
                if year > current_year + 1:
                    print(f"警告: 跳过未来日期数据: {date_str}")
                    continue
                    
                # 过滤日期范围
                if start_date and date_str < start_date:
                    continue
                if end_date and date_str > end_date:
                    continue
                    
                # 转换价格 (根据研究，需要根据证券类型使用不同的系数)
                # 对于大多数A股股票，使用/100，但对于ETF等可能需要/1000
                # 这里先使用/100，后续可以根据证券类型调整
                price_divisor = 100.0  # 默认除数
                
                open_price = open_int / price_divisor
                high_price = high_int / price_divisor
                low_price = low_int / price_divisor
                close_price = close_int / price_divisor
                
                # 转换成交量 (乘以100)
                volume_shares = volume_int * 100
                
                records.append({
                    'trade_date': pd.to_datetime(date_str, format='%Y%m%d'),
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price,
                    'volume': volume_shares,
                    'amount': amount_float
                })
        
        if not records:
            return pd.DataFrame()
            
        df = pd.DataFrame(records)
        df = df.sort_values('trade_date', ascending=True)
        df = df.reset_index(drop=True)
        
        return df
        
    def get_stock_data(self, code: str, start_date: Optional[str] = None,
                       end_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取股票历史数据
        
        Args:
            code: 股票代码
            start_date: 开始日期 (YYYYMMDD格式)
            end_date: 结束日期 (YYYYMMDD格式)
            
        Returns:
            DataFrame with historical data
        """
        file_path = self._get_tdx_file_path(code)
        if not file_path:
            return pd.DataFrame()
            
        return self.read_day_file(file_path, start_date, end_date)
        
    def get_index_data(self, code: str, start_date: Optional[str] = None,
                       end_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取指数历史数据
        
        Note: 通达信中指数数据可能存储在不同的目录中
        这里先尝试与股票相同的方式读取
        """
        return self.get_stock_data(code, start_date, end_date)
        
    def get_etf_data(self, code: str, start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取ETF历史数据
        
        Note: 通达信中ETF数据可能存储在不同的目录中
        这里先尝试与股票相同的方式读取
        """
        return self.get_stock_data(code, start_date, end_date)
        
    def get_available_codes(self) -> Dict[str, List[str]]:
        """
        获取可用的股票代码列表
        
        Returns:
            字典: {'sh': [上海股票代码], 'sz': [深圳股票代码]}
        """
        available_codes = {'sh': [], 'sz': []}
        
        for exchange in ['sh', 'sz']:
            lday_dir = os.path.join(self.tdx_data_dir, exchange, 'lday')
            if not os.path.exists(lday_dir):
                continue
                
            for file_name in os.listdir(lday_dir):
                if file_name.endswith('.day'):
                    # 提取代码: sh600000.day -> 600000
                    code = file_name[2:-4]  # 去掉'sh'前缀和'.day'后缀
                    available_codes[exchange].append(code)
                    
        return available_codes
        
    def test_connection(self) -> bool:
        """测试TDX数据连接"""
        try:
            # 测试读取一个样本文件
            test_file = os.path.join(self.tdx_data_dir, 'sz', 'lday', 'sz000001.day')
            if os.path.exists(test_file):
                df = self.read_day_file(test_file, end_date='20231231')
                return not df.empty
            return False
        except Exception as e:
            print(f"TDX连接测试失败: {e}")
            return False


# 全局TDX读取器实例
_tdx_reader = None

def get_tdx_reader() -> TDXDataReader:
    """获取全局TDX读取器实例"""
    global _tdx_reader
    if _tdx_reader is None:
        _tdx_reader = TDXDataReader()
    return _tdx_reader


def read_tdx_data(code: str, security_type: str = 'stock', 
                  start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> pd.DataFrame:
    """
    读取TDX数据的便捷函数
    
    Args:
        code: 证券代码
        security_type: 证券类型 ('stock', 'index', 'etf')
        start_date: 开始日期 (YYYYMMDD格式)
        end_date: 结束日期 (YYYYMMDD格式)
        
    Returns:
        DataFrame with historical data
    """
    reader = get_tdx_reader()
    
    if security_type == 'stock':
        return reader.get_stock_data(code, start_date, end_date)
    elif security_type == 'index':
        return reader.get_index_data(code, start_date, end_date)
    elif security_type == 'etf':
        return reader.get_etf_data(code, start_date, end_date)
    else:
        return pd.DataFrame()


if __name__ == "__main__":
    # 测试代码
    reader = TDXDataReader()
    
    # 测试连接
    print("测试TDX连接...")
    if reader.test_connection():
        print("TDX连接成功")
    else:
        print("TDX连接失败")
        exit(1)
        
    # 测试读取数据
    print("\n测试读取平安银行(000001)数据...")
    df = reader.get_stock_data('000001', end_date='20231231')
    if not df.empty:
        print(f"读取成功，共{len(df)}条记录")
        print(f"数据范围: {df['trade_date'].min()} 到 {df['trade_date'].max()}")
        print("\n最新5条记录:")
        print(df.tail())
    else:
        print("读取失败")
        
    # 测试读取上证指数(000001)
    print("\n测试读取上证指数(000001)数据...")
    df = reader.get_index_data('000001', end_date='20231231')
    if not df.empty:
        print(f"读取成功，共{len(df)}条记录")
    else:
        print("读取失败")
        
    # 获取可用代码
    print("\n获取可用代码列表...")
    available_codes = reader.get_available_codes()
    print(f"上海交易所: {len(available_codes['sh'])} 只股票")
    print(f"深圳交易所: {len(available_codes['sz'])} 只股票")
    print(f"上海示例: {available_codes['sh'][:5]}")
    print(f"深圳示例: {available_codes['sz'][:5]}")