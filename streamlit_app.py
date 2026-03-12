import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from scipy.stats import pearsonr, spearmanr
from scipy.spatial.distance import euclidean
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD
import jieba
import re
import os
import pickle
import hashlib
from dotenv import load_dotenv
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional
from functools import wraps
import json
import time
import glob

# 使用akshare获取数据
import akshare as ak

st.set_page_config(page_title="A股K线智能分析系统", layout="wide")

# 加载 .env 文件
load_dotenv()

# API 配置 - 优先从 .env 读取，如果没有则从 streamlit 环境变量读取
def get_config(key, default_value=""):
    """安全地获取配置值，优先从环境变量获取，然后尝试从 secrets 获取"""
    try:
        # 首先尝试从环境变量获取
        value = os.getenv(key)
        if value is not None:
            return value
            
        # 如果环境变量不存在，尝试从 secrets 获取
        try:
            return st.secrets.get(key, default_value)
        except FileNotFoundError:
            return default_value
    except Exception:
        return default_value

# 使用新的配置获取函数
API_KEY = get_config('API_KEY')
API_BASE = get_config('API_BASE', "https://api.openai.com")
MODEL = get_config('MODEL', "gpt-4o-mini")
PROXY_URL = get_config('PROXY_URL')

# 设置代理（在配置加载后生效）
if PROXY_URL:
    os.environ['HTTP_PROXY'] = PROXY_URL
    os.environ['HTTPS_PROXY'] = PROXY_URL

def file_cache(cache_dir="./data_cache", expire_days=1):
    """
    文件缓存装饰器，将数据存储到本地文件系统
    
    参数：
        cache_dir: 缓存目录路径
        expire_days: 缓存过期天数，默认1天
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # 创建缓存目录
            os.makedirs(cache_dir, exist_ok=True)
            
            # 构建缓存文件路径，使用函数名和参数作为缓存键
            cache_key = f"{func.__name__}_{str(args)}_{str(kwargs)}"
            cache_file = os.path.join(cache_dir, f"{cache_key}.json")
            meta_file = os.path.join(cache_dir, f"{cache_key}_meta.json")
            
            # 检查缓存是否存在且未过期
            if os.path.exists(cache_file) and os.path.exists(meta_file):
                with open(meta_file, 'r') as f:
                    meta = json.load(f)
                cache_time = datetime.strptime(meta['timestamp'], 
                                             '%Y-%m-%d %H:%M:%S')
                
                # 如果缓存未过期，直接从文件加载数据
                if datetime.now() - cache_time < timedelta(days=expire_days):
                    try:
                        with open(cache_file, 'r', encoding='utf-8') as f:
                            cached_data = json.load(f)
                        # 根据保存时的数据类型进行恢复
                        data_type = meta.get('data_type', 'unknown')
                        expected_return = getattr(func, '__annotations__', {}).get('return')
                        should_be_dataframe = data_type == 'dataframe' or expected_return is pd.DataFrame

                        if should_be_dataframe and isinstance(cached_data, list):
                            return pd.DataFrame(cached_data)
                        # 其他类型直接返回，保持原样
                        return cached_data
                    except Exception as e:
                        print(f"读取缓存文件出错: {str(e)}")
            
            # 如果缓存不存在或已过期，重新获取数据
            results = func(*args, **kwargs)
            
            # 保存数据到缓存文件
            try:
                # 处理DataFrame对象
                if hasattr(results, 'to_dict'):
                    # DataFrame转换为字典列表
                    cache_data = results.to_dict('records')
                elif isinstance(results, pd.DataFrame):
                    cache_data = results.to_dict('records')
                else:
                    cache_data = results
                
                # 保存数据
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)
                
                # 保存元数据
                meta = {
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'function': func.__name__,
                    'args': str(args),
                    'kwargs': str(kwargs),
                    'data_type': 'dataframe' if isinstance(results, pd.DataFrame) else 'other'
                }
                with open(meta_file, 'w', encoding='utf-8') as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                    
            except Exception as e:
                print(f"写入缓存文件出错: {str(e)}")
            
            return results
        return wrapper
    return decorator

@file_cache(cache_dir="./securities_cache", expire_days=30)
def load_security_data(security_type: str) -> pd.DataFrame:
    """
    加载证券数据，支持本地文件缓存 - 使用akshare
    
    参数：
        security_type: 证券类型 ('index', 'stock', 'etf')
    
    返回：
        pd.DataFrame: 包含证券信息的数据框
    """
    try:
        if security_type == 'index':
            # 获取指数列表
            df = ak.index_stock_info()
            df = df[['index_code', 'display_name']].rename(columns={'index_code': 'index_code', 'display_name': 'name'})
            return df
        elif security_type == 'stock':
            # 获取A股股票列表
            df = ak.stock_info_a_code_name()
            df = df.rename(columns={'code': 'stock_code', 'name': 'short_name'})
            return df
        elif security_type == 'etf':
            # 从TDX获取ETF列表
            try:
                # 尝试从TDX读取ETF数据
                from tdx_reader import get_tdx_reader
                reader = get_tdx_reader()
                available_codes = reader.get_available_codes()
                
                # 创建ETF列表（简化版，实际需要更精确的ETF识别）
                etf_codes = []
                for exchange in ['sh', 'sz']:
                    for code in available_codes.get(exchange, []):
                        # 简单规则：5开头的代码通常是ETF
                        if code.startswith('5') or code.startswith('15'):
                            etf_codes.append({
                                'fund_code': code,
                                'short_name': f'ETF-{code}'  # 占位名称
                            })
                
                df = pd.DataFrame(etf_codes)
                if not df.empty:
                    print(f"从TDX加载了 {len(df)} 个ETF")
                return df
            except Exception as e:
                print(f"从TDX加载ETF数据时出错: {str(e)}")
                # 返回空DataFrame作为降级方案
                return pd.DataFrame(columns=['fund_code', 'short_name'])
        else:
            return pd.DataFrame()
    except Exception as e:
        print(f"加载{security_type}数据时出错: {str(e)}")
        return pd.DataFrame()

def preprocess_query(query: str) -> str:
    """预处理搜索关键词"""
    query = re.sub(r'[^\w\s]', '', query)
    query = query.lower()
    query = ' '.join(query.split())
    return query

@file_cache(cache_dir="./search_cache", expire_days=30)
def search_single_type(query: str, security_type: str) -> List[Dict]:
    """
    在单个证券类型中搜索
    
    Args:
        query: 搜索关键词
        security_type: 证券类型
    
    Returns:
        List[Dict]: 搜索结果列表
    """
    results = []
    
    # 加载数据
    try:
        df = load_security_data(security_type)
    except Exception as e:
        print(f"加载{security_type}数据时出错: {str(e)}")
        return results
        
    # 兼容历史缓存中可能出现的 list 格式
    if isinstance(df, list):
        df = pd.DataFrame(df)

    if not isinstance(df, pd.DataFrame) or df.empty:
        return results
        
    try:
        # 根据证券类型确定代码和名称列
        code_col = {
            'index': 'index_code',
            'stock': 'stock_code',
            'etf': 'fund_code'
        }.get(security_type)
        
        name_col = 'name' if security_type == 'index' else 'short_name'
        
        # 确保列存在
        if code_col not in df.columns or name_col not in df.columns:
            print(f"警告: {security_type}数据缺少必要列")
            return results
        
        # 创建搜索条件 - 添加错误处理
        try:
            code_match = df[code_col].astype(str).str.contains(query, case=False, na=False)
        except Exception as e:
            print(f"代码列搜索出错: {str(e)}")
            code_match = pd.Series([False] * len(df))
        
        try:
            name_match = df[name_col].astype(str).str.contains(query, case=False, na=False)
        except Exception as e:
            print(f"名称列搜索出错: {str(e)}")
            name_match = pd.Series([False] * len(df))
        
        # 应用搜索条件
        matched_df = df[code_match | name_match]
        
        # 提取结果
        for _, row in matched_df.iterrows():
            # 处理可能的NaN值
            code_value = row[code_col]
            name_value = row[name_col]
            
            # 如果code或name是NaN，转换为空字符串
            if pd.isna(code_value):
                code_value = ''
            if pd.isna(name_value):
                name_value = ''
            
            results.append({
                'code': str(code_value) if code_value is not None else '',
                'name': str(name_value) if name_value is not None else '',
                'type': security_type,
                'exchange': str(row.get('exchange', '')) if not pd.isna(row.get('exchange', '')) else ''
            })
            
    except Exception as e:
        print(f"搜索{security_type}时出错: {str(e)}")
    
    return results

@lru_cache(maxsize=2056)
def search_securities(query: str) -> List[Dict]:
    """
    搜索证券(指数、股票)
    
    技术特点:
    1. 使用 LRU 缓存优化数据加载
    2. 多线程并行搜索提升性能
    3. 关键词预处理提高匹配准确性
    4. 异常处理确保功能稳定性
    5. 类型注解增强代码可读性
    
    Args:
        query: 搜索关键词(代码或名称)
    
    Returns:
        List[Dict]: 搜索结果列表，每个结果包含:
            - code: 证券代码
            - name: 证券名称
            - type: 证券类型
            - exchange: 交易所
    """
    if not query or len(query.strip()) == 0:
        return []
        
    # 预处理查询关键词
    query = preprocess_query(query)
    
    # 使用线程池并行搜索不同类型的证券
    security_types = ['index', 'stock', 'etf']
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(search_single_type, query, security_type)
            for security_type in security_types
        ]
        
        # 收集所有结果
        all_results = []
        for future in futures:
            try:
                results = future.result()
                # 确保results是列表
                if isinstance(results, list):
                    all_results.extend(results)
                else:
                    print(f"警告: search_single_type返回了非列表类型: {type(results)}")
            except Exception as e:
                print(f"获取搜索结果时出错: {str(e)}")
                
    # 过滤掉非字典类型的结果
    filtered_results = []
    for item in all_results:
        if isinstance(item, dict):
            filtered_results.append(item)
        else:
            print(f"警告: 过滤掉非字典类型的结果: {type(item)}")
    
    all_results = filtered_results
    
    # 按相关度排序结果
    def get_sort_key(x):
        # 确保x是字典
        if not isinstance(x, dict):
            return (0, 0, 0, 0)
        
        code = x.get('code', '')
        name = x.get('name', '')
        
        # 确保code和name是字符串
        code_str = str(code) if code is not None else ''
        name_str = str(name) if name is not None else ''
        
        # 完全匹配代码的优先级最高
        exact_code_match = -int(code_str.lower() == query)
        
        # 其次是包含代码的
        code_contains = -int(query in code_str.lower())
        
        # 再次是包含名称的
        name_contains = -int(query in name_str.lower())
        
        # 最后按代码长度排序
        code_length = len(code_str)
        
        return (exact_code_match, code_contains, name_contains, code_length)
    
    all_results.sort(key=get_sort_key)
    
    return all_results

def get_market_data(code, security_type, days=365*3):
    """获取市场数据 - 使用本地通达信数据"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # 导入TDX读取器
    try:
        from tdx_reader import read_tdx_data
    except ImportError:
        print("错误: 无法导入tdx_reader模块")
        return None
    
    # 转换日期格式为YYYYMMDD
    start_date_str = start_date.strftime('%Y%m%d')
    end_date_str = end_date.strftime('%Y%m%d')
    
    try:
        # 使用TDX读取器获取数据 - 获取更多数据以便过滤
        df = read_tdx_data(
            code=code,
            security_type=security_type,
            start_date=None,  # 获取所有数据
            end_date=end_date_str
        )
        
        if df is not None and not df.empty:
            # 确保列名一致
            required_columns = ['trade_date', 'open', 'high', 'low', 'close', 'volume']
            available_columns = df.columns.tolist()
            
            # 检查是否有缺失的列
            missing_columns = [col for col in required_columns if col not in available_columns]
            if missing_columns:
                print(f"警告: 数据缺少列: {missing_columns}")
            
            # 确保数据类型正确
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            
            # 过滤掉未来日期（超过当前日期的数据）
            current_date = datetime.now()
            df = df[df['trade_date'] <= current_date]
            
            # 按日期排序
            df = df.sort_values('trade_date', ascending=True)
            
            # 如果指定了开始日期，进行过滤
            if start_date_str:
                df = df[df['trade_date'] >= pd.to_datetime(start_date_str, format='%Y%m%d')]
            
            # 重置索引
            df = df.reset_index(drop=True)
            
            if not df.empty:
                print(f"成功从TDX获取 {code} 数据，共 {len(df)} 条有效记录")
                print(f"数据范围: {df['trade_date'].min()} 到 {df['trade_date'].max()}")
                return df
            else:
                print(f"TDX中 {code} 的数据过滤后为空")
                return None
        else:
            print(f"TDX中没有找到 {code} 的数据")
            return None
            
    except Exception as e:
        print(f"从TDX获取数据时出错: {str(e)}")
        return None

def normalize_window(window):
    """标准化价格序列"""
    numeric_window = pd.to_numeric(window, errors='coerce')
    if numeric_window.isna().any():
        return None
    return (numeric_window - numeric_window.iloc[0]) / numeric_window.iloc[0] * 100

def normalize_window_zscore(window):
    """Z-score标准化价格序列"""
    numeric_window = pd.to_numeric(window, errors='coerce')
    if numeric_window.isna().any() or len(numeric_window) < 2:
        return None
    mean_val = numeric_window.mean()
    std_val = numeric_window.std()
    if std_val == 0:
        return np.zeros_like(numeric_window)
    return (numeric_window - mean_val) / std_val


def _to_numeric_series(values, fill_nan_method="drop"):
    """将输入值转为数值序列并清洗异常值"""
    if values is None:
        return None

    series = pd.to_numeric(pd.Series(values), errors='coerce')
    if series.empty:
        return None

    # 将无穷大转为NaN后处理
    series = series.replace([np.inf, -np.inf], np.nan)

    if fill_nan_method == "drop":
        series = series.dropna().reset_index(drop=True)
    else:
        series = series.fillna(method='ffill').fillna(method='bfill').reset_index(drop=True)

    if series.empty:
        return None

    return series.astype(float)


def _safe_align_series(series1, series2):
    """按最小长度对齐两组数值序列并剔除空值"""
    s1 = _to_numeric_series(series1)
    s2 = _to_numeric_series(series2)
    if s1 is None or s2 is None:
        return None, None

    min_len = min(len(s1), len(s2))
    if min_len < 5:
        return None, None

    s1 = s1.tail(min_len).reset_index(drop=True)
    s2 = s2.tail(min_len).reset_index(drop=True)

    aligned = pd.DataFrame({'s1': s1, 's2': s2}).dropna()
    if aligned.empty:
        return None, None
    return aligned['s1'].values.astype(float), aligned['s2'].values.astype(float)


def _safe_pearson(x, y):
    """安全计算皮尔逊相关系数，失败时返回None"""
    try:
        if len(x) != len(y) or len(x) < 3:
            return None
        if np.std(x) == 0 or np.std(y) == 0:
            return None
        corr, _ = pearsonr(x, y)
        if pd.isna(corr):
            return None
        return float(corr)
    except Exception:
        return None


def _sequence_similarity(a, b):
    """序列综合相似度：相关性(主项) + 形状距离(辅项)，输出0~1"""
    x, y = _safe_align_series(a, b)
    if x is None or y is None:
        return 0.0

    if len(x) < 2:
        return 0.0

    # 去极值后做稳健处理
    def _robust_z(score_series):
        s = _to_numeric_series(score_series, fill_nan_method="fill")
        if s is None:
            return None
        s = s.tail(len(s))
        clipped = s.clip(lower=s.quantile(0.02), upper=s.quantile(0.98))
        return normalize_window_zscore(clipped)

    nz = _robust_z(pd.Series(x))
    mz = _robust_z(pd.Series(y))
    if nz is None or mz is None:
        return 0.0

    min_len = min(len(nz), len(mz))
    nz = nz[:min_len]
    mz = mz[:min_len]

    corr = _safe_pearson(nz, mz)
    if corr is None:
        corr_score = 0.5
    else:
        corr_score = (corr + 1) / 2

    dist = euclidean(nz, mz)
    dist_score = 1 / (1 + dist / max(1, len(nz)))

    return 0.65 * corr_score + 0.35 * dist_score


def calculate_macd_similarity(df1, df2, return_components=False):
    """MACD 方向与形态相似度"""
    result = {
        'score': 0.0,
        'dif': 0.0,
        'dea': 0.0,
        'hist': 0.0
    }

    macd1 = calculate_macd(df1)
    macd2 = calculate_macd(df2)
    if macd1 is None or macd2 is None:
        if return_components:
            return result
        return 0.0

    align_len = min(len(macd1), len(macd2))
    if align_len < 5:
        if return_components:
            return result
        return 0.0

    m1 = macd1.tail(align_len).reset_index(drop=True)
    m2 = macd2.tail(align_len).reset_index(drop=True)

    dif_sim = _sequence_similarity(m1['DIF'], m2['DIF'])
    dea_sim = _sequence_similarity(m1['DEA'], m2['DEA'])
    hist_sim = _sequence_similarity(m1['MACD'], m2['MACD'])

    result.update({
        'dif': dif_sim,
        'dea': dea_sim,
        'hist': hist_sim
    })
    result['score'] = 0.4 * dif_sim + 0.3 * dea_sim + 0.3 * hist_sim

    if return_components:
        return result
    return result['score']


def calculate_kdj_similarity(df1, df2, return_components=False):
    """KDJ 相似度（K/D/J）"""
    result = {
        'score': 0.0,
        'k': 0.0,
        'd': 0.0,
        'j': 0.0
    }

    kdj1 = calculate_kdj(df1)
    kdj2 = calculate_kdj(df2)
    if kdj1 is None or kdj2 is None:
        if return_components:
            return result
        return 0.0

    align_len = min(len(kdj1), len(kdj2))
    if align_len < 5:
        if return_components:
            return result
        return 0.0

    k1 = kdj1.tail(align_len).reset_index(drop=True)
    k2 = kdj2.tail(align_len).reset_index(drop=True)

    k_sim = _sequence_similarity(k1['K'], k2['K'])
    d_sim = _sequence_similarity(k1['D'], k2['D'])
    j_sim = _sequence_similarity(k1['J'], k2['J'])

    result.update({
        'k': k_sim,
        'd': d_sim,
        'j': j_sim
    })
    result['score'] = 0.4 * k_sim + 0.4 * d_sim + 0.2 * j_sim

    if return_components:
        return result
    return result['score']

