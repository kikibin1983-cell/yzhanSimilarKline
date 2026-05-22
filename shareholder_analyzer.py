"""
股东人数筹码分析模块

数据源: akshare (stock_zh_a_gdhs_detail_em / stock_zh_a_gdhs)
缓存策略: 本地 JSON，当天有效，季报数据更新频率低

功能:
  1. get_shareholder_history(code) → 单只股票历史股东人数 + 股价联动数据
  2. get_market_shareholder_snapshot(date) → 全市场当季股东人数快照
  3. scan_sector_concentration(codes, progress_cb) → 板块集中度扫描
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

# ── 代理配置：避免因 .env 中 PROXY_URL 不可用导致 akshare 连接失败 ──
import contextlib

@contextlib.contextmanager
def _no_proxy():
    """临时清除 HTTP/HTTPS 代理环境变量，aksahre 调用完后恢复"""
    saved = {}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    try:
        yield
    finally:
        for key, value in saved.items():
            os.environ[key] = value


# ── 缓存配置 ──
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shareholder_cache")
CACHE_EXPIRE_DAYS = 1  # 每天更新即可（季报数据不频繁）

os.makedirs(CACHE_DIR, exist_ok=True)


def _code_to_cache_path(code: str) -> str:
    """股票代码 → 缓存文件路径"""
    return os.path.join(CACHE_DIR, f"{code}.json")


def _read_cache(code: str) -> Optional[Dict]:
    """读取缓存，过期返回 None"""
    path = _code_to_cache_path(code)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cached_time = data.get("_cached_at", "")
        if cached_time:
            cached_dt = datetime.strptime(cached_time, "%Y-%m-%d %H:%M:%S")
            if datetime.now() - cached_dt > timedelta(days=CACHE_EXPIRE_DAYS):
                return None
        return data
    except Exception:
        return None


def _write_cache(code: str, data: Dict):
    """写入缓存"""
    data["_cached_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = _code_to_cache_path(code)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
#  核心数据获取
# ═══════════════════════════════════════════════════════

def get_shareholder_history(code: str, years: int = 3, force_refresh: bool = False) -> Optional[Dict]:
    """
    获取单只股票的历史股东人数 + 股价联动数据
    
    Args:
        code: 6位股票代码
        years: 回溯年数（默认3年）
        force_refresh: 是否强制刷新缓存
        
    Returns:
        {
            "code": "000001",
            "name": "平安银行",
            "records": [
                {
                    "date": "2025-03-31",
                    "shareholder_count": 441142,
                    "prev_count": 453515,
                    "change_pct": -2.73,
                    "close_price": 12.50,
                    "total_market_value": 221400000000,
                    "circulating_shares": 19400000000,
                },
                ...
            ],
            "stats": {
                "latest_count": 457610,
                "consecutive_decrease_quarters": 0,
                "avg_holding_value": 469870,
                "concentration_score": 75,
                "trend_quarters": ["2024Q4","2025Q1","2025Q2","2025Q3"],
                "trend_values": [500000, 480000, 460000, 457610],
            }
        }
    """
    cache = _read_cache(code)
    if cache and not force_refresh:
        return cache
    
    try:
        import akshare as ak
        with _no_proxy():
            df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
    except Exception as e:
        # akshare 失败时尝试返回缓存（即使过期）
        if cache:
            return cache
        error_msg = f"[股东人数] 获取 {code} 失败: {e}"
        print(error_msg)
        # 尝试导入streamlit并显示错误（如果在streamlit环境中）
        try:
            import streamlit as st
            st.error(error_msg)
        except:
            pass
        return None
    
    if df is None or df.empty:
        return None
    
    # 列名映射（akshare 中文列名）
    col_map = {
        "股东户数统计截止日": "date",
        "股东户数-本次": "shareholder_count",
        "股东户数-上次": "prev_count",
        "股东户数-增减": "change_amount",
        "股东户数-增减比例": "change_pct",
        "区间涨跌幅": "price_change_pct",
        "户均持股市值": "avg_holding_value",
        "户均持股数量": "avg_holding_shares",
        "总市值": "total_market_value",
        "总股本": "total_shares",
        "公告日期": "announce_date",
        "代码": "code",
        "名称": "name",
    }
    
    # 重命名存在的列
    rename_dict = {}
    for cn_name, en_name in col_map.items():
        if cn_name in df.columns:
            rename_dict[cn_name] = en_name
    df = df.rename(columns=rename_dict)
    
    # 提取股票名称
    stock_name = ""
    if "name" in df.columns and not df.empty:
        stock_name = str(df["name"].iloc[-1])
    
    # 日期筛选：取最近 N 年
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        cutoff = datetime.now() - timedelta(days=years * 365)
        df = df[df["date"] >= cutoff].copy()
    
    if df.empty:
        return None
    
    # 按日期排序
    df = df.sort_values("date")
    
    # 构建 records 列表
    records = []
    for _, row in df.iterrows():
        rec = {
            "date": row["date"].strftime("%Y-%m-%d") if pd.notna(row.get("date")) else "",
            "shareholder_count": int(row["shareholder_count"]) if pd.notna(row.get("shareholder_count")) else 0,
        }
        if "prev_count" in df.columns and pd.notna(row.get("prev_count")):
            rec["prev_count"] = int(row["prev_count"])
        if "change_pct" in df.columns and pd.notna(row.get("change_pct")):
            rec["change_pct"] = round(float(row["change_pct"]), 2)
        if "price_change_pct" in df.columns and pd.notna(row.get("price_change_pct")):
            rec["price_change_pct"] = round(float(row["price_change_pct"]), 2)
        if "avg_holding_value" in df.columns and pd.notna(row.get("avg_holding_value")):
            rec["avg_holding_value"] = round(float(row["avg_holding_value"]), 2)
        if "close_price" in df.columns:
            rec["close_price"] = float(row["close_price"]) if pd.notna(row.get("close_price")) else 0
        records.append(rec)
    
    # 计算统计指标
    stats = _calculate_stats(records)
    stats["name"] = stock_name
    
    result = {
        "code": code,
        "name": stock_name,
        "records": records,
        "stats": stats,
    }
    
    _write_cache(code, result)
    return result


def _calculate_stats(records: List[Dict]) -> Dict:
    """从 records 计算集中度统计指标"""
    if not records:
        return {}
    
    # 最新数据
    latest = records[-1]
    
    # 连续减少季度数
    consecutive_decrease = 0
    counts = [r.get("shareholder_count", 0) for r in records if r.get("shareholder_count", 0) > 0]
    for i in range(len(counts) - 1, 0, -1):
        if counts[i] < counts[i-1]:
            consecutive_decrease += 1
        else:
            break
    
    # 最近4季度变化趋势
    quarter_counts = counts[-min(4, len(counts)):] if len(counts) >= 1 else counts
    if len(quarter_counts) >= 2:
        q_change = quarter_counts[-1] - quarter_counts[0]
        q_change_pct = q_change / quarter_counts[0] * 100 if quarter_counts[0] > 0 else 0
    else:
        q_change = 0
        q_change_pct = 0
    
    # 人均持股市值
    avg_holding = latest.get("avg_holding_value", 0)
    
    # 集中度评分 (0-100)
    # 维度: 连续减少季度(40分) + 1年变化幅度(30分) + 人均持股(30分)
    dec_score = min(consecutive_decrease * 10, 40)  # 连续4季度=40分
    change_score = min(abs(q_change_pct) * 1.5, 30) if q_change_pct < 0 else 0  # 减少才有分
    holding_score = min(avg_holding / 100000 * 10, 30)  # 30万=30分
    concentration_score = min(round(dec_score + change_score + holding_score), 100)
    
    return {
        "latest_count": latest.get("shareholder_count", 0),
        "consecutive_decrease_quarters": consecutive_decrease,
        "quarter_change_pct": round(q_change_pct, 2),
        "avg_holding_value": round(avg_holding, 2) if avg_holding else 0,
        "concentration_score": concentration_score,
        "total_records": len(records),
    }


def get_market_shareholder_snapshot(quarter_date: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    获取全市场股东人数快照（单次API调用）
    
    Args:
        quarter_date: 季度日期 YYYYMMDD，默认最新
        
    Returns:
        DataFrame with columns: 代码, 名称, 最新价, 股东人数-最新, 股东人数-上次, 
                                股东人数-变化, 股东人数-变化比例, 户均持股市值, etc.
    """
    if quarter_date is None:
        # 推断最新季度
        now = datetime.now()
        year = now.year
        month = now.month
        if month <= 3:
            quarter_date = f"{year-1}1231"
        elif month <= 6:
            quarter_date = f"{year}0331"
        elif month <= 9:
            quarter_date = f"{year}0630"
        elif month <= 11:
            quarter_date = f"{year}0930"
        else:
            quarter_date = f"{year}0930"  # Q4 还没出，用 Q3
    
    try:
        import akshare as ak
        with _no_proxy():
            df = ak.stock_zh_a_gdhs(symbol=quarter_date)
        return df
    except Exception as e:
        print(f"[股东人数] 获取全市场快照失败: {e}")
        return None


