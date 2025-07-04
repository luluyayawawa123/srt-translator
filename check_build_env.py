#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SRT字幕翻译工具 - 环境检查脚本
用于排查打包环境问题和依赖

使用方法：
python check_build_env.py
"""

import os
import sys
import importlib
import subprocess
import platform
from pathlib import Path


def print_separator(title):
    """打印分隔符"""
    print(f"\n{'='*50}")
    print(f" {title}")
    print(f"{'='*50}")


def check_python_version():
    """检查Python版本"""
    print_separator("Python环境检查")
    
    version = sys.version_info
    print(f"Python版本: {version.major}.{version.minor}.{version.micro}")
    print(f"Python路径: {sys.executable}")
    print(f"操作系统: {platform.system()} {platform.release()}")
    print(f"架构: {platform.machine()}")
    
    # 检查Python版本兼容性
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print("⚠️  警告: 建议使用Python 3.8或更高版本")
    else:
        print("✅ Python版本兼容")


def check_required_modules():
    """检查必需模块"""
    print_separator("必需模块检查")
    
    required_modules = [
        ("tkinter", "GUI框架"),
        ("customtkinter", "现代GUI组件"),
        ("requests", "HTTP请求"),
        ("json", "JSON处理"),
        ("threading", "多线程"),
        ("queue", "队列"),
        ("concurrent.futures", "并发处理"),
        ("logging", "日志"),
        ("re", "正则表达式"),
        ("os", "操作系统接口"),
        ("sys", "系统参数"),
        ("pathlib", "路径处理"),
        ("time", "时间处理")
    ]
    
    missing_modules = []
    for module_name, description in required_modules:
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, '__version__'):
                version = getattr(module, '__version__')
                print(f"✅ {module_name} ({description}) - 版本: {version}")
            else:
                print(f"✅ {module_name} ({description}) - 已安装")
        except ImportError:
            print(f"❌ {module_name} ({description}) - 未安装")
            missing_modules.append(module_name)
    
    if missing_modules:
        print(f"\n缺少模块: {', '.join(missing_modules)}")
        print("请安装缺少的模块:")
        print(f"pip install {' '.join(missing_modules)}")
    else:
        print("\n✅ 所有必需模块已安装")


def check_build_tools():
    """检查打包工具"""
    print_separator("打包工具检查")
    
    # 检查PyInstaller
    try:
        import PyInstaller
        print(f"✅ PyInstaller 版本: {PyInstaller.__version__}")
        
        # 检查PyInstaller命令行工具
        try:
            result = subprocess.run([sys.executable, "-m", "PyInstaller", "--version"], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✅ PyInstaller命令行工具可用")
            else:
                print(f"⚠️  PyInstaller命令行工具异常: {result.stderr}")
        except Exception as e:
            print(f"⚠️  PyInstaller命令行测试失败: {e}")
    
    except ImportError:
        print("❌ PyInstaller 未安装")
        print("请安装: pip install pyinstaller")
    
    # 检查UPX（可选）
    try:
        result = subprocess.run(["upx", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ UPX压缩工具可用（可选）")
        else:
            print("ℹ️  UPX压缩工具不可用（可选，不影响打包）")
    except:
        print("ℹ️  UPX压缩工具不可用（可选，不影响打包）")


def check_source_files():
    """检查源文件"""
    print_separator("源文件检查")
    
    current_dir = Path.cwd()
    required_files = [
        "srt_translator_gui.py",
        "srt_translator.py",
        "srt_checker.py"
    ]
    
    optional_files = [

        "srt_icon.ico"
    ]
    
    missing_required = []
    for file_name in required_files:
        file_path = current_dir / file_name
        if file_path.exists():
            size = file_path.stat().st_size
            print(f"✅ {file_name} ({size} 字节)")
        else:
            print(f"❌ {file_name} - 缺失")
            missing_required.append(file_name)
    
    for file_name in optional_files:
        file_path = current_dir / file_name
        if file_path.exists():
            size = file_path.stat().st_size
            print(f"✅ {file_name} ({size} 字节) - 可选文件")
        else:
            print(f"ℹ️  {file_name} - 可选文件，不存在")
    
    if missing_required:
        print(f"\n❌ 缺少必需文件: {', '.join(missing_required)}")
        return False
    else:
        print("\n✅ 所有必需源文件存在")
        return True


def check_permissions():
    """检查文件权限"""
    print_separator("权限检查")
    
    current_dir = Path.cwd()
    
    # 检查当前目录写权限
    try:
        test_file = current_dir / "test_write_permission.tmp"
        with open(test_file, 'w') as f:
            f.write("test")
        os.remove(test_file)
        print("✅ 当前目录可写")
    except Exception as e:
        print(f"❌ 当前目录写权限测试失败: {e}")
        return False
    
    # 检查系统临时目录
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            tmp.write(b"test")
        print("✅ 系统临时目录可写")
    except Exception as e:
        print(f"❌ 系统临时目录权限测试失败: {e}")
        return False
    
    return True


def check_disk_space():
    """检查磁盘空间"""
    print_separator("磁盘空间检查")
    
    current_dir = Path.cwd()
    
    try:
        if platform.system() == "Windows":
            import shutil
            total, used, free = shutil.disk_usage(current_dir)
            
            total_gb = total / (1024**3)
            free_gb = free / (1024**3)
            
            print(f"磁盘总容量: {total_gb:.1f} GB")
            print(f"可用空间: {free_gb:.1f} GB")
            
            # 打包通常需要几百MB到1GB的空间
            if free_gb < 1:
                print("⚠️  可用磁盘空间较少，建议至少保留1GB空间用于打包")
            else:
                print("✅ 磁盘空间充足")
        else:
            print("ℹ️  非Windows系统，跳过磁盘空间检查")
    except Exception as e:
        print(f"⚠️  磁盘空间检查失败: {e}")


def test_gui_import():
    """测试GUI相关导入"""
    print_separator("GUI模块测试")
    
    try:
        import tkinter as tk
        print("✅ tkinter基础模块")
        
        # 测试tkinter GUI创建
        root = tk.Tk()
        root.withdraw()  # 隐藏窗口
        root.destroy()
        print("✅ tkinter GUI创建测试")
        
    except Exception as e:
        print(f"❌ tkinter测试失败: {e}")
        return False
    
    try:
        import customtkinter as ctk
        print("✅ customtkinter模块")
        
        # 测试customtkinter
        ctk.set_appearance_mode("System")
        print("✅ customtkinter配置测试")
        
    except Exception as e:
        print(f"❌ customtkinter测试失败: {e}")
        return False
    
    return True


def provide_recommendations():
    """提供建议"""
    print_separator("建议与故障排除")
    
    print("🔧 常见问题解决方案:")
    print("")
    print("1. 如果PyInstaller安装失败:")
    print("   pip install --upgrade pip")
    print("   pip install pyinstaller")
    print("")
    print("2. 如果customtkinter导入失败:")
    print("   pip install customtkinter")
    print("")
    print("3. 如果打包过程中出现权限错误:")
    print("   - 确保以管理员权限运行命令提示符")
    print("   - 关闭杀毒软件的实时保护")
    print("   - 将项目目录加入杀毒软件白名单")
    print("")
    print("4. 如果打包文件过大:")
    print("   - 使用虚拟环境，只安装必需的包")
    print("   - 在spec文件中排除不需要的模块")
    print("")
    print("5. 如果运行时出现模块缺失:")
    print("   - 检查hiddenimports配置")
    print("   - 手动添加缺失的模块到spec文件")
    print("")
    print("📞 获取帮助:")
    print("   - 检查build_exe.py脚本的日志输出")
    print("   - 查看PyInstaller官方文档")
    print("   - 确保所有源文件在同一目录")


def main():
    """主函数"""
    print("🔍 SRT字幕翻译工具 - 环境检查")
    print(f"检查时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 执行所有检查
    checks = [
        check_python_version,
        check_required_modules,
        check_build_tools,
        check_source_files,
        check_permissions,
        check_disk_space,
        test_gui_import
    ]
    
    all_passed = True
    for check_func in checks:
        try:
            result = check_func()
            if result is False:
                all_passed = False
        except Exception as e:
            print(f"检查过程出错: {e}")
            all_passed = False
    
    # 提供建议
    provide_recommendations()
    
    # 总结
    print_separator("检查结果总结")
    if all_passed:
        print("🎉 环境检查通过！可以开始打包。")
        print("运行命令: python build_exe.py")
    else:
        print("⚠️  发现一些问题，请根据上述建议进行修复。")
    
    print("\n按任意键退出...")
    input()


if __name__ == "__main__":
    main() 