def calculate_price_similarity(window1, window2):
    """计算价格序列相似度（多维度）"""
    if len(window1) != len(window2):
        return 0
    
    # 提取多维度特征
    features1 = {
        'close': window1['close'].values if 'close' in window1 else window1.values,
        'open': window1['open'].values if 'open' in window1 else None,
        'high': window1['high'].values if 'high' in window1 else None,
        'low': window1['low'].values if 'low' in window1 else None
    }
    
    features2 = {
        'close': window2['close'].values if 'close' in window2 else window2.values,
        'open': window2['open'].values if 'open' in window2 else None,
        'high': window2['high'].values if 'high' in window2 else None,
        'low': window2['low'].values if 'low' in window2 else None
    }
    
    similarities = []
    weights = []
    
    # 计算收盘价相似度（主要权重）
    close1 = features1['close']
    close2 = features2['close']
    if close1 is not None and close2 is not None:
        norm1 = _to_numeric_series(close1, fill_nan_method="drop")
        norm2 = _to_numeric_series(close2, fill_nan_method="drop")
        if norm1 is not None and norm2 is not None:
            min_len = min(len(norm1), len(norm2))
            if min_len >= 2:
                norm1 = norm1.tail(min_len)
                norm2 = norm2.tail(min_len)
                norm1 = normalize_window_zscore(norm1)
                norm2 = normalize_window_zscore(norm2)
                if norm1 is not None and norm2 is not None:
                    try:
                        corr, _ = pearsonr(norm1, norm2)
                        corr = 0 if pd.isna(corr) else corr
                        dist = euclidean(norm1, norm2)
                        normalized_dist = 1 / (1 + dist / max(1, len(norm1)))
                        similarity = (corr + 1)/2 * 0.7 + normalized_dist * 0.3
                        similarities.append(similarity)
                        weights.append(0.5)  # 收盘价权重50%
                    except Exception:
                        pass
    
    # 计算其他价格特征相似度
    other_features = ['open', 'high', 'low']
    for feature in other_features:
        if features1[feature] is not None and features2[feature] is not None:
            norm1 = _to_numeric_series(features1[feature], fill_nan_method="drop")
            norm2 = _to_numeric_series(features2[feature], fill_nan_method="drop")
            if norm1 is not None and norm2 is not None:
                min_len = min(len(norm1), len(norm2))
                if min_len >= 2:
                    norm1 = norm1.tail(min_len)
                    norm2 = norm2.tail(min_len)
                    try:
                        corr, _ = pearsonr(norm1, norm2)
                        if pd.isna(corr):
                            continue
                        similarities.append((corr + 1) / 2)
                        weights.append(0.1)  # 每个其他特征权重10%
                    except Exception:
                        pass
    
    # 计算加权平均相似度
    if similarities and weights:
        total_weight = sum(weights)
        if total_weight > 0:
            weighted_similarity = sum(s * w for s, w in zip(similarities, weights)) / total_weight
            return weighted_similarity
    
    return 0

def calculate_volume_similarity(volume1, volume2):
    """基于成交量水平和变化率的鲁棒相似度（0~1）"""
    vol1 = _to_numeric_series(volume1, fill_nan_method="drop")
    vol2 = _to_numeric_series(volume2, fill_nan_method="drop")
    if vol1 is None or vol2 is None:
        return 0.0

    align_len = min(len(vol1), len(vol2))
    if align_len < 6:
        return 0.0

    vol1 = vol1.tail(align_len).astype(float)
    vol2 = vol2.tail(align_len).astype(float)

    # 计算变化率（使用log回报，防止零值和极值）
    ret1 = np.diff(np.log1p(np.maximum(vol1, 0).astype(float)))
    ret2 = np.diff(np.log1p(np.maximum(vol2, 0).astype(float)))

    ret1 = pd.Series(ret1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    ret2 = pd.Series(ret2).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # 剪掉极端值，避免成交量大单导致失真
    ret1 = ret1.clip(lower=ret1.quantile(0.01), upper=ret1.quantile(0.99))
    ret2 = ret2.clip(lower=ret2.quantile(0.01), upper=ret2.quantile(0.99))

    ret1_norm = normalize_window_zscore(ret1)
    ret2_norm = normalize_window_zscore(ret2)
    if ret1_norm is None or ret2_norm is None:
        return 0.0

    min_len = min(len(ret1_norm), len(ret2_norm))
    ret1_norm = ret1_norm[:min_len]
    ret2_norm = ret2_norm[:min_len]

    ret_corr = _safe_pearson(ret1_norm, ret2_norm)
    ret_corr_score = 0.5 if ret_corr is None else (ret_corr + 1) / 2

    vol_level_corr = _safe_pearson(
        normalize_window_zscore(np.log1p(np.maximum(vol1, 0)) ),
        normalize_window_zscore(np.log1p(np.maximum(vol2, 0)) )
    )
    vol_level_score = 0.5 if vol_level_corr is None else (vol_level_corr + 1) / 2

    dist = euclidean(ret1_norm, ret2_norm)
    dist_score = 1 / (1 + dist / max(1, len(ret1_norm)))

    # 成交量相似度：变化率更重要，水平次之
    return 0.55 * ret_corr_score + 0.25 * dist_score + 0.20 * vol_level_score

def calculate_technical_similarity(df1, df2, return_components=False, technical_weights=None):
    """计算技术指标相似度"""
    components = {
        'macd': {'score': 0.0, 'dif': 0.0, 'dea': 0.0, 'hist': 0.0},
        'kdj': {'score': 0.0, 'k': 0.0, 'd': 0.0, 'j': 0.0},
    }

    macd_component = calculate_macd_similarity(df1, df2, return_components=True)
    kdj_component = calculate_kdj_similarity(df1, df2, return_components=True)
    components['macd'] = {
        'score': macd_component['score'],
        'dif': macd_component['dif'],
        'dea': macd_component['dea'],
        'hist': macd_component['hist'],
    }
    components['kdj'] = {
        'score': kdj_component['score'],
        'k': kdj_component['k'],
        'd': kdj_component['d'],
        'j': kdj_component['j'],
    }

    macd_w = 0.55
    kdj_w = 0.45
    if isinstance(technical_weights, dict):
        macd_w = max(float(technical_weights.get('macd', macd_w)), 0.0)
        kdj_w = max(float(technical_weights.get('kdj', kdj_w)), 0.0)
        total_w = macd_w + kdj_w
        if total_w > 0:
            macd_w, kdj_w = macd_w / total_w, kdj_w / total_w

    similarities = [
        (components['macd']['score'], macd_w),
        (components['kdj']['score'], kdj_w)
    ]

    total_weight = sum(weight for _, weight in similarities if isinstance(_, (int, float)))
    technical_similarity = 0.0
    if total_weight > 0:
        technical_similarity = sum(value * weight for value, weight in similarities) / total_weight

    if return_components:
        return {
            'score': technical_similarity,
            'macd': components['macd'],
            'kdj': components['kdj'],
            'weights': {
                'macd': macd_w,
                'kdj': kdj_w
            }
        }

    return technical_similarity

def calculate_pattern_similarity(df1, df2):
    """计算K线形态相似度"""
    if df1 is None or df2 is None or len(df1) != len(df2):
        return 0
    
    features = []
    
    # 计算实体大小（收盘价-开盘价的绝对值）
    body_size1 = abs(df1['close'] - df1['open'])
    body_size2 = abs(df2['close'] - df2['open'])
    
    # 计算上影线比例（最高价-最大(开盘价,收盘价)）
    upper_shadow1 = df1['high'] - np.maximum(df1['open'], df1['close'])
    upper_shadow2 = df2['high'] - np.maximum(df2['open'], df2['close'])
    
    # 计算下影线比例（最小(开盘价,收盘价)-最低价）
    lower_shadow1 = np.minimum(df1['open'], df1['close']) - df1['low']
    lower_shadow2 = np.minimum(df2['open'], df2['close']) - df2['low']
    
    # 计算总长度（最高价-最低价）
    total_length1 = df1['high'] - df1['low']
    total_length2 = df2['high'] - df2['low']
    
    # 避免除零
    total_length1 = total_length1.replace(0, 1)
    total_length2 = total_length2.replace(0, 1)
    
    # 计算比例特征
    body_ratio1 = body_size1 / total_length1
    body_ratio2 = body_size2 / total_length2
    
    upper_ratio1 = upper_shadow1 / total_length1
    upper_ratio2 = upper_shadow2 / total_length2
    
    lower_ratio1 = lower_shadow1 / total_length1
    lower_ratio2 = lower_shadow2 / total_length2
    
    # 计算特征相似度
    try:
        body_corr, _ = pearsonr(body_ratio1, body_ratio2)
        upper_corr, _ = pearsonr(upper_ratio1, upper_ratio2)
        lower_corr, _ = pearsonr(lower_ratio1, lower_ratio2)
        
        # 加权平均
        similarity = (body_corr * 0.5 + upper_corr * 0.25 + lower_corr * 0.25 + 1) / 2
        return similarity
    except Exception:
        return 0


def _prepare_similarity_frames(left_df, right_df, required_columns=None, min_length=6):
    """对齐两个DataFrame并返回同长度、数值化后的数据。"""
    if left_df is None or right_df is None:
        return None, None

    left = pd.DataFrame(left_df).copy()
    right = pd.DataFrame(right_df).copy()

    if 'close' not in left.columns or 'close' not in right.columns:
        return None, None

    if required_columns:
        for col in required_columns:
            if col not in left.columns or col not in right.columns:
                return None, None

    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount']
    for col in numeric_cols:
        if col in left.columns:
            left[col] = pd.to_numeric(left[col], errors='coerce')
        if col in right.columns:
            right[col] = pd.to_numeric(right[col], errors='coerce')

    # 丢弃完全不合法行，避免异常传播到相似度计算
    for col in ['close']:
        left = left[left[col].notna()]
        right = right[right[col].notna()]

    min_len = min(len(left), len(right))
    if min_len < min_length:
        return None, None

    return left.tail(min_len).reset_index(drop=True), right.tail(min_len).reset_index(drop=True)


def calculate_comprehensive_similarity(df1, df2, market_volatility=1.0, return_components=False, weight_profile=None):
    """
    计算综合相似度
    
    参数：
    - df1, df2: 包含价格和成交量数据的DataFrame
    - market_volatility: 市场波动率因子（>1表示高波动，<1表示低波动）
    
    返回：
    - 综合相似度（0-1之间）或包含详细组件的字典（return_components=True）
    """
    # 基础权重：默认形态优先（趋势/量能/形态/MACD/KDJ/状态）
    weights = {
        'trend': 0.0,
        'volume': 0.0,
        'pattern': 1.0,
        'macd': 0.0,
        'kdj': 0.0,
        'regime': 0.0,
    }

    def _normalize_weights(source_weights):
        """按总和归一化，避免极端配置导致权重越界。"""
        positive_weights = {key: max(float(value), 0.0) for key, value in source_weights.items()}
        total = sum(positive_weights.values())
        if total <= 0:
            return {key: 1.0 / len(positive_weights) for key in positive_weights}
        return {key: value / total for key, value in positive_weights.items()}

    def _as_scalar_score(similarity_result):
        """将结果统一转换为Python float，避免return_components=False返回非标量。"""
        try:
            score = similarity_result.get('score', 0.0)
            score_float = float(score)
            if np.isnan(score_float) or np.isinf(score_float):
                return 0.0
            return score_float
        except Exception:
            return 0.0

    # 对齐样本窗口，避免不同长度导致的误差放大
    aligned_df1, aligned_df2 = _prepare_similarity_frames(df1, df2, min_length=6)
    if aligned_df1 is None or aligned_df2 is None:
        result = {
            'score': 0.0,
            'components': {
                'price': 0.0,
                'volume': 0.0,
                'technical': {'score': 0.0, 'macd': {'score': 0.0}, 'kdj': {'score': 0.0}},
                'pattern': 0.0,
                'regime': 0.0,
                'return_rank_penalty': 1.0,
            },
            'weights': {'trend': 0.0, 'volume': 0.0, 'pattern': 1.0, 'macd': 0.0, 'kdj': 0.0, 'regime': 0.0},
            'meta': {'aligned_length': 0, 'market_volatility': market_volatility}
        }
        if return_components:
            return result
        return _as_scalar_score(result)
    
    if isinstance(weight_profile, dict):
        for key in list(weights.keys()):
            if key in weight_profile:
                weights[key] = max(float(weight_profile[key]), 0.0)

    # 根据市场波动率动态微调（在用户权重基础上小幅调整）
    if market_volatility > 1.2:  # 高波动市场
        weights['macd'] += 0.03
        weights['kdj'] += 0.02
        weights['trend'] = max(0.0, weights['trend'] - 0.05)
    elif market_volatility < 0.8:  # 低波动市场
        weights['pattern'] += 0.03
        weights['trend'] += 0.02
        weights['macd'] = max(0.0, weights['macd'] - 0.03)
        weights['kdj'] = max(0.0, weights['kdj'] - 0.02)

    # 归一化权重
    weights = _normalize_weights(weights)

    # 计算各项相似度
    trend_sim = calculate_price_similarity(aligned_df1, aligned_df2)
    
    volume_sim = 0
    if 'volume' in aligned_df1.columns and 'volume' in aligned_df2.columns:
        volume_sim = calculate_volume_similarity(
            aligned_df1['volume'].values,
            aligned_df2['volume'].values
        )
    
    technical_sim_result = calculate_technical_similarity(
        aligned_df1,
        aligned_df2,
        return_components=True,
        technical_weights={'macd': weights['macd'], 'kdj': weights['kdj']}
    )
    macd_sim = technical_sim_result['macd']['score']
    kdj_sim = technical_sim_result['kdj']['score']
    pattern_sim = calculate_pattern_similarity(aligned_df1, aligned_df2)
    regime_sim = calculate_regime_similarity(aligned_df1, aligned_df2)
    
    # 计算加权综合相似度
    total_similarity = (
        trend_sim * weights['trend'] +
        volume_sim * weights['volume'] +
        pattern_sim * weights['pattern'] +
        macd_sim * weights['macd'] +
        kdj_sim * weights['kdj'] +
        regime_sim * weights['regime']
    )

    # 稳健性惩罚：若收益率秩相关较差，降低最终得分
    robustness_penalty = calculate_return_rank_penalty(df1, df2)
    total_similarity *= robustness_penalty

    result = {
        'score': max(0.0, min(1.0, total_similarity)),
        'components': {
            'trend': trend_sim,
            'volume': volume_sim,
            'technical': technical_sim_result,
            'pattern': pattern_sim,
            'regime': regime_sim,
            'return_rank_penalty': robustness_penalty,
        },
        'weights': weights,
        'meta': {
            'aligned_length': len(aligned_df1),
            'market_volatility': market_volatility,
            'source_lengths': {
                'left': len(df1) if df1 is not None else 0,
                'right': len(df2) if df2 is not None else 0
            }
        }
    }

    if return_components:
        return result

    return _as_scalar_score(result)

def calculate_regime_similarity(df1, df2):
    """计算市场状态相似度（趋势、波动率、回撤）"""
    try:
        if df1 is None or df2 is None or len(df1) < 5 or len(df2) < 5:
            return 0

        close1 = pd.Series(df1['close']).astype(float)
        close2 = pd.Series(df2['close']).astype(float)

        ret1 = close1.pct_change().dropna()
        ret2 = close2.pct_change().dropna()
        if ret1.empty or ret2.empty:
            return 0

        # 趋势强度（线性斜率）
        x1 = np.arange(len(close1))
        x2 = np.arange(len(close2))
        slope1 = np.polyfit(x1, close1.values, 1)[0]
        slope2 = np.polyfit(x2, close2.values, 1)[0]
        slope_sim = 1 / (1 + abs(slope1 - slope2))

        # 波动率状态
        vol1 = ret1.std()
        vol2 = ret2.std()
        vol_sim = 1 / (1 + abs(vol1 - vol2) * 10)

        # 最大回撤状态
        drawdown1 = ((close1 / close1.cummax()) - 1).min()
        drawdown2 = ((close2 / close2.cummax()) - 1).min()
        dd_sim = 1 / (1 + abs(drawdown1 - drawdown2) * 10)

        return 0.4 * slope_sim + 0.3 * vol_sim + 0.3 * dd_sim
    except Exception:
        return 0

def calculate_return_rank_penalty(df1, df2):
    """收益率秩相关惩罚因子，范围[0.7, 1.0]"""
    try:
        ret1 = pd.Series(df1['close']).pct_change().dropna()
        ret2 = pd.Series(df2['close']).pct_change().dropna()
        min_len = min(len(ret1), len(ret2))
        if min_len < 5:
            return 1.0

        r1 = ret1.tail(min_len)
        r2 = ret2.tail(min_len)
        corr, _ = spearmanr(r1, r2)
        corr = 0 if pd.isna(corr) else corr
        norm_corr = (corr + 1) / 2
        return 0.7 + 0.3 * norm_corr
    except Exception:
        return 1.0

def kline_conversion_cache(cache_dir="./kline_cache", expire_days=7):
    """
    专门用于K线数据转换的缓存装饰器
    
    参数：
        cache_dir: 缓存目录路径
        expire_days: 缓存过期天数，默认7天
    """
    def decorator(func):
        def wrapper(df, *args, **kwargs):
            # 如果输入数据为空，直接返回
            if df is None or df.empty:
                return df
            
            # 创建缓存目录
            os.makedirs(cache_dir, exist_ok=True)
            
            # 生成缓存键：函数名 + 数据哈希 + 数据时间范围
            # 使用数据的最后日期和行数作为版本标识
            df_copy = df.copy()
            df_copy['trade_date'] = pd.to_datetime(df_copy['trade_date'])
            df_sorted = df_copy.sort_values('trade_date', ascending=True)
            
            if df_sorted.empty:
                return func(df, *args, **kwargs)
            
            # 使用稳定哈希生成缓存键（避免 Python 内置 hash 跨进程不稳定）
            stable_key = (
                f"{func.__name__}|"
                f"{str(df_sorted['trade_date'].iloc[0])[:10]}|"
                f"{str(df_sorted['trade_date'].iloc[-1])[:10]}|"
                f"{len(df_sorted)}"
            )
            data_hash = hashlib.md5(stable_key.encode('utf-8')).hexdigest()
            cache_key = f"{func.__name__}_{data_hash}"
            cache_file = os.path.join(cache_dir, f"{cache_key}.pkl")
            meta_file = os.path.join(cache_dir, f"{cache_key}_meta.json")
            
            # 检查缓存是否存在且未过期
            if os.path.exists(cache_file) and os.path.exists(meta_file):
                try:
                    with open(meta_file, 'r') as f:
                        meta = json.load(f)
                    cache_time = datetime.strptime(meta['timestamp'], 
                                                 '%Y-%m-%d %H:%M:%S')
                    
                    # 如果缓存未过期，直接从文件加载数据
                    if datetime.now() - cache_time < timedelta(days=expire_days):
                        try:
                            # 使用pickle加载DataFrame（保持数据类型）
                            with open(cache_file, 'rb') as f:
                                cached_df = pickle.load(f)
                            print(f"从缓存加载 {func.__name__} 数据")
                            return cached_df
                        except Exception as e:
                            print(f"读取缓存文件出错: {str(e)}")
                except Exception as e:
                    print(f"读取缓存元数据出错: {str(e)}")
            
            # 如果缓存不存在或已过期，重新计算
            print(f"计算 {func.__name__} 数据...")
            result_df = func(df, *args, **kwargs)
            
            # 保存数据到缓存文件
            try:
                # 使用pickle保存DataFrame（保持数据类型）
                with open(cache_file, 'wb') as f:
                    pickle.dump(result_df, f)
                
                # 保存元数据
                meta = {
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'function': func.__name__,
                    'data_hash': str(data_hash),
                    'data_rows': len(df),
                    'data_start': str(df_sorted['trade_date'].iloc[0])[:10],
                    'data_end': str(df_sorted['trade_date'].iloc[-1])[:10]
                }
                with open(meta_file, 'w', encoding='utf-8') as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                    
                print(f"已缓存 {func.__name__} 数据")
                    
            except Exception as e:
                print(f"写入缓存文件出错: {str(e)}")
            
            return result_df
        return wrapper
    return decorator

@kline_conversion_cache(cache_dir="./weekly_cache", expire_days=7)
def convert_to_weekly(df):
    """
    将日线数据转换为周线数据，确保周K线连续显示（无节假日空缺）
    
    参数：
    - df: 日线数据DataFrame，包含trade_date, open, high, low, close, volume等列
    
    返回：
    - 周线数据DataFrame，包含连续的周K线数据
    """
    if df is None or df.empty:
        return df
    
    df = df.copy()
    
    # 确保日期是datetime类型并设置为索引
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date', ascending=True)
    df.set_index('trade_date', inplace=True)
    
    # 使用 resample 进行周线转换（向量化优化）
    # 'W-MON' 表示以周一结束的周（确保周K线连续显示）
    weekly_df = df.resample('W-MON').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    })
    
    # 重置索引
    weekly_df = weekly_df.reset_index()
    
    # 添加周标识（年份+周数）
    weekly_df['year_week'] = weekly_df['trade_date'].dt.strftime('%Y-%U')
    
    # 确保周K线连续显示：使用fill_missing_periods填充缺失的周
    weekly_df = fill_missing_periods(weekly_df, period_type='weekly')
    
    return weekly_df

