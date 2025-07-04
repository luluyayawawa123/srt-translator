#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SRT字幕翻译工具 - 打包脚本
用于将程序打包为Windows绿色版软件

使用方法：
python build_exe.py                 # 完整打包
python build_exe.py --check-only    # 仅检查文件排除设置

依赖：
pip install pyinstaller

注意：
- 打包的程序不包含配置和日志文件，这些文件会在运行时自动生成  
- 自动排除所有包含API密钥等敏感信息的配置文件
- 打包后的程序是独立的，不需要Python环境即可运行
"""

import os
import sys
import shutil
import subprocess
import json
import fnmatch
from pathlib import Path


class SRTTranslatorBuilder:
    """SRT翻译工具打包器"""
    
    def __init__(self):
        self.script_dir = Path(__file__).parent.absolute()
        self.dist_dir = self.script_dir / "dist"
        self.build_dir = self.script_dir / "build"
        self.output_dir = self.script_dir / "SRT翻译工具"
        
        # 需要包含的文件列表
        self.include_files = [
            "srt_translator_gui.py",
            "srt_translator.py", 
            "srt_checker.py",
    
        ]
        
        # 不需要包含的文件/目录（会在运行时自动生成）
        self.exclude_patterns = [
            # 日志文件
            "*.log",
            
            # 配置文件（包含敏感信息如API密钥）
            "*config*.json",
            "prompts_config.json",
            "srt_translator_gui_config.json",
            
            # 进度和临时文件
            "*_progress*.json",
            "*_batch*.srt",
            
            # Python缓存和构建文件
            "__pycache__",
            "*.pyc",
            "*.pyo",
            "*.spec",
            "build/",
            "dist/",
            
            # 版本控制
            ".git*",
            ".gitignore",
            
            # 虚拟环境
            ".venv/",
            "venv/",
            "env/",
            
            # 开发和测试文件
            "test_*",
            "*.md",
            "requirements.txt",
            
            # IDE和编辑器文件
            ".vscode/",
            ".idea/",
            "*.swp",
            "*.swo",
            "*~",
            
            # 操作系统文件
            ".DS_Store",
            "Thumbs.db",
            
            # 项目特定目录
            "SRT翻译工具/",
            ".历史文件备份（请忽略）/",
            
            # 用户数据文件（运行时生成）
            "*.sqlite",
            "*.db"
        ]
    
    def check_dependencies(self):
        """检查打包依赖"""
        print("🔍 检查打包依赖...")
        
        try:
            import PyInstaller
            print(f"✅ PyInstaller 版本: {PyInstaller.__version__}")
        except ImportError:
            print("❌ 缺少 PyInstaller")
            print("请运行: pip install pyinstaller")
            return False
            
        # 检查主要模块
        required_modules = ["customtkinter", "tkinter"]
        missing_modules = []
        
        for module in required_modules:
            try:
                __import__(module)
                print(f"✅ {module} 模块可用")
            except ImportError:
                missing_modules.append(module)
                print(f"❌ 缺少 {module} 模块")
        
        if missing_modules:
            print(f"请安装缺少的模块: pip install {' '.join(missing_modules)}")
            return False
            
        return True
    
    def check_source_files(self):
        """检查源文件"""
        print("\n📋 检查源文件...")
        
        missing_files = []
        for file_name in self.include_files:
            file_path = self.script_dir / file_name
            if file_path.exists():
                print(f"✅ {file_name}")
            else:
                missing_files.append(file_name)
                print(f"❌ {file_name}")
        
        if missing_files:
            print(f"\n❌ 缺少必要文件: {missing_files}")
            return False
            
        return True
    
    def clean_previous_build(self):
        """清理之前的打包结果"""
        print("\n🧹 清理之前的打包结果...")
        
        dirs_to_clean = [self.build_dir, self.dist_dir, self.output_dir]
        
        for dir_path in dirs_to_clean:
            if dir_path.exists():
                try:
                    shutil.rmtree(dir_path)
                    print(f"✅ 已清理: {dir_path.name}")
                except Exception as e:
                    print(f"⚠️  清理失败 {dir_path.name}: {e}")
    
    def create_pyinstaller_spec(self):
        """创建PyInstaller规格文件"""
        print("\n📝 创建PyInstaller配置...")
        
        spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None

a = Analysis(
    ['srt_translator_gui.py'],
    pathex=[r'{self.script_dir}'],
    binaries=[],
    datas=[
        ('srt_translator.py', '.'),
        ('srt_checker.py', '.'),
    ],
    hiddenimports=[
        'customtkinter',
        'tkinter',
        'tkinter.ttk',
        'tkinter.font',
        'tkinter.messagebox',
        'tkinter.filedialog',
        'requests',
        'json',
        'logging',
        'threading',
        'queue',
        'concurrent.futures',
        're',
        'time',
        'os',
        'sys'
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 使用onedir模式 - 真正的绿色软件，不会产生系统临时文件
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # 关键：这让PyInstaller使用onedir模式
    name='SRT字幕翻译工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 无控制台窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # 如果有图标文件可以在这里指定
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SRT字幕翻译工具',
)
'''
        
        spec_file = self.script_dir / "srt_translator.spec"
        with open(spec_file, 'w', encoding='utf-8') as f:
            f.write(spec_content)
            
        print(f"✅ 已创建规格文件: {spec_file.name}")
        return spec_file
    
    def build_executable(self, spec_file):
        """执行打包"""
        print("\n🔨 开始打包...")
        print("这可能需要几分钟时间，请耐心等待...")
        
        try:
            # 构建PyInstaller命令
            cmd = [
                sys.executable, "-m", "PyInstaller",
                "--clean",  # 清理缓存
                "--noconfirm",  # 不要确认覆盖
                str(spec_file)
            ]
            
            print(f"执行命令: {' '.join(cmd)}")
            
            # 执行打包命令 - 改进编码处理
            result = subprocess.run(
                cmd, 
                cwd=self.script_dir,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'  # 遇到编码错误时替换为?，避免崩溃
            )
            
            if result.returncode == 0:
                print("✅ 打包成功！")
                
                # 显示警告信息（如果有）
                if result.stderr and result.stderr.strip():
                    # 过滤掉常见的无害警告
                    stderr_lines = result.stderr.strip().split('\n')
                    important_warnings = []
                    for line in stderr_lines:
                        # 跳过编码相关的错误和一些常见的无害警告
                        if ('UnicodeDecodeError' not in line and 
                            'deprecation' not in line.lower() and
                            'warning' not in line.lower() and
                            line.strip()):
                            important_warnings.append(line)
                    
                    if important_warnings:
                        print("⚠️  打包过程中的信息:")
                        for warning in important_warnings[:5]:  # 只显示前5条重要信息
                            print(f"   {warning}")
                        if len(important_warnings) > 5:
                            print(f"   ... 还有 {len(important_warnings) - 5} 条信息")
                
                return True
            else:
                print("❌ 打包失败！")
                print("错误输出:")
                # 清理错误输出中的编码问题
                error_output = result.stderr or "未知错误"
                error_lines = error_output.split('\n')
                for line in error_lines:
                    if line.strip() and 'UnicodeDecodeError' not in line:
                        print(f"   {line}")
                return False
                
        except Exception as e:
            print(f"❌ 打包过程出错: {e}")
            return False
    
    def organize_output(self):
        """整理输出文件"""
        print("\n📦 整理输出文件...")
        
        # onedir模式会生成一个文件夹，查找生成的可执行文件
        exe_dir = self.dist_dir / "SRT字幕翻译工具"
        exe_file = exe_dir / "SRT字幕翻译工具.exe"
        
        if not exe_file.exists():
            print(f"❌ 找不到生成的可执行文件: {exe_file}")
            return False
        
        # 删除旧的输出目录（如果存在）
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        
        # 直接重命名dist目录中的文件夹为我们想要的输出目录
        shutil.move(str(exe_dir), str(self.output_dir))
        print(f"✅ 已整理可执行文件目录")
        
        # 创建使用说明
        readme_content = '''SRT字幕翻译工具 - 使用说明
=======================

这是一个用于翻译SRT字幕文件的工具，支持多种AI翻译API。
** 真正的绿色软件：不会在系统临时目录产生任何文件！**

使用方法：
1. 双击"SRT字幕翻译工具.exe"启动程序
2. 在设置面板中填入您的API密钥和相关配置
3. 选择输入的SRT文件和输出位置
4. 点击"开始翻译"按钮

主要功能：
- 支持自定义AI翻译API（如DeepSeek、GPT、Claude等）
- 批量翻译，支持断点续传
- 多线程并行处理，提高翻译速度
- 字幕文件校验功能
- 智能错误处理和重试机制

绿色软件特征：
- 不写入系统注册表
- 不在系统临时目录产生文件
- 所有文件都在软件目录内
- 删除文件夹即完全卸载

配置文件：
- 程序首次运行时会自动创建配置文件（在软件目录内）
- 配置会自动保存，下次启动时自动加载
- 提示词设置支持个性化定制

系统要求：
- Windows 7/8/10/11 (32位/64位)
- 网络连接（用于API调用）

技术支持：
如有问题，请检查日志文件了解详细错误信息。

版本：v1.0 - 真绿色版
'''
        
        readme_file = self.output_dir / "使用说明.txt"
        with open(readme_file, 'w', encoding='utf-8') as f:
            f.write(readme_content)
        print(f"✅ 已创建使用说明: {readme_file.name}")
        

        
        return True
    
    def cleanup_build_files(self):
        """清理构建文件"""
        print("\n🧹 清理构建文件...")
        
        files_to_clean = [
            self.script_dir / "srt_translator.spec",
            self.build_dir,
            self.dist_dir
        ]
        
        for item in files_to_clean:
            if item.exists():
                try:
                    if item.is_file():
                        item.unlink()
                    else:
                        shutil.rmtree(item)
                    print(f"✅ 已清理: {item.name}")
                except Exception as e:
                    print(f"⚠️  清理失败 {item.name}: {e}")
    
    def get_output_size(self):
        """获取输出文件大小"""
        if not self.output_dir.exists():
            return "未知"
        
        total_size = 0
        for file_path in self.output_dir.rglob('*'):
            if file_path.is_file():
                total_size += file_path.stat().st_size
        
        # 转换为可读格式
        if total_size < 1024:
            return f"{total_size} B"
        elif total_size < 1024 * 1024:
            return f"{total_size / 1024:.1f} KB"
        else:
            return f"{total_size / (1024 * 1024):.1f} MB"
    
    def check_exclusions(self):
        """检查并显示会被排除的文件"""
        print("\n🔍 检查文件排除设置...")
        
        all_files = []
        excluded_files = []
        
        # 遍历当前目录下的所有文件
        for item in self.script_dir.rglob("*"):
            if item.is_file():
                relative_path = item.relative_to(self.script_dir)
                all_files.append(str(relative_path))
                
                # 检查是否匹配排除模式
                should_exclude = False
                for pattern in self.exclude_patterns:
                    if fnmatch.fnmatch(str(relative_path), pattern) or fnmatch.fnmatch(item.name, pattern):
                        should_exclude = True
                        excluded_files.append((str(relative_path), pattern))
                        break
        
        print(f"📂 总文件数: {len(all_files)}")
        print(f"🚫 排除文件数: {len(excluded_files)}")
        
        if excluded_files:
            print("\n📋 被排除的文件:")
            config_files = []
            other_files = []
            
            for file_path, pattern in excluded_files:
                if 'config' in file_path.lower() or 'progress' in file_path.lower() or file_path.endswith('.log'):
                    config_files.append(f"   🔒 {file_path} (匹配: {pattern})")
                else:
                    other_files.append(f"   📄 {file_path} (匹配: {pattern})")
            
            if config_files:
                print("   🔐 敏感配置文件 (包含API密钥等):")
                for item in config_files[:10]:  # 最多显示10个
                    print(item)
                if len(config_files) > 10:
                    print(f"      ... 还有 {len(config_files) - 10} 个配置文件")
            
            if other_files:
                print("   📁 其他开发文件:")
                for item in other_files[:5]:  # 最多显示5个
                    print(item)
                if len(other_files) > 5:
                    print(f"      ... 还有 {len(other_files) - 5} 个其他文件")
        
        print("\n✅ 排除设置检查完成！")
        return True
    
    def build(self):
        """执行完整的打包流程"""
        print("🚀 SRT字幕翻译工具 - 开始打包")
        print("=" * 50)
        
        # 检查依赖
        if not self.check_dependencies():
            return False
        
        # 检查源文件
        if not self.check_source_files():
            return False
        
        # 检查文件排除设置
        if not self.check_exclusions():
            return False
        
        # 清理之前的构建
        self.clean_previous_build()
        
        # 创建规格文件
        spec_file = self.create_pyinstaller_spec()
        
        # 执行打包
        if not self.build_executable(spec_file):
            return False
        
        # 整理输出
        if not self.organize_output():
            return False
        
        # 清理构建文件
        self.cleanup_build_files()
        
        # 显示结果
        output_size = self.get_output_size()
        print("\n" + "=" * 50)
        print("🎉 打包完成！")
        print(f"📁 输出目录: {self.output_dir}")
        print(f"📊 总大小: {output_size}")
        print(f"🎯 主程序: SRT字幕翻译工具.exe")
        print(f"🟢 绿色软件模式: 不产生系统临时文件")
        print("\n✨ 这是真正的绿色软件：")
        print("   • 运行时不会在系统临时目录产生任何文件")
        print("   • 所有文件都在软件目录内")
        print("   • 删除整个文件夹即可完全卸载")
        print("\n🚀 现在您可以将整个文件夹复制到任何Windows电脑上使用！")
        
        return True


def main():
    """主函数"""
    builder = SRTTranslatorBuilder()
    
    # 检查命令行参数
    if len(sys.argv) > 1 and sys.argv[1] == "--check-only":
        print("🔍 仅检查文件排除设置...")
        print("=" * 50)
        builder.check_exclusions()
        print("\n按任意键退出...")
        input()
        return
    
    try:
        success = builder.build()
        if success:
            print("\n按任意键退出...")
            input()
        else:
            print("\n❌ 打包失败，请检查上述错误信息")
            print("按任意键退出...")
            input()
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户取消打包")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 打包过程出现未预期的错误: {e}")
        print("按任意键退出...")
        input()
        sys.exit(1)


if __name__ == "__main__":
    main() 