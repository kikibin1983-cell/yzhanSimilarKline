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
import hashlib
import hmac
import html
import threading
from pathlib import Path
from dotenv import load_dotenv
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from typing import List, Dict, Optional
from functools import wraps
import multiprocessing as mp

# Numba加速（如果可用）
try:
    from numba import jit, prange
    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False
    print("[性能提示] Numba未安装，将使用纯NumPy计算。建议安装: pip install numba")

# ==================== 图表辅助函数 ====================

def add_similarity_segment_highlight(fig, x0, x1, color_fill, label_text):
    """在K线图上高亮相似段落。"""
    if fig is None:
        return
    try:
        fig.add_vrect(
            x0=max(0, x0) - 0.5,
            x1=max(0, x1) + 0.5,
            fillcolor=color_fill,
            opacity=0.40,
            line_width=0,
            layer='below',
            annotation_text=label_text,
            annotation_position='top left'
        )
    except Exception:
        pass

# ==================== 技术指标缓存系统 ====================

# 导入技术指标缓存（如果可用）
_indicator_cache = None
def _get_indicator_cache():
    """懒加载技术指标缓存"""
    global _indicator_cache
    if _indicator_cache is None:
        try:
            import sys
            technical_cache_path = os.path.join(os.path.dirname(__file__), 'technical_cache')
            if technical_cache_path not in sys.path:
                sys.path.insert(0, technical_cache_path)
            from indicator_cache import get_indicator_cache as _get_cache
            _indicator_cache = _get_cache()
            print(f"[缓存] 技术指标缓存系统已加载，内存缓存: {len(_indicator_cache._memory_cache)} 条")
        except Exception as e:
            print(f"[缓存] 技术指标缓存加载失败: {e}")
            _indicator_cache = None
    return _indicator_cache

# ==================== 内存缓存系统 ====================

class SimilarityCache:
    """
    相似度计算内存缓存
    
    缓存内容：
    - 股票数据的预计算特征（归一化窗口、统计特征等）
    - 相似度搜索结果（热门查询缓存）
    
    特点：
    - LRU淘汰策略
    - 内存限制保护
    - 线程安全
    """
    
    def __init__(self, max_size=100, max_memory_mb=512):
        self.max_size = max_size
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.cache = {}  # {key: (value, timestamp, size)}
        self.access_order = []  # LRU顺序
        self.current_memory = 0
        self._lock = mp.Lock() if 'mp' in globals() else None
        
    def _get_size(self, obj):
        """估算对象内存占用"""
        try:
            import sys
            return sys.getsizeof(obj)
        except:
            return 1024  # 默认1KB
    
    def get(self, key):
        """获取缓存值"""
        if key not in self.cache:
            return None
        
        value, timestamp, size = self.cache[key]
        
        # 更新访问顺序（移到末尾表示最近使用）
        if key in self.access_order:
            self.access_order.remove(key)
        self.access_order.append(key)
        
        return value
    
    def set(self, key, value):
        """设置缓存值"""
        size = self._get_size(value)
        
        # 如果单个对象超过内存限制，不缓存
        if size > self.max_memory_bytes * 0.5:
            return False
        
        # 如果key已存在，更新
        if key in self.cache:
            old_size = self.cache[key][2]
            self.current_memory -= old_size
            self.access_order.remove(key)
        
        # 清理旧缓存直到有足够空间
        while (self.current_memory + size > self.max_memory_bytes or 
               len(self.cache) >= self.max_size) and self.access_order:
            oldest_key = self.access_order.pop(0)
            if oldest_key in self.cache:
                old_size = self.cache[oldest_key][2]
                del self.cache[oldest_key]
                self.current_memory -= old_size
        
        # 添加新缓存
        import time
        self.cache[key] = (value, time.time(), size)
        self.access_order.append(key)
        self.current_memory += size
        
        return True
    
    def clear(self):
        """清空缓存"""
        self.cache.clear()
        self.access_order.clear()
        self.current_memory = 0
    
    def get_stats(self):
        """获取缓存统计"""
        return {
            'size': len(self.cache),
            'max_size': self.max_size,
            'memory_mb': self.current_memory / (1024 * 1024),
            'max_memory_mb': self.max_memory_bytes / (1024 * 1024)
        }

# 全局缓存实例（用于缓存股票特征）
_stock_feature_cache = SimilarityCache(max_size=50, max_memory_mb=256)
_search_result_cache = SimilarityCache(max_size=200, max_memory_mb=128)

print(f"[性能优化] 内存缓存系统已初始化")
import json
import time
import glob

# 使用akshare获取数据
import akshare as ak

# ============ 万向金枢 - 用户管理模块 ============
load_dotenv()

def _safe_html(value) -> str:
    """Escape dynamic text before inserting it into unsafe_allow_html blocks."""
    return html.escape("" if value is None else str(value), quote=True)

def _write_dataframe_cache(cache_file, df: pd.DataFrame) -> None:
    """Write DataFrame cache without pickle deserialization risk."""
    df.to_json(cache_file, orient="split", date_format="iso", force_ascii=False)

def _read_dataframe_cache(cache_file) -> Optional[pd.DataFrame]:
    """Read a DataFrame JSON cache and restore common date columns."""
    df = pd.read_json(cache_file, orient="split")
    for date_col in ("trade_date", "date"):
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    return df

def _get_initial_admin():
    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD", "admin123")
    return {
        "username": username,
        "password_hash": hashlib.sha256(password.encode()).hexdigest(),
        "role": "admin",
        "uses_default_password": password == "admin123",
    }

DEFAULT_ADMIN = _get_initial_admin()

def init_user_system():
    if 'users' not in st.session_state:
        st.session_state.users = {DEFAULT_ADMIN["username"]: DEFAULT_ADMIN}
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'current_user' not in st.session_state:
        st.session_state.current_user = None

def verify_password(username, password):
    if username in st.session_state.users:
        expected_hash = st.session_state.users[username]["password_hash"]
        actual_hash = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(expected_hash, actual_hash)
    return False

def login_user(username):
    st.session_state.logged_in = True
    st.session_state.current_user = username

def logout_user():
    st.session_state.logged_in = False
    st.session_state.current_user = None