@kline_conversion_cache(cache_dir="./monthly_cache", expire_days=7)
def convert_to_monthly(df):
    """
    将日线数据转换为月线数据，确保月K线连续显示（无节假日空缺）
    
    参数：
    - df: 日线数据DataFrame，包含trade_date, open, high, low, close, volume等列
    
    返回：
    - 月线数据DataFrame，包含连续的月K线数据
    """
    if df is None or df.empty:
        return df
    
    df = df.copy()
    
    # 确保日期是datetime类型并设置为索引
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date', ascending=True)
    df.set_index('trade_date', inplace=True)
    
    # 使用 resample 进行月线转换（向量化优化）
    # 'MS' 表示月初（与fill_missing_periods保持一致，确保月K线连续显示）
    monthly_df = df.resample('MS').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    })
    
    # 重置索引
    monthly_df = monthly_df.reset_index()
    
    # 添加月标识（年份+月份）
    monthly_df['year_month'] = monthly_df['trade_date'].dt.strftime('%Y-%m')
    
    # 确保月K线连续显示：使用fill_missing_periods填充缺失的月
    monthly_df = fill_missing_periods(monthly_df, period_type='monthly')
    
    return monthly_df

def calculate_similarity(window1, window2):
    """计算两个窗口之间的相似度"""
    if len(window1) != len(window2):
        return 0
    
    norm1 = normalize_window(window1)
    norm2 = normalize_window(window2)
    
    if norm1 is None or norm2 is None:
        return 0
    
    try:
        corr, _ = pearsonr(norm1, norm2)
        dist = euclidean(norm1, norm2)
        normalized_dist = 1 / (1 + dist/len(window1))
        similarity = (corr + 1)/2 * 0.7 + normalized_dist * 0.3
        return similarity
    except Exception:
        return 0


def _sanitize_cache_code(code: Optional[str]) -> str:
    """标准化代码用于缓存文件名。"""
    if code is None:
        return 'unknown'
    value = str(code).strip().lower().replace(' ', '')
    if not value:
        return 'unknown'
    value = value.replace('sh', '').replace('sz', '')
    safe = ''.join(ch for ch in value if ch.isalnum() or ch in ('-', '_'))
    return safe or 'unknown'


def _period_cache_snapshot_paths(period_type: str, source_code: Optional[str]) -> tuple[str, str]:
    """返回周/月快照缓存文件路径。"""
    cache_dir = './weekly_cache' if period_type == 'weekly' else './monthly_cache'
    os.makedirs(cache_dir, exist_ok=True)
    code_key = _sanitize_cache_code(source_code)
    snapshot_file = os.path.join(cache_dir, f"{period_type}_{code_key}_latest.pkl")
    meta_file = os.path.join(cache_dir, f"{period_type}_{code_key}_latest_meta.json")
    return snapshot_file, meta_file


def _load_period_cache_snapshot(period_type: str, source_code: Optional[str]) -> Optional[pd.DataFrame]:
    """优先读取按代码的快照缓存；若不存在则回退读取目录中最近的历史缓存。"""
    snapshot_file, _ = _period_cache_snapshot_paths(period_type, source_code)
    if os.path.exists(snapshot_file):
        try:
            with open(snapshot_file, 'rb') as f:
                cached_df = pickle.load(f)
            if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
                return cached_df
        except Exception:
            pass

    cache_dir = './weekly_cache' if period_type == 'weekly' else './monthly_cache'
    if not os.path.exists(cache_dir):
        return None

    try:
        legacy_files = [
            file for file in glob.glob(os.path.join(cache_dir, '*.pkl'))
            if not file.endswith('_latest.pkl')
        ]
        if not legacy_files:
            return None
        latest_file = max(legacy_files, key=lambda file: os.path.getmtime(file))
        with open(latest_file, 'rb') as f:
            legacy_df = pickle.load(f)
        if isinstance(legacy_df, pd.DataFrame) and not legacy_df.empty:
            return legacy_df
    except Exception:
        return None

    return None


def _save_period_cache_snapshot(period_type: str, source_code: Optional[str], df: pd.DataFrame) -> None:
    """保存按代码的周/月快照缓存，供下次直接复用。"""
    if df is None or df.empty:
        return

    snapshot_file, meta_file = _period_cache_snapshot_paths(period_type, source_code)
    try:
        with open(snapshot_file, 'wb') as f:
            pickle.dump(df, f)
        meta = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'period_type': period_type,
            'source_code': _sanitize_cache_code(source_code),
            'rows': int(len(df)),
            'start': str(df['trade_date'].min()) if 'trade_date' in df.columns else '',
            'end': str(df['trade_date'].max()) if 'trade_date' in df.columns else '',
        }
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _get_period_cache_status(period_type: str, source_code: Optional[str]) -> dict:
    """读取周/月快照缓存状态信息。"""
    snapshot_file, meta_file = _period_cache_snapshot_paths(period_type, source_code)
    status = {
        'exists': os.path.exists(snapshot_file),
        'snapshot_file': snapshot_file,
        'meta_file': meta_file,
        'timestamp': '-',
        'rows': 0,
        'start': '-',
        'end': '-'
    }
    if os.path.exists(meta_file):
        try:
            with open(meta_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            status['timestamp'] = meta.get('timestamp', '-')
            status['rows'] = int(meta.get('rows', 0) or 0)
            status['start'] = meta.get('start', '-')
            status['end'] = meta.get('end', '-')
        except Exception:
            pass
    return status

def _prepare_similarity_df(input_df, period_type='daily', window_size=30, force_update=False, source_code: Optional[str] = None):
    """返回按周期转换后的数据框、实际窗口和未来天数。
    
    参数：
    - input_df: 输入的日线数据
    - period_type: 周期类型 ('daily', 'weekly', 'monthly')
    - window_size: 窗口大小
    - force_update: 是否强制更新缓存（忽略缓存，强制重新计算）
    - source_code: 标的代码（用于定位周/月缓存快照）
    """
    future_days = 20
    if period_type == 'weekly':
        # 周线相似度比较窗口固定为20根K线
        window_size = 20
        future_days = 4
        if force_update:
            # 强制更新时直接计算，不使用缓存
            transformed_df = _convert_to_weekly_nocache(input_df)
            _save_period_cache_snapshot('weekly', source_code, transformed_df)
        else:
            transformed_df = _load_period_cache_snapshot('weekly', source_code)
            if transformed_df is None:
                transformed_df = convert_to_weekly(input_df)
                _save_period_cache_snapshot('weekly', source_code, transformed_df)
        # 相似度搜索仅使用真实有效周期，剔除补齐出来的空周期
        if transformed_df is not None and 'is_valid_period' in transformed_df.columns:
            transformed_df = transformed_df[transformed_df['is_valid_period'] == True].copy()
    elif period_type == 'monthly':
        # 月线相似度比较窗口固定为20根K线
        window_size = 20
        future_days = 1
        if force_update:
            # 强制更新时直接计算，不使用缓存
            transformed_df = _convert_to_monthly_nocache(input_df)
            _save_period_cache_snapshot('monthly', source_code, transformed_df)
        else:
            transformed_df = _load_period_cache_snapshot('monthly', source_code)
            if transformed_df is None:
                transformed_df = convert_to_monthly(input_df)
                _save_period_cache_snapshot('monthly', source_code, transformed_df)
        # 相似度搜索仅使用真实有效周期，剔除补齐出来的空周期
        if transformed_df is not None and 'is_valid_period' in transformed_df.columns:
            transformed_df = transformed_df[transformed_df['is_valid_period'] == True].copy()
    else:
        transformed_df = input_df

    return transformed_df, int(window_size), int(future_days)


def _convert_to_weekly_nocache(df):
    """直接转换周K线，不使用缓存（用于强制更新），确保周K线连续显示"""
    if df is None or df.empty:
        return df
    
    df = df.copy()
    
    # 确保日期是datetime类型并设置为索引
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date', ascending=True)
    df.set_index('trade_date', inplace=True)
    
    # 使用 resample 进行周线转换（向量化优化）
    # 'W-MON' 表示以周一结束的周（确保周K线连续显示）
    weekly_df = df.resample('W-MON').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    })
    
    # 重置索引
    weekly_df = weekly_df.reset_index()
    
    # 添加周标识（年份+周数）
    weekly_df['year_week'] = weekly_df['trade_date'].dt.strftime('%Y-%U')
    
    # 确保周K线连续显示：使用fill_missing_periods填充缺失的周
    weekly_df = fill_missing_periods(weekly_df, period_type='weekly')
    
    return weekly_df


def _convert_to_monthly_nocache(df):
    """直接转换月K线，不使用缓存（用于强制更新），确保月K线连续显示"""
    if df is None or df.empty:
        return df
    
    df = df.copy()
    
    # 确保日期是datetime类型并设置为索引
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date', ascending=True)
    df.set_index('trade_date', inplace=True)
    
    # 使用 resample 进行月线转换（向量化优化）
    # 'MS' 表示月初（与fill_missing_periods保持一致，确保月K线连续显示）
    monthly_df = df.resample('MS').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    })
    
    # 重置索引
    monthly_df = monthly_df.reset_index()
    
    # 添加月标识（年份+月份）
    monthly_df['year_month'] = monthly_df['trade_date'].dt.strftime('%Y-%m')
    
    # 确保月K线连续显示：使用fill_missing_periods填充缺失的月
    monthly_df = fill_missing_periods(monthly_df, period_type='monthly')
    
    return monthly_df


def _calculate_macd_similarity_from_precomputed(macd1, macd2):
    """
    使用预计算的MACD指标计算相似度
    
    参数:
    - macd1: 预计算的MACD DataFrame (包含DIF, DEA, MACD列)
    - macd2: 预计算的MACD DataFrame (包含DIF, DEA, MACD列)
    
    返回:
    - 相似度得分 (0~1)
    """
    if macd1 is None or macd2 is None or macd1.empty or macd2.empty:
        return 0.0
    
    align_len = min(len(macd1), len(macd2))
    if align_len < 5:
        return 0.0
    
    m1 = macd1.tail(align_len).reset_index(drop=True)
    m2 = macd2.tail(align_len).reset_index(drop=True)
    
    dif_sim = _sequence_similarity(m1['DIF'], m2['DIF'])
    dea_sim = _sequence_similarity(m1['DEA'], m2['DEA'])
    hist_sim = _sequence_similarity(m1['MACD'], m2['MACD'])
    
    return 0.4 * dif_sim + 0.3 * dea_sim + 0.3 * hist_sim


def _calculate_kdj_similarity_from_precomputed(kdj1, kdj2):
    """
    使用预计算的KDJ指标计算相似度
    
    参数:
    - kdj1: 预计算的KDJ DataFrame (包含K, D, J列)
    - kdj2: 预计算的KDJ DataFrame (包含K, D, J列)
    
    返回:
    - 相似度得分 (0~1)
    """
    if kdj1 is None or kdj2 is None or kdj1.empty or kdj2.empty:
        return 0.0
    
    align_len = min(len(kdj1), len(kdj2))
    if align_len < 5:
        return 0.0
    
    k1 = kdj1.tail(align_len).reset_index(drop=True)
    k2 = kdj2.tail(align_len).reset_index(drop=True)
    
    k_sim = _sequence_similarity(k1['K'], k2['K'])
    d_sim = _sequence_similarity(k1['D'], k2['D'])
    j_sim = _sequence_similarity(k1['J'], k2['J'])
    
    return 0.4 * k_sim + 0.4 * d_sim + 0.2 * j_sim


def _find_patterns_in_single_series(
    data_df,
    window_size,
    top_n,
    future_days,
    period_type,
    use_optimized,
    source_code,
    source_type='history',
    current_df_for_period=None,
    similarity_weights=None
):
    """在单个标的序列内寻找历史相似模式。"""
    if data_df is None or data_df.empty:
        return []

    required_columns = {'trade_date', 'close'}
    if not required_columns.issubset(set(data_df.columns)):
        return []

    df_work = data_df.copy()
    if df_work['close'].isna().all():
        return []

    if current_df_for_period is None:
        current_df_for_period = df_work

    recent_df = df_work.tail(window_size).copy()
    if len(df_work) < window_size * 2 or len(recent_df) < window_size:
        return []

    similar_patterns = []
    max_i = len(df_work) - (window_size * 2 + future_days)
    if max_i <= 0:
        return []

    if use_optimized:
        def _is_pattern_only(weights):
            if not isinstance(weights, dict):
                return False
            pattern_w = float(weights.get('pattern', 0.0))
            others = (
                float(weights.get('trend', 0.0)) +
                float(weights.get('volume', 0.0)) +
                float(weights.get('macd', 0.0)) +
                float(weights.get('kdj', 0.0)) +
                float(weights.get('regime', 0.0))
            )
            return pattern_w > 0 and others <= 1e-9

        # 形态专用极速路径：仅计算K线形态相似度，跳过技术指标/综合精排
        if _is_pattern_only(similarity_weights):
            need_cols = {'open', 'high', 'low', 'close'}
            if need_cols.issubset(set(df_work.columns)):
                eps = 1e-9
                o = df_work['open'].values.astype(float)
                h = df_work['high'].values.astype(float)
                l = df_work['low'].values.astype(float)
                c = df_work['close'].values.astype(float)

                # 最近窗口特征
                ro = recent_df['open'].values.astype(float)
                rh = recent_df['high'].values.astype(float)
                rl = recent_df['low'].values.astype(float)
                rc = recent_df['close'].values.astype(float)

                r_total = np.maximum(rh - rl, eps)
                r_body = np.abs(rc - ro) / r_total
                r_upper = (rh - np.maximum(ro, rc)) / r_total
                r_lower = (np.minimum(ro, rc) - rl) / r_total

                # 候选窗口特征矩阵
                try:
                    wo = np.lib.stride_tricks.sliding_window_view(o, window_shape=window_size)[:max_i]
                    wh = np.lib.stride_tricks.sliding_window_view(h, window_shape=window_size)[:max_i]
                    wl = np.lib.stride_tricks.sliding_window_view(l, window_shape=window_size)[:max_i]
                    wc = np.lib.stride_tricks.sliding_window_view(c, window_shape=window_size)[:max_i]
                except Exception:
                    wo = np.array([o[i:i + window_size] for i in range(max_i)])
                    wh = np.array([h[i:i + window_size] for i in range(max_i)])
                    wl = np.array([l[i:i + window_size] for i in range(max_i)])
                    wc = np.array([c[i:i + window_size] for i in range(max_i)])

                w_total = np.maximum(wh - wl, eps)
                w_body = np.abs(wc - wo) / w_total
                w_upper = (wh - np.maximum(wo, wc)) / w_total
                w_lower = (np.minimum(wo, wc) - wl) / w_total

                def _row_corr(X, y):
                    Xc = X - X.mean(axis=1, keepdims=True)
                    yc = y - y.mean()
                    denom = (np.linalg.norm(Xc, axis=1) * max(np.linalg.norm(yc), eps)) + eps
                    return np.clip((Xc @ yc) / denom, -1.0, 1.0)

                body_corr = _row_corr(w_body, r_body)
                upper_corr = _row_corr(w_upper, r_upper)
                lower_corr = _row_corr(w_lower, r_lower)

                # 增加距离项，避免纯相关系数导致大量近1分并列
                body_dist = np.linalg.norm(w_body - r_body[None, :], axis=1)
                upper_dist = np.linalg.norm(w_upper - r_upper[None, :], axis=1)
                lower_dist = np.linalg.norm(w_lower - r_lower[None, :], axis=1)
                dist_score = 1.0 / (1.0 + (0.5 * body_dist + 0.25 * upper_dist + 0.25 * lower_dist) / max(1, window_size))

                corr_score = np.clip((body_corr * 0.5 + upper_corr * 0.25 + lower_corr * 0.25 + 1.0) / 2.0, 0.0, 1.0)
                similarities = np.clip(corr_score * 0.7 + dist_score * 0.3, 0.0, 1.0)
                top_indices = np.argsort(similarities)[-top_n:][::-1]

                for i in top_indices:
                    i = int(i)
                    historical_df = df_work.iloc[i:i + window_size]
                    future_start = i + window_size
                    future_end = min(future_start + future_days, len(df_work))
                    future_data = df_work.iloc[future_start:future_end].copy() if future_end > future_start else pd.DataFrame()

                    similar_patterns.append({
                        'similarity': float(similarities[i]),
                        'start_date': historical_df['trade_date'].iloc[0],
                        'end_date': historical_df['trade_date'].iloc[-1],
                        'pattern_data': historical_df.copy(),
                        'future_data': future_data,
                        'period_type': period_type,
                        'source_code': source_code,
                        'source_type': source_type,
                    })

                return similar_patterns

        market_volatility = recent_df['close'].std() / recent_df['close'].mean() if recent_df['close'].mean() != 0 else 1.0

        # 阶段1：快速向量化粗筛（速度优先）
        close_values = df_work['close'].values.astype(float)
        if len(close_values) < window_size:
            return []

        recent_close = recent_df['close'].values.astype(float)
        eps = 1e-9
        recent_base = recent_close[0] if abs(recent_close[0]) > eps else eps
        recent_norm = (recent_close - recent_base) / recent_base * 100.0

        try:
            windows_2d = np.lib.stride_tricks.sliding_window_view(close_values, window_shape=window_size)[:max_i]
        except Exception:
            windows_2d = np.array([close_values[i:i + window_size] for i in range(max_i)])

        if windows_2d.size == 0:
            return []

        win_base = np.where(np.abs(windows_2d[:, 0]) > eps, windows_2d[:, 0], eps)
        windows_norm = (windows_2d - win_base[:, None]) / win_base[:, None] * 100.0

        # 形态相关性（z-score相关）
        w_centered = windows_norm - windows_norm.mean(axis=1, keepdims=True)
        r_centered = recent_norm - recent_norm.mean()
        w_norm = np.linalg.norm(w_centered, axis=1) + eps
        r_norm = np.linalg.norm(r_centered) + eps
        corr = np.clip((w_centered @ r_centered) / (w_norm * r_norm), -1.0, 1.0)

        # 距离相似度
        dist = np.linalg.norm(windows_norm - recent_norm[None, :], axis=1)
        dist_sim = 1.0 / (1.0 + dist / max(1, window_size))

        # 趋势斜率相似度（提升准确度）
        slope_recent = recent_norm[-1] - recent_norm[0]
        slope_windows = windows_norm[:, -1] - windows_norm[:, 0]
        slope_sim = 1.0 - np.minimum(1.0, np.abs(slope_windows - slope_recent) / (np.abs(slope_recent) + 10.0))

        # 成交量轮廓相似（存在volume时）
        volume_sim = np.zeros(max_i, dtype=float)
        if 'volume' in df_work.columns:
            vol_values = np.log1p(np.maximum(0.0, df_work['volume'].values.astype(float)))
            try:
                vol_wins = np.lib.stride_tricks.sliding_window_view(vol_values, window_shape=window_size)[:max_i]
            except Exception:
                vol_wins = np.array([vol_values[i:i + window_size] for i in range(max_i)])
            vol_recent = vol_values[-window_size:]
            vw_centered = vol_wins - vol_wins.mean(axis=1, keepdims=True)
            vr_centered = vol_recent - vol_recent.mean()
            vw_norm = np.linalg.norm(vw_centered, axis=1) + eps
            vr_norm = np.linalg.norm(vr_centered) + eps
            volume_sim = np.clip((vw_centered @ vr_centered) / (vw_norm * vr_norm), -1.0, 1.0)
            volume_sim = (volume_sim + 1.0) / 2.0

        quick_score = (
            ((corr + 1.0) / 2.0) * 0.55 +
            dist_sim * 0.25 +
            slope_sim * 0.12 +
            volume_sim * 0.08
        )

        # 日线优先速度：降低精排候选规模；周/月保持较高候选覆盖
        if period_type == 'daily':
            pre_top_k = min(max(top_n * 6, 30), max_i)
        else:
            pre_top_k = min(max(top_n * 10, 40), max_i)
        candidate_indices = np.argsort(quick_score)[-pre_top_k:][::-1]

        # 阶段2：对候选窗口做精排（准确度优先）
        refined = []
        for i in candidate_indices:
            historical_df = df_work.iloc[int(i):int(i) + window_size]
            if historical_df.empty:
                continue
            similarity = calculate_comprehensive_similarity(
                recent_df,
                historical_df,
                market_volatility=market_volatility,
                return_components=False,
                weight_profile=similarity_weights
            )
            # 融合粗筛分，兼顾速度与稳健性
            final_similarity = float(similarity) * 0.82 + float(quick_score[int(i)]) * 0.18
            refined.append((int(i), max(0.0, min(1.0, final_similarity)), historical_df))

        if not refined:
            return []

        refined.sort(key=lambda item: item[1], reverse=True)
        top_refined = refined[:top_n]

        for i, similarity, historical_df in top_refined:

            future_start = i + window_size
            future_end = min(future_start + future_days, len(df_work))

            if future_end > future_start:
                future_data = df_work.iloc[future_start:future_end].copy()
            else:
                future_data = pd.DataFrame()

            similar_patterns.append({
                'similarity': similarity,
                'start_date': historical_df['trade_date'].iloc[0],
                'end_date': historical_df['trade_date'].iloc[-1],
                'pattern_data': historical_df,
                'future_data': future_data,
                'period_type': period_type,
                'source_code': source_code,
                'source_type': source_type,
            })
    else:
        recent_window = df_work.tail(window_size)['close'].values
        if recent_window.size == 0:
            return []

        if recent_window[0] == 0:
            return []

        recent_window_norm = (recent_window - recent_window[0]) / recent_window[0] * 100

        all_windows = np.array([
            df_work['close'].values[i:i + window_size]
            for i in range(max_i)
        ])
        all_windows_norm = np.array([
            (window - window[0]) / window[0] * 100
            for window in all_windows
        ])

        correlations = []
        distances = []
        for i, window_norm in enumerate(all_windows_norm):
            if i >= max_i:
                break
            try:
                correlations.append(pearsonr(recent_window_norm, window_norm)[0])
            except Exception:
                correlations.append(0)

            try:
                distances.append(euclidean(recent_window_norm, window_norm))
            except Exception:
                distances.append(0)

        if not correlations:
            return []

        correlations_array = np.array(correlations, dtype=float)
        distances_array = np.array(distances, dtype=float)
        normalized_distances = 1 / (1 + distances_array / max(1, window_size))
        similarities = correlations_array * 0.7 + normalized_distances * 0.3

        top_indices = np.argsort(similarities)[-top_n:][::-1]

        for i in top_indices:
            future_start = i + window_size
            future_end = min(future_start + future_days, len(df_work))

            if future_end > future_start:
                future_data = df_work.iloc[future_start:future_end].copy()
            else:
                future_data = pd.DataFrame()

            similar_patterns.append({
                'similarity': float(similarities[i]),
                'start_date': df_work.iloc[i]['trade_date'],
                'end_date': df_work.iloc[i + window_size - 1]['trade_date'],
                'pattern_data': df_work.iloc[i:i + window_size].copy(),
                'future_data': future_data,
                'period_type': period_type,
                'source_code': source_code,
                'source_type': source_type,
            })

    return similar_patterns