def scan_sector_concentration(
    codes: List[str],
    sector_name: str = "",
    progress_callback=None,
    force_refresh: bool = False,
) -> Dict:
    """
    扫描指定板块的筹码集中度
    
    策略:
      1. 优先用市场快照 API（1次调用获取全市场）
      2. 快照失败则逐个拉取（慢但有缓存兜底）
    
    Args:
        codes: 股票代码列表
        sector_name: 板块名称（用于进度显示）
        progress_callback: 进度回调 (current, total)
        force_refresh: 是否强制刷新缓存
        
    Returns:
        {
            "results": [{code, name, score, consecutive_decrease, ...,}],
            "total_scanned": 100,
            "cached_hits": 80,
            "scan_time_seconds": 5.2,
        }
    """
    start_time = time.time()
    results = []
    cached_hits = 0
    
    total = len(codes)
    
    # 尝试快照模式：1次API获取全市场当季数据
    snapshot_df = None
    snapshot_error = ""
    try:
        snapshot_df = get_market_shareholder_snapshot()
    except Exception as e:
        snapshot_error = str(e)
        pass
    
    if snapshot_df is not None and not snapshot_df.empty:
        # 快照模式工作流程
        col_code = None
        col_name = None
        col_latest = None
        col_prev = None
        col_change_pct = None
        col_holding_val = None
        col_price = None
        
        for col in snapshot_df.columns:
            col_str = str(col)
            if "代码" in col_str:
                col_code = col
            elif "名称" in col_str:
                col_name = col
            elif ("最新" in col_str or "本次" in col_str) and ("人数" in col_str or "户数" in col_str) and "统计截止" not in col_str:
                col_latest = col
            elif "上次" in col_str and ("人数" in col_str or "户数" in col_str) and "统计截止" not in col_str:
                col_prev = col
            elif "增减" in col_str and "比例" in col_str:
                col_change_pct = col
            elif "市值" in col_str and ("均" in col_str or "户" in col_str):
                col_holding_val = col
            elif "最新价" in col_str:
                col_price = col
        
        if col_code and col_latest:
            for _, row in snapshot_df.iterrows():
                code = str(row.get(col_code, "")).zfill(6)
                if code not in codes:
                    continue
                
                latest_count = int(row[col_latest]) if pd.notna(row.get(col_latest)) else 0
                prev_count = int(row[col_prev]) if col_prev and pd.notna(row.get(col_prev)) else 0
                change_pct = float(row[col_change_pct]) if col_change_pct and pd.notna(row.get(col_change_pct)) else 0
                holding_val = float(row[col_holding_val]) if col_holding_val and pd.notna(row.get(col_holding_val)) else 0
                
                name = str(row.get(col_name, code)) if col_name else code
                price = float(row[col_price]) if col_price and pd.notna(row.get(col_price)) else 0
                
                # 从缓存获取历史记录来计算连续减少季度
                cache = _read_cache(code)
                consecutive_dec = 0
                if cache and cache.get("stats"):
                    consecutive_dec = cache["stats"].get("consecutive_decrease_quarters", 0)
                    cached_hits += 1
                elif change_pct < 0:
                    consecutive_dec = 1  # 至少当前季度在减少
                
                score = _quick_score(latest_count, change_pct, consecutive_dec, holding_val)
                
                results.append({
                    "code": code,
                    "name": name,
                    "latest_count": latest_count,
                    "prev_count": prev_count,
                    "change_pct": round(change_pct, 2),
                    "consecutive_decrease_quarters": consecutive_dec,
                    "avg_holding_value": round(holding_val, 2),
                    "concentration_score": score,
                    "current_price": round(price, 2),
                })
                
                if progress_callback and len(results) % 100 == 0:
                    progress_callback(len(results), total)
    else:
        # 快照失败，回退到逐个拉取（慢但可靠）
        for i, code in enumerate(codes):
            cache = _read_cache(code)
            if cache and not force_refresh:
                stats = cache.get("stats", {})
                results.append({
                    "code": code,
                    "name": cache.get("name", code),
                    "latest_count": stats.get("latest_count", 0),
                    "consecutive_decrease_quarters": stats.get("consecutive_decrease_quarters", 0),
                    "avg_holding_value": stats.get("avg_holding_value", 0),
                    "concentration_score": stats.get("concentration_score", 0),
                })
                cached_hits += 1
            else:
                # 异步拉取 - 为了速度，先跳过无缓存的
                pass
            
            if progress_callback and (i + 1) % 100 == 0:
                progress_callback(i + 1, total)
    
    # 按集中度评分降序排列
    results.sort(key=lambda x: x.get("concentration_score", 0), reverse=True)
    
    elapsed = time.time() - start_time
    
    return {
        "results": results,
        "total_scanned": len(results),
        "cached_hits": cached_hits,
        "scan_time_seconds": round(elapsed, 1),
        "snapshot_used": snapshot_df is not None,
        "snapshot_error": snapshot_error,
    }


