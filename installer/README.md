# A股K线智能分析系统 - 安装包构建指南

本文档介绍如何为客户创建一键安装的EXE安装包。

## 方案概述

提供两种安装包方案供选择：

### 方案1:EXE安装版（推荐）
-专业 Windows安装程序
- 图形界面安装过程
- 自动检测/安装Python
- 自动安装依赖
- 创建桌面快捷方式
- **需要**: 安装 Inno Setup

### 方案2: 便携版ZIP
- 简单的ZIP压缩包
- 解压后运行安装脚本
- 自动检测/安装Python
- 自动安装依赖
- **无需**: 额外工具

---

## 方案1: EXE安装版构建步骤

### 步骤1: 安装Inno Setup

下载地址: https://jrsoftware.org/isdl.php

安装时选择添加到PATH环境变量。

### 步骤2: 运行构建脚本

双击运行 `installer\build_installer.bat`

脚本会自动：
1. 检查Inno Setup环境
2. 检查Python环境
3. 收集缓存数据
4. 编译生成EXE安装包

### 步骤3: 分发安装包

生成的文件位于: `installer\Output\A股K线智能分析系统_安装版.exe`

将此EXE文件发送给客户即可。

---

## 方案2: 便携版ZIP构建步骤

### 步骤1: 运行构建脚本

双击运行 `installer\build_portable.bat`

脚本会自动：
1. 收集程序文件
2. 复制缓存数据
3. 创建安装脚本
4. 打包成ZIP文件

### 步骤2: 分发安装包

生成的文件: `A股K线智能分析系统_便携版.zip`

### 客户使用步骤：

1. 解压ZIP到任意目录
2. 双击运行`安装程序.bat`
3. 等待Python和依赖安装
4. 双击`启动.bat`运行程序

---

## 包含内容

安装包会包含以下内容：

### 核心文件
- `streamlit_app.py` - 主程序
- `tdx_reader.py` - 通达信数据读取
- `requirements.txt` - 依赖列表
- `.env.template` - 配置模板

### 缓存数据（如果您有）
- `securities_cache/` - 证券信息缓存
- `technical_cache/` - 技术分析缓存
- `search_cache/` - 搜索结果缓存
- `cache/` - 通用缓存
- `weekly_cache/` - 周线数据缓存
- `monthly_cache/` - 月线数据缓存

### 文档
- `用户手册.md`
- `readme.md`

---

## 常见问题

### Q: 缓存数据有什么用？
A: 包含缓存数据可以让客户首次使用时无需等待数据加载，直接使用预缓存的数据。

### Q: 为什么需要安装Python？
A: 这是一个Python Streamlit应用，需要Python运行环境。安装程序会自动处理Python安装。

### Q: 客户需要什么技术知识？
A: 完全不需要！客户只需：
- EXE版：双击安装包，点击"下一步"
- 便携版：解压后双击安装脚本

### Q: 安装包大小？
A: 取决于缓存数据大小，通常50-200MB。

### Q: 如何更新程序？
A: 重新构建安装包发送给客户，客户重新安装即可。

---

## 文件说明

```
installer/
├── setup.iss              # Inno Setup安装脚本
├── installer_script.py    # 安装后配置脚本
├── uninstall.bat          # 卸载脚本
├── build_installer.bat    # EXE版构建脚本
├── build_portable.bat     # 便携版构建脚本
├── 安装前说明.txt          # 安装前显示
├── 安装后说明.txt          # 安装后显示
└── Output/                # 输出目录（自动创建）
    └── A股K线智能分析系统_安装版.exe
```

---

## 技术支持

项目地址: https://github.com/mantoufan/yzhanSimilarKline