def find_similar_patterns(
    df,
    window_size=30,
    top_n=3,
    use_optimized=True,
    period_type='daily',
    search_across_market=False,
    search_codes=None,
    max_candidate_codes=80,
    current_code=None,
    security_type='stock',
    force_update=False,
    similarity_weights=None
):
    """
    查找最相似的历史模式
    
    参数：
    - df: 包含价格和成交量数据的DataFrame（日线数据）
    - window_size: 窗口大小（默认30个交易日）
    - top_n: 返回最相似的N个模式
    - use_optimized: 是否使用优化算法（默认True）
    - period_type: 时间周期类型，'daily'（日线）、'weekly'（周线）、'monthly'（月线）
    - search_across_market: 是否跨标的检索历史模式
    - search_codes: 明确指定候选标的代码列表
    - max_candidate_codes: 跨标的时最多扫描的候选标的数
    - current_code: 当前标的代码
    - security_type: 标的类型，影响跨标的候选池（默认 stock）
    - force_update: 是否强制更新周/月K线缓存（默认 False）
    
    技术点：
    - K线模式识别：通过滑动窗口方法识别历史K线形态
    - 多维度特征提取：价格、成交量、技术指标、形态特征
    - 自适应权重调整：根据市场波动率调整特征权重
    - 批量计算优化：提高计算效率
    - 多周期支持：支持日线、周线、月线数据的相似度计算
    """
    source_df = df.copy() if isinstance(df, pd.DataFrame) else None
    if source_df is None or source_df.empty:
        return []

    source_df = source_df.copy()

    # 当前标的的周期化数据
    source_period_df, window_size, future_days = _prepare_similarity_df(
        source_df,
        period_type=period_type,
        window_size=window_size,
        force_update=force_update,
        source_code=current_code or 'current'
    )

    max_n = max(1, int(top_n))
    candidate_patterns = _find_patterns_in_single_series(
        source_period_df,
        window_size=window_size,
        top_n=max_n * 2,
        future_days=future_days,
        period_type=period_type,
        use_optimized=use_optimized,
        source_code=current_code or 'current',
        source_type=security_type,
        current_df_for_period=source_period_df,
        similarity_weights=similarity_weights
    )

    def _normalize_candidate_code(raw_code: object) -> str | None:
        """清洗候选码：保留数字并去掉交易所前缀。"""
        if raw_code is None:
            return None

        normalized = str(raw_code).strip()
        if not normalized:
            return None

        normalized = normalized.replace(' ', '')
        if normalized.lower().startswith('sh'):
            normalized = normalized[2:]
        elif normalized.lower().startswith('sz'):
            normalized = normalized[2:]

        normalized = ''.join(ch for ch in normalized if ch.isdigit())
        if not normalized:
            return None

        return normalized

    def _normalize_candidate_codes(raw_codes) -> list[str]:
        """将原始候选码列表标准化为字符串列表。"""
        if raw_codes is None:
            return []

        if isinstance(raw_codes, str):
            candidates = re.split(r"[;,\s]+", raw_codes)
        else:
            try:
                candidates = list(raw_codes)
            except TypeError:
                candidates = [raw_codes]

        normalized = [_normalize_candidate_code(item) for item in candidates]
        return [code for code in normalized if code is not None]

    def _dedupe_keep_order(items: list[str]) -> list[str]:
        seen = set()
        deduped: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    # 如无需跨标的，直接返回当前标的结果
    if not search_across_market:
        return candidate_patterns[:max_n]

    normalized_current_code = _normalize_candidate_code(current_code)
    if not isinstance(max_candidate_codes, int):
        try:
            max_candidate_codes = int(max_candidate_codes)
        except Exception:
            max_candidate_codes = 80
    max_candidate_codes = max(0, max_candidate_codes)

    candidate_codes = []
    if search_codes:
        candidate_codes = _normalize_candidate_codes(search_codes)
    else:
        try:
            from tdx_reader import get_tdx_reader

            tdx_reader = get_tdx_reader()
            available = tdx_reader.get_available_codes()
            all_candidates = list(available.get('sh', [])) + list(available.get('sz', []))
            candidate_codes = _normalize_candidate_codes(all_candidates)
        except Exception as e:
            print(f"跨标的候选码读取失败: {e}")
            candidate_codes = []

    candidate_codes = _dedupe_keep_order(candidate_codes)

    # 过滤重复并控制总量，优先保留当前标的
    if normalized_current_code:
        candidate_codes = [
            normalized_current_code,
            *[code for code in candidate_codes if code != normalized_current_code]
        ]

    uniq_codes = _dedupe_keep_order(candidate_codes)

    # 限流避免大规模扫描拖慢交互
    filtered_codes = uniq_codes[:max_candidate_codes] if max_candidate_codes > 0 else []

    if not filtered_codes:
        return candidate_patterns[:max_n]

    try:
        from tdx_reader import read_tdx_data
    except Exception as e:
        print(f"跨标的时读取read_tdx_data失败: {e}")
        return candidate_patterns[:max_n]

    # 为每个候选标的计算匹配（当前标的已在前面收集）
    for code in filtered_codes:
        if normalized_current_code and code == normalized_current_code:
            continue

        try:
            candidate_df = read_tdx_data(
                code=code,
                security_type=security_type
            )
        except Exception:
            continue

        if candidate_df is None or candidate_df.empty:
            continue

        transformed_df, candidate_window_size, candidate_future_days = _prepare_similarity_df(
            candidate_df,
            period_type=period_type,
            window_size=window_size,
            force_update=force_update,
            source_code=code
        )

        patterns = _find_patterns_in_single_series(
            transformed_df,
            window_size=candidate_window_size,
            top_n=max_n,
            future_days=candidate_future_days,
            period_type=period_type,
            use_optimized=use_optimized,
            source_code=code,
            source_type=security_type,
            current_df_for_period=transformed_df,
            similarity_weights=similarity_weights
        )

        candidate_patterns.extend(patterns)

    candidate_patterns.sort(key=lambda item: float(item.get('similarity', 0.0)), reverse=True)

    # 去重：同一来源同一起始点只保留一个
    deduped = []
    seen_keys = set()
    for pattern in candidate_patterns:
        key = (
            pattern.get('source_code', ''),
            str(pattern.get('start_date', '')),
            str(pattern.get('end_date', '')),
            round(float(pattern.get('similarity', 0.0)), 6)
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(pattern)

    return deduped[:max_n]

def analyze_future_trends(similar_patterns, period_type=None):
    """
    分析历史K线后续走势
    
    参数：
    - similar_patterns: 相似模式列表
    - period_type: 时间周期类型，'daily'（日线）、'weekly'（周线）、'monthly'（月线）。如果为None，则从第一个模式中提取
    
    技术点：
    - 历史模式匹配：基于相似K线模式的历史表现
    - 统计概率分析：计算涨跌概率和幅度分布
    - 趋势预测建模：构建未来可能的走势预测
    - 多周期支持：支持日线、周线、月线数据的趋势分析
    """
    if not similar_patterns:
        return None
    
    # 如果未提供period_type，从第一个模式中提取
    if period_type is None and similar_patterns:
        period_type = similar_patterns[0].get('period_type', 'daily')
        
    # 确定最大未来周期数
    max_future_periods = 0
    for pattern in similar_patterns:
        if len(pattern['future_data']) > max_future_periods:
            max_future_periods = len(pattern['future_data'])
    
    # 根据周期类型设置标签
    if period_type == 'weekly':
        period_labels = {str(i): f'第{i}周' for i in range(1, max_future_periods + 1)}
    elif period_type == 'monthly':
        period_labels = {str(i): f'第{i}个月' for i in range(1, max_future_periods + 1)}
    else:  # daily
        period_labels = {str(i): f'第{i}天' for i in range(1, max_future_periods + 1)}
    
    stats = {
        'up': {str(i): {'count': 0, 'max': 0, 'min': float('inf'), 'mean': 0, 'values': [], 'label': period_labels.get(str(i), f'第{i}期')} 
               for i in range(1, max_future_periods + 1)},
        'down': {str(i): {'count': 0, 'max': 0, 'min': float('inf'), 'mean': 0, 'values': [], 'label': period_labels.get(str(i), f'第{i}期')} 
                 for i in range(1, max_future_periods + 1)}
    }
    
    for pattern in similar_patterns:
        future_data = pattern['future_data']
        
        for i in range(len(future_data)):
            day = str(i + 1)
            current_price = future_data.iloc[i]['close']
            prev_price = pattern['pattern_data'].iloc[-1]['close'] if i == 0 else future_data.iloc[i-1]['close']
            
            change_rate = ((current_price - prev_price) / prev_price) * 100
            
            category = 'up' if change_rate >= 0 else 'down'
            change_rate = abs(change_rate)
            
            stats[category][day]['count'] += 1
            stats[category][day]['values'].append(change_rate)
            stats[category][day]['max'] = max(stats[category][day]['max'], change_rate)
            stats[category][day]['min'] = min(stats[category][day]['min'], change_rate)
    
    total_patterns = len(similar_patterns)
    for category in ['up', 'down']:
        for day in stats[category]:
            day_stats = stats[category][day]
            if day_stats['count'] > 0:
                day_stats['probability'] = day_stats['count'] / total_patterns
                day_stats['mean'] = sum(day_stats['values']) / day_stats['count']
                rounded_values = [round(x, 2) for x in day_stats['values']]
                value_counts = {}
                for v in rounded_values:
                    value_counts[v] = value_counts.get(v, 0) + 1
                day_stats['mode'] = max(value_counts.items(), key=lambda x: x[1])[0]
                
                if day_stats['min'] == float('inf'):
                    day_stats['min'] = 0
                
            del day_stats['values']
    
    return stats

def analyze_holding_returns(similar_patterns):
    """
    分析不同持有期的收益情况
    
    技术点：
    - 持仓期收益率计算：计算不同持有期的收益表现
    - 风险度量(标准差/波动率)：评估投资风险水平
    - 胜率统计：分析不同持有期的盈利概率
    - 多周期支持：支持日线、周线、月线数据的持仓收益分析
    """
    if not similar_patterns:
        return None
    
    # 从第一个模式中提取周期类型
    period_type = similar_patterns[0].get('period_type', 'daily')
    
    # 确定最大持有期数
    max_holding_periods = 0
    for pattern in similar_patterns:
        if len(pattern['future_data']) > max_holding_periods:
            max_holding_periods = len(pattern['future_data'])
    
    # 根据周期类型设置标签
    if period_type == 'weekly':
        period_labels = {str(i): f'持有{i}周' for i in range(1, max_holding_periods + 1)}
    elif period_type == 'monthly':
        period_labels = {str(i): f'持有{i}个月' for i in range(1, max_holding_periods + 1)}
    else:  # daily
        period_labels = {str(i): f'持有{i}天' for i in range(1, max_holding_periods + 1)}
        
    stats = {str(i): {
        'returns': [],
        'max_prices': [],
        'min_prices': [],
        'win_count': 0,
        'loss_count': 0,
        'label': period_labels.get(str(i), f'持有{i}期')
    } for i in range(1, max_holding_periods + 1)}
    
    for pattern in similar_patterns:
        entry_price = pattern['pattern_data'].iloc[-1]['close']
        future_data = pattern['future_data']
        
        for days in range(1, 21):  # 分析20个交易日
            day_key = str(days)
            if days <= len(future_data):
                holding_period_data = future_data.iloc[:days]
                exit_price = holding_period_data.iloc[-1]['close']
                returns = (exit_price - entry_price) / entry_price * 100
                
                stats[day_key]['returns'].append(returns)
                
                if returns > 0:
                    stats[day_key]['win_count'] += 1
                else:
                    stats[day_key]['loss_count'] += 1
                
                stats[day_key]['max_prices'].append(holding_period_data['high'].max())
                stats[day_key]['min_prices'].append(holding_period_data['low'].min())
    
    analysis_results = {}
    for days, day_stats in stats.items():
        returns_array = np.array(day_stats['returns'])
        total_trades = len(returns_array)
        
        if total_trades > 0:
            analysis_results[days] = {
                'avg_return': np.mean(returns_array),
                'max_return': np.max(returns_array),
                'min_return': np.min(returns_array),
                'std_return': np.std(returns_array),
                'win_rate': day_stats['win_count'] / total_trades,
                'trade_count': total_trades,
                'max_price_change': (np.max(day_stats['max_prices']) - entry_price) / entry_price * 100,
                'min_price_change': (np.min(day_stats['min_prices']) - entry_price) / entry_price * 100
            }
    
    return analysis_results

class ChineseTextVectorizer:
    """中文文本向量化器"""
    
    def __init__(self, vector_size=100):
        self.vector_size = vector_size
        self.tfidf = TfidfVectorizer(
            tokenizer=self._parallel_tokenize,  # 使用并行分词
            token_pattern=None,
            max_features=2000  # 减少特征数量
        )
        self.svd = TruncatedSVD(
            n_components=vector_size,
            random_state=42,
            algorithm='randomized'  # 使用随机算法加速
        )
        self.is_fitted = False

         # 预加载结巴词典
        jieba.initialize()
    
    @lru_cache(maxsize=1000)  # 缓存分词结果
    def _tokenize(self, text):
        text = re.sub(r'[^\w\s]', '', text)
        words = jieba.lcut(text)
        return [w for w in words if w.strip()]
    
    def fit(self, texts):
        tfidf_matrix = self.tfidf.fit_transform(texts)
        self.svd.fit(tfidf_matrix)
        self.is_fitted = True
    
    def transform(self, text):
        tfidf_vector = self.tfidf.transform([text])
        vector = self.svd.transform(tfidf_vector)
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        return vector.flatten()
    
    def _parallel_tokenize(self, text):
        # 对较长的文本进行分块
        if len(text) < 1000:  # 短文本直接处理
            return self._tokenize(text)
            
        # 长文本并行处理
        chunks = self._split_text(text)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(self._tokenize, chunks))
        return [token for chunk_result in results for token in chunk_result]

    def _split_text(self, text, chunk_size=800):
        """将长文本按固定长度切分，避免单次分词过长导致性能问题"""
        if not text:
            return [""]
        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

