#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股K线智能分析系统 - 安装后脚本
功能：安装Python环境、创建虚拟环境、安装依赖、创建启动器
"""

import os
import sys
import subprocess
import platform
import urllib.request
import tempfile
from pathlib import Path

# 配置
PYTHON_VERSION = "3.11.9"
PYTHON_MIN_VERSION = (3, 9)
APP_NAME = "A股K线智能分析系统"

def print_step(step_name):
    """打印安装步骤"""
    print(f"\n{'='*60}")
    print(f"  {step_name}")
    print(f"{'='*60}\n")

def check_python():
    """检查Python版本"""
    print_step("检查Python环境")
    
    version = sys.version_info
    print(f"当前Python版本: {version.major}.{version.minor}.{version.micro}")
    
    if version.major >=3 and version.minor >= PYTHON_MIN_VERSION[1]:
        print("✓ Python版本符合要求")
        return True
    else:
        print(f"✗ Python版本过低，需要 {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]} 或更高")
        return False

def download_python():
    """下载Python安装包"""
    print_step("下载Python安装包")
    
    python_url = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe"
    temp_dir = tempfile.gettempdir()
    installer_path = os.path.join(temp_dir, "python_installer.exe")
    
    print(f"下载地址: {python_url}")
    print("请稍候...")
    
    try:
        urllib.request.urlretrieve(python_url, installer_path)
        print(f"✓ 下载完成: {installer_path}")
        return installer_path
    except Exception as e:
        print(f"✗ 下载失败: {e}")
        return None

def install_python(installer_path):
    """安装Python"""
    print_step("安装Python")
    
    print("正在安装Python...（这可能需要几分钟）")
    
    # 安装参数：添加到PATH，为所有用户安装
    cmd = [
        installer_path,
        "/quiet",
        "InstallAllUsers=1",
        "PrependPath=1",
        "Include_test=0",
        "Include_pip=1",
        "Include_launcher=1"
    ]
    
    try:
        result = subprocess.run(cmd, check=True)
        print("✓ Python安装完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Python安装失败: {e}")
        return False

def create_venv(app_dir):
    """创建虚拟环境"""
    print_step("创建虚拟环境")
    
    venv_dir = os.path.join(app_dir, "venv")
    
    if os.path.exists(venv_dir):
        print("虚拟环境已存在，跳过创建")
        return True
    
    print(f"创建目录: {venv_dir}")
    
    try:
        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
        print("✓ 虚拟环境创建成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ 创建虚拟环境失败: {e}")
        return False

def install_dependencies(app_dir):
    """安装依赖包"""
    print_step("安装依赖包")
    
    venv_dir = os.path.join(app_dir, "venv")
    pip_path = os.path.join(venv_dir, "Scripts", "pip.exe")
    requirements_path = os.path.join(app_dir, "requirements.txt")
    
    if not os.path.exists(pip_path):
        print(f"✗ 找不到pip: {pip_path}")
        return False
    
    if not os.path.exists(requirements_path):
        print(f"✗ 找不到requirements.txt: {requirements_path}")
        return False
    
    print("升级pip...")
    try:
        subprocess.run([pip_path, "install", "--upgrade", "pip", "-i", 
                       "https://pypi.tuna.tsinghua.edu.cn/simple"],
                      check=True, capture_output=True)
    except:
        pass  # 忽略pip升级失败
    
    print("安装依赖包（这可能需要几分钟）...")
    dependencies = [
        ("streamlit==1.54.0", "Streamlit框架"),
        ("pandas==2.2.3", "Pandas数据处理"),
        ("numpy==1.26.4", "NumPy数值计算"),
        ("scipy==1.13.1", "Scipy科学计算"),
        ("plotly==5.24.1", "Plotly图表"),
        ("scikit-learn==1.5.2", "机器学习"),
        ("jieba==0.42.1", "中文分词"),
        ("akshare==1.13.42", "金融数据"),
        ("requests==2.32.3", "网络请求"),
        ("python-dotenv==1.0.1", "环境变量"),
        ("Pillow==10.4.0", "图像处理"),
        ("numba==0.60.0", "JIT加速"),
        ("altair==5.4.1", "Altair可视化"),
        ("pyarrow==17.0.0", "Arrow数据"),
        ("toml==0.10.2", "TOML配置"),
        ("watchdog==4.0.1", "文件监控"),
        ("pygments==2.18.0", "代码高亮"),
        ("click==8.1.7", "命令行"),
        ("rich==13.9.4", "富文本"),
        ("packaging==24.1", "包管理"),
        ("adata", "A股数据"),# 额外依赖
        ("pefile", "PE文件"),# 打包需要
    ]
    
    failed = []
    for i, (package, desc) in enumerate(dependencies, 1):
        print(f"[{i}/{len(dependencies)}] 安装 {desc} ({package})...")
        try:
            subprocess.run([pip_path, "install", package, "-i",
                          "https://pypi.tuna.tsinghua.edu.cn/simple","--quiet"],
                         check=True, capture_output=True)
            print(f"  ✓ 成功")
        except subprocess.CalledProcessError:
            print(f"  ✗ 失败（将在稍后重试）")
            failed.append(package)
    
    # 重试失败的包
    if failed:
        print("\n重试失败的包...")
        for package in failed:
            try:
                subprocess.run([pip_path, "install", package],
                             check=True, capture_output=True)
                print(f"  ✓ {package} 安装成功")
            except:
                print(f"  ✗ {package} 安装失败（可选依赖）")
    
    print("\n✓ 依赖安装完成")
    return True

def create_launcher(app_dir):
    """创建启动器"""
    print_step("创建启动器")
    
    venv_dir = os.path.join(app_dir, "venv")
    python_path = os.path.join(venv_dir, "Scripts", "python.exe")
    streamlit_path = os.path.join(venv_dir, "Scripts", "streamlit.exe")
    main_script = os.path.join(app_dir, "streamlit_app.py")
    
    # 创建启动器脚本
    launcher_script = os.path.join(app_dir, "launcher.py")
    launcher_content = f'''#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A股K线智能分析系统启动器"""