def show_login_page():
    st.set_page_config(page_title="万向金枢 | Universal Golden Hub", page_icon="🧭", layout="centered", initial_sidebar_state="collapsed")
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600;700&display=swap');
    .stApp { background: linear-gradient(135deg, #0a0a0f 0%, #1a1a2e 50%, #16213e 100%); }
    .login-container { max-width: 420px; margin: 60px auto; padding: 50px 40px; background: rgba(20, 20, 35, 0.7); backdrop-filter: blur(20px); border-radius: 24px; border: 1px solid rgba(212, 175, 55, 0.3); box-shadow: 0 25px 50px rgba(0,0,0,0.5), 0 0 100px rgba(212,175,55,0.1), inset 0 1px 0 rgba(255,255,255,0.1); text-align: center; position: relative; overflow: hidden; }
    .login-container::before { content: ''; position: absolute; top: 0; left: -100%; width: 200%; height: 3px; background: linear-gradient(90deg, transparent, #D4AF37, #FFD700, #D4AF37, transparent); animation: shimmer 3s infinite; }
    @keyframes shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(50%); } }
    .compass-icon { font-size: 72px; margin-bottom: 15px; filter: drop-shadow(0 0 30px rgba(212, 175, 55, 0.6)); }
    .brand-title { font-family: 'Noto Serif SC', serif; font-size: 38px; font-weight: 700; background: linear-gradient(135deg, #D4AF37 0%, #FFD700 30%, #FFF8DC 50%, #FFD700 70%, #D4AF37 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; margin-bottom: 5px; letter-spacing: 10px; }
    .brand-subtitle { font-size: 11px; letter-spacing: 5px; color: rgba(212, 175, 55, 0.8); text-transform: uppercase; margin-bottom: 5px; }
    .brand-tagline { font-size: 10px; letter-spacing: 3px; color: rgba(255,255,255,0.4); text-transform: uppercase; margin-bottom: 30px; }
    .divider { width: 60px; height: 2px; background: linear-gradient(90deg, transparent, #D4AF37, transparent); margin: 25px auto; }
    .login-title { font-size: 16px; color: rgba(255,255,255,0.9); margin-bottom: 25px; font-weight: 300; letter-spacing: 3px; }
    div[data-testid="stTextInput"] > div > div > input { background: rgba(10, 10, 15, 0.8) !important; border: 1px solid rgba(212, 175, 55, 0.3) !important; border-radius: 12px !important; color: #FFD700 !important; padding: 14px 18px !important; font-size: 15px !important; }
    div[data-testid="stTextInput"] > div > div > input:focus { border-color: #D4AF37 !important; box-shadow: 0 0 20px rgba(212, 175, 55, 0.3) !important; }
    div[data-testid="stTextInput"] label { color: rgba(255,255,255,0.6) !important; font-size: 12px !important; letter-spacing: 1px !important; }
    .stButton > button { width: 100%; padding: 16px !important; background: linear-gradient(135deg, #D4AF37 0%, #B8941F 100%) !important; color: #0a0a0f !important; border: none !important; border-radius: 12px !important; font-size: 15px !important; font-weight: 600 !important; letter-spacing: 4px !important; cursor: pointer !important; transition: all 0.3s ease !important; text-transform: uppercase !important; margin-top: 15px !important; }
    .stButton > button:hover { transform: translateY(-2px) !important; box-shadow: 0 10px 30px rgba(212, 175, 55, 0.4) !important; background: linear-gradient(135deg, #FFD700 0%, #D4AF37 100%) !important; }
    .footer-text { margin-top: 35px; font-size: 10px; color: rgba(255,255,255,0.25); letter-spacing: 1px; }
    #MainMenu, footer, header { visibility: hidden !important; }
    </style>
    <div class="login-container">
        <div style="font-size: 72px; margin-bottom: 10px;">🧭</div>
        <div class="brand-subtitle">Universal Golden Hub</div>
        <div class="brand-title">万向金枢</div>
        <div class="brand-tagline">Stock Backtesting System</div>
        <div class="divider"></div>
        <div class="login-title">系统登录</div>
    </div>
    """, unsafe_allow_html=True)
    with st.form("login_form"):
        username = st.text_input("用户名", placeholder="请输入用户名")
        password = st.text_input("密码", type="password", placeholder="请输入密码")
        if st.form_submit_button("登 录"):
            if verify_password(username, password):
                login_user(username)
                st.success("登录成功！")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error("用户名或密码错误")
    st.markdown('<div class="footer-text">管理员账号可通过 ADMIN_USERNAME / ADMIN_PASSWORD 配置</div>', unsafe_allow_html=True)
    st.stop()

init_user_system()
if not st.session_state.logged_in:
    show_login_page()

# ============ 主应用开始 ============

@st.cache_data(ttl=3600)
def get_securities_name_map():
    """获取证券代码到名称的映射"""
    try:
        from tdx_reader import get_tdx_reader
        reader = get_tdx_reader()
        available = reader.get_available_codes()
        
        name_map = {}
        # 合并上海和深圳的代码
        for code in available.get('sh', []):
            if isinstance(code, dict):
                name_map[code.get('code', '')] = code.get('name', '')
            else:
                name_map[str(code)] = str(code)
        
        for code in available.get('sz', []):
            if isinstance(code, dict):
                name_map[code.get('code', '')] = code.get('name', '')
            else:
                name_map[str(code)] = str(code)
        
        return name_map
    except Exception as e:
        # 返回空映射
        return {}

def get_exchange_from_code(code: str) -> str:
    """根据代码识别交易所"""
    if not code:
        return '未知'
    code = str(code)
    if code.startswith('6') or code.startswith('5') or code.startswith('9'):
        return '上海'
    elif code.startswith('0') or code.startswith('3') or code.startswith('1') or code.startswith('2'):
        return '深圳'
    elif code.startswith('sh'):
        return '上海'
    elif code.startswith('sz'):
        return '深圳'
    else:
        return '未知'

def get_detailed_type(code: str, security_type: str = 'stock') -> str:
    """
    根据代码识别详细的证券类型
    
    分类规则：
    - 指数：000001, 399001, 000300, 000016, 000905, 000688 等
    - 上海主板：600, 601, 603, 605 开头
    - 上海科创板：688 开头
    - 上海B股：900 开头
    - 上海基金：50, 51 开头
    - 深圳主板：000 开头
    - 深圳中小板：002 开头
    - 深圳创业板：300 开头
    - 深圳B股：200 开头
    - 深圳基金：15, 16, 18 开头
    - 北京交易所：43, 83, 87 开头
    - 可转债：11, 12 开头
    """
    if not code:
        return '未知'
    
    code = str(code).strip()
    
    # 移除交易所前缀
    if code.lower().startswith('sh'):
        code = code[2:]
    elif code.lower().startswith('sz'):
        code = code[2:]
    
    # 指数判断
    if security_type == 'index':
        return '指数'
    
    # ETF判断（5开头或15开头）
    if code.startswith('5') or code.startswith('15'):
        return 'ETF'
    
    # 上海市场
    if code.startswith('6'):
        if code.startswith('60'):
            return '上证主板'
        elif code.startswith('68'):
            return '科创板'
        elif code.startswith('69'):
            return '科创板'
    elif code.startswith('9'):
        return '上海B股'
    
    # 深圳市场
    elif code.startswith('0'):
        if code.startswith('000'):
            return '深证主板'
        elif code.startswith('002'):
            return '中小板'
        elif code.startswith('003'):
            return '中小板'
    elif code.startswith('3'):
        return '创业板'
    elif code.startswith('2'):
        return '深圳B股'
    
    # 北京交易所
    elif code.startswith('4') or code.startswith('8'):
        return '北交所'
    
    # 可转债
    elif code.startswith('11') or code.startswith('12'):
        return '可转债'
    
    # LOF基金
    elif code.startswith('16') or code.startswith('18'):
        return 'LOF基金'
    
    return '其他'

# 自选股持久化存储
WATCHLIST_FILE = "./watchlist.json"

def load_watchlist():
    """从磁盘加载自选股列表"""
    try:
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"加载自选股失败: {e}")
    return []

def save_watchlist(watchlist):
    """保存自选股列表到磁盘"""
    try:
        with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(watchlist, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存自选股失败: {e}")

def get_stock_latest_data(code, security_type='stock'):
    """获取股票最新交易日数据"""
    try:
        df = get_market_data(code, security_type, days=30)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            
            change_pct = (latest['close'] - prev['close']) / prev['close'] * 100
            change_amount = latest['close'] - prev['close']
            
            return {
                'code': code,
                'price': latest['close'],
                'change_pct': change_pct,
                'change_amount': change_amount,
                'volume': latest['volume'],
                'trade_date': latest['trade_date'].strftime('%Y-%m-%d') if hasattr(latest['trade_date'], 'strftime') else str(latest['trade_date'])
            }
    except Exception as e:
        print(f"获取{code}最新数据失败: {e}")
    return None

def get_tdx_file_path_for_code(code: str, security_type: str = 'stock') -> str:
    """获取TDX源文件路径（显示理论路径，不检查文件是否存在）"""
    try:
        tdx_path = st.session_state.get('tdx_data_path', r"D:\new_tdx\vipdoc")
        
        # 处理指数代码映射
        actual_code = code
        if security_type == 'index':
            # 上证指数映射
            if code == '000001':
                actual_code = '999999'
            # 深证成指已经是399001
        
        # 获取文件路径
        if actual_code.startswith('6') or actual_code.startswith('5') or actual_code.startswith('9'):
            exchange = 'sh'
        elif actual_code.startswith('0') or actual_code.startswith('3') or actual_code.startswith('1') or actual_code.startswith('2'):
            exchange = 'sz'
        else:
            # 尝试其他规则
            if actual_code.startswith('sh'):
                exchange = 'sh'
                actual_code = actual_code[2:]
            elif actual_code.startswith('sz'):
                exchange = 'sz'
                actual_code = actual_code[2:]
            else:
                return ''
        
        file_name = f"{exchange}{actual_code}.day"
        file_path = os.path.join(tdx_path, exchange, 'lday', file_name)
        
        return file_path
    except Exception as e:
        return ''

def get_stock_name_by_code(code, name_map=None):
    """根据股票代码获取股票名称"""
    if name_map is None:
        name_map = {}
    
    # 确保code是字符串
    code_str = str(code) if code is not None else ''
    
    # 尝试直接查找
    if code_str in name_map:
        return name_map[code_str]
    
    # 尝试去掉前缀再次查找
    for prefix in ['sh', 'sz', 'SH', 'SZ']:
        if code_str.lower().startswith(prefix):
            code_without_prefix = code_str[2:]
            if code_without_prefix in name_map:
                return name_map[code_without_prefix]
    
    # 未找到则返回代码本身
    return code_str


st.set_page_config(page_title="万向金枢 | Universal Golden Hub", page_icon="🧭", layout="wide")

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
            cache_key_raw = f"{func.__name__}_{repr(args)}_{repr(kwargs)}"
            cache_key = f"{func.__name__}_{hashlib.md5(cache_key_raw.encode('utf-8')).hexdigest()}"
            cache_file = os.path.join(cache_dir, f"{cache_key}.json")
            meta_file = os.path.join(cache_dir, f"{cache_key}_meta.json")
            legacy_cache_file = os.path.join(cache_dir, f"{func.__name__}_{str(args)}_{str(kwargs)}.json")
            legacy_meta_file = os.path.join(cache_dir, f"{func.__name__}_{str(args)}_{str(kwargs)}_meta.json")
            read_cache_file, read_meta_file = cache_file, meta_file
            if not os.path.exists(read_cache_file) and os.path.exists(legacy_cache_file) and os.path.exists(legacy_meta_file):
                read_cache_file, read_meta_file = legacy_cache_file, legacy_meta_file
            
            # 检查缓存是否存在且未过期
            if os.path.exists(read_cache_file) and os.path.exists(read_meta_file):
                with open(read_meta_file, 'r') as f:
                    meta = json.load(f)
                cache_time = datetime.strptime(meta['timestamp'], 
                                             '%Y-%m-%d %H:%M:%S')
                
                # 如果缓存未过期，直接从文件加载数据
                if datetime.now() - cache_time < timedelta(days=expire_days):
                    try:
                        with open(read_cache_file, 'r', encoding='utf-8') as f:
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
            # 添加交易所列
            df['exchange'] = df['index_code'].apply(get_exchange_from_code)
            return df
        elif security_type == 'stock':
            # 获取A股股票列表
            df = ak.stock_info_a_code_name()
            df = df.rename(columns={'code': 'stock_code', 'name': 'short_name'})
            # 添加交易所列
            df['exchange'] = df['stock_code'].apply(get_exchange_from_code)
            return df
        elif security_type == 'etf':
            # 从TDX获取ETF列表
            try:
                # 尝试从TDX读取ETF数据
                from tdx_reader import get_tdx_reader
                tdx_path = st.session_state.get('tdx_data_path', r"D:\new_tdx\vipdoc")
                reader = get_tdx_reader(tdx_path)
                available_codes = reader.get_available_codes()
                
                # 创建ETF列表（简化版，实际需要更精确的ETF识别）
                etf_codes = []
                for exchange in ['sh', 'sz']:
                    for code in available_codes.get(exchange, []):
                        # 简单规则：5开头的代码通常是ETF
                        if code.startswith('5') or code.startswith('15'):
                            etf_codes.append({
                                'fund_code': code,
                                'short_name': f'ETF-{code}',  # 占位名称
                                'exchange': '上海' if exchange == 'sh' else '深圳'
                            })
                
                df = pd.DataFrame(etf_codes)
                if not df.empty:
                    print(f"从TDX加载了 {len(df)} 个ETF")
                return df
            except Exception as e:
                print(f"从TDX加载ETF数据时出错: {str(e)}")
                # 返回空DataFrame作为降级方案
                return pd.DataFrame(columns=['fund_code', 'short_name', 'exchange'])
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
            
            # 获取交易所
            exchange_value = row.get('exchange', '')
            if pd.isna(exchange_value) or not exchange_value:
                exchange_value = get_exchange_from_code(str(code_value))
            
            # 获取源文件路径
            source_file = get_tdx_file_path_for_code(str(code_value), security_type)
            
            results.append({
                'code': str(code_value) if code_value is not None else '',
                'name': str(name_value) if name_value is not None else '',
                'type': security_type,
                'exchange': str(exchange_value),
                'source_file': source_file
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

def get_market_data(code, security_type, days=None):
    """获取市场数据 - 使用本地通达信数据
    
    参数:
        code: 证券代码
        security_type: 证券类型
        days: 获取多少天的数据，None表示获取所有可用数据
    """
    end_date = datetime.now()
    
    # 导入TDX读取器
    try:
        from tdx_reader import read_tdx_data
    except ImportError:
        print("错误: 无法导入tdx_reader模块")
        return None
    
    # 转换日期格式为YYYYMMDD
    if days is not None:
        start_date = end_date - timedelta(days=days)
        start_date_str = start_date.strftime('%Y%m%d')
    else:
        start_date_str = None
    
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

def get_stock_category(code: str) -> str:
    """
    根据股票代码判断股票类型分类
    
    参数:
        code: 股票代码
    
    返回:
        股票类型: '上证主板', '科创板', '深证主板', '中小板', '创业板', '北交所', 'ETF基金', '可转债', '其他'
    """
    if not code or not isinstance(code, str):
        return '其他'
    
    code = code.strip()
    
    # 上海证券交易所
    if code.startswith('6'):
        # 600/601/603/605: 上证主板
        if code[:3] in ['600', '601', '603', '605']:
            return '上证主板'
        # 688: 科创板
        elif code.startswith('688'):
            return '科创板'
        # 589/58x: ETF基金
        elif code.startswith('5'):
            return 'ETF基金'
        else:
            return '上证其他'
    
    # 深圳证券交易所
    elif code.startswith('0'):
        # 000/001: 深证主板
        if code[:3] in ['000', '001']:
            return '深证主板'
        # 002/003: 中小板
        elif code[:3] in ['002', '003']:
            return '中小板'
        else:
            return '深证其他'
    
    elif code.startswith('3'):
        # 300/301: 创业板
        if code[:3] in ['300', '301']:
            return '创业板'
        else:
            return '深证其他'
    
    # 北京证券交易所/新三板
    elif code.startswith('4') or code.startswith('8'):
        # 430/83x/87x: 北交所/新三板
        if code.startswith('43') or code.startswith('83') or code.startswith('87'):
            return '北交所'
        else:
            return '新三板'
    
    # ETF基金 (深交所)
    elif code.startswith('15') or code.startswith('16') or code.startswith('17') or code.startswith('18') or code.startswith('19'):
        return 'ETF基金'
    
    # 可转债
    elif code.startswith('1'):
        return '可转债'
    
    # 指数
    elif code in ['000001', '999999', '399001', '399006', '000300']:
        return '指数'
    
    return '其他'


def count_stocks_by_category(codes: list) -> dict:
    """
    统计股票代码列表中各分类的数量
    
    参数:
        codes: 股票代码列表
    
    返回:
        各分类的数量字典
    """
    categories = {}
    for code in codes:
        category = get_stock_category(code)
        categories[category] = categories.get(category, 0) + 1
    return categories


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


def calculate_macd_similarity(df1, df2, return_components=False, macd1=None, macd2=None):
    """MACD 方向与形态相似度
    
    参数:
        macd1, macd2: 预计算的MACD数据（如果为None或空则重新计算）
    """
    result = {
        'score': 0.0,
        'dif': 0.0,
        'dea': 0.0,
        'hist': 0.0
    }

    # 使用预计算的MACD或重新计算
    # ✅ 修复：检查空DataFrame，不仅仅是None
    if macd1 is None or (hasattr(macd1, 'empty') and macd1.empty):
        macd1 = calculate_macd(df1)
    if macd2 is None or (hasattr(macd2, 'empty') and macd2.empty):
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


def calculate_kdj_similarity(df1, df2, return_components=False, kdj1=None, kdj2=None):
    """KDJ 相似度（K/D/J）
    
    参数:
        kdj1, kdj2: 预计算的KDJ数据（如果为None或空则重新计算）
    """
    result = {
        'score': 0.0,
        'k': 0.0,
        'd': 0.0,
        'j': 0.0
    }

    # 使用预计算的KDJ或重新计算
    # ✅ 修复：检查空DataFrame，不仅仅是None
    if kdj1 is None or (hasattr(kdj1, 'empty') and kdj1.empty):
        kdj1 = calculate_kdj(df1)
    if kdj2 is None or (hasattr(kdj2, 'empty') and kdj2.empty):
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

def calculate_technical_similarity(df1, df2, return_components=False, technical_weights=None, 
                                   macd1=None, macd2=None, kdj1=None, kdj2=None):
    """计算技术指标相似度
    
    参数:
        macd1, macd2: 预计算的MACD数据
        kdj1, kdj2: 预计算的KDJ数据
    """
    components = {
        'macd': {'score': 0.0, 'dif': 0.0, 'dea': 0.0, 'hist': 0.0},
        'kdj': {'score': 0.0, 'k': 0.0, 'd': 0.0, 'j': 0.0},
    }

    macd_component = calculate_macd_similarity(df1, df2, return_components=True, macd1=macd1, macd2=macd2)
    kdj_component = calculate_kdj_similarity(df1, df2, return_components=True, kdj1=kdj1, kdj2=kdj2)
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


def calculate_comprehensive_similarity(df1, df2, market_volatility=1.0, return_components=False, weight_profile=None, precomputed_indicators_df1=None, precomputed_indicators_df2=None):
    """
    计算综合相似度
    
    参数：
    - df1, df2: 包含价格和成交量数据的DataFrame
    - market_volatility: 市场波动率因子（>1表示高波动，<1表示低波动）
    
    返回：
    - 综合相似度（0-1之间）或包含详细组件的字典（return_components=True）
    """
    # 基础权重：默认形态优先（趋势/量能/形态/MA）
    weights = {
        'trend': 0.0,
        'volume': 0.0,
        'pattern': 0.0,
        'ma': 0.0,
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
            'weights': {'trend': 0.0, 'volume': 0.0, 'pattern': 1.0, 'ma': 0.0},
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
        weights['trend'] = max(0.0, weights['trend'] - 0.05)
    elif market_volatility < 0.8:  # 低波动市场
        weights['pattern'] += 0.03
        weights['trend'] += 0.02

    # 归一化权重
    weights = _normalize_weights(weights)

    # ✅ 优化：延迟计算 - 只计算有权重的指标
    
    # 1. 趋势相似度（价格相似度）- 基础指标，但可跳过
    if weights.get('trend', 0) > 0.001:
        trend_sim = calculate_price_similarity(aligned_df1, aligned_df2)
    else:
        trend_sim = 0.5  # 默认值
    
    # 2. 成交量相似度
    volume_sim = 0.0
    if weights.get('volume', 0) > 0.001:
        if 'volume' in aligned_df1.columns and 'volume' in aligned_df2.columns:
            volume_sim = calculate_volume_similarity(
                aligned_df1['volume'].values,
                aligned_df2['volume'].values
            )
    
    # 3. MACD和KDJ技术指标相似度
    macd_sim = 0.5  # 默认值
    kdj_sim = 0.5   # 默认值
    technical_sim_result = None
    
    if weights.get('macd', 0) > 0.001 or weights.get('kdj', 0) > 0.001:
        # ✅ 使用预计算指标（如果提供）
        macd1_precomputed = None
        macd2_precomputed = None
        kdj1_precomputed = None
        kdj2_precomputed = None
        
        if precomputed_indicators_df1 is not None:
            macd1_precomputed = precomputed_indicators_df1.get('macd')
            kdj1_precomputed = precomputed_indicators_df1.get('kdj')
        if precomputed_indicators_df2 is not None:
            macd2_precomputed = precomputed_indicators_df2.get('macd')
            kdj2_precomputed = precomputed_indicators_df2.get('kdj')
        
        # 根据日期截取对应窗口的指标数据
        if macd1_precomputed is not None and not macd1_precomputed.empty:
            start_date = aligned_df1['trade_date'].min()
            end_date = aligned_df1['trade_date'].max()
            macd1_precomputed = macd1_precomputed[
                (macd1_precomputed.index >= start_date) & 
                (macd1_precomputed.index <= end_date)
            ]
        
        if macd2_precomputed is not None and not macd2_precomputed.empty:
            start_date = aligned_df2['trade_date'].min()
            end_date = aligned_df2['trade_date'].max()
            macd2_precomputed = macd2_precomputed[
                (macd2_precomputed.index >= start_date) & 
                (macd2_precomputed.index <= end_date)
            ]
        
        if kdj1_precomputed is not None and not kdj1_precomputed.empty:
            start_date = aligned_df1['trade_date'].min()
            end_date = aligned_df1['trade_date'].max()
            kdj1_precomputed = kdj1_precomputed[
                (kdj1_precomputed.index >= start_date) & 
                (kdj1_precomputed.index <= end_date)
            ]
        
        if kdj2_precomputed is not None and not kdj2_precomputed.empty:
            start_date = aligned_df2['trade_date'].min()
            end_date = aligned_df2['trade_date'].max()
            kdj2_precomputed = kdj2_precomputed[
                (kdj2_precomputed.index >= start_date) & 
                (kdj2_precomputed.index <= end_date)
            ]
        
        technical_sim_result = calculate_technical_similarity(
            aligned_df1,
            aligned_df2,
            return_components=True,
            technical_weights={'macd': weights.get('macd', 0), 'kdj': weights.get('kdj', 0)},
            macd1=macd1_precomputed,
            macd2=macd2_precomputed,
            kdj1=kdj1_precomputed,
            kdj2=kdj2_precomputed
        )
        macd_sim = technical_sim_result['macd']['score']
        kdj_sim = technical_sim_result['kdj']['score']
    
    # 4. K线形态相似度
    if weights.get('pattern', 0) > 0.001:
        pattern_sim = calculate_pattern_similarity(aligned_df1, aligned_df2)
    else:
        pattern_sim = 0.5  # 默认值
    
    # 5. 市场状态相似度
    if weights.get('regime', 0) > 0.001:
        regime_sim = calculate_regime_similarity(aligned_df1, aligned_df2)
    else:
        regime_sim = 0.5  # 默认值
    
    # 6. MA均线相似度
    if weights.get('ma', 0) > 0.001:
        ma_sim = calculate_ma_similarity(aligned_df1, aligned_df2)
    else:
        ma_sim = 0.5  # 默认值
    
    # 计算加权综合相似度
    total_similarity = (
        trend_sim * weights.get('trend', 0) +
        volume_sim * weights.get('volume', 0) +
        pattern_sim * weights.get('pattern', 0) +
        ma_sim * weights.get('ma', 0) +
        macd_sim * weights.get('macd', 0) +
        kdj_sim * weights.get('kdj', 0) +
        regime_sim * weights.get('regime', 0)
    )

    # 稳健性惩罚：若收益率秩相关较差，降低最终得分
    robustness_penalty = calculate_return_rank_penalty(df1, df2)
    total_similarity *= robustness_penalty

    # 确保technical_sim_result不为None
    if technical_sim_result is None:
        technical_sim_result = {
            'score': 0.5,
            'macd': {'score': macd_sim},
            'kdj': {'score': kdj_sim},
            'weights': {'macd': weights.get('macd', 0), 'kdj': weights.get('kdj', 0)}
        }
    
    result = {
        'score': max(0.0, min(1.0, total_similarity)),
        'components': {
            'trend': trend_sim,
            'volume': volume_sim,
            'pattern': pattern_sim,
            'ma': ma_sim,
            'technical': technical_sim_result,
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

def calculate_ma_similarity(df1, df2, ma_windows=None):
    """
    计算均线相似度 - 多时间框架MA收敛评分
    
    算法逻辑:
    1. 计算多周期MA (5, 10, 20, 30, 60)
    2. 价格与MA的距离相似度 (30%)
    3. MA排列形态相似度 (40%) - 多头排列/空头排列/纠缠
    4. MA斜率相似度 (30%) - 趋势方向与强度
    
    参数:
        df1, df2: 包含close价格的DataFrame
        ma_windows: MA周期列表, 默认[5, 10, 20, 30, 60, 120]
    
    返回:
        similarity: 0-1之间的相似度分数
    """
    try:
        if ma_windows is None:
            ma_windows = [5, 10, 20, 30, 60, 120]
        
        # 确保数据长度足够
        min_len = min(len(df1), len(df2))
        if min_len < max(ma_windows):
            # 数据不足时使用较短的MA周期
            ma_windows = [w for w in ma_windows if w <= min_len]
            if not ma_windows:
                return 0.5  # 默认中等相似度
        
        close1 = df1['close'].tail(min_len).values
        close2 = df2['close'].tail(min_len).values
        
        # 计算各周期MA
        ma_data1 = {}
        ma_data2 = {}
        for window in ma_windows:
            ma_data1[window] = pd.Series(close1).rolling(window=window, min_periods=1).mean().values
            ma_data2[window] = pd.Series(close2).rolling(window=window, min_periods=1).mean().values
        
        # 1. 价格与MA距离相似度 (30%)
        # 计算最新价格相对于各MA的位置
        def price_ma_distance(close, ma_dict):
            """计算价格与MA的平均偏离度"""
            distances = []
            weights = []
            for window, ma in ma_dict.items():
                if len(ma) > 0 and ma[-1] != 0:
                    dist = (close[-1] - ma[-1]) / ma[-1]
                    distances.append(dist)
                    # 短周期MA权重更高
                    weights.append(1.0 / window)
            if not distances:
                return 0
            # 加权平均
            return np.average(distances, weights=weights)
        
        dist1 = price_ma_distance(close1, ma_data1)
        dist2 = price_ma_distance(close2, ma_data2)
        distance_sim = 1 / (1 + abs(dist1 - dist2) * 10)
        
        # 2. MA排列形态相似度 (40%)
        # 判断多头排列(短>中>长)、空头排列、纠缠状态
        def ma_alignment_pattern(ma_dict):
            """
            返回MA排列特征向量
            [多头排列强度, 空头排列强度, 纠缠度]
            """
            if len(ma_dict) < 3:
                return [0.33, 0.33, 0.34]
            
            # 获取最新MA值
            latest_ma = {w: ma[-1] for w, ma in ma_dict.items() if len(ma) > 0}
            if len(latest_ma) < 3:
                return [0.33, 0.33, 0.34]
            
            sorted_windows = sorted(latest_ma.keys())
            values = [latest_ma[w] for w in sorted_windows]
            
            # 计算相邻MA的相对位置
            bullish_count = 0  # 短MA在长MA之上
            bearish_count = 0  # 短MA在长MA之下
            
            for i in range(len(values) - 1):
                if values[i] > values[i + 1]:  # 短周期MA > 长周期MA
                    bullish_count += 1
                elif values[i] < values[i + 1]:
                    bearish_count += 1
            
            total_pairs = len(values) - 1
            if total_pairs == 0:
                return [0.33, 0.33, 0.34]
            
            # 计算纠缠度 (MA之间的离散程度)
            ma_mean = np.mean(values)
            ma_std = np.std(values)
            dispersion = ma_std / ma_mean if ma_mean != 0 else 0
            entanglement = max(0, 1 - dispersion * 20)  # 越纠缠值越大
            
            bullish_score = bullish_count / total_pairs
            bearish_score = bearish_count / total_pairs
            
            # 归一化
            total = bullish_score + bearish_score + entanglement
            if total > 0:
                return [
                    bullish_score / total,
                    bearish_score / total,
                    entanglement / total
                ]
            return [0.33, 0.33, 0.34]
        
        pattern1 = ma_alignment_pattern(ma_data1)
        pattern2 = ma_alignment_pattern(ma_data2)
        
        # 计算模式相似度 (余弦相似度)
        dot_product = sum(p1 * p2 for p1, p2 in zip(pattern1, pattern2))
        norm1 = np.sqrt(sum(p * p for p in pattern1))
        norm2 = np.sqrt(sum(p * p for p in pattern2))
        pattern_sim = dot_product / (norm1 * norm2) if norm1 > 0 and norm2 > 0 else 0.5
        
        # 3. MA斜率相似度 (30%)
        # 计算各MA的近期斜率
        def ma_slopes(ma_dict, periods=5):
            """计算各MA的近期斜率"""
            slopes = []
            for window, ma in ma_dict.items():
                if len(ma) >= periods:
                    recent = ma[-periods:]
                    slope = (recent[-1] - recent[0]) / recent[0] if recent[0] != 0 else 0
                    slopes.append(slope)
            return np.mean(slopes) if slopes else 0
        
        slope1 = ma_slopes(ma_data1)
        slope2 = ma_slopes(ma_data2)
        slope_sim = 1 / (1 + abs(slope1 - slope2) * 20)
        
        # 综合评分
        total_sim = 0.3 * distance_sim + 0.4 * pattern_sim + 0.3 * slope_sim
        
        return max(0.0, min(1.0, total_sim))
        
    except Exception as e:
        print(f"MA相似度计算出错: {e}")
        return 0.5

def kline_conversion_cache(cache_dir="./kline_cache", expire_days=7):
    """
    专门用于K线数据转换的缓存装饰器
    
    参数：
        cache_dir: 缓存目录路径
        expire_days: 缓存过期天数，默认7天
    """
    # 使用基于脚本目录的绝对路径
    script_dir = Path(__file__).parent
    abs_cache_dir = script_dir / cache_dir
    
    def decorator(func):
        def wrapper(df, *args, **kwargs):
            # 如果输入数据为空，直接返回
            if df is None or df.empty:
                return df
            
            # 创建缓存目录
            abs_cache_dir.mkdir(parents=True, exist_ok=True)
            
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
            cache_file = abs_cache_dir / f"{cache_key}.json"
            meta_file = abs_cache_dir / f"{cache_key}_meta.json"
            
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
                            cached_df = _read_dataframe_cache(cache_file)
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
                _write_dataframe_cache(cache_file, result_df)
                
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

@kline_conversion_cache(cache_dir="./cache/kline/weekly", expire_days=7)
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

@kline_conversion_cache(cache_dir="./cache/kline/monthly", expire_days=7)
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
    # 使用基于脚本目录的绝对路径，避免工作目录影响
    script_dir = Path(__file__).parent
    cache_dir = script_dir / 'cache' / 'kline' / period_type
    cache_dir.mkdir(parents=True, exist_ok=True)
    code_key = source_code if source_code else 'unknown'
    snapshot_file = cache_dir / f"{period_type}_{code_key}_latest.json"
    meta_file = cache_dir / f"{period_type}_{code_key}_latest_meta.json"
    return str(snapshot_file), str(meta_file)


def _load_period_cache_snapshot(period_type: str, source_code: Optional[str]) -> Optional[pd.DataFrame]:
    """优先读取按代码的快照缓存；若不存在则回退读取目录中最近的历史缓存。"""
    snapshot_file, _ = _period_cache_snapshot_paths(period_type, source_code)
    if os.path.exists(snapshot_file):
        try:
            cached_df = _read_dataframe_cache(snapshot_file)
            if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
                return cached_df
        except Exception:
            pass

    cache_dir = f'./cache/kline/{period_type}'
    if not os.path.exists(cache_dir):
        return None

    try:
        legacy_files = [
            file for file in glob.glob(os.path.join(cache_dir, '*.json'))
            if not file.endswith('_latest.json') and not file.endswith('_meta.json')
        ]
        if not legacy_files:
            return None
        latest_file = max(legacy_files, key=lambda file: os.path.getmtime(file))
        legacy_df = _read_dataframe_cache(latest_file)
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
        _write_dataframe_cache(snapshot_file, df)
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

def _prepare_similarity_df(input_df, period_type='daily', window_size=30, force_update=False, source_code: Optional[str] = None, 
                            start_date=None, end_date=None, custom_future_days=None, anchor_date=None):
    """返回按周期转换后的数据框、实际窗口和未来天数。
    
    参数：
    - input_df: 输入的日线数据
    - period_type: 周期类型 ('daily', 'weekly', 'monthly')
    - window_size: 窗口大小（匹配周期数）
    - force_update: 是否强制更新缓存（忽略缓存，强制重新计算）
    - source_code: 标的代码（用于定位周/月缓存快照）
    - start_date: 开始日期（可选，用于时间范围筛选）
    - end_date: 结束日期（可选，用于时间范围筛选）
    - custom_future_days: 自定义预测周期数（可选，None表示使用默认值）
    - anchor_date: 锚点日期（可选，用于截取历史某段K线。指定后，将从该日期往前推window_size根K线作为匹配区间）
    """
    future_days = 20
    if period_type == 'weekly':
        # 周线相似度比较默认窗口为20根K线
        if window_size == 30:  # 如果未自定义，使用默认值
            window_size = 20
        future_days = 4 if custom_future_days is None else custom_future_days
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
        # 月线相似度比较默认窗口为20根K线
        if window_size == 30:  # 如果未自定义，使用默认值
            window_size = 20
        future_days = 1 if custom_future_days is None else custom_future_days
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
        # 日线默认值
        future_days = 20 if custom_future_days is None else custom_future_days
        transformed_df = input_df

    # 时间范围筛选
    if transformed_df is not None and not transformed_df.empty:
        if 'trade_date' in transformed_df.columns:
            # 确保trade_date是datetime类型
            if not pd.api.types.is_datetime64_any_dtype(transformed_df['trade_date']):
                transformed_df['trade_date'] = pd.to_datetime(transformed_df['trade_date'])
            
            # 传统模式：应用开始和结束日期筛选
            # 注意：锚点日期模式不再这里过滤数据，而是在_find_patterns_in_single_series中处理
            # 这样可以保留完整历史数据用于匹配
            if start_date is not None:
                start_date = pd.to_datetime(start_date)
                transformed_df = transformed_df[transformed_df['trade_date'] >= start_date]
            
            # 应用结束日期筛选
            if end_date is not None:
                end_date = pd.to_datetime(end_date)
                transformed_df = transformed_df[transformed_df['trade_date'] <= end_date]
            
            # 锚点日期模式：只应用结束日期筛选（anchor_date作为结束日期）
            # 但保留锚点日期前的所有历史数据，用于相似度匹配
            if anchor_date is not None:
                anchor_datetime = pd.to_datetime(anchor_date)
                # 筛选anchor_date之前的数据，保留所有历史数据
                transformed_df = transformed_df[transformed_df['trade_date'] <= anchor_datetime].copy()

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
    similarity_weights=None,
    anchor_date=None,
    precomputed_indicators=None,
    source_precomputed_indicators=None  # ✅ 新增：源股票的预计算指标
):
    """在单个标的序列内寻找历史相似模式。"""
    global _stock_feature_cache, _search_result_cache
    
    if data_df is None or data_df.empty:
        return []

    required_columns = {'trade_date', 'close'}
    if not required_columns.issubset(set(data_df.columns)):
        return []

    df_work = data_df.copy()
    if df_work['close'].isna().all():
        return []
    
    # 检查搜索结果缓存
    # ⚠️ 重要：在跨股票搜索模式下，禁用缓存，因为同一个候选股票对不同源股票的匹配结果不同
    # 只有单股票模式（current_df_for_period == df_work）才使用缓存
    is_single_stock_mode = (current_df_for_period is None) or (current_df_for_period is df_work)
    
    if is_single_stock_mode and source_code and len(df_work) >= window_size:
        try:
            import hashlib
            # 使用数据特征生成哈希（包含股票代码确保唯一性）
            recent_data = df_work.tail(window_size)['close'].values
            unique_str = f"{source_code}_{recent_data.tobytes().hex()}_{len(df_work)}"
            data_hash = hashlib.md5(unique_str.encode()).hexdigest()[:16]
            cache_key = f"{source_code}:{window_size}:{top_n}:{period_type}:{data_hash}"
            
            # 尝试从缓存获取
            cached_result = _search_result_cache.get(cache_key)
            if cached_result is not None:
                print(f"[缓存命中] 股票 {source_code} 的相似度搜索结果（窗口={window_size}, top_n={top_n}）")
                # 验证缓存结果的 source_code 是否正确
                if cached_result and len(cached_result) > 0:
                    cached_source = cached_result[0].get('source_code', '')
                    if cached_source != source_code:
                        print(f"[警告] 缓存数据不匹配！期望 {source_code}，实际 {cached_source}，跳过缓存")
                    else:
                        return cached_result
        except Exception as e:
            print(f"[缓存检查] 警告: {e}")
    elif not is_single_stock_mode:
        # 跨股票搜索模式：跳过缓存，直接计算
        print(f"[跨股票搜索] 股票 {source_code} 跳过缓存（不同源股票匹配结果不同）")

    # ✅ 修复：使用 current_df_for_period 作为比较基准（源股票），而不是 df_work（候选股票）
    if current_df_for_period is None:
        current_df_for_period = df_work

    # 处理锚点日期模式：提取锚点日期前window_size根K线作为匹配模式
    if anchor_date is not None:
        anchor_datetime = pd.to_datetime(anchor_date)
        # 找到锚点日期前的数据
        df_before_anchor = current_df_for_period[current_df_for_period['trade_date'] <= anchor_datetime].copy()
        if len(df_before_anchor) >= window_size:
            # 取锚点日期前最后window_size根K线作为匹配模式
            recent_df = df_before_anchor.tail(window_size).copy()
        else:
            # 数据不足，无法使用锚点日期
            st.warning(f"⚠️ 锚点日期 {anchor_date.strftime('%Y-%m-%d')} 前只有 {len(df_before_anchor)} 根K线，少于设定的 {window_size} 根")
            return []
    else:
        # ✅ 修复：使用 current_df_for_period 的最后window_size根作为比较基准
        recent_df = current_df_for_period.tail(window_size).copy()
    
    # 检查是否有足够的历史数据进行匹配
    # 注意：当有锚点日期时，使用锚点日期前的所有历史数据，而不是只使用window_size根
    if anchor_date is not None:
        # 锚点日期模式：需要锚点日期前至少有 window_size * 2 根数据（用于匹配的模式 + 历史模式）
        anchor_datetime = pd.to_datetime(anchor_date)
        df_before_anchor = df_work[df_work['trade_date'] <= anchor_datetime].copy()
        if len(df_before_anchor) < window_size * 2:
            st.warning(f"⚠️ 锚点日期 {anchor_date.strftime('%Y-%m-%d')} 前数据不足（{len(df_before_anchor)} 根），需要至少 {window_size * 2} 根才能进行相似度匹配")
            return []
        # 历史数据搜索范围：锚点日期前的所有数据，排除最后window_size根（那是匹配模式本身）
        max_i = len(df_before_anchor) - (window_size + future_days)
    else:
        # 默认模式：使用全部数据
        if len(df_work) < window_size * 2 or len(recent_df) < window_size:
            return []
        max_i = len(df_work) - (window_size * 2 + future_days)
    
    if max_i <= 0:
        return []

    similar_patterns = []

    if use_optimized:
        def _is_pattern_only(weights):
            if not isinstance(weights, dict):
                return False
            pattern_w = float(weights.get('pattern', 0.0))
            others = (
                float(weights.get('trend', 0.0)) +
                float(weights.get('volume', 0.0)) +
                float(weights.get('ma', 0.0)) +
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
                top_indices = np.argsort(similarities)[-top_n * 3:][::-1]  # 多取3倍候选，用于去重筛选

                # 时间间隔过滤：只要有任何时间重合就过滤
                selected_indices = []
                selected_ranges = []  # 存储已选中的日期范围 [(start_date, end_date), ...]
                
                for idx in top_indices:
                    idx = int(idx)
                    # 获取当前候选模式的日期范围
                    candidate_start = df_work.iloc[idx]['trade_date']
                    candidate_end = df_work.iloc[idx + window_size - 1]['trade_date']
                    
                    # 检查是否与已选中的模式有时间重合
                    too_close = False
                    for selected_start, selected_end in selected_ranges:
                        # 检查是否有重合：两个区间有重叠当且仅当开始小于对方的结束且结束大于对方的开始
                        if (candidate_start < selected_end and candidate_end > selected_start):
                            print(f"[调试] 过滤掉位置 {idx}，日期范围 {candidate_start.strftime('%Y-%m-%d')} 至 {candidate_end.strftime('%Y-%m-%d')} 与已选范围 {selected_start.strftime('%Y-%m-%d')} 至 {selected_end.strftime('%Y-%m-%d')} 有重合")
                            too_close = True
                            break
                    
                    if not too_close:
                        selected_indices.append(idx)
                        selected_ranges.append((candidate_start, candidate_end))
                        print(f"[调试] 选中位置 {idx}，日期范围 {candidate_start.strftime('%Y-%m-%d')} 至 {candidate_end.strftime('%Y-%m-%d')}，已选数量: {len(selected_indices)}/{top_n}")
                        if len(selected_indices) >= top_n:
                            break

                for i in selected_indices:
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

        # ========== 智能剪枝：快速预过滤 ==========
        # 在计算相似度前，先用简单统计特征过滤明显不匹配的模式
        # 这可以减少 50-70% 的无效计算，同时不损失精度
        
        def _smart_pruning_candidates(df_work, recent_df, window_size, max_i, min_keep_ratio=0.3):
            """
            智能剪枝：基于简单统计特征快速过滤
            
            策略：
            1. 波动率差异过滤：历史窗口的波动率与目标差异过大，直接跳过
            2. 趋势方向过滤：趋势方向完全相反的，降低优先级
            3. 振幅范围过滤：价格振幅范围差异过大的，直接跳过
            
            返回：保留下来的候选窗口索引列表
            """
            if max_i < 100:  # 样本太少时不剪枝
                return list(range(max_i))
            
            close_values = df_work['close'].values.astype(float)
            recent_close = recent_df['close'].values.astype(float)
            
            # 目标窗口的统计特征
            target_std = np.std(recent_close)
            target_mean = np.mean(recent_close)
            target_trend = recent_close[-1] - recent_close[0]  # 正为上涨，负为下跌
            target_range = np.max(recent_close) - np.min(recent_close)
            
            candidates = []
            
            for i in range(max_i):
                window = close_values[i:i + window_size]
                if len(window) < window_size:
                    continue
                
                # 特征1：波动率差异（标准差差异超过2倍，大概率不匹配）
                window_std = np.std(window)
                if target_std > 0 and window_std > 0:
                    std_ratio = max(window_std, target_std) / min(window_std, target_std)
                    if std_ratio > 3.0:  # 波动率差异3倍以上，过滤
                        continue
                
                # 特征2：趋势方向（完全相反的趋势，优先级降低但不完全过滤）
                window_trend = window[-1] - window[0]
                if target_trend * window_trend < 0:  # 趋势相反
                    # 不直接过滤，但降低评分（后续处理）
                    pass
                
                # 特征3：振幅范围（价格范围差异过大）
                window_range = np.max(window) - np.min(window)
                if target_range > 0 and window_range > 0:
                    range_ratio = max(window_range, target_range) / min(window_range, target_range)
                    if range_ratio > 4.0:  # 振幅差异4倍以上，过滤
                        continue
                
                candidates.append(i)
            
            # 确保至少保留一定比例
            min_keep = int(max_i * min_keep_ratio)
            if len(candidates) < min_keep:
                # 如果过滤太多，放宽条件，按波动率排序取前N
                all_indices = list(range(max_i))
                all_indices.sort(key=lambda i: abs(np.std(close_values[i:i + window_size]) - target_std))
                candidates = all_indices[:min_keep]
            
            return candidates
        
        # 应用智能剪枝
        pruned_indices = _smart_pruning_candidates(df_work, recent_df, window_size, max_i)
        if len(pruned_indices) < max_i:
            print(f"[智能剪枝] 从 {max_i} 个窗口过滤到 {len(pruned_indices)} 个，减少 {(1 - len(pruned_indices)/max_i)*100:.1f}% 计算量")
            max_i = len(pruned_indices)
        
        # 阶段1：快速向量化粗筛（速度优先）
        close_values = df_work['close'].values.astype(float)
        if len(close_values) < window_size:
            return []

        recent_close = recent_df['close'].values.astype(float)
        eps = 1e-9
        recent_base = recent_close[0] if abs(recent_close[0]) > eps else eps
        recent_norm = (recent_close - recent_base) / recent_base * 100.0

        # 根据剪枝结果生成窗口（只生成保留下来的窗口）
        try:
            if len(pruned_indices) < max_i:
                # 只生成剪枝后保留的窗口
                windows_2d = np.array([close_values[i:i + window_size] for i in pruned_indices])
                # 创建原始索引映射，用于后续定位
                original_indices = pruned_indices
            else:
                windows_2d = np.lib.stride_tricks.sliding_window_view(close_values, window_shape=window_size)[:max_i]
                original_indices = list(range(max_i))
        except Exception:
            if len(pruned_indices) < max_i:
                windows_2d = np.array([close_values[i:i + window_size] for i in pruned_indices])
                original_indices = pruned_indices
            else:
                windows_2d = np.array([close_values[i:i + window_size] for i in range(max_i)])
                original_indices = list(range(max_i))

        if windows_2d.size == 0:
            return []

        win_base = np.where(np.abs(windows_2d[:, 0]) > eps, windows_2d[:, 0], eps)
        windows_norm = (windows_2d - win_base[:, None]) / win_base[:, None] * 100.0

        # 使用Numba加速的批量相似度计算（如果可用）
        # 确保数组是contiguous的，有利于Numba性能
        windows_norm_contig = np.ascontiguousarray(windows_norm)
        recent_norm_contig = np.ascontiguousarray(recent_norm)
        
        # 调用Numba加速函数计算综合相似度
        quick_score = _numba_batch_similarity(windows_norm_contig, recent_norm_contig)
        
        # 同时计算各分量用于后续融合
        corr = _numba_pearson_batch(windows_norm_contig, recent_norm_contig)
        dist = np.linalg.norm(windows_norm - recent_norm[None, :], axis=1)
        dist_sim = 1.0 / (1.0 + dist / max(1, window_size))
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
            # ✅ 修复：准备预计算指标
            # - precomputed_recent: 使用源股票的指标（source_precomputed_indicators）
            # - precomputed_historical: 使用候选股票的指标（precomputed_indicators）
            precomputed_recent = None
            precomputed_historical = None
            
            # recent_df 的指标：从源股票指标中截取
            if source_precomputed_indicators is not None:
                if 'macd' in source_precomputed_indicators and not source_precomputed_indicators['macd'].empty:
                    recent_start = pd.Timestamp(recent_df['trade_date'].min())
                    recent_end = pd.Timestamp(recent_df['trade_date'].max())
                    # ✅ 修复：序列化后index可能变成numpy数组，需要转换为DatetimeIndex
                    macd_df = source_precomputed_indicators['macd']
                    if not isinstance(macd_df.index, pd.DatetimeIndex):
                        macd_df.index = pd.to_datetime(macd_df.index)
                    kdj_df = source_precomputed_indicators.get('kdj')
                    if kdj_df is not None and not isinstance(kdj_df.index, pd.DatetimeIndex):
                        kdj_df.index = pd.to_datetime(kdj_df.index)
                    macd_filtered = macd_df[
                        (macd_df.index >= recent_start) &
                        (macd_df.index <= recent_end)
                    ]
                    kdj_filtered = kdj_df[
                        (kdj_df.index >= recent_start) &
                        (kdj_df.index <= recent_end)
                    ] if kdj_df is not None else None
                    precomputed_recent = {
                        'macd': macd_filtered,
                        'kdj': kdj_filtered
                    }
                    if len(macd_filtered) > 0:
                        print(f"[调试] recent_df日期范围: {recent_start.strftime('%Y-%m-%d')} 至 {recent_end.strftime('%Y-%m-%d')}, MACD筛选后{len(macd_filtered)}条")
                    else:
                        print(f"[警告] recent_df日期范围: {recent_start.strftime('%Y-%m-%d')} 至 {recent_end.strftime('%Y-%m-%d')}, MACD筛选后为空!")
            
            # historical_df 的指标：从候选股票指标中截取
            if precomputed_indicators is not None:
                if 'macd' in precomputed_indicators and not precomputed_indicators['macd'].empty:
                    hist_start = pd.Timestamp(historical_df['trade_date'].min())
                    hist_end = pd.Timestamp(historical_df['trade_date'].max())
                    # ✅ 修复：序列化后index可能变成numpy数组，需要转换为DatetimeIndex
                    macd_df = precomputed_indicators['macd']
                    if not isinstance(macd_df.index, pd.DatetimeIndex):
                        macd_df.index = pd.to_datetime(macd_df.index)
                    kdj_df = precomputed_indicators.get('kdj')
                    if kdj_df is not None and not isinstance(kdj_df.index, pd.DatetimeIndex):
                        kdj_df.index = pd.to_datetime(kdj_df.index)
                    precomputed_historical = {
                        'macd': macd_df[
                            (macd_df.index >= hist_start) &
                            (macd_df.index <= hist_end)
                        ],
                        'kdj': kdj_df[
                            (kdj_df.index >= hist_start) &
                            (kdj_df.index <= hist_end)
                        ] if kdj_df is not None else None
                    }
            
            similarity = calculate_comprehensive_similarity(
                recent_df,
                historical_df,
                market_volatility=market_volatility,
                return_components=False,
                weight_profile=similarity_weights,
                precomputed_indicators_df1=precomputed_recent,
                precomputed_indicators_df2=precomputed_historical
            )
            # 融合粗筛分，兼顾速度与稳健性
            final_similarity = float(similarity) * 0.82 + float(quick_score[int(i)]) * 0.18
            refined.append((int(i), max(0.0, min(1.0, final_similarity)), historical_df))

        if not refined:
            return []

        refined.sort(key=lambda item: item[1], reverse=True)
        
        # 时间间隔过滤：只要有任何时间重合就过滤
        selected_refined = []
        selected_ranges = []  # 存储已选中的日期范围 [(start_date, end_date), ...]
        
        for i, similarity, historical_df in refined:
            # 获取当前候选模式的日期范围
            candidate_start = historical_df['trade_date'].iloc[0]
            candidate_end = historical_df['trade_date'].iloc[-1]
            
            # 检查是否与已选中的模式有时间重合
            too_close = False
            for selected_start, selected_end in selected_ranges:
                # 检查是否有重合：两个区间有重叠当且仅当开始小于对方的结束且结束大于对方的开始
                if (candidate_start < selected_end and candidate_end > selected_start):
                    print(f"[调试-精排路径] 过滤掉位置 {i}，日期范围 {candidate_start.strftime('%Y-%m-%d')} 至 {candidate_end.strftime('%Y-%m-%d')} 与已选范围 {selected_start.strftime('%Y-%m-%d')} 至 {selected_end.strftime('%Y-%m-%d')} 有重合")
                    too_close = True
                    break
            
            if not too_close:
                selected_refined.append((i, similarity, historical_df))
                selected_ranges.append((candidate_start, candidate_end))
                print(f"[调试-精排路径] 选中位置 {i}，日期范围 {candidate_start.strftime('%Y-%m-%d')} 至 {candidate_end.strftime('%Y-%m-%d')}，已选数量: {len(selected_refined)}/{top_n}")
                if len(selected_refined) >= top_n:
                    break

        for i, similarity, historical_df in selected_refined:
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

        # ✅ 优化：使用Numba批量计算相关系数和欧氏距离
        if NUMBA_AVAILABLE and len(all_windows_norm) > 0:
            # Numba加速：批量计算所有窗口的相关系数
            correlations_array = _numba_pearson_batch(all_windows_norm, recent_window_norm)
            # NumPy向量化：批量计算欧氏距离
            distances_array = np.sqrt(np.sum((all_windows_norm - recent_window_norm[None, :]) ** 2, axis=1))
        else:
            # 回退：纯NumPy批量计算（比Python循环快）
            centered_windows = all_windows_norm - np.mean(all_windows_norm, axis=1, keepdims=True)
            centered_recent = recent_window_norm - np.mean(recent_window_norm)
            std_windows = np.std(all_windows_norm, axis=1) + 1e-10
            std_recent = np.std(recent_window_norm) + 1e-10
            correlations_array = (centered_windows @ centered_recent) / (std_windows * std_recent)
            correlations_array = np.clip(correlations_array, -1.0, 1.0)
            
            distances_array = np.sqrt(np.sum((all_windows_norm - recent_window_norm[None, :]) ** 2, axis=1))
        
        if len(correlations_array) == 0:
            return []

        normalized_distances = 1 / (1 + distances_array / max(1, window_size))
        similarities = correlations_array * 0.7 + normalized_distances * 0.3

        top_indices = np.argsort(similarities)[-top_n * 3:][::-1]  # 多取3倍候选，用于去重筛选

        # 时间间隔过滤：只要有任何时间重合就过滤
        selected_indices = []
        selected_ranges = []  # 存储已选中的日期范围 [(start_date, end_date), ...]
        
        for idx in top_indices:
            idx = int(idx)
            # 获取当前候选模式的日期范围
            candidate_start = df_work.iloc[idx]['trade_date']
            candidate_end = df_work.iloc[idx + window_size - 1]['trade_date']
            
            # 检查是否与已选中的模式有时间重合
            too_close = False
            for selected_start, selected_end in selected_ranges:
                # 检查是否有重合：两个区间有重叠当且仅当开始小于对方的结束且结束大于对方的开始
                if (candidate_start < selected_end and candidate_end > selected_start):
                    print(f"[调试-标准路径] 过滤掉位置 {idx}，日期范围 {candidate_start.strftime('%Y-%m-%d')} 至 {candidate_end.strftime('%Y-%m-%d')} 与已选范围 {selected_start.strftime('%Y-%m-%d')} 至 {selected_end.strftime('%Y-%m-%d')} 有重合")
                    too_close = True
                    break
            
            if not too_close:
                selected_indices.append(idx)
                selected_ranges.append((candidate_start, candidate_end))
                print(f"[调试-标准路径] 选中位置 {idx}，日期范围 {candidate_start.strftime('%Y-%m-%d')} 至 {candidate_end.strftime('%Y-%m-%d')}，已选数量: {len(selected_indices)}/{top_n}")
                if len(selected_indices) >= top_n:
                    break

        for i in selected_indices:
            i = int(i)
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
                'future_type': period_type,
                'source_code': source_code,
                'source_type': source_type,
            })

    # 将结果存入缓存（仅在单股票模式下）
    if is_single_stock_mode and source_code and len(df_work) >= window_size and similar_patterns:
        try:
            recent_data = df_work.tail(window_size)['close'].values
            unique_str = f"{source_code}_{recent_data.tobytes().hex()}_{len(df_work)}"
            data_hash = hashlib.md5(unique_str.encode()).hexdigest()[:16]
            cache_key = f"{source_code}:{window_size}:{top_n}:{period_type}:{data_hash}"
            _search_result_cache.set(cache_key, similar_patterns)
            print(f"[缓存存储] 股票 {source_code} 的相似度搜索结果已缓存（当前缓存: {_search_result_cache.get_stats()['size']} 条）")
        except Exception as e:
            print(f"[缓存存储] 警告: {e}")

    return similar_patterns


def _process_single_stock_for_parallel(args):
    """
    处理单个股票的相似度搜索（用于多进程并行）
    
    参数:
        args: tuple (code, params)
            code: 股票代码
            params: dict 包含所有必要参数
                - source_recent_close: 源股票的close价格序列（numpy数组）
                - source_recent_ohlc: 源股票的OHLC数据（dict of numpy数组）
    
    返回:
        list: 该股票的相似模式列表
    """
    code, params = args
    
    try:
        # 动态导入避免序列化问题
        import sys
        sys.path.insert(0, '.')
        from tdx_reader import read_tdx_data
        
        # 读取数据
        candidate_df = read_tdx_data(
            code=code,
            security_type=params['security_type']
        )
        
        if candidate_df is None or candidate_df.empty:
            return []
        
        # 时间范围过滤
        if params.get('start_date') is not None:
            candidate_df = candidate_df[candidate_df['trade_date'] >= pd.to_datetime(params['start_date'])]
        if params.get('end_date') is not None:
            candidate_df = candidate_df[candidate_df['trade_date'] <= pd.to_datetime(params['end_date'])]
        
        if candidate_df.empty:
            return []
        
        # 数据转换
        transformed_df, candidate_window_size, candidate_future_days = _prepare_similarity_df(
            candidate_df,
            period_type=params['period_type'],
            window_size=params['window_size'],
            force_update=params.get('force_update', False),
            source_code=code
        )
        
        # ✅ 从缓存读取预计算的技术指标
        precomputed_indicators = None
        try:
            cache = _get_indicator_cache()
            if cache is not None:
                precomputed_indicators = cache.get_indicators(
                    code=code,
                    df=candidate_df,
                    period_type=params['period_type'],
                    force_recompute=False
                )
                if precomputed_indicators:
                    print(f"[缓存] 股票 {code} 使用预计算指标")
        except Exception as e:
            print(f"[缓存] 读取股票 {code} 指标缓存失败: {e}")
        
        # ✅ 修复：使用源股票的recent数据作为比较基准
        # 从params中获取源股票的OHLC数据
        source_recent_ohlc = params.get('source_recent_ohlc')
        if source_recent_ohlc is not None:
            # 创建源股票的current_df_for_period
            window_size = candidate_window_size
            import pandas as pd
            # ✅ 修复：使用源股票的真实日期，而不是假日期
            # 如果source_recent_ohlc中有真实日期，使用它们；否则回退到假日期
            trade_dates = source_recent_ohlc.get('trade_date')
            if trade_dates is not None and len(trade_dates) == window_size:
                source_recent_df = pd.DataFrame({
                    'open': source_recent_ohlc['open'],
                    'high': source_recent_ohlc['high'],
                    'low': source_recent_ohlc['low'],
                    'close': source_recent_ohlc['close'],
                    'volume': source_recent_ohlc.get('volume', [0] * window_size),
                    'trade_date': pd.to_datetime(trade_dates)
                })
                print(f"[调试] 股票 {code} 使用源股票真实日期: {source_recent_df['trade_date'].min()} 至 {source_recent_df['trade_date'].max()}")
            else:
                # 回退：使用假日期（仅用于无日期数据的兼容情况）
                source_recent_df = pd.DataFrame({
                    'open': source_recent_ohlc['open'],
                    'high': source_recent_ohlc['high'],
                    'low': source_recent_ohlc['low'],
                    'close': source_recent_ohlc['close'],
                    'volume': source_recent_ohlc.get('volume', [0] * window_size),
                    'trade_date': pd.date_range(end=pd.Timestamp.now(), periods=window_size, freq='D')
                })
                print(f"[警告] 股票 {code} 源股票缺少真实日期，使用假日期")
            current_df_for_period = source_recent_df
        else:
            # 如果没有提供源股票数据，使用候选股票自己的数据（兼容旧逻辑）
            current_df_for_period = transformed_df
        
        # ✅ 从params中获取源股票的预计算指标
        source_precomputed_indicators = params.get('source_precomputed_indicators')
        
        # ✅ 调试：打印源股票指标是否正确传递
        if source_precomputed_indicators is not None:
            macd_info = "非空" if 'macd' in source_precomputed_indicators and not source_precomputed_indicators['macd'].empty else "空"
            kdj_info = "非空" if 'kdj' in source_precomputed_indicators and not source_precomputed_indicators['kdj'].empty else "空"
            print(f"[调试] 股票 {code}: 源股票指标 MACD={macd_info}, KDJ={kdj_info}")
        else:
            print(f"[调试] 股票 {code}: 源股票指标为 None")
        
        # 查找相似模式
        patterns = _find_patterns_in_single_series(
            transformed_df,
            window_size=candidate_window_size,
            top_n=params['max_n'],
            future_days=candidate_future_days,
            period_type=params['period_type'],
            use_optimized=params.get('use_optimized', True),
            source_code=code,
            source_type=params['security_type'],
            current_df_for_period=current_df_for_period,  # ✅ 使用源股票的recent数据
            similarity_weights=params.get('similarity_weights'),
            precomputed_indicators=precomputed_indicators,  # 候选股票的指标
            source_precomputed_indicators=source_precomputed_indicators  # ✅ 源股票的指标
        )
        
        # ✅ 调试：确保返回的 patterns 都有正确的 source_code
        if patterns:
            for i, p in enumerate(patterns[:3]):  # 只检查前3个
                actual_source = p.get('source_code', 'UNKNOWN')
                if actual_source != code:
                    print(f"[警告] 股票 {code} 返回的模式[{i}] source_code={actual_source}，已修正为 {code}")
                    p['source_code'] = code
        
        return patterns
        
    except Exception as e:
        print(f"[并行处理] 处理股票 {code} 时出错: {e}")
        import traceback
        traceback.print_exc()
        return []


def _process_single_stock_with_data(args):
    """
    🚀 优化版：使用预加载数据的相似度搜索（用于多进程并行）
    
    与 _process_single_stock_for_parallel 的区别：
    - 接收预加载的数据，而不是从磁盘读取
    - 减少磁盘IO开销
    
    参数:
        args: tuple (code, params, df_dict)
            code: 股票代码
            params: dict 包含所有必要参数
            df_dict: dict 包含预加载的数据（numpy数组格式）
                - 'open', 'high', 'low', 'close', 'volume', 'trade_date'
    
    返回:
        list: 该股票的相似模式列表
    """
    code, params, df_dict = args
    
    try:
        import pandas as pd
        import numpy as np
        
        # 🚀 优化：从预加载的字典重建DataFrame（避免磁盘IO）
        if df_dict['close'] is None or len(df_dict['close']) == 0:
            return []
        
        # 构建DataFrame
        candidate_df = pd.DataFrame({
            'open': df_dict['open'] if df_dict['open'] is not None else df_dict['close'],
            'high': df_dict['high'] if df_dict['high'] is not None else df_dict['close'],
            'low': df_dict['low'] if df_dict['low'] is not None else df_dict['close'],
            'close': df_dict['close'],
            'volume': df_dict['volume'] if df_dict['volume'] is not None else np.zeros(len(df_dict['close'])),
            'trade_date': pd.to_datetime(df_dict['trade_date']) if df_dict['trade_date'] is not None else pd.date_range(end=pd.Timestamp.now(), periods=len(df_dict['close']), freq='D')
        })
        
        if candidate_df.empty:
            return []
        
        # 动态导入避免序列化问题
        import sys
        sys.path.insert(0, '.')
        
        # 数据转换
        transformed_df, candidate_window_size, candidate_future_days = _prepare_similarity_df(
            candidate_df,
            period_type=params['period_type'],
            window_size=params['window_size'],
            force_update=params.get('force_update', False),
            source_code=code,
            custom_future_days=params.get('custom_future_days')
        )
        
        # ✅ 从缓存读取预计算的技术指标
        precomputed_indicators = None
        try:
            cache = _get_indicator_cache()
            if cache is not None:
                precomputed_indicators = cache.get_indicators(
                    code=code,
                    df=candidate_df,
                    period_type=params['period_type'],
                    force_recompute=False
                )
                if precomputed_indicators:
                    print(f"[缓存] 股票 {code} 使用预计算指标")
        except Exception as e:
            print(f"[缓存] 读取股票 {code} 指标缓存失败: {e}")
        
        # ✅ 修复：使用源股票的recent数据作为比较基准
        source_recent_ohlc = params.get('source_recent_ohlc')
        if source_recent_ohlc is not None:
            window_size = candidate_window_size
            trade_dates = source_recent_ohlc.get('trade_date')
            if trade_dates is not None and len(trade_dates) == window_size:
                source_recent_df = pd.DataFrame({
                    'open': source_recent_ohlc['open'],
                    'high': source_recent_ohlc['high'],
                    'low': source_recent_ohlc['low'],
                    'close': source_recent_ohlc['close'],
                    'volume': source_recent_ohlc.get('volume', [0] * window_size),
                    'trade_date': pd.to_datetime(trade_dates)
                })
            else:
                source_recent_df = pd.DataFrame({
                    'open': source_recent_ohlc['open'],
                    'high': source_recent_ohlc['high'],
                    'low': source_recent_ohlc['low'],
                    'close': source_recent_ohlc['close'],
                    'volume': source_recent_ohlc.get('volume', [0] * window_size),
                    'trade_date': pd.date_range(end=pd.Timestamp.now(), periods=window_size, freq='D')
                })
            current_df_for_period = source_recent_df
        else:
            current_df_for_period = transformed_df
        
        # ✅ 从params中获取源股票的预计算指标
        source_precomputed_indicators = params.get('source_precomputed_indicators')
        
        # 查找相似模式
        patterns = _find_patterns_in_single_series(
            transformed_df,
            window_size=candidate_window_size,
            top_n=params['max_n'],
            future_days=candidate_future_days,
            period_type=params['period_type'],
            use_optimized=params.get('use_optimized', True),
            source_code=code,
            source_type=params['security_type'],
            current_df_for_period=current_df_for_period,
            similarity_weights=params.get('similarity_weights'),
            precomputed_indicators=precomputed_indicators,
            source_precomputed_indicators=source_precomputed_indicators
        )
        
        # ✅ 调试：确保返回的 patterns 都有正确的 source_code
        if patterns:
            for i, p in enumerate(patterns[:3]):
                actual_source = p.get('source_code', 'UNKNOWN')
                if actual_source != code:
                    print(f"[警告] 股票 {code} 返回的模式[{i}] source_code={actual_source}，已修正为 {code}")
                    p['source_code'] = code
        
        return patterns
        
    except Exception as e:
        print(f"[并行处理-预加载] 处理股票 {code} 时出错: {e}")
        import traceback
        traceback.print_exc()
        return []


# ==================== Numba加速函数 ====================

if NUMBA_AVAILABLE:
    @jit(nopython=True, cache=True, parallel=True)
    def _numba_pearson_batch(windows_norm, recent_norm, eps=1e-10):
        """
        Numba加速的批量皮尔逊相关系数计算
        
        参数:
            windows_norm: (n_windows, window_size) 归一化历史窗口
            recent_norm: (window_size,) 归一化目标窗口
            eps: 数值稳定性常数
        
        返回:
            corr: (n_windows,) 相关系数数组
        """
        n_windows = windows_norm.shape[0]
        window_size = windows_norm.shape[1]
        corr = np.empty(n_windows, dtype=np.float64)
        
        # 目标窗口的均值和方差
        recent_mean = 0.0
        for j in range(window_size):
            recent_mean += recent_norm[j]
        recent_mean /= window_size
        
        recent_var = 0.0
        for j in range(window_size):
            diff = recent_norm[j] - recent_mean
            recent_var += diff * diff
        recent_std = np.sqrt(recent_var) + eps
        
        # 并行计算每个窗口的相关系数
        for i in prange(n_windows):
            # 窗口均值
            win_mean = 0.0
            for j in range(window_size):
                win_mean += windows_norm[i, j]
            win_mean /= window_size
            
            # 协方差和方差
            cov = 0.0
            win_var = 0.0
            for j in range(window_size):
                win_diff = windows_norm[i, j] - win_mean
                recent_diff = recent_norm[j] - recent_mean
                cov += win_diff * recent_diff
                win_var += win_diff * win_diff
            
            win_std = np.sqrt(win_var) + eps
            corr[i] = cov / (win_std * recent_std)
            
            # 裁剪到[-1, 1]
            if corr[i] > 1.0:
                corr[i] = 1.0
            elif corr[i] < -1.0:
                corr[i] = -1.0
        
        return corr
    
    @jit(nopython=True, cache=True, parallel=True)
    def _numba_batch_similarity(windows_norm, recent_norm, weights=None):
        """
        Numba加速的批量相似度计算（综合指标）
        
        返回综合相似度分数，包含：
        - 皮尔逊相关系数 (50%)
        - 欧氏距离相似度 (30%)
        - 趋势斜率相似度 (20%)
        """
        n_windows = windows_norm.shape[0]
        window_size = windows_norm.shape[1]
        scores = np.empty(n_windows, dtype=np.float64)
        
        # 目标窗口统计
        recent_mean = 0.0
        for j in range(window_size):
            recent_mean += recent_norm[j]
        recent_mean /= window_size
        
        recent_var = 0.0
        for j in range(window_size):
            diff = recent_norm[j] - recent_mean
            recent_var += diff * diff
        recent_std = np.sqrt(recent_var) + 1e-10
        
        recent_slope = recent_norm[-1] - recent_norm[0]
        
        for i in prange(n_windows):
            # 1. 皮尔逊相关系数
            win_mean = 0.0
            for j in range(window_size):
                win_mean += windows_norm[i, j]
            win_mean /= window_size
            
            cov = 0.0
            win_var = 0.0
            for j in range(window_size):
                win_diff = windows_norm[i, j] - win_mean
                recent_diff = recent_norm[j] - recent_mean
                cov += win_diff * recent_diff
                win_var += win_diff * win_diff
            
            win_std = np.sqrt(win_var) + 1e-10
            corr = cov / (win_std * recent_std)
            corr = max(-1.0, min(1.0, corr))
            corr_score = (corr + 1.0) / 2.0
            
            # 2. 欧氏距离相似度
            dist_sq = 0.0
            for j in range(window_size):
                diff = windows_norm[i, j] - recent_norm[j]
                dist_sq += diff * diff
            dist = np.sqrt(dist_sq)
            dist_score = 1.0 / (1.0 + dist / max(1, window_size))
            
            # 3. 趋势斜率相似度
            win_slope = windows_norm[i, -1] - windows_norm[i, 0]
            slope_diff = abs(win_slope - recent_slope)
            slope_score = 1.0 - min(1.0, slope_diff / (abs(recent_slope) + 10.0))
            
            # 综合得分
            scores[i] = 0.50 * corr_score + 0.30 * dist_score + 0.20 * slope_score
        
        return scores
else:
    # Numba不可用时使用纯NumPy实现
    def _numba_pearson_batch(windows_norm, recent_norm, eps=1e-10):
        """纯NumPy回退实现"""
        w_centered = windows_norm - windows_norm.mean(axis=1, keepdims=True)
        r_centered = recent_norm - recent_norm.mean()
        w_norm = np.linalg.norm(w_centered, axis=1) + eps
        r_norm = np.linalg.norm(r_centered) + eps
        return np.clip((w_centered @ r_centered) / (w_norm * r_norm), -1.0, 1.0)
    
    def _numba_batch_similarity(windows_norm, recent_norm, weights=None):
        """纯NumPy回退实现"""
        corr = _numba_pearson_batch(windows_norm, recent_norm)
        corr_score = (corr + 1.0) / 2.0
        
        dist = np.linalg.norm(windows_norm - recent_norm[None, :], axis=1)
        dist_score = 1.0 / (1.0 + dist / max(1, windows_norm.shape[1]))
        
        slope_recent = recent_norm[-1] - recent_norm[0]
        slope_windows = windows_norm[:, -1] - windows_norm[:, 0]
        slope_score = 1.0 - np.minimum(1.0, np.abs(slope_windows - slope_recent) / (np.abs(slope_recent) + 10.0))
        
        return 0.50 * corr_score + 0.30 * dist_score + 0.20 * slope_score


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
    similarity_weights=None,
    start_date=None,
    end_date=None,
    custom_future_days=None,
    anchor_date=None,
    stock_type_filter=None,
    progress_callback=None,
    cancel_check=None
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
    - start_date: 开始日期（可选，用于时间范围筛选）
    - end_date: 结束日期（可选，用于时间范围筛选）
    - custom_future_days: 自定义预测周期数（可选，None表示使用默认值）
    - anchor_date: 锚点日期（可选，用于截取历史某段K线。指定后，将从该日期往前推window_size根K线作为匹配区间）
    - stock_type_filter: 股票类型筛选列表（可选，如 ['上证主板', '创业板']）
    
    技术点：
    - K线模式识别：通过滑动窗口方法识别历史K线形态
    - 多维度特征提取：价格、成交量、技术指标、形态特征
    - 自适应权重调整：根据市场波动率调整特征权重
    - 批量计算优化：提高计算效率
    - 多周期支持：支持日线、周线、月线数据的相似度计算
    - 可配置回测：支持自定义时间范围、匹配周期数和预测周期数
    - 锚点回测：支持截取历史某段K线进行分析
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
        source_code=current_code or 'current',
        start_date=start_date,
        end_date=end_date,
        custom_future_days=custom_future_days,
        anchor_date=anchor_date
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
        similarity_weights=similarity_weights,
        anchor_date=anchor_date
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

    # 股票类型筛选
    if stock_type_filter and candidate_codes:
        candidate_codes = [
            code for code in candidate_codes 
            if get_stock_category(code) in stock_type_filter
        ]
        print(f"[股票类型筛选] 从 {len(_dedupe_keep_order(candidate_codes))} 只中筛选出 {len(candidate_codes)} 只符合类型的股票")

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
        
        # 🚀 优化：检查是否有预加载服务
        preload_service = None
        if hasattr(st, 'session_state') and 'preload_service' in dir(st.session_state):
            try:
                preload_service = st.session_state.preload_service
                if preload_service and preload_service.is_loaded:
                    print(f"[预加载服务] 使用内存缓存，跳过磁盘读取")
            except:
                preload_service = None
    except Exception as e:
        print(f"跨标的时读取read_tdx_data失败: {e}")
        return candidate_patterns[:max_n]

    # 为每个候选标的计算匹配（当前标的已在前面收集）
    # 使用多进程并行处理，大幅提升跨标的搜索速度
    # 过滤掉当前标的（已在前面处理）
    codes_to_process = [
        code for code in filtered_codes 
        if not (normalized_current_code and code == normalized_current_code)
    ]
    
    if codes_to_process:
        # ✅ 修复：提取源股票的recent window数据作为比较基准
        # 获取源股票的最后window_size根K线数据
        source_recent_ohlc = None
        if source_period_df is not None and not source_period_df.empty:
            source_recent = source_period_df.tail(window_size)
            if all(col in source_recent.columns for col in ['open', 'high', 'low', 'close']):
                source_recent_ohlc = {
                    'open': source_recent['open'].values.astype(float),
                    'high': source_recent['high'].values.astype(float),
                    'low': source_recent['low'].values.astype(float),
                    'close': source_recent['close'].values.astype(float),
                    'volume': source_recent['volume'].values.astype(float) if 'volume' in source_recent.columns else np.zeros(window_size),
                    'trade_date': source_recent['trade_date'].values if 'trade_date' in source_recent.columns else None  # ✅ 添加真实日期
                }
        
        # ✅ 修复：获取源股票的预计算指标
        source_precomputed_indicators = None
        try:
            cache = _get_indicator_cache()
            if cache is not None and current_code:
                # 读取源股票的日线数据
                from tdx_reader import read_tdx_data
                source_df = read_tdx_data(code=current_code, security_type=security_type)
                if source_df is not None and not source_df.empty:
                    source_precomputed_indicators = cache.get_indicators(
                        code=current_code,
                        df=source_df,
                        period_type=period_type,
                        force_recompute=False
                    )
                    if source_precomputed_indicators:
                        print(f"[缓存] 源股票 {current_code} 预计算指标已加载")
        except Exception as e:
            print(f"[缓存] 读取源股票 {current_code} 指标失败: {e}")
        
# 🚀 优化：批量预加载所有候选股票数据
        print(f"[数据预加载] 开始加载 {len(codes_to_process)} 只股票...")
        preload_start = time.time()
        
        stock_data_cache = {}
        
        # 优先使用预加载服务（内存缓存）
        # 如果正在加载，等待最多30秒，否则直接使用后备加载
        if preload_service:
            if preload_service.is_loading and not preload_service.is_loaded:
                print(f"[预加载服务] 正在后台加载，等待30秒...")
                wait_start = time.time()
                while preload_service.is_loading and (time.time() - wait_start) < 30:
                    time.sleep(1)
                if preload_service.is_loaded:
                    print(f"[预加载服务] 预加载完成！")
                else:
                    print(f"[预加载服务] 未完成，使用后备加载")
        
        if preload_service and preload_service.is_loaded:
            print(f"[预加载服务] 从内存获取数据...")
            for code in codes_to_process:
                df = preload_service.get_stock_data(code, period='daily')
                # 时间范围过滤
                if df is not None and start_date is not None:
                    df = df[df['trade_date'] >= pd.to_datetime(start_date)]
                if df is not None and end_date is not None:
                    df = df[df['trade_date'] <= pd.to_datetime(end_date)]
                if df is not None and not df.empty:
                    stock_data_cache[code] = df
            
            preload_elapsed = time.time() - preload_start
            print(f"[预加载服务] 完成！从内存获取 {len(stock_data_cache)}/{len(codes_to_process)} 只股票，耗时 {preload_elapsed:.2f} 秒")
        else:
            # 后备：串行加载（避免多线程开销和竞争）
            print(f"[数据预加载] 串行加载 {len(codes_to_process)} 只股票...")
            loaded_count = 0
            
            for code in codes_to_process:
                try:
                    df = read_tdx_data(code=code, security_type=security_type)
                    if df is not None and not df.empty:
                        # 时间范围过滤
                        if start_date is not None:
                            df = df[df['trade_date'] >= pd.to_datetime(start_date)]
                        if end_date is not None:
                            df = df[df['trade_date'] <= pd.to_datetime(end_date)]
                        if not df.empty:
                            stock_data_cache[code] = df
                    
                    loaded_count += 1
                    
                    # 每50只显示进度
                    if loaded_count % 50 == 0:
                        elapsed = time.time() - preload_start
                        avg_time = elapsed / loaded_count if loaded_count > 0 else 0
                        remaining = (len(codes_to_process) - loaded_count) * avg_time
                        print(f"[数据预加载] 进度: {loaded_count}/{len(codes_to_process)} | 已用 {elapsed:.0f}秒 | 预计还需 {remaining/60:.1f}分钟")
                        
                except Exception as e:
                    print(f"[数据预加载] 股票 {code} 加载失败: {e}")
            
            preload_elapsed = time.time() - preload_start
            print(f"[数据预加载] 完成！成功加载 {len(stock_data_cache)}/{len(codes_to_process)} 只股票，耗时 {preload_elapsed:.2f} 秒")
        
        # 只处理成功加载数据的股票
        codes_with_data = list(stock_data_cache.keys())
        if not codes_with_data:
            print("[警告] 没有成功加载任何股票数据，跳过跨股票搜索")
            return candidate_patterns[:max_n]
        
        # 🚀 GPU加速选项：对纯形态相似度计算使用GPU
        use_gpu = False  # 默认关闭，可以通过参数启用
        
        def _is_pattern_weight_only(weights):
            """检查是否只使用形态权重（适合GPU加速）"""
            if not isinstance(weights, dict):
                return False
            pattern_w = float(weights.get('pattern', 0.0))
            others = (
                float(weights.get('trend', 0.0)) +
                float(weights.get('volume', 0.0)) +
                float(weights.get('ma', 0.0)) +
                float(weights.get('macd', 0.0)) +
                float(weights.get('kdj', 0.0)) +
                float(weights.get('regime', 0.0))
            )
            return pattern_w > 0 and others <= 1e-9
        
        pattern_only = _is_pattern_weight_only(similarity_weights)
        
        if use_gpu and pattern_only:
            try:
                from gpu_similarity import get_gpu_similarity_calculator
                gpu_calc = get_gpu_similarity_calculator(use_gpu=True)
                
                print(f"[GPU加速] 使用GPU批量计算相似度...")
                gpu_start = time.time()
                
                # GPU批量计算相似度
                gpu_results = gpu_calc.batch_compute_similarity_gpu(
                    {'close': source_period_df['close'].values[-params['window_size']:]},
                    stock_data_cache,
                    window_size=params['window_size']
                )
                
                gpu_elapsed = time.time() - gpu_start
                print(f"[GPU加速] 完成！耗时 {gpu_elapsed:.2f} 秒")
                
                # 将GPU结果转换为candidate_patterns格式
                for code, sim_score in sorted(gpu_results.items(), key=lambda x: x[1], reverse=True)[:max_candidate_codes * 2]:
                    if sim_score > 0.3:  # 过滤低相似度
                        df = stock_data_cache.get(code)
                        if df is not None:
                            # 简化结果，后续可补充详细分析
                            candidate_patterns.append({
                                'source_code': code,
                                'similarity': sim_score,
                                'start_date': df['trade_date'].iloc[-params['window_size']],
                                'end_date': df['trade_date'].iloc[-1],
                                'window_size': params['window_size']
                            })
                
                # 跳过CPU并行计算
                codes_with_data = []  # 清空，避免重复计算
                
            except Exception as e:
                print(f"[GPU加速] 失败，回退到CPU: {e}")
                use_gpu = False
        
        # 如果不使用GPU或GPU失败，使用CPU批量处理
        
        # 准备参数
        params = {
            'period_type': period_type,
            'window_size': window_size,
            'max_n': max_n,
            'force_update': force_update,
            'use_optimized': use_optimized,
            'security_type': security_type,
            'similarity_weights': similarity_weights,
            'start_date': start_date,
            'end_date': end_date,
            'custom_future_days': custom_future_days,  # ✅ 添加未来天数参数
            'source_recent_ohlc': source_recent_ohlc,  # ✅ 添加源股票的OHLC数据作为比较基准
            'source_precomputed_indicators': source_precomputed_indicators  # ✅ 添加源股票的预计算指标
        }
        
        # 🚀 优化：分批处理，减少进程创建开销
        # 每批处理N只股票，每批创建一次进程池
        batch_size = min(200, len(codes_with_data))  # 每批最多200只
        total_batches = (len(codes_with_data) + batch_size - 1) // batch_size
        
        print(f"[批量搜索] 将 {len(codes_with_data)} 只股票分为 {total_batches} 批处理，每批 {batch_size} 只")
        start_time = time.time()
        
        # 确定进程数
        n_workers = min(max(4, mp.cpu_count() - 1), 16)  # 4-16个进程
        
        completed_count = 0
        total_count = len(codes_with_data)
        
        # 分批处理
        for batch_idx in range(total_batches):
            # 检查是否请求停止搜索
            if cancel_check and cancel_check():
                print(f"[批量搜索] 用户请求停止搜索，在第 {batch_idx+1}/{total_batches} 批中断")
                st.warning("搜索已停止")
                break
            
            batch_start = batch_idx * batch_size
            batch_end = min(batch_start + batch_size, len(codes_with_data))
            batch_codes = codes_with_data[batch_start:batch_end]
            
            print(f"[批量搜索] 处理第 {batch_idx+1}/{total_batches} 批 ({len(batch_codes)} 只股票)...")
            batch_start_time = time.time()
            
            # 构建批次任务（包含预加载的数据）
            batch_tasks = []
            for code in batch_codes:
                try:
                    # 将预加载的数据序列化为pickle可传输的格式
                    df = stock_data_cache[code]
                    # 转换为简单的dict格式，减少序列化开销
                    df_dict = {
                        'open': df['open'].values if 'open' in df.columns else None,
                        'high': df['high'].values if 'high' in df.columns else None,
                        'low': df['low'].values if 'low' in df.columns else None,
                        'close': df['close'].values if 'close' in df.columns else None,
                        'volume': df['volume'].values if 'volume' in df.columns else None,
                        'trade_date': df['trade_date'].values if 'trade_date' in df.columns else None,
                    }
                    batch_tasks.append((code, params, df_dict))
                except Exception as e:
                    print(f"[批量搜索] 构建任务失败 {code}: {e}")
                    continue
            
            if not batch_tasks:
                print(f"[批量搜索] 第 {batch_idx+1}/{total_batches} 批无有效任务，跳过")
                continue
            
            print(f"[批量搜索] 第 {batch_idx+1}/{total_batches} 批串行处理 {len(batch_tasks)} 只股票...")
            
            # 串行处理（避免多线程开销）
            batch_success = 0
            batch_failed = 0
            
            for task in batch_tasks:
                code = task[0]
                
                # 检查是否请求停止搜索
                if cancel_check and cancel_check():
                    print(f"[批量搜索] 用户请求停止搜索...")
                    raise InterruptedError("用户停止搜索")
                
                try:
                    patterns = _process_single_stock_with_data(task)
                    if patterns:
                        candidate_patterns.extend(patterns)
                    batch_success += 1
                except Exception as e:
                    batch_failed += 1
                    if batch_failed <= 5:  # 只显示前5个错误
                        print(f"[批量搜索] 处理股票 {code} 时出错: {str(e)[:100]}")
                
                # 更新进度
                completed_count += 1
                
                if progress_callback and completed_count % 10 == 0:
                    elapsed = time.time() - start_time
                    avg_time_per_stock = elapsed / completed_count if completed_count > 0 else 0
                    remaining_stocks = total_count - completed_count
                    estimated_remaining = avg_time_per_stock * remaining_stocks
                    
                    progress_callback({
                        'completed': completed_count,
                        'total': total_count,
                        'percentage': completed_count / total_count * 100,
                        'elapsed': elapsed,
                        'estimated_remaining': estimated_remaining,
                        'avg_time_per_stock': avg_time_per_stock,
                        'current_code': code,
                        'batch': batch_idx + 1,
                        'total_batches': total_batches
                    })
            
            batch_elapsed = time.time() - batch_start_time
            print(f"[批量搜索] 第 {batch_idx+1}/{total_batches} 批完成，耗时 {batch_elapsed:.2f} 秒")
        
        elapsed = time.time() - start_time
        print(f"[批量搜索] 全部完成，总耗时 {elapsed:.2f} 秒，共找到 {len(candidate_patterns)} 个候选模式")

    candidate_patterns.sort(key=lambda item: float(item.get('similarity', 0.0)), reverse=True)

    # 去重：同一来源同一起始点只保留一个，且同一股票内的模式间隔至少一个窗口
    deduped = []
    seen_keys = set()
    # 记录每个股票已选中的模式位置，用于时间间隔过滤
    source_positions = {}  # {source_code: [start_idx1, start_idx2, ...]}
    
    for pattern in candidate_patterns:
        # 基本去重key
        key = (
            pattern.get('source_code', ''),
            str(pattern.get('start_date', '')),
            str(pattern.get('end_date', '')),
            round(float(pattern.get('similarity', 0.0)), 6)
        )
        if key in seen_keys:
            continue
        
        # 对同一股票内的模式进行时间间隔过滤
        source_code = pattern.get('source_code', '')
        pattern_start = pattern.get('start_date')
        
        if source_code and pattern_start is not None:
            # 将日期转换为位置索引（简化处理：通过日期差估算）
            if source_code not in source_positions:
                source_positions[source_code] = []
            
            # 检查与已选中的同股票模式是否间隔太近
            too_close = False
            for existing_start in source_positions[source_code]:
                # 计算交易日间隔（假设平均每月22个交易日）
                try:
                    days_diff = abs((pattern_start - existing_start).days)
                    trading_days_diff = days_diff * 22 / 30  # 转换为交易日
                    if trading_days_diff < (window_size * 0.5):  # 至少50%不重合
                        too_close = True
                        break
                except:
                    pass
            
            if too_close:
                continue
            
            source_positions[source_code].append(pattern_start)
        
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

def display_market_analysis(current_df, similar_patterns, future_dates=None, security_info=None, search_across_market=False):
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
        # 显示数据统计 - 所有周期统一显示120个
        if time_period == "日线":
            display_count = min(120, len(current_df))
            st.markdown(f"**显示**: {display_count}交易日")
        elif time_period == "周线":
            weekly_df = convert_to_weekly(current_df)
            display_count = min(120, len(weekly_df))
            st.markdown(f"**显示**: {display_count}周")
        else:  # 月线
            monthly_df = convert_to_monthly(current_df)
            display_count = min(120, len(monthly_df))
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
            ["MA5", "MA10", "MA20", "MA30", "MA60", "MA120"],
            default=["MA5", "MA10", "MA20", "MA30", "MA60"],
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
    
    # 计算动态显示限制（匹配周期数 + 预测周期数）
    custom_window = st.session_state.get('custom_window_size', 100)
    custom_future = st.session_state.get('custom_future_days', 100)
    max_display = custom_window + custom_future + 20  # 加20作为缓冲
    
    if similar_patterns and len(similar_patterns) >= 3:
        # 准备当前股票数据
        display_df_current, chart_title_current = prepare_chart_data(current_df, time_period, max_display)

        # 统一均线设置
        ma_windows = [int(x.replace("MA", "")) for x in _ma_settings] if _ma_settings else [5, 10, 20, 30, 60]

        # 检查是否需要自动降级从2x2到展开模式
        auto_degraded = False
        degrade_reason = ""

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
            # 高亮颜色用于标记相似片段（扩展为10个颜色）- 增加透明度使颜色更明显
            palette = [
                'rgba(255,0,0,0.55)', 'rgba(0,128,0,0.55)', 'rgba(0,0,255,0.55)',
                'rgba(255,128,0,0.55)', 'rgba(128,0,128,0.55)', 'rgba(0,128,128,0.55)',
                'rgba(255,0,128,0.55)', 'rgba(128,128,0,0.55)', 'rgba(0,255,128,0.55)', 'rgba(128,0,255,0.55)'
            ]
            
            # 第一行：当前股票K线 + TOP1 并排显示
            st.markdown("#### 📈 当前K线 vs TOP1 最相似历史模式")
            col_current, col_top1 = st.columns(2)
            
            # 左侧：当前股票K线
            with col_current:
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

# 在当前图中高亮"与历史模式对比的相似段落"（尾部窗口）
                current_match_len = 0
                if similar_patterns and similar_patterns[0].get('pattern_data') is not None:
                    pattern_for_len, _ = prepare_chart_data(similar_patterns[0]['pattern_data'].copy(), time_period, max_display)
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

                st.plotly_chart(fig_current, config={'displayModeBar': True})
                st.markdown('</div>', unsafe_allow_html=True)
            
            # 右侧：TOP1历史模式
            with col_top1:
                if len(similar_patterns) > 0:
                    pattern = similar_patterns[0]
                    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                    similarity_percent = f"{pattern['similarity']:.1%}"
                    
                    source_code = pattern.get('source_code', '')
                    source_name = pattern.get('source_name', '')
                    
                    if search_across_market and source_code:
                        if source_name and source_name != source_code:
                            title_text = f"TOP 1: {source_name}"
                        else:
                            title_text = f"TOP 1: {source_code}"
                    else:
                        title_text = "TOP 1 历史模式"
                    
                    st.markdown(f'<div style="font-size: 16px; margin-bottom: 8px;"><span style="background-color: #1f449c; color: white; padding: 3px 8px; border-radius: 3px;">{_safe_html(similarity_percent)}</span> <strong>{_safe_html(title_text)}</strong></div>', unsafe_allow_html=True)
                    
                    date_range = f"{pattern['start_date'].strftime('%Y-%m-%d')} 至 {pattern['end_date'].strftime('%Y-%m-%d')}"
                    st.markdown(f'<div style="color: #8b949e; font-size: 12px; margin-bottom: 8px;">{_safe_html(date_range)}</div>', unsafe_allow_html=True)

                    pattern_data_raw = pattern['pattern_data'].copy() if pattern['pattern_data'] is not None else None
                    future_data_raw = pattern['future_data'].copy() if pattern['future_data'] is not None and not pattern['future_data'].empty else pattern['future_data']
                    display_df_history, _ = prepare_chart_data(pattern_data_raw, time_period, max_display)

                    fig_history = plot_kline(
                        display_df_history,
                        "TOP1历史模式",
                        future_df=future_data_raw,
                        show_future_data=True,
                        show_volume=True,
                        show_indicators=show_indicators,
                        ma_windows=ma_windows
                    )
                    fig_history = apply_professional_style(fig_history, "TOP1历史模式")
                    history_segment_len = len(display_df_history)
                    if history_segment_len > 0:
                        add_similarity_segment_highlight(
                            fig_history,
                            x0=0,
                            x1=history_segment_len - 1,
                            color_fill=palette[0],
                            label_text='TOP1'
                        )
                    st.plotly_chart(fig_history, config={'displayModeBar': True})
                    st.markdown('</div>', unsafe_allow_html=True)

            # TOP2-10历史模式，左右2个显示
            if len(similar_patterns) > 1:
                st.markdown("#### TOP 2-10 历史模式（左右对比）")
                
                for i in range(1, min(10, len(similar_patterns)), 2):
                    col_left, col_right = st.columns(2)
                    
                    # 左侧模式
                    with col_left:
                        if i < len(similar_patterns):
                            pattern = similar_patterns[i]
                            st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                            similarity_percent = f"{pattern['similarity']:.1%}"
                            
                            source_code = pattern.get('source_code', '')
                            source_name = pattern.get('source_name', '')
                            
                            if search_across_market and source_code:
                                if source_name and source_name != source_code:
                                    title_text = f"TOP {i+1}: {source_name}"
                                else:
                                    title_text = f"TOP {i+1}: {source_code}"
                            else:
                                title_text = f"TOP {i+1}"
                            
                            st.markdown(f'<div style="font-size: 14px; margin-bottom: 8px;"><span style="background-color: #1f449c; color: white; padding: 3px 8px; border-radius: 3px;">{_safe_html(similarity_percent)}</span> <strong>{_safe_html(title_text)}</strong></div>', unsafe_allow_html=True)
                            
                            date_range = f"{pattern['start_date'].strftime('%Y-%m-%d')} 至 {pattern['end_date'].strftime('%Y-%m-%d')}"
                            st.markdown(f'<div style="color: #8b949e; font-size: 12px; margin-bottom: 8px;">{_safe_html(date_range)}</div>', unsafe_allow_html=True)

                            pattern_data_raw = pattern['pattern_data'].copy() if pattern['pattern_data'] is not None else None
                            future_data_raw = pattern['future_data'].copy() if pattern['future_data'] is not None and not pattern['future_data'].empty else pattern['future_data']

                            display_df_history, _ = prepare_chart_data(pattern_data_raw, time_period, max_display)
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
                                add_similarity_segment_highlight(
                                    fig_history,
                                    x0=0,
                                    x1=history_segment_len - 1,
                                    color_fill=palette[i % len(palette)],
                                    label_text=f'TOP{i+1}'
                                )
                            st.plotly_chart(fig_history, config={'displayModeBar': True})
                            st.markdown('</div>', unsafe_allow_html=True)
                    
                    # 右侧模式
                    with col_right:
                        if i + 1 < len(similar_patterns):
                            pattern = similar_patterns[i + 1]
                            st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                            similarity_percent = f"{pattern['similarity']:.1%}"
                            
                            source_code = pattern.get('source_code', '')
                            source_name = pattern.get('source_name', '')
                            
                            if search_across_market and source_code:
                                if source_name and source_name != source_code:
                                    title_text = f"TOP {i+2}: {source_name}"
                                else:
                                    title_text = f"TOP {i+2}: {source_code}"
                            else:
                                title_text = f"TOP {i+2}"
                            
                            st.markdown(f'<div style="font-size: 14px; margin-bottom: 8px;"><span style="background-color: #1f449c; color: white; padding: 3px 8px; border-radius: 3px;">{_safe_html(similarity_percent)}</span> <strong>{_safe_html(title_text)}</strong></div>', unsafe_allow_html=True)
                            
                            date_range = f"{pattern['start_date'].strftime('%Y-%m-%d')} 至 {pattern['end_date'].strftime('%Y-%m-%d')}"
                            st.markdown(f'<div style="color: #8b949e; font-size: 12px; margin-bottom: 8px;">{_safe_html(date_range)}</div>', unsafe_allow_html=True)

                            pattern_data_raw = pattern['pattern_data'].copy() if pattern['pattern_data'] is not None else None
                            future_data_raw = pattern['future_data'].copy() if pattern['future_data'] is not None and not pattern['future_data'].empty else pattern['future_data']

                            display_df_history, _ = prepare_chart_data(pattern_data_raw, time_period, max_display)
                            fig_history = plot_kline(
                                display_df_history,
                                f"历史模式 {i+2}",
                                future_df=future_data_raw,
                                show_future_data=True,
                                show_volume=True,
                                show_indicators=show_indicators,
                                ma_windows=ma_windows
                            )
                            fig_history = apply_professional_style(fig_history, f"历史模式 {i+2}")
                            history_segment_len = len(display_df_history)
                            if history_segment_len > 0:
                                add_similarity_segment_highlight(
                                    fig_history,
                                    x0=0,
                                    x1=history_segment_len - 1,
                                    color_fill=palette[(i+1) % len(palette)],
                                    label_text=f'TOP{i+2}'
                                )
                            st.plotly_chart(fig_history, config={'displayModeBar': True})
                            st.markdown('</div>', unsafe_allow_html=True)
            
            # 计算并显示上涨概率（放在最底部）
            up_count = 0
            total_patterns = min(10, len(similar_patterns))
            for i in range(total_patterns):
                pattern = similar_patterns[i]
                future_data = pattern.get('future_data')
                if future_data is not None and not future_data.empty and len(future_data) > 0:
                    pattern_end_price = pattern['pattern_data']['close'].iloc[-1] if pattern['pattern_data'] is not None and not pattern['pattern_data'].empty else 0
                    future_end_price = future_data['close'].iloc[-1]
                    if pattern_end_price > 0 and future_end_price > pattern_end_price:
                        up_count += 1
            
            up_probability = (up_count / total_patterns * 100) if total_patterns > 0 else 50
            color = '#ff4d4d' if up_probability > 50 else '#26a69a'
            trend_text = '偏多' if up_probability > 60 else '偏空' if up_probability < 40 else '震荡'
            
            # 显示上涨概率
            st.markdown(f"""
            <div style='background-color: #e8f4fd; padding: 20px; border-radius: 10px; margin: 30px 0; text-align: center; border: 2px solid #1f449c;'>
                <h3 style='color: #1f449c; margin: 0 0 15px 0;'>📊 基于TOP{total_patterns}相似样本走势评估</h3>
                <div style='font-size: 48px; font-weight: bold; color: {color}; margin: 15px 0;'>
                    上涨概率: {up_probability:.1f}%
                </div>
                <div style='color: #666; font-size: 16px; margin-top: 10px;'>
                    <strong>{up_count}/{total_patterns}</strong> 个样本后续上涨 | <strong style="color: {color};">{trend_text}</strong>
                </div>
            </div>
            """, unsafe_allow_html=True)

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
            st.plotly_chart(fig_current, config={'displayModeBar': True})

            # 相似模式按大图顺序展示（TOP1-10）
            for i in range(min(10, len(similar_patterns))):
                pattern = similar_patterns[i]
                st.markdown(f"### TOP {i+1} 历史模式（相似度 {pattern['similarity']:.1%}）")
                # 索引轴模式下只保留真实有效交易数据，禁止补齐休市空档
                pattern_data_raw = pattern['pattern_data'].copy() if pattern['pattern_data'] is not None else None
                future_data_raw = pattern['future_data'].copy() if pattern['future_data'] is not None and not pattern['future_data'].empty else pattern['future_data']

                display_df_history, _ = prepare_chart_data(pattern_data_raw, time_period, max_display)
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
                    palette = ['rgba(255,0,0,0.55)', 'rgba(0,128,0,0.55)', 'rgba(0,0,255,0.55)']
                    add_similarity_segment_highlight(
                        fig_history,
                        x0=0,
                        x1=history_segment_len - 1,
                        color_fill=palette[i % len(palette)],
                        label_text=f'TOP{i+1} 相似段'
                    )
                fig_history.update_layout(height=1100)
                st.plotly_chart(fig_history, config={'displayModeBar': True})
        
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
                st.plotly_chart(fig_aligned, config={'displayModeBar': True})
                
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
        display_df, chart_title = prepare_chart_data(current_df, time_period, max_display)
        ma_windows = [int(x.replace("MA", "")) for x in _ma_settings] if _ma_settings else [5, 10, 20, 30, 60]
        fig = plot_kline(display_df, chart_title, future_dates=future_dates, show_volume=True, show_indicators=show_indicators, ma_windows=ma_windows)
        fig = apply_professional_style(fig, "当前股票")
        st.plotly_chart(fig)
    
    return None

def prepare_chart_data(df, time_period, max_display=250):
    """根据时间周期准备图表数据，仅保留真实有效交易数据"""
    if time_period == "日线":
        # 日线不再补齐休市日期，避免索引轴下再次出现空白区间
        display_df = df.copy() if df is not None else df
        chart_title = "日K线"
        # 根据匹配周期数+预测周期数动态调整显示限制
        if len(display_df) > max_display:
            display_df = display_df.tail(max_display)
    elif time_period == "周线":
        display_df = convert_to_weekly(df)
        chart_title = "周K线"
        if len(display_df) > max_display:
            display_df = display_df.tail(max_display)
    else:  # 月线
        display_df = convert_to_monthly(df)
        chart_title = "月K线"
        if len(display_df) > max_display:
            display_df = display_df.tail(max_display)
    
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
            'orientation': 'h',  # 水平排列
            'yanchor': 'bottom',
            'y': -0.15,  # 放在图表下方
            'xanchor': 'center',
            'x': 0.5,  # 水平居中
            'bgcolor': 'rgba(255, 255, 255, 0.9)',
            'bordercolor': '#e0e0e0',
            'borderwidth': 1,
            'font': {'color': '#333333'}
        },
        margin=dict(l=40, r=40, t=60, b=80)  # 增加底部边距为图例留空间
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
    
    # 计算涨跌幅
    change_pct = []
    for i in range(len(valid_data)):
        if i == 0:
            change_pct.append(0.0)
        else:
            prev_close = valid_data['close'].iloc[i-1]
            curr_close = valid_data['close'].iloc[i]
            pct = ((curr_close - prev_close) / prev_close * 100) if prev_close != 0 else 0.0
            change_pct.append(pct)
    
    # 准备hover文本
    hover_text = []
    for i in range(len(valid_data)):
        date_str = valid_data['trade_date'].iloc[i].strftime('%Y-%m-%d') if hasattr(valid_data['trade_date'].iloc[i], 'strftime') else str(valid_data['trade_date'].iloc[i])
        text = f"时间: {date_str}<br>" + \
               f"开盘: {valid_data['open'].iloc[i]:.2f}<br>" + \
               f"最高: {valid_data['high'].iloc[i]:.2f}<br>" + \
               f"最低: {valid_data['low'].iloc[i]:.2f}<br>" + \
               f"收盘: {valid_data['close'].iloc[i]:.2f}<br>" + \
               f"成交量: {valid_data['volume'].iloc[i]/10000:.0f}万<br>" + \
               f"涨幅: {change_pct[i]:+.2f}%"
        hover_text.append(text)
    
    # 添加主K线 - A股颜色标准：上涨红色，下跌绿色
    # 使用索引轴：x=display_indices
    fig.add_trace(go.Candlestick(
        x=display_indices if use_index_axis else valid_data['trade_date'],
        open=valid_data['open'],
        high=valid_data['high'],
        low=valid_data['low'],
        close=valid_data['close'],
        name='K线',
        text=hover_text,
        hoverinfo='text',
        increasing=dict(line=dict(color='#ff4d4d'), fillcolor='#ff4d4d'),  # 上涨红色
        decreasing=dict(line=dict(color='#26a69a'), fillcolor='#26a69a')   # 下跌绿色
    ), row=price_row, col=1)
    
    # 叠加均线到K线主图
    if ma_windows is None:
        ma_windows = [5, 10, 20, 30, 60]
    ma_color_map = {
        5: '#fdd835',
        10: '#29b6f6',
        20: '#ab47bc',
        30: '#7e57c2',
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


# ==================== MACD底背离多进程扫描辅助函数 ====================

def _build_macd_df_from_cached(cached_macd, limit=None):
    """Build a lightweight MACD DataFrame from precomputed cache data."""
    if not cached_macd:
        return None

    required = ['DIF', 'DEA', 'MACD', 'close']
    if not all(key in cached_macd for key in required):
        return None

    lengths = [len(cached_macd[key]) for key in required if cached_macd.get(key) is not None]
    if len(lengths) != len(required):
        return None

    row_count = min(lengths)
    if row_count < 30:
        return None

    if limit is not None:
        row_count = min(row_count, int(limit))

    data = {
        key: np.asarray(cached_macd[key])[-row_count:]
        for key in required
    }

    trade_dates = cached_macd.get('trade_date')
    if trade_dates is not None and len(trade_dates) >= row_count:
        data['trade_date'] = list(trade_dates)[-row_count:]

    return pd.DataFrame(data)


def _scan_single_stock_macd(args):
    """
    多进程扫描单只股票的MACD底背离（必须在main函数外部定义）
    
    参数：
        args: (stock_dict, tdx_path, scan_mode, strict_mode, min_confidence, debug_mode, max_cross_days)
    
    返回：
        dict: {'type': 'divergence'|'cross'|None, 'data': result_data}
    """
    try:
        stock, tdx_path, scan_mode, strict_mode, min_confidence, debug_mode, max_cross_days = args
        
        # 导入必要的模块
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from tdx_reader import read_tdx_data, set_tdx_data_dir
        from macd_divergence_scanner import calculate_macd, detect_macd_divergence, detect_upcoming_golden_cross
        
        # 设置数据源路径
        set_tdx_data_dir(tdx_path)
        
        # 读取股票数据
        df = read_tdx_data(code=stock['code'], security_type='stock')
        
        if df is None or len(df) < 30:
            return None
        
        result_data = None
        result_type = None
        
        # 计算MACD
        df_macd = calculate_macd(df)
        if df_macd is None:
            return None
        
        # 扫描底背离
        if scan_mode in ["底背离形态", "全部扫描"]:
            result = detect_macd_divergence(df_macd, min_wave_length=3, max_cross_days=max_cross_days)
            if result:
                # 严格模式：必须已金叉；宽松模式：只要有底背离形态即可
                is_valid_result = result.is_valid if strict_mode else (result.confidence >= min_confidence)
                if is_valid_result and result.confidence >= min_confidence:
                    result.code = stock['code']
                    result.name = stock['name']
                    result_data = result
                    result_type = 'divergence'
        
        # 扫描即将金叉
        if scan_mode in ["即将金叉预测", "全部扫描"] and result_type is None:
            if detect_upcoming_golden_cross(df_macd):
                result_data = {
                    'code': stock['code'],
                    'name': stock['name'],
                    'type': stock['type'],
                    'close': df['close'].iloc[-1],
                    'dif': df_macd['DIF'].iloc[-1],
                    'dea': df_macd['DEA'].iloc[-1]
                }
                result_type = 'cross'
        
        if result_type:
            return {'type': result_type, 'data': result_data}
        return None
        
    except Exception as e:
        return None


def main():
    """
    主函数：实现金融数据分析系统的整体功能流程
    """
    # 确保pandas可用
    import pandas as pd
    
    # ========== 用户信息与数据源配置（侧边栏）==========
    with st.sidebar:
        # 用户信息区域 - 使用f-string避免转义问题
        current_user = st.session_state.get('current_user', '未知用户')
        user_role = '管理员' if current_user == DEFAULT_ADMIN["username"] else '普通用户'
        current_user_html = _safe_html(current_user)
        user_role_html = _safe_html(user_role)
        
        user_html = f'''
        <style>
        .user-section {{
            background: linear-gradient(135deg, rgba(212, 175, 55, 0.15) 0%, rgba(212, 175, 55, 0.05) 100%);
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 20px;
            border: 1px solid rgba(212, 175, 55, 0.3);
        }}
        .user-name {{
            color: #D4AF37;
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 5px;
        }}
        .user-role {{
            color: rgba(255, 255, 255, 0.6);
            font-size: 12px;
        }}
        </style>
        <div class="user-section">
            <div style="font-size: 24px; margin-bottom: 8px;">👤</div>
            <div class="user-name">{current_user_html}</div>
            <div class="user-role">{user_role_html}</div>
        </div>
        '''
        st.markdown(user_html, unsafe_allow_html=True)
        
        if st.button("🚪 退出登录", use_container_width=True):
            logout_user()
            st.rerun()
        
        st.markdown("---")
        st.markdown("### ⚙️ 数据源配置")
        
        # 默认路径
        default_tdx_path = r"D:\new_tdx\vipdoc"
        
        # 从 session_state 获取已保存的路径
        if 'tdx_data_path' not in st.session_state:
            st.session_state.tdx_data_path = default_tdx_path
        
        # 路径输入框
        tdx_path_input = st.text_input(
            "TDX 数据源路径",
            value=st.session_state.tdx_data_path,
            help="修改后按回车生效，如：D:\\new_tdx\\vipdoc",
            key="tdx_path_input"
        )
        
        # 如果路径改变，更新 session_state
        if tdx_path_input != st.session_state.tdx_data_path:
            st.session_state.tdx_data_path = tdx_path_input
            st.success(f"✅ 数据源已切换到：{tdx_path_input}")
        
        # 显示当前状态
        if os.path.exists(st.session_state.tdx_data_path):
            st.caption(f"✅ 当前数据源：{st.session_state.tdx_data_path}")
        else:
            st.caption(f"⚠️ 当前数据源：{st.session_state.tdx_data_path}（路径不存在）")
        
        # 快捷按钮
        col_path1, col_path2 = st.columns(2)
        with col_path1:
            if st.button("恢复默认", key="restore_default_path"):
                st.session_state.tdx_data_path = default_tdx_path
                # 同时更新TDX reader的路径
                from tdx_reader import set_tdx_data_dir
                set_tdx_data_dir(default_tdx_path)
                st.rerun()
        with col_path2:
            if st.button("测试路径", key="test_path_btn"):
                if os.path.exists(st.session_state.tdx_data_path):
                    st.success("✅ 路径有效！")
                else:
                    st.error("❌ 路径无效！")
        
        # 立即应用路径设置到TDX reader
        from tdx_reader import set_tdx_data_dir
        set_tdx_data_dir(st.session_state.tdx_data_path)
        
        # ========== 数据预加载服务已禁用 ==========
        # 改为使用后备加载，只加载用户选择的股票类型
        # 这样可以避免加载不需要的股票（ETF、可转债等）
        
        st.markdown("---")

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

    # 万向金枢 - 顶部导航栏（简洁版）
    st.markdown(
        '<div style="background: linear-gradient(90deg, #0a0a0f 0%, #1a1a2e 50%, #16213e 100%); '
        'padding: 12px 24px; margin-bottom: 20px; border-radius: 8px; '
        'border: 1px solid rgba(212, 175, 55, 0.3); display: flex; align-items: center; gap: 15px;">'
        '<span style="font-size: 28px;">🧭</span>'
        '<div style="display: flex; flex-direction: column;">'
        '<span style="font-size: 22px; font-weight: 700; color: #D4AF37; letter-spacing: 6px;">万向金枢</span>'
        '<span style="font-size: 10px; color: rgba(212, 175, 55, 0.7); letter-spacing: 3px;">UNIVERSAL GOLDEN HUB</span>'
        '</div>'
        '</div>',
        unsafe_allow_html=True
    )
    
    # 添加导航标签页
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(["证券搜索", "技术分析", "🤖 AI 智能体", "⭐ 我的自选股", "📋 历史回测验证", "📈 MACD底背离筛选", "🏦 筹码集中度", "💰 虚拟盘"])
    
    # 初始化自选股（从磁盘加载）
    if 'watchlist' not in st.session_state:
        st.session_state.watchlist = load_watchlist()
    
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
                        
                        # 显示详细类型和源文件
                        col4, col5 = st.columns([1, 2])
                        with col4:
                            # 使用新的详细类型分类
                            detailed_type = get_detailed_type(result['code'], result['type'])
                            st.metric("类型", detailed_type)
                        with col5:
                            source_file = result.get('source_file', '')
                            if source_file:
                                st.caption(f"📁 {source_file}")
                            else:
                                st.caption("📁 数据源: 本地TDX")
                        
# 添加分析按钮 - 使用唯一key，包含索引防止重复
                        if st.button(f"开始分析 {result['name']}", key=f"analyze_{i}_{result['code']}_{result['type']}"):
                            # 检测股票切换，清除旧的搜索缓存
                            old_security = st.session_state.get('selected_security')
                            if old_security and old_security.get('code') != result.get('code'):
                                # 股票切换，清除缓存
                                if 'similar_patterns_cache' in st.session_state:
                                    del st.session_state.similar_patterns_cache
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
            
# 显示证券标题和加入自选股按钮
            # 获取数据日期范围
            market_data_for_date = get_market_data(result['code'], result['type'], days=None)  # 获取所有可用数据
            data_date_range = ""
            if market_data_for_date is not None and not market_data_for_date.empty:
                earliest_date = market_data_for_date['trade_date'].min().strftime('%Y-%m-%d')
                latest_date = market_data_for_date['trade_date'].max().strftime('%Y-%m-%d')
                data_date_range = f"数据范围：{earliest_date} 至 {latest_date} ({len(market_data_for_date)} 根K线)"
            
            title_col, btn_col = st.columns([4, 1])
            with title_col:
                st.markdown(f"""
                <div style='background-color: #e8f4fd; padding: 15px; border-radius: 10px; margin-bottom: 20px;'>
                    <h3 style='color: #1f449c; margin: 0;'>
                        正在分析：{result['name']} ({result['code']}) - {result['type'].upper()}
                    </h3>
                    <div style='color: #666; font-size: 12px; margin-top: 5px;'>
                        {data_date_range}
                    </div>
                </div>
                """, unsafe_allow_html=True)
            with btn_col:
                st.markdown("<br>", unsafe_allow_html=True)
                # 检查是否已在自选股中
                is_in_watchlist = any(w['code'] == result['code'] and w['type'] == result['type'] 
                                     for w in st.session_state.watchlist)
                if is_in_watchlist:
                    if st.button("✅ 已在自选", key="add_to_watchlist_tab2", disabled=True):
                        pass
                else:
                    if st.button("➕ 加入自选", key="add_to_watchlist_tab2"):
                        st.session_state.watchlist.append({
                            'code': result['code'],
                            'name': result['name'],
                            'type': result['type'],
                            'exchange': result.get('exchange', '未知'),
                            'added_time': datetime.now().strftime('%Y-%m-%d %H:%M')
                        })
                        save_watchlist(st.session_state.watchlist)
                        st.success(f"已添加 {result['name']} 到自选股")
                        st.rerun()
            
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
                
                # 对比范围相关控制
                search_across_market = compare_scope == "不同股票共同参与对比"
                
                if search_across_market:
                    st.markdown("---")
                    
                    # 股票类型筛选
                    st.markdown("**📊 股票类型筛选**")
                    
                    # 获取可用股票代码并统计分类
                    try:
                        from tdx_reader import get_tdx_reader
                        tdx_path = st.session_state.get('tdx_data_path', r"D:\new_tdx\vipdoc")
                        tdx_reader = get_tdx_reader(tdx_path)
                        available_codes = tdx_reader.get_available_codes()
                        all_codes = list(available_codes.get('sh', [])) + list(available_codes.get('sz', []))
                        
                        # 统计各分类数量
                        category_counts = count_stocks_by_category(all_codes)
                        
                        # 股票类型选项（只保留需要匹配的类型）
                        stock_type_options = {
                            '上证主板': '上证主板',
                            '深证主板': '深证主板',
                            '创业板': '创业板',
                            '中小板': '中小板',
                            '科创板': '科创板'
                        }
                        
                        # 初始化选中状态（默认不选择任何类型，用户需要主动勾选）
                        if 'selected_stock_types' not in st.session_state:
                            st.session_state.selected_stock_types = []  # 默认空列表，不选择任何类型
                        
                        # 创建多列布局显示复选框
                        type_cols = st.columns(4)
                        selected_types = []
                        
                        for idx, (type_key, type_name) in enumerate(stock_type_options.items()):
                            with type_cols[idx % 4]:
                                count = category_counts.get(type_name, 0)
                                is_selected = type_name in st.session_state.selected_stock_types
                                
                                if st.checkbox(
                                    f"{type_name} ({count})",
                                    value=is_selected,
                                    key=f"stock_type_{type_name}"
                                ):
                                    selected_types.append(type_name)
                        
                        # 更新session_state
                        st.session_state.selected_stock_types = selected_types
                        
                        # 显示总计和提示
                        total_selected = sum(category_counts.get(t, 0) for t in selected_types)
                        total_all = sum(category_counts.values())
                        if len(selected_types) == 0:
                            st.warning("⚠️ 请选择要搜索的股票类型（勾选上方的类型）")
                        else:
                            st.caption(f"📈 已选择 {len(selected_types)} 个类型，共 **{total_selected}** 只股票")
                        
                    except Exception as e:
                        st.warning(f"无法加载股票类型统计: {str(e)}")
                        selected_types = ['上证主板', '深证主板', '创业板', '中小板', '科创板']
                        total_selected = 0
                else:
                    selected_types = []
                    total_selected = 0
                
                # 根据选中的股票类型计算候选数量（取消200限制）
                max_candidate_codes = total_selected if search_across_market and total_selected > 0 else 80
            
            # 分析数据时间范围选择
            with st.expander("📅 分析数据时间范围", expanded=False):
                st.markdown("**选择用于分析的历史数据时间范围**（默认使用全部数据）")
                
                # 获取数据范围用于限制日期选择器
                market_data_for_range = get_market_data(result['code'], result['type'], days=None)
                data_min_date = None
                data_max_date = None
                if market_data_for_range is not None and not market_data_for_range.empty:
                    data_min_date = market_data_for_range['trade_date'].min().date()
                    data_max_date = market_data_for_range['trade_date'].max().date()
                    st.caption(f"📊 可用数据范围：{data_min_date} 至 {data_max_date}")
                
                # 是否使用自定义时间范围
                if 'use_custom_time_range' not in st.session_state:
                    st.session_state.use_custom_time_range = False
                
                use_custom_time_range = st.checkbox(
                    "启用自定义时间范围",
                    value=st.session_state.use_custom_time_range,
                    help="勾选后可限定分析使用的历史数据范围，取消则使用全部数据"
                )
                st.session_state.use_custom_time_range = use_custom_time_range
                
                if use_custom_time_range:
                    col_range1, col_range2 = st.columns(2)
                    with col_range1:
                        analysis_start_date = st.date_input(
                            "开始日期",
                            value=data_min_date if data_min_date else None,
                            min_value=data_min_date if data_min_date else None,
                            max_value=data_max_date if data_max_date else None,
                            help="分析数据的起始日期"
                        )
                    with col_range2:
                        analysis_end_date = st.date_input(
                            "结束日期",
                            value=data_max_date if data_max_date else None,
                            min_value=data_min_date if data_min_date else None,
                            max_value=data_max_date if data_max_date else None,
                            help="分析数据的结束日期"
                        )
                    
                    # 验证日期范围
                    if analysis_start_date and analysis_end_date:
                        if analysis_start_date > analysis_end_date:
                            st.warning(f"⚠️ 开始日期 {analysis_start_date} 晚于结束日期 {analysis_end_date}，将自动调整")
                            analysis_start_date, analysis_end_date = analysis_end_date, analysis_start_date
                        
                        # 显示可用K线数量估算
                        if market_data_for_range is not None:
                            start_dt = pd.to_datetime(analysis_start_date)
                            end_dt = pd.to_datetime(analysis_end_date)
                            filtered_data = market_data_for_range[
                                (market_data_for_range['trade_date'] >= start_dt) &
                                (market_data_for_range['trade_date'] <= end_dt)
                            ]
                            st.info(f"📈 选定时间范围内包含 **{len(filtered_data)}** 根K线")
                else:
                    analysis_start_date = None
                    analysis_end_date = None
                    if data_min_date and data_max_date:
                        st.caption(f"📊 当前使用全部数据：{data_min_date} 至 {data_max_date}")
            
            # Store time range in session_state for use in analysis
            st.session_state.analysis_start_date = analysis_start_date if use_custom_time_range else None
            st.session_state.analysis_end_date = analysis_end_date if use_custom_time_range else None
            
            # 回测参数配置面板
            with st.expander("⚙️ 回测参数配置（可配置模式）", expanded=False):
                st.markdown("**匹配区间设置（截取历史某段K线进行回测）**")
                
                # 获取当前股票的数据范围用于日期选择器的限制
                market_data_for_config = get_market_data(result['code'], result['type'], days=365*5)
                min_date = None
                max_date = None
                if market_data_for_config is not None and not market_data_for_config.empty:
                    min_date = market_data_for_config['trade_date'].min().date()
                    max_date = market_data_for_config['trade_date'].max().date()
                
                col_date1, col_date2 = st.columns(2)
                with col_date1:
                    # 选择匹配区间的结束日期（即分析的锚点日期）
                    anchor_date = st.date_input(
                        "匹配区间结束日期（分析锚点）",
                        value=max_date if max_date else None,
                        min_value=min_date if min_date else None,
                        max_value=max_date if max_date else None,
                        help="选择要分析的历史K线段的结束日期（最近一个交易日）。系统将从该日期往前推N根K线作为匹配区间。"
                    )
                with col_date2:
                    # 根据周期类型设置默认值
                    default_window = {"日线": 100, "周线": 100, "月线": 100}.get(similarity_period, 100)
                    custom_window_size = st.number_input(
                        "匹配周期数（往前推N根K线）",
                        min_value=5,
                        max_value=100,
                        value=default_window,
                        step=1,
                        help=f"从结束日期往前推多少根K线作为匹配区间。默认：{100}根"
                    )
                
                st.markdown("**预测设置**")
                col_period1, col_period2 = st.columns(2)
                with col_period1:
                    # 根据周期类型设置默认值
                    default_future = {"日线": 100, "周线": 100, "月线": 100}.get(similarity_period, 100)
                    custom_future_days = st.number_input(
                        "预测周期数（未来天数）",
                        min_value=1,
                        max_value=100,
                        value=default_future,
                        step=1,
                        help=f"预测未来多少个周期的走势。默认：{100}个周期"
                    )
                with col_period2:
                    # 是否使用最近数据模式
                    # 关键修复：使用session_state来保持复选框状态，避免每次刷新重置
                    if 'use_latest_data' not in st.session_state:
                        st.session_state.use_latest_data = True
                    
                    use_latest_data = st.checkbox(
                        "使用最新数据",
                        value=st.session_state.use_latest_data,
                        help="勾选则使用最新的K线数据（忽略上面的结束日期设置）。取消勾选则使用上面选择的结束日期作为分析锚点。"
                    )
                    # 更新session_state
                    st.session_state.use_latest_data = use_latest_data
                
                # 计算并显示匹配区间
                if anchor_date and not use_latest_data:
                    if market_data_for_config is not None and not market_data_for_config.empty:
                        # 找到锚点日期在数据中的位置
                        anchor_datetime = pd.to_datetime(anchor_date)
                        data_before_anchor = market_data_for_config[market_data_for_config['trade_date'] <= anchor_datetime]
                        
                        if len(data_before_anchor) >= custom_window_size:
                            start_idx = len(data_before_anchor) - custom_window_size
                            match_start_date = data_before_anchor.iloc[start_idx]['trade_date'].strftime('%Y-%m-%d')
                            match_end_date = data_before_anchor.iloc[-1]['trade_date'].strftime('%Y-%m-%d')
                            
                            st.info(f"📊 当前配置：匹配 **{match_start_date}** 至 **{match_end_date}** 的 **{custom_window_size}** 根{similarity_period}K线，" +
                                   f"预测未来 **{custom_future_days}** 个{similarity_period}的走势")
                        else:
                            st.warning(f"⚠️ 警告：从 {anchor_date} 往前推 {custom_window_size} 根K线超出了数据范围，" +
                                      f"该日期前只有 {len(data_before_anchor)} 根K线")
                else:
                    st.info(f"📊 当前配置：匹配 **最近{custom_window_size}** 个{similarity_period}，" +
                           f"预测未来 **{custom_future_days}** 个{similarity_period}的走势")
            
            # 将配置参数存储到session_state
            # 计算实际的起始和结束日期
            if use_latest_data or anchor_date is None:
                # 使用最新数据模式
                st.session_state.backtest_start_date = None
                st.session_state.backtest_end_date = None
                st.session_state.anchor_date = None
            else:
                # 使用锚点日期模式
                anchor_datetime = pd.to_datetime(anchor_date)
                st.session_state.anchor_date = anchor_datetime
                # 结束日期就是锚点日期
                st.session_state.backtest_end_date = anchor_datetime
                # 起始日期会通过window_size在后续逻辑中计算
                st.session_state.backtest_start_date = None
            
            st.session_state.custom_window_size = custom_window_size
            st.session_state.custom_future_days = custom_future_days
            st.session_state.use_latest_data = use_latest_data

# 可调权重面板（形态/趋势/量能/MA）
            default_similarity_weights = {
                'trend': 20,
                'volume': 0,
                'pattern': 80,
                'ma': 0,
            }
            if 'similarity_weights' not in st.session_state:
                st.session_state.similarity_weights = default_similarity_weights.copy()
            # 向后兼容：如果旧配置没有MA权重，添加默认值
            if 'ma' not in st.session_state.similarity_weights:
                st.session_state.similarity_weights['ma'] = 0
            
            # 保存的配置方案列表
            if 'saved_configurations' not in st.session_state:
                st.session_state.saved_configurations = []
            
            # 当前配置是否已保存的标志
            if 'config_saved' not in st.session_state:
                st.session_state.config_saved = False

            with st.expander("🎛️ 可调权重面板（趋势/量能/形态/MA）"):
                # 使用session_state直接控制滑块值，避免无限刷新
                # 初始化权重到session_state
                for key in ['trend', 'volume', 'pattern', 'ma']:
                    slider_key = f'weight_slider_{key}'
                    if slider_key not in st.session_state:
                        st.session_state[slider_key] = st.session_state.similarity_weights.get(key, 0)
                
                # 权重动态约束回调函数
                def on_weight_change(changed_key):
                    """当某个权重变化时，自动调整其他权重保持总和为100"""
                    # 获取所有当前值
                    current_values = {
                        'trend': st.session_state.weight_slider_trend,
                        'volume': st.session_state.weight_slider_volume,
                        'pattern': st.session_state.weight_slider_pattern,
                        'ma': st.session_state.weight_slider_ma,
                    }
                    
                    total = sum(current_values.values())
                    if total == 0:
                        # 如果全部归零，恢复默认值
                        st.session_state.similarity_weights = {'trend': 20, 'volume': 0, 'pattern': 80, 'ma': 0}
                        for k, v in st.session_state.similarity_weights.items():
                            st.session_state[f'weight_slider_{k}'] = v
                        return
                    
                    if total == 100:
                        # 总和正好100，直接保存
                        st.session_state.similarity_weights = current_values.copy()
                        return
                    
                    # 需要调整其他权重
                    new_value = current_values[changed_key]
                    other_keys = [k for k in current_values if k != changed_key]
                    other_total = sum(current_values[k] for k in other_keys)
                    
                    if other_total == 0 and new_value < 100:
                        # 其他都是0，将剩余值平均分配
                        remaining = 100 - new_value
                        per_key = remaining // len(other_keys)
                        for key in other_keys:
                            current_values[key] = per_key
                        # 处理余数
                        remainder = 100 - sum(current_values.values())
                        if remainder > 0 and other_keys:
                            current_values[other_keys[0]] += remainder
                    elif other_total > 0:
                        # 按比例调整其他权重
                        remaining = 100 - new_value
                        for key in other_keys:
                            proportion = current_values[key] / other_total
                            current_values[key] = max(0, int(remaining * proportion))
                        
                        # 确保总和为100
                        current_sum = sum(current_values.values())
                        if current_sum != 100:
                            diff = 100 - current_sum
                            for key in other_keys:
                                if current_values[key] + diff >= 0:
                                    current_values[key] += diff
                                    break
                    
                    # 更新session_state
                    st.session_state.similarity_weights = current_values.copy()
                    for k, v in current_values.items():
                        st.session_state[f'weight_slider_{k}'] = v
                
                wcol1, wcol2 = st.columns(2)
                with wcol1:
                    st.slider("趋势权重", 0, 100, key='weight_slider_trend',
                             on_change=on_weight_change, args=('trend',))
                    st.slider("量能权重", 0, 100, key='weight_slider_volume',
                             on_change=on_weight_change, args=('volume',))
                with wcol2:
                    st.slider("K线形态权重", 0, 100, key='weight_slider_pattern',
                             on_change=on_weight_change, args=('pattern',))
                    st.slider("MA均线权重", 0, 100, key='weight_slider_ma',
                             on_change=on_weight_change, args=('ma',))
                
                # 从滑块同步到similarity_weights（用于初始化和备份）
                st.session_state.similarity_weights = {
                    'trend': st.session_state.weight_slider_trend,
                    'volume': st.session_state.weight_slider_volume,
                    'pattern': st.session_state.weight_slider_pattern,
                    'ma': st.session_state.weight_slider_ma,
                }

                total_weight = sum(st.session_state.similarity_weights.values())
                st.caption(f"当前权重总和: {total_weight}（已锁定为100）")

                # 方案管理按钮
                btn_col1, btn_col2, btn_col3 = st.columns(3)
                with btn_col1:
                    if st.button("💾 保存方案", help="保存当前配置方案"):
                        config_name = f"方案{len(st.session_state.saved_configurations) + 1}"
                        saved_config = {
                            'name': config_name,
                            'weights': st.session_state.similarity_weights.copy(),
                            'window_size': st.session_state.get('custom_window_size', 30),
                            'future_days': st.session_state.get('custom_future_days', 20),
                            'period': similarity_period
                        }
                        st.session_state.saved_configurations.append(saved_config)
                        st.session_state.config_saved = True
                        st.success(f"已保存: {config_name}")
                
                with btn_col2:
                    if st.button("🗑️ 清空方案", help="清空所有保存的方案"):
                        st.session_state.saved_configurations = []
                        st.session_state.config_saved = False
                        st.info("已清空所有方案")
                
                with btn_col3:
                    # 显示已保存的方案数量
                    st.caption(f"已保存方案: {len(st.session_state.saved_configurations)}个")
                
                # 显示已保存的方案列表
                if st.session_state.saved_configurations:
                    with st.container():
                        st.markdown("**已保存方案列表**")
                        for i, config in enumerate(st.session_state.saved_configurations):
                            col_name, col_load, col_del = st.columns([3, 1, 1])
                            with col_name:
                                st.text(f"{config['name']} - 窗口:{config['window_size']} 预测:{config['future_days']}")
                            with col_load:
                                if st.button("加载", key=f"load_config_{i}"):
                                    # 更新权重配置
                                    st.session_state.similarity_weights = config['weights'].copy()
                                    # 同步到滑块
                                    for k, v in config['weights'].items():
                                        st.session_state[f'weight_slider_{k}'] = v
                                    st.session_state.custom_window_size = config['window_size']
                                    st.session_state.custom_future_days = config['future_days']
                                    st.success(f"已加载: {config['name']}")
                                    st.rerun()  # 重新渲染以更新滑块显示
                            with col_del:
                                if st.button("删除", key=f"del_config_{i}"):
                                    st.session_state.saved_configurations.pop(i)
                                    st.rerun()
            
            # 周/月K线缓存控制
            if similarity_period in ["周线", "月线"]:
                col_cache1, col_cache2 = st.columns([1, 3])
                with col_cache1:
                    # 显示缓存目录状态
                    cache_dir = "./kline_cache"
                    if os.path.exists(cache_dir):
                        cache_files = [f for f in os.listdir(cache_dir) if f.endswith('.json')]
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
            
# 重新搜索按钮 - 防止参数变化自动触发搜索
            col_search1, col_search2, col_search3 = st.columns([2, 1, 1])
            with col_search1:
                st.caption("调整参数后，点击'重新搜索'执行分析")
            with col_search2:
                do_search = st.button("🔍 重新搜索", type="primary", help="使用当前参数执行相似度搜索")
            with col_search3:
                # 标记是否需要进行搜索
                st.session_state.need_search = do_search
            
# 获取市场数据
            try:
                with st.spinner('正在加载市场数据...'):
                    # 获取所有可用数据
                    market_data = get_market_data(result['code'], result['type'], days=None)
                
                if market_data is None or market_data.empty:
                    st.error(f"无法获取 {result['name']} 的市场数据。请检查：")
                    st.info("1. 确保TDX数据目录配置正确")
                    st.info("2. 检查证券代码是否正确")
                    st.info("3. 尝试重新搜索并选择证券")
                    return
                
                # 应用时间范围过滤
                analysis_start = st.session_state.get('analysis_start_date')
                analysis_end = st.session_state.get('analysis_end_date')
                if analysis_start or analysis_end:
                    original_len = len(market_data)
                    if analysis_start:
                        market_data = market_data[market_data['trade_date'] >= pd.to_datetime(analysis_start)]
                    if analysis_end:
                        market_data = market_data[market_data['trade_date'] <= pd.to_datetime(analysis_end)]
                    filtered_len = len(market_data)
                    st.info(f"📅 已应用时间范围过滤：{analysis_start or '最早'} 至 {analysis_end or '最新'}，共 {filtered_len} 根K线（原始数据 {original_len} 根）")
                    
                    if market_data.empty:
                        st.error("筛选时间范围内没有数据，请调整时间范围")
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
                # 搜索触发逻辑：
                # 1. 只有用户点击"重新搜索"按钮时才触发搜索
                # 2. 参数修改不会自动触发搜索
                # 3. TAB1"开始分析"只是选择股票，不触发搜索
                
                # 初始化搜索状态
                if 'similar_patterns_cache' not in st.session_state:
                    st.session_state.similar_patterns_cache = None
                
                # 获取当前搜索状态
                need_search = st.session_state.get('need_search', False)
                
                # 执行搜索的唯一条件：用户点击了"重新搜索"按钮
                if need_search:
                    try:
                        # 搜索开始时重置停止标志
                        st.session_state.stop_search_flag = False
                        
                        # 检查股票类型选择
                        selected_types = st.session_state.get('selected_stock_types', [])
                        if search_across_market and not selected_types:
                            st.error("❌ 请先选择要搜索的股票类型（如科创板、创业板等）")
                            st.session_state.need_search = False
                            return
                        
                        # 根据选择的周期类型设置参数
                        period_type_map = {
                            "日线": "daily",
                            "周线": "weekly", 
                            "月线": "monthly"
                        }
                        period_type = period_type_map.get(similarity_period, "daily")
                        
                        # 检查是否需要强制更新缓存
                        force_update = st.session_state.get('force_cache_update', False)
                        
                        # 获取股票名称映射（用于跨股票比较结果显示）
                        stock_name_map = get_securities_name_map() if search_across_market else {}
                        
                        # 判断是否使用锚点日期模式
                        use_latest = st.session_state.get('use_latest_data', True)
                        anchor_date = None if use_latest else st.session_state.get('anchor_date')
                        
                        # 调试信息：显示当前使用的模式
                        if use_latest:
                            st.info(f"📊 使用最新数据模式 - 匹配最近{st.session_state.get('custom_window_size', 30)}个{similarity_period}")
                        else:
                            st.info(f"📊 使用历史锚点模式 - 匹配至 {anchor_date.strftime('%Y-%m-%d') if anchor_date else '未设置'} 的K线")
                        
                        # 创建进度条容器（仅在跨股票搜索时显示）
                        progress_container = st.container()
                        progress_bar = None
                        progress_text = None
                        stop_search_button = None
                        
                        # 初始化停止搜索标志（只在未设置时初始化）
                        if 'stop_search_flag' not in st.session_state:
                            st.session_state.stop_search_flag = False
                        
                        if search_across_market:
                            with progress_container:
                                st.markdown("**🔍 正在搜索相似K线形态**")
                                progress_col1, progress_col2 = st.columns([4, 1])
                                with progress_col1:
                                    progress_bar = st.progress(0)
                                    progress_text = st.empty()
                                with progress_col2:
                                    # 停止搜索按钮
                                    if st.button("⏹️ 停止搜索", type="secondary", key="stop_search_btn"):
                                        st.session_state.stop_search_flag = True
                                        st.warning("正在停止搜索...")
                        
                        # 定义进度回调函数（带取消检查）
                        def update_progress(progress_info):
                            if progress_bar is not None and progress_text is not None:
                                percentage = progress_info['percentage']
                                completed = progress_info['completed']
                                total = progress_info['total']
                                elapsed = progress_info['elapsed']
                                estimated_remaining = progress_info['estimated_remaining']
                                current_code = progress_info.get('current_code', '')
                                
                                # 更新进度条
                                progress_bar.progress(min(percentage / 100, 1.0))
                                
                                # 格式化时间显示
                                elapsed_str = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒" if elapsed >= 60 else f"{int(elapsed)}秒"
                                remaining_str = f"{int(estimated_remaining // 60)}分{int(estimated_remaining % 60)}秒" if estimated_remaining >= 60 else f"{int(estimated_remaining)}秒"
                                
                                # 更新进度文本
                                progress_text.markdown(
                                    f"⏱️ **进度**: {completed}/{total} ({percentage:.1f}%) | "
                                    f"🕐 **已用**: {elapsed_str} | "
                                    f"⏳ **剩余**: {remaining_str} | "
                                    f"📈 **当前**: {current_code}"
                                )
                        
                        # 调试：确认实际使用的窗口大小
                        actual_window_size = st.session_state.get('custom_window_size', 30)
                        print(f"[调试] 匹配周期数设置: {actual_window_size}")
                        
                        with st.spinner(f'正在分析{similarity_period}相似K线形态（窗口大小: {actual_window_size}）...'):
                            # 定义取消检查函数
                            def check_cancel():
                                return st.session_state.get('stop_search_flag', False)
                            
                            similar_patterns = find_similar_patterns(
                                market_data, 
                                window_size=actual_window_size, 
                                top_n=10, 
                                use_optimized=True,
                                period_type=period_type,
                                search_across_market=search_across_market,
                                max_candidate_codes=max_candidate_codes,
                                current_code=result['code'],
                                security_type=result['type'],
                                force_update=force_update,
                                similarity_weights=st.session_state.get('similarity_weights', default_similarity_weights),
                                start_date=st.session_state.get('backtest_start_date'),
                                end_date=st.session_state.get('backtest_end_date'),
                                custom_future_days=st.session_state.get('custom_future_days'),
                                anchor_date=anchor_date,
                                stock_type_filter=st.session_state.get('selected_stock_types') if search_across_market else None,
                                progress_callback=update_progress if search_across_market else None,
                                cancel_check=check_cancel if search_across_market else None
                            )
                        
                        # 搜索完成后清除进度显示
                        if progress_container is not None:
                            progress_container.empty()
                        
                        # 为跨股票比较结果添加股票名称
                        if search_across_market and similar_patterns:
                            for pattern in similar_patterns:
                                source_code = pattern.get('source_code', '')
                                source_name = get_stock_name_by_code(source_code, stock_name_map)
                                pattern['source_name'] = source_name
                        
                        # 缓存搜索结果
                        st.session_state.similar_patterns_cache = similar_patterns
                        st.session_state.need_search = False  # 搜索完成后重置标志
                        st.session_state.stop_search_flag = False  # 重置停止标志
                        
                        # 重置强制更新标志
                        if force_update:
                            st.session_state.force_cache_update = False
                        
                        if not similar_patterns:
                            st.warning("未找到足够相似的K线形态进行分析")
                            st.info("这可能是因为：")
                            st.info("1. 该证券的历史数据较短")
                            st.info("2. 当前K线形态比较独特")
                            st.info("3. 可以尝试分析其他时间周期")
                    
                    except InterruptedError:
                        # 用户手动停止搜索
                        st.warning("⚠️ 搜索已被用户停止")
                        if progress_container is not None:
                            progress_container.empty()
                        st.session_state.need_search = False
                        st.session_state.stop_search_flag = False  # 重置停止标志
                        similar_patterns = st.session_state.get('similar_patterns_cache', [])
                    except Exception as e:
                        st.error(f"分析相似K线形态时出错: {str(e)}")
                        st.info("请尝试重新搜索并选择证券")
                        st.session_state.stop_search_flag = False  # 重置停止标志
                        similar_patterns = []
                else:
                    # 使用缓存的结果
                    similar_patterns = st.session_state.get('similar_patterns_cache', [])
                    
                    # 如果没有缓存结果，提示用户点击"重新搜索"
                    if not similar_patterns and st.session_state.get('similar_patterns_cache') is None:
                        st.info("👆 请点击上方「重新搜索」按钮开始分析相似K线形态")
                
                # 显示市场分析（专业4格布局）
                try:
                    # 如果使用锚点日期模式，截取对应的K线数据用于显示
                    display_df = market_data.copy()
                    anchor_date_display = st.session_state.get('anchor_date')
                    custom_window = st.session_state.get('custom_window_size', 30)
                    
                    if anchor_date_display is not None and not st.session_state.get('use_latest_data', True):
                        # 锚点日期模式：截取锚点日期前window_size根K线作为当前显示
                        anchor_dt = pd.to_datetime(anchor_date_display)
                        df_before_anchor = display_df[display_df['trade_date'] <= anchor_dt].copy()
                        if len(df_before_anchor) >= custom_window:
                            display_df = df_before_anchor.tail(custom_window).copy()
                            print(f"[显示] 使用锚点日期模式，显示 {anchor_date_display.strftime('%Y-%m-%d')} 前 {custom_window} 根K线")
                        else:
                            print(f"[显示警告] 锚点日期前数据不足：{len(df_before_anchor)} < {custom_window}")
                    
                    holding_stats = display_market_analysis(display_df, similar_patterns, future_dates, security_info=result, search_across_market=search_across_market)
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
        # ═══════════════════════════════════════════════════════
        #  TAB3: AI Agent 智能体 — 自主决策循环中枢
        # ═══════════════════════════════════════════════════════
        from ai_agent import AIAgent, LLMClient, MODEL_REGISTRY, RISK_RULES, AgentMemory, ToolExecutor

        # ── 初始化 session_state ──
        if "ai_agent" not in st.session_state:
            st.session_state.ai_agent = None
        if "agent_messages" not in st.session_state:
            st.session_state.agent_messages = []
        if "agent_running" not in st.session_state:
            st.session_state.agent_running = False
        if "agent_api_key" not in st.session_state:
            st.session_state.agent_api_key = ""  # 保留兼容旧代码
        if "agent_api_keys" not in st.session_state:
            st.session_state.agent_api_keys = {}  # {provider_name: api_key}  ← 新设计
        if "agent_model" not in st.session_state:
            st.session_state.agent_model = "deepseek-v4-pro"
        if "agent_connected" not in st.session_state:
            st.session_state.agent_connected = False
        if "agent_conn_msg" not in st.session_state:
            st.session_state.agent_conn_msg = ""
        if "agent_input_counter" not in st.session_state:
            st.session_state.agent_input_counter = 0
        if "agent_custom_provider" not in st.session_state:
            st.session_state.agent_custom_provider = "自定义"
        if "agent_custom_base_url" not in st.session_state:
            st.session_state.agent_custom_base_url = "http://localhost:11434/v1"
        if "agent_custom_model_id" not in st.session_state:
            st.session_state.agent_custom_model_id = "qwen2.5:7b"
        if "agent_fast_mode" not in st.session_state:
            st.session_state.agent_fast_mode = True
        if "agent_max_iterations" not in st.session_state:
            st.session_state.agent_max_iterations = 6
        if "agent_auto_universe" not in st.session_state:
            st.session_state.agent_auto_universe = "自选股优先+全市场抽样"
        if "agent_auto_max_candidates" not in st.session_state:
            st.session_state.agent_auto_max_candidates = 120
        if "agent_auto_top_n" not in st.session_state:
            st.session_state.agent_auto_top_n = 8
        if "agent_auto_add_watchlist" not in st.session_state:
            st.session_state.agent_auto_add_watchlist = False

        user_input = ""
        send_btn = False
        custom_model_option = "自定义 OpenAI 兼容"
        quick_prompts = {
            "📊 市场扫描": "分析今天市场整体走势，先调用本地自主选股工具找出3-5只最值得关注的股票，再说明理由",
            "🔍 自主选股": "请调用 auto_select_stocks 做一次自选股优先+全市场抽样选股，然后对Top候选给出后续分析计划",
            "📈 持仓诊断": "检查当前所有持仓的止损止盈情况，是否需要调整",
            "📋 盘后复盘": "复盘今天的所有操作，总结经验教训，并调用 save_learning 保存可执行经验",
        }

        # ── 对话优先布局：视觉顺序为 主对话 / 控制台 / 数据看板 ──
        agent_col2, agent_col1, agent_col3 = st.columns([3.35, 1.25, 1.05], gap="large")

        # ═════════════════════════════════════
        #  中栏：配置与本地选股控制台
        # ═════════════════════════════════════
        with agent_col1:
            st.markdown("""
            <div style='background: linear-gradient(135deg, #1a237e 0%, #283593 100%); color: white; padding: 14px; border-radius: 10px; margin-bottom: 12px;'>
                <h4 style='margin:0; color: white;'>🤖 AI 智能体</h4>
                <p style='margin:5px 0 0 0; font-size:0.85em; opacity:0.8;'>配置 · 本地选股 · 风控</p>
            </div>
            """, unsafe_allow_html=True)

            # ── 第1步：选择模型服务 ──
            st.markdown("#### ① 选择服务与模型")
            model_names = list(MODEL_REGISTRY.keys()) + [custom_model_option]
            current_model_idx = model_names.index(st.session_state.agent_model) if st.session_state.agent_model in model_names else 0

            # 按 provider 分组显示
            def _format_model_label(name):
                if name == custom_model_option:
                    return "自定义 OpenAI 兼容  [Custom]  — 填写Base URL和模型ID"
                cfg = MODEL_REGISTRY.get(name, {})
                provider = cfg.get("provider_name", "?")
                desc = cfg.get("description", "")
                return f"{name}  [{provider}]  — {desc}"

            selected_model = st.selectbox(
                "模型",
                options=model_names,
                index=current_model_idx,
                format_func=_format_model_label,
                key="agent_model_select",
            )
            # 模型切换时更新
            if selected_model != st.session_state.agent_model:
                st.session_state.agent_model = selected_model
                st.session_state.agent_connected = False
                st.session_state.agent_conn_msg = ""

            # 显示当前服务信息
            if selected_model == custom_model_option:
                custom_provider_input = st.text_input(
                    "服务商名称",
                    value=st.session_state.agent_custom_provider,
                    key="agent_custom_provider_widget",
                    help="用于区分不同服务商的API Key",
                )
                custom_base_input = st.text_input(
                    "Base URL",
                    value=st.session_state.agent_custom_base_url,
                    key="agent_custom_base_url_widget",
                    help="可填到域名或/v1，例如 http://localhost:11434/v1",
                )
                custom_model_input = st.text_input(
                    "模型 ID",
                    value=st.session_state.agent_custom_model_id,
                    key="agent_custom_model_id_widget",
                    help="服务商实际模型名，例如 qwen-plus、gpt-4o-mini、qwen2.5:7b",
                )
                if (
                    custom_provider_input != st.session_state.agent_custom_provider
                    or custom_base_input != st.session_state.agent_custom_base_url
                    or custom_model_input != st.session_state.agent_custom_model_id
                ):
                    st.session_state.agent_custom_provider = custom_provider_input
                    st.session_state.agent_custom_base_url = custom_base_input
                    st.session_state.agent_custom_model_id = custom_model_input
                    st.session_state.agent_connected = False
                    st.session_state.agent_conn_msg = ""
                provider_name = custom_provider_input.strip() or "自定义"
                base_url = custom_base_input.strip()
                agent_model_name = custom_model_input.strip() or "openai-compatible-model"
                st.caption(f"📡 服务商: **{provider_name}**  |  地址: `{base_url}`  |  模型: `{agent_model_name}`")
            else:
                model_cfg = MODEL_REGISTRY.get(selected_model, {})
                provider_name = model_cfg.get("provider_name", "未知")
                base_url = model_cfg.get("base_url", "")
                agent_model_name = selected_model
                st.caption(f"📡 服务商: **{provider_name}**  |  地址: `{base_url}`")

            # ── 第2步：API Key（与该服务商绑定）──
            st.markdown(f"#### ② {provider_name} API Key")

            # 获取该 provider 已保存的 key
            current_key = st.session_state.agent_api_keys.get(provider_name, "")

            # Key 输入行 + 清除/显示按钮
            key_col1, key_col2, key_col3 = st.columns([4, 1, 1])
            with key_col1:
                api_key_input = st.text_input(
                    f"{provider_name} Key",
                    value=current_key,
                    type="password",
                    placeholder=f"粘贴你的 {provider_name} API Key",
                    label_visibility="collapsed",
                    key=f"agent_key_{provider_name}",
                )
            with key_col2:
                if st.button("✕", key=f"clear_key_{provider_name}", help="清除此 Key", use_container_width=True):
                    st.session_state.agent_api_keys[provider_name] = ""
                    st.session_state.agent_connected = False
                    st.session_state.agent_conn_msg = ""
                    st.rerun()
            with key_col3:
                if st.button("👁", key=f"show_key_btn_{provider_name}", help="显示/隐藏", use_container_width=True):
                    st.session_state[f"show_key_visible_{provider_name}"] = not st.session_state.get(f"show_key_visible_{provider_name}", False)
                    st.rerun()

            # 如果用户点击了显示，则明文展示
            if st.session_state.get(f"show_key_visible_{provider_name}", False):
                st.text(f"明文: {current_key if current_key else '(空)'}")

            # 同步 key 到 provider
            if api_key_input != current_key:
                st.session_state.agent_api_keys[provider_name] = api_key_input
                current_key = api_key_input
                st.session_state.agent_connected = False
                st.session_state.agent_conn_msg = ""

            # 显示 Key 状态
            if current_key:
                masked = current_key[:6] + "..." + current_key[-4:] if len(current_key) > 10 else "****"
                st.caption(f"✅ 已保存 {provider_name} Key: {masked}")
            else:
                st.caption(f"⚠️ 未设置 {provider_name} Key")

            # ── 第3步：测试连接 ──
            st.markdown("#### ③ 测试连接")
            if st.button("🔌 测试 API 连接", key="agent_test_conn_btn", use_container_width=True):
                if not current_key:
                    st.session_state.agent_connected = False
                    st.session_state.agent_conn_msg = f"❌ 请先输入 {provider_name} API Key"
                else:
                    with st.spinner(f"正在连接 {provider_name}..."):
                        test_client = LLMClient(
                            model_name=agent_model_name,
                            api_key=current_key,
                            base_url=base_url,
                        )
                    ok, msg = test_client.test_connection()
                    st.session_state.agent_connected = ok
                    st.session_state.agent_conn_msg = msg

            # 连接状态
            if st.session_state.agent_connected:
                st.success(st.session_state.agent_conn_msg)
            elif st.session_state.agent_conn_msg:
                st.error(st.session_state.agent_conn_msg)
            else:
                st.info("👆 输入 Key 后点击「测试连接」")

            api_ready = bool(current_key.strip()) and bool(base_url.strip()) and bool(agent_model_name.strip())

            # ── 运行模式 ──
            st.markdown("---")
            st.markdown("#### ⚙️ 运行模式")
            st.session_state.agent_fast_mode = st.checkbox(
                "快速模式：按需取数，不预抓所有实时行情",
                value=st.session_state.agent_fast_mode,
                key="agent_fast_mode_widget",
            )
            st.session_state.agent_max_iterations = st.slider(
                "最大工具轮数",
                min_value=1,
                max_value=12,
                value=int(st.session_state.agent_max_iterations),
                key="agent_max_iterations_widget",
                help="轮数越大越自主，但响应越慢。日常建议4-6。",
            )

            # ── 风控规则 ──
            st.markdown("---")
            st.markdown("#### 🛡️ 风控规则")
            st.caption(f"""
            • 单票≤总资产{RISK_RULES['max_single_position_pct']:.0%}
            • 总仓≤{RISK_RULES['max_total_position_pct']:.0%}
            • 止损{RISK_RULES['stop_loss_pct']:.0%} / 止盈+{RISK_RULES['take_profit_pct']:.0%}
            • 日交易≤{RISK_RULES['max_daily_trades']}笔
            • 最低现金{RISK_RULES['min_cash_reserve']:,.0f}元
            """)

            # ── 本地自主选股闭环：不依赖LLM，先把候选池跑出来 ──
            st.markdown("---")
            st.markdown("#### ⚡ 本地闭环选股")
            st.session_state.agent_auto_universe = st.selectbox(
                "候选范围",
                options=["自选股优先+全市场抽样", "自选股", "全市场抽样"],
                index=["自选股优先+全市场抽样", "自选股", "全市场抽样"].index(st.session_state.agent_auto_universe)
                if st.session_state.agent_auto_universe in ["自选股优先+全市场抽样", "自选股", "全市场抽样"] else 0,
                key="agent_auto_universe_widget",
            )
            st.session_state.agent_auto_max_candidates = st.slider(
                "最多扫描股票数",
                min_value=30,
                max_value=1000,
                value=int(st.session_state.agent_auto_max_candidates),
                step=30,
                key="agent_auto_max_candidates_widget",
                help="数值越大越接近全市场，但耗时越长。",
            )
            st.session_state.agent_auto_top_n = st.slider(
                "返回Top N",
                min_value=3,
                max_value=20,
                value=int(st.session_state.agent_auto_top_n),
                key="agent_auto_top_n_widget",
            )
            st.session_state.agent_auto_add_watchlist = st.checkbox(
                "把Top结果自动加入自选股",
                value=st.session_state.agent_auto_add_watchlist,
                key="agent_auto_add_watchlist_widget",
            )
            if st.button("运行本地选股闭环", key="agent_local_auto_select_btn", use_container_width=True, disabled=st.session_state.agent_running):
                st.session_state.agent_running = True
                ts = datetime.now().strftime("%H:%M:%S")
                local_prompt = (
                    f"本地自主选股：{st.session_state.agent_auto_universe}，"
                    f"最多{st.session_state.agent_auto_max_candidates}只，Top {st.session_state.agent_auto_top_n}"
                )
                st.session_state.agent_messages.append(("user", local_prompt, ts))
                try:
                    with st.spinner("正在读取TDX日线并本地打分..."):
                        result = ToolExecutor().execute("auto_select_stocks", {
                            "universe": st.session_state.agent_auto_universe,
                            "max_candidates": st.session_state.agent_auto_max_candidates,
                            "top_n": st.session_state.agent_auto_top_n,
                            "add_to_watchlist": st.session_state.agent_auto_add_watchlist,
                        })
                    st.session_state.agent_messages.append(("assistant", result, datetime.now().strftime("%H:%M:%S")))
                except Exception as e:
                    st.session_state.agent_messages.append(("system", f"❌ 本地选股执行出错: {str(e)}", datetime.now().strftime("%H:%M:%S")))
                finally:
                    st.session_state.agent_running = False
                    st.rerun()

            # ── 快捷指令（有可用API Key即可使用；测试连接变成可选）──
            if api_ready:
                st.markdown("---")
                st.caption("大模型快捷指令已移动到主对话区顶部，配置栏只保留参数和本地选股控制。")
            else:
                st.caption("本地闭环选股不需要API；大模型对话需先填写Key、Base URL和模型ID。")

        # ═════════════════════════════════════
        #  左侧主区域：放大的对话区
        # ═════════════════════════════════════
        with agent_col2:
            if not api_ready and not st.session_state.agent_messages:
                st.markdown("""
                <div style='text-align: center; padding: 60px 20px; color: #999;'>
                    <div style='font-size: 48px; margin-bottom: 16px;'>🔒</div>
                    <h3>请先填写大模型配置</h3>
                    <p>① 选择模型 → ② 输入 API Key / Base URL / 模型ID</p>
                    <p style='font-size: 0.85em;'>测试连接是可选项；本地闭环选股可直接在中间控制台运行</p>
                </div>
                """, unsafe_allow_html=True)
            else:
                # 对话历史
                st.markdown("""
                <div style='background: linear-gradient(135deg, #eef6ff 0%, #f8fbff 100%); padding: 14px 16px; border-radius: 10px; margin-bottom: 10px; border: 1px solid #d9e9ff;'>
                    <span style='color: #1f449c; font-weight: 700; font-size:1.08em;'>💬 主对话区</span>
                    <span style='color: #666; font-size: 0.9em;'>（放大上下文 · 工具调用实时显示 · 本地选股结果也在这里汇总）</span>
                </div>
                """, unsafe_allow_html=True)

                if api_ready:
                    quick_cols = st.columns([1, 1, 1, 1, 0.8])
                    for idx, (label, prompt) in enumerate(quick_prompts.items()):
                        with quick_cols[idx]:
                            if st.button(label, key=f"quick_main_{label}", use_container_width=True):
                                st.session_state.agent_quick_prompt = prompt
                    with quick_cols[-1]:
                        if st.button("清空对话", key="agent_clear_messages_btn", use_container_width=True):
                            st.session_state.agent_messages = []
                            st.rerun()
                elif st.session_state.agent_messages:
                    if st.button("清空对话", key="agent_clear_messages_no_api_btn"):
                        st.session_state.agent_messages = []
                        st.rerun()

                chat_container = st.container(height=720)
                def _agent_msg_html(value):
                    return _safe_html(str(value)).replace("\n", "<br>")

                with chat_container:
                    if st.session_state.agent_messages:
                        for i, (role, content, ts) in enumerate(st.session_state.agent_messages):
                            safe_ts = _agent_msg_html(ts)
                            safe_content = _agent_msg_html(content)
                            if role == "system":
                                st.markdown(f"""
                                <div style='background-color: #f0f0f0; padding: 8px 12px; border-radius: 6px; margin: 4px 0; font-size: 0.85em; color: #666;'>
                                    ⚙️ <small>{safe_ts}</small> {safe_content}
                                </div>
                                """, unsafe_allow_html=True)
                            elif role == "assistant":
                                st.markdown(f"""
                                <div style='background-color: #e8f4f9; padding: 10px 14px; border-radius: 8px; margin: 6px 0;'>
                                    <span style='color: #1f449c; font-weight: 600;'>🤖 助手</span>
                                    <small style='color: #999; margin-left: 8px;'>{safe_ts}</small><br>
                                    {safe_content}
                                </div>
                                """, unsafe_allow_html=True)
                            elif role == "tool":
                                st.markdown(f"""
                                <div style='background-color: #fff8e1; padding: 8px 12px; border-radius: 6px; margin: 4px 0; font-size: 0.85em;'>
                                    🔧 <small>{safe_ts}</small> {safe_content}
                                </div>
                                """, unsafe_allow_html=True)
                            elif role == "user":
                                st.markdown(f"""
                                <div style='background-color: #f0f2f6; padding: 10px 14px; border-radius: 8px; margin: 6px 0;'>
                                    <span style='color: #666; font-weight: 600;'>👤 你</span>
                                    <small style='color: #999; margin-left: 8px;'>{safe_ts}</small><br>
                                    {safe_content}
                                </div>
                                """, unsafe_allow_html=True)
                    else:
                        st.info("使用上方快捷指令，或在下方输入问题，开始对话")

                # 输入区域
                if api_ready:
                    input_col1, input_col2 = st.columns([7, 1])
                    with input_col1:
                        user_input = st.text_input(
                            "输入指令",
                            placeholder="例如：分析今天的市场走势，找出3只值得关注的股票",
                            key=f"agent_input_text_{st.session_state.agent_input_counter}",
                        )
                    with input_col2:
                        send_btn = st.button("▶ 发送", key="agent_send_btn", use_container_width=True)
                else:
                    st.info("已显示本地闭环结果。填写中间控制台的大模型配置后，可继续让Agent对候选股做深度分析。")

                # 处理快捷指令
                if "agent_quick_prompt" in st.session_state:
                    user_input = st.session_state.agent_quick_prompt
                    del st.session_state.agent_quick_prompt
                    send_btn = True

        # ═════════════════════════════════════
        #  右栏：数据看板
        # ═════════════════════════════════════
        with agent_col3:
            st.markdown("""
            <div style='background-color: #f8f9fa; padding: 10px; border-radius: 8px; margin-bottom: 15px;'>
                <span style='font-weight: 600; color: #1f449c;'>📊 数据看板</span>
            </div>
            """, unsafe_allow_html=True)

            # 持仓概览
            try:
                from virtual_portfolio import get_or_create_portfolio, get_realtime_price
                portfolio = get_or_create_portfolio()
                if portfolio.positions:
                    price_map = {}
                    for code in portfolio.positions:
                        info = get_realtime_price(code)
                        if info:
                            price_map[code] = info["price"]

                    if price_map:
                        total_asset = portfolio.get_total_asset(price_map)
                        total_pnl = portfolio.get_pnl(price_map)
                        pnl_pct = portfolio.get_pnl_pct(price_map)
                        st.metric("总资产", f"{total_asset:,.0f}", f"{total_pnl:+,.0f}")
                        st.metric("收益率", f"{pnl_pct:+.2f}%")
                    else:
                        st.metric("总资产", f"{portfolio.cash:,.0f}")
                    st.metric("持仓数", f"{len(portfolio.positions)}只")
                    st.metric("现金", f"{portfolio.cash:,.0f}")

                    if portfolio.positions and price_map:
                        st.markdown("---")
                        st.markdown("**持仓明细**")
                        for code, pos in portfolio.positions.items():
                            price = price_map.get(code, 0)
                            if price > 0:
                                pnl_pct_item = (price - pos["avg_cost"]) / pos["avg_cost"] * 100
                                name = get_realtime_price(code).get("name", code) if code in price_map else code
                                st.markdown(f"• {name}({code[:6]}) {pos['shares']}股 {pnl_pct_item:+.1f}%")
                else:
                    st.metric("总资产", f"{portfolio.cash:,.0f}")
                    st.info("暂无持仓")
            except Exception:
                st.warning("持仓数据加载中...")

            # Agent记忆
            st.markdown("---")
            st.markdown("""
            <div style='background-color: #f8f9fa; padding: 10px; border-radius: 8px;'>
                <span style='font-weight: 600; color: #1f449c;'>🧠 Agent 记忆</span>
            </div>
            """, unsafe_allow_html=True)
            memory = AgentMemory()
            rules = memory.long_term.get("learned_rules", [])
            if rules:
                with st.expander(f"经验教训 ({len(rules)}条)", expanded=False):
                    for i, rule in enumerate(rules[-5:], 1):
                        st.markdown(f"{i}. {rule}")
            else:
                st.caption("Agent 尚无历史经验，运行后会自动积累")
            screen_history = memory.long_term.get("screen_history", [])
            if screen_history:
                latest_screen = screen_history[-1]
                st.caption(
                    f"最近选股: 扫描{latest_screen.get('scanned', 0)}只，"
                    f"有效{latest_screen.get('valid', 0)}只"
                )
                with st.expander("最近Top候选", expanded=False):
                    for item in latest_screen.get("top", [])[:5]:
                        try:
                            item_score = float(item.get("score", 0) or 0)
                        except Exception:
                            item_score = 0.0
                        st.markdown(
                            f"• {item.get('name', '?')}({item.get('code', '?')}) "
                            f"评分 {item_score:.1f}"
                        )
            interactions = memory.long_term.get("interactions", [])
            if interactions:
                st.caption(f"已记录对话闭环 {len(interactions)} 次")

        # ── 执行 Agent ──
        if api_ready and send_btn and user_input and not st.session_state.agent_running:
            api_key = st.session_state.agent_api_keys.get(provider_name, "").strip()
            st.session_state.agent_running = True

            # 添加用户消息
            ts = datetime.now().strftime("%H:%M:%S")
            st.session_state.agent_messages.append(("user", user_input, ts))

            try:
                # 创建Agent实例
                agent = AIAgent(model_name=agent_model_name, api_key=api_key, base_url=base_url)

                def on_step(step, action, detail):
                    ts_step = datetime.now().strftime("%H:%M:%S")
                    msg = f"[步骤{step}] {action}: {detail[:150]}"
                    st.session_state.agent_messages.append(("tool", msg, ts_step))

                # 执行Agent
                with st.spinner("🤖 Agent 正在思考..."):
                    result = agent.run(
                        user_input,
                        on_step=on_step,
                        eager_context=not st.session_state.agent_fast_mode,
                        max_iterations=st.session_state.agent_max_iterations,
                    )

                # 记录结果
                ts_result = datetime.now().strftime("%H:%M:%S")
                st.session_state.agent_messages.append(("assistant", result, ts_result))

                # 保存Agent实例（保留记忆）
                st.session_state.ai_agent = agent

            except Exception as e:
                st.session_state.agent_messages.append(("system", f"❌ Agent 执行出错: {str(e)}", datetime.now().strftime("%H:%M:%S")))
            finally:
                st.session_state.agent_running = False
                # 递增计数器，下次渲染创建新 widget（清空输入框）
                st.session_state.agent_input_counter += 1
                st.rerun()
        
        # 添加页脚
        st.markdown("---")
        st.markdown("""
        <div style='text-align: center; color: #666; font-size: 0.9em; margin-top: 2em;'>
            <p><strong>万向金枢 | Universal Golden Hub</strong> | 版本 2.0 | 支持日线/周线/月线多周期分析</p>
            <p>功能特点：多周期K线显示 | 智能相似度匹配 | 技术指标分析 | AI智能问答</p>
            <p style='font-size: 0.8em;'>数据来源：TDX | 分析算法：优化多维度相似度匹配 | 更新时间：2025年</p>
        </div>
        """, unsafe_allow_html=True)
    
    with tab4:
        st.markdown("""
        <div style='background-color: #f8f9fa; padding: 20px; border-radius: 10px; margin-bottom: 20px;'>
            <h3 style='color: #1f449c; margin-top: 0;'>⭐ 我的自选股</h3>
            <p style='color: #666;'>实时追踪您关注的股票，显示最新行情数据</p>
        </div>
        """, unsafe_allow_html=True)
        
        # 初始化自选股
        if 'watchlist' not in st.session_state:
            st.session_state.watchlist = load_watchlist()
        
        # 刷新数据按钮
        col1, col2, col3 = st.columns([1, 1, 4])
        with col1:
            if st.button("🔄 刷新数据", key="refresh_watchlist"):
                st.rerun()
        with col2:
            if st.button("🗑️ 清空自选股", key="clear_all_watchlist"):
                st.session_state.watchlist = []
                save_watchlist([])
                st.success("已清空所有自选股")
                st.rerun()
        
        # 显示自选股列表
        if st.session_state.watchlist:
            st.markdown(f"**共 {len(st.session_state.watchlist)} 只自选股**")
            
            # 获取实时数据并显示
            watchlist_data = []
            for stock in st.session_state.watchlist:
                with st.spinner(f'正在获取 {stock["name"]} 的数据...'):
                    latest_data = get_stock_latest_data(stock['code'], stock['type'])
                    if latest_data:
                        watchlist_data.append({
                            **stock,
                            **latest_data
                        })
                    else:
                        watchlist_data.append({
                            **stock,
                            'price': None,
                            'change_pct': None,
                            'change_amount': None,
                            'volume': None,
                            'trade_date': None
                        })
            
            # 显示为表格
            if watchlist_data:
                # 创建DataFrame用于显示
                df_data = []
                for item in watchlist_data:
                    df_data.append({
                        '代码': item['code'],
                        '名称': item['name'],
                        '交易所': item.get('exchange', ''),
                        '现价': f"{item['price']:.2f}" if item['price'] else '-',
                        '涨跌': f"{item['change_amount']:+.2f}" if item['change_amount'] else '-',
                        '涨幅': f"{item['change_pct']:+.2f}%" if item['change_pct'] else '-',
                        '成交量': f"{item['volume']/10000:.0f}万" if item['volume'] else '-',
                        '日期': item.get('trade_date', '-')
                    })
                
                df_watchlist = pd.DataFrame(df_data)
                
                # 使用data_editor显示，支持删除操作
                st.dataframe(
                    df_watchlist,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        '代码': st.column_config.TextColumn('代码', width='small'),
                        '名称': st.column_config.TextColumn('名称', width='medium'),
                        '交易所': st.column_config.TextColumn('交易所', width='small'),
                        '现价': st.column_config.TextColumn('现价', width='small'),
                        '涨跌': st.column_config.TextColumn('涨跌', width='small'),
                        '涨幅': st.column_config.TextColumn('涨幅', width='small'),
                        '成交量': st.column_config.TextColumn('成交量', width='small'),
                        '日期': st.column_config.TextColumn('日期', width='small')
                    }
                )
                
                # 为每只股票显示操作按钮
                st.markdown("#### 操作")
                for i, stock in enumerate(st.session_state.watchlist):
                    col1, col2, col3, col4 = st.columns([2, 2, 1, 1])
                    with col1:
                        st.markdown(f"**{stock['name']}** ({stock['code']})")
                    with col2:
                        st.caption(f"添加时间: {stock.get('added_time', '-')}")
                    with col3:
                        if st.button("📊 分析", key=f"analyze_watch_{i}"):
                            st.session_state.selected_security = stock
                            st.session_state.active_tab = "技术分析"
                            st.rerun()
                    with col4:
                        if st.button("❌ 删除", key=f"del_watch_{i}"):
                            st.session_state.watchlist.pop(i)
                            save_watchlist(st.session_state.watchlist)
                            st.rerun()
        else:
            st.info("暂无自选股，请在「技术分析」页面点击「加入自选」按钮添加")
            st.markdown("""
            **使用说明：**
            1. 在「证券搜索」页面搜索并选择股票
            2. 进入「技术分析」页面
            3. 点击股票名称旁的「➕ 加入自选」按钮
            4. 股票将保存到本地，下次打开不会丢失
            """)

    with tab5:
        # 历史回测验证界面
        st.markdown("""
        <div style='background-color: #f8f9fa; padding: 20px; border-radius: 10px; margin-bottom: 20px;'>
            <h3 style='color: #1f449c; margin-top: 0;'>📋 历史回测验证</h3>
            <p style='color: #666;'>基于TAB2已找到的相似K线，验证历史模式的实际走势表现</p>
        </div>
        """, unsafe_allow_html=True)
        
        # 检查是否有相似K线结果
        similar_patterns = st.session_state.get('similar_patterns_cache', None)
        
        if similar_patterns is None or len(similar_patterns) == 0:
            st.warning("⚠️ 请先在「技术分析」标签页搜索相似K线，然后再查看历史回测验证")
            st.info("""
**使用步骤：**
1. 切换到「技术分析」标签页
2. 选择股票并调整参数
3. 点击「🔍 重新搜索」
4. 搜索完成后回到本标签页查看验证结果
            """)
        else:
            # 显示当前分析信息
            result = st.session_state.get('selected_security', {})
            stock_name = result.get('name', '未知')
            stock_code = result.get('code', '未知')
            
            # 获取参数
            custom_window = st.session_state.get('custom_window_size', 30)
            custom_future = st.session_state.get('custom_future_days', 20)
            similarity_period = st.session_state.get('pro_time_period', '日线')
            
            st.success(f"✅ 已找到 {len(similar_patterns)} 个相似K线模式 | 当前标的: {stock_name}({stock_code}) | 匹配周期: {custom_window}个{similarity_period} | 预测周期: {custom_future}个{similarity_period}")
            
            # ===== 核心验证逻辑 =====
            # 统计所有相似模式的历史走势
            valid_patterns = []
            for pattern in similar_patterns:
                future_data = pattern.get('future_data')
                pattern_data = pattern.get('pattern_data')
                if future_data is not None and not future_data.empty and pattern_data is not None and not pattern_data.empty:
                    valid_patterns.append(pattern)
            
            if not valid_patterns:
                st.warning("⚠️ 相似模式中没有包含未来走势数据的模式，无法进行回测验证")
            else:
                # 计算每个持有期的胜率和收益
                entry_price_list = []
                
                # 按持有期统计
                max_hold_days = custom_future
                period_stats = {}
                
                for hold_days in range(1, max_hold_days + 1):
                    period_stats[str(hold_days)] = {
                        'returns': [],
                        'max_gains': [],
                        'max_losses': [],
                        'win_count': 0,
                        'loss_count': 0,
                        'entry_prices': [],
                        'exit_prices': [],
                        'sources': [],
                        'dates': [],
                    }
                
                for pattern in valid_patterns:
                    future_data = pattern['future_data']
                    pattern_data = pattern['pattern_data']
                    entry_price = pattern_data.iloc[-1]['close']
                    
                    source_code = pattern.get('source_code', '')
                    source_name = pattern.get('source_name', pattern.get('source_code', ''))
                    end_date = pattern.get('end_date', '')
                    
                    for hold_days in range(1, min(max_hold_days + 1, len(future_data) + 1)):
                        day_key = str(hold_days)
                        holding_data = future_data.iloc[:hold_days]
                        
                        if len(holding_data) > 0:
                            exit_price = holding_data.iloc[-1]['close']
                            ret = (exit_price - entry_price) / entry_price * 100
                            max_price = holding_data['high'].max()
                            max_gain = (max_price - entry_price) / entry_price * 100
                            min_price = holding_data['low'].min()
                            max_loss = (min_price - entry_price) / entry_price * 100
                            
                            period_stats[day_key]['returns'].append(ret)
                            period_stats[day_key]['max_gains'].append(max_gain)
                            period_stats[day_key]['max_losses'].append(max_loss)
                            period_stats[day_key]['entry_prices'].append(entry_price)
                            period_stats[day_key]['exit_prices'].append(exit_price)
                            period_stats[day_key]['sources'].append(source_name or source_code)
                            period_stats[day_key]['dates'].append(end_date)
                            
                            if ret > 0:
                                period_stats[day_key]['win_count'] += 1
                            else:
                                period_stats[day_key]['loss_count'] += 1
                
                # 计算综合统计
                total_patterns = len(valid_patterns)
                
                # ===== 第一部分：关键指标概览 =====
                st.markdown("### 📊 关键指标概览")
                
                # 找到最佳持有期（胜率最高且样本数>=3）
                best_period = None
                best_win_rate = -1
                best_avg_return = -999
                
                for day_key, stats in period_stats.items():
                    returns = stats['returns']
                    if len(returns) >= 3:
                        win_rate = stats['win_count'] / len(returns)
                        avg_return = sum(returns) / len(returns)
                        # 综合评分：胜率*0.6 + 最大收益*0.4
                        score = win_rate * 0.6 + (avg_return / 10) * 0.4
                        if best_period is None or score > best_win_rate * 0.6 + best_avg_return * 0.04:
                            best_win_rate = win_rate
                            best_avg_return = avg_return
                            best_period = day_key
                
                # 提取关键持有期
                key_periods = ['1', '3', '5', '10', '15', '20']
                if str(custom_future) not in key_periods:
                    key_periods.append(str(custom_future))
                key_periods = [p for p in key_periods if p in period_stats and len(period_stats[p]['returns']) >= 1]
                
                # 关键指标卡片
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.markdown(f"""
                    <div style='background-color: #e8f5e9; padding: 15px; border-radius: 10px; text-align: center;'>
                        <div style='font-size: 24px; font-weight: bold; color: #2e7d32;'>{total_patterns:.0f}</div>
                        <div style='font-size: 12px; color: #666;'>样本数量</div>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    avg_all = np.mean([r for stats in period_stats.values() for r in stats['returns']]) if any(stats['returns'] for stats in period_stats.values()) else 0
                    color = '#2e7d32' if avg_all >= 0 else '#c62828'
                    st.markdown(f"""
                    <div style='background-color: #f3e5f5; padding: 15px; border-radius: 10px; text-align: center;'>
                        <div style='font-size: 24px; font-weight: bold; color: {color};'>{avg_all:+.2f}%</div>
                        <div style='font-size: 12px; color: #666;'>平均收益率</div>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col3:
                    if best_period and best_period in period_stats:
                        best_stats = period_stats[best_period]
                        best_wr = best_stats['win_count'] / len(best_stats['returns']) * 100 if best_stats['returns'] else 0
                    else:
                        best_wr = 0
                    wr_color = '#2e7d32' if best_wr >= 60 else '#e65100' if best_wr >= 50 else '#c62828'
                    st.markdown(f"""
                    <div style='background-color: #fff3e0; padding: 15px; border-radius: 10px; text-align: center;'>
                        <div style='font-size: 24px; font-weight: bold; color: {wr_color};'>{best_wr:.1f}%</div>
                        <div style='font-size: 12px; color: #666;'>最高胜率（{best_period or '-'}期）</div>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col4:
                    if best_period and best_period in period_stats:
                        best_stats = period_stats[best_period]
                        best_avg = np.mean(best_stats['returns']) if best_stats['returns'] else 0
                    else:
                        best_avg = 0
                    ret_color = '#2e7d32' if best_avg >= 0 else '#c62828'
                    st.markdown(f"""
                    <div style='background-color: #e3f2fd; padding: 15px; border-radius: 10px; text-align: center;'>
                        <div style='font-size: 24px; font-weight: bold; color: {ret_color};'>{best_avg:+.2f}%</div>
                        <div style='font-size: 12px; color: #666;'>最佳持有期收益（{best_period or '-'}期）</div>
                    </div>
                    """, unsafe_allow_html=True)
                
                st.markdown("---")
                
                # ===== 第二部分：详细的持有期收益表 =====
                st.markdown("### 📈 持有期收益分析")
                st.caption(f"基于 {total_patterns} 个历史相似模式的统计结果 | 匹配周期: {custom_window}{similarity_period} | 预测周期: {custom_future}{similarity_period}")
                
                # 构建数据表
                table_data = []
                for day_key in key_periods:
                    stats = period_stats[day_key]
                    returns = stats['returns']
                    if not returns:
                        continue
                    
                    avg_ret = np.mean(returns)
                    win_rate = stats['win_count'] / len(returns) * 100
                    max_gain = max(stats['max_gains']) if stats['max_gains'] else 0
                    max_loss = min(stats['max_losses']) if stats['max_losses'] else 0
                    std_ret = np.std(returns) if len(returns) > 1 else 0
                    median_ret = np.median(returns)
                    
                    # 风险收益比
                    sharpe = avg_ret / std_ret if std_ret > 0 else 0
                    
                    table_data.append({
                        '持有期': f"第{day_key}{similarity_period}",
                        '样本数': len(returns),
                        '胜率': f"{win_rate:.1f}%",
                        '平均收益': f"{avg_ret:+.2f}%",
                        '中位收益': f"{median_ret:+.2f}%",
                        '最大上涨': f"{max_gain:+.2f}%",
                        '最大下跌': f"{max_loss:+.2f}%",
                        '波动率': f"{std_ret:.2f}%",
                        '风险收益比': f"{sharpe:.2f}",
                    })
                
                if table_data:
                    # 使用pandas展示，带条件格式
                    df_stats = pd.DataFrame(table_data)
                    st.dataframe(
                        df_stats,
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.info("暂无足够的历史数据生成收益分析表")
                
                st.markdown("---")
                
                # ===== 第三部分：关键持有期详细对比 =====
                st.markdown("### 🔍 单个模式详细走势")
                
                # 选择查看的模式
                pattern_options = []
                for i, pattern in enumerate(valid_patterns[:10]):
                    similarity = pattern.get('similarity', 0)
                    source = pattern.get('source_name', pattern.get('source_code', ''))
                    start_d = pattern.get('start_date', '')
                    end_d = pattern.get('end_date', '')
                    if hasattr(start_d, 'strftime'):
                        start_d = start_d.strftime('%Y-%m-%d')
                    if hasattr(end_d, 'strftime'):
                        end_d = end_d.strftime('%Y-%m-%d')
                    pattern_options.append(f"TOP{i+1} | {source} | {start_d}~{end_d} | 相似度{similarity:.1%}")
                
                if pattern_options:
                    selected_idx = st.selectbox(
                        "选择模式查看详细走势",
                        range(len(pattern_options)),
                        format_func=lambda i: pattern_options[i],
                        key="pattern_detail_select"
                    )
                    
                    pattern = valid_patterns[selected_idx]
                    pattern_data = pattern['pattern_data']
                    future_data = pattern['future_data']
                    similarity = pattern.get('similarity', 0)
                    source_code = pattern.get('source_code', '')
                    source_name = pattern.get('source_name', source_code)
                    
                    # 显示模式详情
                    col_info1, col_info2, col_info3 = st.columns(3)
                    
                    with col_info1:
                        st.metric("相似度", f"{similarity:.1%}")
                    with col_info2:
                        if not future_data.empty:
                            entry_price = pattern_data.iloc[-1]['close']
                            exit_price = future_data.iloc[-1]['close']
                            ret = (exit_price - entry_price) / entry_price * 100
                            st.metric("实际收益", f"{ret:+.2f}%", delta=f"入场价 {entry_price:.2f}")
                        else:
                            st.metric("实际收益", "N/A")
                    with col_info3:
                        if not future_data.empty:
                            max_price = future_data['high'].max()
                            min_price = future_data['low'].min()
                            st.metric("振幅区间", f"{min_price:.2f} ~ {max_price:.2f}")
                        else:
                            st.metric("振幅区间", "N/A")
                    
                    # 绘制该模式的K线图（包含匹配段+未来走势）
                    try:
                        max_display_detail = custom_window + custom_future + 20
                        display_df_pattern, _ = prepare_chart_data(pattern_data, similarity_period, max_display_detail)
                        
                        fig_pattern = plot_kline(
                            display_df_pattern,
                            f"历史模式详情 ({source_name})",
                            future_df=future_data,
                            show_future_data=True,
                            show_volume=True,
                            show_indicators=True,
                            ma_windows=[5, 10, 20, 30, 60]
                        )
                        
                        if fig_pattern:
                            fig_pattern = apply_professional_style(fig_pattern, f"历史模式 ({source_name})")
                            fig_pattern.update_layout(height=600)
                            
                            # 高亮匹配段
                            if len(display_df_pattern) > 0:
                                add_similarity_segment_highlight(
                                    fig_pattern,
                                    x0=0,
                                    x1=len(display_df_pattern) - 1,
                                    color_fill='rgba(255, 165, 0, 0.15)',
                                    label_text='匹配段'
                                )
                            
                            st.plotly_chart(fig_pattern, config={'displayModeBar': True})
                    except Exception as e:
                        st.warning(f"绘制详细走势图时出错: {e}")
                else:
                    st.info("暂无可查看的模式")
                
                st.markdown("---")
                
                # ===== 第四部分：使用说明 =====
                with st.expander("📖 回测验证说明"):
                    st.markdown("""
                    **什么是历史回测验证？**
                    
                    历史回测验证基于TAB2技术分析中找到的相似K线模式，分析这些模式在历史上的实际后续走势，帮助您评估相似度匹配的预测能力。
                    
                    **关键指标解读：**
                    - **胜率**：在所有历史样本中，该持有期收益为正的比例
                    - **平均收益**：所有样本的平均收益率
                    - **中位收益**：所有样本的中位数收益率（更稳健的指标）
                    - **最大上涨/下跌**：持有期间的极端收益情况
                    - **波动率**：收益率的标准差，衡量风险
                    - **风险收益比**：平均收益/波动率，越高表示单位风险收益越好
                    
                    **注意事项：**
                    - 历史表现不代表未来收益
                    - 样本数量过少时统计意义有限
                    - 相似度越高的模式不一定收益越好
                    - 建议结合多种技术指标综合判断
                    """)



    with tab6:
        # MACD底背离全市场筛选功能
        st.markdown("""
        <div style="background: linear-gradient(90deg, #1a1a2e 0%, #16213e 100%); padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid rgba(212, 175, 55, 0.3);">
            <h3 style="color: #D4AF37; margin-top: 0;">📈 MACD底背离全市场筛选</h3>
            <p style="color: rgba(255,255,255,0.7);">智能识别符合MACD底背离形态的股票，捕捉潜在反转机会</p>
        </div>
        """, unsafe_allow_html=True)
        
        # 导入MACD底背离扫描模块
        try:
            from macd_divergence_scanner import (
                detect_macd_divergence, 
                detect_upcoming_golden_cross, create_divergence_chart
            )
            # 尝试导入Numba加速版MACD计算
            try:
                from macd_fast import calculate_macd_fast as calculate_macd
                print("[性能优化] 已启用Numba加速MACD计算")
            except ImportError:
                from macd_divergence_scanner import calculate_macd
                print("[性能提示] Numba未安装，使用普通MACD计算。建议安装: pip install numba")
            scanner_available = True
        except Exception as e:
            st.error(f"MACD扫描模块加载失败: {e}")
            scanner_available = False
        
        # 导入MACD预计算缓存
        try:
            from macd_precompute_cache import get_macd_precompute_cache, update_macd_cache_for_all_stocks
            macd_cache_available = True
        except Exception as e:
            macd_cache_available = False
            print(f"[缓存] MACD预计算缓存加载失败: {e}")
        
        # 导入MACD内存缓存（启动时预加载，扫描时零延迟）
        try:
            from macd_memory_cache import get_macd_memory_cache
            macd_memory_cache_available = True
        except Exception as e:
            macd_memory_cache_available = False
            print(f"[缓存] MACD内存缓存加载失败: {e}")
        
        if scanner_available:
            # ========== MACD缓存管理区域 ==========
            if macd_cache_available:
                with st.expander("💾 MACD预计算缓存管理", expanded=False):
                    st.markdown("""
                    <div style="background-color: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 15px;">
                        <h4 style="margin-top: 0;">🚀 MACD预计算缓存系统 V2</h4>
                        <p style="color: #666; font-size: 0.9em; margin-bottom: 0;">
                            预计算所有股票的MACD指标并缓存在本地，可将扫描速度提升 <strong>10-50倍</strong>！<br>
                            首次使用需要执行完整更新，后续每日收盘后可执行增量更新。
                        </p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # 获取缓存状态
                    cache = get_macd_precompute_cache()
                    cache_stats = cache.get_cache_stats()
                    
                    # 显示缓存统计
                    col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
                    with col_stat1:
                        st.metric("已缓存股票", f"{cache_stats['total_cached']} 只")
                    with col_stat2:
                        st.metric("缓存文件数", f"{cache_stats['file_count']} 个")
                    with col_stat3:
                        st.metric("缓存大小", f"{cache_stats['cache_size_mb']} MB")
                    with col_stat4:
                        last_update = cache_stats['last_full_update'] or '从未'
                        if last_update != '从未':
                            last_update = last_update.split()[0]  # 只显示日期
                        st.metric("上次完整更新", last_update)
                    
                    st.caption(f"📁 缓存位置: {cache_stats['cache_dir']}")
                    
                    # 更新操作区域
                    st.markdown("---")
                    st.markdown("#### 🔄 缓存更新操作")
                    
                    col_update1, col_update2, col_update3 = st.columns([1, 1, 2])
                    
                    with col_update1:
                        if st.button("🔄 完整更新", type="primary", use_container_width=True):
                            with st.spinner("正在执行完整更新，计算所有股票的MACD..."):
                                try:
                                    tdx_path = st.session_state.get('tdx_data_path', r"D:\new_tdx\vipdoc")
                                    
                                    # 创建进度条
                                    progress_bar = st.progress(0)
                                    status_text = st.empty()
                                    
                                    def update_progress(current, total):
                                        progress = current / total
                                        progress_bar.progress(min(progress, 0.99))
                                        status_text.text(f"计算进度: {current}/{total} ({progress*100:.1f}%)")
                                    
                                    # 执行完整更新
                                    result = update_macd_cache_for_all_stocks(
                                        tdx_path=tdx_path,
                                        max_workers=8,
                                        incremental=False,
                                        progress_callback=update_progress
                                    )
                                    
                                    progress_bar.empty()
                                    status_text.empty()
                                    
                                    st.success(f"✅ 完整更新完成！成功计算 {result['success_count']}/{result['total']} 只股票的MACD")
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(f"❌ 更新失败: {e}")
                    
                    with col_update2:
                        if st.button("⬆️ 增量更新", use_container_width=True):
                            with st.spinner("正在执行增量更新..."):
                                try:
                                    tdx_path = st.session_state.get('tdx_data_path', r"D:\new_tdx\vipdoc")
                                    
                                    progress_bar = st.progress(0)
                                    status_text = st.empty()
                                    
                                    def update_progress(current, total):
                                        progress = current / total
                                        progress_bar.progress(min(progress, 0.99))
                                        status_text.text(f"更新进度: {current}/{total}")
                                    
                                    result = update_macd_cache_for_all_stocks(
                                        tdx_path=tdx_path,
                                        max_workers=8,
                                        incremental=True,
                                        progress_callback=update_progress
                                    )
                                    
                                    progress_bar.empty()
                                    status_text.empty()
                                    
                                    if result['success_count'] > 0:
                                        st.success(f"✅ 增量更新完成！成功计算: {result['success_count']}/{result['total']} 只股票")
                                    else:
                                        st.info("ℹ️ 所有缓存都是最新的，无需更新")
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(f"❌ 更新失败: {e}")
                    
                    with col_update3:
                        use_macd_cache = st.checkbox(
                            "使用MACD缓存加速扫描",
                            value=True,
                            help="勾选后扫描时将优先使用预计算的MACD缓存，大幅提升速度"
                        )
                        
                        # 内存缓存预加载状态
                        if 'macd_memory_cache_loaded' not in st.session_state:
                            st.session_state.macd_memory_cache_loaded = False
                        
                        if macd_memory_cache_available and use_macd_cache:
                            if st.button("🚀 预加载内存缓存", type="secondary", use_container_width=True):
                                with st.spinner("正在预加载所有MACD缓存到内存（首次约5-15秒）..."):
                                    try:
                                        memory_cache = get_macd_memory_cache(preload=True, show_progress=True)
                                        st.session_state.macd_memory_cache_loaded = True
                                        stats = memory_cache.get_stats()
                                        st.success(f"✅ 内存缓存加载完成！{stats['loaded_count']} 只股票，内存占用约 {stats['estimated_memory_mb']:.1f} MB")
                                    except Exception as e:
                                        st.error(f"❌ 内存缓存加载失败: {e}")
                            
                            # 显示内存缓存状态
                            if st.session_state.macd_memory_cache_loaded:
                                st.info("⚡ 内存缓存已就绪，扫描将零延迟")
                        
                        if st.button("🗑️ 清空缓存", type="secondary"):
                            if st.checkbox("确认清空所有缓存？"):
                                cache.clear_all_cache()
                                st.success("✅ 缓存已清空")
                                st.rerun()
            
            # 参数配置区域
            with st.expander("⚙️ 扫描参数配置", expanded=True):
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    scan_mode = st.radio(
                        "扫描模式",
                        ["底背离形态", "即将金叉预测", "全部扫描"],
                        index=0,
                        help="选择要扫描的技术形态类型"
                    )
                
                with col2:
                    max_results = st.slider(
                        "最大结果数量",
                        min_value=5,
                        max_value=50,
                        value=20,
                        step=5,
                        help="显示符合条件的最多股票数量"
                    )
                
                with col3:
                    min_confidence = st.slider(
                        "最小可信度",
                        min_value=0.0,
                        max_value=1.0,
                        value=0.1,
                        step=0.1,
                        help="过滤低可信度的信号"
                    )
                    
                    strict_mode = st.checkbox(
                        "严格模式（必须已金叉）",
                        value=True,
                        help="开启后只显示已经形成金叉的底背离。关闭则显示潜在底背离形态"
                    )
                    
                    max_cross_days = st.slider(
                        "金叉时间范围（天）",
                        min_value=1,
                        max_value=15,
                        value=10,
                        step=1,
                        help="只显示最近N天内完成金叉的底背离（1-15天）"
                    )
                    
                    debug_mode = st.checkbox(
                        "调试模式",
                        value=False,
                        help="显示详细检测过程（仅用于排查问题）"
                    )
                
                # 股票类型筛选 - 带数量统计
                st.markdown("#### 🏢 股票类型筛选")
                
                # 获取TDX路径并统计各板块股票数量
                tdx_path = st.session_state.get('tdx_data_path', r"D:\new_tdx\vipdoc")
                
                # 统计各板块股票数量
                stock_type_counts = {}
                try:
                    from tdx_reader import get_tdx_reader
                    reader = get_tdx_reader(tdx_path)
                    available_codes = reader.get_available_codes()
                    
                    # 合并所有股票并统计
                    all_stocks_for_count = []
                    for code_info in available_codes.get('sh', []):
                        if isinstance(code_info, dict):
                            code = code_info.get('code', '')
                        else:
                            code = str(code_info)
                        stock_type = get_detailed_type(code, 'stock')
                        all_stocks_for_count.append(stock_type)
                    
                    for code_info in available_codes.get('sz', []):
                        if isinstance(code_info, dict):
                            code = code_info.get('code', '')
                        else:
                            code = str(code_info)
                        stock_type = get_detailed_type(code, 'stock')
                        all_stocks_for_count.append(stock_type)
                    
                    # 统计各板块数量
                    from collections import Counter
                    stock_type_counts = Counter(all_stocks_for_count)
                except Exception as e:
                    st.warning(f"统计股票数量失败: {e}")
                
                stock_type_options = {
                    '上证主板': '上证主板',
                    '深证主板': '深证主板',
                    '创业板': '创业板',
                    '科创板': '科创板',
                    '中小板': '中小板',
                    '北交所': '北交所'
                }
                
                # 初始化选中状态
                if 'macd_selected_stock_types' not in st.session_state:
                    st.session_state.macd_selected_stock_types = list(stock_type_options.values())
                
                # 创建多列布局显示复选框（带数量）
                type_cols = st.columns(3)
                selected_types = []
                
                for idx, (type_key, type_name) in enumerate(stock_type_options.items()):
                    with type_cols[idx % 3]:
                        is_selected = type_name in st.session_state.macd_selected_stock_types
                        count = stock_type_counts.get(type_name, 0)
                        if st.checkbox(
                            f"{type_name} ({count}只)",
                            value=is_selected,
                            key=f"macd_stock_type_{type_name}"
                        ):
                            selected_types.append(type_name)
                
                # 更新session_state
                st.session_state.macd_selected_stock_types = selected_types
                
                total_selected_count = sum(stock_type_counts.get(t, 0) for t in selected_types)
                st.caption(f"📊 已选择 {len(selected_types)} 个类型，共 **{total_selected_count}** 只股票")
                
                # 扫描速度优化选项
                st.markdown("#### ⚡ 扫描优化")
                col_opt1, col_opt2 = st.columns(2)
                with col_opt1:
                    use_parallel = st.checkbox(
                        "启用多进程并行扫描",
                        value=True,
                        help="利用多核CPU同时扫描多只股票，可提速3-8倍"
                    )
                    max_workers = st.slider(
                        "并行进程数",
                        min_value=2,
                        max_value=16,
                        value=8,
                        step=1,
                        help="根据CPU核心数调整，建议设置为CPU核心数"
                    ) if use_parallel else 1
                    
                    use_batch_io = st.checkbox(
                        "启用批量并行IO（方案B）",
                        value=True,
                        help="先批量并行读取所有文件到内存，再处理。配合Numba可提速30-50倍！"
                    )
                    
                    # 批量读取大小选项
                    batch_mode = st.radio(
                        "批量模式",
                        options=["自动（一次性读取全部）", "手动设置批次大小"],
                        index=0,
                        help="自动模式：一次性并行读取所有股票，最快但需要更多内存。手动模式：分批次读取，内存友好。"
                    ) if use_batch_io else "手动设置批次大小"
                    
                    if batch_mode == "手动设置批次大小":
                        batch_size = st.slider(
                            "批量读取大小",
                            min_value=50,
                            max_value=5000,
                            value=500,
                            step=100,
                            help="每批并行读取的股票数量。建议：8GB内存选500-1000，16GB内存选1000-2000，32GB内存可选3000+"
                        )
                    else:
                        batch_size = None  # 自动模式，后面会根据股票数量动态设置
                
                with col_opt2:
                    use_sampling = st.checkbox(
                        "智能采样模式",
                        value=False,
                        help="当股票数量超过1000只时，随机采样扫描，大幅提速"
                    )
                    sample_size = st.number_input(
                        "采样数量",
                        min_value=100,
                        max_value=2000,
                        value=500,
                        step=100,
                        help="采样扫描的股票数量"
                    ) if use_sampling else None
            
            # 扫描控制区域
            col_scan1, col_scan2 = st.columns([1, 3])
            
            with col_scan1:
                start_scan = st.button("🔍 开始扫描", type="primary", use_container_width=True)
            
            with col_scan2:
                if start_scan:
                    if len(selected_types) == 0:
                        st.warning("请至少选择一种股票类型")
                    else:
                        st.info(f"准备扫描 {len(selected_types)} 个类型的股票...")
            
            # 扫描执行和结果展示
            if start_scan and len(selected_types) > 0:
                # 进度显示
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                # 获取股票列表 - 使用配置的数据源路径
                try:
                    from tdx_reader import read_tdx_data
                    from tdx_reader import get_tdx_reader
                    from tdx_reader import set_tdx_data_dir
                    
                    # 使用用户配置的数据源路径
                    tdx_path = st.session_state.get('tdx_data_path', r"D:\new_tdx\vipdoc")
                    set_tdx_data_dir(tdx_path)
                    
                    status_text.text(f"正在加载股票列表... (数据源: {tdx_path})")
                    reader = get_tdx_reader(tdx_path)
                    available_codes = reader.get_available_codes()
                    
                    # 合并上海和深圳的股票
                    all_stocks = []
                    for code_info in available_codes.get('sh', []):
                        if isinstance(code_info, dict):
                            code = code_info.get('code', '')
                            name = code_info.get('name', '')
                        else:
                            code = str(code_info)
                            name = str(code_info)
                        
                        stock_type = get_detailed_type(code, 'stock')
                        if stock_type in selected_types:
                            all_stocks.append({'code': code, 'name': name, 'type': stock_type})
                    
                    for code_info in available_codes.get('sz', []):
                        if isinstance(code_info, dict):
                            code = code_info.get('code', '')
                            name = code_info.get('name', '')
                        else:
                            code = str(code_info)
                            name = str(code_info)
                        
                        stock_type = get_detailed_type(code, 'stock')
                        if stock_type in selected_types:
                            all_stocks.append({'code': code, 'name': name, 'type': stock_type})
                    
                    # 智能采样
                    if use_sampling and len(all_stocks) > 1000:
                        import random
                        random.seed(42)  # 固定种子确保可重复
                        original_count = len(all_stocks)
                        all_stocks = random.sample(all_stocks, min(sample_size, len(all_stocks)))
                        status_text.info(f"已启用智能采样：从 {original_count} 只股票中随机采样 {len(all_stocks)} 只进行扫描")
                    
                    total_stocks = len(all_stocks)
                    status_text.text(f"找到 {total_stocks} 只符合条件的股票，开始全市场扫描...")
                    
                    # 执行扫描 - 全市场扫描，不限制数量
                    divergence_results = []
                    upcoming_cross_results = []
                    
                    # 自动模式：一次性读取所有股票
                    if batch_size is None:
                        batch_size = total_stocks
                        if total_stocks > 2000:
                            status_text.warning(f"⚠️ 自动模式将一次性读取全部 {total_stocks} 只股票，可能占用大量内存。如内存不足请切换为手动模式并减小批次大小。")
                        else:
                            status_text.info(f"🚀 自动模式：将一次性并行读取全部 {total_stocks} 只股票")
                    
                    if use_parallel and total_stocks > 50:
                        # 方案B: 批量并行IO + Numba加速
                        if use_batch_io:
                            status_text.text(f"【方案B】批量并行IO + Numba加速（批次大小: {batch_size}）...")
                            
                            # 步骤1: 批量并行读取所有文件
                            status_text.text(f"步骤1/3: 批量并行读取 {total_stocks} 只股票数据...")
                            from concurrent.futures import ThreadPoolExecutor, as_completed
                            
                            prefetched_macd_cache = {}
                            stocks_to_read = []
                            cache_hit_count = 0
                            memory_hit_count = 0
                            use_macd_cache_enabled = bool(locals().get('use_macd_cache', False))
                            use_macd_cache_flag = macd_cache_available and use_macd_cache_enabled
                            use_memory_cache_flag = macd_memory_cache_available and use_macd_cache_enabled and st.session_state.get('macd_memory_cache_loaded', False)
                            memory_cache_instance = None
                            disk_cache_manager = None

                            if use_memory_cache_flag:
                                try:
                                    memory_cache_instance = get_macd_memory_cache(preload=False)
                                    if memory_cache_instance.loaded_count == 0:
                                        memory_cache_instance = None
                                except Exception:
                                    memory_cache_instance = None

                            if use_macd_cache_flag and memory_cache_instance is None:
                                disk_cache_manager = get_macd_precompute_cache()

                            if use_macd_cache_flag:
                                status_text.text("步骤1/3: 优先读取MACD缓存，只回读缓存缺失的股票...")
                                for stock in all_stocks:
                                    code = stock['code']
                                    cached_macd = None
                                    if memory_cache_instance and memory_cache_instance.has(code):
                                        cached_macd = memory_cache_instance.get(code)
                                        memory_hit_count += 1
                                        cache_hit_count += 1
                                    elif disk_cache_manager:
                                        cached_macd = disk_cache_manager.get(code)
                                        if cached_macd:
                                            cache_hit_count += 1

                                    df_macd = _build_macd_df_from_cached(cached_macd)
                                    if df_macd is not None:
                                        prefetched_macd_cache[code] = (df_macd, df_macd)
                                    else:
                                        stocks_to_read.append(stock)
                            else:
                                stocks_to_read = all_stocks

                            stock_data_cache = {}
                            read_count = 0
                            read_total = len(stocks_to_read)
                            
                            def read_single_stock(stock):
                                """读取单只股票数据（复用reader实例）"""
                                try:
                                    df = reader.get_stock_data(stock['code'])
                                    if df is not None and len(df) >= 30:
                                        return stock['code'], df
                                except:
                                    pass
                                return stock['code'], None
                            
                            # 分批并行读取
                            for batch_start in range(0, read_total, batch_size):
                                batch_end = min(batch_start + batch_size, read_total)
                                batch_stocks = stocks_to_read[batch_start:batch_end]
                                
                                with ThreadPoolExecutor(max_workers=max_workers*2) as executor:
                                    futures = {executor.submit(read_single_stock, s): s for s in batch_stocks}
                                    
                                    for future in as_completed(futures):
                                        code, df = future.result()
                                        read_count += 1
                                        if df is not None:
                                            stock_data_cache[code] = df
                                        
                                        # 更新进度
                                        if read_count % 100 == 0 or read_count == read_total:
                                            progress = read_count / max(read_total, 1)
                                            progress_bar.progress(min(progress * 0.4, 0.4))  # 读取占40%
                                            status_text.text(f"读取中: {read_count}/{read_total} | 缓存命中: {len(prefetched_macd_cache)} | 成功: {len(stock_data_cache)} | 已发现底背离: {len(divergence_results)}")
                            
                            # 步骤2: 批量计算MACD (优先使用内存缓存，其次磁盘缓存)
                            if read_total == 0:
                                progress_bar.progress(0.4)
                                status_text.text(f"MACD缓存命中 {len(prefetched_macd_cache)}/{total_stocks}，跳过通达信文件回读")

                            status_text.text(f"步骤2/3: 获取MACD数据（优先使用缓存）...")
                            macd_cache = dict(prefetched_macd_cache)
                            calc_count = 0
                            # cache_hit_count / memory_hit_count already include the cache-first phase.
                            
                            # 优先使用内存缓存（纳秒级），其次磁盘缓存
                            use_macd_cache_enabled = bool(locals().get('use_macd_cache', False))
                            use_macd_cache_flag = macd_cache_available and use_macd_cache_enabled
                            use_memory_cache_flag = macd_memory_cache_available and use_macd_cache_enabled and st.session_state.get('macd_memory_cache_loaded', False)
                            
                            # 获取内存缓存实例（如果已加载）
                            memory_cache_instance = None
                            disk_cache_manager = None
                            
                            if use_memory_cache_flag:
                                try:
                                    memory_cache_instance = get_macd_memory_cache(preload=False)
                                    if memory_cache_instance.loaded_count == 0:
                                        memory_cache_instance = None  # 未预加载，降级到磁盘缓存
                                except:
                                    memory_cache_instance = None
                            
                            if use_macd_cache_flag and memory_cache_instance is None:
                                disk_cache_manager = get_macd_precompute_cache()
                            
                            for code, df in stock_data_cache.items():
                                try:
                                    df_macd = None
                                    
                                    # 优先从内存缓存获取（纳秒级）
                                    if memory_cache_instance and memory_cache_instance.has(code):
                                        cached_macd = memory_cache_instance.get(code)
                                        if cached_macd:
                                            df_macd = df.copy()
                                            df_macd['DIF'] = cached_macd['DIF'][-len(df):] if len(cached_macd['DIF']) >= len(df) else cached_macd['DIF']
                                            df_macd['DEA'] = cached_macd['DEA'][-len(df):] if len(cached_macd['DEA']) >= len(df) else cached_macd['DEA']
                                            df_macd['MACD'] = cached_macd['MACD'][-len(df):] if len(cached_macd['MACD']) >= len(df) else cached_macd['MACD']
                                            memory_hit_count += 1
                                            cache_hit_count += 1
                                    
                                    # 其次从磁盘缓存获取（毫秒级）
                                    elif disk_cache_manager:
                                        cached_macd = disk_cache_manager.get(code)
                                        if cached_macd:
                                            df_macd = df.copy()
                                            df_macd['DIF'] = cached_macd['DIF'][-len(df):] if len(cached_macd['DIF']) >= len(df) else cached_macd['DIF']
                                            df_macd['DEA'] = cached_macd['DEA'][-len(df):] if len(cached_macd['DEA']) >= len(df) else cached_macd['DEA']
                                            df_macd['MACD'] = cached_macd['MACD'][-len(df):] if len(cached_macd['MACD']) >= len(df) else cached_macd['MACD']
                                            cache_hit_count += 1
                                    
                                    # 缓存未命中，实时计算
                                    if df_macd is None:
                                        df_macd = calculate_macd(df)
                                    
                                    if df_macd is not None:
                                        macd_cache[code] = (df, df_macd)
                                    calc_count += 1
                                    
                                    if calc_count % 100 == 0 or calc_count == len(stock_data_cache):
                                        progress = 0.4 + (calc_count / len(stock_data_cache)) * 0.3  # 计算占30%
                                        progress_bar.progress(min(progress, 0.7))
                                        if memory_cache_instance:
                                            cache_info = f" | 内存命中: {memory_hit_count}, 缓存命中: {cache_hit_count}"
                                        elif use_macd_cache_flag:
                                            cache_info = f" | 缓存命中: {cache_hit_count}"
                                        else:
                                            cache_info = ""
                                        status_text.text(f"获取MACD: {calc_count}/{len(stock_data_cache)}{cache_info} | 已发现底背离: {len(divergence_results)}")
                                except Exception as e:
                                    continue
                            
                            # 步骤3: 批量检测底背离（并行）
                            status_text.text(f"步骤3/3: 批量检测底背离形态（{max_workers}线程并行）...")
                            check_count = 0
                            check_lock = threading.Lock()
                            stock_by_code = {stock['code']: stock for stock in all_stocks}
                            
                            def _detect_single_stock(args):
                                """检测单只股票的形态（用于线程池）"""
                                code, df, df_macd = args
                                nonlocal check_count
                                
                                stock = stock_by_code.get(code)
                                if not stock:
                                    return None, None
                                
                                div_result = None
                                cross_result = None
                                
                                try:
                                    # 扫描底背离 — 用原始 df（detect_macd_divergence 内部会计算 MACD）
                                    if scan_mode in ["底背离形态", "全部扫描"]:
                                        result = detect_macd_divergence(df, min_wave_length=3, max_cross_days=max_cross_days)
                                        if result:
                                            is_valid_result = result.is_valid if strict_mode else (result.confidence >= min_confidence)
                                            if is_valid_result and result.confidence >= min_confidence:
                                                result.code = stock['code']
                                                result.name = stock['name']
                                                div_result = result
                                    
                                    # 扫描即将金叉
                                    if scan_mode in ["即将金叉预测", "全部扫描"]:
                                        if detect_upcoming_golden_cross(df_macd):
                                            cross_result = {
                                                'code': stock['code'],
                                                'name': stock['name'],
                                                'type': stock['type'],
                                                'close': df['close'].iloc[-1],
                                                'dif': df_macd['DIF'].iloc[-1],
                                                'dea': df_macd['DEA'].iloc[-1]
                                            }
                                except Exception:
                                    pass
                                
                                # 更新进度（线程安全）
                                with check_lock:
                                    nonlocal check_count
                                    check_count += 1
                                    if check_count % 50 == 0 or check_count == len(macd_cache):
                                        progress = 0.7 + (check_count / len(macd_cache)) * 0.3
                                        progress_bar.progress(min(progress, 0.99))
                                        status_text.text(f"检测中: {check_count}/{len(macd_cache)} | 底背离: {len(divergence_results)} | 即将金叉: {len(upcoming_cross_results)}")
                                
                                return div_result, cross_result
                            
                            # 使用线程池并行检测
                            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                                futures = {
                                    executor.submit(_detect_single_stock, (code, df, df_macd)): code 
                                    for code, (df, df_macd) in macd_cache.items()
                                }
                                
                                for future in as_completed(futures):
                                    try:
                                        div_result, cross_result = future.result()
                                        if div_result:
                                            divergence_results.append(div_result)
                                        if cross_result:
                                            upcoming_cross_results.append(cross_result)
                                    except Exception:
                                        continue
                        else:
                            # 普通多进程并行扫描
                            from concurrent.futures import ProcessPoolExecutor, as_completed
                            import multiprocessing as mp
                            
                            status_text.text(f"使用多进程并行扫描（{max_workers}个进程）...")
                            
                            # 准备扫描参数
                            scan_args = []
                            for stock in all_stocks:
                                scan_args.append((
                                    stock, 
                                    tdx_path, 
                                    scan_mode, 
                                    strict_mode, 
                                    min_confidence,
                                    debug_mode,
                                    max_cross_days
                                ))
                            
                            # 并行处理
                            completed = 0
                            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                                future_to_stock = {
                                    executor.submit(_scan_single_stock_macd, args): args[0] 
                                    for args in scan_args
                                }
                                
                                for future in as_completed(future_to_stock):
                                    completed += 1
                                    progress = completed / total_stocks
                                    progress_bar.progress(min(progress, 0.99))
                                    
                                    if completed % 50 == 0 or completed == total_stocks:
                                        status_text.text(f"并行扫描中: {completed}/{total_stocks} | 已发现底背离: {len(divergence_results)} | 即将金叉: {len(upcoming_cross_results)}")
                                    
                                    try:
                                        result = future.result(timeout=10)
                                        if result:
                                            if result.get('type') == 'divergence':
                                                divergence_results.append(result['data'])
                                            elif result.get('type') == 'cross':
                                                upcoming_cross_results.append(result['data'])
                                    except Exception as e:
                                        continue
                    else:
                        # 串行扫描（适用于小批量）
                        # 使用缓存系统避免重复计算MACD
                        cache = _get_indicator_cache()
                        
                        # 初始化MACD缓存（优先内存缓存，其次磁盘缓存）
                        use_macd_cache_flag_serial = (macd_cache_available and use_macd_cache) if 'use_macd_cache' in locals() else False
                        use_memory_cache_flag_serial = macd_memory_cache_available and use_macd_cache_flag_serial and st.session_state.get('macd_memory_cache_loaded', False)
                        
                        # 获取内存缓存实例（如果已加载）
                        memory_cache_instance_serial = None
                        disk_cache_manager_serial = None
                        
                        if use_memory_cache_flag_serial:
                            try:
                                memory_cache_instance_serial = get_macd_memory_cache(preload=False)
                                if memory_cache_instance_serial.loaded_count == 0:
                                    memory_cache_instance_serial = None  # 未预加载，降级到磁盘缓存
                            except:
                                memory_cache_instance_serial = None
                        
                        if use_macd_cache_flag_serial and memory_cache_instance_serial is None:
                            disk_cache_manager_serial = get_macd_precompute_cache()
                        
                        for idx, stock in enumerate(all_stocks):
                            progress = (idx + 1) / total_stocks
                            progress_bar.progress(min(progress, 0.99))
                            
                            # 每10只更新一次状态，避免界面卡顿
                            if idx % 10 == 0 or idx == total_stocks - 1:
                                status_text.text(f"正在扫描: {stock['name']} ({stock['code']}) - {idx+1}/{total_stocks} | 已发现底背离: {len(divergence_results)} | 即将金叉: {len(upcoming_cross_results)}")
                            
                            try:
                                # 读取股票数据
                                df = read_tdx_data(code=stock['code'], security_type='stock')
                                
                                if df is None or len(df) < 30:
                                    continue
                                
                                # 尝试从内存缓存获取（纳秒级，最优先）
                                df_macd = None
                                if memory_cache_instance_serial and memory_cache_instance_serial.has(stock['code']):
                                    try:
                                        cached_macd = memory_cache_instance_serial.get(stock['code'])
                                        if cached_macd:
                                            df_macd = df.copy()
                                            df_macd['DIF'] = cached_macd['DIF'][-len(df):] if len(cached_macd['DIF']) >= len(df) else cached_macd['DIF']
                                            df_macd['DEA'] = cached_macd['DEA'][-len(df):] if len(cached_macd['DEA']) >= len(df) else cached_macd['DEA']
                                            df_macd['MACD'] = cached_macd['MACD'][-len(df):] if len(cached_macd['MACD']) >= len(df) else cached_macd['MACD']
                                    except:
                                        pass
                                
                                # 尝试从磁盘缓存获取（毫秒级，次优先）
                                if df_macd is None and disk_cache_manager_serial:
                                    try:
                                        cached_macd = disk_cache_manager_serial.get(stock['code'])
                                        if cached_macd:
                                            df_macd = df.copy()
                                            df_macd['DIF'] = cached_macd['DIF'][-len(df):] if len(cached_macd['DIF']) >= len(df) else cached_macd['DIF']
                                            df_macd['DEA'] = cached_macd['DEA'][-len(df):] if len(cached_macd['DEA']) >= len(df) else cached_macd['DEA']
                                            df_macd['MACD'] = cached_macd['MACD'][-len(df):] if len(cached_macd['MACD']) >= len(df) else cached_macd['MACD']
                                    except:
                                        pass
                                
                                # 尝试从技术指标缓存获取（备用）
                                if df_macd is None and cache is not None:
                                    try:
                                        indicators = cache.get_indicators(
                                            code=stock['code'],
                                            df=df,
                                            period_type='daily',
                                            force_recompute=False
                                        )
                                        if indicators and 'macd' in indicators:
                                            # 使用缓存的MACD数据
                                            df_macd = df.copy()
                                            df_macd['DIF'] = indicators['macd']['dif']
                                            df_macd['DEA'] = indicators['macd']['dea']
                                            df_macd['MACD'] = indicators['macd']['macd']
                                    except:
                                        pass
                                
                                # 如果缓存都没有，实时计算MACD
                                if df_macd is None:
                                    df_macd = calculate_macd(df)
                                
                                if df_macd is None:
                                    continue
                                
                                # 扫描底背离
                                if scan_mode in ["底背离形态", "全部扫描"]:
                                    result = detect_macd_divergence(df_macd, min_wave_length=3, max_cross_days=max_cross_days)  # 允许较短的波段
                                    if result:
                                        # 严格模式：必须已金叉；宽松模式：只要有底背离形态即可
                                        is_valid_result = result.is_valid if strict_mode else (result.confidence >= min_confidence)
                                        if is_valid_result and result.confidence >= min_confidence:
                                            result.code = stock['code']
                                            result.name = stock['name']
                                            divergence_results.append(result)
                                        elif debug_mode and idx < 20:  # 调试模式下显示前20只未通过的股票
                                            print(f"[调试] {stock['code']} 有背离但条件不符: is_valid={result.is_valid}, confidence={result.confidence:.2f}, price_a={result.price_a:.2f}, price_b={result.price_b:.2f}")
                                    elif debug_mode and idx < 20:
                                        # 检测为什么没结果
                                        df_test = df_macd
                                        if df_test is not None:
                                            from macd_divergence_scanner import find_downtrend_waves
                                            waves = find_downtrend_waves(df_test)
                                            print(f"[调试] {stock['code']} 无背离: waves={len(waves)}, data_len={len(df)}")
                                
                                # 扫描即将金叉
                                if scan_mode in ["即将金叉预测", "全部扫描"]:
                                    if detect_upcoming_golden_cross(df_macd):
                                        upcoming_cross_results.append({
                                            'code': stock['code'],
                                            'name': stock['name'],
                                            'type': stock['type'],
                                            'close': df['close'].iloc[-1],
                                            'dif': df_macd['DIF'].iloc[-1],
                                            'dea': df_macd['DEA'].iloc[-1]
                                        })
                                
                            except Exception as e:
                                continue
                    
                    progress_bar.empty()
                    status_text.empty()
                    
                    # 按可信度排序并限制结果数量
                    divergence_results.sort(key=lambda x: x.confidence, reverse=True)
                    divergence_results = divergence_results[:max_results]
                    upcoming_cross_results = upcoming_cross_results[:max_results]
                    
                    # 显示结果
                    st.markdown("---")
                    st.markdown("### 📊 扫描结果")
                    
                    # ===== MACD底背离形态 =====
                    st.markdown(f"#### 🎯 MACD底背离形态 ({len(divergence_results)}只)")
                    
                    if divergence_results:
                        # 每行显示2只股票
                        for i in range(0, len(divergence_results[:10]), 2):
                            cols = st.columns(2)
                            for j in range(2):
                                if i + j < len(divergence_results[:10]):
                                    result = divergence_results[i + j]
                                    # 获取股票板块信息
                                    stock_type = get_detailed_type(result.code, 'stock')
                                    with cols[j]:
                                        # 股票卡片
                                        st.markdown(f"""
                                        <div style="background: rgba(38, 166, 154, 0.1); padding: 15px; border-radius: 8px; margin-bottom: 15px; border-left: 4px solid #26a69a;">
                                            <strong>{i+j+1}. {result.code}（{result.name}）（{stock_type}）</strong><br>
                                            <small>可信度: {result.confidence:.1%} | 波段A: {result.price_a:.2f} | 波段B: {result.price_b:.2f}</small>
                                        </div>
                                        """, unsafe_allow_html=True)
                                        
                                        # 直接显示图表（不折叠）
                                        try:
                                            df_chart = read_tdx_data(code=result.code, security_type='stock')
                                            if df_chart is not None:
                                                fig = create_divergence_chart(result, df_chart, days=120)
                                                st.plotly_chart(fig, height=500)
                                        except Exception as e:
                                            st.error(f"图表加载失败: {e}")
                    else:
                        st.info("未找到符合条件的MACD底背离股票")
                    
                    # ===== 即将金叉预测 =====
                    st.markdown("---")
                    st.markdown(f"#### ⚡ 即将金叉预测 ({len(upcoming_cross_results)}只)")
                    
                    if upcoming_cross_results:
                        # 每行显示2只股票
                        for i in range(0, len(upcoming_cross_results[:20]), 2):
                            cols = st.columns(2)
                            for j in range(2):
                                if i + j < len(upcoming_cross_results[:20]):
                                    stock = upcoming_cross_results[i + j]
                                    # 获取股票板块信息
                                    stock_type = get_detailed_type(stock['code'], 'stock')
                                    with cols[j]:
                                        # 股票卡片
                                        st.markdown(f"""
                                        <div style="background: rgba(255, 193, 7, 0.1); padding: 15px; border-radius: 8px; margin-bottom: 15px; border-left: 4px solid #ffc107;">
                                            <strong>{i+j+1}. {stock['code']}（{stock['name']}）（{stock_type}）</strong><br>
                                            <small>最新价: {stock['close']:.2f} | DIF: {stock['dif']:.4f} | DEA: {stock['dea']:.4f}</small>
                                        </div>
                                        """, unsafe_allow_html=True)
                                        
                                        # 显示图表
                                        try:
                                            from macd_divergence_scanner import create_golden_cross_chart
                                            df_chart = read_tdx_data(code=stock['code'], security_type='stock')
                                            if df_chart is not None:
                                                fig = create_golden_cross_chart(stock, df_chart, days=120)
                                                st.plotly_chart(fig, height=500)
                                        except Exception as e:
                                            st.error(f"图表加载失败: {e}")
                    else:
                        st.info("未找到即将金叉的股票")
                    
                    # 导出结果
                    if divergence_results or upcoming_cross_results:
                        st.markdown("---")
                        st.markdown("#### 📥 导出结果")
                        
                        export_data = []
                        for r in divergence_results:
                            export_data.append({
                                '类型': 'MACD底背离',
                                '股票代码': r.code,
                                '股票名称': r.name,
                                '可信度': f"{r.confidence:.1%}",
                                '波段A价格': r.price_a,
                                '波段B价格': r.price_b
                            })
                        for r in upcoming_cross_results:
                            export_data.append({
                                '类型': '即将金叉',
                                '股票代码': r['code'],
                                '股票名称': r['name'],
                                '最新价': r['close'],
                                'DIF': r['dif'],
                                'DEA': r['dea']
                            })
                        
                        if export_data:
                            import pandas as pd
                            df_export = pd.DataFrame(export_data)
                            csv = df_export.to_csv(index=False, encoding='utf-8-sig')
                            st.download_button(
                                label="📥 下载CSV",
                                data=csv,
                                file_name=f"MACD扫描结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                                mime="text/csv"
                            )
                
                except Exception as e:
                    st.error(f"扫描过程中出错: {e}")
                    import traceback
                    st.code(traceback.format_exc())

    with tab7:
        # ── 导入股东人数分析模块 ──
        try:
            from shareholder_analyzer import (
                get_shareholder_history,
                get_market_shareholder_snapshot,
                scan_sector_concentration,
            )
            shareholder_available = True
        except Exception as e:
            st.error(f"股东人数分析模块加载失败: {e}")
            shareholder_available = False
        
        if shareholder_available:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
            
            st.markdown("""
            <div style='background-color: #f0f8ff; padding: 20px; border-radius: 10px; margin-bottom: 20px;'>
                <h3 style='color: #1565c0; margin-top: 0;'>🏦 筹码集中度分析</h3>
                <p style='color: #666;'>基于股东人数变化，识别主力吸筹/出货行为。人数↓ = 筹码集中，人数↑ = 筹码分散。</p>
            </div>
            """, unsafe_allow_html=True)
            
            # ══════════════════════════════════════════════
            #  上半部分：个股筹码分析
            # ══════════════════════════════════════════════
            st.markdown("### 📊 个股筹码分析")
            
            selected = st.session_state.get('selected_security', None)
            
            if selected is None or not selected.get('code'):
                st.info("👆 请先在「证券搜索」标签页选择一只股票，然后回来查看筹码分析。")
            else:
                stock_code = selected.get('code', '')
                stock_name = selected.get('name', stock_code)
                
                with st.spinner(f"正在获取 {stock_name}({stock_code}) 的股东人数数据..."):
                    history = get_shareholder_history(stock_code, years=3)
                
                if history is None:
                    st.warning(f"⚠️ 未能获取 {stock_name}({stock_code}) 的股东人数数据。可能是新股或数据源暂不支持。")
                else:
                    stats = history.get("stats", {})
                    records = history.get("records", [])
                    
                    # ── 关键指标卡片 ──
                    col1, col2, col3, col4 = st.columns(4)
                    
                    latest_count = stats.get("latest_count", 0)
                    consecutive_dec = stats.get("consecutive_decrease_quarters", 0)
                    avg_holding = stats.get("avg_holding_value", 0)
                    score = stats.get("concentration_score", 0)
                    q_change = stats.get("quarter_change_pct", 0)
                    
                    with col1:
                        count_display = f"{latest_count/10000:.1f}万" if latest_count > 10000 else str(latest_count)
                        st.metric("最新股东人数", count_display)
                    
                    with col2:
                        if consecutive_dec > 0:
                            st.metric("连续减少", f"{consecutive_dec}季度", delta=f"集中趋势", delta_color="inverse")
                        else:
                            st.metric("连续减少", "0季度")
                    
                    with col3:
                        holding_display = f"{avg_holding/10000:.1f}万" if avg_holding > 10000 else f"{avg_holding:.0f}元"
                        st.metric("人均持股市值", holding_display)
                    
                    with col4:
                        score_color = "normal" if score < 50 else "off"
                        label = "🟢 高集中" if score >= 70 else ("🟡 中等" if score >= 40 else "🔴 分散")
                        st.metric("集中度评分", f"{score}分 {label}")
                    
                    # ── 股东人数 vs 股价双轴图 ──
                    if records:
                        st.markdown("#### 📈 股东人数 vs 股价走势 (3年)")
                        
                        # 提取数据
                        dates = []
                        counts = []
                        for r in records:
                            d = r.get("date", "")
                            c = r.get("shareholder_count", 0)
                            if d and c > 0:
                                dates.append(d)
                                counts.append(c)
                        
                        # 尝试获取股价数据
                        price_data = None
                        price_dates = []
                        price_values = []
                        try:
                            from tdx_reader import read_tdx_data
                            df_price = read_tdx_data(code=stock_code, security_type='stock')
                            if df_price is not None and not df_price.empty and 'close' in df_price.columns:
                                price_data = df_price
                        except Exception:
                            pass
                        
                        if price_data is not None and 'trade_date' in price_data.columns:
                            price_data = price_data.sort_values('trade_date')
                            # 取与股东人数同期数据（按季度对齐）
                            price_dates = price_data['trade_date'].dt.strftime('%Y-%m-%d').tolist()
                            price_values = price_data['close'].tolist()
                        
                        # 创建双轴图
                        fig = make_subplots(specs=[[{"secondary_y": True}]])
                        
                        # 股东人数（左轴，柱状图）
                        if dates and counts:
                            fig.add_trace(
                                go.Bar(
                                    x=dates,
                                    y=counts,
                                    name="股东人数",
                                    marker_color="rgba(25, 118, 210, 0.6)",
                                    yaxis="y",
                                ),
                                secondary_y=False,
                            )
                        
                        # 股价（右轴，折线图）
                        if price_dates and price_values:
                            fig.add_trace(
                                go.Scatter(
                                    x=price_dates,
                                    y=price_values,
                                    name="股价",
                                    line=dict(color="#e53935", width=2),
                                    yaxis="y2",
                                ),
                                secondary_y=True,
                            )
                        
                        # 高亮筹码集中期（股东人数连续下降的区间）
                        if len(dates) >= 3 and len(counts) >= 3:
                            highlight_starts = []
                            in_decrease = False
                            start_idx = 0
                            for i in range(1, len(counts)):
                                if counts[i] < counts[i-1]:
                                    if not in_decrease:
                                        in_decrease = True
                                        start_idx = i - 1
                                else:
                                    if in_decrease and (i - 1 - start_idx) >= 2:
                                        highlight_starts.append((start_idx, i - 1))
                                    in_decrease = False
                            if in_decrease and (len(counts) - 1 - start_idx) >= 2:
                                highlight_starts.append((start_idx, len(counts) - 1))
                            
                            for hs, he in highlight_starts:
                                if hs < len(dates) and he < len(dates):
                                    fig.add_vrect(
                                        x0=dates[hs], x1=dates[he],
                                        fillcolor="rgba(46, 125, 50, 0.08)",
                                        line_width=0,
                                        layer="below",
                                    )
                        
                        fig.update_layout(
                            title=f"{stock_name}({stock_code}) 股东人数与股价联动",
                            xaxis_title="日期",
                            yaxis_title="股东人数（户）",
                            yaxis2_title="股价（元）",
                            hovermode="x unified",
                            legend=dict(orientation="h", y=1.12),
                            height=450,
                            margin=dict(t=60),
                        )
                        fig.update_yaxes(title_text="股东人数（户）", secondary_y=False, tickformat=",d")
                        fig.update_yaxes(title_text="股价（元）", secondary_y=True, tickformat=".2f")
                        
                        st.plotly_chart(fig, config={'displayModeBar': True})
                        
                        st.caption("🟢 绿色区域 = 筹码集中期（股东人数连续下降）")
                    
                    # ── 趋势明细表 ──
                    if records:
                        st.markdown("#### 📋 季报明细")
                        detail_rows = []
                        recent_records = records[-12:]  # 最近12个季度
                        for r in reversed(recent_records):
                            count_str = f"{r.get('shareholder_count', 0):,}"
                            change = ""
                            if r.get("change_pct") is not None:
                                pct = r["change_pct"]
                                if pct < -1:
                                    change = f"↓{abs(pct):.1f}% 🟢"
                                elif pct > 1:
                                    change = f"↑{pct:.1f}% 🔴"
                                else:
                                    change = "→ 持平"
                            detail_rows.append({
                                "日期": r.get("date", ""),
                                "股东人数": count_str,
                                "环比变化": change,
                                "人均持股市值": f"{r.get('avg_holding_value', 0)/10000:.1f}万" if (r.get('avg_holding_value') or 0) > 0 else "-",
                            })
                        
                        if detail_rows:
                            df_detail = pd.DataFrame(detail_rows)
                            st.dataframe(df_detail, hide_index=True, use_container_width=True)
            
            # ══════════════════════════════════════════════
            #  下半部分：板块集中度扫描
            # ══════════════════════════════════════════════
            st.markdown("---")
            st.markdown("### 🔍 板块筹码集中度扫描")
            st.caption("选择板块，扫描该板块内股东人数正在集中的股票")
            
            # 板块选择
            sector_options = {
                "上证主板": "上证主板",
                "深证主板": "深证主板",
                "创业板": "创业板",
                "科创板": "科创板",
                "中小板": "中小板",
            }
            
            if 'shareholder_selected_sectors' not in st.session_state:
                st.session_state.shareholder_selected_sectors = []
            
            sector_cols = st.columns(5)
            selected_sectors = []
            for idx, (key, name) in enumerate(sector_options.items()):
                with sector_cols[idx]:
                    if st.checkbox(name, key=f"shareholder_sector_{key}", value=(name in st.session_state.shareholder_selected_sectors)):
                        selected_sectors.append(name)
            st.session_state.shareholder_selected_sectors = selected_sectors
            
            # 扫描按钮
            col_scan_btn, col_note = st.columns([1, 3])
            with col_scan_btn:
                start_scan = st.button("🔍 扫描筹码集中度", type="primary", use_container_width=True)
            with col_note:
                if start_scan:
                    if not selected_sectors:
                        st.warning("请至少选择一个板块")
                    else:
                        st.info("正在准备扫描...")
            
            if start_scan and selected_sectors:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                try:
                    from tdx_reader import get_tdx_reader, set_tdx_data_dir
                    tdx_path = st.session_state.get('tdx_data_path', r"D:\new_tdx\vipdoc")
                    set_tdx_data_dir(tdx_path)
                    reader = get_tdx_reader(tdx_path)
                    available = reader.get_available_codes()
                    
                    # 收集目标板块的股票代码
                    target_codes = set()
                    for code_info in available.get('sh', []) + available.get('sz', []):
                        if isinstance(code_info, dict):
                            code = code_info.get('code', '')
                        else:
                            code = str(code_info)
                        stock_type = get_detailed_type(code, 'stock')
                        if stock_type in selected_sectors:
                            target_codes.add(code)
                    
                    total = len(target_codes)
                    status_text.text(f"目标板块共 {total} 只股票，正在获取股东人数数据...")
                    
                    # 使用快照模式扫描
                    def scan_progress(current, t):
                        progress_bar.progress(min(current / max(t, 1), 0.99))
                        status_text.text(f"已处理: {current}/{t}")
                    
                    scan_result = scan_sector_concentration(
                        list(target_codes),
                        sector_name=",".join(selected_sectors),
                        progress_callback=scan_progress,
                        force_refresh=False,
                    )
                    
                    progress_bar.empty()
                    status_text.empty()
                    
                    scan_time = scan_result.get("scan_time_seconds", 0)
                    results = scan_result.get("results", [])
                    snapshot_used = scan_result.get("snapshot_used", False)
                    snapshot_error = scan_result.get("snapshot_error", "")
                    
                    if not results and (snapshot_error or not snapshot_used):
                        if snapshot_error:
                            st.warning(f"⚠️ 快照数据获取失败: {snapshot_error}。请检查网络或稍后重试。")
                        else:
                            st.warning("⚠️ 快照数据获取失败，缓存中暂无数据。请先在「个股筹码分析」中查看几只股票以建立缓存。")
                    elif results:
                        st.success(f"✅ 扫描完成！共 {len(results)} 只有效结果，耗时 {scan_time:.1f}秒")
                    
                    if results:
                        # 过滤：只看集中度评分>0的（即股东人数在减少的）
                        concentrating = [r for r in results if r.get("consecutive_decrease_quarters", 0) > 0]
                        concentrating.sort(key=lambda x: x.get("concentration_score", 0), reverse=True)
                        
                        st.markdown(f"#### 🎯 筹码集中股票（{len(concentrating)}只）")
                        st.caption("按集中度评分排序 | 评分维度：连续减少季度 + 变化幅度 + 人均持股")
                        
                        if concentrating:
                            for i, stock in enumerate(concentrating[:30]):
                                score = stock.get("concentration_score", 0)
                                dec = stock.get("consecutive_decrease_quarters", 0)
                                change_pct = stock.get("change_pct", 0)
                                holding = stock.get("avg_holding_value", 0)
                                price = stock.get("current_price", 0)
                                
                                score_color = "#2e7d32" if score >= 70 else ("#f57f17" if score >= 40 else "#666")
                                signal_icon = "🟢" if score >= 70 else ("🟡" if score >= 40 else "⚪")
                                
                                col_card, col_chart = st.columns([2, 1])
                                with col_card:
                                    st.markdown(f"""
                                    <div style="background: rgba(33, 150, 243, 0.05); padding: 12px; border-radius: 8px; border-left: 3px solid {score_color};">
                                        <strong>{signal_icon} {i+1}. {stock['code']} — {stock['name']}</strong><br>
                                        <small>
                                            最新人数: {stock['latest_count']:,}户 |
                                            连续↓{dec}季度 |
                                            变化: {change_pct:+.1f}% |
                                            人均持股: {holding/10000:.1f}万 |
                                            集中度: <b style='color:{score_color}'>{score}分</b>
                                        </small>
                                    </div>
                                    """, unsafe_allow_html=True)
                                
                                with col_chart:
                                    if st.button("查看筹码图", key=f"chip_{stock['code']}", use_container_width=True):
                                        with st.spinner(f"获取 {stock['name']} 详情..."):
                                            detail = get_shareholder_history(stock['code'], years=3)
                                            if detail:
                                                st.session_state['shareholder_detail'] = detail
                                                st.session_state['shareholder_detail_code'] = stock['code']
                                                st.rerun()
                        else:
                            st.info("未找到筹码正在集中的股票")
                        
                        # 显示缓存的个股详情
                        if st.session_state.get('shareholder_detail_code'):
                            detail = st.session_state.get('shareholder_detail')
                            if detail:
                                st.markdown("---")
                                st.markdown(f"#### 📊 个股详情: {detail.get('name', detail.get('code', ''))}")
                                
                                records = detail.get("records", [])
                                if records:
                                    dates = [r.get("date", "")[:7] for r in records if r.get("date")]
                                    counts = [r.get("shareholder_count", 0) for r in records if r.get("date")]
                                    
                                    fig2 = go.Figure()
                                    fig2.add_trace(go.Bar(x=dates, y=counts, name="股东人数", marker_color="rgba(25, 118, 210, 0.6)"))
                                    fig2.update_layout(height=350, title="股东人数变化趋势")
                                    st.plotly_chart(fig2, config={'displayModeBar': True})
                                    
                                    if st.button("关闭详情", use_container_width=True):
                                        st.session_state.pop('shareholder_detail_code', None)
                                        st.session_state.pop('shareholder_detail', None)
                                        st.rerun()
                    else:
                        if not (snapshot_error or not snapshot_used):
                            st.info("未获取到有效数据，请检查数据源或稍后重试")
                
                except Exception as e:
                    st.error(f"扫描出错: {e}")
                    import traceback
                    st.code(traceback.format_exc())
        
        else:
            st.warning("⚠️ 股东人数分析模块加载失败，请检查 akshare 是否已安装：`pip install akshare`")

    with tab8:
        try:
            from virtual_portfolio import Portfolio, get_realtime_price, get_or_create_portfolio

            # 加载/初始化账户
            if "portfolio" not in st.session_state:
                st.session_state.portfolio = get_or_create_portfolio()
            pf: Portfolio = st.session_state.portfolio

            st.markdown("""
            <div style='background: linear-gradient(135deg, #1a237e 0%, #283593 100%); padding: 20px; border-radius: 10px; margin-bottom: 20px;'>
                <h3 style='color: #ffd54f; margin-top: 0;'>💰 虚拟交易盘</h3>
                <p style='color: rgba(255,255,255,0.8);'>模拟真实交易环境，验证你的投资策略 | 初始资金 100万</p>
            </div>
            """, unsafe_allow_html=True)

            # ── 获取持仓股票价格 ──
            watchlist = st.session_state.get("watchlist", [])
            all_codes = list(pf.positions.keys())
            for w in watchlist:
                code = w.get("code", "")
                if code and code not in all_codes:
                    all_codes.append(code)

            price_map = {}
            price_names = {}
            price_dates = {}
            price_sources = {}
            price_chg = {}
            for code in all_codes:
                info = get_realtime_price(code)
                if info:
                    price_map[code] = info["price"]
                    price_names[code] = info.get("name", code)
                    price_dates[code] = info.get("date", "")
                    price_sources[code] = info.get("source", "")
                    price_chg[code] = info.get("change_pct", 0)

            # ── 账户概览 ──
            total_mv = pf.get_total_market_value(price_map)
            total_asset = pf.cash + total_mv
            pnl = pf.get_pnl(price_map)
            pnl_pct = pf.get_pnl_pct(price_map)

            col_a, col_b, col_c, col_d, col_e = st.columns(5)
            with col_a:
                pnl_color = "normal" if pnl >= 0 else "inverse"
                st.metric("总资产", f"{total_asset/10000:.2f}万", delta=f"{pnl:+.0f}", delta_color=pnl_color)
            with col_b:
                st.metric("现金", f"{pf.cash/10000:.2f}万")
            with col_c:
                st.metric("持仓市值", f"{total_mv/10000:.2f}万")
            with col_d:
                st.metric("累计盈亏", f"{pnl:+.2f}元", delta=f"{pnl_pct:+.2f}%", delta_color=pnl_color)
            with col_e:
                st.metric("持仓数", f"{len(pf.positions)}只")

            st.markdown("---")

            # ── 持仓明细 ──
            st.markdown("### 📊 持仓明细")

            if pf.positions:
                rows = []
                for code, pos in pf.positions.items():
                    price = price_map.get(code, 0)
                    shares = pos["shares"]
                    cost = pos["avg_cost"]
                    mv = shares * price
                    cost_total = shares * cost
                    pnl_stock = mv - cost_total
                    pnl_stock_pct = (price / cost - 1) * 100 if cost > 0 else 0
                    name = price_names.get(code, code)

                    pnl_display = f"{pnl_stock:+.0f}"
                    pnl_pct_display = f"{pnl_stock_pct:+.1f}%"

                    # 用日期+来源标注价格时效性
                    price_date = price_dates.get(code, "")
                    price_src = price_sources.get(code, "")
                    price_label = f"{price:.2f}" if price else "N/A"
                    if price_date:
                        price_label += f"({price_date})"

                    rows.append({
                        "代码": code,
                        "名称": name[:6],
                        "持仓": f"{shares}股",
                        "成本": f"{cost:.2f}",
                        "现价": price_label,
                        "来源": price_src,
                        "市值": f"{mv/10000:.1f}万" if mv >= 10000 else f"{mv:.0f}",
                        "盈亏": pnl_display,
                        "盈亏%": pnl_pct_display,
                    })

                df_pos = pd.DataFrame(rows)
                st.dataframe(
                    df_pos,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "盈亏": st.column_config.TextColumn("盈亏", help="持仓浮动盈亏"),
                        "盈亏%": st.column_config.TextColumn("盈亏%", help="持仓浮动盈亏百分比"),
                    },
                )
            else:
                st.info("暂无持仓。使用下方交易面板开始你的第一笔交易！")

            # ── 自选股行情 ──
            if watchlist:
                st.markdown("#### 📈 自选股行情")
                refresh_col1, refresh_col2 = st.columns([1, 6])
                with refresh_col1:
                    if st.button("🔄 刷新行情", use_container_width=True):
                        # 清除行情缓存，强制重新获取
                        import virtual_portfolio as vp_mod
                        vp_mod._sina_spot_cache = None
                        vp_mod._sina_spot_cache_time = None
                        vp_mod._intraday_cache.clear()
                        st.rerun()
                wrows = []
                for w in watchlist[:10]:
                    code = w.get("code", "")
                    name = w.get("name", code)
                    price = price_map.get(code)
                    if price is not None:
                        p_date = price_dates.get(code, "")
                        p_src = price_sources.get(code, "")
                        p_chg = price_chg.get(code, 0)
                        price_label = f"{price:.2f}"
                        if p_date:
                            price_label += f"({p_date})"
                        chg_str = f"{p_chg:+.2f}%"
                        wrows.append({
                            "代码": code,
                            "名称": name[:8],
                            "涨跌幅": chg_str,
                            "现价": price_label,
                            "来源": p_src,
                        })
                if wrows:
                    st.dataframe(pd.DataFrame(wrows), hide_index=True, use_container_width=True)

                # ── 分时走势图 ──
                st.markdown("#### 📉 分时走势")
                from virtual_portfolio import get_intraday_data
                chart_codes = [w.get("code", "") for w in watchlist[:5] if w.get("code")]
                chart_tabs = st.tabs([f"{w.get('code', '')} {w.get('name', '')[:4]}" for w in watchlist[:5]])
                for i, (tab, code) in enumerate(zip(chart_tabs, chart_codes)):
                    with tab:
                        idf = get_intraday_data(code)
                        if idf is not None and not idf.empty and "close" in idf.columns:
                            # 确保 close/open/high/low 为 float 类型
                            for col in ["close", "open", "high", "low"]:
                                idf[col] = pd.to_numeric(idf[col], errors="coerce")
                            idf = idf.dropna(subset=["close"])
                            if idf.empty:
                                st.info(f"暂无 {code} 的有效分时数据")
                                continue
                            # 取昨收价作为基准线（取第一行的开盘价近似）
                            base_price = float(idf["open"].iloc[0])
                            # 计算涨跌幅 = (close - base) / base * 100
                            idf = idf.copy()
                            idf["pct"] = (idf["close"].astype(float) - base_price) / base_price * 100
                            chart_data = idf[["pct"]].copy()
                            chart_data.index = range(len(chart_data))
                            st.line_chart(chart_data, use_container_width=True)
                            # 在图表下方显示关键指标
                            last_close = float(idf["close"].iloc[-1])
                            pct_from_base = (last_close - base_price) / base_price * 100
                            high = float(idf["high"].max())
                            low = float(idf["low"].min())
                            col_info1, col_info2, col_info3 = st.columns(3)
                            with col_info1:
                                chg_color = "🔴" if pct_from_base < 0 else "🟢"
                                st.caption(f"{chg_color} 涨跌: {pct_from_base:+.2f}%")
                            with col_info2:
                                st.caption(f"📊 最高: {high:.2f} | 最低: {low:.2f}")
                            with col_info3:
                                st.caption(f"📈 今开: {base_price:.2f} → 现价: {last_close:.2f}")
                        else:
                            st.info(f"暂无 {code} 的分时数据")

            st.markdown("---")

            # ── 交易面板 ──
            st.markdown("### 🔍 交易操作")

            trade_code = st.text_input("股票代码", placeholder="输入6位代码，如 000001", key="trade_code_input")

            if trade_code and len(trade_code) == 6:
                # 获取价格
                price_info = get_realtime_price(trade_code)
                if price_info:
                    current_price = price_info["price"]
                    stock_name = price_info.get("name", trade_code)
                    price_source = price_info.get("source", "?")
                    price_date = price_info.get("date", "")

                    price_caption = f"**{stock_name}** | 参考价: {current_price:.2f}元"
                    if price_date:
                        price_caption += f" ({price_date})"
                    price_caption += f" | 来源: {price_source}"
                    st.caption(price_caption)

                    col_buy, col_sell = st.columns(2)

                    with col_buy:
                        buy_shares = st.number_input("买入股数（100的倍数）", min_value=100, step=100, value=100, key="buy_shares")
                        buy_amount = buy_shares * current_price
                        buy_fee = max(buy_amount * 0.0001, 5)
                        st.caption(f"预估金额: {buy_amount:,.0f}元 | 佣金: {buy_fee:.2f}元 | 可用现金: {pf.cash:,.0f}元")
                        if st.button(f"🟢 买入 {stock_name}", type="primary", use_container_width=True):
                            success, msg = pf.buy(trade_code, stock_name, buy_shares, current_price)
                            if success:
                                pf.save()
                                st.session_state.portfolio = pf
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)

                    with col_sell:
                        held = pf.positions.get(trade_code, {}).get("shares", 0)
                        sell_shares = st.number_input("卖出股数（0=全部）", min_value=0, step=100, value=min(held, 100) if held else 0, key="sell_shares")
                        if held > 0:
                            sell_amount = (sell_shares if sell_shares > 0 else held) * current_price
                            sell_fee = max(sell_amount * 0.0001, 5)
                            sell_tax = sell_amount * 0.001
                            st.caption(f"持仓: {held}股 | 预估回款: {sell_amount - sell_fee - sell_tax:,.0f}元 | 佣金: {sell_fee:.2f} | 印花税: {sell_tax:.2f}")
                            if st.button(f"🔴 卖出 {stock_name}", use_container_width=True):
                                success, msg = pf.sell(trade_code, stock_name, sell_shares, current_price)
                                if success:
                                    pf.save()
                                    st.session_state.portfolio = pf
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)
                        else:
                            st.caption("暂无该股票持仓")
                else:
                    st.warning(f"未获取到 {trade_code} 的价格信息。请检查代码是否正确。")
            elif trade_code:
                st.info("请输入完整的6位代码")

            st.markdown("---")

            # ── 交易记录 ──
            st.markdown("### 📋 交易记录")
            if pf.trade_history:
                df_hist = pd.DataFrame(pf.trade_history[-20:][::-1])  # 最近20条，倒序

                show_cols = ["date", "code", "name", "type", "shares", "price", "amount", "commission", "stamp_tax"]
                if "pnl" in df_hist.columns:
                    show_cols.append("pnl")
                df_show = df_hist[show_cols].copy()
                df_show.columns = ["时间", "代码", "名称", "类型", "股数", "价格", "金额", "佣金", "印花税"] + (["盈亏"] if "pnl" in df_hist.columns else [])

                st.dataframe(df_show, hide_index=True, use_container_width=True)

                col_export, col_reset = st.columns(2)
                with col_export:
                    csv_data = df_show.to_csv(index=False, encoding="utf-8-sig")
                    st.download_button("📥 导出交易记录", csv_data, "trade_history.csv", "text/csv")
                with col_reset:
                    if st.button("🔄 重置账户", type="secondary", use_container_width=True):
                        st.warning("确定要重置账户吗？所有持仓和记录将被清空。")
                        if st.button("确认重置", key="confirm_reset"):
                            pf.reset()
                            pf.save()
                            st.session_state.portfolio = pf
                            st.success("账户已重置")
                            st.rerun()
            else:
                st.info("暂无交易记录")

        except Exception as e:
            st.error(f"虚拟盘模块加载失败: {e}")
            import traceback
            st.code(traceback.format_exc())

if __name__ == "__main__":
    main()