def call_llm_api(prompt):
    """调用 LLM API"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }
    
    try:
        response = requests.post(
            f"{API_BASE}/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        st.error(f"API 调用出错: {str(e)}")
        return None

def compute_embedding(text, vectorizer=None):
    """计算文本的向量表示"""
    if vectorizer is None:
        vectorizer = ChineseTextVectorizer()
        
    try:
        if not text.strip():
            return np.zeros(vectorizer.vector_size)
            
        if not vectorizer.is_fitted:
            vectorizer.fit([text])
            
        embedding = vectorizer.transform(text)
        return embedding
        
    except Exception as e:
        print(f"计算文本向量时发生错误: {str(e)}")
        return np.zeros(vectorizer.vector_size)

def compute_similarity(query_embedding, chunk_embedding):
    """计算两个向量之间的余弦相似度"""
    if query_embedding.shape != chunk_embedding.shape:
        raise ValueError("向量维度不匹配")
        
    similarity = np.dot(query_embedding, chunk_embedding)
    return similarity

def retrieve_relevant_chunks(query, chunks, top_k=3):
    """检索与查询最相关的文本块"""
    vectorizer = ChineseTextVectorizer(vector_size=50)  # 减少向量维度
    
    # 预处理所有文本
    all_texts = [query] + [chunk_text for _, chunk_text in chunks]
    vectorizer.fit(all_texts)
    
    # 批量计算向量
    query_embedding = compute_embedding(query, vectorizer)
    chunk_embeddings = np.vstack([
        compute_embedding(chunk_text, vectorizer)
        for _, chunk_text in chunks
    ])
    
    # 批量计算相似度
    similarities = np.dot(chunk_embeddings, query_embedding)
    
    # 获取top_k个最相似的chunk
    top_indices = np.argsort(similarities)[-top_k:][::-1]
    return [chunks[i] for i in top_indices]

def create_text_chunks(security, current_df, similar_patterns, holding_stats):
    """
    将市场数据分成多个独立的文本块，便于后续检索

    技术点：
    - RAG检索增强生成：构建结构化的文本块供检索
    - 向量化(TF-IDF + SVD)：将文本转化为向量表示
    - 余弦相似度匹配：计算查询与文本块的相似度
    
    这个函数将各类市场数据转换成结构化的文本块，包括：
    1. 证券基本信息（代码、名称等）
    2. 最新行情数据
    3. 近期价格走势
    4. 历史表现分析
    5. 相似K线分析
    6. 持仓收益分析
    """
    chunks = []
    
    # 构建基本信息文本块
    basic_info = f"""
            证券基本信息：
            名称：{security['name']}
            代码：{security['code']}
            类型：{security['type']}
            交易所：{security['exchange'] if security['exchange'] else '未知'}
    """
    chunks.append(("basic_info", basic_info))
    
    if current_df is not None and not current_df.empty:
        # 添加最新行情信息
        latest_data = current_df.iloc[-1]
        latest_market = f"""
            最新市场行情（{latest_data['trade_date'].strftime('%Y-%m-%d')}）：
            收盘价：{latest_data['close']:.2f}
            开盘价：{latest_data['open']:.2f}
            最高价：{latest_data['high']:.2f}
            最低价：{latest_data['low']:.2f}
            成交量：{latest_data.get('volume', '未知')}
        """
        chunks.append(("latest_market", latest_market))
        
        # 添加近期走势分析（如果有足够数据）
        if len(current_df) >= 21:
            recent_trend = f"""
            近期价格走势：
            5日涨跌幅：{((latest_data['close'] - current_df.iloc[-6]['close'])/current_df.iloc[-6]['close']*100):.2f}%
            10日涨跌幅：{((latest_data['close'] - current_df.iloc[-11]['close'])/current_df.iloc[-11]['close']*100):.2f}%
            20日涨跌幅：{((latest_data['close'] - current_df.iloc[-21]['close'])/current_df.iloc[-21]['close']*100):.2f}%
            """
            chunks.append(("recent_trend", recent_trend))
        
        # 添加历史表现分析（最近3年）
        three_year_data = current_df.tail(252 * 3).copy()
        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_columns:
            if col in three_year_data.columns:
                three_year_data[col] = pd.to_numeric(three_year_data[col], errors='coerce')
        
        if not three_year_data.empty:
            # 按年度统计市场表现
            yearly_data = {}
            for year in three_year_data['trade_date'].dt.year.unique():
                year_df = three_year_data[three_year_data['trade_date'].dt.year == year]
                if not year_df.empty:
                    yearly_data[year] = {
                        'start_price': year_df.iloc[0]['close'],
                        'end_price': year_df.iloc[-1]['close'],
                        'highest': year_df['high'].max(),
                        'lowest': year_df['low'].min(),
                        'vol_mean': pd.to_numeric(year_df.get('volume', pd.Series()), errors='coerce').mean()
                    }
            
            # 构建年度分析文本
            historical_trend = """
            近年市场表现："""
            for year, data in sorted(yearly_data.items(), reverse=True):
                yearly_return = ((data['end_price'] - data['start_price']) / data['start_price'] * 100)
                yearly_volatility = ((data['highest'] - data['lowest']) / data['lowest'] * 100)
                
                historical_trend += f"""
                {year}年度表现：
                年度涨跌幅：{yearly_return:.2f}%
                最高价：{data['highest']:.2f}
                最低价：{data['lowest']:.2f}
                波动率：{yearly_volatility:.2f}%"""
                if pd.notnull(data['vol_mean']):
                    historical_trend += f"""平均成交量：{data['vol_mean']:.0f}"""
            
            # 添加3年整体统计
            total_return = ((latest_data['close'] - three_year_data.iloc[0]['close']) 
                          / three_year_data.iloc[0]['close'] * 100)
            max_price = three_year_data['high'].max()
            min_price = three_year_data['low'].min()
            total_volatility = ((max_price - min_price) / min_price * 100)
            
            historical_trend += f"""
            近年整体统计：
            累计涨跌幅：{total_return:.2f}%
            历史最高价：{max_price:.2f}
            历史最低价：{min_price:.2f}
            价格波动率：{total_volatility:.2f}%
            """
            
            chunks.append(("historical_trend", historical_trend))
    
    # 添加相似K线分析结果
    if similar_patterns:
        for i, pattern in enumerate(similar_patterns[:3], 1):
            future_trend = ""
            for j, row in pattern['future_data'].iterrows():
                future_trend += f"""
            第{j+1}日:{row["close"]:.2f}"""
            
            pattern_info = f"""
            历史相似K线分析（TOP {i}）：
            相似度：{pattern['similarity']:.2%}
            历史时间段：{pattern['start_date'].strftime('%Y-%m-%d')} 至 {pattern['end_date'].strftime('%Y-%m-%d')}
            后续7日实际走势：{future_trend}
                """
            chunks.append((f"similar_pattern_{i}", pattern_info))
    
    # 添加持仓分析数据
    if holding_stats:
        for days, stats in holding_stats.items():
            holding_info = f"""            持仓{days}天分析：
            平均收益：{stats['avg_return']:.2f}%
            胜率：{stats['win_rate']*100:.1f}%
            最大上涨：{stats['max_return']:.2f}%
            最大下跌：{stats['min_return']:.2f}%
            波动率：{stats['std_return']:.2f}%"""
            chunks.append((f"holding_days_{days}", holding_info))
    
    return chunks

def get_analysis_prompt(query, relevant_chunks, chat_history=None):
    """
    构建带有检索上下文和对话历史的分析提示
    
    Args:
        query: 当前用户问题
        relevant_chunks: 相关的市场数据块
        chat_history: 之前的对话历史记录，格式为 [(user_msg, assistant_msg), ...]
    """
    context = "\n".join([chunk for _, chunk in relevant_chunks])
    
    # 构建提示的基础部分
    prompt = f"""作为一位专业的金融分析师，请基于以下相关市场数据回答用户问题。

要求：
1. 只使用提供的数据进行分析，不要添加其他市场信息
2. 明确区分数据支持的结论和不确定的推测
3. 如果数据不足以回答问题，请明确指出
4. 适当提醒投资风险

相关市场数据：
{context}
"""

    # 如果存在对话历史，添加到提示中
    if chat_history and len(chat_history) > 0:
        prompt += "\n对话历史：\n"
        for i, (user_msg, assistant_msg) in enumerate(chat_history, 1):
            prompt += f"第{i}轮问答：\n"
            prompt += f"用户：{user_msg}\n"
            prompt += f"助手：{assistant_msg}\n"
    
    # 添加当前问题
    prompt += f"\n当前用户问题：{query}"
    
    # 添加角色指示
    prompt += """