import os
import sys
import subprocess
import webbrowser
import time

def main():
    app_dir = r"{app_dir}"
    venv_python = r"{python_path}"
    main_script = r"{main_script}"os.chdir(app_dir)
    
    print("=" * 60)
    print("  A股K线智能分析系统")
    print("=" * 60)
    print()
    print("正在启动服务...")
    print("首次启动可能需要30秒初始化缓存")
    print("启动后请在浏览器中访问: http://localhost:8501")
    print()
    print("按Ctrl+C停止服务")
    print()
    
    # 启动Streamlit
    cmd = [venv_python, "-m", "streamlit", "run", main_script,
           "--server.port=8501", "--server.headless=true"]
    
    try:
        # 等待一段时间后打开浏览器
        import threading
        import time
        
        def open_browser():
            time.sleep(3)
            webbrowser.open("http://localhost:8501")        
        browser_thread = threading.Thread(target=open_browser)
        browser_thread.daemon = True
        browser_thread.start()
        
        # 运行Streamlit
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\\n服务已停止")
    except Exception as e:
        print(f"启动失败: {{e}}")
        input("按回车键退出...")

if __name__ == "__main__":
    main()
'''
    
    with open(launcher_script, 'w', encoding='utf-8') as f:
        f.write(launcher_content)
    
    # 创建EXE启动器（使用Python的setup.py方式）
    # 先创建一个简单的启动批处理文件作为备选
    bat_path = os.path.join(app_dir, "启动程序.bat")
    bat_content = f'''@echo off
chcp 65001 >nul
cd /d "{app_dir}"
"{python_path}" launcher.py
pause
'''
    with open(bat_path, 'w', encoding='utf-8') as f:
        f.write(bat_content)
    
    # 使用PyInstaller创建真正的EXE
    try:
        import PyInstaller
        print("创建EXE启动器...")
        spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
block_cipher = None
a = Analysis(['{os.path.join(app_dir, "launcher.py")}'],
             pathex=['{app_dir}'],
             binaries=[],
             datas=[],
             hiddenimports=[],
             hookspath=[],
             hooksconfig={{}},
             runtime_hooks=[],
             excludes=['tkinter', 'matplotlib', 'notebook', 'jupyter'],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)pyz = PYZ(a.pure, a.datas, cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.datas,
          [],
          name='启动程序',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=True,
          disable_windowed_traceback=False,
          argv_emulation=False,
          target_arch=None,
          codesign_identity=None,
          entitlements_file=None)
'''
        spec_path = os.path.join(app_dir, "launcher.spec")
        with open(spec_path, 'w', encoding='utf-8') as f:
            f.write(spec_content)
        
        # 编译EXE
        subprocess.run([python_path, "-m", "PyInstaller", "--clean", spec_path],
                      check=True, capture_output=True)
        
        # 复制EXE到app目录
        exe_src = os.path.join(app_dir, "dist", "启动程序.exe")
        exe_dst = os.path.join(app_dir, "启动程序.exe")
        if os.path.exists(exe_src):
            import shutil
            shutil.copy(exe_src, exe_dst)
            print("✓ EXE启动器创建成功")
    except Exception as e:
        print(f"EXE创建失败: {e}")
        print("将使用批处理启动器")
    
    print("✓ 启动器创建完成")
    return True

