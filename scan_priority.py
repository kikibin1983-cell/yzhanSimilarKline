"""
智能扫描优先级系统
优先扫描活跃度高的股票
"""

# 板块活跃度权重
SECTOR_PRIORITY = {
    '证券': 1.0,      # 高波动，底背离多
    '半导体': 1.0,
    '新能源': 1.0,
    '医药': 0.9,
    '创业板': 0.9,
    '科创板': 0.9,
    '上证主板': 0.7,
    '深证主板': 0.7,
    '中小板': 0.6,
    '北交所': 0.5,    # 流动性差，底背离少
}

def sort_stocks_by_priority(stocks):
    """
    按优先级排序股票
    
    排序规则：
    1. 板块活跃度权重
    2. 近期成交量（放量优先）
    3. 近期振幅（波动大优先）
    """
    def get_priority(stock):
        base_priority = SECTOR_PRIORITY.get(stock['type'], 0.5)
        return base_priority
    
    return sorted(stocks, key=get_priority, reverse=True)


# 历史底背离热点板块（基于统计）
HOT_DIVERGENCE_SECTORS = [
    '证券', '半导体', '新能源', '医药', '军工', '科技'
]

def filter_by_sector_priority(stocks, top_n=1000):
    """
    只扫描热门板块的前N只股票
    """
    hot_stocks = [
        s for s in stocks 
        if any(sector in s['type'] for sector in HOT_DIVERGENCE_SECTORS)
    ]
    return hot_stocks[:top_n]