请基于以上信息和对话历史，遵循以下原则回答：
1. 用专业且通俗的语言回答问题，确保分析逻辑清晰
2. 如果涉及到之前的对话内容，请保持分析的连贯性
3. 在回答中适当提供一些思考的切入点，引导用户进行更深入的提问
4. 如果用户追问某个观点，请进一步展开解释背后的原理和依据
5. 如果某个分析涉及到多个方面，可以明确指出，方便用户选择感兴趣的方向继续探讨
"""
    
    return prompt

def display_market_analysis(current_df, similar_patterns, future_dates=None, security_info=None):
    """
    市场分析主函数 - 专业股票软件风格，4格多股同列布局
    """
    if current_df is None or current_df.empty:
        st.warning("无法获取市场数据")
        return None

    security_info = security_info or {}
    
    # 专业股票软件头部 - 显示证券信息
    if security_info:
        st.markdown(f"""
        <div class="professional-header">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h2 style="margin: 0; font-size: 20px;">{security_info.get('name', '未知')} ({security_info.get('code', '未知')})</h2>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">
                        {security_info.get('type', '')} | 交易所: {security_info.get('exchange', '未知')}
                    </p>
                </div>
                <div style="text-align: right;">
                    <div style="font-size: 24px; font-weight: bold;">
                        {current_df.iloc[-1]['close']:.2f}
                    </div>
                    <div style="font-size: 14px; margin-top: 5px;">
                        最新: {current_df.iloc[-1]['trade_date'].strftime('%Y-%m-%d')}
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    # 控制面板 - 专业软件风格
    st.markdown('<div class="control-panel">', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    
    # 根据相似度搜索周期自动同步默认展示周期（避免周线搜索却默认看日线）
    period_label_map = {'daily': '日线', 'weekly': '周线', 'monthly': '月线'}
    inferred_period_label = '日线'
    if similar_patterns and len(similar_patterns) > 0:
        inferred_period_label = period_label_map.get(similar_patterns[0].get('period_type', 'daily'), '日线')

    sync_key = f"display_period_sync::{security_info.get('code', '')}"
    if 'pro_time_period' not in st.session_state:
        st.session_state['pro_time_period'] = inferred_period_label
    if st.session_state.get(sync_key) != inferred_period_label:
        st.session_state['pro_time_period'] = inferred_period_label
        st.session_state[sync_key] = inferred_period_label

    with col1:
        # 时间周期选择
        time_period = st.selectbox(
            "时间周期",
            ["日线", "周线", "月线"],
            key="pro_time_period"
        )
    
    with col2:
        # 显示数据统计
        if time_period == "日线":
            display_count = min(120, len(current_df))
            st.markdown(f"**显示**: {display_count}交易日")
        elif time_period == "周线":
            weekly_df = convert_to_weekly(current_df)
            display_count = min(52, len(weekly_df))
            st.markdown(f"**显示**: {display_count}周")
        else:  # 月线
            monthly_df = convert_to_monthly(current_df)
            display_count = min(24, len(monthly_df))
            st.markdown(f"**显示**: {display_count}月")
    
    with col3:
        # 展示模式选择（解决多股同列拥挤问题）
        display_mode = st.selectbox(
            "展示模式",
            ["多股同列(2x2)", "展开模式(大图)", "对齐对比(索引轴)"],
            index=0,  # 默认多股同列(2x2)
            key="display_mode"
        )
    
    with col4:
        # 均线设置
        _ma_settings = st.multiselect(
            "均线设置",
            ["MA5", "MA10", "MA20", "MA60", "MA120"],
            default=["MA5", "MA10", "MA20"],
            key="pro_ma"
        )

    # 技术指标参数配置
    with st.expander("⚙️ 技术指标参数配置"):
        col_indicator1, col_indicator2 = st.columns(2)
        
        with col_indicator1:
            st.markdown("**KDJ指标参数**")
            kdj_n = st.number_input("RSV周期(n)", min_value=3, max_value=30, value=9, key="kdj_n")
            kdj_m1 = st.number_input("K值平滑(m1)", min_value=1, max_value=10, value=3, key="kdj_m1")
            kdj_m2 = st.number_input("D值平滑(m2)", min_value=1, max_value=10, value=3, key="kdj_m2")
        
        with col_indicator2:
            st.markdown("**MACD指标参数**")
            macd_fast = st.number_input("快速EMA周期", min_value=5, max_value=50, value=12, key="macd_fast")
            macd_slow = st.number_input("慢速EMA周期", min_value=10, max_value=100, value=26, key="macd_slow")
            macd_signal = st.number_input("信号线周期", min_value=5, max_value=30, value=9, key="macd_signal")
        
        # 将参数保存到session_state供plot_kline使用
        st.session_state.kdj_params = {'n': kdj_n, 'm1': kdj_m1, 'm2': kdj_m2}
        st.session_state.macd_params = {'fast': macd_fast, 'slow': macd_slow, 'signal': macd_signal}

    # 指标固定：K线上叠加均线，下方依次成交量/MACD/KDJ
    show_indicators = True
    
    st.markdown('</div>', unsafe_allow_html=True)
    
    # 4格多股同列布局
    st.markdown("### 📊 多股同列分析")
    
    if similar_patterns and len(similar_patterns) >= 3:
        # 准备当前股票数据
        display_df_current, chart_title_current = prepare_chart_data(current_df, time_period)

        # 统一均线设置
        ma_windows = [int(x.replace("MA", "")) for x in _ma_settings] if _ma_settings else [5, 10, 20]

        # 检查是否需要自动降级从2x2到展开模式
        auto_degraded = False
        degrade_reason = ""
        
        def add_similarity_segment_highlight(fig, x0, x1, color_fill, label_text):
            """在索引轴K线图上高亮相似段落。"""
            if fig is None:
                return
            try:
                fig.add_vrect(
                    x0=max(0, x0) - 0.5,
                    x1=max(0, x1) + 0.5,
                    fillcolor=color_fill,
                    opacity=0.18,
                    line_width=0,
                    layer='below',
                    annotation_text=label_text,
                    annotation_position='top left'
                )
            except Exception:
                pass

        if display_mode == "多股同列(2x2)":
            # 检查拥挤条件
            # 1. 检查数据点数量是否过多（可能导致图表拥挤）
            data_point_threshold = 200
            current_data_points = len(display_df_current)
            
            # 2. 检查是否显示复杂指标（MACD/KDJ同时显示可能拥挤）
            complex_indicators = show_indicators and len(ma_windows) > 2
            
            # 3. 检查相似度是否过低（低相似度模式可能不值得拥挤显示）
            low_similarity_threshold = 0.3
            low_similarity_count = sum(1 for p in similar_patterns[:3] if p.get('similarity', 0) < low_similarity_threshold)
            
            # 判断是否需要自动降级
            if current_data_points > data_point_threshold:
                auto_degraded = True
                degrade_reason = f"数据点过多（{current_data_points} > {data_point_threshold}），展开模式更清晰"
            elif complex_indicators and low_similarity_count >= 2:
                auto_degraded = True
                degrade_reason = f"复杂指标+低相似度模式，展开模式更易观察"
            elif low_similarity_count >= 3:
                auto_degraded = True
                degrade_reason = f"所有相似模式相似度均低于{low_similarity_threshold:.0%}，展开模式更合适"
            
            # 如果自动降级，显示通知并切换到展开模式
            if auto_degraded:
                st.warning(f"⚠️ 自动切换到展开模式：{degrade_reason}")
                display_mode = "展开模式(大图)"
        
        if display_mode == "多股同列(2x2)":
            # 创建2x2网格布局
            col1, col2 = st.columns(2)
            col3, col4 = st.columns(2)
            grid_columns = [col1, col2, col3, col4]

            # 格子1: 当前股票K线
            with grid_columns[0]:
                st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                st.markdown(f'<div class="chart-title">📈 当前K线 - {security_info.get("code", "")}</div>', unsafe_allow_html=True)

                fig_current = plot_kline(
                    display_df_current,
                    f"{security_info.get('code', '')} {chart_title_current}",
                    future_dates=future_dates,
                    show_volume=True,
                    show_indicators=show_indicators,
                    ma_windows=ma_windows
                )

                fig_current = apply_professional_style(fig_current, "当前股票")

                # 在当前图中高亮“与历史模式对比的相似段落”（尾部窗口）
                current_match_len = 0
                if similar_patterns and similar_patterns[0].get('pattern_data') is not None:
                    pattern_for_len, _ = prepare_chart_data(similar_patterns[0]['pattern_data'].copy(), time_period)
                    current_match_len = len(pattern_for_len) if pattern_for_len is not None else 0
                if current_match_len > 0:
                    x1 = len(display_df_current) - 1
                    x0 = max(0, x1 - current_match_len + 1)
                    add_similarity_segment_highlight(
                        fig_current,
                        x0=x0,
                        x1=x1,
                        color_fill='rgba(255, 165, 0, 0.26)',
                        label_text='当前相似段'
                    )

                st.plotly_chart(fig_current, use_container_width=True, config={'displayModeBar': True})

                st.markdown('<div class="info-panel">', unsafe_allow_html=True)
                latest = display_df_current.iloc[-1]
                prev_close = display_df_current.iloc[-2]['close'] if len(display_df_current) > 1 else latest['close']
                change = latest['close'] - prev_close
                change_pct = (change / prev_close * 100) if prev_close != 0 else 0
                st.metric("最新价", f"{latest['close']:.2f}", f"{change:+.2f} ({change_pct:+.2f}%)")
                if 'volume' in latest:
                    st.markdown(f"**成交量**: {latest['volume']:,.0f}")
                amplitude = ((latest['high'] - latest['low']) / latest['close'] * 100) if latest['close'] != 0 else 0
                st.markdown(f"**振幅**: {amplitude:.1f}%")
                st.markdown('</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

            # 格子2-4: 相似度最高的3个历史模式
            # 高亮颜色用于标记相似片段
            palette = ['rgba(255,0,0,0.25)', 'rgba(0,128,0,0.25)', 'rgba(0,0,255,0.25)']
            for i in range(3):
                with grid_columns[i + 1]:
                    pattern = similar_patterns[i]
                    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                    similarity_percent = f"{pattern['similarity']:.1%}"
                    st.markdown(f"""
                    <div class="chart-title">
                        <span class="similarity-badge">相似度 {similarity_percent}</span>
                        TOP {i+1} 历史模式
                    </div>
                    """, unsafe_allow_html=True)
                    date_range = f"{pattern['start_date'].strftime('%Y-%m-%d')} 至 {pattern['end_date'].strftime('%Y-%m-%d')}"
                    st.markdown(f'<div style="color: #8b949e; font-size: 12px; margin-bottom: 10px;">{date_range}</div>', unsafe_allow_html=True)

                    # 索引轴模式下只保留真实有效交易数据，禁止补齐休市空档
                    pattern_data_raw = pattern['pattern_data'].copy() if pattern['pattern_data'] is not None else None
                    future_data_raw = pattern['future_data'].copy() if pattern['future_data'] is not None and not pattern['future_data'].empty else pattern['future_data']

                    display_df_history, _ = prepare_chart_data(pattern_data_raw, time_period)
                    fig_history = plot_kline(
                        display_df_history,
                        f"历史模式 {i+1}",
                        future_df=future_data_raw,
                        show_future_data=True,
                        show_volume=True,
                        show_indicators=show_indicators,
                        ma_windows=ma_windows
                    )
                    fig_history = apply_professional_style(fig_history, f"历史模式 {i+1}")
                    # 在历史图中高亮“相似段落”（pattern_data区间，不包含future_data）
                    history_segment_len = len(display_df_history)
                    if history_segment_len > 0:
                        add_similarity_segment_highlight(
                            fig_history,
                            x0=0,
                            x1=history_segment_len - 1,
                            color_fill=palette[i % len(palette)],
                            label_text=f'TOP{i+1} 相似段'
                        )
                    st.plotly_chart(fig_history, use_container_width=True, config={'displayModeBar': True})
                    st.markdown('</div>', unsafe_allow_html=True)
        elif display_mode == "展开模式(大图)":
            st.info("当前为展开模式：单图更大，便于细节观察")

            # 当前股票先展示
            st.markdown('### 📈 当前K线（大图）')
            fig_current = plot_kline(
                display_df_current,
                f"{security_info.get('code', '')} {chart_title_current}",
                future_dates=future_dates,
                show_volume=True,
                show_indicators=show_indicators,
                ma_windows=ma_windows
            )
            fig_current = apply_professional_style(fig_current, "当前股票")
            # 展开模式同样高亮当前相似段
            current_match_len = 0
            if similar_patterns and similar_patterns[0].get('pattern_data') is not None:
                pattern_for_len, _ = prepare_chart_data(similar_patterns[0]['pattern_data'].copy(), time_period)
                current_match_len = len(pattern_for_len) if pattern_for_len is not None else 0
            if current_match_len > 0:
                x1 = len(display_df_current) - 1
                x0 = max(0, x1 - current_match_len + 1)
                add_similarity_segment_highlight(
                    fig_current,
                    x0=x0,
                    x1=x1,
                    color_fill='rgba(255, 165, 0, 0.26)',
                    label_text='当前相似段'
                )
            fig_current.update_layout(height=1100)
            st.plotly_chart(fig_current, use_container_width=True, config={'displayModeBar': True})

            # 相似模式按大图顺序展示
            for i in range(3):
                pattern = similar_patterns[i]
                st.markdown(f"### TOP {i+1} 历史模式（相似度 {pattern['similarity']:.1%}）")
                # 索引轴模式下只保留真实有效交易数据，禁止补齐休市空档
                pattern_data_raw = pattern['pattern_data'].copy() if pattern['pattern_data'] is not None else None
                future_data_raw = pattern['future_data'].copy() if pattern['future_data'] is not None and not pattern['future_data'].empty else pattern['future_data']

                display_df_history, _ = prepare_chart_data(pattern_data_raw, time_period)
                fig_history = plot_kline(
                    display_df_history,
                    f"历史模式 {i+1}",
                    future_df=future_data_raw,
                    show_future_data=True,
                    show_volume=True,
                    show_indicators=show_indicators,
                    ma_windows=ma_windows
                )
                fig_history = apply_professional_style(fig_history, f"历史模式 {i+1}")
                history_segment_len = len(display_df_history)
                if history_segment_len > 0:
                    palette = ['rgba(255,0,0,0.25)', 'rgba(0,128,0,0.25)', 'rgba(0,0,255,0.25)']
                    add_similarity_segment_highlight(
                        fig_history,
                        x0=0,
                        x1=history_segment_len - 1,
                        color_fill=palette[i % len(palette)],
                        label_text=f'TOP{i+1} 相似段'
                    )
                fig_history.update_layout(height=1100)
                st.plotly_chart(fig_history, use_container_width=True, config={'displayModeBar': True})
        
        elif display_mode == "对齐对比(索引轴)":
            st.info("当前为对齐对比模式：使用索引轴对齐，消除节假日空隙，支持多周期统一渲染")
            
            # 准备对齐对比数据
            aligned_data = prepare_aligned_similarity_data(
                current_df=display_df_current,
                similar_patterns=similar_patterns[:3],  # 只取前3个最相似的
                top_n=3
            )
            
            if aligned_data:
                # 绘制对齐对比图
                fig_aligned = plot_multi_stock_comparison(
                    aligned_data['aligned_stocks'],
                    titles=aligned_data.get('titles'),
                    show_volume=True,
                    show_indicators=show_indicators,
                    ma_windows=ma_windows
                )
                
                # 应用专业样式
                fig_aligned = apply_professional_style(fig_aligned, "对齐对比(索引轴)")
                fig_aligned.update_layout(height=900)
                
                # 显示图表
                st.plotly_chart(fig_aligned, use_container_width=True, config={'displayModeBar': True})
                
                # 显示对齐信息
                with st.expander("📋 对齐信息详情"):
                    st.markdown("**索引轴对齐原理：**")
                    st.markdown("""
                    1. **时间轴 → 索引轴转换**：将基于日期的K线数据转换为基于索引的序列
                    2. **多股对齐**：所有股票序列强制对齐到同一虚拟索引 [0, 1, 2, ... n]
                    3. **多周期适配**：日线、周线、月线使用相同的索引轴逻辑
                    4. **坐标轴标签**：索引动态映射回日期字符串显示
                    """)
                    
                    # 显示每个序列的详细信息
                    for i, stock_data in enumerate(aligned_data['aligned_stocks']):
                        stock_name = stock_data.get('name', f'序列{i+1}')
                        data_length = len(stock_data.get('data', pd.DataFrame()))
                        aligned_length = len(stock_data.get('aligned_indices', []))
                        is_reference = stock_data.get('is_reference', False)
                        
                        st.markdown(f"**{stock_name}**")
                        st.markdown(f"- 数据点数: {data_length}")
                        st.markdown(f"- 对齐后索引数: {aligned_length}")
                        st.markdown(f"- 是否参考序列: {'是' if is_reference else '否'}")
                        if i < len(aligned_data['aligned_stocks']) - 1:
                            st.markdown("---")
            else:
                st.warning("无法准备对齐对比数据，请检查数据格式")
    else:
        st.warning("未找到足够的相似模式进行多股同列分析")
        # 回退到单图显示
        display_df, chart_title = prepare_chart_data(current_df, time_period)
        ma_windows = [int(x.replace("MA", "")) for x in _ma_settings] if _ma_settings else [5, 10, 20]
        fig = plot_kline(display_df, chart_title, future_dates=future_dates, show_volume=True, show_indicators=show_indicators, ma_windows=ma_windows)
        fig = apply_professional_style(fig, "当前股票")
        st.plotly_chart(fig, use_container_width=True)
    
    # 下方显示趋势分析和持仓收益（保持原有功能）
    if similar_patterns:
        # 显示趋势分析
        display_trend_analysis(similar_patterns)
        
        # 计算和显示持仓收益分析
        holding_stats = analyze_holding_returns(similar_patterns)
        display_holding_period_analysis(holding_stats)
        
        return holding_stats
    
    return None

def prepare_chart_data(df, time_period):
    """根据时间周期准备图表数据，仅保留真实有效交易数据"""
    if time_period == "日线":
        # 日线不再补齐休市日期，避免索引轴下再次出现空白区间
        display_df = df.copy() if df is not None else df
        chart_title = "日K线"
        if len(display_df) > 120:
            display_df = display_df.tail(120)
    elif time_period == "周线":
        display_df = convert_to_weekly(df)
        chart_title = "周K线"
        if len(display_df) > 52:
            display_df = display_df.tail(52)
    else:  # 月线
        display_df = convert_to_monthly(df)
        chart_title = "月K线"
        if len(display_df) > 24:
            display_df = display_df.tail(24)
    
    return display_df, chart_title

def apply_professional_style(fig, title):
    """应用简洁专业的股票图表样式 - 保留A股红涨绿跌语义"""
    # A股标准颜色：红涨绿跌（必须保留）
    colors = ['#ff4d4d', '#26a69a']  # 红涨绿跌
    
    # 简化布局：更轻量的主题，减少视觉重量
    fig.update_layout(
        plot_bgcolor='#ffffff',
        paper_bgcolor='#ffffff',
        font={'color': '#333333', 'size': 11},
        title={
            'text': title,
            'y': 0.95,
            'x': 0.5,
            'xanchor': 'center',
            'yanchor': 'top',
            'font': {'size': 13, 'color': '#1a237e'}
        },
        xaxis={
            'gridcolor': '#e0e0e0',
            'linecolor': '#cccccc',
            'showgrid': True,
            'tickfont': {'color': '#555555'}
        },
        yaxis={
            'gridcolor': '#e0e0e0',
            'linecolor': '#cccccc',
            'showgrid': True,
            'tickfont': {'color': '#555555'}
        },
        showlegend=True,
        legend={
            'yanchor': 'top',
            'y': 0.99,
            'xanchor': 'left',
            'x': 0.01,
            'bgcolor': 'rgba(255, 255, 255, 0.9)',
            'bordercolor': '#e0e0e0',
            'borderwidth': 1,
            'font': {'color': '#333333'}
        },
        margin=dict(l=40, r=40, t=60, b=40)
    )
    
    # 更新K线颜色（必须保留A股语义）
    if len(fig.data) > 0 and fig.data[0].type == 'candlestick':
        fig.data[0].increasing.line.color = colors[0]
        fig.data[0].decreasing.line.color = colors[1]
        fig.data[0].increasing.fillcolor = colors[0]
        fig.data[0].decreasing.fillcolor = colors[1]
    
    # 隐藏范围滑块
    fig.update_xaxes(rangeslider_visible=False)
    
    return fig

def display_rag_qa(security, current_df, similar_patterns, holding_stats):
    """
    显示支持多轮对话的智能问答界面
    """
    st.markdown("""
### 智能问答助手
<div style='background-color: #eef6ff; padding: 12px; border-radius: 8px; margin-bottom: 20px; font-size: 0.9em;'>
    使用指南
    <ul style='margin-top: 8px; margin-bottom: 8px;'>
        <li>这是一个支持多轮对话的智能助手，您可以围绕一个话题深入交流</li>
        <li>助手会记住对话内容，您可以基于之前的回答继续提问</li>
        <li>随时可以要求助手解释某个观点，或者提供更详细的分析</li>
        <li>如果分析不够清晰，请告诉助手“具体说明一下吗？”</li>
    </ul>
</div>""", unsafe_allow_html=True)

    # 创建文本块用于检索
    chunks = create_text_chunks(security, current_df, similar_patterns, holding_stats)
    base_key = f"{security['code']}_{security['type']}"
    
    # 初始化会话状态
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    
    # 初始化输入框计数器
    if 'input_key_counter' not in st.session_state:
        st.session_state.input_key_counter = 0
    
    # 显示历史对话
    for i, (user_msg, assistant_msg) in enumerate(st.session_state.chat_history):
        with st.container():
            # 用户消息
            st.markdown(f"""
            <div style='background-color: #f0f2f6; padding: 10px; border-radius: 8px; margin-bottom: 10px;'>
                <span style='color: #666;'>您：</span><br>
                {user_msg}
            </div>
            """, unsafe_allow_html=True)
            
            # 助手回复
            st.markdown(f"""
            <div style='background-color: #e8f4f9; padding: 10px; border-radius: 8px; margin-bottom: 20px;'>
                <span style='color: #666;'>助手：</span><br>
                {assistant_msg}
            </div>
            """, unsafe_allow_html=True)

            # 在最后一条对话后显示免责声明
            if user_msg == st.session_state.chat_history[-1][0]:
                st.markdown("""
            <div class="statement">
            ⚠️ <strong>免责声明：</strong>以上分析仅供参考，不构成投资建议。投资有风险，入市需谨慎。
            </div>
            """, unsafe_allow_html=True)
    
    # 用户输入新问题
    input_key = f'rag_question_{base_key}'
    user_question = st.text_input(
        f"请输入您关于 {security['name']}（{security['code']}）的问题：", 
        key=f"{input_key}_{st.session_state.input_key_counter}",
        help="您可以：\n1. 询问基本信息、最新行情、历史表现、相似K线分析等内容\n2. 基于助手的回答继续追问，比如'为什么会这样预测？'\n3. 寻求更详细的解释，如'能具体解释一下这个原因吗？'\n4. 要求展开某个观点，如'刚才说到xxx，能详细分析一下吗？'"
    )
    
    if user_question:
        with st.spinner('正在根据相关数据块思考答案...'):
            # 检索相关文本块
            relevant_chunks = retrieve_relevant_chunks(user_question, chunks, top_k=10)
            
            # 显示检索到的相关文本块
            chunks_content = ""
            for _, chunk_text in relevant_chunks:
                chunks_content += f'<p>{chunk_text}</p>'

            st.markdown(f'<details class="details" style="margin-top: -6px;">'
                    f'<summary class="details-summary">相关数据块（基于 RAG 结果）</summary>'
                    f'<div class="details-body details-body-related-chunks">'
                    f'{chunks_content}'
                    f'</div>'
                    f'</details>', unsafe_allow_html=True)
            
            # 构建prompt并调用API，加入对话历史
            prompt = get_analysis_prompt(
                user_question, 
                relevant_chunks, 
                st.session_state.chat_history
            )
            response = call_llm_api(prompt)
            
            if response:
                # 保存新的对话记录
                st.session_state.chat_history.append((user_question, response))
                
                # 增加计数器以更新输入框的key
                st.session_state.input_key_counter += 1

                # 刷新页面
                st.rerun()
            else:
                st.error("抱歉，获取回答失败，请稍后重试。")

def main():
    """
    主函数：实现金融数据分析系统的整体功能流程
    """

    # 简洁专业的CSS样式 - 轻量化设计
    st.markdown("""
    <style>
    /* 简洁专业主题 */
    .professional-header {
        background: linear-gradient(135deg, #1a237e 0%, #283593 100%);
        color: white;
        padding: 12px 16px;
        border-radius: 6px;
        margin-bottom: 16px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.08);
    }
    .chart-container {
        background-color: #ffffff;
        border-radius: 6px;
        padding: 12px;
        margin-bottom: 12px;
        border: 1px solid #e0e0e0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .chart-title {
        color: #1a237e;
        font-size: 14px;
        font-weight: 600;
        margin-bottom: 8px;
        padding-bottom: 6px;
        border-bottom: 1px solid #e0e0e0;
    }
    .info-panel {
        background-color: #f8f9fa;
        border-radius: 6px;
        padding: 10px;
        margin-bottom: 8px;
        border: 1px solid #e0e0e0;
    }
    .similarity-badge {
        display: inline-block;
        background: linear-gradient(135deg, #ff6b6b 0%, #ee5a52 100%);
        color: white;
        padding: 3px 10px;
        border-radius: 16px;
        font-size: 11px;
        font-weight: 600;
        margin-right: 6px;
    }
    .time-period-selector {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 4px;
        padding: 6px 10px;
        color: #333333;
    }
    .grid-chart {
        background-color: #ffffff;
        border-radius: 6px;
        padding: 8px;
        border: 1px solid #e0e0e0;
        height: 380px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .stock-info-card {
        background: linear-gradient(135deg, #1a237e 0%, #283593 100%);
        color: white;
        padding: 10px;
        border-radius: 6px;
        margin-bottom: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.08);
    }
    .control-panel {
        background-color: #f8f9fa;
        border-radius: 6px;
        padding: 12px;
        margin-bottom: 12px;
        border: 1px solid #e0e0e0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    </style>
    """, unsafe_allow_html=True)

    # 优化标题和导航
    st.markdown("""
    <div style='text-align: center; margin-bottom: 1.5em;'>
        <h1 style='color: #1f449c; margin-bottom: 0.2em;'>A股 K线智能分析系统</h1>
        <p style='color: #666; font-size: 1.1em;'>专业级A股技术分析工具 | 多周期K线 | 智能相似度匹配</p>
    </div>
    """, unsafe_allow_html=True)
    
    # 添加导航标签页
    tab1, tab2, tab3 = st.tabs(["证券搜索", "技术分析", "智能问答"])
    
    with tab1:
        # 搜索证券 - 优化布局
        st.markdown("""
        <div style='background-color: #f8f9fa; padding: 20px; border-radius: 10px; margin-bottom: 20px;'>
            <h3 style='color: #1f449c; margin-top: 0;'>快速搜索证券</h3>
            <p style='color: #666;'>输入股票代码、名称或拼音首字母进行搜索</p>
        </div>
        """, unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns([1, 3, 1])
        with col2:
            query = st.text_input(
                '输入股票代码或名称搜索', 
                '', 
                key='security_search', 
                placeholder='例如：上证指数、平安银行、600000、MS'
            )
        
        if query:
            with st.spinner('正在搜索证券...'):
                results = search_securities(query)
            
            if results:
                st.success(f'找到 {len(results)} 个匹配结果')
                
                # 显示搜索结果卡片
                for i, result in enumerate(results):
                    with st.expander(f"**{i+1}. {result['name']} ({result['code']})** - {result['type'].upper()}", expanded=(i==0)):
                        col1, col2, col3 = st.columns(3)
                        
                        with col1:
                            st.metric("代码", result['code'])
                        
                        with col2:
                            st.metric("名称", result['name'])
                        
                        with col3:
                            st.metric("类型", result['type'].upper())
                        
                        # 显示交易所信息
                        col4, col5 = st.columns(2)
                        with col4:
                            if result.get('exchange'):
                                st.metric("交易所", result['exchange'])
                            else:
                                st.metric("交易所", "未知")
                        
                        # 添加分析按钮 - 使用唯一key，包含索引防止重复
                        if st.button(f"开始分析 {result['name']}", key=f"analyze_{i}_{result['code']}_{result['type']}"):
                            st.session_state.selected_security = result
                            st.session_state.active_tab = "技术分析"
                            st.rerun()
                
                # 如果没有选择任何证券，使用第一个结果
                if 'selected_security' not in st.session_state:
                    st.session_state.selected_security = results[0]
            else:
                st.warning("未找到匹配的证券，请尝试其他关键词")
    
    with tab2:
        if 'selected_security' not in st.session_state:
            st.info("请先在「证券搜索」标签页选择要分析的证券")
        else:
            result = st.session_state.selected_security
            
            # 显示证券标题
            st.markdown(f"""
            <div style='background-color: #e8f4fd; padding: 15px; border-radius: 10px; margin-bottom: 20px;'>
                <h3 style='color: #1f449c; margin: 0;'>
                    正在分析：{result['name']} ({result['code']}) - {result['type'].upper()}
                </h3>
            </div>
            """, unsafe_allow_html=True)
            
            # 相似度分析周期选择器
            col1, col2 = st.columns([1, 3])
            with col1:
                similarity_period = st.selectbox(
                    "相似度分析周期",
                    ["日线", "周线", "月线"],
                    index=0,
                    help="选择用于相似度比较的时间周期"
                )
            with col2:
                compare_scope = st.selectbox(
                    "相似度对比范围",
                    ["同一只股票历史对比", "不同股票共同参与对比"],
                    index=0,
                    help="选择仅在当前股票历史中查找，或跨股票联合查找"
                )

            # 可调权重面板（形态/趋势/量能 + MACD/KDJ优先级）
            default_similarity_weights = {
                'trend': 0,
                'volume': 0,
                'pattern': 100,
                'macd': 0,
                'kdj': 0,
                'regime': 0,
            }
            if 'similarity_weights' not in st.session_state:
                st.session_state.similarity_weights = default_similarity_weights.copy()

            with st.expander("🎛️ 可调权重面板（趋势/量能/形态/MACD/KDJ）"):
                wcol1, wcol2, wcol3 = st.columns(3)
                with wcol1:
                    w_trend = st.slider("趋势权重", 0, 100, int(st.session_state.similarity_weights.get('trend', 0)), 1)
                    w_volume = st.slider("量能权重", 0, 100, int(st.session_state.similarity_weights.get('volume', 0)), 1)
                with wcol2:
                    w_pattern = st.slider("K线形态权重", 0, 100, int(st.session_state.similarity_weights.get('pattern', 100)), 1)
                    w_regime = st.slider("市场状态权重", 0, 100, int(st.session_state.similarity_weights.get('regime', 0)), 1)
                with wcol3:
                    w_macd = st.slider("MACD权重", 0, 100, int(st.session_state.similarity_weights.get('macd', 0)), 1)
                    w_kdj = st.slider("KDJ权重", 0, 100, int(st.session_state.similarity_weights.get('kdj', 0)), 1)

                st.session_state.similarity_weights = {
                    'trend': w_trend,
                    'volume': w_volume,
                    'pattern': w_pattern,
                    'macd': w_macd,
                    'kdj': w_kdj,
                    'regime': w_regime,
                }

                total_weight = sum(st.session_state.similarity_weights.values())
                st.caption(f"当前权重总和: {total_weight}（系统会自动归一化）")
            
            # 周/月K线缓存控制
            if similarity_period in ["周线", "月线"]:
                col_cache1, col_cache2 = st.columns([1, 3])
                with col_cache1:
                    # 显示缓存目录状态
                    cache_dir = "./kline_cache"
                    if os.path.exists(cache_dir):
                        cache_files = [f for f in os.listdir(cache_dir) if f.endswith('.pkl')]
                        st.caption(f"📦 缓存: {len(cache_files)} 个文件")
                    else:
                        st.caption("📦 缓存: 无")
                
                with col_cache2:
                    if st.button(
                        "🔄 强制更新周/月K线缓存",
                        help="重新计算周/月K线数据并更新缓存（首次计算较慢）"
                    ):
                        st.session_state.force_cache_update = True
                        st.rerun()

            # 缓存状态栏
            cache_status_weekly = _get_period_cache_status('weekly', result['code'])
            cache_status_monthly = _get_period_cache_status('monthly', result['code'])
            status_col1, status_col2 = st.columns(2)
            with status_col1:
                st.markdown("**🗂️ 周K缓存状态**")
                st.caption(
                    f"存在: {'是' if cache_status_weekly['exists'] else '否'} | "
                    f"更新时间: {cache_status_weekly['timestamp']} | "
                    f"行数: {cache_status_weekly['rows']}"
                )
            with status_col2:
                st.markdown("**🗂️ 月K缓存状态**")
                st.caption(
                    f"存在: {'是' if cache_status_monthly['exists'] else '否'} | "
                    f"更新时间: {cache_status_monthly['timestamp']} | "
                    f"行数: {cache_status_monthly['rows']}"
                )
            
            # 对比范围控制
            col3, col4 = st.columns([1, 3])
            with col3:
                search_across_market = compare_scope == "不同股票共同参与对比"
                st.caption(f"当前范围: {'跨股票' if search_across_market else '仅本股票历史'}")

            with col4:
                if search_across_market:
                    max_candidate_codes = st.slider(
                        "最大候选标的数",
                        min_value=10,
                        max_value=200,
                        value=80,
                        step=10,
                        help="限制跨股票搜索候选数，平衡速度与覆盖面"
                    )
                else:
                    max_candidate_codes = 80
            
            # 获取市场数据
            try:
                with st.spinner('正在加载市场数据...'):
                    # 获取5年数据，确保有足够的历史数据用于周线/月线计算
                    market_data = get_market_data(result['code'], result['type'], days=365*5)
                
                if market_data is None or market_data.empty:
                    st.error(f"无法获取 {result['name']} 的市场数据。请检查：")
                    st.info("1. 确保TDX数据目录配置正确")
                    st.info("2. 检查证券代码是否正确")
                    st.info("3. 尝试重新搜索并选择证券")
                    return
            
            except Exception as e:
                st.error(f"加载市场数据时出错: {str(e)}")
                st.info("请尝试重新搜索并选择证券，或检查网络连接")
                return
            
            if market_data is not None:
                # 使用完整数据进行K线分析（显示逻辑在display_market_analysis中处理）
                # 生成未来7个交易日的日期
                last_date = market_data['trade_date'].iloc[-1]
                future_dates = pd.date_range(
                    start=last_date + pd.Timedelta(days=1),
                    periods=7,
                    freq='B'  # 'B' 表示工作日
                )
                
                # 寻找相似K线形态
                try:
                    # 根据选择的周期类型设置参数
                    period_type_map = {
                        "日线": "daily",
                        "周线": "weekly", 
                        "月线": "monthly"
                    }
                    period_type = period_type_map.get(similarity_period, "daily")
                    
                    # 检查是否需要强制更新缓存
                    force_update = st.session_state.get('force_cache_update', False)
                    
                    with st.spinner(f'正在分析{similarity_period}相似K线形态...'):
                        similar_patterns = find_similar_patterns(
                            market_data, 
                            window_size=30, 
                            top_n=10, 
                            use_optimized=True,
                            period_type=period_type,
                            search_across_market=search_across_market,
                            max_candidate_codes=max_candidate_codes,
                            current_code=result['code'],
                            security_type=result['type'],
                            force_update=force_update,
                            similarity_weights=st.session_state.get('similarity_weights', default_similarity_weights)
                        )
                    
                    # 重置强制更新标志
                    if force_update:
                        st.session_state.force_cache_update = False
                    
                    if not similar_patterns:
                        st.warning("未找到足够相似的K线形态进行分析")
                        st.info("这可能是因为：")
                        st.info("1. 该证券的历史数据较短")
                        st.info("2. 当前K线形态比较独特")
                        st.info("3. 可以尝试分析其他时间周期")
                
                except Exception as e:
                    st.error(f"分析相似K线形态时出错: {str(e)}")
                    st.info("请尝试重新搜索并选择证券")
                    similar_patterns = []
                
                # 显示市场分析（专业4格布局）
                try:
                    holding_stats = display_market_analysis(market_data, similar_patterns, future_dates, security_info=result)
                except Exception as e:
                    st.error(f"显示市场分析时出错: {str(e)}")
                    st.info("请尝试重新加载页面")
                    holding_stats = None
                
                # 在技术分析标签页显示智能问答按钮
                if holding_stats is not None:
                    if st.button("开启智能问答", key="start_qa"):
                        st.session_state.show_qa = True
                        st.rerun()
            else:
                st.warning("暂无K线数据")
    
    with tab3:
        if 'selected_security' not in st.session_state:
            st.info("请先在「证券搜索」标签页选择要分析的证券")
        elif 'show_qa' not in st.session_state or not st.session_state.show_qa:
            st.info("请在「技术分析」标签页点击「开启智能问答」按钮开始对话")
        else:
            result = st.session_state.selected_security
            
            # 重新获取数据用于问答
            with st.spinner('正在准备问答数据...'):
                market_data = get_market_data(result['code'], result['type'], days=365*5)
                if market_data is not None:
                    similar_patterns = find_similar_patterns(market_data, window_size=30, top_n=10, use_optimized=True, period_type='daily')
                    holding_stats = analyze_holding_returns(similar_patterns) if similar_patterns else None
                    
                    # 显示智能问答界面
                    display_rag_qa(result, market_data.tail(252 * 10).copy(), similar_patterns, holding_stats)
                else:
                    st.warning("无法获取市场数据，无法进行问答")
        
        # 添加页脚
        st.markdown("---")
        st.markdown("""
        <div style='text-align: center; color: #666; font-size: 0.9em; margin-top: 2em;'>
            <p><strong>A股 K线智能分析系统</strong> | 版本 2.0 | 支持日线/周线/月线多周期分析</p>
            <p>功能特点：多周期K线显示 | 智能相似度匹配 | 技术指标分析 | AI智能问答</p>
            <p style='font-size: 0.8em;'>数据来源：TDX | 分析算法：优化多维度相似度匹配 | 更新时间：2025年</p>
        </div>
        """, unsafe_allow_html=True)

def fill_trading_dates(df):
    """
    填充交易日空缺，处理节假日导致的K线空缺
    
    参数说明：
    - df: 原始K线数据，包含trade_date, open, high, low, close, volume等列
    
    返回：
    - 填充后的DataFrame，确保交易日连续
    """
    if df is None or df.empty:
        return df
    
    # 确保日期是datetime类型
    df = df.copy()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    
    # 按日期排序
    df = df.sort_values('trade_date', ascending=True)
    
    # 创建完整的交易日范围（从最早到最晚日期）
    start_date = df['trade_date'].min()
    end_date = df['trade_date'].max()
    
    # 生成所有可能的日期（包括周末）
    all_dates = pd.date_range(start=start_date, end=end_date, freq='D')
    
    # 创建完整日期的DataFrame
    full_df = pd.DataFrame({'trade_date': all_dates})
    
    # 合并原始数据
    full_df = pd.merge(full_df, df, on='trade_date', how='left')
    
    # 标记是否为交易日（原始数据中有值的日期）
    full_df['is_trading_day'] = full_df['close'].notna()
    
    # 向前填充缺失的K线数据（使用前一个交易日的收盘价）
    # 对于开盘、最高、最低价，使用前一个交易日的收盘价
    full_df['close'] = full_df['close'].ffill()
    
    # 填充其他价格数据
    for col in ['open', 'high', 'low']:
        full_df[col] = full_df[col].fillna(full_df['close'])
    
    # 填充成交量（非交易日成交量为0）
    if 'volume' in full_df.columns:
        full_df['volume'] = full_df['volume'].fillna(0)
    
    # 只保留原始交易日或填充后的连续日期
    # 这里我们保留所有日期以实现连续显示
    return full_df

def fill_missing_periods(df, period_type='daily'):
    """
    填充缺失的周期数据，确保K线连续显示
    
    参数说明：
    - df: 原始K线数据，包含trade_date, open, high, low, close, volume等列
    - period_type: 周期类型，'daily'（日线）, 'weekly'（周线）, 'monthly'（月线）
    
    返回：
    - 填充后的DataFrame，确保周期连续
    """
    if df is None or df.empty:
        return df
    
    df = df.copy()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date', ascending=True)
    
    start_date = df['trade_date'].min()
    end_date = df['trade_date'].max()
    
    # 根据周期类型生成完整的日期范围
    if period_type == 'daily':
        freq = 'D'
    elif period_type == 'weekly':
        # 周线：每周一作为周的开始
        freq = 'W-MON'
    elif period_type == 'monthly':
        # 月线：每月第一天
        freq = 'MS'  # Month Start
    else:
        freq = 'D'
    
    all_dates = pd.date_range(start=start_date, end=end_date, freq=freq)
    full_df = pd.DataFrame({'trade_date': all_dates})
    full_df = pd.merge(full_df, df, on='trade_date', how='left')
    
    # 标记是否为有效周期
    full_df['is_valid_period'] = full_df['close'].notna()
    
    # 向前填充缺失的K线数据
    full_df['close'] = full_df['close'].ffill()
    
    # 填充其他价格数据
    for col in ['open', 'high', 'low']:
        full_df[col] = full_df[col].fillna(full_df['close'])
    
    # 填充成交量（非有效周期成交量为0）
    if 'volume' in full_df.columns:
        full_df['volume'] = full_df['volume'].fillna(0)
    
    return full_df

def prepare_kline_data(df, period_type='daily', fill_holidays=False):
    """
    准备K线图数据，根据参数决定是否填充节假日
    
    参数说明：
    - df: 原始K线数据
    - period_type: 周期类型，'daily'（日线）, 'weekly'（周线）, 'monthly'（月线）
    - fill_holidays: 是否填充节假日数据
    
    返回：
    - 处理后的DataFrame
    - 是否填充了节假日
    """
    if df is None or df.empty:
        return df, False
    
    df = df.copy()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date', ascending=True)
    
    if not fill_holidays:
        # 不填充节假日，直接返回原始数据
        df['is_valid_period'] = True  # 所有数据都是有效的
        return df, False
    
    # 填充节假日（原有逻辑）
    start_date = df['trade_date'].min()
    end_date = df['trade_date'].max()
    
    # 根据周期类型生成完整的日期范围
    if period_type == 'daily':
        freq = 'D'
    elif period_type == 'weekly':
        freq = 'W-MON'
    elif period_type == 'monthly':
        freq = 'MS'
    else:
        freq = 'D'
    
    all_dates = pd.date_range(start=start_date, end=end_date, freq=freq)
    full_df = pd.DataFrame({'trade_date': all_dates})
    full_df = pd.merge(full_df, df, on='trade_date', how='left')
    
    # 标记是否为有效周期
    full_df['is_valid_period'] = full_df['close'].notna()
    
    # 向前填充缺失的K线数据
    full_df['close'] = full_df['close'].ffill()
    
    # 填充其他价格数据
    for col in ['open', 'high', 'low']:
        full_df[col] = full_df[col].fillna(full_df['close'])
    
    # 填充成交量（非有效周期成交量为0）
    if 'volume' in full_df.columns:
        full_df['volume'] = full_df['volume'].fillna(0)
    
    return full_df, True

def calculate_macd(df, fast_period=12, slow_period=26, signal_period=9):
    """
    计算MACD指标
    
    参数说明：
    - df: 包含收盘价的数据框
    - fast_period: 快速EMA周期（默认12）
    - slow_period: 慢速EMA周期（默认26）
    - signal_period: 信号线周期（默认9）
    
    返回：
    - 包含DIF、DEA、MACD柱的数据框
    """
    if df is None or df.empty or 'close' not in df.columns:
        return None
    
    df = df.copy()
    
    # 计算EMA
    df['EMA_fast'] = df['close'].ewm(span=fast_period, adjust=False).mean()
    df['EMA_slow'] = df['close'].ewm(span=slow_period, adjust=False).mean()
    
    # 计算DIF（差离值）
    df['DIF'] = df['EMA_fast'] - df['EMA_slow']
    
    # 计算DEA（信号线）
    df['DEA'] = df['DIF'].ewm(span=signal_period, adjust=False).mean()
    
    # 计算MACD柱（DIF-DEA）
    df['MACD'] = df['DIF'] - df['DEA']
    
    return df[['DIF', 'DEA', 'MACD']]

def calculate_kdj(df, n=9, m1=3, m2=3):
    """
    计算KDJ指标
    
    参数说明：
    - df: 包含最高价、最低价、收盘价的数据框
    - n: RSV计算周期（默认9）
    - m1: K值平滑周期（默认3）
    - m2: D值平滑周期（默认3）
    
    返回：
    - 包含K、D、J值的数据框
    """
    if df is None or df.empty:
        return None
    
    required_cols = ['high', 'low', 'close']
    for col in required_cols:
        if col not in df.columns:
            return None
    
    df = df.copy()
    
    # 计算最近n日内的最低价和最高价
    df['lowest_low'] = df['low'].rolling(window=n, min_periods=1).min()
    df['highest_high'] = df['high'].rolling(window=n, min_periods=1).max()
    
    # 计算RSV（未成熟随机值）
    df['RSV'] = 100 * (df['close'] - df['lowest_low']) / (df['highest_high'] - df['lowest_low'])
    df['RSV'] = df['RSV'].fillna(50)  # 处理除零情况
    
    # 计算K值（RSV的m1日移动平均）
    df['K'] = df['RSV'].ewm(alpha=1/m1, adjust=False).mean()
    
    # 计算D值（K值的m2日移动平均）
    df['D'] = df['K'].ewm(alpha=1/m2, adjust=False).mean()
    
    # 计算J值（3*K-2*D）
    df['J'] = 3 * df['K'] - 2 * df['D']
    
    return df[['K', 'D', 'J']]


def convert_to_index_based(df, include_dates=True):
    """
    将带日期的DataFrame转换为索引序列
    
    参数:
    - df: 包含'trade_date'列的DataFrame
    - include_dates: 是否包含日期映射信息
    
    返回:
    - dict: 包含索引序列和日期映射的字典
    """
    if df is None or df.empty:
        return None
    
    # 创建索引序列: 0, 1, 2, ...
    indices = list(range(len(df)))
    
    result = {
        'indices': indices,
        'data': df.copy(),
        'length': len(df)
    }
    
    if include_dates and 'trade_date' in df.columns:
        # 创建日期到索引的映射
        date_to_index = {}
        index_to_date = {}
        date_strings = []
        
        for i, date in enumerate(df['trade_date']):
            date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
            date_to_index[date_str] = i
            index_to_date[i] = date_str
            date_strings.append(date_str)
        
        result.update({
            'date_to_index': date_to_index,
            'index_to_date': index_to_date,
            'date_strings': date_strings
        })
    
    return result


def align_multiple_stocks(stock_data_list, method='index', reference_index=0):
    """
    对齐多个股票序列进行对比
    
    参数:
    - stock_data_list: 多个股票数据的列表，每个元素是DataFrame或convert_to_index_based的返回结果
    - method: 对齐方法
        'index': 相对索引对齐（默认）- 忽略日期，按顺序排列
        'date': 日期对齐（需要所有股票有相同的交易日历）
    - reference_index: 参考股票索引，其他股票将与此股票对齐
    
    返回:
    - dict: 包含对齐后的数据和配置信息
    """
    if not stock_data_list:
        return None
    
    # 确保参考索引有效
    if reference_index >= len(stock_data_list):
        reference_index = 0
    
    if method == 'index':
        # 相对索引对齐：每个股票从0开始索引，忽略日期
        aligned_results = []
        reference_data = None
        
        for i, stock_data in enumerate(stock_data_list):
            if isinstance(stock_data, dict) and 'indices' in stock_data:
                # 已经是转换后的格式
                aligned = stock_data.copy()
            else:
                # 原始DataFrame，转换为索引格式
                aligned = convert_to_index_based(stock_data, include_dates=True)
            
            if aligned:
                # 确保索引从0开始
                aligned['aligned_indices'] = list(range(len(aligned['indices'])))
                aligned['stock_index'] = i
                
                if i == reference_index:
                    reference_data = aligned
                    aligned['is_reference'] = True
                else:
                    aligned['is_reference'] = False
                
                aligned_results.append(aligned)
        
        if not aligned_results:
            return None
        
        # 计算对齐配置
        max_length = max(len(result['indices']) for result in aligned_results)
        min_length = min(len(result['indices']) for result in aligned_results)
        
        return {
            'aligned_stocks': aligned_results,
            'reference_index': reference_index,
            'max_length': max_length,
            'min_length': min_length,
            'method': method,
            'alignment_info': f"相对索引对齐，共{aligned_results}个股票，长度范围{min_length}-{max_length}"
        }
    
    elif method == 'date':
        # 日期对齐：需要所有股票有相同的交易日历
        # 这里实现日期对齐逻辑（如果需要）
        pass
    
    return None


def plot_multi_stock_comparison(stock_data_list, titles=None, method='index', 
                               show_volume=True, show_indicators=False, ma_windows=None):
    """
    绘制多股对比图（索引轴对齐）
    
    参数:
    - stock_data_list: 多个股票数据的列表，每个元素是DataFrame
    - titles: 每个股票的标题列表（可选）
    - method: 对齐方法 'index'（索引对齐）或 'date'（日期对齐）
    - show_volume: 是否显示成交量
    - show_indicators: 是否显示技术指标
    - ma_windows: 均线窗口列表
    
    返回:
    - list: 对齐后的图表列表
    """
    if not stock_data_list:
        return []
    
    # 对齐股票数据
    alignment_result = align_multiple_stocks(stock_data_list, method=method)
    if not alignment_result:
        return []
    
    aligned_stocks = alignment_result['aligned_stocks']
    
    # 创建图表
    charts = []
    
    for i, stock_data in enumerate(aligned_stocks):
        # 准备标题
        if titles and i < len(titles):
            title = titles[i]
        else:
            title = f"股票 {i+1}"
        
        if stock_data.get('is_reference', False):
            title += " (参考)"
        
        # 获取原始DataFrame
        df = stock_data['data']
        
        # 使用索引轴绘制图表
        fig = plot_kline_index_based(
            df=df,
            title=title,
            future_df=None,
            future_dates=None,
            show_future_data=False,
            show_volume=show_volume,
            show_indicators=show_indicators,
            ma_windows=ma_windows,
            use_index_axis=True
        )
        
        if fig:
            # 更新图表标题，添加对齐信息
            aligned_length = len(stock_data['aligned_indices'])
            fig.update_layout(
                title=f"{title} (长度: {aligned_length})",
                height=400  # 多股对比时使用较小高度
            )
            charts.append(fig)
    
    return charts


def prepare_aligned_similarity_data(current_df, similar_patterns, top_n=3):
    """
    准备对齐的相似度比对数据
    
    参数:
    - current_df: 当前股票数据
    - similar_patterns: 相似模式列表
    - top_n: 取前N个相似模式
    
    返回:
    - dict: 包含对齐后的数据和配置
    """
    if not similar_patterns:
        return None
    
    # 限制相似模式数量
    top_patterns = similar_patterns[:top_n]
    
    # 准备数据列表：当前股票 + 相似模式
    stock_data_list = [current_df]
    titles = ["当前股票"]
    
    for i, pattern in enumerate(top_patterns):
        stock_data_list.append(pattern['pattern_data'])
        similarity_percent = f"{pattern['similarity']:.1%}"
        date_range = f"{pattern['start_date'].strftime('%Y-%m-%d')} 至 {pattern['end_date'].strftime('%Y-%m-%d')}"
        titles.append(f"相似模式 {i+1} ({similarity_percent})")
    
    # 对齐数据
    alignment_result = align_multiple_stocks(stock_data_list, method='index', reference_index=0)
    
    if not alignment_result:
        return None
    
    # 添加额外信息
    alignment_result['titles'] = titles
    alignment_result['similar_patterns'] = top_patterns
    
    return alignment_result


def plot_kline_index_based(df, title, future_df=None, future_dates=None, show_future_data=False, 
                          show_volume=True, show_indicators=True, ma_windows=None, use_index_axis=True):
    """
    创建K线图（索引轴版本），支持显示历史走势和未来预测
    
    参数说明：
    - df: 当前K线数据
    - title: 图表标题
    - future_df: 未来实际数据（用于历史相似图）
    - future_dates: 未来日期（用于当前K线图）
    - show_future_data: 是否显示未来数据
    - use_index_axis: 是否使用索引轴（True=索引轴，False=时间轴）
    """
    if df is None or df.empty:
        return None
    
    # 根据标题判断周期类型
    if '日' in title or 'daily' in title.lower():
        period_type = 'daily'
    elif '周' in title or 'weekly' in title.lower():
        period_type = 'weekly'
    elif '月' in title or 'monthly' in title.lower():
        period_type = 'monthly'
    else:
        period_type = 'daily'
    
    # 准备K线数据 - 根据用户要求，不填充节假日数据
    # fill_holidays=False 确保时间轴不连续，节假日不显示任何数据
    df_filled, _ = prepare_kline_data(df, period_type, fill_holidays=False)
    
    # 将未来数据也加入数据框，以便计算后续走势的指标
    if future_df is not None and not future_df.empty:
        # 对未来数据也进行节假日填充
        future_df_filled = prepare_kline_data(future_df.copy(), period_type, fill_holidays=True)[0]
        if future_df_filled is not None and not future_df_filled.empty:
            # 合并主数据和未来数据
            df_filled = pd.concat([df_filled, future_df_filled], ignore_index=True)
    
    # 转换为索引格式
    index_data = convert_to_index_based(df_filled, include_dates=True)
    if index_data is None:
        return None
    
    # 只显示有效交易日的K线
    valid_mask = df_filled['is_valid_period'] == True if 'is_valid_period' in df_filled.columns else pd.Series([True] * len(df_filled))
    valid_indices = [i for i, is_valid in enumerate(valid_mask) if is_valid]
    valid_data = df_filled[valid_mask]
    
    # 创建有效的索引映射
    valid_index_mapping = {orig_idx: display_idx for display_idx, orig_idx in enumerate(valid_indices)}
    display_indices = list(range(len(valid_indices)))
    
    # 创建子图布局（根据是否显示成交量/指标动态设置）
    if show_indicators:
        fig = make_subplots(
            rows=4, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.4, 0.2, 0.2, 0.2]
        )
        price_row = 1
        volume_row = 2
        xaxis_rows = [1, 2, 3, 4]
    elif show_volume:
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.7, 0.3]
        )
        price_row = 1
        volume_row = 2
        xaxis_rows = [1, 2]
    else:
        fig = make_subplots(
            rows=1, cols=1,
            shared_xaxes=True
        )
        price_row = 1
        volume_row = None
        xaxis_rows = [1]
    
    # 添加主K线 - A股颜色标准：上涨红色，下跌绿色
    # 使用索引轴：x=display_indices
    fig.add_trace(go.Candlestick(
        x=display_indices if use_index_axis else valid_data['trade_date'],
        open=valid_data['open'],
        high=valid_data['high'],
        low=valid_data['low'],
        close=valid_data['close'],
        name='K线',
        increasing=dict(line=dict(color='#ff4d4d'), fillcolor='#ff4d4d'),  # 上涨红色
        decreasing=dict(line=dict(color='#26a69a'), fillcolor='#26a69a')   # 下跌绿色
    ), row=price_row, col=1)
    
    # 叠加均线到K线主图
    if ma_windows is None:
        ma_windows = [5, 10, 20]
    ma_color_map = {
        5: '#fdd835',
        10: '#29b6f6',
        20: '#ab47bc',
        60: '#ffa726',
        120: '#66bb6a'
    }
    for ma in ma_windows:
        if len(valid_data) >= ma:
            ma_series = valid_data['close'].rolling(window=ma, min_periods=1).mean()
            fig.add_trace(go.Scatter(
                x=display_indices if use_index_axis else valid_data['trade_date'],
                y=ma_series,
                mode='lines',
                name=f'MA{ma}',
                line=dict(color=ma_color_map.get(ma, '#90a4ae'), width=1.2),
                opacity=0.9
            ), row=price_row, col=1)
    
    # 添加成交量柱状图
    if show_volume and 'volume' in df_filled.columns and volume_row is not None:
        # 根据涨跌设置成交量颜色
        colors = []
        for i in range(len(valid_data)):
            if i == 0:
                colors.append('#cccccc')  # 第一个交易日用灰色
            else:
                if valid_data['close'].iloc[i] >= valid_data['open'].iloc[i]:
                    colors.append('#ff4d4d')  # 上涨红色
                else:
                    colors.append('#26a69a')   # 下跌绿色
        
        fig.add_trace(go.Bar(
            x=display_indices if use_index_axis else valid_data['trade_date'],
            y=valid_data['volume'],
            name='成交量',
            marker_color=colors,
            opacity=0.7
        ), row=volume_row, col=1)
    
    # 同时显示MACD和KDJ指标
    # 获取自定义参数或使用默认值
    macd_params = st.session_state.get('macd_params', {'fast': 12, 'slow': 26, 'signal': 9})
    kdj_params = st.session_state.get('kdj_params', {'n': 9, 'm1': 3, 'm2': 3})
    
    # 添加MACD指标到第3行
    macd_data = calculate_macd(
        df_filled, 
        fast_period=macd_params['fast'],
        slow_period=macd_params['slow'],
        signal_period=macd_params['signal']
    )
    if show_indicators and macd_data is not None:
        # 确保MACD数据没有NaN值，向前填充以保证线连续
        macd_data_clean = macd_data.ffill()
        
        # 获取有效的MACD数据（对应有效交易日）
        valid_macd_data = macd_data_clean.iloc[valid_indices] if len(macd_data_clean) > max(valid_indices) else macd_data_clean
        
        # 添加DIF线（快线）
        fig.add_trace(go.Scatter(
            x=display_indices if use_index_axis else valid_data['trade_date'],
            y=valid_macd_data['DIF'],
            mode='lines',
            name='DIF',
            line=dict(color='#ff9900', width=1.5),
            opacity=0.8,
            connectgaps=True  # 连接数据中的间隙
        ), row=3, col=1)
        
        # 添加DEA线（慢线/信号线）
        fig.add_trace(go.Scatter(
            x=display_indices if use_index_axis else valid_data['trade_date'],
            y=valid_macd_data['DEA'],
            mode='lines',
            name='DEA',
            line=dict(color='#0099ff', width=1.5),
            opacity=0.8,
            connectgaps=True  # 连接数据中的间隙
        ), row=3, col=1)
        
        # 添加MACD柱状图
        macd_colors = ['#ff4d4d' if val >= 0 else '#26a69a' for val in valid_macd_data['MACD']]
        fig.add_trace(go.Bar(
            x=display_indices if use_index_axis else valid_data['trade_date'],
            y=valid_macd_data['MACD'],
            name='MACD',
            marker_color=macd_colors,
            opacity=0.6
        ), row=3, col=1)
        
        # 添加MACD标题
        fig.update_yaxes(title_text="MACD", row=3, col=1)
    
    # 添加KDJ指标到第4行
    kdj_data = calculate_kdj(
        df_filled,
        n=kdj_params['n'],
        m1=kdj_params['m1'],
        m2=kdj_params['m2']
    )
    if show_indicators and kdj_data is not None:
        # 确保KDJ数据没有NaN值，向前填充以保证线连续
        kdj_data_clean = kdj_data.ffill()
        
        # 获取有效的KDJ数据（对应有效交易日）
        valid_kdj_data = kdj_data_clean.iloc[valid_indices] if len(kdj_data_clean) > max(valid_indices) else kdj_data_clean
        
        # 添加K线
        fig.add_trace(go.Scatter(
            x=display_indices if use_index_axis else valid_data['trade_date'],
            y=valid_kdj_data['K'],
            mode='lines',
            name='K',
            line=dict(color='#ff9900', width=1.5),
            opacity=0.8,
            connectgaps=True  # 连接数据中的间隙
        ), row=4, col=1)
        
        # 添加D线
        fig.add_trace(go.Scatter(
            x=display_indices if use_index_axis else valid_data['trade_date'],
            y=valid_kdj_data['D'],
            mode='lines',
            name='D',
            line=dict(color='#0099ff', width=1.5),
            opacity=0.8,
            connectgaps=True  # 连接数据中的间隙
        ), row=4, col=1)
        
        # 添加J线
        fig.add_trace(go.Scatter(
            x=display_indices if use_index_axis else valid_data['trade_date'],
            y=valid_kdj_data['J'],
            mode='lines',
            name='J',
            line=dict(color='#66ff66', width=1.5),
            opacity=0.8,
            connectgaps=True  # 连接数据中的间隙
        ), row=4, col=1)
        
        # 添加KDJ标题
        fig.update_yaxes(title_text="KDJ", row=4, col=1)
    
    # 配置X轴
    for row in xaxis_rows:
        if use_index_axis:
            # 索引轴配置 - 智能日期标签格式化
            tick_interval = max(1, len(display_indices) // 10)  # 显示约10个刻度
            
            # 准备刻度值和标签
            tickvals = []
            ticktext = []
            
            for i in range(0, len(display_indices), tick_interval):
                if i < len(display_indices):
                    display_idx = display_indices[i]
                    orig_idx = valid_indices[i] if i < len(valid_indices) else i
                    
                    # 获取日期字符串
                    date_str = index_data['index_to_date'].get(orig_idx, str(orig_idx))
                    
                    # 如果是日期字符串，进行格式化
                    if date_str and len(date_str) >= 8:  # 假设是YYYY-MM-DD格式
                        try:
                            # 简化日期显示：对于密集显示，只显示月和日
                            if len(display_indices) > 30:
                                # 长序列：显示月-日
                                date_parts = date_str.split('-')
                                if len(date_parts) >= 2:
                                    ticktext.append(f"{date_parts[1]}-{date_parts[2]}")
                                else:
                                    ticktext.append(date_str)
                            else:
                                # 短序列：显示完整日期
                                ticktext.append(date_str)
                        except:
                            ticktext.append(date_str)
                    else:
                        ticktext.append(f"第{display_idx+1}日")
                    
                    tickvals.append(display_idx)
            
            # 确保至少有一个刻度
            if not tickvals and display_indices:
                tickvals = [display_indices[0], display_indices[-1]]
                ticktext = ["开始", "结束"]
            
            fig.update_xaxes(
                type='category',  # 使用分类轴（索引轴）
                tickmode='array',
                tickvals=tickvals,
                ticktext=ticktext,
                title_text="交易日 (索引轴)" if row == max(xaxis_rows) else "",
                row=row, col=1
            )
        else:
            # 时间轴配置（保持原样）
            fig.update_xaxes(
                type='date',
                title_text="日期" if row == max(xaxis_rows) else "",
                row=row, col=1
            )
    
    # 更新布局
    fig.update_layout(
        title=title,
        height=800 if show_indicators else 600 if show_volume else 400,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode='x unified'
    )
    
    return fig


def plot_kline(df, title, future_df=None, future_dates=None, show_future_data=False, show_volume=True, show_indicators=True, ma_windows=None):
    """
    创建K线图，支持显示历史走势和未来预测
    
    参数说明：
    - df: 当前K线数据
    - title: 图表标题
    - future_df: 未来实际数据（用于历史相似图）
    - future_dates: 未来日期（用于当前K线图）
    - show_future_data: 是否显示未来数据
    
    注意：此函数现在默认使用索引轴渲染，消除节假日空隙
    """
    # 默认使用索引轴渲染（消除节假日空隙）
    return plot_kline_index_based(
        df=df,
        title=title,
        future_df=future_df,
        future_dates=future_dates,
        show_future_data=show_future_data,
        show_volume=show_volume,
        show_indicators=show_indicators,
        ma_windows=ma_windows,
        use_index_axis=True  # 使用索引轴
    )

def display_trend_analysis(similar_patterns):
    """
    显示趋势分析结果
    """
    stats = analyze_future_trends(similar_patterns)
    
    if not stats:
        st.write("未找到历史K线数据")
        return
    
    st.markdown(f"### 未来走势涨跌预测（基于最相似 {len(similar_patterns)} 条历史 K 线）")
    
    # 创建网格展示数据
    html = create_trend_analysis_grid(stats)
    
    # 显示网格布局
    st.markdown(html, unsafe_allow_html=True)
    
    # 添加分析说明
    st.markdown("""
    <details class="details">
        <summary class="details-summary">
        <strong>指标计算方法说明</strong></summary>
        <div class="details-body">
            <p><strong>涨跌概率：</strong>当天上涨/下跌的历史K线数量 ÷ 总K线数量</p>
            <p><strong>涨跌幅指标（相对于前一天收盘价）：</strong></p>
            <ul>
                <li>最高/最低涨跌幅：极值涨跌幅</li>
                <li>平均涨跌幅：所有样本的平均值</li>
                <li>最可能涨跌幅：出现频率最高的涨跌幅</li>
            </ul>
            <p><strong>注意：</strong>所有计算均基于历史相似K线的统计结果，仅供参考。</p>
        </div>
    </details>
    """, unsafe_allow_html=True)

def create_trend_analysis_grid(stats):
    """
    创建趋势分析网格的HTML布局
    """
    metrics_data = {
        'up': [
            ('涨概率', 'probability', '%.1f%%'),
            ('最高涨幅', 'max', '%.2f%%'),
            ('平均涨幅', 'mean', '%.2f%%'),
            ('最可能涨幅', 'mode', '%.2f%%'),
            ('最低涨幅', 'min', '%.2f%%')
        ],
        'down': [
            ('跌概率', 'probability', '%.1f%%'),
            ('最高跌幅', 'max', '%.2f%%'),
            ('平均跌幅', 'mean', '%.2f%%'),
            ('最可能跌幅', 'mode', '%.2f%%'),
            ('最低跌幅', 'min', '%.2f%%')
        ]
    }
    
    day_keys = sorted(stats['up'].keys(), key=lambda x: int(x))
    if not day_keys:
        return "<div>无可用趋势数据</div>"

    day_labels = [stats['up'][day_key].get('label', f'第{day_key}期') for day_key in day_keys]

    # 创建HTML布局
    html = """
    <style>
        .grid-container {
            display: grid;
            grid-template-columns: 100px repeat(__COLUMN_COUNT__, 1fr);
            gap: 1px;
            background-color: #ddd;
            padding: 1px;
            font-size: 12px;
            font-family: Arial, sans-serif;
        }
        .grid-header {
            background-color: #f4f4f4;
            padding: 8px;
            text-align: center;
            font-weight: bold;
        }
        .grid-label {
            background-color: #f4f4f4;
            padding: 8px;
            text-align: right;
        }
        .grid-cell {
            background-color: white;
            padding: 8px;
            text-align: center;
        }
        .up-value { color: #ff4444; }
        .down-value { color: #00cc00; }
        .details {
            background-color: rgb(240, 242, 246);
            padding: 15px;
            border-radius: 5px;
            margin-top: 16px;
            margin-bottom: 16px;
            transition: background-color 0.2s;
        }
        .details:hover {
            background-color: rgb(230, 232, 236);
        }
        .details-summary {
            margin: 0;
            font-size: 0.9em;
            color: #666;
            cursor: pointer;
        }
        .details-body {
            background-color: #f9f9f9;
            padding: 10px;
            margin: 5px 0;
            border-radius: 4px;
            font-family: monospace;
        }

        .details-body-related-chunks {
            font-size: 12px;
            white-space: pre-wrap;
        }

        .details-body-related-chunks p {
            background: #eee;
            padding: 20px;
        }

        .statement {
            background-color: #fff3cd;
            padding: 10px;
            border-radius: 5px; 
            font-size: 0.8em;
            margin-top: 10px;
            margin-bottom: 10px;
        }
    </style>
    <div class="grid-container">
        <div class="grid-header"></div>
    """
    html = html.replace("__COLUMN_COUNT__", str(len(day_keys)))
    
    # 添加列标题
    for label in day_labels:
        html += f'<div class="grid-header">{label}</div>'
    
    # 处理数据行
    for direction in ['up', 'down']:
        for label, metric_key, format_str in metrics_data[direction]:
            html += f'<div class="grid-label">{label}</div>'
            
            for day_key in day_keys:
                day_stats = stats[direction].get(day_key, {})
                if day_stats['count'] > 0:
                    value = day_stats[metric_key]
                    if metric_key == 'probability':
                        value = value * 100
                    cell_content = format_str % value
                    style_class = 'up-value' if direction == 'up' else 'down-value'
                    html += f'<div class="grid-cell"><span class="{style_class}">{cell_content}</span></div>'
                else:
                    html += '<div class="grid-cell">无数据</div>'
    
    html += "</div>"
    return html

def display_holding_period_analysis(stats, initial_money=10000):
    """
    显示不同持有期的收益分析结果
    """
    if not stats:
        st.warning("没有足够的历史数据进行分析")
        return
        
    sample_count = list(stats.values())[0]['trade_count']
    
    st.markdown(f"### 立即买入收益预测（基于最相似的 {sample_count} 条历史 K 线）")
    
    # 创建表格数据
    data = []
    for days, day_stats in stats.items():
        data.append({
            "持有期": f"{days}个交易日",
            "平均收益率": f"{day_stats['avg_return']:.2f}%",
            "胜率": f"{day_stats['win_rate']*100:.1f}%",
            "最大上涨": f"{day_stats['max_return']:.2f}%",
            "最大下跌": f"{day_stats['min_return']:.2f}%",
            "波动率": f"{day_stats['std_return']:.2f}%"
        })
    
    # 显示表格
    st.dataframe(
        data,
        hide_index=True,
        use_container_width=True,
        column_config={
            "持有天数": st.column_config.TextColumn("持有天数", help="从当前交易日收盘价买入的持有时间"),
            "平均收益率": st.column_config.TextColumn("平均收益率", help="历史相似情况下的平均收益率"),
            "胜率": st.column_config.TextColumn("胜率", help="盈利次数/总次数"),
            "最大上涨": st.column_config.TextColumn("最大上涨", help="历史相似情况下的最大上涨幅度"),
            "最大下跌": st.column_config.TextColumn("最大下跌", help="历史相似情况下的最大下跌幅度"),
            "波动率": st.column_config.TextColumn("波动率", help="收益率的标准差,反映风险大小")
        }
    )
    
    # 添加指标计算方法说明
    st.markdown("""
    <details class="details" style="margin-top: -12px;">
        <summary class="details-summary">
        <strong>指标计算方法说明</strong></summary >
        <div class="details-body">
            <p><strong>收益率和风险指标计算公式：</strong></p>
            <ul>
                <li><strong>平均收益率</strong> = (各样本的收益率之和) ÷ 样本数量<br>
                    其中，单个样本收益率 = (卖出价格 - 买入价格) ÷ 买入价格 × 100%</li>
                <li><strong>胜率</strong> = 盈利次数 ÷ 总交易次数 × 100%<br>
                    当收益率 > 0 时，计为盈利；当收益率 ≤ 0 时，计为亏损</li>
                <li><strong>最大上涨</strong> = MAX((卖出价格 - 买入价格) ÷ 买入价格 × 100%)<br>
                    在所有样本中，选取收益率最高的记录</li>
                <li><strong>最大下跌</strong> = MIN((卖出价格 - 买入价格) ÷ 买入价格 × 100%)<br>
                    在所有样本中，选取收益率最低的记录</li>
                <li><strong>波动率</strong> = STDEV(所有样本收益率) = √(∑(收益率 - 平均收益率)² ÷ (样本数量-1))<br>
                    反映收益率的离散程度，波动率越大代表风险越高</li>
            </ul>
            <p><strong>重要提示：</strong></p>
            <ul>
                <li>买入价格为当前K线的收盘价</li>
                <li>卖出价格为持有期结束时的收盘价</li>
                <li>所有统计均基于历史相似K线数据，仅供参考</li>
                <li>过往表现不代表未来收益，投资需谨慎</li>
            </ul>
        </div>
    </details>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