def create_uninstall_script(app_dir):
    """创建卸载脚本"""
    print_step("创建卸载脚本")
    
    uninstall_script = os.path.join(app_dir, "uninstall.bat")
    content = f'''@echo off
chcp 65001 >nul
echo========================================
echo   A股K线智能分析系统 - 卸载程序
echo ========================================
echo.
echo 正在卸载...
echo.

REM 删除虚拟环境
if exist "{app_dir}\\venv" (
    echo 删除虚拟环境...
    rmdir /s /q "{app_dir}\\venv"
)

REM 删除缓存
if exist "{app_dir}\\__pycache__" (
    echo 删除缓存文件...
    rmdir /s /q "{app_dir}\\__pycache__"
)

if exist "{app_dir}\\logs" (
    rmdir /s /q "{app_dir}\\logs"
)

echo.
echo 卸载完成！
echo.
echo 注意：用户数据和缓存数据保留在以下目录：
echo {app_dir}
echo 如需完全删除，请手动删除该目录
echo.
pause
'''
    with open(uninstall_script, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✓ 卸载脚本创建完成")
    return True

def create_shortcuts(app_dir):
    """创建桌面快捷方式"""
    print_step("创建快捷方式")
    
    try:
        from win32com.client import Dispatch
        import pythoncom
        
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        shortcut_path = os.path.join(desktop, f"{APP_NAME}.lnk")
        
        shell = Dispatch('WScript.Shell')
        shortcut = shell.CreateShortcut(shortcut_path)
        shortcut.TargetPath = os.path.join(app_dir, "启动程序.exe")
        shortcut.WorkingDirectory = app_dir
        shortcut.Description = APP_NAME
        shortcut.Save()
        
        print("✓ 桌面快捷方式创建成功")
    except Exception as e:
        print(f"快捷方式创建失败: {e}（请手动创建）")
    
    return True

def main():
    """主安装流程"""
    print("\n" + "="*60)
    print(f"   {APP_NAME}")
    print("   安装后配置程序")
    print("="*60 + "\n")
    
    # 获取应用目录
    if len(sys.argv) > 1:
        app_dir = sys.argv[1]
    else:
        # 尝试从环境变量或当前目录获取
        app_dir = os.path.dirname(os.path.abspath(__file__))
    
    print(f"安装目录: {app_dir}\n")
    
    # 步骤1: 检查Python
    if not check_python():
        print("\n需要安装Python，是否继续？(Y/N)")
        choice = input().strip().upper()
        if choice == 'Y':
            installer_path = download_python()
            if installer_path and install_python(installer_path):
                print("\nPython安装完成，请重新运行此安装程序")
            else:
                print("\nPython安装失败，请手动安装Python后重试")
            input("按回车键退出...")
            return False
        else:
            print("安装已取消")
            return False
    
    # 步骤2: 创建虚拟环境
    if not create_venv(app_dir):
        print("\n虚拟环境创建失败")
        input("按回车键退出...")
        return False
    
    # 步骤3: 安装依赖
    if not install_dependencies(app_dir):
        print("\n依赖安装可能存在部分问题，但可以继续")
    
    # 步骤4: 创建启动器
    if not create_launcher(app_dir):
        print("\n启动器创建失败")
        input("按回车键退出...")
        return False
    
    # 步骤5: 创建卸载脚本
    create_uninstall_script(app_dir)
    
    # 步骤6: 创建快捷方式
    create_shortcuts(app_dir)
    
    print("\n" + "="*60)
    print("   安装完成！")
    print("="*60)
    print("\n使用方法：")
    print(f"  1. 双击桌面上的 \"{APP_NAME}\" 快捷方式")
    print(f"  或双击 \"{app_dir}\\启动程序.exe\"")
    print("\n首次启动:")
    print("  - 需要等待约30秒初始化缓存")
    print("  - 程序会自动在浏览器中打开")
    print("  - 默认访问地址: http://localhost:8501")
    print("\n如需卸载:")
    print(f"  运行 \"{app_dir}\\uninstall.bat\"")
    print("\n")
    
    input("按回车键退出安装程序...")
    return True

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n安装过程出错: {e}")
        input("按回车键退出...")
        sys.exit(1)