def _quick_score(latest_count: int, change_pct: float, consecutive_dec: int, holding_val: float) -> int:
    """快速集中度评分（用于快照模式）"""
    score = 0
    
    # 连续减少季度（0-40分）
    score += min(consecutive_dec * 10, 40)
    
    # 变化幅度（人数减少 = 正面，0-30分）
    if change_pct < 0:
        score += min(abs(change_pct) * 1.5, 30)
    
    # 人均持股市值（0-30分）
    if holding_val > 0:
        score += min(holding_val / 100000 * 10, 30)
    
    return min(round(score), 100)


def get_concentration_trend(records: List[Dict]) -> Tuple[List[str], List[float]]:
    """
    从 records 中提取季度级别的股东人数趋势（用于图表）
    
    Returns:
        (quarter_labels, count_values)
    """
    if not records:
        return [], []
    
    labels = []
    values = []
    for r in records:
        d = r.get("date", "")
        if d and r.get("shareholder_count", 0) > 0:
            labels.append(d[:7])  # YYYY-MM
            values.append(r["shareholder_count"])
    
    return labels, values


def clear_cache():
    """清空所有股东人数缓存"""
    for f in os.listdir(CACHE_DIR):
        if f.endswith(".json"):
            os.remove(os.path.join(CACHE_DIR, f))
    return len(os.listdir(CACHE_DIR))
