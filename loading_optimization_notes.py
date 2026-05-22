"""
数据加载优化对比

问题: 磁盘IO性能极差（约1秒/只）
原因: Windows文件系统缓存机制 + 多线程IO竞争

优化方案对比:

1. 多线程并行(16线程) - 当前已禁用
   - 适用于: SSD固态硬盘
   - 速度: 50-100ms/只
   - 问题: 机械硬盘上磁头频繁寻道，速度反而降到1秒/只

2. 单线程顺序读取(1线程) - 当前方案
   - 适用于: 机械硬盘
   - 速度: 100-300ms/只（理论值）
   - 实际: 可能仍然受Windows缓存影响

3. 预加载服务 + 等待 - 推荐方案
   - 流程:
     a. 启动应用后等待5-10分钟让预加载完成
     b. 预加载完成后，搜索时直接从内存读取
     c. 速度: <1ms/只（内存速度）
   - 优点: 一劳永逸
   - 缺点: 首次启动慢

4. 磁盘缓存 - 已启用
   - 首次加载后保存到 ./stock_cache/
   - 下次启动从缓存加载（pickle格式）
   - 速度提升: 约2-5倍

建议操作:
1. 如果是机械硬盘，建议:
   - 启动应用后先去喝杯水(5-10分钟)
   - 让预加载服务在后台完成
   - 预加载完成后会显示"✅ 已预加载 X 只股票"
   - 此时再进行搜索，速度会快100倍以上

2. 如果急需使用:
   - 减少搜索范围（如只选100-500只股票）
   - 或在代码中修改 max_stocks = 500

3. 终极解决方案:
   - 将通达信数据目录移到SSD上
   - 速度立即提升10-50倍
"""

# 快速修复代码 - 如需使用，替换到streamlit_app.py中

def load_stocks_sequential(codes_to_process, security_type, start_date, end_date):
    """
    纯串行加载股票数据（避免所有线程开销）
    适用于: 机械硬盘 + Windows系统
    """
    import time
    from tdx_reader import read_tdx_data
    
    stock_data_cache = {}
    total = len(codes_to_process)
    start_time = time.time()
    
    print(f"[串行加载] 开始加载 {total} 只股票（纯串行模式）...")
    
    for i, code in enumerate(codes_to_process):
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
            
            # 每50只显示进度
            if (i + 1) % 50 == 0:
                elapsed = time.time() - start_time
                avg_time = elapsed / (i + 1)
                remaining = (total - i - 1) * avg_time
                print(f"[串行加载] {i+1}/{total} | 已用 {elapsed:.0f}秒 | 预计还需 {remaining/60:.1f}分钟")
                
        except Exception as e:
            print(f"[串行加载] 股票 {code} 失败: {str(e)[:50]}")
    
    elapsed = time.time() - start_time
    print(f"[串行加载] 完成！加载 {len(stock_data_cache)}/{total} 只，耗时 {elapsed:.1f}秒")
    return stock_data_cache
