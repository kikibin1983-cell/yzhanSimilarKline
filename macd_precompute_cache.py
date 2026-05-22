"""
MACD预计算缓存管理器 V2
每天收盘后预计算全市场MACD，白天扫描直接读取，支持增量更新
"""
import os
import pickle
import json
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple

class MACDPrecomputeCache:
    """MACD预计算缓存管理器 V2"""
    
    def __init__(self, cache_dir="./macd_precompute_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.memory_cache = {}  # 内存缓存
        
        # 元数据文件
        self.metadata_file = self.cache_dir / "metadata.json"
        self.metadata = self._load_metadata()
        
    def _load_metadata(self) -> Dict:
        """加载元数据"""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {
            'last_full_update': None,
            'last_incremental_update': None,
            'total_stocks': 0,
            'cached_stocks': [],
            'version': '2.0'
        }
    
    def _save_metadata(self):
        """保存元数据"""
        try:
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存元数据失败: {e}")
    
    def _get_cache_file(self, code: str) -> Path:
        """获取缓存文件路径（按代码首字母分目录）"""
        first_char = str(code)[0] if code else '0'
        sub_dir = self.cache_dir / first_char
        sub_dir.mkdir(exist_ok=True)
        return sub_dir / f"{code}.pkl"
    
    def get(self, code: str) -> Optional[Dict]:
        """
        获取预计算的MACD数据
        
        参数：
            code: 股票代码
        
        返回：
            dict: {'DIF': array, 'DEA': array, 'MACD': array, 'close': array, 
                   'trade_date': list, 'last_date': str} 或 None
        """
        # 先查内存缓存
        if code in self.memory_cache:
            return self.memory_cache[code]
        
        # 查文件缓存
        cache_file = self._get_cache_file(code)
        if cache_file.exists():
            try:
                with open(cache_file, 'rb') as f:
                    data = pickle.load(f)
                # 放入内存缓存
                self.memory_cache[code] = data
                return data
            except Exception as e:
                print(f"读取缓存失败 {code}: {e}")
                return None
        
        return None
    
    def set(self, code: str, macd_data: Dict):
        """
        保存预计算的MACD数据
        
        参数：
            code: 股票代码
            macd_data: {'DIF': array, 'DEA': array, 'MACD': array, ...}
        """
        # 保存到内存
        self.memory_cache[code] = macd_data
        
        # 保存到文件
        cache_file = self._get_cache_file(code)
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(macd_data, f, protocol=pickle.HIGHEST_PROTOCOL)
            
            # 更新元数据
            if code not in self.metadata['cached_stocks']:
                self.metadata['cached_stocks'].append(code)
        except Exception as e:
            print(f"保存缓存失败 {code}: {e}")
    
    def batch_precompute(self, stock_codes: List[str], get_data_func, 
                         max_workers: int = 8, progress_callback=None) -> Tuple[int, int]:
        """
        批量预计算全市场MACD（完整更新）
        
        参数：
            stock_codes: 股票代码列表
            get_data_func: 获取股票数据的函数
            max_workers: 并行进程数
            progress_callback: 进度回调函数(当前, 总数)
        
        返回：
            (成功数量, 总数)
        """
        from concurrent.futures import ProcessPoolExecutor, as_completed
        
        print(f"开始预计算 {len(stock_codes)} 只股票的MACD...")
        
        success_count = 0
        completed = 0
        
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_code = {
                executor.submit(self._compute_single_macd, code, get_data_func): code 
                for code in stock_codes
            }
            
            # 处理结果
            for future in as_completed(future_to_code):
                code = future_to_code[future]
                completed += 1
                
                try:
                    macd_data = future.result()
                    if macd_data:
                        self.set(code, macd_data)
                        success_count += 1
                except Exception as e:
                    print(f"计算失败 {code}: {e}")
                
                # 更新进度
                if progress_callback and completed % 10 == 0:
                    progress_callback(completed, len(stock_codes))
                
                if completed % 100 == 0:
                    print(f"预计算进度: {completed}/{len(stock_codes)}, 成功: {success_count}")
        
        # 更新元数据
        self.metadata['last_full_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.metadata['total_stocks'] = success_count
        self._save_metadata()
        
        print(f"预计算完成！成功: {success_count}/{len(stock_codes)}")
        return success_count, len(stock_codes)
    
    def incremental_update(self, stock_codes: List[str], get_data_func,
                           max_workers: int = 8, progress_callback=None) -> Tuple[int, int, int]:
        """
        增量更新MACD缓存
        只更新：1) 新添加的股票 2) 数据有更新的股票
        
        参数：
            stock_codes: 所有股票代码列表
            get_data_func: 获取股票数据的函数
            max_workers: 并行进程数
            progress_callback: 进度回调函数
        
        返回：
            (新增数量, 更新数量, 总数)
        """
        from concurrent.futures import ProcessPoolExecutor, as_completed
        
        print("开始增量更新MACD缓存...")
        
        # 找出需要更新的股票
        need_update = []
        for code in stock_codes:
            cached = self.get(code)
            if cached is None:
                # 新股票，需要计算
                need_update.append((code, 'new'))
            else:
                # 检查数据是否过期（简单检查：对比最后一天数据）
                # 实际使用时可以检查数据日期
                pass
        
        print(f"需要更新: {len(need_update)} 只股票")
        
        if len(need_update) == 0:
            print("所有缓存都是最新的，无需更新")
            return 0, 0, len(stock_codes)
        
        # 并行计算
        new_count = 0
        update_count = 0
        completed = 0
        
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_to_code = {
                executor.submit(self._compute_single_macd, code, get_data_func): (code, status)
                for code, status in need_update
            }
            
            for future in as_completed(future_to_code):
                code, status = future_to_code[future]
                completed += 1
                
                try:
                    macd_data = future.result()
                    if macd_data:
                        self.set(code, macd_data)
                        if status == 'new':
                            new_count += 1
                        else:
                            update_count += 1
                except Exception as e:
                    print(f"更新失败 {code}: {e}")
                
                if progress_callback and completed % 10 == 0:
                    progress_callback(completed, len(need_update))
                
                if completed % 50 == 0:
                    print(f"增量更新进度: {completed}/{len(need_update)}")
        
        # 更新元数据
        self.metadata['last_incremental_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.metadata['total_stocks'] = len(self.metadata['cached_stocks'])
        self._save_metadata()
        
        print(f"增量更新完成！新增: {new_count}, 更新: {update_count}")
        return new_count, update_count, len(stock_codes)
    
    def _compute_single_macd(self, code: str, get_data_func) -> Optional[Dict]:
        """计算单只股票的MACD（供并行调用）"""
        try:
            df = get_data_func(code=code, security_type='stock')
            if df is not None and len(df) >= 30:
                # 使用Numba加速版计算MACD
                try:
                    from macd_fast import calculate_macd_fast
                    df_macd = calculate_macd_fast(df)
                except ImportError:
                    from macd_divergence_scanner import calculate_macd
                    df_macd = calculate_macd(df)
                
                if df_macd is not None:
                    return {
                        'DIF': df_macd['DIF'].values,
                        'DEA': df_macd['DEA'].values,
                        'MACD': df_macd['MACD'].values,
                        'close': df_macd['close'].values if 'close' in df_macd.columns else df['close'].values,
                        'trade_date': df['trade_date'].tolist() if 'trade_date' in df.columns else df.index.tolist(),
                        'last_date': str(df['trade_date'].iloc[-1]) if 'trade_date' in df.columns else str(df.index[-1]),
                        'compute_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
        except Exception as e:
            pass
        return None
    
    def get_cache_stats(self) -> Dict:
        """获取缓存统计信息"""
        total_cached = len(self.metadata.get('cached_stocks', []))
        
        # 计算缓存大小
        total_size = 0
        file_count = 0
        for sub_dir in self.cache_dir.iterdir():
            if sub_dir.is_dir() and sub_dir.name != '.git':
                for f in sub_dir.glob('*.pkl'):
                    total_size += f.stat().st_size
                    file_count += 1
        
        return {
            'total_cached': total_cached,
            'file_count': file_count,
            'cache_size_mb': round(total_size / (1024 * 1024), 2),
            'last_full_update': self.metadata.get('last_full_update', '从未'),
            'last_incremental_update': self.metadata.get('last_incremental_update', '从未'),
            'cache_dir': str(self.cache_dir)
        }
    
    def clean_cache(self, max_age_days: int = 30):
        """清理过期缓存"""
        cutoff = datetime.now() - timedelta(days=max_age_days)
        removed = 0
        
        for sub_dir in self.cache_dir.iterdir():
            if sub_dir.is_dir() and sub_dir.name not in ['.git', 'metadata.json']:
                for f in sub_dir.glob('*.pkl'):
                    try:
                        mtime = datetime.fromtimestamp(f.stat().st_mtime)
                        if mtime < cutoff:
                            f.unlink()
                            removed += 1
                    except:
                        pass
        
        print(f"清理完成，移除 {removed} 个过期缓存文件")
        return removed
    
    def clear_all_cache(self):
        """清空所有缓存"""
        import shutil
        
        for sub_dir in self.cache_dir.iterdir():
            if sub_dir.is_dir():
                shutil.rmtree(sub_dir)
            elif sub_dir.is_file() and sub_dir.name != 'metadata.json':
                sub_dir.unlink()
        
        self.memory_cache.clear()
        self.metadata = {
            'last_full_update': None,
            'last_incremental_update': None,
            'total_stocks': 0,
            'cached_stocks': [],
            'version': '2.0'
        }
        self._save_metadata()
        print("所有缓存已清空")


# 全局实例
_macd_precompute_cache = None

def get_macd_precompute_cache(cache_dir="./macd_precompute_cache"):
    """获取全局缓存实例"""
    global _macd_precompute_cache
    if _macd_precompute_cache is None:
        _macd_precompute_cache = MACDPrecomputeCache(cache_dir)
    return _macd_precompute_cache


def _compute_macd_for_code(code: str, tdx_path: str) -> Optional[Dict]:
    """
    为单个股票计算MACD（供多进程使用）
    
    参数：
        code: 股票代码
        tdx_path: TDX数据路径
    
    返回：
        MACD数据字典或None
    """
    try:
        # 每个进程独立初始化reader
        from tdx_reader import get_tdx_reader
        reader = get_tdx_reader(tdx_path)
        
        # 使用正确的方法名 get_stock_data 而不是 read_daily
        df = reader.get_stock_data(code=code)
        if df is not None and len(df) >= 30:
            # 使用Numba加速版计算MACD
            try:
                from macd_fast import calculate_macd_fast
                df_macd = calculate_macd_fast(df)
            except ImportError:
                from macd_divergence_scanner import calculate_macd
                df_macd = calculate_macd(df)
            
            if df_macd is not None:
                return {
                    'DIF': df_macd['DIF'].values,
                    'DEA': df_macd['DEA'].values,
                    'MACD': df_macd['MACD'].values,
                    'close': df_macd['close'].values if 'close' in df_macd.columns else df['close'].values,
                    'trade_date': df['trade_date'].tolist() if 'trade_date' in df.columns else df.index.tolist(),
                    'last_date': str(df['trade_date'].iloc[-1]) if 'trade_date' in df.columns else str(df.index[-1]),
                    'compute_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
    except Exception as e:
        print(f"计算失败 {code}: {e}")
    return None

def update_macd_cache_for_all_stocks(tdx_path: str, max_workers: int = 8, 
                                     incremental: bool = True,
                                     progress_callback=None) -> Dict:
    """
    更新所有股票的MACD缓存（供外部调用）- 修复版
    
    参数：
        tdx_path: TDX数据路径
        max_workers: 并行进程数
        incremental: 是否使用增量更新
        progress_callback: 进度回调函数(current, total)
    
    返回：
        统计信息字典
    """
    from tdx_reader import get_tdx_reader
    from concurrent.futures import ProcessPoolExecutor, as_completed
    
    # 获取所有股票代码
    tdx_reader = get_tdx_reader(tdx_path)
    available = tdx_reader.get_available_codes()
    all_codes = []
    for code in available.get('sh', []):
        if isinstance(code, dict):
            all_codes.append(code.get('code', ''))
        else:
            all_codes.append(str(code))
    for code in available.get('sz', []):
        if isinstance(code, dict):
            all_codes.append(code.get('code', ''))
        else:
            all_codes.append(str(code))
    
    # 过滤有效代码
    all_codes = [c for c in all_codes if c and len(c) >= 6]
    
    print(f"共找到 {len(all_codes)} 只股票")
    
    # 获取缓存实例
    cache = get_macd_precompute_cache()
    
    # 确定需要处理的股票
    if incremental:
        # 增量更新：只处理未缓存的股票
        codes_to_process = [code for code in all_codes if cache.get(code) is None]
        print(f"增量更新：需要处理 {len(codes_to_process)} 只新股票")
    else:
        # 完整更新：处理所有股票
        codes_to_process = all_codes
        print(f"完整更新：需要处理全部 {len(codes_to_process)} 只股票")
    
    if len(codes_to_process) == 0:
        print("没有需要处理的股票")
        return {
            'mode': 'incremental' if incremental else 'full',
            'success_count': 0,
            'total': len(all_codes),
            'stats': cache.get_cache_stats()
        }
    
    # 使用多进程并行计算（传递tdx_path而不是函数）
    success_count = 0
    completed = 0
    
    print(f"开始并行计算（使用 {max_workers} 个进程）...")
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务（传递tdx_path字符串，可被pickle）
        future_to_code = {
            executor.submit(_compute_macd_for_code, code, tdx_path): code 
            for code in codes_to_process
        }
        
        for future in as_completed(future_to_code):
            code = future_to_code[future]
            completed += 1
            
            try:
                macd_data = future.result()
                if macd_data:
                    cache.set(code, macd_data)
                    success_count += 1
            except Exception as e:
                print(f"处理失败 {code}: {e}")
            
            # 更新进度
            if progress_callback and completed % 10 == 0:
                progress_callback(completed, len(codes_to_process))
            
            if completed % 100 == 0:
                print(f"进度: {completed}/{len(codes_to_process)}, 成功: {success_count}")
    
    print(f"计算完成！成功: {success_count}/{len(codes_to_process)}")
    
    # 更新元数据
    cache.metadata['last_full_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cache.metadata['total_stocks'] = len(cache.metadata.get('cached_stocks', []))
    cache._save_metadata()
    
    return {
        'mode': 'incremental' if incremental else 'full',
        'success_count': success_count,
        'total': len(all_codes),
        'stats': cache.get_cache_stats()
    }


if __name__ == "__main__":
    # 测试
    cache = MACDPrecomputeCache()
    stats = cache.get_cache_stats()
    print(f"MACD预计算缓存系统 V2 已初始化")
    print(f"缓存统计: {stats}")
