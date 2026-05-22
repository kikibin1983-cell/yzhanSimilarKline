"""
缓存迁移工具
将旧的缓存数据迁移到新的统一目录结构

使用方法:
    python migrate_cache.py

迁移内容:
1. ./technical_cache/* -> ./cache/technical/*
2. ./weekly_cache/* -> ./cache/kline/weekly/*
3. ./monthly_cache/* -> ./cache/kline/monthly/*
"""

import os
import shutil
from pathlib import Path


def migrate_cache():
    """执行缓存迁移"""
    print("=" * 60)
    print("🚀 开始缓存迁移")
    print("=" * 60)
    
    # 定义迁移映射
    migrations = [
        {
            'source': './technical_cache',
            'target': './cache/technical',
            'name': '技术指标缓存'
        },
        {
            'source': './weekly_cache',
            'target': './cache/kline/weekly',
            'name': '周K线缓存'
        },
        {
            'source': './monthly_cache',
            'target': './cache/kline/monthly',
            'name': '月K线缓存'
        }
    ]
    
    total_migrated = 0
    
    for migration in migrations:
        source = Path(migration['source'])
        target = Path(migration['target'])
        name = migration['name']
        
        print(f"\n📁 迁移 {name}")
        print(f"   源: {source}")
        print(f"   目标: {target}")
        
        if not source.exists():
            print(f"   ⚠️ 源目录不存在，跳过")
            continue
        
        # 创建目标目录
        target.mkdir(parents=True, exist_ok=True)
        
        # 统计文件数量
        files_to_migrate = list(source.glob('**/*'))
        files_to_migrate = [f for f in files_to_migrate if f.is_file()]
        
        if not files_to_migrate:
            print(f"   ℹ️ 没有文件需要迁移")
            continue
        
        print(f"   发现 {len(files_to_migrate)} 个文件")
        
        # 迁移文件
        migrated_count = 0
        for file in files_to_migrate:
            # 计算相对路径
            rel_path = file.relative_to(source)
            target_file = target / rel_path
            
            # 创建子目录
            target_file.parent.mkdir(parents=True, exist_ok=True)
            
            try:
                # 复制文件
                shutil.copy2(file, target_file)
                migrated_count += 1
            except Exception as e:
                print(f"   ❌ 迁移失败 {file}: {e}")
        
        print(f"   ✅ 成功迁移 {migrated_count} 个文件")
        total_migrated += migrated_count
    
    print("\n" + "=" * 60)
    print(f"🎉 缓存迁移完成！共迁移 {total_migrated} 个文件")
    print("=" * 60)
    print("\n📋 新目录结构:")
    print("   ./cache/")
    print("   ├── technical/     # 技术指标缓存")
    print("   │   ├── daily/")
    print("   │   ├── weekly/")
    print("   │   └── monthly/")
    print("   └── kline/         # K线转换缓存")
    print("       ├── weekly/")
    print("       └── monthly/")
    print("\n💡 提示: 旧缓存目录可以手动删除:")
    print("   - ./technical_cache")
    print("   - ./weekly_cache")
    print("   - ./monthly_cache")


if __name__ == "__main__":
    migrate_cache()
