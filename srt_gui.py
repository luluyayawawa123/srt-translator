#!/usr/bin/env python
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import subprocess
import sys
import os
import logging
import json
import queue
import multiprocessing
import time
from typing import Optional, Dict, Any
import tempfile
import uuid
import glob
import math
import re

def _is_writable_stream(stream) -> bool:
    try:
        if stream is None:
            return False
        if getattr(stream, "closed", False):
            return False
        stream.write("")
        try:
            stream.flush()
        except Exception:
            pass
        return True
    except Exception:
        return False


try:
    if not _is_writable_stream(sys.stdout):
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
    if not _is_writable_stream(sys.stderr):
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")
except Exception:
    pass

from independent_bilingual_translator import convert_to_bilingual
from srt_task_worker import (
    CheckerJobConfig,
    CorrectorJobConfig,
    PolisherJobConfig,
    TranslationJobConfig,
    run_checker_job,
    run_corrector_job,
    run_polisher_job,
    run_translation_job,
)

def get_app_dir() -> str:
    try:
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_name: str) -> str:
    base = get_app_dir()
    candidate = os.path.join(base, relative_name)
    if os.path.exists(candidate):
        return candidate

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate2 = os.path.join(meipass, relative_name)
        if os.path.exists(candidate2):
            return candidate2

    return candidate


GUI_DEBUG = os.environ.get("SRT_GUI_DEBUG", "0") == "1"


def safe_file_log(message: str) -> None:
    try:
        log_path = os.path.join(get_app_dir(), "srt_gui_debug.log")
        with open(log_path, "a", encoding="utf-8", errors="ignore") as f:
            f.write(str(message) + "\n")
    except Exception:
        pass


def debug_file_log(message: str) -> None:
    if not GUI_DEBUG:
        return
    safe_file_log(message)

class CorrectionReviewEntry:
    """纠错审核条目数据结构"""
    def __init__(self, number: int, start_time: str, end_time: str, 
                 original_content: str, corrected_content: str):
        self.number = number
        self.start_time = start_time
        self.end_time = end_time
        self.original_content = original_content
        self.corrected_content = corrected_content
        self.current_status = "corrected"  # "corrected" | "original" | "edited"
        self.edited_content = corrected_content  # 用于手动编辑

class SubtitleDiffEngine:
    """字幕差异检测引擎"""
    
    @staticmethod
    def parse_srt_file(file_path: str):
        """解析SRT文件，返回字幕条目字典"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, 'r', encoding='gbk') as f:
                    content = f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
        
        # SRT格式正则表达式
        SRT_PATTERN = re.compile(
            r'(\d+)\s*\n'               # 字幕序号
            r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n'  # 时间码
            r'((?:.+(?:\n|$))+?)'       # 字幕内容
            r'(?:\n|$)',                # 空行或文件结尾
            re.MULTILINE
        )
        
        entries = {}
        for match in SRT_PATTERN.finditer(content):
            number = int(match.group(1))
            start_time = match.group(2)
            end_time = match.group(3)
            subtitle_content = match.group(4).strip()
            
            entries[number] = {
                'number': number,
                'start_time': start_time,
                'end_time': end_time,
                'content': subtitle_content
            }
        
        return entries
    
    @staticmethod
    def find_changed_entries(original_dict: dict, corrected_dict: dict):
        """找出有变化的字幕条目"""
        changed_entries = []
        
        for num in sorted(corrected_dict.keys()):
            if num in original_dict:
                orig = original_dict[num]
                corr = corrected_dict[num]
                
                # 比较内容是否有变化
                if orig['content'].strip() != corr['content'].strip():
                    changed_entries.append(CorrectionReviewEntry(
                        number=num,
                        start_time=corr['start_time'],
                        end_time=corr['end_time'],
                        original_content=orig['content'],
                        corrected_content=corr['content']
                    ))
        
        return changed_entries
    
    @staticmethod
    def compare_srt_files(original_path: str, corrected_path: str):
        """比较两个SRT文件，返回变化的条目列表"""
        try:
            original_dict = SubtitleDiffEngine.parse_srt_file(original_path)
            corrected_dict = SubtitleDiffEngine.parse_srt_file(corrected_path)
            
            changed_entries = SubtitleDiffEngine.find_changed_entries(original_dict, corrected_dict)
            
            return changed_entries, len(original_dict), len(corrected_dict)
            
        except Exception as e:
            raise Exception(f"比较文件时出错: {str(e)}")

class ColoredLogWidget:
    """增强版日志显示组件，支持彩色输出和表情符号"""
    
    def __init__(self, parent, height=28, width=None, dark_mode=False):
        self.dark_mode = dark_mode
        # 根据模式选择背景和前景
        bg_color = '#1e1e1e' if dark_mode else '#fafafa'
        fg_color = '#d4d4d4' if dark_mode else '#333333'
        select_bg = '#264f78' if dark_mode else '#e3f2fd'
        insert_color = '#ffffff' if dark_mode else '#2196f3'
        
        # 创建ScrolledText组件
        self.text_widget = scrolledtext.ScrolledText(
            parent, 
            height=height, 
            wrap=tk.WORD,
            width=width,
            font=('Microsoft YaHei UI', 9, 'normal'),
            background=bg_color,
            foreground=fg_color,
            selectbackground=select_bg,
            insertbackground=insert_color,
            relief=tk.FLAT,
            borderwidth=1
        )
        
        # 配置颜色标签
        self._setup_color_tags()
        
    def _setup_color_tags(self):
        """设置颜色标签"""
        if self.dark_mode:
            # Neutral Professional (VS Code Dark Style) - 简洁素雅专业
            self.text_widget.tag_config('success', foreground='#6a9955')  # 柔和绿
            self.text_widget.tag_config('warning', foreground='#dcdcaa')  # 柔和黄
            self.text_widget.tag_config('error', foreground='#f44747')    # 柔和红
            self.text_widget.tag_config('info', foreground='#569cd6')     # 经典蓝
            self.text_widget.tag_config('highlight', background='#2d2d30') # 深灰高亮
            self.text_widget.tag_config('progress', foreground='#c586c0')  # 柔和紫
            self.text_widget.tag_config('timestamp', foreground='#858585') # 中性灰
        else:
            # 亮色模式下的清爽配色
            self.text_widget.tag_config('success', foreground='#2d8659')
            self.text_widget.tag_config('warning', foreground='#c77700')
            self.text_widget.tag_config('error', foreground='#c0392b')
            self.text_widget.tag_config('info', foreground='#1a73a8')
            self.text_widget.tag_config('highlight', background='#f5f5f5')
            self.text_widget.tag_config('progress', foreground='#5e35b1')
            self.text_widget.tag_config('timestamp', foreground='#888888')
        
    def insert_colored(self, text, tag=None):
        """插入带颜色的文本"""
        self.text_widget.config(state=tk.NORMAL)
        
        # 自动检测状态标记并应用颜色
        if '[OK]' in text:
            tag = 'success'
        elif '[WARN]' in text:
            tag = 'warning'
        elif '[ERROR]' in text:
            tag = 'error'
        elif '[INFO]' in text:
            tag = 'info'
        elif any(progress_word in text for progress_word in ['进度', '处理', '完成', '%', '批次']):
            tag = 'progress'
            
        if tag:
            self.text_widget.insert(tk.END, text, tag)
        else:
            self.text_widget.insert(tk.END, text)
            
        self.text_widget.config(state=tk.DISABLED)
        self.text_widget.see(tk.END)
        
    def insert(self, index, text, *args):
        """兼容原始insert方法"""
        if index == tk.END:
            self.insert_colored(text)
        else:
            self.text_widget.config(state=tk.NORMAL)
            self.text_widget.insert(index, text, *args)
            self.text_widget.config(state=tk.DISABLED)
            
    def delete(self, start, end=None):
        """删除文本"""
        self.text_widget.config(state=tk.NORMAL)
        self.text_widget.delete(start, end)
        self.text_widget.config(state=tk.DISABLED)
        
    def config(self, **kwargs):
        """兼容config方法"""
        return self.text_widget.config(**kwargs)
        
    def get(self, start, end=None):
        """兼容get方法"""
        return self.text_widget.get(start, end)
        
    def see(self, index):
        """滚动到指定位置"""
        self.text_widget.see(index)
        
    def pack(self, **kwargs):
        """pack布局"""
        self.text_widget.pack(**kwargs)

class TkTextLogHandler(logging.Handler):
    """将logging日志安全写入到Tk文本组件的处理器（通过root.after跨线程）。"""
    def __init__(self, tk_root, append_fn):
        super().__init__()
        self.tk_root = tk_root
        self.append_fn = append_fn

    def emit(self, record):
        try:
            msg = self.format(record)
            self.tk_root.after(0, lambda m=msg: self.append_fn(m + "\n"))
        except Exception:
            pass
        
    def grid(self, **kwargs):
        """grid布局"""
        self.text_widget.grid(**kwargs)

# 导入字幕纠错模块
try:
    from srt_corrector import SRTCorrector, CorrectionAPI
except ImportError:
    # 如果导入失败，定义空的类以避免错误
    class SRTCorrector:
        pass
    class CorrectionAPI:
        pass

class FlowLayout(ttk.Frame):
    """流动布局容器，按钮会自动换行"""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.widgets = []
        self.rows = []
        
    def add_widget(self, widget, padx=5, pady=2):
        """添加widget到流动布局中"""
        self.widgets.append({'widget': widget, 'padx': padx, 'pady': pady})
        self.update_layout()
        
    def update_layout(self):
        """更新布局，重新排列所有widgets"""
        # 清理现有布局
        for row in self.rows:
            for widget in row:
                widget['widget'].grid_forget()
        self.rows.clear()
        
        if not self.widgets:
            return
            
        # 获取容器宽度
        self.update_idletasks()
        container_width = self.winfo_width()
        if container_width <= 1:  # 初次创建时宽度可能为1
            container_width = 800  # 使用默认宽度
            
        current_row = []
        current_width = 0
        row_index = 0
        
        for widget_info in self.widgets:
            widget = widget_info['widget']
            padx = widget_info['padx']
            pady = widget_info['pady']
            
            # 更新widget以获取实际尺寸
            widget.update_idletasks()
            widget_width = widget.winfo_reqwidth() + padx * 2
            
            # 检查是否需要换行
            if current_row and current_width + widget_width > container_width:
                # 当前行已满，开始新行
                self.rows.append(current_row)
                self._place_row(current_row, row_index)
                current_row = []
                current_width = 0
                row_index += 1
                
            # 添加到当前行
            current_row.append(widget_info)
            current_width += widget_width
            
        # 处理最后一行
        if current_row:
            self.rows.append(current_row)
            self._place_row(current_row, row_index)
    
    def _place_row(self, row, row_index):
        """放置一行的widgets"""
        for col_index, widget_info in enumerate(row):
            widget = widget_info['widget']
            padx = widget_info['padx']
            pady = widget_info['pady']
            widget.grid(row=row_index, column=col_index, padx=padx, pady=pady, sticky=tk.W)

class ToolTip:
    """为widget添加tooltip提示"""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)
        
    def show_tooltip(self, event=None):
        if self.tooltip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(self.tooltip, text=self.text, background="#ffffe0", 
                         relief="solid", borderwidth=1)
        label.pack()
        
    def hide_tooltip(self, event=None):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None

def truncate_text(text, max_length=10):
    """截断文本，超长时添加省略号"""
    if len(text) <= max_length:
        return text
    return text[:max_length-1] + "…"

class SRTGuiApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SRT 字幕翻译工具 v2.8.1")
        self.root.geometry("1400x900")  # 进一步增大默认尺寸确保所有按钮显示
        self.root.minsize(1000, 750)  # 增大最小窗口大小
        
        # 设置软件图标
        try:
            icon_path = resource_path("srt_translator.ico")
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except:
            pass  # 如果图标加载失败，继续使用默认图标
            
        # 设置现代化的主题颜色
        self.root.configure(bg='#f8f9fa')
        
        # 设置窗口图标和样式
        self.setup_styles()
        
        # 配置文件路径
        self.app_dir = get_app_dir()
        self.config_file = os.path.join(self.app_dir, "srt_gui_config.json")
        
        # 当前运行的进程
        self.current_process = None
        self.is_running = False
        self.corrector_process = None
        self.polisher_process = None
        self.translation_process = None
        self.checking_process = None
        
        # 输出队列，用于线程间通信
        self.output_queue = queue.Queue()

        self._mp_ctx = multiprocessing.get_context("spawn")
        self.worker_queue = self._mp_ctx.Queue()
        self._polisher_total_batches = None
        self._polisher_done_batches = set()
        
        # 初始化预设内容
        self.init_default_presets()
        
        # 检查脚本兼容性
        self.check_scripts_compatibility()
        
        # 创建主界面
        self.create_widgets()
        
        # 加载配置
        self.load_config()
        
        # 启动输出检查器
        self.check_output_queue()
        
        # 绑定关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def setup_styles(self):
        """设置界面样式"""
        style = ttk.Style()
        
        # 保持默认主题，按钮更简洁美观
        # 不使用clam主题，避免按钮过大过厚重
        
        # 配置标题样式
        style.configure('Title.TLabel', 
                       font=('Microsoft YaHei UI', 14, 'bold'),
                       foreground='#2c3e50',
                       background='#f8f9fa')
        
        # 配置章节标题样式
        style.configure('Section.TLabel', 
                       font=('Microsoft YaHei UI', 10, 'bold'),
                       foreground='#34495e',
                       background='#f8f9fa')
        
        # 按钮样式 - 保持简洁
        style.configure('Running.TButton', 
                       foreground='#e74c3c')
        
        # 状态标签样式
        style.configure('Success.TLabel', 
                       foreground='#27ae60',
                       font=('Microsoft YaHei UI', 9, 'bold'),
                       background='#f8f9fa')
        
        style.configure('Error.TLabel', 
                       foreground='#e74c3c',
                       font=('Microsoft YaHei UI', 9, 'bold'),
                       background='#f8f9fa')
        
        style.configure('Warning.TLabel', 
                       foreground='#f39c12',
                       font=('Microsoft YaHei UI', 9, 'bold'),
                       background='#f8f9fa')
        
        # 保持标签页简洁样式
        style.configure(
            'TNotebook.Tab',
            font=('Microsoft YaHei UI', 10),
            padding=(12, 6),
            foreground="#111111",
        )
        try:
            style.map(
                'TNotebook.Tab',
                # 注意：某些 Windows 主题可能忽略 tab 的 background 映射，但会应用 foreground 映射。
                # 因此前景色必须始终可见，避免出现“白字白底”。
                background=[('selected', '#dbeafe'), ('active', '#eef2ff')],
                foreground=[('selected', '#111111'), ('active', '#111111')],
                font=[('selected', ('Microsoft YaHei UI', 10, 'bold'))],
            )
        except Exception:
            pass
    
    def create_widgets(self):
        """创建主界面控件"""
        # 创建主容器框架，底部预留状态栏空间
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(12, 45))
        
        # 添加软件标题
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 10))
        
        title_label = ttk.Label(title_frame, 
                               text="SRT 字幕翻译工具", 
                               style='Title.TLabel')
        title_label.pack(side=tk.LEFT)
        
        # 副标题
        subtitle_label = ttk.Label(title_frame, 
                                  text="AI驱动，支持字幕翻译/纠错/润色/双语字幕合成", 
                                  foreground="#666666",
                                  font=('Microsoft YaHei UI', 9))
        subtitle_label.pack(side=tk.LEFT, padx=(15, 0))
        
        # 创建主标签页
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # 翻译器标签页
        self.translator_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.translator_frame, text="字幕翻译器")
        self.create_translator_widgets()
        
        # 校验器标签页
        self.checker_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.checker_frame, text="字幕校验器")
        self.create_checker_widgets()
        
        # 双语转换器标签页
        self.bilingual_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.bilingual_frame, text="双语转换器")
        self.create_bilingual_widgets()
        
        # 字幕纠错器标签页
        self.corrector_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.corrector_frame, text="字幕纠错器")
        self.create_corrector_widgets()

        # 字幕润色器标签页
        self.polisher_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.polisher_frame, text="字幕润色器")
        self.create_polisher_widgets()

        # 纠错/润色审核标签页（移到最右边）
        self.review_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.review_frame, text="纠错/润色审核")
        self.create_review_widgets()
        
        # 状态栏
        self._init_notebook_tab_indicator()
        self.create_status_bar()

    def _init_notebook_tab_indicator(self):
        try:
            self._notebook_tab_texts = {tab_id: self.notebook.tab(tab_id, "text") for tab_id in self.notebook.tabs()}
            self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)
            self._on_notebook_tab_changed()
        except Exception:
            self._notebook_tab_texts = {}

    def _on_notebook_tab_changed(self, event=None):
        try:
            current = self.notebook.select()
            for tab_id, base_text in getattr(self, "_notebook_tab_texts", {}).items():
                prefix = "> " if tab_id == current else "  "
                self.notebook.tab(tab_id, text=prefix + base_text)
        except Exception:
            pass
    
    def create_translator_widgets(self):
        """创建翻译器界面"""
        # 主容器
        main_frame = ttk.Frame(self.translator_frame)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 左侧配置面板
        config_frame = ttk.Frame(main_frame)
        config_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        
        # 右侧输出面板
        output_frame = ttk.Frame(main_frame)
        output_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        self.create_translator_config(config_frame)
        self.create_translator_output(output_frame)
    
    def create_translator_config(self, parent):
        """创建翻译器配置面板"""
        # 文件选择部分
        file_frame = ttk.LabelFrame(parent, text="文件设置", padding=(5, 5, 5, 5))
        file_frame.pack(fill=tk.X, pady=(0, 5))
        
        # 输入文件
        input_frame = ttk.Frame(file_frame)
        input_frame.pack(fill=tk.X, pady=(2, 2))
        ttk.Label(input_frame, text="输入SRT文件:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.input_file_var = tk.StringVar()
        self.input_file_var.trace('w', self.on_input_file_change)  # 监听输入文件变化
        self.input_entry = ttk.Entry(input_frame, textvariable=self.input_file_var)
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(input_frame, text="浏览", command=self.browse_input_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # 输出文件
        output_frame = ttk.Frame(file_frame)
        output_frame.pack(fill=tk.X, pady=2)
        ttk.Label(output_frame, text="输出SRT文件:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.output_file_var = tk.StringVar()
        self.output_entry = ttk.Entry(output_frame, textvariable=self.output_file_var)
        self.output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(output_frame, text="浏览", command=self.browse_output_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # API设置部分
        api_frame = ttk.LabelFrame(parent, text="API设置", padding=(5, 5, 5, 5))
        api_frame.pack(fill=tk.X, pady=5)
        
        # 隐藏API类型，始终使用自定义
        self.api_type_var = tk.StringVar(value="custom")

        # API设置头部（折叠控制 + 预设选择 + 测试按钮）
        api_header_frame = ttk.Frame(api_frame)
        api_header_frame.pack(fill=tk.X, pady=2)

        # 折叠按钮
        self.api_details_visible = False
        self.api_toggle_btn = ttk.Button(api_header_frame, text=">", width=3, command=self.toggle_api_details)
        self.api_toggle_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        # API预设下拉框
        ttk.Label(api_header_frame, text="选择API:", width=8).pack(side=tk.LEFT, padx=(0, 5))
        self.api_preset_var = tk.StringVar()
        self.api_preset_combo = ttk.Combobox(api_header_frame, textvariable=self.api_preset_var, width=15)
        self.api_preset_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.api_preset_combo.bind("<<ComboboxSelected>>", self.on_api_preset_change)
        self.api_preset_combo.bind("<Return>", self.on_api_preset_name_change) # 回车确认新名称
        self.api_preset_combo.bind("<FocusOut>", self.on_api_preset_name_change) # 失去焦点保存名称

        # 新增按钮
        self.add_api_btn = ttk.Button(api_header_frame, text="➕", width=3, command=self.add_api_preset)
        self.add_api_btn.pack(side=tk.LEFT, padx=(0, 2))
        ToolTip(self.add_api_btn, "新增API配置")

        # 保存按钮
        self.save_api_btn = ttk.Button(api_header_frame, text="💾", width=3, command=self.save_api_preset)
        self.save_api_btn.pack(side=tk.LEFT, padx=(0, 2))
        ToolTip(self.save_api_btn, "保存当前API配置")

        # 删除按钮
        self.delete_api_btn = ttk.Button(api_header_frame, text="🗑️", width=3, command=self.delete_api_preset)
        self.delete_api_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.delete_api_btn, "删除当前选中的API配置")

        # API详细设置区域（可折叠）
        self.api_details_frame = ttk.Frame(api_frame)
        
        # API服务器URL
        api_endpoint_frame = ttk.Frame(self.api_details_frame)
        api_endpoint_frame.pack(fill=tk.X, pady=2)
        ttk.Label(api_endpoint_frame, text="API服务器:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.api_endpoint_var = tk.StringVar(value="https://api.deepseek.com/v1/chat/completions")
        self.api_endpoint_entry = ttk.Entry(api_endpoint_frame, textvariable=self.api_endpoint_var)
        self.api_endpoint_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # API密钥
        api_key_frame = ttk.Frame(self.api_details_frame)
        api_key_frame.pack(fill=tk.X, pady=2)
        ttk.Label(api_key_frame, text="API密钥:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.api_key_var = tk.StringVar()
        self.api_key_entry = ttk.Entry(api_key_frame, textvariable=self.api_key_var, show="*")
        self.api_key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # 密码显示/隐藏按钮
        self.show_password_var = tk.BooleanVar(value=False)
        self.toggle_password_btn = ttk.Button(api_key_frame, text="显示", width=6, 
                                             command=self.toggle_password_visibility)
        self.toggle_password_btn.pack(side=tk.RIGHT, padx=(2, 0))
        
        # 模型名称
        model_frame = ttk.Frame(self.api_details_frame)
        model_frame.pack(fill=tk.X, pady=2)
        ttk.Label(model_frame, text="模型名称:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.model_var = tk.StringVar(value="deepseek-chat")
        ttk.Entry(model_frame, textvariable=self.model_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # API连通性检测 (底部常驻)
        test_api_frame = ttk.Frame(api_frame)
        test_api_frame.pack(fill=tk.X, pady=5)
        self.test_api_btn = ttk.Button(test_api_frame, text="测试API连接", 
                                      command=self.test_api_connection)
        self.test_api_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # API测试状态标签
        self.api_status_label = ttk.Label(test_api_frame, text="", foreground="gray")
        self.api_status_label.pack(side=tk.LEFT)
        
        # 翻译参数部分 - 使用两列布局
        params_frame = ttk.LabelFrame(parent, text="翻译参数", padding=(5, 5, 5, 5))
        params_frame.pack(fill=tk.X, pady=5)
        
        # 创建一个网格布局容器
        params_grid = ttk.Frame(params_frame)
        params_grid.pack(fill=tk.X, pady=2)
        
        # 批次大小
        ttk.Label(params_grid, text="批次大小:", width=12).grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.batch_size_var = tk.StringVar(value="5")
        self.batch_size_spin = ttk.Spinbox(params_grid, from_=1, to=500, textvariable=self.batch_size_var, width=5)
        self.batch_size_spin.grid(row=0, column=1, sticky=tk.W)
        ToolTip(self.batch_size_spin, "每批发送的字幕条数。值越大并发上下文越多，但更易触发模型分割失败；建议 2~10")
        
        # 上下文大小
        ttk.Label(params_grid, text="上下文大小:", width=12).grid(row=0, column=2, sticky=tk.W, padx=(10, 5))
        self.context_size_var = tk.StringVar(value="2")
        self.context_size_spin = ttk.Spinbox(params_grid, from_=0, to=100, textvariable=self.context_size_var, width=5)
        self.context_size_spin.grid(row=0, column=3, sticky=tk.W)
        ToolTip(self.context_size_spin, "前后文条目数量(每侧)。越大越连贯，但更占用上下文；建议 0~2")
        
        # 线程数
        ttk.Label(params_grid, text="线程数:", width=12).grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=2)
        self.threads_var = tk.StringVar(value="1")
        self.threads_spin = ttk.Spinbox(params_grid, from_=1, to=50, textvariable=self.threads_var, width=5)
        self.threads_spin.grid(row=1, column=1, sticky=tk.W)
        ToolTip(self.threads_spin, "并行批次数(线程)。过高可能触发速率/超时；建议 1~5")
        
        # 温度
        ttk.Label(params_grid, text="温度(0-1):", width=12).grid(row=1, column=2, sticky=tk.W, padx=(10, 5))
        self.temperature_var = tk.StringVar(value="0.8")
        temp_spin = ttk.Spinbox(params_grid, from_=0.0, to=1.0, increment=0.1, textvariable=self.temperature_var, width=5)
        temp_spin.grid(row=1, column=3, sticky=tk.W)
        ToolTip(temp_spin, "温度参数：越低越稳定(0.0-1.0)。0.2~0.4 更一致；0.7~0.9 更有创造性")

        # 选项
        # 结构化输出(JSON防串行)
        self.structured_output_var = tk.BooleanVar(value=False)
        self.structured_output_chk = ttk.Checkbutton(params_grid, text="结构化输出(JSON)", variable=self.structured_output_var)
        self.structured_output_chk.grid(row=0, column=4, sticky=tk.W, padx=(10, 0), pady=2)
        ToolTip(self.structured_output_chk, "要求模型返回JSON数组，防止合并串行；默认关闭")

        # 逐条逐句对齐（直译优先）
        self.literal_align_var = tk.BooleanVar(value=False)
        self.literal_align_chk = ttk.Checkbutton(params_grid, text="逐条逐句对齐", variable=self.literal_align_var)
        self.literal_align_chk.grid(row=1, column=4, sticky=tk.W, padx=(10, 0), pady=2)
        ToolTip(self.literal_align_chk, "直译优先，严格按原文句子结构翻译")

        # 专业模式（实验）
        self.professional_mode_var = tk.BooleanVar(value=False)
        self.professional_mode_chk = ttk.Checkbutton(params_grid, text="专业模式(实验)", variable=self.professional_mode_var)
        self.professional_mode_chk.grid(row=0, column=5, sticky=tk.W, padx=(10, 0), pady=2)
        ToolTip(self.professional_mode_chk, "专业级系统提示词，智能处理Whisper断句问题")

        self.no_resume_var = tk.BooleanVar()
        self.no_resume_chk = ttk.Checkbutton(params_grid, text="不使用断点续接", variable=self.no_resume_var)
        self.no_resume_chk.grid(row=1, column=5, columnspan=1, sticky=tk.W, padx=(10, 0), pady=2)
        ToolTip(self.no_resume_chk, "启用后每次重头开始翻译，忽略progress文件")
        
        
        # 范围设置部分
        range_frame = ttk.LabelFrame(parent, text="翻译范围 (可选)", padding=(5, 5, 5, 5))
        range_frame.pack(fill=tk.X, pady=5)
        
        range_grid = ttk.Frame(range_frame)
        range_grid.pack(fill=tk.X, pady=2)
        
        # 开始编号
        ttk.Label(range_grid, text="开始编号:", width=12).grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.start_num_var = tk.StringVar()
        ttk.Entry(range_grid, textvariable=self.start_num_var, width=10).grid(row=0, column=1, sticky=tk.W)
        
        # 结束编号
        ttk.Label(range_grid, text="结束编号:", width=12).grid(row=0, column=2, sticky=tk.W, padx=(10, 5))
        self.end_num_var = tk.StringVar()
        ttk.Entry(range_grid, textvariable=self.end_num_var, width=10).grid(row=0, column=3, sticky=tk.W)
        
        # 用户提示词
        prompt_frame = ttk.LabelFrame(parent, text="用户提示词 (可选)", padding=(5, 5, 5, 5))
        prompt_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(prompt_frame, text="用自然语言告诉AI如何翻译:").pack(anchor=tk.W, pady=(0, 2))
        
        self.user_prompt_var = tk.StringVar()
        self.user_prompt_text = scrolledtext.ScrolledText(prompt_frame, height=5, wrap=tk.WORD)
        self.user_prompt_text.pack(fill=tk.X, expand=True, pady=(0, 5))
        
        # 预设提示词按钮 - 使用简单grid布局
        preset_frame = ttk.Frame(prompt_frame)
        preset_frame.pack(fill=tk.X)
        
        # 所有预设按钮，使用grid布局（每行6个）
        preset_buttons = [
            ("清空", self.clear_user_prompt, None),
            ("预设1", self.set_preset_1, 1),
            ("预设2", self.set_preset_2, 2),
            ("预设3", self.set_preset_3, 3),
            ("预设4", self.set_preset_4, 4),
            ("预设5", self.set_preset_5, 5),
            ("预设6", self.set_preset_6, 6),
            ("预设7", self.set_preset_7, 7),
            ("预设8", self.set_preset_8, 8),
            ("预设9", self.set_preset_9, 9),
            ("预设10", self.set_preset_10, 10),
            ("编辑预设", self.edit_presets, None),
        ]
        
        cols_per_row = 6
        self.translator_preset_buttons = {}  # 保存按钮引用以便更新tooltip
        self.translator_preset_tooltips = {}
        for idx, (text, cmd, preset_id) in enumerate(preset_buttons):
            row = idx // cols_per_row
            col = idx % cols_per_row
            btn = ttk.Button(preset_frame, text=text, command=cmd)
            btn.grid(row=row, column=col, padx=2, pady=2, sticky=tk.W)
            
            # 为预设按钮添加ToolTip显示预设名称
            if preset_id is not None:
                self.translator_preset_buttons[preset_id] = btn
                preset_data = self.presets.get(preset_id, {})
                name = preset_data.get("name", f"预设{preset_id}")
                self.translator_preset_tooltips[preset_id] = ToolTip(btn, name)
        
        # 控制按钮
        button_frame = ttk.Frame(parent)
        button_frame.pack(fill=tk.X, pady=5)
        
        self.translate_button = ttk.Button(button_frame, text="开始翻译", command=self.start_translation)
        self.translate_button.pack(side=tk.LEFT, padx=(0, 5))
        
        self.stop_button = ttk.Button(button_frame, text="停止翻译", command=self.stop_translation, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(button_frame, text="保存配置", command=self.save_config).pack(side=tk.RIGHT)
    
    def toggle_api_details(self):
        """切换API详细设置的显示状态"""
        if self.api_details_visible:
            self.api_details_frame.pack_forget()
            self.api_toggle_btn.config(text=">")
            self.api_details_visible = False
        else:
            self.api_details_frame.pack(fill=tk.X, pady=2)
            self.api_toggle_btn.config(text="v")
            self.api_details_visible = True

    def on_api_preset_change(self, event=None):
        """API预设切换处理"""
        name = self.api_preset_var.get()
        if not hasattr(self, 'api_configs') or name not in self.api_configs:
            return
            
        config = self.api_configs[name]
        self.api_endpoint_var.set(config.get('api_endpoint', ''))
        self.api_key_var.set(config.get('api_key', ''))
        self.model_var.set(config.get('model', ''))
        self.current_api_config = name

    def on_api_preset_name_change(self, event=None):
        """API预设名称修改/新增处理"""
        new_name = self.api_preset_var.get().strip()
        if not new_name:
            return
        self.current_api_config = new_name
        
        # 更新下拉列表
        values = list(self.api_preset_combo['values'])
        if new_name not in values:
            values.append(new_name)
            self.api_preset_combo['values'] = values

    def add_api_preset(self):
        """新增API配置"""
        # 弹出对话框让用户输入新API名称
        dialog = tk.Toplevel(self.root)
        dialog.title("新增API配置")
        dialog.geometry("350x120")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # 居中显示
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 350) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 120) // 2
        dialog.geometry(f"+{x}+{y}")
        
        ttk.Label(dialog, text="请输入新API配置名称：").pack(pady=(15, 5))
        
        name_var = tk.StringVar()
        name_entry = ttk.Entry(dialog, textvariable=name_var, width=30)
        name_entry.pack(pady=5)
        name_entry.focus_set()
        
        def confirm():
            new_name = name_var.get().strip()
            if not new_name:
                messagebox.showwarning("警告", "API名称不能为空！", parent=dialog)
                return
            
            # 检查是否已存在
            if hasattr(self, 'api_configs') and new_name in self.api_configs:
                messagebox.showwarning("警告", f"API配置 '{new_name}' 已存在！", parent=dialog)
                return
            
            # 创建新的空白配置
            if not hasattr(self, 'api_configs'):
                self.api_configs = {}
            
            self.api_configs[new_name] = {
                "api_endpoint": "",
                "api_key": "",
                "model": ""
            }
            
            # 更新下拉列表
            values = list(self.api_configs.keys())
            self.api_preset_combo['values'] = values
            self.api_preset_combo.set(new_name)
            self.current_api_config = new_name
            
            # 展开API详情区域供用户填写
            if not self.api_details_visible:
                self.toggle_api_details()
            
            # 清空输入框供用户填写
            self.api_endpoint_var.set("")
            self.api_key_var.set("")
            self.model_var.set("")
            
            # 关闭对话框
            dialog.destroy()
            
            # 输出提示
            self.translator_output.insert(tk.END, f"➕ 已创建新API配置: {new_name}\n")
            self.translator_output.insert(tk.END, "   请填写API服务器、密钥和模型，然后点击💾保存\n")
            self.translator_output.see(tk.END)
        
        def cancel():
            dialog.destroy()
        
        # 按钮区域
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="确定", command=confirm, width=8).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="取消", command=cancel, width=8).pack(side=tk.LEFT, padx=10)
        
        # 回车确认
        name_entry.bind("<Return>", lambda e: confirm())
        dialog.bind("<Escape>", lambda e: cancel())

    def save_api_preset(self):
        """保存当前API配置"""
        current_name = self.api_preset_var.get().strip()
        if not current_name:
            messagebox.showwarning("警告", "请先选择或创建一个API配置！")
            return
        
        # 获取当前表单值
        endpoint = self.api_endpoint_var.get().strip()
        api_key = self.api_key_var.get().strip()
        model = self.model_var.get().strip()
        
        # 验证必填项
        if not endpoint:
            messagebox.showwarning("警告", "API服务器地址不能为空！")
            return
        if not api_key:
            messagebox.showwarning("警告", "API密钥不能为空！")
            return
        if not model:
            messagebox.showwarning("警告", "模型名称不能为空！")
            return
        
        # 初始化api_configs如果不存在
        if not hasattr(self, 'api_configs'):
            self.api_configs = {}
        
        # 保存到api_configs
        self.api_configs[current_name] = {
            "api_endpoint": endpoint,
            "api_key": api_key,
            "model": model
        }
        self.current_api_config = current_name
        
        # 更新下拉列表（确保新名称在列表中）
        values = list(self.api_configs.keys())
        self.api_preset_combo['values'] = values
        
        # 静默保存配置到文件
        self.save_config(quiet=True)
        
        # 输出提示
        self.translator_output.insert(tk.END, f"💾 已保存API配置: {current_name}\n")
        self.translator_output.see(tk.END)
        
        # 更新API测试状态标签
        self.api_status_label.config(text="✓ 配置已保存", foreground="green")

    def delete_api_preset(self):
        """删除当前选中的API预设"""
        current_name = self.api_preset_var.get()
        if not current_name:
            return
            
        # 确认删除
        if not messagebox.askyesno("确认删除", f"确定要删除API配置 '{current_name}' 吗？"):
            return
            
        # 从配置中移除
        if hasattr(self, 'api_configs') and current_name in self.api_configs:
            del self.api_configs[current_name]
            
        # 如果删空了，恢复默认
        if not self.api_configs:
            self.api_configs = {
                "Default": {
                    "api_endpoint": "https://api.deepseek.com/v1/chat/completions",
                    "api_key": "",
                    "model": "deepseek-chat"
                }
            }
            
        # 更新下拉列表
        values = list(self.api_configs.keys())
        self.api_preset_combo['values'] = values
        
        # 选中第一个
        new_selection = values[0]
        self.api_preset_combo.set(new_selection)
        self.on_api_preset_change()
        
        # 保存配置
        self.save_config()
        self.translator_output.insert(tk.END, f"🗑️ 已删除API配置: {current_name}\n")
        self.translator_output.see(tk.END)

    def create_translator_output(self, parent):
        """创建翻译器输出面板"""
        output_label = ttk.Label(parent, text="翻译输出", style='Section.TLabel')
        output_label.pack(anchor=tk.W, pady=(0, 5))
        
        # 输出文本框 - 使用彩色日志组件 (开启暗色模式)
        self.translator_output = ColoredLogWidget(parent, height=18, dark_mode=True)
        self.translator_output.pack(fill=tk.BOTH, expand=True)
        
        # 清空按钮
        clear_frame = ttk.Frame(parent)
        clear_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(clear_frame, text="清空输出", command=lambda: self.translator_output.delete(1.0, tk.END)).pack(side=tk.RIGHT)
    
    def create_checker_widgets(self):
        """创建校验器界面"""
        # 主容器
        main_frame = ttk.Frame(self.checker_frame)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左侧配置面板：增加最小宽度，让日志宽度接近“字幕翻译器”的观感
        config_frame = ttk.Frame(main_frame, width=660)
        config_frame.pack_propagate(False)
        config_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 10))

        # 右侧输出面板
        output_frame = ttk.Frame(main_frame)
        output_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.create_checker_config(config_frame)
        self.create_checker_output(output_frame)
    
    def create_bilingual_widgets(self):
        """创建双语转换器界面"""
        # 主容器
        main_frame = ttk.Frame(self.bilingual_frame)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        config_frame = ttk.Frame(main_frame, width=660)
        config_frame.pack_propagate(False)
        config_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 10))

        output_frame = ttk.Frame(main_frame)
        output_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.create_bilingual_config(config_frame)
        self.create_bilingual_output(output_frame)
    
    def create_checker_config(self, parent):
        """创建校验器配置面板"""
        # 文件选择部分
        file_frame = ttk.LabelFrame(parent, text="文件设置", padding=(5, 5, 5, 5))
        file_frame.pack(fill=tk.X, pady=(0, 5))
        
        # 源文件
        source_frame = ttk.Frame(file_frame)
        source_frame.pack(fill=tk.X, pady=(2, 2))
        ttk.Label(source_frame, text="源SRT文件:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.source_file_var = tk.StringVar()
        self.source_entry = ttk.Entry(source_frame, textvariable=self.source_file_var)
        self.source_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(source_frame, text="浏览", command=self.browse_source_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # 翻译文件 - 修改为"翻译后的SRT文件"
        translated_frame = ttk.Frame(file_frame)
        translated_frame.pack(fill=tk.X, pady=2)
        ttk.Label(translated_frame, text="翻译后的SRT文件:", width=15).pack(side=tk.LEFT, padx=(0, 5))
        
        self.translated_file_var = tk.StringVar()
        self.translated_entry = ttk.Entry(translated_frame, textvariable=self.translated_file_var)
        self.translated_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(translated_frame, text="浏览", command=self.browse_translated_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # 报告文件 - 添加"可选"提示
        report_frame = ttk.Frame(file_frame)
        report_frame.pack(fill=tk.X, pady=2)
        ttk.Label(report_frame, text="报告文件(可选):", width=15).pack(side=tk.LEFT, padx=(0, 5))
        
        self.report_file_var = tk.StringVar()
        self.report_entry = ttk.Entry(report_frame, textvariable=self.report_file_var)
        self.report_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(report_frame, text="浏览", command=self.browse_report_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # 控制按钮
        button_frame = ttk.Frame(parent)
        button_frame.pack(fill=tk.X, pady=5)
        
        self.check_button = ttk.Button(button_frame, text="开始校验", command=self.start_checking)
        self.check_button.pack(side=tk.LEFT, padx=(0, 5))

        # 一键修复按钮
        self.auto_fix_button = ttk.Button(button_frame, text="一键修复并重翻缺失批次", command=self.auto_fix_and_retranslate)
        self.auto_fix_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_check_button = ttk.Button(button_frame, text="停止校验", command=self.stop_checking, state=tk.DISABLED)
        self.stop_check_button.pack(side=tk.LEFT)

    def _parse_srt_entries_quick(self, srt_path: str):
        """快速解析SRT，返回按顺序的条目列表(编号、起止时间、文本)。只用于GUI内部计算。"""
        pattern = re.compile(
            r'(\d+)\s*\n'  # number
            r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n'  # time
            r'((?:.+(?:\n|$))+?)'  # content
            r'(?:\n|$)',
            re.MULTILINE
        )
        try:
            with open(srt_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            if not content.endswith('\n'):
                content += '\n'
            entries = []
            for m in pattern.finditer(content):
                entries.append({
                    'number': int(m.group(1)),
                    'start': m.group(2),
                    'end': m.group(3),
                    'text': m.group(4)
                })
            return entries
        except Exception as e:
            self.checker_output.insert(tk.END, f"解析SRT失败: {e}\n")
            self.checker_output.see(tk.END)
            return []

    def _detect_range_tag_from_filename(self, base_without_ext: str):
        """从形如 name_10_200 推断范围标签与去除后的基础名。(range_tag, pure_base)"""
        m = re.search(r"(_\d+_\d+)$", base_without_ext)
        if m:
            return m.group(1), base_without_ext[: -len(m.group(1))]
        return "", base_without_ext

    def _find_progress_and_base(self, translated_file: str):
        """定位progress文件、输出基础名和range_tag。返回(tuple): (progress_path, output_base, range_tag) 或 (None, None, None)"""
        tr_dir = os.path.dirname(translated_file) or '.'
        base_no_ext = os.path.splitext(os.path.basename(translated_file))[0]
        range_tag, pure_base = self._detect_range_tag_from_filename(base_no_ext)

        candidates = []
        # 1) 直接用 name_progress*.json
        candidates.extend(glob.glob(os.path.join(tr_dir, f"{base_no_ext}_progress*.json")))
        # 2) 用去掉range的pure_base
        candidates.extend(glob.glob(os.path.join(tr_dir, f"{pure_base}_progress{range_tag}.json")))
        # 3) 回退：同目录所有progress，筛选名字前缀相同的
        if not candidates:
            for p in glob.glob(os.path.join(tr_dir, f"*_progress*.json")):
                name = os.path.splitext(os.path.basename(p))[0]
                if name.startswith(pure_base + "_progress"):
                    candidates.append(p)

        if not candidates:
            return None, None, None

        # 选择最长匹配(更可能带准确range)
        candidates.sort(key=lambda x: len(os.path.basename(x)), reverse=True)
        progress_path = candidates[0]
        fname = os.path.splitext(os.path.basename(progress_path))[0]
        # fname 形如: out_progress 或 out_progress_10_200
        m = re.match(r"(.+?)_progress(.*)$", fname)
        if not m:
            return None, None, None
        output_base = m.group(1)
        detected_range = m.group(2)  # 可能为空或 _10_200
        return progress_path, output_base, detected_range

    def _compute_batches_to_reset(self, missing_numbers, source_entries, total_batches, range_tag, batch_files_pattern, gui_batch_size_str: str):
        """根据缺失编号推断所属批次。优先用实际 batch_size（来自GUI），必要时回退到批次文件边界分析。"""
        # 确定处理范围与基准起点
        baseline_start = None
        if range_tag:
            m = re.match(r"_(\d+)_(\d+)$", range_tag)
            if m:
                start_num = int(m.group(1))
                end_num = int(m.group(2))
                baseline_start = start_num
                range_entries = [e for e in source_entries if start_num <= e['number'] <= end_num]
            else:
                range_entries = source_entries
        else:
            range_entries = source_entries

        if total_batches <= 0 or len(range_entries) == 0:
            return set()

        # ���Ȱ���现存批文件的编号范围进行定位，确保精确
        mapping = self._map_missing_to_batches_by_files(missing_numbers, batch_files_pattern, total_batches)
        if mapping:
            return set(mapping.values())

        # 尝试使用GUI中的 batch_size
        batch_size = None
        try:
            if gui_batch_size_str:
                batch_size = int(float(gui_batch_size_str))
        except Exception:
            batch_size = None

        # 回退：用名义值
        if not batch_size or batch_size <= 0:
            batch_size = math.ceil(len(range_entries) / total_batches)

        batches = set()
        
        # 【关键修复】：基于字幕条目在列表中的索引位置来计算批次，而不是基于编号值
        # 创建编号到索引的映射
        number_to_index = {entry['number']: idx for idx, entry in enumerate(range_entries)}
        
        for n in missing_numbers:
            if n not in number_to_index:
                continue
            # 获取该字幕条目在排序列表中的索引位置
            index = number_to_index[n]
            # 基于索引位置计算批次号（从1开始）
            b = (index // batch_size) + 1
            if b < 1:
                b = 1
            if b > total_batches:
                b = total_batches
            batches.add(b)

        # 回退：利用已有批次文件的编号范围（保持原有逻辑作为双重保险）
        if not batches:
            batch_minmax = {}
            for path in glob.glob(batch_files_pattern):
                m = re.search(r"(\d+)\.srt$", path)
                if not m:
                    continue
                bnum = int(m.group(1))
                entries = self._parse_srt_entries_quick(path)
                if entries:
                    nums = [e['number'] for e in entries]
                    batch_minmax[bnum] = (min(nums), max(nums))
            for n in missing_numbers:
                chosen = None
                for b, (mn, mx) in batch_minmax.items():
                    if mn <= n <= mx:
                        chosen = b
                        break
                if chosen is None and batch_minmax:
                    # 夹在相邻批之间
                    for b in range(1, total_batches):
                        if b in batch_minmax and (b+1) in batch_minmax:
                            if batch_minmax[b][1] < n < batch_minmax[b+1][0]:
                                chosen = b
                                break
                if chosen:
                    batches.add(chosen)

        return batches

    def _scan_srt_numbers_quick(self, srt_path: str):
        """仅按编号+时间轴快速扫描条目编号列表（不依赖内容匹配）。"""
        nums = []
        try:
            with open(srt_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.read().splitlines()
            i = 0
            while i < len(lines):
                m_num = re.match(r"^\s*(\d+)\s*$", lines[i])
                if m_num and i + 1 < len(lines):
                    if re.match(r"^\s*\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}\s*$", lines[i+1]):
                        try:
                            nums.append(int(m_num.group(1)))
                        except Exception:
                            pass
                        i += 2
                        continue
                i += 1
        except Exception:
            return []
        return nums

    def _map_missing_to_batches_by_files(self, missing_numbers, batch_files_pattern, total_batches):
        """基于现存批文件编号范围，为缺失编号生成批次映射。"""
        mapping = {}
        batch_minmax = {}
        for path in sorted(glob.glob(batch_files_pattern)):
            m = re.search(r"(\d+)\.srt$", path)
            if not m:
                continue
            bnum = int(m.group(1))
            nums = self._scan_srt_numbers_quick(path)
            if nums:
                batch_minmax[bnum] = (min(nums), max(nums))

        if not batch_minmax:
            return mapping

        ordered = sorted(batch_minmax.items(), key=lambda kv: kv[0])
        first_b, (first_min, first_max) = ordered[0]
        last_b, (last_min, last_max) = ordered[-1]

        for n in missing_numbers:
            hit = None
            for b, (mn, mx) in ordered:
                if mn <= n <= mx:
                    hit = b
                    break
            if hit is not None:
                mapping[n] = hit
                continue
            if n < first_min:
                mapping[n] = first_b
                continue
            if n > last_max:
                mapping[n] = last_b
                continue
            for i in range(len(ordered) - 1):
                b_curr, (mn_curr, mx_curr) = ordered[i]
                b_next, (mn_next, mx_next) = ordered[i+1]
                if mx_curr < n < mn_next:
                    mapping[n] = b_curr
                    break

        return mapping

    def auto_fix_and_retranslate(self):
        """一键修复：定位缺失编号→重置对应批次→删除合并文件→断点续翻。"""
        if not self.validate_checker_inputs():
            return

        # 预处理，确保UTF-8
        try:
            self.preprocess_srt_files()
        except Exception as e:
            messagebox.showerror("预处理错误", f"处理文件编码时出错: {str(e)}")
            return

        # 用于“校验差异”的解析：优先使用UTF-8临时文件（避免编码问题）
        source_file = self.temp_source_file or self.source_file_var.get()
        translated_file = self.temp_translated_file or self.translated_file_var.get()
        # 用于“定位进度/批次/删除合并文件”的基础：必须使用用户原始输出路径，而不是临时文件
        original_translated_path = self.translated_file_var.get()

        self.checker_output.insert(tk.END, "开始分析缺失编号…\n")
        self.checker_output.see(tk.END)
        self.root.update()

        src_entries = self._parse_srt_entries_quick(source_file)
        tr_entries = self._parse_srt_entries_quick(translated_file)
        src_nums = {e['number'] for e in src_entries}
        tr_nums = {e['number'] for e in tr_entries}
        missing = sorted(src_nums - tr_nums)

        if not missing:
            self.checker_output.insert(tk.END, "未检测到缺失编号，无需修复。\n")
            self.checker_output.see(tk.END)
            return

        self.checker_output.insert(tk.END, f"检测到缺失编号: {missing}\n")
        self.checker_output.see(tk.END)

        # 定位progress与base：基于原始译文路径进行查找
        progress_path, output_base, range_tag = self._find_progress_and_base(original_translated_path)
        if not progress_path:
            messagebox.showerror("错误", "未找到对应的进度文件(progress.json)。请确认翻译输出与工作目录。")
            return

        self.checker_output.insert(tk.END, f"使用进度文件: {progress_path}\n")
        self.checker_output.insert(tk.END, f"输出基名: {output_base}, 范围标签: {range_tag or '无'}\n")
        self.checker_output.see(tk.END)

        # 读取progress
        try:
            with open(progress_path, 'r', encoding='utf-8') as f:
                prog = json.load(f)
        except Exception as e:
            messagebox.showerror("错误", f"读取进度文件失败: {e}")
            return

        total_batches = int(prog.get('total_batches', 0) or 0)
        completed = set(prog.get('completed_batches', []))

        # 批次文件模式
        batch_glob_pattern = os.path.join(os.path.dirname(progress_path) or '.', f"{output_base}_batch{range_tag}*.srt")

        # 【增强日志】：详细记录批次计算过程
        current_batch_size = self.batch_size_var.get() if hasattr(self, 'batch_size_var') else None
        self.checker_output.insert(tk.END, f"批次计算参数: 总批次={total_batches}, GUI批次大小={current_batch_size}\n")
        
        batches_to_reset = self._compute_batches_to_reset(
            missing,
            src_entries,
            total_batches,
            range_tag,
            batch_glob_pattern,
            current_batch_size,
        )
        if not batches_to_reset:
            messagebox.showerror("错误", "无法定位需要重翻的批次。请检查日志或手动处理。")
            return

        self.checker_output.insert(tk.END, f"需要重置的批次: {sorted(batches_to_reset)}\n")
        
        # 【调试信息】：显示每个缺失编号对应的批次
        for n in missing[:10]:  # 只显示前10个，避免输出过长
            number_to_index = {entry['number']: idx for idx, entry in enumerate(src_entries)}
            if n in number_to_index:
                index = number_to_index[n]
                batch_size = int(float(current_batch_size)) if current_batch_size else math.ceil(len(src_entries) / total_batches)
                calculated_batch = (index // batch_size) + 1
                self.checker_output.insert(tk.END, f"  编号{n} → 索引{index} → 批次{calculated_batch}\n")
        if len(missing) > 10:
            self.checker_output.insert(tk.END, f"  ... 还有 {len(missing)-10} 个缺失编号\n")
            
        self.checker_output.see(tk.END)

        # 【强化进度文件更新】：移除完成标记并验证更新结果
        original_completed = completed.copy()  # 保存原始状态用于对比
        updated_completed = [b for b in completed if b not in batches_to_reset]
        removed_batches = [b for b in completed if b in batches_to_reset]
        
        prog['completed_batches'] = sorted(updated_completed)
        
        # 显示详细的进度更新信息
        self.checker_output.insert(tk.END, f"进度文件更新详情:\n")
        self.checker_output.insert(tk.END, f"  原有完成批次: {sorted(original_completed)}\n")
        self.checker_output.insert(tk.END, f"  移除的批次: {sorted(removed_batches)}\n")
        self.checker_output.insert(tk.END, f"  更新后完成批次: {sorted(updated_completed)}\n")
        
        try:
            with open(progress_path, 'w', encoding='utf-8') as f:
                json.dump(prog, f, ensure_ascii=False, indent=2)
            self.checker_output.insert(tk.END, "✓ 已成功更新进度文件\n")
            
            # 【验证写入】：重新读取文件确认更新成功
            try:
                with open(progress_path, 'r', encoding='utf-8') as f:
                    verify_prog = json.load(f)
                verify_completed = set(verify_prog.get('completed_batches', []))
                if verify_completed == set(updated_completed):
                    self.checker_output.insert(tk.END, "✓ 进度文件更新验证通过\n")
                else:
                    self.checker_output.insert(tk.END, f"⚠ 进度文件验证失败: 期望{sorted(updated_completed)}, 实际{sorted(verify_completed)}\n")
            except Exception as ve:
                self.checker_output.insert(tk.END, f"⚠ 进度文件验证失败: {ve}\n")
                
        except Exception as e:
            messagebox.showerror("错误", f"写入进度文件失败: {e}")
            return

        # 【强化文件清理】：删除对应批次文件，包括可能的备份文件
        deleted_files = []
        failed_deletions = []
        
        for b in sorted(batches_to_reset):
            # 主批次文件
            bf = os.path.join(os.path.dirname(progress_path) or '.', f"{output_base}_batch{range_tag}{b}.srt")
            try:
                if os.path.exists(bf):
                    os.remove(bf)
                    deleted_files.append(bf)
                    self.checker_output.insert(tk.END, f"✓ 已删除批次文件: {bf}\n")
            except Exception as e:
                failed_deletions.append(f"{bf} -> {e}")
                self.checker_output.insert(tk.END, f"✗ 删除批次文件失败: {bf} -> {e}\n")
            
            # 检查并删除可能的备份或临时文件
            backup_patterns = [
                f"{output_base}_batch{range_tag}{b}.srt.bak",
                f"{output_base}_batch{range_tag}{b}.srt.tmp",
            ]
            for backup_file in backup_patterns:
                backup_path = os.path.join(os.path.dirname(progress_path) or '.', backup_file)
                try:
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                        deleted_files.append(backup_path)
                        self.checker_output.insert(tk.END, f"✓ 已删除备份文件: {backup_path}\n")
                except Exception as e:
                    self.checker_output.insert(tk.END, f"✗ 删除备份文件失败: {backup_path} -> {e}\n")

        # 【强化合并文件清理】：删除所有相关的合并文件
        files_to_delete = [
            original_translated_path,  # 主输出文件
            original_translated_path + ".bak",  # 可能的备份文件
            original_translated_path + ".tmp",  # 可能的临时文件
        ]
        
        for file_path in files_to_delete:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    deleted_files.append(file_path)
                    self.checker_output.insert(tk.END, f"✓ 已删除合并文件: {file_path}\n")
            except Exception as e:
                failed_deletions.append(f"{file_path} -> {e}")
                self.checker_output.insert(tk.END, f"✗ 删除合并文件失败: {file_path} -> {e}\n")
        
        # 汇总删除结果
        if deleted_files:
            self.checker_output.insert(tk.END, f"总共成功删除 {len(deleted_files)} 个文件\n")
        if failed_deletions:
            self.checker_output.insert(tk.END, f"警告: {len(failed_deletions)} 个文件删除失败\n")
        self.checker_output.see(tk.END)

        # 构建重翻命令：必须使用最初的输出基名(不含range_tag)，以复用同一个progress
        requested_output = os.path.join(os.path.dirname(progress_path) or '.', f"{output_base}.srt")

        # 解析范围
        start_arg = None
        end_arg = None
        if range_tag:
            m = re.match(r"_(\d+)_(\d+)$", range_tag)
            if m:
                start_arg = m.group(1)
                end_arg = m.group(2)

        # 使用当前翻译器配置构建参数（API/KEY/端点/模型/线程数等），但不传 --no-resume
        cmd = [sys.executable, "srt_translator.py", self.source_file_var.get(), requested_output,
               "--api", "custom",
               "--api-key", self.api_key_var.get(),
               "--api-endpoint", self.api_endpoint_var.get()]
        if self.model_var.get():
            cmd.extend(["--model", self.model_var.get()])
        # 线程数、批次、上下文尽量沿用当前设置（批次大小不会影响resume的批次数，但为安全建议保持与上次一致）
        if self.batch_size_var.get():
            cmd.extend(["--batch-size", self.batch_size_var.get()])
        if self.context_size_var.get():
            cmd.extend(["--context-size", self.context_size_var.get()])
        if self.threads_var.get():
            cmd.extend(["--threads", self.threads_var.get()])
        # 用户提示词
        user_prompt = self.user_prompt_text.get(1.0, tk.END).strip()
        if user_prompt:
            cmd.extend(["--user-prompt", user_prompt])
        # 温度
        if getattr(self, "temperature_var", None) and self.temperature_var.get():
            cmd.extend(["--temperature", self.temperature_var.get()])
        # 逐条逐句对齐
        if getattr(self, "literal_align_var", None) and self.literal_align_var.get():
            cmd.append("--literal-align")
        # 结构化输出
        if getattr(self, "structured_output_var", None) and self.structured_output_var.get():
            cmd.append("--structured-output")
        # 专业模式
        if getattr(self, "professional_mode_var", None) and self.professional_mode_var.get():
            cmd.append("--professional-mode")
        # 范围
        if start_arg and end_arg:
            cmd.extend(["--start", start_arg, "--end", end_arg])

        self.checker_output.insert(tk.END, "开始断点续翻缺失批次…\n")
        self.checker_output.see(tk.END)
        # 复用后台执行器（以translation类型运行，便于完成后自动填充路径）
        try:
            start_num = int(start_arg) if start_arg else None
            end_num = int(end_arg) if end_arg else None
        except Exception:
            start_num = None
            end_num = None

        def _to_int(v, default):
            try:
                return int(float(v))
            except Exception:
                return default

        def _to_float(v, default):
            try:
                return float(v)
            except Exception:
                return default

        job = TranslationJobConfig(
            input_file=self.source_file_var.get(),
            output_file=requested_output,
            api_type="custom",
            api_key=self.api_key_var.get(),
            api_endpoint=self.api_endpoint_var.get(),
            model=self.model_var.get().strip(),
            batch_size=_to_int(self.batch_size_var.get(), 5),
            context_size=_to_int(self.context_size_var.get(), 2),
            threads=_to_int(self.threads_var.get(), 1),
            temperature=_to_float(getattr(self, "temperature_var", tk.StringVar(value="0.8")).get(), 0.8),
            user_prompt=user_prompt,
            resume=True,
            bilingual=False,
            start_num=start_num,
            end_num=end_num,
            literal_align=bool(getattr(self, "literal_align_var", tk.BooleanVar(value=False)).get()),
            structured_output=bool(getattr(self, "structured_output_var", tk.BooleanVar(value=False)).get()),
            professional_mode=bool(getattr(self, "professional_mode_var", tk.BooleanVar(value=False)).get()),
        )

        self.is_running = True
        self.update_ui_state(True, "translation")
        self._start_translation_process(job)
    
    def create_checker_output(self, parent):
        """创建校验器输出面板"""
        output_label = ttk.Label(parent, text="校验输出", style='Section.TLabel')
        output_label.pack(anchor=tk.W, pady=(0, 5))
        
        # 输出文本框 - 使用彩色日志组件
        self.checker_output = ColoredLogWidget(parent, height=18, dark_mode=True)
        self.checker_output.pack(fill=tk.BOTH, expand=True)
        
        # 清空按钮
        clear_frame = ttk.Frame(parent)
        clear_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(clear_frame, text="清空输出", command=lambda: self.checker_output.delete(1.0, tk.END)).pack(side=tk.RIGHT)
    
    def create_bilingual_config(self, parent):
        """创建双语转换器配置面板"""
        # 文件选择部分
        file_frame = ttk.LabelFrame(parent, text="文件设置", padding=(5, 5, 5, 5))
        file_frame.pack(fill=tk.X, pady=(0, 5))
        
        # 原始英文字幕文件
        original_frame = ttk.Frame(file_frame)
        original_frame.pack(fill=tk.X, pady=(2, 2))
        ttk.Label(original_frame, text="原始英文字幕:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.bilingual_original_var = tk.StringVar()
        self.bilingual_original_entry = ttk.Entry(original_frame, textvariable=self.bilingual_original_var)
        self.bilingual_original_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(original_frame, text="浏览", command=self.browse_bilingual_original_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # 已翻译的字幕文件
        translated_frame = ttk.Frame(file_frame)
        translated_frame.pack(fill=tk.X, pady=2)
        ttk.Label(translated_frame, text="已译字幕文件:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.bilingual_translated_var = tk.StringVar()
        self.bilingual_translated_entry = ttk.Entry(translated_frame, textvariable=self.bilingual_translated_var)
        self.bilingual_translated_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(translated_frame, text="浏览", command=self.browse_bilingual_translated_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # 添加“从润色结果加载”按钮
        ttk.Button(translated_frame, text="从润色加载", command=self.load_polished_to_bilingual, width=10).pack(side=tk.RIGHT, padx=(2, 2))
        
        # 输出文件
        output_frame = ttk.Frame(file_frame)
        output_frame.pack(fill=tk.X, pady=2)
        ttk.Label(output_frame, text="输出文件:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.bilingual_output_var = tk.StringVar()
        self.bilingual_output_entry = ttk.Entry(output_frame, textvariable=self.bilingual_output_var)
        self.bilingual_output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(output_frame, text="浏览", command=self.browse_bilingual_output_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # 控制按钮
        button_frame = ttk.Frame(parent)
        button_frame.pack(fill=tk.X, pady=5)
        
        self.bilingual_convert_button = ttk.Button(button_frame, text="开始转换", command=self.start_bilingual_conversion)
        self.bilingual_convert_button.pack(side=tk.LEFT, padx=(0, 5))
        
        self.bilingual_stop_button = ttk.Button(button_frame, text="停止转换", command=self.stop_bilingual_conversion, state=tk.DISABLED)
        self.bilingual_stop_button.pack(side=tk.LEFT, padx=5)
        
        # 添加自动命名输出文件的复选框
        self.auto_name_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(button_frame, text="自动生成输出文件名", variable=self.auto_name_var, 
                       command=self.toggle_auto_naming).pack(side=tk.LEFT, padx=(10, 0))
    
    def create_bilingual_output(self, parent):
        """创建双语转换器输出面板"""
        output_label = ttk.Label(parent, text="转换输出", style='Section.TLabel')
        output_label.pack(anchor=tk.W, pady=(0, 5))
        
        # 输出文本框 - 使用彩色日志组件
        self.bilingual_output = ColoredLogWidget(parent, height=18, dark_mode=True)
        self.bilingual_output.pack(fill=tk.BOTH, expand=True)
        
        # 清空按钮
        clear_frame = ttk.Frame(parent)
        clear_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(clear_frame, text="清空输出", command=lambda: self.bilingual_output.delete(1.0, tk.END)).pack(side=tk.RIGHT)

    def create_polisher_widgets(self):
        """创建字幕文本润色界面"""
        # 主容器
        main_frame = ttk.Frame(self.polisher_frame)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左侧配置面板
        config_frame = ttk.Frame(main_frame)
        config_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        # 右侧输出面板
        output_frame = ttk.Frame(main_frame)
        output_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.create_polisher_config(config_frame)
        self.create_polisher_output(output_frame)

    def create_polisher_config(self, parent):
        """创建字幕润色器配置面板"""
        # 文件选择部分
        file_frame = ttk.LabelFrame(parent, text="文件设置", padding=(5, 5, 5, 5))
        file_frame.pack(fill=tk.X, pady=(0, 5))

        # 输入文件
        input_frame = ttk.Frame(file_frame)
        input_frame.pack(fill=tk.X, pady=(2, 2))
        ttk.Label(input_frame, text="输入SRT文件:", width=12).pack(side=tk.LEFT, padx=(0, 5))

        self.polisher_input_file_var = tk.StringVar()
        self.polisher_input_entry = ttk.Entry(input_frame, textvariable=self.polisher_input_file_var)
        self.polisher_input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(input_frame, text="浏览", command=self.browse_polisher_input_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))

        # 输出文件
        output_frame = ttk.Frame(file_frame)
        output_frame.pack(fill=tk.X, pady=2)
        ttk.Label(output_frame, text="输出SRT文件:", width=12).pack(side=tk.LEFT, padx=(0, 5))

        self.polisher_output_file_var = tk.StringVar()
        self.polisher_output_entry = ttk.Entry(output_frame, textvariable=self.polisher_output_file_var)
        self.polisher_output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(output_frame, text="浏览", command=self.browse_polisher_output_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))

        # API设置提示 - 使用翻译器的配置
        api_info_frame = ttk.LabelFrame(parent, text="API设置", padding=(5, 5, 5, 5))
        api_info_frame.pack(fill=tk.X, pady=5)

        info_label = ttk.Label(api_info_frame, text="将使用翻译器标签页中的API配置\n(API服务器、密钥、模型名称)", 
                              foreground="#2d6a4f", anchor=tk.W, justify=tk.LEFT)
        info_label.pack(fill=tk.X, pady=2)

        # 润色参数设置
        param_frame = ttk.LabelFrame(parent, text="润色参数", padding=(5, 5, 5, 5))
        param_frame.pack(fill=tk.X, pady=5)

        # 批次大小
        batch_frame = ttk.Frame(param_frame)
        batch_frame.pack(fill=tk.X, pady=2)
        ttk.Label(batch_frame, text="批次大小:", width=10).pack(side=tk.LEFT, padx=(0, 5))

        self.polisher_batch_size_var = tk.StringVar(value="10")
        batch_spin = ttk.Spinbox(batch_frame, from_=5, to=20, textvariable=self.polisher_batch_size_var, width=8)
        batch_spin.pack(side=tk.LEFT)
        ToolTip(batch_spin, "每批处理的字幕条数。Netflix字幕润色建议5-10条")

        # 上下文大小
        context_frame = ttk.Frame(param_frame)
        context_frame.pack(fill=tk.X, pady=2)
        ttk.Label(context_frame, text="上下文大小:", width=10).pack(side=tk.LEFT, padx=(0, 5))

        self.polisher_context_size_var = tk.StringVar(value="2")
        context_spin = ttk.Spinbox(context_frame, from_=0, to=5, textvariable=self.polisher_context_size_var, width=8)
        context_spin.pack(side=tk.LEFT)
        ToolTip(context_spin, "提供前后字幕作为润色参考")

        # 线程数
        thread_frame = ttk.Frame(param_frame)
        thread_frame.pack(fill=tk.X, pady=2)
        ttk.Label(thread_frame, text="线程数:", width=10).pack(side=tk.LEFT, padx=(0, 5))

        self.polisher_threads_var = tk.StringVar(value="3")
        thread_spin = ttk.Spinbox(thread_frame, from_=1, to=8, textvariable=self.polisher_threads_var, width=8)
        thread_spin.pack(side=tk.LEFT)
        ToolTip(thread_spin, "并行处理的线程数。润色任务建议1-3个线程")

        # 温度参数
        temp_frame = ttk.Frame(param_frame)
        temp_frame.pack(fill=tk.X, pady=2)
        ttk.Label(temp_frame, text="温度参数:", width=10).pack(side=tk.LEFT, padx=(0, 5))

        self.polisher_temperature_var = tk.StringVar(value="0.3")
        temp_spin = ttk.Spinbox(temp_frame, from_=0.1, to=0.8, increment=0.1, textvariable=self.polisher_temperature_var, width=8)
        temp_spin.pack(side=tk.LEFT)
        ToolTip(temp_spin, "采样温度(0.1-0.8)。润色任务建议使用较低值(0.2-0.4)")

        # 字数分配方案（单选）
        policy_frame = ttk.LabelFrame(parent, text="字数分配方案", padding=(5, 5, 5, 5))
        policy_frame.pack(fill=tk.X, pady=5)

        self.polisher_length_policy_var = tk.StringVar(value="cn_balanced")
        rb_balanced = ttk.Radiobutton(policy_frame, text="中文语速最佳实践", value="cn_balanced", variable=self.polisher_length_policy_var)
        rb_balanced.pack(anchor=tk.W)
        ToolTip(rb_balanced, "平衡模式：约5.5字/秒，适合大多数中文视频，兼顾语速与阅读体验")

        rb_new = ttk.Radiobutton(policy_frame, text="新·中文语速最佳实践", value="cn_balanced_new", variable=self.polisher_length_policy_var)
        rb_new.pack(anchor=tk.W)
        ToolTip(rb_new, '结合了"无"的高质量润色与"平衡"的科学语速控制，且不强制TTS读音')

        rb_exp = ttk.Radiobutton(policy_frame, text="最新中文语速方案（实验）", value="cn_speed_experimental", variable=self.polisher_length_policy_var)
        rb_exp.pack(anchor=tk.W)
        ToolTip(rb_exp, "实验模式：基于最佳实践方案，但移除强制的数字逐位读法（如保留'2023'而非'二零二三'），适合特定配音需求")

        rb_exp2 = ttk.Radiobutton(policy_frame, text="最新中文语速方案（实验2）（推荐）", value="cn_speed_experimental_2", variable=self.polisher_length_policy_var)
        rb_exp2.pack(anchor=tk.W)
        ToolTip(rb_exp2, '【推荐】最高优先级保护"原汁原味"的语言风格，严禁将"回血"等俚语正规化，基于实验模式。综合表现最佳，推荐优先使用。')

        rb_none = ttk.Radiobutton(policy_frame, text="无（不限制字数）", value="none", variable=self.polisher_length_policy_var)
        rb_none.pack(anchor=tk.W)
        ToolTip(rb_none, "自由模式：不限制字数，优先保证语义连贯与断句自然，适合不追求严格对齐的场景")
        
        # ToolTip(policy_frame, "选择不同的字数分配策略会影响润色时的字数建议与上限") # Removed general tooltip in favor of specific ones

        # 用户提示词说明
        prompt_frame = ttk.LabelFrame(parent, text="润色策略说明", padding=(5, 5, 5, 5))
        prompt_frame.pack(fill=tk.X, pady=5)

        prompt_info_label = ttk.Label(prompt_frame, 
                                      text="润色器使用内置的专业润色提示词，会根据上方选择的【字数分配方案】自动调整策略。",
                                      foreground="#2d6a4f", anchor=tk.W, justify=tk.LEFT, wraplength=350)
        prompt_info_label.pack(fill=tk.X, pady=2)

        # 高级选项
        advanced_frame = ttk.LabelFrame(parent, text="高级选项", padding=(5, 5, 5, 5))
        advanced_frame.pack(fill=tk.X, pady=5)

        # 断点续接选项
        self.polisher_resume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(advanced_frame, text="使用断点续接", variable=self.polisher_resume_var).pack(anchor=tk.W, pady=1)

        # 自动校验选项
        self.polisher_auto_verify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(advanced_frame, text="完成后自动校验", variable=self.polisher_auto_verify_var).pack(anchor=tk.W, pady=1)

        # 可选：折角引号转换（编程后处理，默认关闭以保证稳定）
        self.polisher_corner_quotes_var = tk.BooleanVar(value=False)
        corner_cb = ttk.Checkbutton(
            advanced_frame,
            text="将引号转换为折角引号（「」/『』）",
            variable=self.polisher_corner_quotes_var
        )
        corner_cb.pack(anchor=tk.W, pady=1)
        ToolTip(corner_cb, "仅做输出后处理：把 “ ” / \" 转成 「 」 并支持嵌套『』。默认关闭以避免改变现有输出。")

        # 控制按钮
        button_frame = ttk.Frame(parent)
        button_frame.pack(fill=tk.X, pady=10)

        self.polisher_start_button = ttk.Button(button_frame, text="开始润色", command=self.start_polisher)
        self.polisher_start_button.pack(side=tk.LEFT, padx=(0, 5))

        self.polisher_stop_button = ttk.Button(button_frame, text="停止润色", command=self.stop_polisher, state=tk.DISABLED)
        self.polisher_stop_button.pack(side=tk.LEFT, padx=(0, 10))

        # 添加保存配置按钮
        ttk.Button(button_frame, text="保存配置", command=self.save_polisher_config,
                  style='Accent.TButton').pack(side=tk.LEFT, padx=(10, 0))

        # 一键修复并重润色缺失批次
        ttk.Button(button_frame, text="一键修复并重润色缺失批次", command=self.auto_fix_and_repolish).pack(side=tk.LEFT, padx=(10, 0))

        # Netflix标准说明
        netflix_frame = ttk.LabelFrame(parent, text="Netflix字幕标准说明", padding=(5, 5, 5, 5))
        netflix_frame.pack(fill=tk.X, pady=5)

        netflix_text = (
            "• 阅读速度：2.8字符/秒\n"
            "• 短字幕(≤1.5s)：3-20字符，单行\n"
            "• 中等(1.5-3.5s)：5-35字符，可双行\n"
            "• 长字幕(>3.5s)：8-40字符，双行\n"
            "• 重点：语义完整，断句自然"
        )
        ttk.Label(netflix_frame, text=netflix_text, anchor=tk.W, justify=tk.LEFT,
                 foreground="#555555", font=('Microsoft YaHei UI', 8)).pack(fill=tk.X)

    def create_polisher_output(self, parent):
        """创建润色器输出面板"""
        output_label = ttk.Label(parent, text="润色输出", style='Section.TLabel')
        output_label.pack(anchor=tk.W, pady=(0, 5))

        # 输出文本框 - 使用彩色日志组件 (开启暗色模式)
        self.polisher_output = ColoredLogWidget(parent, height=32, dark_mode=True)
        self.polisher_output.pack(fill=tk.BOTH, expand=True)

        # 清空按钮
        clear_frame = ttk.Frame(parent)
        clear_frame.pack(fill=tk.X, pady=(5, 0))

        ttk.Button(clear_frame, text="清空输出", command=lambda: self.polisher_output.delete(1.0, tk.END)).pack(side=tk.RIGHT)

    def create_review_widgets(self):
        """创建纠错审核界面"""
        # 主容器
        main_frame = ttk.Frame(self.review_frame)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 文件选择区域
        file_frame = ttk.LabelFrame(main_frame, text="文件选择", padding=(10, 5))
        file_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 原始文件选择
        original_frame = ttk.Frame(file_frame)
        original_frame.pack(fill=tk.X, pady=2)
        ttk.Label(original_frame, text="原始字幕文件:").pack(side=tk.LEFT)
        self.review_original_file_var = tk.StringVar()
        self.review_original_entry = ttk.Entry(original_frame, textvariable=self.review_original_file_var, width=60)
        self.review_original_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 5))
        ttk.Button(original_frame, text="浏览", command=self.browse_review_original_file).pack(side=tk.RIGHT)
        
        # 纠错后文件选择
        corrected_frame = ttk.Frame(file_frame)
        corrected_frame.pack(fill=tk.X, pady=2)
        ttk.Label(corrected_frame, text="纠错后文件:").pack(side=tk.LEFT)
        self.review_corrected_file_var = tk.StringVar()
        self.review_corrected_entry = ttk.Entry(corrected_frame, textvariable=self.review_corrected_file_var, width=60)
        self.review_corrected_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 5))
        ttk.Button(corrected_frame, text="浏览", command=self.browse_review_corrected_file).pack(side=tk.RIGHT)
        
        # 加载按钮
        load_frame = ttk.Frame(file_frame)
        load_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(load_frame, text="加载对比", command=self.load_comparison, style='Success.TButton').pack(side=tk.LEFT)
        
        # 状态信息显示
        self.review_status_label = ttk.Label(load_frame, text="请选择文件进行对比", foreground="gray")
        self.review_status_label.pack(side=tk.LEFT, padx=(15, 0))
        
        # 统计信息栏
        stats_frame = ttk.Frame(main_frame)
        stats_frame.pack(fill=tk.X, pady=(0, 2))

        # 统计信息标签
        self.review_stats_label = ttk.Label(stats_frame, text="", foreground="blue")
        self.review_stats_label.pack(side=tk.LEFT)

        # 语速阈值设定区域 - 极致紧凑布局
        speed_frame = ttk.Frame(main_frame)
        speed_frame.pack(fill=tk.X, pady=0)

        # 初始化语速阈值（如果还没有设置的话）
        if not hasattr(self, 'cn_speed_min'):
            self.cn_speed_min = 2.0
        if not hasattr(self, 'cn_speed_max'):
            self.cn_speed_max = 4.0

        # 标题
        ttk.Label(speed_frame, text="语速阈值:").pack(side=tk.LEFT)

        # 最小语速
        ttk.Label(speed_frame, text="最小").pack(side=tk.LEFT, padx=(8, 1))
        self.cn_speed_min_var = tk.DoubleVar(value=self.cn_speed_min)
        self.min_speed_scale = tk.Scale(speed_frame, from_=0.5, to=5.0, resolution=0.1,
                                       variable=self.cn_speed_min_var, orient=tk.HORIZONTAL,
                                       length=100, showvalue=0, width=6, sliderlength=20,
                                       sliderrelief='raised', bd=1)
        self.min_speed_scale.pack(side=tk.LEFT, padx=1)
        self.min_speed_label = ttk.Label(speed_frame, text=f"{self.cn_speed_min_var.get():.1f}", width=3)
        self.min_speed_label.pack(side=tk.LEFT, padx=(1, 5))

        # 最大语速
        ttk.Label(speed_frame, text="最大").pack(side=tk.LEFT, padx=(0, 1))
        self.cn_speed_max_var = tk.DoubleVar(value=self.cn_speed_max)
        self.max_speed_scale = tk.Scale(speed_frame, from_=0.5, to=8.0, resolution=0.1,
                                       variable=self.cn_speed_max_var, orient=tk.HORIZONTAL,
                                       length=100, showvalue=0, width=6, sliderlength=20,
                                       sliderrelief='raised', bd=1)
        self.max_speed_scale.pack(side=tk.LEFT, padx=1)
        self.max_speed_label = ttk.Label(speed_frame, text=f"{self.cn_speed_max_var.get():.1f}", width=3)
        self.max_speed_label.pack(side=tk.LEFT, padx=(1, 8))

        # 保存按钮
        ttk.Button(speed_frame, text="保存", command=self.save_speed_thresholds,
                  style='Success.TButton').pack(side=tk.LEFT)

        # 绑定滑块事件以更新数值显示
        self.min_speed_scale.configure(command=self.update_min_speed_label)
        self.max_speed_scale.configure(command=self.update_max_speed_label)
        
        # 主显示区域 - 表格
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 仅对审核表格微调行高（+2px），避免影响其他表格
        try:
            style = ttk.Style()
            base_rowheight = style.lookup("Treeview", "rowheight")
            try:
                base_rowheight = int(base_rowheight) if base_rowheight else None
            except Exception:
                base_rowheight = None
            if base_rowheight is None:
                try:
                    base_rowheight = int(style.configure("Treeview").get("rowheight", 20))
                except Exception:
                    base_rowheight = 20
            style.configure("Review.Treeview", rowheight=base_rowheight + 2)
        except Exception:
            pass

        # 创建Treeview表格
        columns = ("编号", "原文", "纠错后", "恢复", "状态")
        self.review_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended", style="Review.Treeview")
        
        # 配置列
        self.review_tree.heading("编号", text="编号")
        self.review_tree.heading("原文", text="原文")  
        self.review_tree.heading("纠错后", text="纠错后")
        self.review_tree.heading("恢复", text="↩")
        self.review_tree.heading("状态", text="状态")
        
        # 设置列宽
        self.review_tree.column("编号", width=55, anchor=tk.CENTER, stretch=False)
        self.review_tree.column("原文", width=420, anchor=tk.W, stretch=True)
        self.review_tree.column("纠错后", width=420, anchor=tk.W, stretch=True)
        self.review_tree.column("恢复", width=55, anchor=tk.CENTER, stretch=False)
        self.review_tree.column("状态", width=90, anchor=tk.CENTER, stretch=False)
        
        # 添加滚动条（包一层以便同步高亮覆盖层）
        self.review_tree_scrollbar_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self._on_review_scrollbar_y)
        self.review_tree_scrollbar_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self._on_review_scrollbar_x)
        self.review_tree.configure(
            yscrollcommand=self._on_review_tree_yscroll,
            xscrollcommand=self._on_review_tree_xscroll
        )
        
        # 布局表格和滚动条
        self.review_tree.grid(row=0, column=0, sticky="nsew")
        self.review_tree_scrollbar_y.grid(row=0, column=1, sticky="ns")
        self.review_tree_scrollbar_x.grid(row=1, column=0, sticky="ew")
        
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        
        # 配置行高亮标签（整行背景）
        self.review_tree.tag_configure("changed", background="#fff2cc")  # 未处理但有差异：淡黄
        self.review_tree.tag_configure("kept_original", background="#b0ffb0")  # 已处理：更饱和的淡绿

        # 用于彩色差异覆盖的内部结构
        self._review_cell_overlays = {}  # {(item_id, column_name): text_widget}
        self._review_overlay_update_job = None
        
        # 绑定事件
        self.review_tree.bind("<Double-1>", self.on_review_item_double_click)
        self.review_tree.bind("<Button-3>", self.on_review_item_right_click)
        self.review_tree.bind("<Configure>", lambda e: self._schedule_review_overlay_update())
        self.review_tree.bind("<<TreeviewSelect>>", lambda e: self._schedule_review_overlay_update())
        # 悬浮显示“↩”并支持一键恢复（保持原文）
        self.review_tree.bind("<Motion>", self._on_review_tree_motion)
        self.review_tree.bind("<Leave>", self._on_review_tree_leave)
        self.review_tree.bind("<Button-1>", self._on_review_tree_left_click)
        
        # 保存按钮区域
        save_frame = ttk.Frame(main_frame)
        save_frame.pack(fill=tk.X)
        
        ttk.Button(save_frame, text="保存修改", command=self.save_review_modifications, style='Success.TButton').pack(side=tk.LEFT)
        
        # 修改状态显示
        self.review_modified_label = ttk.Label(save_frame, text="", foreground="orange")
        self.review_modified_label.pack(side=tk.LEFT, padx=(15, 0))
        
        # 初始化数据
        self.review_entries = []  # 存储CorrectionReviewEntry对象列表
        self.review_modified = False  # 是否有未保存的修改
        self.review_processed_numbers = set()  # 记录已处理（保持原文/采用AI/手动编辑）的编号
        self.last_review_files = None  # (original_path, corrected_path)
        self.review_item_to_entry = {}
        self._review_hover_item = None
        self._review_restore_tooltip = None
        self._review_restore_tooltip_after_id = None
        # 重新配置审核表格列，新增“时长”“原文语速”“纠错语速”，并收窄“编号”“状态”列宽
        try:
            new_columns = ("编号", "时长", "原文", "原文语速", "纠错后", "恢复", "纠错语速", "状态")
            self.review_tree.configure(columns=new_columns)
            # 表头
            self.review_tree.heading("编号", text="编号")
            self.review_tree.heading("时长", text="时长")
            self.review_tree.heading("原文", text="原文")
            self.review_tree.heading("原文语速", text="原文语速")
            self.review_tree.heading("纠错后", text="纠错后")
            self.review_tree.heading("恢复", text="↩")
            self.review_tree.heading("纠错语速", text="纠错语速")
            self.review_tree.heading("状态", text="状态")
            # 列宽
            self.review_tree.column("编号", width=45, anchor=tk.CENTER, stretch=False)
            self.review_tree.column("时长", width=55, anchor=tk.CENTER, stretch=False)
            self.review_tree.column("原文", width=420, anchor=tk.W, stretch=True)
            self.review_tree.column("原文语速", width=90, anchor=tk.CENTER, stretch=False)
            self.review_tree.column("纠错后", width=420, anchor=tk.W, stretch=True)
            self.review_tree.column("恢复", width=55, anchor=tk.CENTER, stretch=False)
            self.review_tree.column("纠错语速", width=90, anchor=tk.CENTER, stretch=False)
            self.review_tree.column("状态", width=70, anchor=tk.CENTER, stretch=False)
        except Exception:
            pass
    
    def create_status_bar(self):
        """创建浮动状态栏"""
        # 创建浮动状态栏框架，使用place定位
        self.status_frame = tk.Frame(self.root, 
                                   bg='#e8e8e8',  # 浅灰背景
                                   relief=tk.RAISED, 
                                   borderwidth=1)
        
        self.status_var = tk.StringVar(value="就绪")
        self.status_label = tk.Label(self.status_frame, 
                                   textvariable=self.status_var,
                                   bg='#e8e8e8',
                                   fg='#333333',
                                   font=('Microsoft YaHei UI', 9))
        self.status_label.pack(side=tk.LEFT, padx=12, pady=8)
        
        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.status_frame, 
                                          variable=self.progress_var, 
                                          mode='determinate', 
                                          maximum=100,
                                          length=300)
        self.progress_bar.pack(side=tk.RIGHT, padx=12, pady=8, fill=tk.X, expand=True)
        
        # 绑定窗口大小变化事件，动态调整状态栏位置
        self.root.bind('<Configure>', self._update_status_bar_position)
        
        # 初始定位状态栏
        self.root.after(100, self._update_status_bar_position)
    
    def _update_status_bar_position(self, event=None):
        """动态更新状态栏位置，使其始终浮动在窗口底部"""
        if event and event.widget != self.root:
            return  # 只响应主窗口的大小变化
            
        # 获取当前窗口大小
        window_width = self.root.winfo_width()
        window_height = self.root.winfo_height()
        
        # 状态栏高度
        status_height = 40
        
        # 将状态栏定位在窗口底部
        self.status_frame.place(x=0, y=window_height-status_height, width=window_width, height=status_height)
    
    def browse_input_file(self):
        """浏览输入文件"""
        filename = filedialog.askopenfilename(
            title="选择输入SRT文件",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if filename:
            self.input_file_var.set(filename)
            # 总是自动设置输出文件名（无论输出栏是否为空）
            base_name = os.path.splitext(filename)[0]
            self.output_file_var.set(f"{base_name}_translated.srt")
    
    def on_input_file_change(self, *args):
        """当输入文件路径变化时自动设置输出文件路径"""
        input_file = self.input_file_var.get().strip()
        
        # 只有当输入文件路径看起来像一个有效的文件路径时才自动设置输出路径
        if input_file and len(input_file) > 4 and input_file.lower().endswith('.srt'):
            base_name = os.path.splitext(input_file)[0]
            self.output_file_var.set(f"{base_name}_translated.srt")
    
    def browse_output_file(self):
        """浏览输出文件"""
        filename = filedialog.asksaveasfilename(
            title="选择输出SRT文件",
            defaultextension=".srt",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if filename:
            self.output_file_var.set(filename)
    
    def browse_source_file(self):
        """浏览源文件"""
        filename = filedialog.askopenfilename(
            title="选择源SRT文件",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if filename:
            self.source_file_var.set(filename)
    
    def browse_translated_file(self):
        """浏览翻译文件"""
        filename = filedialog.askopenfilename(
            title="选择翻译后的SRT文件",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if filename:
            self.translated_file_var.set(filename)
    
    def browse_report_file(self):
        """浏览报告文件"""
        filename = filedialog.asksaveasfilename(
            title="选择报告文件",
            defaultextension=".md",
            filetypes=[("Markdown files", "*.md"), ("Text files", "*.txt"), ("All files", "*.*")]
        )
        if filename:
            self.report_file_var.set(filename)
    
    # 双语转换器的文件浏览功能
    def browse_bilingual_original_file(self):
        """浏览原始英文字幕文件"""
        filename = filedialog.askopenfilename(
            title="选择原始英文字幕文件",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if filename:
            self.bilingual_original_var.set(filename)
            # 如果启用了自动生成文件名，则自动设置输出文件名
            if self.auto_name_var.get():
                self.auto_generate_bilingual_output_name()
    
    def browse_bilingual_translated_file(self):
        """浏览已翻译的字幕文件"""
        filename = filedialog.askopenfilename(
            title="选择已翻译的字幕文件",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if filename:
            self.bilingual_translated_var.set(filename)
            # 如果启用了自动生成文件名，则自动设置输出文件名
            if self.auto_name_var.get():
                self.auto_generate_bilingual_output_name()
    
    def browse_bilingual_output_file(self):
        """浏览双语字幕输出文件"""
        filename = filedialog.asksaveasfilename(
            title="选择双语字幕输出文件",
            defaultextension=".srt",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if filename:
            self.bilingual_output_var.set(filename)
            # 用户手动选择了输出文件，禁用自动生成
            self.auto_name_var.set(False)
    
    def auto_generate_bilingual_output_name(self):
        """自动生成双语字幕输出文件名"""
        if not self.auto_name_var.get():
            return
            
        translated_file = self.bilingual_translated_var.get().strip()
        if translated_file and translated_file.lower().endswith('.srt'):
            # 在文件名后添加"_双语"后缀
            base_name = os.path.splitext(translated_file)[0]
            output_file = f"{base_name}_双语.srt"
            self.bilingual_output_var.set(output_file)
    
    def toggle_auto_naming(self):
        """切换自动命名功能"""
        if self.auto_name_var.get():
            # 启用自动命名时，重新生成文件名
            self.auto_generate_bilingual_output_name()
        # 如果禁用自动命名，保持用户手动输入的文件名
    
    def validate_translator_inputs(self) -> bool:
        """验证翻译器输入"""
        if not self.input_file_var.get():
            messagebox.showerror("错误", "请选择输入SRT文件")
            return False
        
        if not os.path.exists(self.input_file_var.get()):
            messagebox.showerror("错误", "输入文件不存在")
            return False
        
        if not self.output_file_var.get():
            messagebox.showerror("错误", "请设置输出SRT文件")
            return False
        
        # 验证API参数
        if not self.api_endpoint_var.get():
            messagebox.showerror("错误", "请填写API服务器地址")
            return False
        
        if not self.api_key_var.get():
            messagebox.showerror("错误", "请输入API密钥")
            return False
        
        # 验证数值参数
        try:
            batch_size = int(self.batch_size_var.get())
            if batch_size < 1 or batch_size > 500:
                raise ValueError()
        except ValueError:
            messagebox.showerror("错误", "批次大小必须是1-500之间的整数")
            return False
        
        try:
            context_size = int(self.context_size_var.get())
            if context_size < 0 or context_size > 100:
                raise ValueError()
        except ValueError:
            messagebox.showerror("错误", "上下文大小必须是0-100之间的整数")
            return False
        
        try:
            threads = int(self.threads_var.get())
            if threads < 1 or threads > 50:
                raise ValueError()
        except ValueError:
            messagebox.showerror("错误", "线程数必须是1-50之间的整数")
            return False
        
        # 验证范围参数
        start_num = self.start_num_var.get()
        end_num = self.end_num_var.get()
        
        if start_num and end_num:
            try:
                start = int(start_num)
                end = int(end_num)
                if start > end:
                    messagebox.showerror("错误", "开始编号不能大于结束编号")
                    return False
            except ValueError:
                messagebox.showerror("错误", "字幕编号必须是整数")
                return False
        elif start_num or end_num:
            messagebox.showerror("错误", "如果设置范围，必须同时设置开始和结束编号")
            return False
        
        return True
    
    def validate_checker_inputs(self) -> bool:
        """验证校验器输入"""
        if not self.source_file_var.get():
            messagebox.showerror("错误", "请选择源SRT文件")
            return False
        
        if not os.path.exists(self.source_file_var.get()):
            messagebox.showerror("错误", "源文件不存在")
            return False
        
        if not self.translated_file_var.get():
            messagebox.showerror("错误", "请选择翻译后的SRT文件")
            return False
        
        if not os.path.exists(self.translated_file_var.get()):
            messagebox.showerror("错误", "翻译后的文件不存在")
            return False
        
        return True
    
    def validate_bilingual_inputs(self) -> bool:
        """验证双语转换器输入"""
        if not self.bilingual_original_var.get():
            messagebox.showerror("错误", "请选择原始英文字幕文件")
            return False
        
        if not os.path.exists(self.bilingual_original_var.get()):
            messagebox.showerror("错误", "原始英文字幕文件不存在")
            return False
        
        if not self.bilingual_translated_var.get():
            messagebox.showerror("错误", "请选择已翻译的字幕文件")
            return False
        
        if not os.path.exists(self.bilingual_translated_var.get()):
            messagebox.showerror("错误", "已翻译的字幕文件不存在")
            return False
        
        if not self.bilingual_output_var.get():
            messagebox.showerror("错误", "请设置输出文件路径")
            return False
        
        # 检查原始文件和翻译文件是否相同
        if self.bilingual_original_var.get() == self.bilingual_translated_var.get():
            messagebox.showerror("错误", "原始文件和翻译文件不能是同一个文件")
            return False
        
        return True

    def load_polished_to_bilingual(self):
        """从润色结果加载文件到双语转换器"""
        candidates = []
        try:
            last_polished = getattr(self, "last_polished_file", None)
            if last_polished:
                candidates.append(last_polished)
        except Exception:
            pass
        try:
            polisher_out = getattr(self, "polisher_output_file_var", None)
            if polisher_out is not None and polisher_out.get():
                candidates.append(polisher_out.get())
        except Exception:
            pass

        for path in candidates:
            try:
                if path and os.path.exists(path):
                    self.bilingual_translated_var.set(path)
                    self._add_bilingual_output(f"[INFO] 已加载润色结果: {path}\n")
                    if self.auto_name_var.get():
                        self.auto_generate_bilingual_output_name()
                    return
            except Exception:
                continue

        messagebox.showwarning("提示", "尚未执行润色任务或文件不存在")

    def _add_bilingual_output(self, text):
        """添加双语转换器输出文本"""
        # 确保在主线程更新UI
        self.root.after(0, lambda: self.bilingual_output.insert_colored(text))
    
    def start_translation(self):
        """开始翻译"""
        if not self.validate_translator_inputs():
            return
        
        if self.is_running:
            messagebox.showwarning("警告", "已有任务正在运行")
            return
        
        input_file = self.input_file_var.get()
        output_file = self.output_file_var.get()

        api_key = self.api_key_var.get()
        api_endpoint = self.api_endpoint_var.get()
        model = self.model_var.get().strip()

        def _to_int(v, default):
            try:
                return int(float(v))
            except Exception:
                return default

        def _to_float(v, default):
            try:
                return float(v)
            except Exception:
                return default

        batch_size = _to_int(self.batch_size_var.get(), 5)
        context_size = _to_int(self.context_size_var.get(), 2)
        threads = _to_int(self.threads_var.get(), 1)
        temperature = _to_float(self.temperature_var.get(), 0.8)

        start_num = None
        end_num = None
        try:
            if self.start_num_var.get() and self.end_num_var.get():
                start_num = int(self.start_num_var.get())
                end_num = int(self.end_num_var.get())
        except Exception:
            start_num = None
            end_num = None

        user_prompt = self.user_prompt_text.get(1.0, tk.END).strip()
        resume = not bool(self.no_resume_var.get())

        job = TranslationJobConfig(
            input_file=input_file,
            output_file=output_file,
            api_type="custom",
            api_key=api_key,
            api_endpoint=api_endpoint,
            model=model,
            batch_size=batch_size,
            context_size=context_size,
            threads=threads,
            temperature=temperature,
            user_prompt=user_prompt,
            resume=resume,
            bilingual=False,
            start_num=start_num,
            end_num=end_num,
            literal_align=bool(getattr(self, "literal_align_var", tk.BooleanVar(value=False)).get()),
            structured_output=bool(getattr(self, "structured_output_var", tk.BooleanVar(value=False)).get()),
            professional_mode=bool(getattr(self, "professional_mode_var", tk.BooleanVar(value=False)).get()),
        )

        self.is_running = True
        self.update_ui_state(True, "translation")
        self._start_translation_process(job)
    
    def preprocess_srt_files(self):
        """预处理SRT文件以确保编码正确 - 创建临时UTF-8文件"""
        # 首先在输出中添加提示
        self.checker_output.insert(tk.END, "正在检查和处理文件编码...\n")
        self.checker_output.see(tk.END)
        self.root.update()
        
        source_file = self.source_file_var.get()
        translated_file = self.translated_file_var.get()
        
        # 使用安全的临时文件名（避免使用原始文件名可能包含的特殊字符）
        temp_dir = tempfile.gettempdir()
        unique_id = str(uuid.uuid4())[:8]
        
        self.temp_source_file = os.path.join(temp_dir, f"srt_source_{unique_id}.srt")
        self.temp_translated_file = os.path.join(temp_dir, f"srt_translated_{unique_id}.srt")
        
        self.checker_output.insert(tk.END, f"临时源文件: {self.temp_source_file}\n")
        self.checker_output.insert(tk.END, f"临时翻译文件: {self.temp_translated_file}\n")
        
        # 尝试多种编码读取文件内容
        encodings = ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'iso-8859-1']
        
        # 处理源文件
        source_content = None
        source_encoding = None
        for encoding in encodings:
            try:
                with open(source_file, 'r', encoding=encoding) as f:
                    source_content = f.read()
                source_encoding = encoding
                break
            except UnicodeDecodeError:
                continue
        
        if source_content is None:
            raise Exception(f"无法解码源文件，请尝试使用其他编码方式打开")
        
        # 处理翻译文件
        translated_content = None
        translated_encoding = None
        for encoding in encodings:
            try:
                with open(translated_file, 'r', encoding=encoding) as f:
                    translated_content = f.read()
                translated_encoding = encoding
                break
            except UnicodeDecodeError:
                continue
        
        if translated_content is None:
            raise Exception(f"无法解码翻译文件，请尝试使用其他编码方式打开")
        
        # 将内容保存为UTF-8格式的临时文件
        try:
            with open(self.temp_source_file, 'w', encoding='utf-8') as f:
                f.write(source_content)
            
            with open(self.temp_translated_file, 'w', encoding='utf-8') as f:
                f.write(translated_content)
                
            # 验证文件是否创建成功
            if not os.path.exists(self.temp_source_file) or not os.path.exists(self.temp_translated_file):
                raise Exception("临时文件创建失败")
                
        except Exception as e:
            self.checker_output.insert(tk.END, f"错误：创建临时文件失败 - {str(e)}\n")
            self.checker_output.see(tk.END)
            raise Exception(f"创建临时文件时出错: {str(e)}")
        
        self.checker_output.insert(tk.END, f"源文件编码: {source_encoding}\n")
        self.checker_output.insert(tk.END, f"翻译文件编码: {translated_encoding}\n")
        self.checker_output.insert(tk.END, f"已创建临时UTF-8文件用于校验\n")
        self.checker_output.see(tk.END)
        self.root.update()
    
    def start_bilingual_conversion(self):
        """开始双语转换"""
        if not self.validate_bilingual_inputs():
            return
        
        if self.is_running:
            messagebox.showwarning("警告", "已有任务正在运行")
            return
        
        # 使用多线程直接调用转换函数
        self.run_bilingual_conversion_threaded()
    
    def start_checking(self):
        """开始校验"""
        if not self.validate_checker_inputs():
            return
        
        if self.is_running:
            messagebox.showwarning("警告", "已有任务正在运行")
            return
        
        # 清理临时文件属性
        self.temp_source_file = None
        self.temp_translated_file = None
        
        # 尝试预处理文件确保编码正确
        try:
            self.preprocess_srt_files()
        except Exception as e:
            messagebox.showerror("预处理错误", f"处理文件编码时出错: {str(e)}")
            return
        
        src = self.temp_source_file or self.source_file_var.get()
        dst = self.temp_translated_file or self.translated_file_var.get()
        report = self.report_file_var.get().strip()

        self.is_running = True
        self.update_ui_state(True, "checking")
        self._start_checker_process(CheckerJobConfig(source_file=src, translated_file=dst, report_file=report))
    
    def run_command(self, cmd, task_type):
        """在后台线程中运行命令"""
        def worker():
            try:
                self.is_running = True
                # 所有UI更新在主线程执行
                self.root.after(0, lambda: self.update_ui_state(True, task_type))
                
                # 设置环境变量以解决控制台输出编码问题
                env = os.environ.copy()
                env['PYTHONIOENCODING'] = 'utf-8'
                
                # 启动进程
                self.current_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    encoding='utf-8',
                    bufsize=1,
                    env=env  # 使用修改后的环境变量
                )
                
                # 读取输出
                if self.current_process.stdout:
                    for line in iter(self.current_process.stdout.readline, ''):
                        if line is None:
                            break
                        # 立即调度最小UI刷新，避免长时阻塞造成“未响应”感
                        self.root.after(0, lambda: None)
                        line = line.strip()
                        if line:
                            self.output_queue.put((task_type, line))
                
                # 等待进程结束
                return_code = self.current_process.wait()
                
                if return_code == 0:
                    self.output_queue.put((task_type, f"\n{'='*50}\n[OK] DONE\n{'='*50}"))
                    self.output_queue.put(("status", "任务完成"))
                    # 如果是翻译任务完成，自动填充校验器和双语转换器文件路径
                    if task_type == "translation":
                        self.output_queue.put(("auto_fill_checker", None))
                        self.output_queue.put(("auto_fill_bilingual_no_switch", None))
                else:
                    self.output_queue.put((task_type, f"\n{'='*50}\n[FAIL] EXIT CODE: {return_code}\n{'='*50}"))
                    self.output_queue.put(("status", f"任务失败 (退出码: {return_code})"))
                
            except Exception as e:
                self.output_queue.put((task_type, f"\n错误: {str(e)}"))
                self.output_queue.put(("status", f"错误: {str(e)}"))
            finally:
                self.is_running = False
                self.current_process = None
                self.output_queue.put(("ui_update", task_type))
                
                # 清理临时文件
                self.clean_temp_files()
        
        # 启动工作线程
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
    
    def run_bilingual_command(self, cmd):
        """在后台线程中运行双语转换命令"""
        def worker():
            try:
                self.is_running = True
                # 所有UI更新在主线程执行
                self.root.after(0, lambda: self.update_bilingual_ui_state(True))
                
                # 设置环境变量以解决控制台输出编码问题
                env = os.environ.copy()
                env['PYTHONIOENCODING'] = 'utf-8'
                env['PYTHONUNBUFFERED'] = '1'  # 强制子进程无缓冲输出
                
                # 启动进程
                # 在命令中插入 -u 以确保Python子进程无缓冲
                launch_cmd = list(cmd)
                try:
                    if launch_cmd and launch_cmd[0] == sys.executable and (len(launch_cmd) == 1 or launch_cmd[1] != '-u'):
                        launch_cmd.insert(1, '-u')
                except Exception:
                    pass

                self.current_process = subprocess.Popen(
                    launch_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    encoding='utf-8',
                    bufsize=1,
                    env=env  # 使用修改后的环境变量
                )
                
                # 读取输出
                if self.current_process.stdout:
                    for line in iter(self.current_process.stdout.readline, ''):
                        if line is None:
                            break
                        self.root.after(0, lambda: None)
                        # 将双语转换的输出发送到双语输出区域
                        s = line.strip()
                        if s:
                            self.output_queue.put(("bilingual", s))
                
                # 等待进程结束
                return_code = self.current_process.wait()
                
                if return_code == 0:
                    self.output_queue.put(("bilingual", f"\n{'='*50}\n✅ 双语转换完成！\n{'='*50}"))
                    self.output_queue.put(("status", "双语转换完成"))
                    # 显示输出文件路径
                    self.output_queue.put(("bilingual", f"双语字幕文件已生成: {self.bilingual_output_var.get()}"))
                else:
                    self.output_queue.put(("bilingual", f"\n{'='*50}\n❌ 转换失败 (退出码: {return_code})\n{'='*50}"))
                    self.output_queue.put(("status", f"转换失败 (退出码: {return_code})"))
                
            except Exception as e:
                self.output_queue.put(("bilingual", f"\n错误: {str(e)}"))
                self.output_queue.put(("status", f"错误: {str(e)}"))
            finally:
                self.is_running = False
                self.current_process = None
                self.output_queue.put(("bilingual_ui_update", None))
        
        # 启动工作线程
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
    
    def run_bilingual_conversion_threaded(self):
        """使用多线程直接调用转换函数"""
        def worker():
            try:
                self.is_running = True
                self.bilingual_stop_event = threading.Event()
                
                # 更新UI状态
                self.root.after(0, lambda: self.update_bilingual_ui_state(True))
                
                # 获取文件路径
                original_file = self.bilingual_original_var.get()
                translated_file = self.bilingual_translated_var.get()
                output_file = self.bilingual_output_var.get()
                
                # 获取CPU核心数，默认使用所有核心
                max_workers = os.cpu_count()
                
                # 定义进度回调函数
                def progress_callback(progress, message):
                    # 格式化进度信息
                    progress_text = f"[INFO] 进度: {progress:.1%} - {message}"
                    self.output_queue.put(("bilingual", progress_text))
                    
                    # 更新状态栏
                    status_text = f"双语转换中... {progress:.1%}"
                    self.output_queue.put(("status", status_text))
                    
                    # 强制UI立即更新 - 缩短检查间隔以获得更流畅的进度显示
                    self.root.after_idle(lambda: None)
                    self.root.update_idletasks()
                
                # 初始化信息
                self.output_queue.put(("bilingual", f"[INFO] 开始双语字幕转换"))
                self.output_queue.put(("bilingual", f"[INFO] 原文件: {original_file}"))
                self.output_queue.put(("bilingual", f"[INFO] 译文件: {translated_file}"))
                self.output_queue.put(("bilingual", f"[INFO] 输出文件: {output_file}"))
                self.output_queue.put(("bilingual", f"[INFO] 使用 {max_workers} 个线程并行处理"))
                self.output_queue.put(("status", "双语转换中..."))
                
                # 调用转换函数
                success = convert_to_bilingual(
                    original_file=original_file,
                    translated_file=translated_file, 
                    output_file=output_file,
                    max_workers=max_workers,
                    progress_callback=progress_callback,
                    stop_event=self.bilingual_stop_event
                )
                
                if success:
                    self.output_queue.put(("bilingual", f"\n{'='*50}\n✅ 双语转换完成！\n{'='*50}"))
                    self.output_queue.put(("status", "双语转换完成"))
                    self.output_queue.put(("bilingual", f"[OK] 双语字幕文件已生成: {output_file}"))
                else:
                    if self.bilingual_stop_event.is_set():
                        self.output_queue.put(("bilingual", f"\n{'='*50}\n⏹️ 转换已被用户停止\n{'='*50}"))
                        self.output_queue.put(("status", "转换已停止"))
                    else:
                        self.output_queue.put(("bilingual", f"\n{'='*50}\n❌ 转换失败\n{'='*50}"))
                        self.output_queue.put(("status", "转换失败"))
                
            except Exception as e:
                self.output_queue.put(("bilingual", f"\n[ERROR] 转换过程中发生错误: {str(e)}"))
                self.output_queue.put(("status", f"错误: {str(e)}"))
            finally:
                self.is_running = False
                self.bilingual_stop_event = None
                self.output_queue.put(("bilingual_ui_update", None))
        
        # 启动工作线程
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
    
    def clean_temp_files(self):
        """清理临时文件"""
        try:
            if hasattr(self, 'temp_source_file') and self.temp_source_file and os.path.exists(self.temp_source_file):
                os.remove(self.temp_source_file)
            
            if hasattr(self, 'temp_translated_file') and self.temp_translated_file and os.path.exists(self.temp_translated_file):
                os.remove(self.temp_translated_file)
        except Exception as e:
            safe_file_log(f"clean_temp_files error: {e}")
    
    def stop_translation(self):
        """停止翻译"""
        self.stop_current_task()
    
    def stop_checking(self):
        """停止校验"""
        self.stop_current_task()
    
    def stop_bilingual_conversion(self):
        """停止双语转换"""
        if hasattr(self, 'bilingual_stop_event') and self.bilingual_stop_event:
            # 使用新的停止事件机制
            self.bilingual_stop_event.set()
            self.bilingual_output.insert_colored("\n[WARN] 正在停止双语转换...", "warning")
            self.bilingual_output.see_end()
        else:
            # 回退到旧的停止机制
            self.stop_current_task()
    
    def stop_current_task(self):
        """停止当前任务"""
        if self.current_process and self.current_process.poll() is None:
            try:
                self.current_process.terminate()
                # 等待最多5秒
                try:
                    self.current_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.current_process.kill()
                    self.current_process.wait()
                
                self.translator_output.insert(tk.END, "\n\n[STOP] Stopped by user\n")
                self.checker_output.insert(tk.END, "\n\n[STOP] Stopped by user\n")
                self.status_var.set("任务已停止")
                
            except Exception as e:
                messagebox.showerror("错误", f"停止任务时出错: {str(e)}")

        # Worker processes: translation/checking
        for attr, label in [("translation_process", "翻译"), ("checking_process", "校验")]:
            try:
                p = getattr(self, attr, None)
                if p is not None and p.is_alive():
                    p.terminate()
                    p.join(timeout=2)
                setattr(self, attr, None)
            except Exception:
                pass

        try:
            self.is_running = False
        except Exception:
            pass
        try:
            self.update_ui_state(False, "translation")
            self.update_ui_state(False, "checking")
        except Exception:
            pass
        try:
            self.clean_temp_files()
        except Exception:
            pass

    def _start_corrector_process(self, job: CorrectorJobConfig):
        try:
            if self.corrector_process is not None and self.corrector_process.is_alive():
                self.corrector_process.terminate()
                self.corrector_process.join(timeout=1)
        except Exception:
            pass

        try:
            proc = self._mp_ctx.Process(target=run_corrector_job, args=(job, self.worker_queue))
            proc.daemon = True
            proc.start()
            self.corrector_process = proc
        except Exception as e:
            self._add_corrector_output(f"\n[ERROR] 启动纠错进程失败: {str(e)}\n")
            self.status_var.set("纠错启动失败")
            self._restore_corrector_buttons()
            raise

    def _start_polisher_process(self, job: PolisherJobConfig):
        try:
            if self.polisher_process is not None and self.polisher_process.is_alive():
                self.polisher_process.terminate()
                self.polisher_process.join(timeout=1)
        except Exception:
            pass

        try:
            proc = self._mp_ctx.Process(target=run_polisher_job, args=(job, self.worker_queue))
            proc.daemon = True
            proc.start()
            self.polisher_process = proc
        except Exception as e:
            self._add_polisher_output(f"\n[ERROR] 启动润色进程失败: {str(e)}\n")
            self.status_var.set("润色启动失败")
            self._restore_polisher_ui()
            raise

    def _start_translation_process(self, job: TranslationJobConfig):
        try:
            if self.translation_process is not None and self.translation_process.is_alive():
                self.translation_process.terminate()
                self.translation_process.join(timeout=1)
        except Exception:
            pass

        try:
            proc = self._mp_ctx.Process(target=run_translation_job, args=(job, self.worker_queue))
            proc.daemon = True
            proc.start()
            self.translation_process = proc
        except Exception as e:
            self.translator_output.insert(tk.END, f"\n[ERROR] 启动翻译进程失败: {str(e)}\n")
            self.translator_output.see(tk.END)
            self.status_var.set("翻译启动失败")
            self.update_ui_state(False, "translation")
            raise

    def _start_checker_process(self, job: CheckerJobConfig):
        try:
            if self.checking_process is not None and self.checking_process.is_alive():
                self.checking_process.terminate()
                self.checking_process.join(timeout=1)
        except Exception:
            pass

        try:
            proc = self._mp_ctx.Process(target=run_checker_job, args=(job, self.worker_queue))
            proc.daemon = True
            proc.start()
            self.checking_process = proc
        except Exception as e:
            self.checker_output.insert(tk.END, f"\n[ERROR] 启动校验进程失败: {str(e)}\n")
            self.checker_output.see(tk.END)
            self.status_var.set("校验启动失败")
            self.update_ui_state(False, "checking")
            raise
    
    def update_ui_state(self, running, task_type):
        """更新界面状态"""
        if task_type == "translation":
            if running:
                self.translate_button.configure(state=tk.DISABLED, style='Running.TButton')
                self.stop_button.configure(state=tk.NORMAL)
                # 实批次进度初始化
                self.total_batches = None
                self.completed_batches = 0
                try:
                    self.progress_bar.stop()
                except Exception:
                    pass
                self.progress_bar.configure(mode='determinate', maximum=100)
                self.progress_var.set(0)
                self.status_var.set("正在翻译... 批次 0/?")
            else:
                self.translate_button.configure(state=tk.NORMAL, style='TButton')
                self.stop_button.configure(state=tk.DISABLED)
                try:
                    self.progress_bar.stop()
                except Exception:
                    pass
                self.progress_bar.configure(mode='determinate', maximum=100)
                self.progress_var.set(0)
        elif task_type == "checking":
            if running:
                self.check_button.configure(state=tk.DISABLED, style='Running.TButton')
                self.stop_check_button.configure(state=tk.NORMAL)
                self.status_var.set("正在校验...")
                self.progress_bar.configure(mode='indeterminate')
                self.progress_bar.start()
            else:
                self.check_button.configure(state=tk.NORMAL, style='TButton')
                self.stop_check_button.configure(state=tk.DISABLED)
                self.progress_bar.stop()
                self.progress_bar.configure(mode='determinate', maximum=100)
                self.progress_var.set(0)
    
    def update_bilingual_ui_state(self, running):
        """更新双语转换器界面状态"""
        if running:
            self.bilingual_convert_button.configure(state=tk.DISABLED, style='Running.TButton')
            self.bilingual_stop_button.configure(state=tk.NORMAL)
            # 初始化双语转换进度
            self.bilingual_total = None
            self.bilingual_done = 0
            try:
                self.progress_bar.stop()
            except Exception:
                pass
            self.progress_bar.configure(mode='determinate', maximum=100)
            self.progress_var.set(0)
            self.status_var.set("正在转换双语字幕... 0%")
        else:
            self.bilingual_convert_button.configure(state=tk.NORMAL, style='TButton')
            self.bilingual_stop_button.configure(state=tk.DISABLED)
            try:
                self.progress_bar.stop()
            except Exception:
                pass
            self.progress_bar.configure(mode='determinate', maximum=100)
            self.progress_var.set(0)
    
    def check_output_queue(self):
        """检查输出队列并更新界面"""
        try:
            while True:
                item = self.output_queue.get_nowait()
                self._dispatch_message(item)
        except queue.Empty:
            pass

        try:
            while True:
                item = self.worker_queue.get_nowait()
                self._dispatch_message(item)
        except Exception:
            pass

        check_interval = 50 if self.is_running else 100
        self.root.after(check_interval, self.check_output_queue)

    def _dispatch_message(self, item):
        if not isinstance(item, tuple) or not item:
            return
        msg_type = item[0]

        if msg_type == "translation":
            content = item[1] if len(item) > 1 else ""
            self._maybe_update_translation_progress(content)
            self.translator_output.insert(tk.END, content + "\n")
            self.translator_output.see(tk.END)
            return
        if msg_type == "translation_error":
            err = item[1] if len(item) > 1 else "Unknown error"
            self.translator_output.insert(tk.END, f"\n[ERROR] {err}\n")
            self.translator_output.see(tk.END)
            return
        if msg_type == "translation_done":
            ok = bool(item[1]) if len(item) > 1 else False
            self.translation_process = None
            self.is_running = False
            banner = "\n" + ("=" * 50) + "\n"
            if ok:
                self.translator_output.insert(tk.END, f"{banner}[OK] DONE{banner}")
                self.status_var.set("任务完成")
                try:
                    self.auto_fill_checker_files()
                    self.auto_fill_bilingual_files_no_switch()
                except Exception:
                    pass
            else:
                self.translator_output.insert(tk.END, f"{banner}[FAIL] STOPPED/FAILED{banner}")
                self.status_var.set("任务失败/中止")
            self.translator_output.see(tk.END)
            try:
                self.update_ui_state(False, "translation")
            except Exception:
                pass
            return
        if msg_type == "checking":
            content = item[1] if len(item) > 1 else ""
            self.checker_output.insert(tk.END, content + "\n")
            self.checker_output.see(tk.END)
            return
        if msg_type == "checking_done":
            ok = bool(item[1]) if len(item) > 1 else False
            self.checking_process = None
            self.is_running = False
            banner = "\n" + ("=" * 50) + "\n"
            if ok:
                self.checker_output.insert(tk.END, f"{banner}[OK] DONE{banner}")
                self.status_var.set("校验通过")
            else:
                self.checker_output.insert(tk.END, f"{banner}[WARN] MISMATCH/STOPPED{banner}")
                self.status_var.set("校验不通过/中止")
            self.checker_output.see(tk.END)
            try:
                self.update_ui_state(False, "checking")
            except Exception:
                pass
            try:
                self.clean_temp_files()
            except Exception:
                pass
            return
        if msg_type == "bilingual":
            content = item[1] if len(item) > 1 else ""
            self._maybe_update_bilingual_progress(content)
            self.bilingual_output.insert(tk.END, content + "\n")
            self.bilingual_output.see(tk.END)
            return
        if msg_type == "status":
            self.status_var.set(item[1] if len(item) > 1 else "")
            return
        if msg_type == "ui_update":
            self.update_ui_state(False, item[1] if len(item) > 1 else "")
            return
        if msg_type == "bilingual_ui_update":
            self.update_bilingual_ui_state(False)
            return
        if msg_type == "auto_fill_checker":
            self.auto_fill_checker_files()
            return
        if msg_type == "auto_fill_bilingual":
            self.auto_fill_bilingual_files()
            return
        if msg_type == "auto_fill_bilingual_no_switch":
            self.auto_fill_bilingual_files_no_switch()
            return
        if msg_type == "auto_fill_review":
            content = item[1] if len(item) > 1 else None
            if isinstance(content, (list, tuple)) and len(content) == 2:
                input_file, output_file = content
                self.auto_fill_review_files(input_file, output_file)
            return

        if msg_type == "corrector":
            text = item[1] if len(item) > 1 else ""
            if text and not text.endswith("\n"):
                text += "\n"
            self.corrector_output_text.insert_colored(text)
            return
        if msg_type == "corrector_progress":
            progress = item[1] if len(item) > 1 else 0.0
            message = item[2] if len(item) > 2 else ""
            try:
                pct = float(progress) * 100.0
            except Exception:
                pct = 0.0
            pct = max(0.0, min(100.0, pct))
            try:
                self.progress_bar.stop()
            except Exception:
                pass
            self.progress_bar.configure(mode='determinate', maximum=100)
            self.progress_var.set(pct)
            self.status_var.set(f"纠错进度: {pct:.1f}%")
            if message:
                self.corrector_output_text.insert_colored(f"进度: {pct:.1f}% - {message}\n")
            return
        if msg_type == "corrector_done":
            ok = bool(item[1]) if len(item) > 1 else False
            input_file = item[2] if len(item) > 2 else ""
            output_file = item[3] if len(item) > 3 else ""
            self.corrector_process = None
            if ok:
                self.progress_bar.configure(mode='determinate', maximum=100)
                self.progress_var.set(100)
                self.status_var.set("纠错完成")
                try:
                    self.output_queue.put(("auto_fill_review", (input_file, output_file)))
                except Exception:
                    pass
            else:
                self.status_var.set("纠错失败/中止")
                self.progress_var.set(0)
            self._restore_corrector_buttons()
            return
        if msg_type == "corrector_error":
            err = item[1] if len(item) > 1 else "未知错误"
            self.corrector_process = None
            self.corrector_output_text.insert_colored(f"\n[ERROR] 纠错进程异常: {err}\n")
            self.status_var.set("纠错异常")
            self.progress_var.set(0)
            self._restore_corrector_buttons()
            return

        if msg_type == "polisher":
            text = item[1] if len(item) > 1 else ""
            if text and not text.endswith("\n"):
                text += "\n"
            self._maybe_update_polisher_progress(text)
            self.polisher_output.insert_colored(text)
            return
        if msg_type == "polisher_done":
            ok = bool(item[1]) if len(item) > 1 else False
            input_file = item[2] if len(item) > 2 else ""
            output_file = item[3] if len(item) > 3 else ""
            summary = item[4] if len(item) > 4 else None
            self.polisher_process = None
            if ok:
                # 记录最后一次润色输出，供“双语转换器 -> 从润色加载”使用
                try:
                    self.last_polished_file = output_file
                except Exception:
                    pass
                self.progress_bar.configure(mode='determinate', maximum=100)
                self.progress_var.set(100)
                self._restore_polisher_ui()
                self.status_var.set("润色完成")
                if isinstance(summary, dict):
                    try:
                        if summary.get("perfect"):
                            self.polisher_output.insert_colored("[OK] 校验通过\n")
                        else:
                            self.polisher_output.insert_colored("[WARN] 校验发现不匹配\n")
                    except Exception:
                        pass
                try:
                    self.output_queue.put(("auto_fill_review", (input_file, output_file)))
                except Exception:
                    pass
            else:
                self.progress_var.set(0)
                self._restore_polisher_ui()
                self.status_var.set("润色失败/中止")
            return
        if msg_type == "polisher_error":
            err = item[1] if len(item) > 1 else "未知错误"
            self.polisher_process = None
            self.polisher_output.insert_colored(f"\n[ERROR] 润色进程异常: {err}\n")
            self.progress_var.set(0)
            self._restore_polisher_ui()
            self.status_var.set("润色异常")
            return

    def _maybe_update_bilingual_progress(self, line: str):
        """尝试从双语转换输出中提取进度。若无法解析，则做简单的已处理计数提示。"""
        try:
            # 匹配新的进度格式：[INFO] 进度: 45.6% - 已处理 123/300 个条目
            m1 = re.search(r"进度:\s*(\d+(?:\.\d+)?)%", line)
            if m1:
                pct = float(m1.group(1))
                pct = max(0, min(100, pct))
                self.progress_bar.configure(maximum=100)
                self.progress_var.set(pct)
                return
            
            # 备用：匹配简单的百分比格式
            m2 = re.search(r"(\d{1,3})%", line)
            if m2:
                pct = int(m2.group(1))
                pct = max(0, min(100, pct))
                self.progress_bar.configure(maximum=100)
                self.progress_var.set(pct)
                return
            m2 = re.search(r"(\d+)\s*/\s*(\d+)", line)
            if m2:
                done = int(m2.group(1))
                total = max(1, int(m2.group(2)))
                pct = int(done * 100 / total)
                self.progress_bar.configure(maximum=100)
                self.progress_var.set(pct)
                self.status_var.set(f"正在转换双语字幕... {pct}% ({done}/{total})")
                return
            # 若无法解析具体比例，显示“进行中”提示但不改变数值
            if any(key in line for key in ["processing", "convert", "转换", "处理"]):
                cur = int(self.progress_var.get()) if self.progress_var.get() else 0
                self.progress_var.set(min(99, cur + 1))
                self.status_var.set(f"正在转换双语字幕... {int(self.progress_var.get())}%")
        except Exception:
            pass

    def _maybe_update_polisher_progress(self, line: str):
        """从润色器日志中解析批次进度并刷新进度条。"""
        try:
            m = re.search(r"总计\\s+(\\d+)\\s+个批次，剩余\\s+(\\d+)\\s+个需要处理", line)
            if m:
                total = int(m.group(1))
                remaining = int(m.group(2))
                done = max(0, total - remaining)
                self._polisher_total_batches = total
                # 保持集合的一致性：断点续润色时，尽量不“回退”已完成数
                if done > len(self._polisher_done_batches):
                    # 不知道具体批次号时，仅同步计数到状态显示
                    pass
                self.progress_bar.configure(mode='determinate', maximum=max(1, total))
                self.progress_var.set(min(done, total))
                self.status_var.set(f"润色进度: 批次 {min(done, total)}/{total}")
                return

            m2 = re.search(r"已将润色批次\\s+(\\d+)\\s+写入", line)
            if m2:
                batch_no = int(m2.group(1))
                self._polisher_done_batches.add(batch_no)
                total = self._polisher_total_batches
                if total:
                    done = min(len(self._polisher_done_batches), total)
                    self.progress_bar.configure(mode='determinate', maximum=max(1, total))
                    self.progress_var.set(done)
                    self.status_var.set(f"润色进度: 批次 {done}/{total}")
                else:
                    # 总数未知时用百分比展示不可靠，只显示已完成数
                    self.status_var.set(f"润色进度: 已完成批次 {len(self._polisher_done_batches)}")
                return

            if "润色已完成" in line or "所有批次已完成" in line:
                total = self._polisher_total_batches
                if total:
                    self.progress_bar.configure(mode='determinate', maximum=max(1, total))
                    self.progress_var.set(total)
                    self.status_var.set(f"润色进度: 批次 {total}/{total}")
                else:
                    self.progress_bar.configure(mode='determinate', maximum=100)
                    self.progress_var.set(100)
                    self.status_var.set("润色完成")
        except Exception:
            pass

    def _maybe_update_translation_progress(self, line: str):
        """从翻译器输出行中解析批次总数与已完成批次数，并刷新进度条与状态文本。"""
        try:
            # 解析总批次数与剩余批次
            m = re.search(r"总计\s+(\d+)\s+个批次，剩余\s+(\d+)\s+个需要处理", line)
            if m:
                total = int(m.group(1))
                remaining = int(m.group(2))
                completed = max(0, total - remaining)
                self.total_batches = total
                self.completed_batches = completed
                # 更新进度条最大值与当前值
                self.progress_bar.configure(maximum=max(1, total))
                self.progress_var.set(completed)
                self.status_var.set(f"正在翻译... 批次 {completed}/{total}")
                return

            # 解析单个批次完成
            if re.search(r"已将批次\s+\d+\s+写入", line):
                # 不依赖批次号自增；多线程下也只是累计
                self.completed_batches = (getattr(self, 'completed_batches', 0) or 0) + 1
                total = getattr(self, 'total_batches', None)
                if total:
                    self.progress_bar.configure(maximum=max(1, total))
                    # 限制不要超过总数
                    self.progress_var.set(min(self.completed_batches, total))
                    self.status_var.set(f"正在翻译... 批次 {min(self.completed_batches, total)}/{total}")
                else:
                    # 未知总数时，用百分比无法准确，显示已完成计数
                    self.status_var.set(f"正在翻译... 已完成批次 {self.completed_batches}")
                return

            # 所有批次已完成（开始合并）
            if "所有批次已完成" in line or "翻译完成。输出在" in line:
                total = getattr(self, 'total_batches', None)
                if total:
                    self.progress_bar.configure(maximum=max(1, total))
                    self.progress_var.set(total)
                    self.status_var.set(f"正在翻译... 批次 {total}/{total}")
        except Exception:
            # 安静失败，不影响主流程
            pass
    
    def auto_fill_checker_files(self):
        """自动填充校验器文件路径"""
        try:
            # 获取翻译器的输入和输出文件路径
            input_file = self.input_file_var.get()
            output_file = self.output_file_var.get()
            
            # 检查文件是否存在
            if input_file and os.path.exists(input_file):
                self.source_file_var.set(input_file)
                self.translator_output.insert(tk.END, f"✅ 已自动设置校验器源文件: {input_file}\n")
                self.translator_output.see(tk.END)
            
            # 检查输出文件是否存在（可能包含范围标记）
            if output_file:
                # 首先检查原始输出文件
                if os.path.exists(output_file):
                    self.translated_file_var.set(output_file)
                    self.translator_output.insert(tk.END, f"✅ 已自动设置校验器翻译文件: {output_file}\n")
                    self.translator_output.see(tk.END)
                else:
                    # 如果原始文件不存在，可能是范围翻译，查找带范围标记的文件
                    base_name = os.path.splitext(output_file)[0]
                    dir_name = os.path.dirname(output_file) if os.path.dirname(output_file) else "."
                    
                    # 查找可能的范围翻译文件
                    import glob
                    pattern = f"{base_name}_*_*.srt"
                    range_files = glob.glob(pattern)
                    
                    if range_files:
                        # 使用最新创建的范围文件
                        latest_file = max(range_files, key=os.path.getctime)
                        self.translated_file_var.set(latest_file)
                        self.translator_output.insert(tk.END, f"✅ 已自动设置校验器翻译文件: {latest_file}\n")
                        self.translator_output.see(tk.END)
                    else:
                        self.translator_output.insert(tk.END, f"⚠️ 无法找到翻译输出文件: {output_file}\n")
                        self.translator_output.see(tk.END)
            
            # 自动切换到校验器标签页并提示用户
            self.notebook.select(self.checker_frame)
            self.translator_output.insert(tk.END, f"🔄 已切换到校验器标签页，您可以立即开始校验！\n")
            self.translator_output.see(tk.END)
            
            # 在校验器输出中也添加提示
            self.checker_output.insert(tk.END, "📂 文件路径已自动填充，点击'开始校验'即可开始校验翻译质量。\n")
            self.checker_output.see(tk.END)
            
        except Exception as e:
            self.translator_output.insert(tk.END, f"❌ 自动填充校验器文件路径时出错: {str(e)}\n")
            self.translator_output.see(tk.END)
    
    def auto_fill_bilingual_files(self):
        """自动填充双语转换器文件路径"""
        try:
            # 获取翻译器的输入和输出文件路径
            input_file = self.input_file_var.get()
            output_file = self.output_file_var.get()
            
            # 检查文件是否存在
            if input_file and os.path.exists(input_file):
                self.bilingual_original_var.set(input_file)
                self.translator_output.insert(tk.END, f"✅ 已自动设置双语转换器原始文件: {input_file}\n")
                self.translator_output.see(tk.END)
            
            # 检查输出文件是否存在（可能包含范围标记）
            if output_file:
                # 首先检查原始输出文件
                if os.path.exists(output_file):
                    self.bilingual_translated_var.set(output_file)
                    self.translator_output.insert(tk.END, f"✅ 已自动设置双语转换器翻译文件: {output_file}\n")
                    self.translator_output.see(tk.END)
                else:
                    # 如果原始文件不存在，可能是范围翻译，查找带范围标记的文件
                    base_name = os.path.splitext(output_file)[0]
                    dir_name = os.path.dirname(output_file) if os.path.dirname(output_file) else "."
                    
                    # 查找可能的范围翻译文件
                    import glob
                    pattern = f"{base_name}_*_*.srt"
                    range_files = glob.glob(pattern)
                    
                    if range_files:
                        # 使用最新创建的范围文件
                        latest_file = max(range_files, key=os.path.getctime)
                        self.bilingual_translated_var.set(latest_file)
                        self.translator_output.insert(tk.END, f"✅ 已自动设置双语转换器翻译文件: {latest_file}\n")
                        self.translator_output.see(tk.END)
                    else:
                        self.translator_output.insert(tk.END, f"⚠️ 无法找到翻译输出文件: {output_file}\n")
                        self.translator_output.see(tk.END)
            
            # 自动设置输出文件名
            if output_file:
                base_name = os.path.splitext(output_file)[0]
                bilingual_output_file = f"{base_name}_双语.srt"
                self.bilingual_output_var.set(bilingual_output_file)
                self.translator_output.insert(tk.END, f"✅ 已自动设置双语转换器输出文件: {bilingual_output_file}\n")
                self.translator_output.see(tk.END)
            
            # 自动切换到双语转换器标签页并提示用户
            self.notebook.select(self.bilingual_frame)
            self.translator_output.insert(tk.END, f"🔄 已切换到双语转换器标签页，您可以立即开始转换！\n")
            self.translator_output.see(tk.END)
            
            # 在双语转换器输出中也添加提示
            self.bilingual_output.insert(tk.END, "📂 文件路径已自动填充，点击'开始转换'即可开始生成双语字幕。\n")
            self.bilingual_output.see(tk.END)
            
        except Exception as e:
            self.translator_output.insert(tk.END, f"❌ 自动填充双语转换器文件路径时出错: {str(e)}\n")
            self.translator_output.see(tk.END)
    
    def auto_fill_bilingual_files_no_switch(self):
        """自动填充双语转换器文件路径但不切换标签页"""
        try:
            # 获取翻译器的输入和输出文件路径
            input_file = self.input_file_var.get()
            output_file = self.output_file_var.get()
            
            # 检查文件是否存在
            if input_file and os.path.exists(input_file):
                self.bilingual_original_var.set(input_file)
                self.translator_output.insert(tk.END, f"✅ 已自动设置双语转换器原始文件: {input_file}\n")
                self.translator_output.see(tk.END)
            
            # 检查输出文件是否存在（可能包含范围标记）
            if output_file:
                # 首先检查原始输出文件
                if os.path.exists(output_file):
                    self.bilingual_translated_var.set(output_file)
                    self.translator_output.insert(tk.END, f"✅ 已自动设置双语转换器翻译文件: {output_file}\n")
                    self.translator_output.see(tk.END)
                else:
                    # 如果原始文件不存在，可能是范围翻译，查找带范围标记的文件
                    base_name = os.path.splitext(output_file)[0]
                    dir_name = os.path.dirname(output_file) if os.path.dirname(output_file) else "."
                    
                    # 查找可能的范围翻译文件
                    import glob
                    pattern = f"{base_name}_*_*.srt"
                    range_files = glob.glob(pattern)
                    
                    if range_files:
                        # 使用最新创建的范围文件
                        latest_file = max(range_files, key=os.path.getctime)
                        self.bilingual_translated_var.set(latest_file)
                        self.translator_output.insert(tk.END, f"✅ 已自动设置双语转换器翻译文件: {latest_file}\n")
                        self.translator_output.see(tk.END)
                    else:
                        self.translator_output.insert(tk.END, f"⚠️ 无法找到翻译输出文件: {output_file}\n")
                        self.translator_output.see(tk.END)
            
            # 自动设置输出文件名
            if output_file:
                base_name = os.path.splitext(output_file)[0]
                bilingual_output_file = f"{base_name}_双语.srt"
                self.bilingual_output_var.set(bilingual_output_file)
                self.translator_output.insert(tk.END, f"✅ 已自动设置双语转换器输出文件: {bilingual_output_file}\n")
                self.translator_output.see(tk.END)
            
            # 不切换标签页，只在输出中提示
            self.translator_output.insert(tk.END, f"📂 双语转换器文件路径已自动填充，可在双语转换器标签页中查看。\n")
            self.translator_output.see(tk.END)
            
        except Exception as e:
            self.translator_output.insert(tk.END, f"❌ 自动填充双语转换器文件路径时出错: {str(e)}\n")
            self.translator_output.see(tk.END)
    
    def load_config(self):
        """从文件加载配置"""
        config_path = self.config_file
        if not os.path.exists(config_path):
            rp = resource_path("srt_gui_config.json")
            if rp and os.path.exists(rp):
                config_path = rp

        if not os.path.exists(config_path):
            # 初始化默认API配置结构
            self.api_configs = {
                "Default": {
                    "api_endpoint": "https://api.deepseek.com/v1/chat/completions",
                    "api_key": "",
                    "model": "deepseek-chat"
                }
            }
            self.current_api_config = "Default"
            if hasattr(self, 'api_preset_combo'):
                self.api_preset_combo['values'] = ["Default"]
                self.api_preset_combo.set("Default")
            return
        
        # 暂时禁用自动保存，避免加载时触发
        self._loading_config = True
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            if config_path != self.config_file:
                try:
                    with open(self.config_file, 'w', encoding='utf-8') as fw:
                        json.dump(config, fw, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            
            # --- API多配置加载与迁移 ---
            self.api_configs = config.get("api_configs", {})
            self.current_api_config = config.get("current_api_config", "Default")
            
            # 迁移旧配置：如果不存在api_configs，则使用旧的顶层配置创建默认配置
            if not self.api_configs:
                old_endpoint = config.get("api_endpoint", "https://api.deepseek.com/v1/chat/completions")
                old_key = config.get("api_key", "")
                old_model = config.get("model", "deepseek-chat")
                
                self.api_configs = {
                    "Default": {
                        "api_endpoint": old_endpoint,
                        "api_key": old_key,
                        "model": old_model
                    }
                }
                self.current_api_config = "Default"
            
            # 更新UI下拉框
            if hasattr(self, 'api_preset_combo'):
                self.api_preset_combo['values'] = list(self.api_configs.keys())
                self.api_preset_combo.set(self.current_api_config)
            
            # 应用当前选中的API配置
            current_conf = self.api_configs.get(self.current_api_config, {})
            self.api_endpoint_var.set(current_conf.get("api_endpoint", "https://api.deepseek.com/v1/chat/completions"))
            self.api_key_var.set(current_conf.get("api_key", ""))
            self.model_var.set(current_conf.get("model", "deepseek-chat"))
            # ---------------------------
            self.batch_size_var.set(config.get("batch_size", "5"))
            self.context_size_var.set(config.get("context_size", "2"))
            self.threads_var.set(config.get("threads", "1"))
            self.no_resume_var.set(config.get("no_resume", False))
            self.temperature_var.set(str(config.get("temperature", "0.8")))
            
            # 恢复API详细设置的折叠状态
            should_be_visible = config.get("api_details_visible", False)
            if should_be_visible != self.api_details_visible:
                self.toggle_api_details()
            
            # 加载纠错器配置
            corrector_config = config.get("corrector", {})
            if corrector_config:
                # 安全地设置纠错器参数（检查变量是否存在）
                if hasattr(self, 'corrector_batch_size_var'):
                    self.corrector_batch_size_var.set(corrector_config.get("batch_size", "5"))
                if hasattr(self, 'corrector_threads_var'):
                    self.corrector_threads_var.set(corrector_config.get("threads", "3"))
                if hasattr(self, 'corrector_temperature_var'):
                    self.corrector_temperature_var.set(corrector_config.get("temperature", "0.3"))
                if hasattr(self, 'corrector_context_window_var'):
                    self.corrector_context_window_var.set(corrector_config.get("context_window", "2"))
                if hasattr(self, 'corrector_batch_mode_var'):
                    self.corrector_batch_mode_var.set(corrector_config.get("batch_mode", "逐条处理"))
                if hasattr(self, 'corrector_timeout_var'):
                    self.corrector_timeout_var.set(corrector_config.get("timeout_seconds", "180"))
                
                # 加载格式规范化选项（检查变量是否存在）
                format_options = corrector_config.get("format_options", {})
                if hasattr(self, 'clean_newlines_var'):
                    self.clean_newlines_var.set(format_options.get("clean_newlines", True))
                if hasattr(self, 'remove_spaces_var'):
                    self.remove_spaces_var.set(format_options.get("remove_spaces", True))
                if hasattr(self, 'normalize_punctuation_var'):
                    self.normalize_punctuation_var.set(format_options.get("normalize_punctuation", True))
                if hasattr(self, 'smart_line_break_var'):
                    self.smart_line_break_var.set(format_options.get("smart_line_break", True))
                if hasattr(self, 'smart_spacing_var'):
                    self.smart_spacing_var.set(format_options.get("smart_spacing", True))
                if hasattr(self, 'smart_punctuation_var'):
                    self.smart_punctuation_var.set(format_options.get("smart_punctuation", True))
                if hasattr(self, 'fluency_optimization_var'):
                    self.fluency_optimization_var.set(format_options.get("fluency_optimization", True))
                
                # 加载纠错器用户提示词
                if hasattr(self, 'corrector_user_prompt_text'):
                    user_prompt = corrector_config.get("user_prompt", "")
                    self.corrector_user_prompt_text.delete(1.0, tk.END)
                    if user_prompt:
                        self.corrector_user_prompt_text.insert(1.0, user_prompt)
            
            # 加载预设内容
            saved_presets = config.get("presets", {})
            if saved_presets:
                # 检查是否为旧格式（直接存储字符串）并转换为新格式
                for key, value in saved_presets.items():
                    if isinstance(value, str):
                        # 旧格式：直接是字符串内容
                        preset_id = int(key) if str(key).isdigit() else None
                        if preset_id and preset_id in self.presets:
                            # 保留默认名称，更新内容
                            self.presets[preset_id]["content"] = value
                    elif isinstance(value, dict) and "name" in value and "content" in value:
                        # 新格式：包含name和content的字典
                        preset_id = int(key) if str(key).isdigit() else None
                        if preset_id:
                            self.presets[preset_id] = value
            
            # 更新翻译器预设按钮的ToolTip文本
            if hasattr(self, 'translator_preset_tooltips'):
                for preset_id in range(1, 11):  # 1-10个预设
                    if preset_id in self.translator_preset_tooltips and preset_id in self.presets:
                        name = self.presets[preset_id].get("name", f"预设{preset_id}")
                        self.translator_preset_tooltips[preset_id].text = name
            
            # 加载纠错器预设内容
            saved_corrector_presets = config.get("corrector_presets", {})
            if saved_corrector_presets:
                for key, value in saved_corrector_presets.items():
                    if isinstance(value, dict) and "name" in value and "content" in value:
                        preset_id = int(key) if str(key).isdigit() else None
                        if preset_id:
                            self.corrector_presets[preset_id] = value
                
                # 统一更新所有按钮文本（确保界面同步）
                if hasattr(self, 'corrector_preset_buttons'):
                    for preset_id in range(1, 9):  # 更新为1-8个预设
                        if preset_id in self.corrector_preset_buttons and preset_id in self.corrector_presets:
                            full_name = self.corrector_presets[preset_id]["name"]
                            display_name = truncate_text(full_name, 10)
                            self.corrector_preset_buttons[preset_id].config(text=display_name)
                            
                            # 更新tooltip
                            if preset_id in self.corrector_preset_tooltips:
                                self.corrector_preset_tooltips[preset_id].text = full_name
                            elif len(full_name) > 10:
                                # 创建新的tooltip
                                self.corrector_preset_tooltips[preset_id] = ToolTip(
                                    self.corrector_preset_buttons[preset_id], full_name)

                            debug_file_log(f"preset_button updated: id={preset_id} name={display_name}")

            # 恢复翻译器的直译对齐选项
            if hasattr(self, 'literal_align_var'):
                self.literal_align_var.set(bool(config.get("literal_align", False)))
            if hasattr(self, 'structured_output_var'):
                self.structured_output_var.set(bool(config.get("structured_output", False)))
            if hasattr(self, 'professional_mode_var'):
                self.professional_mode_var.set(bool(config.get("professional_mode", False)))

            # 加载润色器配置
            polisher_config = config.get("polisher", {})
            if polisher_config:
                # 安全地设置润色器参数（检查变量是否存在）
                if hasattr(self, 'polisher_batch_size_var'):
                    self.polisher_batch_size_var.set(polisher_config.get("batch_size", "10"))
                if hasattr(self, 'polisher_context_size_var'):
                    self.polisher_context_size_var.set(polisher_config.get("context_size", "2"))
                if hasattr(self, 'polisher_threads_var'):
                    self.polisher_threads_var.set(polisher_config.get("threads", "3"))
                if hasattr(self, 'polisher_temperature_var'):
                    self.polisher_temperature_var.set(polisher_config.get("temperature", "0.3"))
                if hasattr(self, 'polisher_resume_var'):
                    self.polisher_resume_var.set(polisher_config.get("resume", True))
                if hasattr(self, 'polisher_auto_verify_var'):
                    self.polisher_auto_verify_var.set(polisher_config.get("auto_verify", True))
                if hasattr(self, 'polisher_length_policy_var'):
                    self.polisher_length_policy_var.set(polisher_config.get("length_policy", "cn_balanced"))
                if hasattr(self, 'polisher_corner_quotes_var'):
                    self.polisher_corner_quotes_var.set(polisher_config.get("corner_quotes", False))

            # 加载语速阈值设定
            speed_thresholds = config.get("speed_thresholds", {})
            if speed_thresholds:
                self.cn_speed_min = speed_thresholds.get("cn_speed_min", 2.0)
                self.cn_speed_max = speed_thresholds.get("cn_speed_max", 4.0)

                # 更新界面控件（如果已创建）
                if hasattr(self, 'cn_speed_min_var'):
                    self.cn_speed_min_var.set(self.cn_speed_min)
                if hasattr(self, 'cn_speed_max_var'):
                    self.cn_speed_max_var.set(self.cn_speed_max)
                if hasattr(self, 'min_speed_label'):
                    self.min_speed_label.config(text=f"{self.cn_speed_min:.1f}")
                if hasattr(self, 'max_speed_label'):
                    self.max_speed_label.config(text=f"{self.cn_speed_max:.1f}")

        except Exception as e:
            safe_file_log(f"load_config error: {e}")
        finally:
            # 重新启用自动保存
            self._loading_config = False
    
    def save_config(self, quiet=False):
        """保存配置到文件"""
        if getattr(self, '_loading_config', False):
            return
            
        # 更新当前API配置
        current_name = getattr(self, "current_api_config", "Default")
        if not hasattr(self, "api_configs"):
             self.api_configs = {}
        
        self.api_configs[current_name] = {
            "api_endpoint": self.api_endpoint_var.get(),
            "api_key": self.api_key_var.get(),
            "model": self.model_var.get()
        }
            
        config = {
            "api_configs": self.api_configs,
            "current_api_config": current_name,
            "api_details_visible": getattr(self, "api_details_visible", False),
            
            # 保持旧字段以兼容
            "api_endpoint": self.api_endpoint_var.get(),
            "api_key": self.api_key_var.get(),
            "model": self.model_var.get(),
            
            "batch_size": self.batch_size_var.get(),
            "context_size": self.context_size_var.get(),
            "threads": self.threads_var.get(),
            "no_resume": self.no_resume_var.get(),
            "temperature": self.temperature_var.get(),
            "literal_align": getattr(self, 'literal_align_var', tk.BooleanVar(value=False)).get(),
            "structured_output": getattr(self, 'structured_output_var', tk.BooleanVar(value=False)).get(),
            "professional_mode": getattr(self, 'professional_mode_var', tk.BooleanVar(value=False)).get(),
            "presets": self.presets,  # 保存翻译器预设内容
            "corrector_presets": self.corrector_presets,  # 保存纠错器预设内容
            # 字幕纠错器配置
            "corrector": {
                "batch_size": self.corrector_batch_size_var.get(),
                "threads": self.corrector_threads_var.get(),
                "temperature": self.corrector_temperature_var.get(),
                "context_window": self.corrector_context_window_var.get(),
                "timeout_seconds": self.corrector_timeout_var.get(),
                "batch_mode": self.corrector_batch_mode_var.get(),
                # 格式规范化选项
                "format_options": {
                    "clean_newlines": self.clean_newlines_var.get(),
                    "remove_spaces": self.remove_spaces_var.get(),
                    "normalize_punctuation": self.normalize_punctuation_var.get(),
                    "smart_line_break": self.smart_line_break_var.get(),
                    "smart_spacing": self.smart_spacing_var.get(),
                    "smart_punctuation": self.smart_punctuation_var.get(),
                    "fluency_optimization": self.fluency_optimization_var.get(),
                },
                "user_prompt": self.corrector_user_prompt_text.get(1.0, tk.END).strip()
            },
            # 字幕润色器配置
            "polisher": {
                "batch_size": getattr(self, 'polisher_batch_size_var', tk.StringVar(value="10")).get(),
                "context_size": getattr(self, 'polisher_context_size_var', tk.StringVar(value="2")).get(),
                "threads": getattr(self, 'polisher_threads_var', tk.StringVar(value="3")).get(),
                "temperature": getattr(self, 'polisher_temperature_var', tk.StringVar(value="0.3")).get(),
                "resume": getattr(self, 'polisher_resume_var', tk.BooleanVar(value=True)).get(),
                "auto_verify": getattr(self, 'polisher_auto_verify_var', tk.BooleanVar(value=True)).get(),
                "length_policy": getattr(self, 'polisher_length_policy_var', tk.StringVar(value="cn_balanced")).get(),
                "corner_quotes": getattr(self, 'polisher_corner_quotes_var', tk.BooleanVar(value=False)).get()
            },
            # 语速阈值设定
            "speed_thresholds": {
                "cn_speed_min": getattr(self, 'cn_speed_min', 2.0),
                "cn_speed_max": getattr(self, 'cn_speed_max', 4.0)
            }
        }
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            if not quiet:
                # 使用状态栏提示，不弹窗
                self.status_var.set("✓ 配置已保存")
                # 同时在翻译输出日志中显示
                if hasattr(self, 'translator_output'):
                    self.translator_output.insert(tk.END, "✓ 配置已保存\n")
                    self.translator_output.see(tk.END)
        except Exception as e:
            if not quiet:
                # 保存失败时在状态栏显示错误
                self.status_var.set(f"✗ 保存配置失败: {str(e)}")
                # 同时在翻译输出日志中显示
                if hasattr(self, 'translator_output'):
                    self.translator_output.insert(tk.END, f"✗ 保存配置失败: {str(e)}\n")
                    self.translator_output.see(tk.END)
            else:
                safe_file_log(f"save_config error: {e}")
    
    def on_closing(self):
        """关闭窗口时的处理"""
        if self.is_running:
            if messagebox.askokcancel("确认", "有任务正在运行，确定要退出吗？"):
                self.stop_current_task()
                try:
                    if self.corrector_process is not None and self.corrector_process.is_alive():
                        self.corrector_process.terminate()
                        self.corrector_process.join(timeout=1)
                except Exception:
                    pass
                try:
                    if self.polisher_process is not None and self.polisher_process.is_alive():
                        self.polisher_process.terminate()
                        self.polisher_process.join(timeout=1)
                except Exception:
                    pass
                self.clean_temp_files()  # 确保关闭前清理临时文件
                self.root.destroy()
        else:
            self.clean_temp_files()  # 确保关闭前清理临时文件
            self.root.destroy()
    
    def toggle_password_visibility(self):
        """切换API密钥显示/隐藏"""
        current_state = self.show_password_var.get()
        self.show_password_var.set(not current_state)
        
        if not current_state:  # 显示密码
            self.api_key_entry.config(show="")
            self.toggle_password_btn.config(text="隐藏")
        else:  # 隐藏密码
            self.api_key_entry.config(show="*")
            self.toggle_password_btn.config(text="显示")
    
    def test_api_connection(self):
        """测试API连接"""
        import threading
        
        def test_in_background():
            self.test_api_btn.config(state="disabled", text="测试中...")
            self.api_status_label.config(text="正在测试...", foreground="orange")
            
            try:
                # 获取API配置
                api_endpoint = self.api_endpoint_var.get().strip()
                api_key = self.api_key_var.get().strip()
                model = self.model_var.get().strip()
                
                # 验证必要参数
                if not api_endpoint:
                    self._show_api_test_result("[ERROR] 失败", "API服务器地址不能为空", "red")
                    return
                
                if not api_key:
                    self._show_api_test_result("[ERROR] 失败", "API密钥不能为空", "red")
                    return
                
                if not model:
                    self._show_api_test_result("[ERROR] 失败", "模型名称不能为空", "red")
                    return
                
                # 添加详细的测试日志
                self.translator_output.insert(tk.END, f"[INFO] 开始测试API连接...\n")
                self.translator_output.insert(tk.END, f"[INFO] 服务器: {api_endpoint}\n")
                self.translator_output.insert(tk.END, f"[INFO] 模型: {model}\n")
                self.translator_output.insert(tk.END, f"[INFO] 密钥: {'*' * (len(api_key) - 4) + api_key[-4:] if len(api_key) > 4 else '****'}\n")
                self.translator_output.see(tk.END)
                
                # 发送测试请求
                import requests
                import json
                
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }
                
                test_data = {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": "Hello, this is a connection test."}
                    ],
                    "max_tokens": 10,
                    "temperature": 0.1
                }
                
                self.translator_output.insert(tk.END, f"[INFO] 发送测试请求...\n")
                self.translator_output.see(tk.END)
                
                response = requests.post(
                    api_endpoint,
                    headers=headers,
                    json=test_data,
                    timeout=30
                )
                
                # 分析响应
                if response.status_code == 200:
                    try:
                        result = response.json()
                        if 'choices' in result and len(result['choices']) > 0:
                            self._show_api_test_result("[OK] 成功", "API连接正常，模型响应正常", "green")
                            self.translator_output.insert(tk.END, f"[OK] API测试成功！\n")
                            self.translator_output.insert(tk.END, f"[INFO] 响应内容: {result['choices'][0].get('message', {}).get('content', '无内容')}\n")
                        else:
                            self._show_api_test_result("[WARN] 部分成功", "API连接成功但响应格式异常", "orange")
                            self.translator_output.insert(tk.END, f"[WARN] API连接成功但响应格式异常\n")
                            self.translator_output.insert(tk.END, f"[INFO] 原始响应: {result}\n")
                    except json.JSONDecodeError:
                        self._show_api_test_result("[WARN] 部分成功", "API连接成功但响应不是有效JSON", "orange")
                        self.translator_output.insert(tk.END, f"[WARN] API连接成功但响应格式错误\n")
                        self.translator_output.insert(tk.END, f"[INFO] 响应内容: {response.text[:200]}...\n")
                        
                elif response.status_code == 401:
                    self._show_api_test_result("[ERROR] 失败", "API密钥无效或已过期", "red")
                    self.translator_output.insert(tk.END, f"[ERROR] 认证失败 (401): API密钥无效\n")
                    self.translator_output.insert(tk.END, f"[INFO] 请检查API密钥是否正确\n")
                    
                elif response.status_code == 403:
                    self._show_api_test_result("[ERROR] 失败", "API密钥权限不足", "red")
                    self.translator_output.insert(tk.END, f"[ERROR] 权限不足 (403): API密钥权限不够\n")
                    self.translator_output.insert(tk.END, f"[INFO] 请检查API密钥是否有调用此模型的权限\n")
                    
                elif response.status_code == 404:
                    self._show_api_test_result("[ERROR] 失败", "API端点不存在或模型不存在", "red")
                    self.translator_output.insert(tk.END, f"[ERROR] 未找到 (404): API端点或模型不存在\n")
                    self.translator_output.insert(tk.END, f"[INFO] 请检查API服务器地址和模型名称是否正确\n")
                    
                elif response.status_code == 429:
                    self._show_api_test_result("[ERROR] 失败", "API调用频率超限", "red")
                    self.translator_output.insert(tk.END, f"[ERROR] 频率超限 (429): API调用过于频繁\n")
                    self.translator_output.insert(tk.END, f"[INFO] 请稍后重试或检查API配额\n")
                    
                elif response.status_code == 500:
                    self._show_api_test_result("[ERROR] 失败", "API服务器内部错误", "red")
                    self.translator_output.insert(tk.END, f"[ERROR] 服务器错误 (500): API服务器内部错误\n")
                    self.translator_output.insert(tk.END, f"[INFO] 请稍后重试或联系API服务商\n")
                    
                else:
                    self._show_api_test_result("[ERROR] 失败", f"HTTP错误 {response.status_code}", "red")
                    self.translator_output.insert(tk.END, f"[ERROR] HTTP错误 ({response.status_code})\n")
                    self.translator_output.insert(tk.END, f"[INFO] 错误详情: {response.text[:200]}...\n")
                
            except requests.exceptions.ConnectTimeout:
                self._show_api_test_result("[ERROR] 失败", "连接超时", "red")
                self.translator_output.insert(tk.END, f"[ERROR] 连接超时: 无法在30秒内连接到API服务器\n")
                self.translator_output.insert(tk.END, f"[INFO] 请检查网络连接和API服务器地址\n")
                
            except requests.exceptions.ConnectionError:
                self._show_api_test_result("[ERROR] 失败", "网络连接错误", "red")
                self.translator_output.insert(tk.END, f"[ERROR] 连接错误: 无法连接到API服务器\n")
                self.translator_output.insert(tk.END, f"[INFO] 请检查网络连接和防火墙设置\n")
                
            except requests.exceptions.SSLError:
                self._show_api_test_result("[ERROR] 失败", "SSL证书验证失败", "red")
                self.translator_output.insert(tk.END, f"[ERROR] SSL错误: 证书验证失败\n")
                self.translator_output.insert(tk.END, f"[INFO] 请检查API服务器的SSL证书\n")
                
            except Exception as e:
                self._show_api_test_result("[ERROR] 失败", f"未知错误: {str(e)}", "red")
                self.translator_output.insert(tk.END, f"[ERROR] 未知错误: {str(e)}\n")
                self.translator_output.insert(tk.END, f"[INFO] 请检查所有配置参数\n")
            
            finally:
                self.translator_output.insert(tk.END, f"{'='*50}\n")
                self.translator_output.see(tk.END)
                # 恢复按钮状态
                self.test_api_btn.config(state="normal", text="测试API连接")
        
        # 在后台线程中执行测试
        threading.Thread(target=test_in_background, daemon=True).start()
    
    def _show_api_test_result(self, status_text, message, color):
        """显示API测试结果"""
        self.api_status_label.config(text=f"{status_text}: {message}", foreground=color)
    
    def check_scripts_compatibility(self):
        """检查脚本的兼容性，尝试解决已知问题"""
        try:
            if getattr(sys, "frozen", False):
                return True
            if not os.path.exists("srt_checker.py"):
                messagebox.showwarning("警告", "找不到srt_checker.py脚本，校验功能可能无法正常工作")
                return False
                
            return True
        except Exception as e:
            safe_file_log(f"check_scripts_compatibility error: {e}")
            return False
    
    # 用户提示词相关方法
    def clear_user_prompt(self):
        """清空用户提示词"""
        self.user_prompt_text.delete(1.0, tk.END)
    
    def set_preset_1(self):
        """设置预设提示词1"""
        preset_data = self.presets.get(1, {})
        prompt = preset_data.get("content", 
            "请将AI翻译为人工智能，Machine Learning翻译为机器学习，Deep Learning翻译为深度学习。"
            "保留所有英文缩写如CPU、GPU、API、JSON等。"
            "技术术语优先使用业界通用的中文译名。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def set_preset_2(self):
        """设置预设提示词2"""
        preset_data = self.presets.get(2, {})
        prompt = preset_data.get("content",
            "对于日本人名请保持日式发音的中文音译，如田中、山田、佐藤等。"
            "对于韩国人名请保持韩式发音的中文音译，如金、朴、李等。"
            "非著名的欧美地名、人名请直接保留英文原文，不要强行翻译。"
            "确保同一人名在整个字幕中翻译一致。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def set_preset_3(self):
        """设置预设提示词3"""
        preset_data = self.presets.get(3, {})
        prompt = preset_data.get("content", 
            "请使用更加口语化、生活化的翻译风格，避免过于正式的书面语。"
            "对话要符合中文表达习惯，自然流畅。"
            "保持原文的语气和情感色彩。"
            "网络用语和俚语请翻译为对应的中文网络用语。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def set_preset_4(self):
        """设置预设提示词4"""
        preset_data = self.presets.get(4, {})
        prompt = preset_data.get("content",
            "对于影视作品名称、角色名称请保持一致性翻译。"
            "影视术语如导演、制片人、演员等使用标准中文术语。"
            "对于电影、电视剧、综艺节目的台词要符合观众的观看习惯。"
            "保持娱乐内容的轻松幽默感，不要过于严肃。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def set_preset_5(self):
        """设置预设提示词5"""
        preset_data = self.presets.get(5, {})
        prompt = preset_data.get("content",
            "使用正式的商务中文表达，避免过于口语化。"
            "商务术语如CEO、CFO、董事会等使用标准翻译。"
            "数字、金额、百分比等要准确翻译。"
            "保持专业性和严谨性，符合商务场合的表达习惯。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def set_preset_6(self):
        """设置预设提示词6"""
        preset_data = self.presets.get(6, {})
        prompt = preset_data.get("content",
            "使用规范的学术中文表达，保持严谨性。"
            "学术术语要使用标准的中文翻译。"
            "引用、参考文献、图表等要按中文学术规范翻译。"
            "保持客观性和准确性，避免主观色彩。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def set_preset_7(self):
        """设置预设提示词7"""
        preset_data = self.presets.get(7, {})
        prompt = preset_data.get("content",
            "使用自然流畅的中文日常对话表达。"
            "俚语、口头禅要翻译为对应的中文表达。"
            "保持对话的自然感和亲切感。"
            "年龄、性别、社会身份要体现在语言风格中。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def set_preset_8(self):
        """设置预设提示词8"""
        preset_data = self.presets.get(8, {})
        prompt = preset_data.get("content",
            "翻译时保持新闻播报的正式性和客观性。"
            "使用标准的新闻用语和表达方式。"
            "人名地名采用通用译名，数据要准确。"
            "保持新闻的严肃性和权威性。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def set_preset_9(self):
        """设置预设提示词9"""
        preset_data = self.presets.get(9, {})
        prompt = preset_data.get("content",
            "翻译医学健康类内容时保持专业准确。"
            "医学术语使用标准中文表达。"
            "药物名称、疾病名称使用通用译名。"
            "涉及健康建议时保持严谨客观的表述。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def set_preset_10(self):
        """设置预设提示词10"""
        preset_data = self.presets.get(10, {})
        prompt = preset_data.get("content",
            "翻译美食烹饪类内容时保持生动诱人的表达。"
            "食材名称使用中文常见名称。"
            "烹饪方法和步骤要清晰易懂。"
            "保持美食内容的诱人和温馨感。"
        )
        self.user_prompt_text.delete(1.0, tk.END)
        self.user_prompt_text.insert(1.0, prompt)
    
    def init_default_presets(self):
        """初始化默认预设内容"""
        # 翻译器预设
        self.presets = {
            1: {
                "name": "技术术语",
                "content": (
                    "请将AI翻译为人工智能，Machine Learning翻译为机器学习，Deep Learning翻译为深度学习。"
                    "保留所有英文缩写如CPU、GPU、API、JSON等。"
                    "技术术语优先使用业界通用的中文译名。"
                )
            },
            2: {
                "name": "人名地名",
                "content": (
                    "对于日本人名请保持日式发音的中文音译，如田中、山田、佐藤等。"
                    "对于韩国人名请保持韩式发音的中文音译，如金、朴、李等。"
                    "非著名的欧美地名、人名请直接保留英文原文，不要强行翻译。"
                    "确保同一人名在整个字幕中翻译一致。"
                )
            },
            3: {
                "name": "语言风格",
                "content": (
                    "请使用更加口语化、生活化的翻译风格，避免过于正式的书面语。"
                    "对话要符合中文表达习惯，自然流畅。"
                    "保持原文的语气和情感色彩。"
                    "网络用语和俚语请翻译为对应的中文网络用语。"
                )
            },
            4: {
                "name": "影视娱乐",
                "content": (
                    "对于影视作品名称、角色名称请保持一致性翻译。"
                    "影视术语如导演、制片人、演员等使用标准中文术语。"
                    "对于电影、电视剧、综艺节目的台词要符合观众的观看习惯。"
                    "保持娱乐内容的轻松幽默感，不要过于严肃。"
                )
            },
            5: {
                "name": "商务用语",
                "content": (
                    "使用正式的商务中文表达，避免过于口语化。"
                    "商务术语如CEO、CFO、董事会等使用标准翻译。"
                    "数字、金额、百分比等要准确翻译。"
                    "保持专业性和严谨性，符合商务场合的表达习惯。"
                )
            },
            6: {
                "name": "学术文献",
                "content": (
                    "使用规范的学术中文表达，保持严谨性。"
                    "学术术语要使用标准的中文翻译。"
                    "引用、参考文献、图表等要按中文学术规范翻译。"
                    "保持客观性和准确性，避免主观色彩。"
                )
            },
            7: {
                "name": "日常对话",
                "content": (
                    "使用自然流畅的中文日常对话表达。"
                    "俚语、口头禅要翻译为对应的中文表达。"
                    "保持对话的自然感和亲切感。"
                    "年龄、性别、社会身份要体现在语言风格中。"
                )
            },
            8: {
                "name": "新闻播报",
                "content": (
                    "翻译时保持新闻播报的正式性和客观性。"
                    "使用标准的新闻用语和表达方式。"
                    "人名地名采用通用译名，数据要准确。"
                    "保持新闻的严肃性和权威性。"
                )
            },
            9: {
                "name": "医学健康",
                "content": (
                    "翻译医学健康类内容时保持专业准确。"
                    "医学术语使用标准中文表达。"
                    "药物名称、疾病名称使用通用译名。"
                    "涉及健康建议时保持严谨客观的表述。"
                )
            },
            10: {
                "name": "美食烹饪",
                "content": (
                    "翻译美食烹饪类内容时保持生动诱人的表达。"
                    "食材名称使用中文常见名称。"
                    "烹饪方法和步骤要清晰易懂。"
                    "保持美食内容的诱人和温馨感。"
                )
            }
        }
    
    def edit_presets(self):
        """编辑预设提示词"""
        # 创建编辑窗口
        edit_window = tk.Toplevel(self.root)
        edit_window.title("编辑预设提示词")
        edit_window.geometry("600x600")
        edit_window.transient(self.root)
        edit_window.grab_set()
        
        # 预设选择
        select_frame = ttk.Frame(edit_window)
        select_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(select_frame, text="选择预设:").pack(side=tk.LEFT)
        preset_var = tk.StringVar()
        # 创建显示文本列表：预设1 (技术术语)
        preset_options = [f"预设{i} ({self.presets.get(i, {}).get('name', '未命名')})" for i in sorted(self.presets.keys())]
        preset_combo = ttk.Combobox(select_frame, textvariable=preset_var, 
                                   values=preset_options, state="readonly")
        preset_combo.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)
        
        # 预设名称编辑
        name_frame = ttk.Frame(edit_window)
        name_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        ttk.Label(name_frame, text="预设名称:").pack(side=tk.LEFT)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(name_frame, textvariable=name_var)
        name_entry.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)
        
        # 编辑区域
        edit_frame = ttk.LabelFrame(edit_window, text="编辑内容", padding=(10, 10))
        edit_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        text_edit = scrolledtext.ScrolledText(edit_frame, wrap=tk.WORD, height=12)
        text_edit.pack(fill=tk.BOTH, expand=True)
        
        # 按钮区域
        button_frame = ttk.Frame(edit_window)
        button_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        def get_preset_id():
            """从选择的文本中提取预设ID"""
            selected_text = preset_var.get()
            if selected_text and selected_text.startswith("预设"):
                try:
                    return int(selected_text.split(" ")[0][2:])  # 提取"预设1"中的"1"
                except:
                    return None
            return None
        
        def load_preset():
            """加载选中的预设到编辑器"""
            preset_id = get_preset_id()
            if preset_id and preset_id in self.presets:
                preset_data = self.presets[preset_id]
                name_var.set(preset_data.get("name", ""))
                text_edit.delete(1.0, tk.END)
                text_edit.insert(1.0, preset_data.get("content", ""))
        
        def save_preset():
            """保存编辑的预设（直接保存到配置文件）"""
            preset_id = get_preset_id()
            if preset_id:
                name = name_var.get().strip()
                content = text_edit.get(1.0, tk.END).strip()
                
                if not name:
                    messagebox.showerror("错误", "预设名称不能为空")
                    return
                
                if not content:
                    messagebox.showerror("错误", "预设内容不能为空")
                    return
                
                # 更新预设
                self.presets[preset_id] = {
                    "name": name,
                    "content": content
                }
                
                # 更新下拉列表
                new_options = [f"预设{i} ({self.presets.get(i, {}).get('name', '未命名')})" for i in sorted(self.presets.keys())]
                preset_combo['values'] = new_options
                
                # 保持当前选择
                current_selection = f"预设{preset_id} ({name})"
                preset_var.set(current_selection)
                
                # 直接保存到配置文件（静默保存，不弹窗）
                try:
                    self.save_config(quiet=True)
                    # 在对话框内显示保存成功提示
                    save_status_label.config(text=f"✓ 预设{preset_id}已保存", foreground="green")
                except Exception as e:
                    save_status_label.config(text=f"✗ 保存失败: {str(e)}", foreground="red")
        
        def close_window():
            """关闭窗口"""
            edit_window.destroy()
        
        # 绑定预设选择事件
        preset_combo.bind("<<ComboboxSelected>>", lambda e: load_preset())
        
        ttk.Button(button_frame, text="保存", command=save_preset).pack(side=tk.LEFT, padx=(0, 5))
        save_status_label = ttk.Label(button_frame, text="", foreground="gray")
        save_status_label.pack(side=tk.LEFT, padx=10)
        ttk.Button(button_frame, text="关闭", command=close_window).pack(side=tk.RIGHT)
        
        # 默认选择第一个预设
        if self.presets:
            preset_combo.current(0)
            load_preset()

    def create_corrector_widgets(self):
        """创建字幕纠错器界面"""
        # 主容器
        main_frame = ttk.Frame(self.corrector_frame)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 左侧配置面板
        config_frame = ttk.Frame(main_frame)
        config_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        
        # 右侧输出面板
        output_frame = ttk.Frame(main_frame)
        output_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        self.create_corrector_config(config_frame)
        self.create_corrector_output(output_frame)

    def create_corrector_config(self, parent):
        """创建字幕纠错器配置面板"""
        # 文件选择部分
        file_frame = ttk.LabelFrame(parent, text="文件设置", padding=(5, 5, 5, 5))
        file_frame.pack(fill=tk.X, pady=(0, 5))
        
        # 输入文件
        input_frame = ttk.Frame(file_frame)
        input_frame.pack(fill=tk.X, pady=(2, 2))
        ttk.Label(input_frame, text="输入SRT文件:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.corrector_input_file_var = tk.StringVar()
        self.corrector_input_file_var.trace('w', self.on_corrector_input_file_change)
        self.corrector_input_entry = ttk.Entry(input_frame, textvariable=self.corrector_input_file_var)
        self.corrector_input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(input_frame, text="浏览", command=self.browse_corrector_input_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # 输出文件
        output_frame = ttk.Frame(file_frame)
        output_frame.pack(fill=tk.X, pady=2)
        ttk.Label(output_frame, text="输出SRT文件:", width=12).pack(side=tk.LEFT, padx=(0, 5))
        
        self.corrector_output_file_var = tk.StringVar()
        self.corrector_output_entry = ttk.Entry(output_frame, textvariable=self.corrector_output_file_var)
        self.corrector_output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(output_frame, text="浏览", command=self.browse_corrector_output_file, width=6).pack(side=tk.RIGHT, padx=(2, 0))
        
        # API设置提示 - 使用翻译器的配置
        api_info_frame = ttk.LabelFrame(parent, text="API设置", padding=(5, 5, 5, 5))
        api_info_frame.pack(fill=tk.X, pady=5)
        
        info_label = ttk.Label(api_info_frame, text="将使用翻译器标签页中的API配置\n(API服务器、密钥、模型名称)", 
                              foreground="#2d6a4f", anchor=tk.W, justify=tk.LEFT)
        info_label.pack(fill=tk.X, pady=2)
        
        # 初始化纠错器预设（确保在按钮创建前已初始化）
        # 注意：这里只是临时初始化，实际值会在load_config()中被覆盖
        if not hasattr(self, 'corrector_presets'):
            self.corrector_presets = {
                1: {
                    "name": "标准纠错",
                    "content": (
                        "修正常见的错别字、同音字错误，如'的得'、'在再'、'做作'等。"
                        "保持原文的语言风格和表达习惯，不要改变原意。"
                    )
                },
                2: {
                    "name": "保守纠错", 
                    "content": (
                        "只修正明显的错别字，对于可能是专有名词或技术术语的内容保持谨慎，"
                        "确实不确定的词汇保持原样。注重保持原文的语言风格。"
                    )
                },
                3: {
                    "name": "口语化纠错",
                    "content": (
                        "注意修正口语化表达中的语法错误，将不规范的口语表达调整为更标准的书面语，"
                        "但保持自然的表达方式，不要过于正式。"
                    )
                },
                4: {
                    "name": "技术内容纠错",
                    "content": (
                        "对于技术术语和专业词汇格外小心，优先保持原样。"
                        "重点修正语音识别导致的技术词汇错误，确保技术表达的准确性。"
                    )
                },
                5: {
                    "name": "标点符号纠错",
                    "content": (
                        "除了修正错别字外，也要注意标点符号的使用，"
                        "修正明显的标点错误，确保句子结构清晰。"
                    )
                }
            }
        
        # 用户提示词部分
        prompt_frame = ttk.LabelFrame(parent, text="纠错提示词", padding=(5, 5, 5, 5))
        prompt_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(prompt_frame, text="用自然语言告诉AI如何进行纠错:").pack(anchor=tk.W, pady=(0, 2))
        
        self.corrector_user_prompt_text = scrolledtext.ScrolledText(prompt_frame, height=5, wrap=tk.WORD)
        self.corrector_user_prompt_text.pack(fill=tk.X, expand=True, pady=(0, 5))
        
        # 功能按钮行（独立）
        function_row = ttk.Frame(prompt_frame)
        function_row.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Button(function_row, text="清空", command=self.clear_corrector_user_prompt).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(function_row, text="保存提示词", command=self.save_corrector_user_prompt).pack(side=tk.LEFT, padx=(0, 5))
        
        # 创建流动布局容器用于预设按钮
        self.corrector_preset_flow = FlowLayout(prompt_frame)
        self.corrector_preset_flow.pack(fill=tk.X, expand=True, pady=(0, 5))
        
        # 创建可编辑的预设按钮
        self.corrector_preset_buttons = {}
        self.corrector_preset_tooltips = {}
        for i in range(1, 9):  # 1-8个预设
            # 处理超出预设数量的情况，初始化默认预设
            if i not in self.corrector_presets:
                default_names = {
                    6: "商务纠错", 7: "学术纠错", 8: "娱乐纠错"
                }
                default_prompts = {
                    6: "请修正语音识别错误，保持商务用语的正式性和准确性。",
                    7: "请修正语音识别错误，保持学术术语的专业性和严谨性。",
                    8: "请修正语音识别错误，保持娱乐内容的生动性和口语化特点。"
                }
                self.corrector_presets[i] = {
                    "name": default_names.get(i, f"预设{i}"),
                    "content": default_prompts.get(i, f"预设提示词{i}")
                }
            
            preset_name = self.corrector_presets[i]["name"]
            # 截断显示名称，但保留完整名称用于tooltip
            display_name = truncate_text(preset_name, 10)
            
            btn = ttk.Button(self.corrector_preset_flow, text=display_name,
                           command=lambda idx=i: self.load_corrector_preset(idx))
            
            # 添加到流动布局
            self.corrector_preset_flow.add_widget(btn, padx=3, pady=2)
            
            # 添加tooltip显示完整名称
            if len(preset_name) > 10:
                self.corrector_preset_tooltips[i] = ToolTip(btn, preset_name)
            
            # 添加右键菜单编辑功能
            self.add_corrector_preset_context_menu(btn, i)
            self.corrector_preset_buttons[i] = btn
        
        # 纠错参数部分
        params_frame = ttk.LabelFrame(parent, text="纠错参数", padding=(5, 5, 5, 5))
        params_frame.pack(fill=tk.X, pady=5)
        
        # 创建一个网格布局容器
        params_grid = ttk.Frame(params_frame)
        params_grid.pack(fill=tk.X, pady=2)
        
        # 批次大小
        ttk.Label(params_grid, text="批次大小:", width=12).grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.corrector_batch_size_var = tk.StringVar(value="5")
        self.corrector_batch_size_var.trace('w', self.on_corrector_option_change)
        self.corrector_batch_size_spin = ttk.Spinbox(params_grid, from_=1, to=20, textvariable=self.corrector_batch_size_var, width=5)
        self.corrector_batch_size_spin.grid(row=0, column=1, sticky=tk.W)
        ToolTip(self.corrector_batch_size_spin, "每次发送给AI的字幕条目数量。推荐5-10条")
        
        # 线程数
        ttk.Label(params_grid, text="线程数:", width=12).grid(row=0, column=2, sticky=tk.W, padx=(10, 5))
        self.corrector_threads_var = tk.StringVar(value="3")
        self.corrector_threads_var.trace('w', self.on_corrector_option_change)
        self.corrector_threads_spin = ttk.Spinbox(params_grid, from_=1, to=10, textvariable=self.corrector_threads_var, width=5)
        self.corrector_threads_spin.grid(row=0, column=3, sticky=tk.W)
        ToolTip(self.corrector_threads_spin, "并发线程数量。推荐3-5个")
        
        # 温度
        ttk.Label(params_grid, text="温度:", width=12).grid(row=1, column=0, sticky=tk.W, padx=(0, 5))
        self.corrector_temperature_var = tk.StringVar(value="0.3")
        self.corrector_temperature_var.trace('w', self.on_corrector_option_change)
        self.corrector_temperature_spin = ttk.Spinbox(params_grid, from_=0.0, to=1.0, increment=0.1, 
                                                     textvariable=self.corrector_temperature_var, width=5)
        self.corrector_temperature_spin.grid(row=1, column=1, sticky=tk.W)
        ToolTip(self.corrector_temperature_spin, "0.1-0.2极保守；0.3推荐；0.4稍灵活")
        
        # 上下文窗口
        ttk.Label(params_grid, text="上下文窗口:", width=12).grid(row=1, column=2, sticky=tk.W, padx=(10, 5))
        self.corrector_context_window_var = tk.StringVar(value="2")
        self.corrector_context_window_var.trace('w', self.on_corrector_option_change)
        self.corrector_context_window_spin = ttk.Spinbox(params_grid, from_=0, to=5, 
                                                        textvariable=self.corrector_context_window_var, width=5)
        self.corrector_context_window_spin.grid(row=1, column=3, sticky=tk.W)
        ToolTip(self.corrector_context_window_spin, "参考前后多少条字幕。0=无上下文，2=前后各2条")
        
        # 批量模式选择
        ttk.Label(params_grid, text="批量模式:", width=12).grid(row=2, column=0, sticky=tk.W, padx=(0, 5))
        self.corrector_batch_mode_var = tk.StringVar(value="逐条处理")
        self.corrector_batch_mode_var.trace('w', self.on_corrector_option_change)
        self.corrector_batch_mode_combo = ttk.Combobox(params_grid, textvariable=self.corrector_batch_mode_var, 
                                                      values=["逐条处理", "真批量"], width=10, state="readonly")
        self.corrector_batch_mode_combo.grid(row=2, column=1, sticky=tk.W)
        ToolTip(self.corrector_batch_mode_combo, "逐条=准确稳定；真批量=快速高效")
        
        # 接口超时
        ttk.Label(params_grid, text="接口超时(秒):", width=12).grid(row=2, column=2, sticky=tk.W, padx=(10, 5))
        self.corrector_timeout_var = tk.StringVar(value="180")
        self.corrector_timeout_var.trace('w', self.on_corrector_option_change)
        self.corrector_timeout_spin = ttk.Spinbox(params_grid, from_=30, to=600, increment=10,
                                                 textvariable=self.corrector_timeout_var, width=6)
        self.corrector_timeout_spin.grid(row=2, column=3, sticky=tk.W)
        ToolTip(self.corrector_timeout_spin, "等待API响应的最长秒数，超时后重试")
        
        # 添加说明
        batch_help = ttk.Label(params_grid, text="逐条=准确稳定 真批量=快速高效(支持上下文)", 
                              foreground="gray", font=('Arial', 8))
        batch_help.grid(row=3, column=0, columnspan=4, sticky=tk.W, padx=(0, 0))
        
        
        # 格式规范化选项部分
        format_frame = ttk.LabelFrame(parent, text="格式规范化选项", padding=(5, 5, 5, 5))
        format_frame.pack(fill=tk.X, pady=5)
        
        # 说明文字
        info_text = ttk.Label(format_frame, text="🔧 编程处理   🤖 需要AI能力", 
                             foreground="gray", font=('Arial', 8))
        info_text.pack(anchor=tk.W, pady=(0, 5))
        
        # 第一行选项
        format_row1 = ttk.Frame(format_frame)
        format_row1.pack(fill=tk.X, pady=2)
        
        self.clean_newlines_var = tk.BooleanVar(value=True)
        self.clean_newlines_var.trace('w', self.on_format_option_change)
        clean_newlines_chk = ttk.Checkbutton(format_row1, text="🔧 清理换行和合并短行", variable=self.clean_newlines_var)
        clean_newlines_chk.pack(side=tk.LEFT, padx=(0, 15))
        ToolTip(clean_newlines_chk, "完全去除所有换行符，将多行字幕合并为单行")
        
        self.remove_spaces_var = tk.BooleanVar(value=True)
        self.remove_spaces_var.trace('w', self.on_format_option_change)
        remove_spaces_chk = ttk.Checkbutton(format_row1, text="🔧 移除多余空格", variable=self.remove_spaces_var)
        remove_spaces_chk.pack(side=tk.LEFT, padx=(0, 15))
        ToolTip(remove_spaces_chk, "清理行首行尾空白，折叠连续空白；可移除中文字符之间的空格（保留英文单词间空格）")
        
        # 第二行选项
        format_row2 = ttk.Frame(format_frame)
        format_row2.pack(fill=tk.X, pady=2)
        
        
        self.normalize_punctuation_var = tk.BooleanVar(value=True)
        self.normalize_punctuation_var.trace('w', self.on_format_option_change)
        normalize_punctuation_chk = ttk.Checkbutton(format_row2, text="🔧 统一标点格式", variable=self.normalize_punctuation_var)
        normalize_punctuation_chk.pack(side=tk.LEFT, padx=(0, 15))
        ToolTip(normalize_punctuation_chk, "移除标点前空格，确保标点后有合适空格")
        
        self.smart_line_break_var = tk.BooleanVar(value=True)
        self.smart_line_break_var.trace('w', self.on_format_option_change)
        smart_line_break_chk = ttk.Checkbutton(format_row2, text="🔧 智能换行显示", variable=self.smart_line_break_var)
        smart_line_break_chk.pack(side=tk.LEFT, padx=(0, 15))
        ToolTip(smart_line_break_chk, "对过长的单行字幕进行智能断行")
        
        # 第三行选项（AI处理）
        format_row3 = ttk.Frame(format_frame)
        format_row3.pack(fill=tk.X, pady=2)
        
        self.smart_spacing_var = tk.BooleanVar(value=True)
        self.smart_spacing_var.trace('w', self.on_format_option_change)
        smart_spacing_chk = ttk.Checkbutton(format_row3, text="🤖 智能添加空格", variable=self.smart_spacing_var)
        smart_spacing_chk.pack(side=tk.LEFT, padx=(0, 15))
        ToolTip(smart_spacing_chk, "AI在中英文间、数字与文字间添加空格")
        
        self.smart_punctuation_var = tk.BooleanVar(value=True)
        self.smart_punctuation_var.trace('w', self.on_format_option_change)
        smart_punctuation_chk = ttk.Checkbutton(format_row3, text="🤖 智能标点优化", variable=self.smart_punctuation_var)
        smart_punctuation_chk.pack(side=tk.LEFT, padx=(0, 15))
        ToolTip(smart_punctuation_chk, "AI优化标点符号，添加缺失标点")
        
        # 第四行选项（AI处理）
        format_row4 = ttk.Frame(format_frame)
        format_row4.pack(fill=tk.X, pady=2)
        
        self.fluency_optimization_var = tk.BooleanVar(value=True)
        self.fluency_optimization_var.trace('w', self.on_format_option_change)
        fluency_optimization_chk = ttk.Checkbutton(format_row4, text="🤖 语法流畅度优化", variable=self.fluency_optimization_var)
        fluency_optimization_chk.pack(side=tk.LEFT, padx=(0, 15))
        ToolTip(fluency_optimization_chk, "AI调整语法使表达更流畅，保守用户建议关闭")
        
        # 操作按钮
        button_frame = ttk.Frame(parent)
        button_frame.pack(fill=tk.X, pady=(20, 0))
        
        self.corrector_start_button = ttk.Button(button_frame, text="开始纠错", command=self.start_correction)
        self.corrector_start_button.pack(side=tk.LEFT, padx=(0, 10))
        
        self.corrector_stop_button = ttk.Button(button_frame, text="停止", command=self.stop_correction, state=tk.DISABLED)
        self.corrector_stop_button.pack(side=tk.LEFT, padx=(0, 10))
        
        # 保存纠错器设置按钮
        self.corrector_save_button = ttk.Button(button_frame, text="保存设置", command=self.save_corrector_config)
        self.corrector_save_button.pack(side=tk.LEFT, padx=(0, 10))

    def create_corrector_output(self, parent):
        """创建字幕纠错器输出面板"""
        # 输出文本区域
        output_frame = ttk.LabelFrame(parent, text="输出信息", padding=(5, 5, 5, 5))
        output_frame.pack(fill=tk.BOTH, expand=True)
        
        self.corrector_output_text = ColoredLogWidget(output_frame, height=16, dark_mode=True)
        self.corrector_output_text.pack(fill=tk.BOTH, expand=True)
        
        # 清空按钮
        clear_frame = ttk.Frame(output_frame)
        clear_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(clear_frame, text="清空输出", command=lambda: self.corrector_output_text.delete(1.0, tk.END)).pack(side=tk.RIGHT)

    def browse_corrector_input_file(self):
        """浏览纠错器输入文件"""
        file_path = filedialog.askopenfilename(
            title="选择输入SRT文件",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if file_path:
            self.corrector_input_file_var.set(file_path)

    def browse_corrector_output_file(self):
        """浏览纠错器输出文件"""
        file_path = filedialog.asksaveasfilename(
            title="选择输出SRT文件",
            defaultextension=".srt",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if file_path:
            self.corrector_output_file_var.set(file_path)

    def on_corrector_input_file_change(self, *args):
        """当纠错器输入文件改变时自动设置输出文件"""
        input_file = self.corrector_input_file_var.get()
        if input_file:
            # 自动生成输出文件名（总是更新，不管输出文件是否已有值）
            base_name = os.path.splitext(input_file)[0]
            output_file = f"{base_name}_corrected.srt"
            self.corrector_output_file_var.set(output_file)


    def start_correction(self):
        """开始字幕纠错"""
        # 验证输入
        input_file = self.corrector_input_file_var.get().strip()
        output_file = self.corrector_output_file_var.get().strip()
        
        # 从翻译器获取API参数
        api_key = self.api_key_var.get().strip()
        api_endpoint = self.api_endpoint_var.get().strip()
        model = self.model_var.get().strip()
        
        if not input_file:
            messagebox.showerror("错误", "请选择输入SRT文件")
            return
        
        if not os.path.exists(input_file):
            messagebox.showerror("错误", "输入文件不存在")
            return
        
        if not output_file:
            messagebox.showerror("错误", "请指定输出SRT文件")
            return
        
        if not api_key:
            messagebox.showerror("错误", "请先在翻译器标签页中配置API密钥")
            return
        
        if not api_endpoint:
            messagebox.showerror("错误", "请先在翻译器标签页中配置API服务器地址")
            return
        
        if not model:
            messagebox.showerror("错误", "请先在翻译器标签页中配置模型名称")
            return
        
        # 获取参数
        try:
            batch_size = int(self.corrector_batch_size_var.get())
            threads = int(self.corrector_threads_var.get())
            temperature = float(self.corrector_temperature_var.get())
            timeout_seconds = int(self.corrector_timeout_var.get())
        except ValueError:
            messagebox.showerror("错误", "参数格式错误")
            return
        
        # 获取格式化选项
        format_options = {
            'clean_newlines': self.clean_newlines_var.get(),
            'remove_spaces': self.remove_spaces_var.get(),
            'normalize_punctuation': self.normalize_punctuation_var.get(),
            'smart_line_break': self.smart_line_break_var.get(),
        }
        
        # 获取AI优化选项（影响AI提示词）
        ai_options = {
            'smart_spacing': self.smart_spacing_var.get(),
            'smart_punctuation': self.smart_punctuation_var.get(),
            'fluency_optimization': self.fluency_optimization_var.get(),
        }
        
        # 禁用开始按钮，启用停止按钮
        self.corrector_start_button.config(state=tk.DISABLED)
        self.corrector_stop_button.config(state=tk.NORMAL)
        self.is_running = True
        
        # 清空输出
        self.corrector_output_text.config(state=tk.NORMAL)
        self.corrector_output_text.delete(1.0, tk.END)
        self.corrector_output_text.config(state=tk.DISABLED)
        
        # 获取用户提示词
        user_prompt = self.corrector_user_prompt_text.get(1.0, tk.END).strip()
        
        # 获取生成报告选项
        generate_report = False  # GUI不再提供生成报告选项
        
        # 在新线程中运行纠错
        try:
            context_window = int(self.corrector_context_window_var.get())
        except ValueError:
            context_window = 2

        batch_mode = self.corrector_batch_mode_var.get()
        use_true_batch = (batch_mode == "真批量")

        try:
            self.progress_bar.stop()
        except Exception:
            pass
        self.progress_bar.configure(mode='determinate', maximum=100)
        self.progress_var.set(0)
        self.status_var.set("纠错进度: 0.0%")

        job = CorrectorJobConfig(
            input_file=input_file,
            output_file=output_file,
            api_key=api_key,
            api_endpoint=api_endpoint,
            model=model,
            batch_size=batch_size,
            threads=threads,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            user_prompt=user_prompt,
            format_options=format_options,
            ai_options=ai_options,
            context_window=context_window,
            use_true_batch=use_true_batch,
        )
        self._start_corrector_process(job)
        return

    def _run_correction(self, input_file, output_file, api_key, api_endpoint, model, batch_size, threads, temperature, timeout_seconds, user_prompt="", format_options=None, ai_options=None, generate_report=False):
        """在后台线程中运行字幕纠错"""
        try:
            # 初始化进度条
            self.progress_var.set(0)
            
            # 添加初始消息
            self._add_corrector_output("开始字幕纠错和优化...\n")
            self._add_corrector_output(f"输入文件: {input_file}\n")
            self._add_corrector_output(f"输出文件: {output_file}\n")
            self._add_corrector_output(f"批次大小: {batch_size}, 线程数: {threads}, 温度: {temperature}, 接口超时: {timeout_seconds}s\n")
            
            # 显示格式化选项
            if format_options:
                enabled_formats = [k for k, v in format_options.items() if v]
                if enabled_formats:
                    self._add_corrector_output(f"格式规范化: {', '.join(enabled_formats)}\n")
            
            # 显示AI优化选项
            if ai_options:
                enabled_ai = [k for k, v in ai_options.items() if v]
                if enabled_ai:
                    self._add_corrector_output(f"AI优化功能: {', '.join(enabled_ai)}\n")
            
            if user_prompt:
                self._add_corrector_output(f"用户提示词: {user_prompt[:50]}...\n" if len(user_prompt) > 50 else f"用户提示词: {user_prompt}\n")
            self._add_corrector_output("\n")
            
            # 创建API实例
            api = CorrectionAPI(
                api_type="custom",
                api_key=api_key,
                api_endpoint=api_endpoint,
                model=model,
                temperature=temperature,
                timeout_seconds=timeout_seconds
            )
            
            # 根据AI选项修改系统提示词
            if ai_options:
                ai_enhancements = []
                if not ai_options.get('smart_spacing', True):
                    ai_enhancements.append("不要添加中英文之间的空格")
                if not ai_options.get('smart_punctuation', True):
                    ai_enhancements.append("不要优化标点符号")
                if not ai_options.get('fluency_optimization', True):
                    ai_enhancements.append("不要优化语法流畅度")
                
                if ai_enhancements:
                    api.system_prompt = api.system_prompt + f"\n\n特别注意：{'; '.join(ai_enhancements)}。"
            
            # 如果有用户提示词，追加到系统提示词
            if user_prompt:
                api.system_prompt = api.system_prompt + f"\n\n用户额外要求：{user_prompt}"
            
            # 创建纠错器实例，传入格式化选项和输出回调
            def output_callback(message):
                if self.is_running:
                    self._add_corrector_output(f"{message}\n")

            # 将API日志透传到GUI输出
            api.log_callback = output_callback
            
            # 获取参数
            try:
                context_window = int(self.corrector_context_window_var.get())
            except ValueError:
                context_window = 2
            
            # 获取批量模式
            batch_mode = self.corrector_batch_mode_var.get()
            use_true_batch = (batch_mode == "真批量")
                
            corrector = SRTCorrector(api, batch_size, threads, format_options or {}, output_callback, context_window, use_true_batch)
            
            # 定义进度回调
            def progress_callback(progress, message):
                if self.is_running:
                    # 更新进度条（0-100）
                    progress_percent = progress * 100
                    self.progress_var.set(progress_percent)
                    
                    # 更新输出和状态栏
                    self._add_corrector_output(f"进度: {progress:.1%} - {message}\n")
                    self.status_var.set(f"纠错进度: {progress:.1%}")
            
            # 执行纠错
            success = corrector.correct_srt_file(input_file, output_file, progress_callback)
            
            if success and self.is_running:
                # 统计信息已经通过 _print_stats() 发送到GUI，这里只需要设置状态
                self.status_var.set("处理完成")
                # 完成时进度条设为100%
                self.progress_var.set(100)
                
                # 纠错完成后自动跳转到纠错审核标签页并填充文件路径
                self.output_queue.put(("auto_fill_review", (input_file, output_file)))
                
                # 如果启用了生成报告功能，生成对比报告
                if generate_report:
                    self._add_corrector_output("\n📊 正在生成对比报告...\n")
                    report_path = self._generate_comparison_report(input_file, output_file, corrector.stats)
                    if report_path:
                        self._add_corrector_output(f"✅ 对比报告已生成: {report_path}\n")
                        # 自动打开报告文件
                        try:
                            import webbrowser
                            webbrowser.open(f"file:///{report_path.replace(os.sep, '/')}")
                            self._add_corrector_output("🌐 已在浏览器中打开对比报告\n")
                        except Exception as e:
                            self._add_corrector_output(f"📂 请手动打开报告文件: {report_path}\n")
                    else:
                        self._add_corrector_output("⚠️ 生成对比报告失败\n")
            elif not self.is_running:
                self._add_corrector_output("\n纠错已停止\n")
                self.status_var.set("纠错已停止")
                # 停止时重置进度条
                self.progress_var.set(0)
            else:
                self._add_corrector_output("\n纠错失败，请查看日志文件了解详情\n")
                self.status_var.set("纠错失败")
                # 失败时重置进度条
                self.progress_var.set(0)
                
        except Exception as e:
            if self.is_running:
                self._add_corrector_output(f"\n纠错过程中出现错误: {str(e)}\n")
                self.status_var.set("纠错出错")
                # 出错时重置进度条
                self.progress_var.set(0)
        finally:
            # 恢复按钮状态
            self.root.after(0, self._restore_corrector_buttons)

    def _add_corrector_output(self, text):
        """添加输出文本到纠错器输出区域"""
        def update_text():
            self.corrector_output_text.insert_colored(text)
        
        self.root.after(0, update_text)

    def _restore_corrector_buttons(self):
        """恢复纠错器按钮状态"""
        self.corrector_start_button.config(state=tk.NORMAL)
        self.corrector_stop_button.config(state=tk.DISABLED)
        self.is_running = False

    def stop_correction(self):
        """停止字幕纠错"""
        self.is_running = False
        self._add_corrector_output("\n[WARN] 正在强制停止纠错...\n")
        self.status_var.set("正在停止纠错...")
        try:
            proc = getattr(self, "corrector_process", None)
            if proc is not None and proc.is_alive():
                proc.terminate()
                proc.join(timeout=2)
        except Exception:
            pass
        self.corrector_process = None
        try:
            self.progress_bar.stop()
        except Exception:
            pass
        self.progress_bar.configure(mode='determinate', maximum=100)
        self.progress_var.set(0)
        self.status_var.set("纠错已停止")
        self._restore_corrector_buttons()

    # ===== 字幕润色器相关方法 =====

    def browse_polisher_input_file(self):
        """浏览润色器输入文件"""
        file_path = filedialog.askopenfilename(
            title="选择要润色的SRT文件",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if file_path:
            self.polisher_input_file_var.set(file_path)
            # 自动生成输出文件名
            base_name = os.path.splitext(file_path)[0]
            output_file = f"{base_name}_polished.srt"
            self.polisher_output_file_var.set(output_file)

    def browse_polisher_output_file(self):
        """浏览润色器输出文件"""
        file_path = filedialog.asksaveasfilename(
            title="选择润色后的SRT文件保存位置",
            defaultextension=".srt",
            filetypes=[("SRT files", "*.srt"), ("All files", "*.*")]
        )
        if file_path:
            self.polisher_output_file_var.set(file_path)

    def start_polisher(self):
        """开始字幕润色"""
        # 验证输入
        input_file = self.polisher_input_file_var.get().strip()
        output_file = self.polisher_output_file_var.get().strip()

        # 从翻译器获取API参数 - 真正的继承！
        api_key = self.api_key_var.get().strip()
        api_endpoint = self.api_endpoint_var.get().strip()
        model = self.model_var.get().strip()
        api_type = self.api_type_var.get()

        # 验证必要参数
        if not input_file:
            messagebox.showerror("参数错误", "请选择输入SRT文件")
            return

        if not output_file:
            messagebox.showerror("参数错误", "请选择输出SRT文件")
            return

        if not api_key:
            messagebox.showerror("参数错误", "请在翻译器标签页中设置API密钥")
            return

        if not os.path.exists(input_file):
            messagebox.showerror("文件错误", f"输入文件不存在: {input_file}")
            return

        # 验证API配置完整性
        if api_type == "custom" and not api_endpoint:
            messagebox.showerror("参数错误", "使用自定义API时，请在翻译器标签页设置API端点")
            return

        # 获取润色参数
        try:
            batch_size = int(self.polisher_batch_size_var.get())
            context_size = int(self.polisher_context_size_var.get())
            threads = int(self.polisher_threads_var.get())
            temperature = float(self.polisher_temperature_var.get())
        except ValueError as e:
            messagebox.showerror("参数错误", f"参数格式错误: {str(e)}")
            return

        # 获取翻译器当前的用户提示词
        # user_prompt = self.get_current_translator_prompt()  # 不再使用翻译器的用户提示词
        user_prompt = ""  # 润色器使用独立的提示词策略

        # 更新UI状态
        self.polisher_start_button.config(state=tk.DISABLED)
        self.polisher_stop_button.config(state=tk.NORMAL)
        self.is_running = True
        self.status_var.set("正在润色字幕...")

        # 清空输出
        self.polisher_output.delete(1.0, tk.END)
        self._add_polisher_output(f"[INFO] 开始润色字幕文件: {input_file}\n")
        self._add_polisher_output(f"[INFO] 输出文件: {output_file}\n")
        self._add_polisher_output(f"[INFO] API类型: {api_type}, 模型: {model or '默认'}\n")
        self._add_polisher_output(f"[INFO] 批次大小: {batch_size}, 上下文: {context_size}, 线程: {threads}, 温度: {temperature}\n")
        self._add_polisher_output(f"[INFO] 字数分配方案: {self.polisher_length_policy_var.get()}\n")
        if user_prompt:
            prompt_preview = user_prompt[:50] + "..." if len(user_prompt) > 50 else user_prompt
            # 改为完整输出用户提示词
            self._add_polisher_output(f"[INFO] 使用提示词(完整): {user_prompt}\n\n")
        else:
            self._add_polisher_output(f"[INFO] 使用默认润色提示词\n\n")

        # 启动润色线程
        def polisher_thread():
            try:
                # 导入润色器模块
                import srt_polisher
                from srt_polisher import SRTPolisher

                # 初始化进度条
                self.progress_var.set(0)
                
                # 解析输入文件获取总条目数，用于计算进度
                try:
                    with open(input_file, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    # 简单计算字幕条目数（匹配时间轴行）
                    import re
                    total_entries = len(re.findall(r'\d{2}:\d{2}:\d{2},\d{3}\s*-->', content))
                    total_batches = (total_entries + batch_size - 1) // batch_size if total_entries > 0 else 1
                except Exception:
                    total_batches = 1
                
                completed_batches = [0]  # 使用列表以便在闭包中修改
                
                # 创建带进度追踪的日志回调
                def polisher_log_callback(text):
                    if self.is_running:
                        self._add_polisher_output(text)
                        # 解析日志中的批次完成信息并更新进度
                        if '润色批次' in text and ('已将' in text or '写入' in text):
                            completed_batches[0] += 1
                            progress = min(completed_batches[0] / total_batches * 100, 99)
                            self.progress_var.set(progress)
                            self.status_var.set(f"润色进度: {progress:.0f}%")

                # 将SRT-Polisher日志导入GUI输出：附加一个Tk处理器
                try:
                    polisher_logger = logging.getLogger("SRT-Polisher")
                    # 先移除控制台输出（如果存在）
                    for h in list(polisher_logger.handlers):
                        import logging as _lg
                        if isinstance(h, _lg.StreamHandler) and not isinstance(h, logging.FileHandler):
                            polisher_logger.removeHandler(h)
                    # 添加GUI日志处理器（去重添加）
                    if not any(isinstance(h, TkTextLogHandler) for h in polisher_logger.handlers):
                        gui_handler = TkTextLogHandler(self.root, polisher_log_callback)
                        gui_handler.setLevel(logging.INFO)
                        gui_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
                        polisher_logger.addHandler(gui_handler)
                    # 禁止向root传播，彻底阻断控制台重复输出
                    polisher_logger.propagate = False
                except Exception:
                    pass

                # 创建润色器实例 - 直接传递API端点！
                polisher = SRTPolisher(
                    api_type=api_type,
                    api_key=api_key,
                    batch_size=batch_size,
                    context_size=context_size,
                    max_workers=threads,
                    model_name=model if model else None,
                    temperature=temperature,
                    user_prompt=user_prompt,  # 传递翻译器的用户提示词
                    api_endpoint=api_endpoint if api_type == "custom" else None,  # 直接传递API端点
                    length_policy=self.polisher_length_policy_var.get(),
                    fallback_on_timecode=False,
                    corner_quotes=getattr(self, 'polisher_corner_quotes_var', tk.BooleanVar(value=False)).get()
                )

                # 获取选项
                resume = self.polisher_resume_var.get()
                auto_verify = self.polisher_auto_verify_var.get()

                # 开始润色
                success = polisher.polish_srt_file(
                    input_file=input_file,
                    output_file=output_file,
                    resume=resume,
                    auto_verify=auto_verify
                )

                if success:
                    # 记录最后一次润色的输出文件
                    self.last_polished_file = output_file
                    # 进度条设为100%
                    self.progress_var.set(100)
                    self.status_var.set("润色完成")
                    self.root.after(0, lambda: self._add_polisher_output(f"\n[OK] 润色完成！输出文件: {output_file}\n"))
                    if auto_verify:
                        try:
                            summary = getattr(polisher, 'last_verify_summary', None)
                            if summary is None:
                                # 兼容旧逻辑：仅使用布尔值
                                perfect = getattr(polisher, 'last_verify_perfect', None)
                                if perfect is True:
                                    self.root.after(0, lambda: self._add_polisher_output(f"[OK] 校验通过\n"))
                                elif perfect is False:
                                    import os as _os
                                    report_path = f"{_os.path.splitext(output_file)[0]}_polish_report.md"
                                    self.root.after(0, lambda: self._add_polisher_output(f"[WARN] 校验发现不匹配，报告: {report_path}\n"))
                                else:
                                    self.root.after(0, lambda: self._add_polisher_output(f"[WARN] 未执行校验或结果未知，请查看日志\n"))
                            else:
                                if summary.get('perfect'):
                                    msg = (
                                        f"[OK] 校验通过：共{summary.get('total_translated')}/{summary.get('total_source')}条，"
                                        f"时间码不匹配0条。\n"
                                    )
                                    self.root.after(0, lambda: self._add_polisher_output(msg))
                                    # 如检测到模型在输出中夹带“字幕#”提示，给出额外提示
                                    try:
                                        if isinstance(summary, dict):
                                            self.root.after(0, lambda: self._add_polisher_output("[INFO] 内容提示：如发现模型输出带有‘字幕#N …’前缀，系统会自动清理。如仍遇到异常，请使用‘一键修复并重润色缺失批次’。\n"))
                                    except Exception:
                                        pass
                                else:
                                    msg = (
                                        f"[WARN] 校验发现不匹配：源={summary.get('total_source')}, 润色={summary.get('total_translated')}, "
                                        f"时间码不匹配={summary.get('time_mismatch_count')}, 缺少={summary.get('missing_count')}, 多余={summary.get('extra_count')}。\n"
                                    )
                                    self.root.after(0, lambda: self._add_polisher_output(msg))
                        except Exception as _e:
                            self.root.after(0, lambda: self._add_polisher_output(f"[ERROR] 自动校验结果处理出错: {str(_e)}\n"))

                    # 润色完成后，自动填充审核文件并跳转到审核标签页
                    try:
                        self.output_queue.put(("auto_fill_review", (input_file, output_file)))
                    except Exception:
                        pass
                else:
                    self.root.after(0, lambda: self._add_polisher_output(f"\n[ERROR] 润色失败，请查看详细日志\n"))

            except ImportError as e:
                error_msg = str(e)
                self.root.after(0, lambda: self._add_polisher_output(f"\n[ERROR] 无法导入润色器模块: {error_msg}\n"))
            except Exception as e:
                error_msg = str(e)
                self.root.after(0, lambda: self._add_polisher_output(f"\n[ERROR] 润色过程出错: {error_msg}\n"))
            finally:
                # 恢复UI状态
                self.root.after(0, self._restore_polisher_ui)

        # 启动线程
        resume = self.polisher_resume_var.get()
        auto_verify = self.polisher_auto_verify_var.get()
        self._polisher_total_batches = None
        self._polisher_done_batches = set()

        try:
            self.progress_bar.stop()
        except Exception:
            pass
        self.progress_bar.configure(mode='determinate', maximum=100)
        self.progress_var.set(0)
        self.status_var.set("润色进度: 0%")

        job = PolisherJobConfig(
            input_file=input_file,
            output_file=output_file,
            api_type=api_type,
            api_key=api_key,
            api_endpoint=api_endpoint,
            model=model,
            batch_size=batch_size,
            context_size=context_size,
            threads=threads,
            temperature=temperature,
            user_prompt=user_prompt,
            length_policy=self.polisher_length_policy_var.get(),
            corner_quotes=getattr(self, 'polisher_corner_quotes_var', tk.BooleanVar(value=False)).get(),
            resume=resume,
            auto_verify=auto_verify,
        )
        self._start_polisher_process(job)
        return

    def stop_polisher(self):
        """停止字幕润色"""
        self.is_running = False
        self._add_polisher_output("\n[WARN] 正在强制停止润色...\n")
        self.status_var.set("正在停止润色...")
        try:
            proc = getattr(self, "polisher_process", None)
            if proc is not None and proc.is_alive():
                proc.terminate()
                proc.join(timeout=2)
        except Exception:
            pass
        self.polisher_process = None
        try:
            self.progress_bar.stop()
        except Exception:
            pass
        self.progress_bar.configure(mode='determinate', maximum=100)
        self.progress_var.set(0)
        self._restore_polisher_ui()
        self.status_var.set("润色已停止")

    def _add_polisher_output(self, text):
        """添加润色器输出文本"""
        self.polisher_output.insert_colored(text)

    def auto_fix_and_repolish(self):
        """针对润色：根据校验差异(缺失编号)重置对应批次，删除合并文件，并断点续润色。"""
        try:
            input_file = self.polisher_input_file_var.get().strip()
            output_file = self.polisher_output_file_var.get().strip()
            if not input_file or not output_file:
                messagebox.showerror("参数错误", "请先在润色面板设置输入与输出文件")
                return

            # 直接做一次内部快速校验，复用润色器的解析逻辑
            from srt_polisher import SRTPolisher

            # 构造一个轻量实例，仅用于解析与统计，不触发API
            api_type = self.api_type_var.get()
            api_endpoint = self.api_endpoint_var.get().strip()
            dummy = SRTPolisher(
                api_type=api_type,
                api_key=self.api_key_var.get(),
                batch_size=int(float(self.polisher_batch_size_var.get() or 10)),
                context_size=int(float(self.polisher_context_size_var.get() or 2)),
                max_workers=1,
                model_name=self.model_var.get() or None,
                temperature=float(self.polisher_temperature_var.get() or 0.3),
                user_prompt=self.get_current_translator_prompt() or "",
                length_policy=self.polisher_length_policy_var.get(),
                api_endpoint=api_endpoint if api_type == "custom" else None
            )

            src_entries = dummy.parse_srt_file(input_file)
            tr_entries = dummy.parse_srt_file(output_file) if os.path.exists(output_file) else []
            src_nums = {e.number for e in src_entries}
            tr_nums = {e.number for e in tr_entries}
            missing = sorted(src_nums - tr_nums)

            if not missing:
                self._add_polisher_output("[INFO] 未检测到缺失编号，无需修复。\n")
                return

            self._add_polisher_output(f"[INFO] 检测到缺失编号: {missing}\n")

            # 复用翻译器的进度文件命名规则：<output_base>_progress.json
            tr_dir = os.path.dirname(output_file) or '.'
            base_no_ext = os.path.splitext(os.path.basename(output_file))[0]

            # 尝试匹配可能的range_tag
            m = re.search(r"(_\\d+_\\d+)$", base_no_ext)
            range_tag = m.group(1) if m else ""
            pure_base = base_no_ext[: -len(range_tag)] if range_tag else base_no_ext

            progress_candidates = [
                os.path.join(tr_dir, f"{pure_base}_progress{range_tag}.json"),
                os.path.join(tr_dir, f"{pure_base}_progress_polish{range_tag}.json"),
            ]
            progress_path = None
            for pc in progress_candidates:
                if os.path.exists(pc):
                    progress_path = pc
                    break
            if not progress_path:
                messagebox.showerror("错误", "未找到对应的进度文件(progress.json)。请确认润色输出与工作目录。")
                return

            # 读取progress
            with open(progress_path, 'r', encoding='utf-8') as f:
                prog = json.load(f)
            total_batches = int(prog.get('total_batches', 0) or 0)
            completed = set(prog.get('completed_batches', []))

            # 计算缺失编号对应批次
            # 基于源条目在有序列表中的索引计算
            number_to_index = {e.number: idx for idx, e in enumerate(src_entries)}
            try:
                batch_size = int(float(self.polisher_batch_size_var.get() or 10))
            except Exception:
                batch_size = max(1, (len(src_entries) + max(1, total_batches) - 1) // max(1, total_batches))

            batches_to_reset = set()
            for n in missing:
                if n in number_to_index:
                    idx = number_to_index[n]
                    b = (idx // batch_size) + 1
                    if b < 1:
                        b = 1
                    if total_batches:
                        b = min(b, total_batches)
                    batches_to_reset.add(b)

            if not batches_to_reset:
                messagebox.showerror("错误", "无法定位需要重润色的批次。请检查日志或手动处理。")
                return

            self._add_polisher_output(f"[INFO] 需要重置的批次: {sorted(batches_to_reset)}\n")

            # 更新progress：移除对应完成标记
            updated_completed = [b for b in completed if b not in batches_to_reset]
            prog['completed_batches'] = sorted(updated_completed)
            with open(progress_path, 'w', encoding='utf-8') as f:
                json.dump(prog, f, ensure_ascii=False, indent=2)
            self._add_polisher_output("[OK] 已更新进度文件，清除对应批次完成标记\n")

            # 删除相关批次文件与合并输出
            batch_glob = os.path.join(tr_dir, f"{pure_base}_polish_batch*.srt")
            deleted = 0
            for p in glob.glob(batch_glob):
                m2 = re.search(r"_polish_batch(\d+)\\.srt$", p)
                if not m2:
                    continue
                bnum = int(m2.group(1))
                if bnum in batches_to_reset:
                    try:
                        os.remove(p)
                        deleted += 1
                        self._add_polisher_output(f"[OK] 已删除批次文件: {p}\n")
                    except Exception as de:
                        self._add_polisher_output(f"[WARN] 删除批次文件失败: {p} -> {de}\n")

            # 删除已合并的输出文件，便于重新合并
            try:
                if os.path.exists(output_file):
                    os.remove(output_file)
                    self._add_polisher_output(f"[OK] 已删除合并输出文件: {output_file}\n")
            except Exception as e:
                self._add_polisher_output(f"[WARN] 删除合并输出失败: {e}\n")

            # 断点续润色（不传 --no-resume）
            # 直接在当前进程线程中调用 polisher.polish_srt_file，避免另起子进程
            def repolish_thread():
                try:
                    self.root.after(0, lambda: self._add_polisher_output("[INFO] 开始断点续润色缺失批次…\n"))

                    # 用真实参数构建SRTPolisher并续润色
                    api_key = self.api_key_var.get().strip()
                    api_endpoint = self.api_endpoint_var.get().strip()
                    model = self.model_var.get().strip()
                    api_type = self.api_type_var.get()

                    def make_polisher(max_workers_val: int):
                        return SRTPolisher(
                            api_type=api_type,
                            api_key=api_key,
                            batch_size=int(float(self.polisher_batch_size_var.get() or 10)),
                            context_size=int(float(self.polisher_context_size_var.get() or 2)),
                            max_workers=max_workers_val,
                            model_name=model if model else None,
                            temperature=float(self.polisher_temperature_var.get() or 0.3),
                            user_prompt=self.get_current_translator_prompt() or "",
                            api_endpoint=api_endpoint if api_type == "custom" else None,
                            length_policy=self.polisher_length_policy_var.get()
                        )

                    max_attempts = 2
                    attempt = 0
                    while attempt < max_attempts:
                        polisher = make_polisher(int(float(self.polisher_threads_var.get() or 1)))
                        success = polisher.polish_srt_file(
                            input_file=input_file,
                            output_file=output_file,
                            resume=True,
                            auto_verify=True
                        )
                        if not success:
                            self.root.after(0, lambda: self._add_polisher_output("[ERROR] 断点续润色失败，请查看日志。\n"))
                            break

                        # 一次校验
                        verify = polisher.auto_verify_result(input_file, output_file)
                        summary = getattr(polisher, 'last_verify_summary', None)
                        if summary and summary.get('perfect'):
                            self.root.after(0, lambda: self._add_polisher_output("[OK] 缺失批次已补齐并完成合并。二次校验通过。\n"))
                            break

                        # 若仍有缺失，自动二次修复（仅一次）
                        if summary:
                            miss_cnt = summary.get('missing_count')
                            self.root.after(0, lambda: self._add_polisher_output(
                                f"[WARN] 二次校验发现仍有缺失: {miss_cnt} 条，准备自动再次修复…\n"))

                        # 重新计算缺失编号与批次
                        src_entries2 = self._parse_srt_entries_quick(input_file)
                        tr_entries2 = self._parse_srt_entries_quick(output_file)
                        src_nums2 = {e['number'] for e in src_entries2}
                        tr_nums2 = {e['number'] for e in tr_entries2}
                        missing2 = sorted(src_nums2 - tr_nums2)

                        if not missing2:
                            # 安全兜底：若摘要缺失>0而集合为空，也视为通过
                            self.root.after(0, lambda: self._add_polisher_output("[OK] 二次校验通过（无缺失集合）。\n"))
                            break

                        # 定位progress文件/输出基名
                        base_no_ext2 = os.path.splitext(os.path.basename(output_file))[0]
                        mrg = re.search(r"(_\d+_\d+)$", base_no_ext2)
                        range_tag2 = mrg.group(1) if mrg else ""
                        pure_base2 = base_no_ext2[: -len(range_tag2)] if range_tag2 else base_no_ext2
                        progress_candidates2 = [
                            os.path.join(os.path.dirname(output_file) or '.', f"{pure_base2}_progress{range_tag2}.json"),
                            os.path.join(os.path.dirname(output_file) or '.', f"{pure_base2}_progress_polish{range_tag2}.json"),
                        ]
                        progress_path2 = next((p for p in progress_candidates2 if os.path.exists(p)), None)
                        if not progress_path2:
                            self.root.after(0, lambda: self._add_polisher_output("[ERROR] 未找到进度文件，无法自动再次修复。\n"))
                            break

                        # 读取progress
                        with open(progress_path2, 'r', encoding='utf-8') as f:
                            prog2 = json.load(f)
                        total_batches2 = int(prog2.get('total_batches', 0) or 0)
                        batch_glob2 = os.path.join(os.path.dirname(progress_path2) or '.', f"{pure_base2}_polish_batch*.srt")
                        batches_to_reset2 = self._compute_batches_to_reset(
                            missing2, src_entries2, total_batches2, range_tag2, batch_glob2, self.polisher_batch_size_var.get()
                        )
                        # 更新progress
                        updated_completed2 = [b for b in prog2.get('completed_batches', []) if b not in batches_to_reset2]
                        prog2['completed_batches'] = sorted(updated_completed2)
                        with open(progress_path2, 'w', encoding='utf-8') as f:
                            json.dump(prog2, f, ensure_ascii=False, indent=2)
                        # 删除批次文件与合并文件
                        for p in glob.glob(batch_glob2):
                            m2 = re.search(r"_polish_batch(\d+)\\.srt$", p)
                            if m2 and int(m2.group(1)) in batches_to_reset2:
                                try:
                                    os.remove(p)
                                except Exception:
                                    pass
                        try:
                            if os.path.exists(output_file):
                                os.remove(output_file)
                        except Exception:
                            pass

                        attempt += 1
                        if attempt >= max_attempts:
                            self.root.after(0, lambda: self._add_polisher_output("[WARN] 已达到自动修复最大次数，请手动再次执行一键修复。\n"))
                            break
                except Exception as e:
                    self.root.after(0, lambda: self._add_polisher_output(f"[ERROR] 重润色过程出错: {str(e)}\n"))

            threading.Thread(target=repolish_thread, daemon=True).start()

        except Exception as e:
            messagebox.showerror("错误", f"一键修复失败: {str(e)}")

    def _restore_polisher_ui(self):
        """恢复润色器UI状态"""
        self.polisher_start_button.config(state=tk.NORMAL)
        self.polisher_stop_button.config(state=tk.DISABLED)
        self.is_running = False
        self.status_var.set("就绪")

    def refresh_polisher_api_status(self):
        """刷新润色器API配置状态显示"""
        try:
            api_type = self.api_type_var.get()
            api_endpoint = self.api_endpoint_var.get().strip()
            model = self.model_var.get().strip()
            api_key = self.api_key_var.get().strip()

            # 显示API配置状态
            if api_key:
                key_display = api_key[:10] + "..." if len(api_key) > 10 else api_key
            else:
                key_display = "未设置"

            if api_type == "custom" and api_endpoint:
                endpoint_display = api_endpoint
            elif api_type == "deepseek":
                endpoint_display = "DeepSeek API"
            elif api_type == "grok":
                endpoint_display = "Grok API"
            else:
                endpoint_display = "未知API"

            status_text = f"API: {endpoint_display}\n模型: {model or '默认'}\n密钥: {key_display}"
            self.polisher_api_status_label.config(text=status_text, foreground="green")

            # 同时更新用户提示词状态
            self.refresh_polisher_prompt_status()

        except Exception as e:
            self.polisher_api_status_label.config(text=f"获取API配置失败: {str(e)}", foreground="red")

    def refresh_polisher_prompt_status(self):
        """刷新润色器用户提示词状态"""
        try:
            # 获取当前翻译器选择的预设
            current_preset = self.user_prompt_var.get()
            if current_preset and current_preset in self.presets:
                preset_name = self.presets[current_preset]["name"]
                prompt_text = f"使用预设: {preset_name}"
            else:
                # 获取用户自定义提示词
                custom_prompt = self.user_prompt_text.get(1.0, tk.END).strip()
                if custom_prompt:
                    prompt_preview = custom_prompt[:30] + "..." if len(custom_prompt) > 30 else custom_prompt
                    prompt_text = f"自定义: {prompt_preview}"
                else:
                    prompt_text = "使用默认润色提示词"

            self.polisher_prompt_status_label.config(text=prompt_text, foreground="blue")
        except Exception as e:
            self.polisher_prompt_status_label.config(text=f"获取提示词失败: {str(e)}", foreground="red")

    def get_current_translator_prompt(self):
        """获取翻译器当前使用的提示词"""
        try:
            # 先检查是否选择了预设
            current_preset = self.user_prompt_var.get()
            if current_preset and current_preset in self.presets:
                return self.presets[current_preset]["content"]
            else:
                # 获取用户自定义提示词
                custom_prompt = self.user_prompt_text.get(1.0, tk.END).strip()
                return custom_prompt if custom_prompt else ""
        except Exception:
            return ""

    def on_format_option_change(self, *args):
        """当格式选项改变时自动保存配置"""
        # 如果正在加载配置，跳过自动保存
        if getattr(self, '_loading_config', False):
            return
            
        try:
            self.save_config(quiet=True)  # 静默保存，不显示成功提示
        except Exception as e:
            # 静默处理保存错误，避免干扰用户体验
            pass
    
    def on_corrector_option_change(self, *args):
        """当纠错器选项改变时自动保存配置"""
        # 如果正在加载配置，跳过自动保存
        if getattr(self, '_loading_config', False):
            return
            
        try:
            self.save_config(quiet=True)  # 静默保存，不显示成功提示
        except Exception as e:
            # 静默处理保存错误，避免干扰用户体验
            safe_file_log(f"auto_save_config error: {e}")
    
    def save_corrector_config(self):
        """保存纠错器配置并显示提示"""
        try:
            self.save_config(quiet=True)
            # 状态栏提示
            self.status_var.set("✓ 配置已保存")
            # 在纠错器日志中显示
            if hasattr(self, 'corrector_output_text'):
                self._add_corrector_output("✓ 配置已保存\n")
        except Exception as e:
            self.status_var.set(f"✗ 保存配置失败: {str(e)}")
            if hasattr(self, 'corrector_output_text'):
                self._add_corrector_output(f"✗ 保存配置失败: {str(e)}\n")

    def save_polisher_config(self):
        """保存润色器配置并显示提示"""
        try:
            self.save_config(quiet=True)
            # 状态栏提示
            self.status_var.set("✓ 配置已保存")
            # 在润色器日志中显示
            if hasattr(self, 'polisher_output'):
                self._add_polisher_output("✓ 配置已保存\n")
        except Exception as e:
            self.status_var.set(f"✗ 保存配置失败: {str(e)}")
            if hasattr(self, 'polisher_output'):
                self._add_polisher_output(f"✗ 保存配置失败: {str(e)}\n")

    # 字幕纠错器用户提示词相关方法
    def clear_corrector_user_prompt(self):
        """清空纠错器用户提示词"""
        self.corrector_user_prompt_text.delete(1.0, tk.END)
    
    def save_corrector_user_prompt(self):
        """保存纠错器用户提示词到配置"""
        try:
            self.save_config(quiet=False)
            messagebox.showinfo("保存成功", "用户提示词已保存到配置文件！")
        except Exception as e:
            messagebox.showerror("保存失败", f"保存用户提示词时出错：{str(e)}")
    
    def load_corrector_preset(self, preset_id):
        """加载纠错器预设到提示词框"""
        if preset_id in self.corrector_presets:
            content = self.corrector_presets[preset_id]["content"]
            self.corrector_user_prompt_text.delete(1.0, tk.END)
            self.corrector_user_prompt_text.insert(1.0, content)
    
    def add_corrector_preset_context_menu(self, button, preset_id):
        """为纠错器预设按钮添加右键菜单"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="编辑预设", command=lambda: self.edit_corrector_preset(preset_id))
        menu.add_command(label="重置为默认", command=lambda: self.reset_corrector_preset(preset_id))
        
        def show_menu(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        
        button.bind("<Button-3>", show_menu)  # 右键菜单
    
    def edit_corrector_preset(self, preset_id):
        """编辑纠错器预设"""
        if preset_id not in self.corrector_presets:
            return
        
        # 创建编辑窗口
        edit_window = tk.Toplevel(self.root)
        edit_window.title(f"编辑纠错器预设 {preset_id}")
        edit_window.geometry("500x400")
        edit_window.transient(self.root)
        edit_window.grab_set()
        
        # 名称输入
        name_frame = ttk.Frame(edit_window)
        name_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(name_frame, text="预设名称:").pack(side=tk.LEFT)
        name_var = tk.StringVar(value=self.corrector_presets[preset_id]["name"])
        name_entry = ttk.Entry(name_frame, textvariable=name_var)
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        
        # 内容输入
        content_frame = ttk.Frame(edit_window)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        ttk.Label(content_frame, text="预设内容:").pack(anchor=tk.W)
        content_text = scrolledtext.ScrolledText(content_frame, height=15, wrap=tk.WORD)
        content_text.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        content_text.insert(1.0, self.corrector_presets[preset_id]["content"])
        
        # 按钮
        button_frame = ttk.Frame(edit_window)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        
        def save_preset():
            new_name = name_var.get().strip()
            new_content = content_text.get(1.0, tk.END).strip()
            
            if not new_name:
                messagebox.showerror("错误", "预设名称不能为空")
                return
            
            if not new_content:
                messagebox.showerror("错误", "预设内容不能为空")
                return
            
            # 更新预设
            self.corrector_presets[preset_id]["name"] = new_name
            self.corrector_presets[preset_id]["content"] = new_content
            debug_file_log(f"preset updated: id={preset_id} name={new_name} len={len(new_content)}")
            
            # 更新按钮文字（使用截断显示）
            display_name = truncate_text(new_name, 10)
            self.corrector_preset_buttons[preset_id].config(text=display_name)
            
            # 更新或创建tooltip
            if preset_id in self.corrector_preset_tooltips:
                self.corrector_preset_tooltips[preset_id].text = new_name
            elif len(new_name) > 10:
                self.corrector_preset_tooltips[preset_id] = ToolTip(
                    self.corrector_preset_buttons[preset_id], new_name)
            
            # 保存配置
            try:
                self.save_config(quiet=True)
                debug_file_log(f"preset save ok: id={preset_id}")
                messagebox.showinfo("成功", f"预设 '{new_name}' 已保存")
                edit_window.destroy()
            except Exception as e:
                safe_file_log(f"preset save error: id={preset_id} err={e}")
                messagebox.showerror("保存失败", f"保存预设时出错：{str(e)}")
        
        ttk.Button(button_frame, text="保存", command=save_preset).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(button_frame, text="取消", command=edit_window.destroy).pack(side=tk.RIGHT)
    
    def reset_corrector_preset(self, preset_id):
        """重置纠错器预设为默认值"""
        current_name = self.corrector_presets[preset_id]["name"]
        if messagebox.askyesno("确认重置", f"确定要重置预设 '{current_name}' 为默认值吗？\n\n此操作不可撤销！"):
            # 重新初始化默认预设并获取对应的默认值
            default_presets = {
                1: {"name": "标准纠错", "content": "修正常见的错别字、同音字错误，如'的得'、'在再'、'做作'等。保持原文的语言风格和表达习惯，不要改变原意。"},
                2: {"name": "保守纠错", "content": "只修正明显的错别字，对于可能是专有名词或技术术语的内容保持谨慎，确实不确定的词汇保持原样。注重保持原文的语言风格。"},
                3: {"name": "口语化纠错", "content": "注意修正口语化表达中的语法错误，将不规范的口语表达调整为更标准的书面语，但保持自然的表达方式，不要过于正式。"},
                4: {"name": "技术内容纠错", "content": "重点修正语音识别导致的技术词汇错误，确保技术表达的准确性。"},
                5: {"name": "标点符号纠错", "content": "除了修正错别字外，也要注意标点符号的使用，修正明显的标点错误，确保句子结构清晰。"}
            }
            
            if preset_id in default_presets:
                self.corrector_presets[preset_id] = default_presets[preset_id].copy()
                # 更新按钮文字（使用截断显示）
                full_name = self.corrector_presets[preset_id]["name"]
                display_name = truncate_text(full_name, 10)
                self.corrector_preset_buttons[preset_id].config(text=display_name)
                
                # 更新或创建tooltip
                if preset_id in self.corrector_preset_tooltips:
                    self.corrector_preset_tooltips[preset_id].text = full_name
                elif len(full_name) > 10:
                    self.corrector_preset_tooltips[preset_id] = ToolTip(
                        self.corrector_preset_buttons[preset_id], full_name)
                
                try:
                    self.save_config(quiet=True)
                    messagebox.showinfo("成功", "预设已重置为默认值")
                except Exception as e:
                    messagebox.showerror("保存失败", f"保存预设时出错：{str(e)}")
    def _generate_comparison_report(self, input_file: str, output_file: str, stats: dict) -> Optional[str]:
        """生成处理前后的对比报告"""
        try:
            import tempfile
            import datetime
            import re
            
            # 解析SRT文件的函数
            def parse_srt_file(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                except UnicodeDecodeError:
                    try:
                        with open(file_path, 'r', encoding='gbk') as f:
                            content = f.read()
                    except UnicodeDecodeError:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                
                # 使用与纠错器相同的正则表达式
                SRT_PATTERN = re.compile(
                    r'(\d+)\s*\n'               # 字幕序号
                    r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n'  # 时间码
                    r'((?:.+(?:\n|$))+?)'       # 字幕内容
                    r'(?:\n|$)',                # 空行或文件结尾
                    re.MULTILINE
                )
                
                entries = []
                for match in SRT_PATTERN.finditer(content):
                    number = int(match.group(1))
                    start_time = match.group(2)
                    end_time = match.group(3)
                    subtitle_content = match.group(4).strip()
                    entries.append({
                        'number': number,
                        'start_time': start_time,
                        'end_time': end_time,
                        'content': subtitle_content
                    })
                return entries
            
            # 解析原文件和输出文件
            original_entries = parse_srt_file(input_file)
            corrected_entries = parse_srt_file(output_file)
            
            # 生成HTML报告内容
            html_content = []
            
            # HTML头部和样式
            html_content.append("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>字幕纠错对比报告</title>
    <style>
        body {{
            font-family: 'Microsoft YaHei', 'SimSun', Arial, sans-serif;
            line-height: 1.6;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            text-align: center;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
            margin-bottom: 30px;
        }}
        .info-section {{
            background: #ecf0f1;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: linear-gradient(135deg, #74b9ff, #0984e3);
            color: white;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-value {{
            font-size: 2em;
            font-weight: bold;
            display: block;
        }}
        .stat-label {{
            font-size: 0.9em;
            opacity: 0.9;
        }}
        .comparison-item {{
            border: 1px solid #ddd;
            border-radius: 8px;
            margin-bottom: 20px;
            overflow: hidden;
        }}
        .comparison-header {{
            background: #34495e;
            color: white;
            padding: 10px 15px;
            font-weight: bold;
        }}
        .comparison-content {{
            display: grid;
            grid-template-columns: 1fr 1fr;
        }}
        .original, .corrected {{
            padding: 15px;
        }}
        .original {{
            background: #fff5f5;
            border-right: 1px solid #ddd;
        }}
        .corrected {{
            background: #f0fff4;
        }}
        .content-label {{
            font-weight: bold;
            color: #666;
            margin-bottom: 8px;
        }}
        .content-text {{
            background: white;
            padding: 10px;
            border-radius: 4px;
            border-left: 4px solid #3498db;
            font-family: 'Consolas', 'Monaco', monospace;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        .no-changes {{
            text-align: center;
            color: #7f8c8d;
            padding: 40px;
            font-style: italic;
        }}
        .summary {{
            background: #d5f4e6;
            border-left: 4px solid #27ae60;
            padding: 15px;
            margin-top: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔧 字幕纠错对比报告</h1>
        
        <div class="info-section">
            <p><strong>📅 生成时间：</strong>{}</p>
            <p><strong>📁 原始文件：</strong>{}</p>
            <p><strong>✅ 纠错文件：</strong>{}</p>
        </div>
""".format(
                datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                os.path.basename(input_file),
                os.path.basename(output_file)
            ))
            
            # 统计信息
            html_content.append('<h2>📊 处理统计</h2>\n<div class="stats-grid">\n')
            
            # 统计卡片
            stats_items = [
                ('总计条目', stats.get('total_entries', 0), '条'),
                ('格式整理', stats.get('formatted_entries', 0), '条'),
                ('AI纠错', stats.get('corrected_entries', 0), '条'),
                ('无需修改', stats.get('unchanged_entries', 0), '条')
            ]
            
            if stats.get('error_entries', 0) > 0:
                stats_items.append(('处理失败', stats.get('error_entries', 0), '条'))
            
            for label, value, unit in stats_items:
                html_content.append(f'''
    <div class="stat-card">
        <span class="stat-value">{value}</span>
        <span class="stat-label">{label} {unit}</span>
    </div>''')
            
            # 添加百分比统计
            if stats.get('total_entries', 0) > 0:
                format_rate = (stats.get('formatted_entries', 0) / stats.get('total_entries', 1)) * 100
                correction_rate = (stats.get('corrected_entries', 0) / stats.get('total_entries', 1)) * 100
                improved_entries = stats.get('formatted_entries', 0) + stats.get('corrected_entries', 0)
                improve_rate = (improved_entries / stats.get('total_entries', 1)) * 100
                
                rate_items = [
                    ('格式整理率', f'{format_rate:.1f}', '%'),
                    ('AI纠错率', f'{correction_rate:.1f}', '%'),
                    ('总改进率', f'{improve_rate:.1f}', '%')
                ]
                
                for label, value, unit in rate_items:
                    html_content.append(f'''
    <div class="stat-card">
        <span class="stat-value">{value}</span>
        <span class="stat-label">{label} {unit}</span>
    </div>''')
            
            html_content.append('</div>\n')
            
            # 详细对比
            html_content.append('<h2>🔍 详细对比</h2>\n')
            html_content.append('<p><em>只显示有修改的字幕条目</em></p>\n')
            
            changed_count = 0
            min_entries = min(len(original_entries), len(corrected_entries))
            
            for i in range(min_entries):
                orig = original_entries[i]
                corr = corrected_entries[i]
                
                if orig['content'].strip() != corr['content'].strip():
                    changed_count += 1
                    html_content.append(f'''
        <div class="comparison-item">
            <div class="comparison-header">
                第 {orig['number']} 条字幕 ({orig['start_time']} --&gt; {orig['end_time']})
            </div>
            <div class="comparison-content">
                <div class="original">
                    <div class="content-label">🔴 原文</div>
                    <div class="content-text">{orig['content']}</div>
                </div>
                <div class="corrected">
                    <div class="content-label">🟢 纠错后</div>
                    <div class="content-text">{corr['content']}</div>
                </div>
            </div>
        </div>''')
            
            if changed_count == 0:
                html_content.append('<div class="no-changes">🎉 没有发现需要修改的字幕条目</div>')
            else:
                html_content.append(f'''
        <div class="summary">
            <strong>📈 修改统计：</strong>共发现 <strong>{changed_count}</strong> 条字幕有修改
        </div>''')
            
            # HTML结尾
            html_content.append('''
    </div>
</body>
</html>''')
            
            # 保存到临时目录
            temp_dir = tempfile.gettempdir()
            input_basename = os.path.splitext(os.path.basename(input_file))[0]
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            report_filename = f"字幕纠错报告_{input_basename}_{timestamp}.html"
            report_path = os.path.join(temp_dir, report_filename)
            
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(''.join(html_content))
            
            return report_path
            
        except Exception as e:
            import logging
            logging.error(f"生成对比报告失败: {e}")
            return None

    def auto_fill_review_files(self, input_file: str, output_file: str):
        """纠错完成后自动填充纠错审核文件路径"""
        try:
            # 设置原始文件和纠错后文件路径
            if input_file and os.path.exists(input_file):
                self.review_original_file_var.set(input_file)
                self._add_corrector_output(f"✅ 已自动设置审核器原始文件: {input_file}\n")
            
            if output_file and os.path.exists(output_file):
                self.review_corrected_file_var.set(output_file)
                self._add_corrector_output(f"✅ 已自动设置审核器纠错文件: {output_file}\n")
            
            # 自动切换到纠错审核标签页
            self.notebook.select(self.review_frame)
            self._add_corrector_output("🔄 已切换到纠错审核标签页，您可以立即开始审核纠错结果！\n")
            
            # 在审核标签页中也添加提示
            self.review_status_label.config(text="文件路径已自动填充，点击'加载对比'开始审核")
            
        except Exception as e:
            self._add_corrector_output(f"❌ 自动填充纠错审核文件路径时出错: {str(e)}\n")

    # 纠错审核相关方法
    def update_min_speed_label(self, value):
        """更新最小语速标签显示"""
        self.min_speed_label.config(text=f"{float(value):.1f}")
        # 实时更新内部变量
        self.cn_speed_min = float(value)

    def update_max_speed_label(self, value):
        """更新最大语速标签显示"""
        self.max_speed_label.config(text=f"{float(value):.1f}")
        # 实时更新内部变量
        self.cn_speed_max = float(value)

    def save_speed_thresholds(self):
        """保存语速阈值到配置文件"""
        try:
            # 更新内部变量
            self.cn_speed_min = self.cn_speed_min_var.get()
            self.cn_speed_max = self.cn_speed_max_var.get()

            # 验证阈值范围
            if self.cn_speed_min >= self.cn_speed_max:
                messagebox.showerror("错误", "最小语速必须小于最大语速！")
                return

            # 保存配置
            self.save_config(quiet=False)

            # 如果当前有加载的对比数据，刷新高亮显示
            if self.review_entries:
                self._schedule_review_overlay_update()

        except Exception as e:
            messagebox.showerror("错误", f"保存语速阈值失败: {str(e)}")

    def browse_review_original_file(self):
        """浏览选择原始字幕文件"""
        file_path = filedialog.askopenfilename(
            title="选择原始字幕文件",
            filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]
        )
        if file_path:
            self.review_original_file_var.set(file_path)
    
    def browse_review_corrected_file(self):
        """浏览选择纠错后字幕文件"""
        file_path = filedialog.askopenfilename(
            title="选择纠错后字幕文件",
            filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]
        )
        if file_path:
            self.review_corrected_file_var.set(file_path)
    
    def load_comparison(self):
        """加载并对比两个字幕文件"""
        original_file = self.review_original_file_var.get().strip()
        corrected_file = self.review_corrected_file_var.get().strip()
        
        if not original_file or not corrected_file:
            messagebox.showerror("错误", "请选择原始文件和纠错后文件")
            return
            
        if not os.path.exists(original_file):
            messagebox.showerror("错误", f"原始文件不存在：{original_file}")
            return
            
        if not os.path.exists(corrected_file):
            messagebox.showerror("错误", f"纠错后文件不存在：{corrected_file}")
            return
        
        try:
            # 当文件组合发生变化时，重置审核状态；同一组合保留状态
            current_pair = (original_file, corrected_file)
            if getattr(self, 'last_review_files', None) != current_pair:
                if hasattr(self, 'review_processed_numbers'):
                    self.review_processed_numbers.clear()
                self.last_review_files = current_pair
            self.review_item_to_entry = {}
            self._clear_review_overlays()

            # 使用差异检测引擎比较文件
            changed_entries, original_count, corrected_count = SubtitleDiffEngine.compare_srt_files(original_file, corrected_file)
            
            # 存储到实例变量
            self.review_entries = changed_entries
            self.corrected_file_path = corrected_file  # 保存纠错后文件路径
            
            # 更新状态显示
            self.review_status_label.config(text=f"已加载 {len(changed_entries)} 条变更项")
            
            # 更新统计信息
            self.review_stats_label.config(text=f"共 {len(changed_entries)} 条变更 | 原文件: {original_count}条 | 纠错后: {corrected_count}条")
            
            # 填充表格数据
            self.populate_review_tree()
            
            # 重置修改状态
            self.review_modified = False
            self.update_review_modified_status()
            
        except Exception as e:
            messagebox.showerror("加载失败", f"加载对比时出错：{str(e)}")
    
    # ====== 审核表格：时长与语速计算辅助 ======
    def _parse_srt_time_to_seconds(self, ts: str) -> float:
        try:
            hms, ms = ts.split(',')
            h, m, s = hms.split(':')
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
        except Exception:
            return 0.0

    def _format_duration(self, seconds: float) -> str:
        if seconds <= 0:
            return "-"
        return f"{seconds:.1f}"

    def _char_count(self, text: str) -> int:
        try:
            import re
            return len(re.sub(r"\s+", "", text or ""))
        except Exception:
            return len(text or "")

    def _format_speed(self, chars: int, seconds: float) -> str:
        if seconds <= 0:
            return "字符数/秒:-"
        return f"字符数/秒:{(chars / seconds):.1f}"

    def populate_review_tree(self):
        """填充审核表格数据"""
        # 清空现有数据
        for item in self.review_tree.get_children():
            self.review_tree.delete(item)
        # 清空覆盖层
        self._clear_review_overlays()

        # 添加变更项数据
        for entry in self.review_entries:
            status_text = self.get_status_text(entry.current_status)

            # 生成高亮差异文本
            original_highlighted, corrected_highlighted = self.highlight_differences(
                entry.original_content, entry.corrected_content
            )

            # 截断显示文本
            original_display = self.truncate_text(original_highlighted, 150)
            corrected_display = self.truncate_text(corrected_highlighted, 150)

            # 计算时长与语速
            dur = max(0.0, self._parse_srt_time_to_seconds(entry.end_time) - self._parse_srt_time_to_seconds(entry.start_time))
            dur_text = self._format_duration(dur)
            orig_chars = self._char_count(entry.original_content)
            corr_chars = self._char_count(entry.corrected_content)
            orig_speed = self._format_speed(orig_chars, dur)
            corr_speed = self._format_speed(corr_chars, dur)

            # 插入行并应用高亮标签
            try:
                item_id = self.review_tree.insert("", tk.END, values=(
                    entry.number,
                    dur_text,
                    original_display,
                    orig_speed,
                    corrected_display,
                    "",
                    corr_speed,
                    status_text
                ))
            except Exception:
                # 兼容旧列配置（未刷新列定义时）
                item_id = self.review_tree.insert("", tk.END, values=(
                    entry.number,
                    original_display,
                    corrected_display,
                    status_text
                ))
            
            # 如果有差异，应用高亮标签
            if "[-" in original_display or "[+" in corrected_display:
                self.review_tree.set(item_id, "原文", original_display)
                self.review_tree.set(item_id, "纠错后", corrected_display)
                # 给整行添加高亮背景
                self.review_tree.item(item_id, tags=("changed",))

            # 建立条目映射，方便覆盖层渲染
            self.review_item_to_entry[item_id] = entry

            # 如果该编号在已处理集合中，覆盖为“已处理”绿色背景
            if entry.number in getattr(self, 'review_processed_numbers', set()):
                self.review_tree.item(item_id, tags=("kept_original",))

        # 在插入完毕后刷新一次覆盖层
        self._schedule_review_overlay_update()
    
    def truncate_text(self, text: str, max_length: int) -> str:
        """截断文本用于表格显示"""
        # 替换换行符为空格
        display_text = text.replace('\n', ' ').strip()
        if len(display_text) > max_length:
            return display_text[:max_length-3] + "..."
        return display_text

    # ====== 覆盖层渲染：在表格单元格内显示彩色差异 ======
    def _on_review_scrollbar_y(self, *args):
        # 同步Treeview与覆盖层的垂直滚动
        self.review_tree.yview(*args)
        self._schedule_review_overlay_update()

    def _on_review_scrollbar_x(self, *args):
        self.review_tree.xview(*args)
        self._schedule_review_overlay_update()

    def _on_review_tree_yscroll(self, first, last):
        # 让滚动条反映位置，并安排刷新
        try:
            self.review_tree_scrollbar_y.set(first, last)
        except Exception:
            pass
        self._schedule_review_overlay_update()

    def _on_review_tree_xscroll(self, first, last):
        try:
            self.review_tree_scrollbar_x.set(first, last)
        except Exception:
            pass
        self._schedule_review_overlay_update()

    def _schedule_review_overlay_update(self):
        # 合并短时间内的多次刷新请求（降低频率以避免大数据量卡顿）
        if self._review_overlay_update_job is not None:
            try:
                self.root.after_cancel(self._review_overlay_update_job)
            except Exception:
                pass
        # 70ms 节流：在快速拖动滚动条时减小刷新频率
        self._review_overlay_update_job = self.root.after(70, self._update_review_overlays)

    def _clear_review_overlays(self):
        # 移除现有覆盖层
        for widget in list(self._review_cell_overlays.values()):
            try:
                widget.destroy()
            except Exception:
                pass
        self._review_cell_overlays.clear()

    def _update_review_overlays(self):
        # 避免频繁调用
        self._review_overlay_update_job = None

        if not hasattr(self, 'review_tree'):
            return

        # 当前可见区域内的行：只遍历能取到bbox的行（即可见行），避免全量遍历引起卡顿
        all_items = self.review_tree.get_children("")
        visible_items = []
        for iid in all_items:
            if self.review_tree.bbox(iid):
                visible_items.append(iid)
        if not visible_items:
            self._clear_review_overlays()
            return

        # 仅为可见行绘制覆盖，避免 O(n) 重绘导致的性能问题
        diff_columns = ("原文", "纠错后")
        speed_columns = ("原文语速", "纠错语速")
        for item_id in visible_items:
            bbox_row = self.review_tree.bbox(item_id)
            if not bbox_row:
                continue
            y = bbox_row[1]
            height = bbox_row[3]

            for col in diff_columns:
                try:
                    bbox = self.review_tree.bbox(item_id, col)
                except Exception:
                    bbox = None
                if not bbox:
                    continue
                x, y, w, h = bbox

                key = (item_id, col)
                overlay = self._review_cell_overlays.get(key)
                if overlay is None:
                    # 创建Text作为覆盖层
                    overlay = tk.Text(self.review_tree, height=1, wrap=tk.NONE, borderwidth=0, highlightthickness=0, takefocus=0)
                    # 提升对比度与可读性：更饱和底色、加粗、更大字号
                    overlay.tag_configure("del", background="#ffb3b3", foreground="#8b0000", font=('Microsoft YaHei UI', 10, 'bold'))
                    overlay.tag_configure("ins", background="#b6ffb6", foreground="#004d00", font=('Microsoft YaHei UI', 10, 'bold'))
                    overlay.tag_configure("eq", foreground="#000000", font=('Microsoft YaHei UI', 10))
                    overlay.configure(font=('Microsoft YaHei UI', 10))
                    self._review_cell_overlays[key] = overlay
                    # 让覆盖层也能响应单击/双击，触发与Treeview一致的行为
                    overlay.bind('<Button-1>', lambda e, iid=item_id: self._handle_overlay_click(iid))
                    overlay.bind('<Double-1>', lambda e, iid=item_id: self._handle_overlay_double_click(iid))
                    overlay.bind('<Button-3>', lambda e, iid=item_id: self._show_review_context_menu_over_overlay(e, iid))
                    overlay.bind('<Enter>', lambda e, iid=item_id: self._set_review_hover_item(iid))

                # 放置在对应单元格位置
                # place 仅在尺寸变化时更新，避免快速拖动时反复布局
                overlay.place_configure(x=x+1, y=y+1, width=w-2, height=h-2)
                overlay.config(state=tk.NORMAL)
                overlay.delete(1.0, tk.END)

                # 获取对应条目文本，解析差异标记并着色
                text = self.review_tree.set(item_id, col)
                self._insert_colored_diff_to_overlay(overlay, text)
                overlay.config(state=tk.DISABLED)

            # 语速列：超出阈值时以红底覆盖该单元格
            try:
                entry = self.review_item_to_entry.get(item_id)
            except Exception:
                entry = None
            if entry is not None:
                try:
                    dur = max(0.0, self._parse_srt_time_to_seconds(entry.end_time) - self._parse_srt_time_to_seconds(entry.start_time))
                except Exception:
                    dur = 0.0
                import re
                def _cn_count(t):
                    return len(re.findall(r"[\u4e00-\u9fff]", t or ""))
                cn_orig = _cn_count(entry.original_content)
                cn_corr = _cn_count(entry.corrected_content)
                sp_min = getattr(self, 'cn_speed_min', 2.0)
                sp_max = getattr(self, 'cn_speed_max', 4.0)
                for col, cn_chars in (("原文语速", cn_orig), ("纠错语速", cn_corr)):
                    try:
                        bbox = self.review_tree.bbox(item_id, col)
                    except Exception:
                        bbox = None
                    if not bbox:
                        continue
                    x, y, w, h = bbox
                    key = (item_id, col)
                    # 仅当存在中文字符并且时长>0时才判断
                    if dur <= 0 or cn_chars <= 0:
                        # 清理可能存在的覆盖
                        if key in self._review_cell_overlays:
                            try:
                                self._review_cell_overlays[key].destroy()
                            except Exception:
                                pass
                            del self._review_cell_overlays[key]
                        continue
                    speed_cn = cn_chars / dur
                    out_of_range = speed_cn < sp_min or speed_cn > sp_max
                    overlay = self._review_cell_overlays.get(key)
                    if out_of_range:
                        if overlay is None:
                            overlay = tk.Label(self.review_tree, borderwidth=0, highlightthickness=0)
                            overlay.bind('<Button-1>', lambda e, iid=item_id: self._handle_overlay_click(iid))
                            overlay.bind('<Double-1>', lambda e, iid=item_id: self._handle_overlay_double_click(iid))
                            overlay.bind('<Button-3>', lambda e, iid=item_id: self._show_review_context_menu_over_overlay(e, iid))
                            overlay.bind('<Enter>', lambda e, iid=item_id: self._set_review_hover_item(iid))
                            self._review_cell_overlays[key] = overlay
                        cell_text = self.review_tree.set(item_id, col)
                        overlay.configure(text=cell_text, bg="#ffcccc", fg="#990000", font=('Microsoft YaHei UI', 9))
                        overlay.place_configure(x=x+1, y=y+1, width=w-2, height=h-2)
                    else:
                        if overlay is not None:
                            try:
                                overlay.destroy()
                            except Exception:
                                pass
                            del self._review_cell_overlays[key]

        # 清理已不可见的覆盖层
        alive_keys = set()
        for item_id in visible_items:
            for col in ("原文", "纠错后", "原文语速", "纠错语速"):
                alive_keys.add((item_id, col))
        for key in list(self._review_cell_overlays.keys()):
            if key not in alive_keys:
                try:
                    self._review_cell_overlays[key].destroy()
                except Exception:
                    pass
                del self._review_cell_overlays[key]

    def _insert_colored_diff_to_overlay(self, text_widget, display_text):
        """将带有 [-删除-] 与 [+新增+] 标记的字符串渲染为彩色。"""
        i = 0
        n = len(display_text)
        while i < n:
            if i+2 < n and display_text[i:i+2] == "[-":
                # 找到删除段
                j = display_text.find("-]", i+2)
                if j == -1:
                    # 无闭合，按正常文本处理
                    text_widget.insert(tk.END, display_text[i:], ("eq",))
                    break
                content = display_text[i+2:j]
                text_widget.insert(tk.END, content, ("del",))
                i = j + 2
            elif i+2 < n and display_text[i:i+2] == "[+":
                # 找到新增段
                j = display_text.find("+]", i+2)
                if j == -1:
                    text_widget.insert(tk.END, display_text[i:], ("eq",))
                    break
                content = display_text[i+2:j]
                text_widget.insert(tk.END, content, ("ins",))
                i = j + 2
            else:
                # 普通文本，尽量到下一个标记的前面
                next_del = display_text.find("[-", i)
                next_ins = display_text.find("[+", i)
                next_pos = [p for p in (next_del, next_ins) if p != -1]
                end = min(next_pos) if next_pos else n
                text_widget.insert(tk.END, display_text[i:end], ("eq",))
                i = end

    def _handle_overlay_click(self, item_id):
        try:
            self.review_tree.selection_set(item_id)
        except Exception:
            pass

    def _handle_overlay_double_click(self, item_id):
        # 与Treeview双击行为一致：打开编辑对话框
        try:
            self.review_tree.selection_set(item_id)
            entry = self.review_item_to_entry.get(item_id)
            if entry:
                self.open_edit_dialog(entry, item_id)
        except Exception:
            pass

    def _show_review_context_menu_over_overlay(self, event, item_id):
        try:
            self.review_tree.selection_set(item_id)
            context_menu = tk.Menu(self.root, tearoff=0)
            context_menu.add_command(label="采用AI纠错", command=lambda: self.set_entry_status(item_id, "corrected"))
            context_menu.add_command(label="保持原文", command=lambda: self.set_entry_status(item_id, "original"))
            context_menu.add_separator()
            context_menu.add_command(label="手动编辑", command=lambda: self.edit_entry(item_id))
            try:
                context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                context_menu.grab_release()
        except Exception:
            pass
    
    def highlight_differences(self, original: str, corrected: str) -> tuple:
        """高亮显示两个文本之间的差异"""
        import difflib
        
        # 清理文本以便比较
        orig_clean = original.replace('\n', ' ').strip()
        corr_clean = corrected.replace('\n', ' ').strip()
        
        # 如果文本完全相同，直接返回
        if orig_clean == corr_clean:
            return (orig_clean, corr_clean)
        
        # 使用difflib进行字符级比较，覆盖面更广
        matcher = difflib.SequenceMatcher(None, orig_clean, corr_clean)
        
        original_parts = []
        corrected_parts = []
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                # 相同部分
                original_parts.append(orig_clean[i1:i2])
                corrected_parts.append(corr_clean[j1:j2])
            elif tag == 'delete':
                # 在原文中删除的部分
                original_parts.append(f"[-{orig_clean[i1:i2]}-]")
            elif tag == 'insert':
                # 在纠错后插入的部分
                corrected_parts.append(f"[+{corr_clean[j1:j2]}+]")
            elif tag == 'replace':
                # 替换的部分
                original_parts.append(f"[-{orig_clean[i1:i2]}-]")
                corrected_parts.append(f"[+{corr_clean[j1:j2]}+]")
        
        original_highlighted = ''.join(original_parts)
        corrected_highlighted = ''.join(corrected_parts)
        
        return (original_highlighted, corrected_highlighted)
    
    def get_status_text(self, status: str) -> str:
        """获取状态显示文本"""
        status_map = {
            "corrected": "采用AI",
            "original": "保持原文", 
            "edited": "手动编辑"
        }
        return status_map.get(status, status)
    
    def update_review_modified_status(self):
        """更新修改状态显示"""
        if self.review_modified:
            modified_count = sum(1 for entry in self.review_entries 
                               if entry.current_status != "corrected")
            self.review_modified_label.config(text=f"有 {modified_count} 处修改，未保存")
        else:
            self.review_modified_label.config(text="")
    
    
    def on_review_item_double_click(self, event):
        """处理审核表格双击事件"""
        selection = self.review_tree.selection()
        if not selection or not self.review_entries:
            return
            
        item = selection[0]
        item_values = self.review_tree.item(item)['values']
        entry_number = item_values[0]
        
        # 查找对应的条目
        target_entry = None
        for entry in self.review_entries:
            if entry.number == entry_number:
                target_entry = entry
                break
        
        if target_entry:
            self.open_edit_dialog(target_entry, item)
    
    def on_review_item_right_click(self, event):
        """处理审核表格右键菜单"""
        # 选中右键点击的项目
        item = self.review_tree.identify_row(event.y)
        if item:
            self.review_tree.selection_set(item)
            
            # 创建右键菜单
            context_menu = tk.Menu(self.root, tearoff=0)
            context_menu.add_command(label="采用AI纠错", command=lambda: self.set_entry_status(item, "corrected"))
            context_menu.add_command(label="保持原文", command=lambda: self.set_entry_status(item, "original"))
            context_menu.add_separator()
            context_menu.add_command(label="手动编辑", command=lambda: self.edit_entry(item))
            
            try:
                context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                context_menu.grab_release()

    def _get_review_restore_column_id(self):
        """返回Treeview中“恢复”列对应的列编号（如 '#8'），不存在则返回None。"""
        try:
            cols = list(self.review_tree["columns"])
            if "恢复" not in cols:
                return None
            idx = cols.index("恢复")
            return f"#{idx + 1}"
        except Exception:
            return None

    def _review_set_restore_indicator(self, item_id, show: bool):
        """在指定行的“恢复”列显示/隐藏 ↩ 指示。"""
        try:
            if not item_id:
                return
            if "恢复" not in self.review_tree["columns"]:
                return
            self.review_tree.set(item_id, "恢复", "↩" if show else "")
        except Exception:
            pass

    def _set_review_hover_item(self, item_id):
        """更新当前悬浮行，并维护“↩”显示。"""
        try:
            if item_id == getattr(self, "_review_hover_item", None):
                return
            prev = getattr(self, "_review_hover_item", None)
            if prev:
                self._review_set_restore_indicator(prev, False)
            self._review_hover_item = item_id
            if item_id:
                self._review_set_restore_indicator(item_id, True)
        except Exception:
            pass

    def _on_review_tree_motion(self, event):
        """Treeview鼠标移动：悬浮显示↩，并在↩上显示Tooltip。"""
        try:
            item = self.review_tree.identify_row(event.y)
            self._set_review_hover_item(item)

            restore_col_id = self._get_review_restore_column_id()
            col_id = self.review_tree.identify_column(event.x)
            if item and restore_col_id and col_id == restore_col_id:
                self._schedule_review_restore_tooltip(event.x_root, event.y_root)
            else:
                self._cancel_review_restore_tooltip()
        except Exception:
            self._cancel_review_restore_tooltip()

    def _on_review_tree_leave(self, event):
        """鼠标离开Treeview：清理悬浮↩与Tooltip。"""
        self._set_review_hover_item(None)
        self._cancel_review_restore_tooltip()

    def _on_review_tree_left_click(self, event):
        """单击↩：直接执行“保持原文”（恢复）。"""
        try:
            item = self.review_tree.identify_row(event.y)
            if not item:
                return
            restore_col_id = self._get_review_restore_column_id()
            col_id = self.review_tree.identify_column(event.x)
            if restore_col_id and col_id == restore_col_id:
                try:
                    self.review_tree.selection_set(item)
                except Exception:
                    pass
                self.set_entry_status(item, "original")
                self._cancel_review_restore_tooltip()
                return "break"
        except Exception:
            pass

    def _schedule_review_restore_tooltip(self, x_root, y_root):
        """在↩上悬浮时，延迟显示Tooltip，避免闪烁。"""
        try:
            if self._review_restore_tooltip is not None:
                return
            if self._review_restore_tooltip_after_id is not None:
                try:
                    self.root.after_cancel(self._review_restore_tooltip_after_id)
                except Exception:
                    pass
                self._review_restore_tooltip_after_id = None
            self._review_restore_tooltip_after_id = self.root.after(
                250, lambda: self._show_review_restore_tooltip(x_root, y_root)
            )
        except Exception:
            pass

    def _cancel_review_restore_tooltip(self):
        """取消延迟显示并隐藏Tooltip。"""
        try:
            if self._review_restore_tooltip_after_id is not None:
                try:
                    self.root.after_cancel(self._review_restore_tooltip_after_id)
                except Exception:
                    pass
                self._review_restore_tooltip_after_id = None
        finally:
            self._hide_review_restore_tooltip()

    def _show_review_restore_tooltip(self, x_root, y_root):
        try:
            self._review_restore_tooltip_after_id = None
            if self._review_restore_tooltip is not None:
                return
            tip = tk.Toplevel(self.root)
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            tip.wm_geometry(f"+{x_root + 12}+{y_root + 16}")
            label = ttk.Label(
                tip,
                text="保持原文",
                background="#ffffe0",
                relief="solid",
                borderwidth=1
            )
            label.pack()
            self._review_restore_tooltip = tip
        except Exception:
            self._hide_review_restore_tooltip()

    def _hide_review_restore_tooltip(self):
        try:
            if self._review_restore_tooltip is not None:
                try:
                    self._review_restore_tooltip.destroy()
                except Exception:
                    pass
                self._review_restore_tooltip = None
        except Exception:
            pass
    
    def set_entry_status(self, tree_item, new_status):
        """设置条目状态"""
        if not self.review_entries:
            return
            
        item_values = self.review_tree.item(tree_item)['values']
        entry_number = item_values[0]
        
        # 查找并更新对应的条目
        for entry in self.review_entries:
            if entry.number == entry_number:
                entry.current_status = new_status
                if new_status == "corrected":
                    entry.edited_content = entry.corrected_content
                elif new_status == "original":
                    entry.edited_content = entry.original_content
                break
        
        # 刷新显示并设置行高亮状态
        self.populate_review_tree()
        # 采用AI或保持原文，都视为已处理，并记录编号
        if new_status in ("original", "corrected"):
            if hasattr(self, 'review_processed_numbers'):
                self.review_processed_numbers.add(entry_number)
            self._mark_review_row_processed(entry_number)
        self.review_modified = True
        self.update_review_modified_status()
    
    def edit_entry(self, tree_item):
        """编辑条目内容"""
        if not self.review_entries:
            return
            
        item_values = self.review_tree.item(tree_item)['values']
        entry_number = item_values[0]
        
        # 查找对应的条目
        target_entry = None
        for entry in self.review_entries:
            if entry.number == entry_number:
                target_entry = entry
                break
        
        if target_entry:
            self.open_edit_dialog(target_entry, tree_item)

    def _mark_review_row_processed(self, entry_number):
        """将指定编号的表格行标记为已处理（淡绿背景）。"""
        try:
            for iid in self.review_tree.get_children(""):
                vals = self.review_tree.item(iid)['values']
                if vals and vals[0] == entry_number:
                    self.review_tree.item(iid, tags=("kept_original",))
                    break
        except Exception:
            pass
    
    def open_edit_dialog(self, entry, tree_item):
        """打开编辑对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title(f"编辑字幕条目 #{entry.number}")
        dialog.geometry("600x400")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # 主框架
        main_frame = ttk.Frame(dialog, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 原文显示
        ttk.Label(main_frame, text="原文：", font=('Microsoft YaHei', 10, 'bold')).pack(anchor=tk.W)
        original_text = tk.Text(main_frame, height=4, wrap=tk.WORD, state=tk.DISABLED)
        original_text.pack(fill=tk.X, pady=(0, 10))
        original_text.config(state=tk.NORMAL)
        original_text.insert(tk.END, entry.original_content)
        original_text.config(state=tk.DISABLED)
        
        # AI纠错结果显示  
        ttk.Label(main_frame, text="AI纠错后：", font=('Microsoft YaHei', 10, 'bold')).pack(anchor=tk.W)
        corrected_text = tk.Text(main_frame, height=4, wrap=tk.WORD, state=tk.DISABLED)
        corrected_text.pack(fill=tk.X, pady=(0, 10))
        corrected_text.config(state=tk.NORMAL)
        corrected_text.insert(tk.END, entry.corrected_content)
        corrected_text.config(state=tk.DISABLED)
        
        # 编辑区域
        ttk.Label(main_frame, text="手动编辑：", font=('Microsoft YaHei', 10, 'bold')).pack(anchor=tk.W)
        edit_text = tk.Text(main_frame, height=6, wrap=tk.WORD)
        edit_text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 预填充当前内容
        current_content = entry.edited_content if entry.current_status == "edited" else entry.corrected_content
        edit_text.insert(tk.END, current_content)
        edit_text.focus()
        
        # 按钮区域
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)
        
        def save_edit():
            new_content = edit_text.get(1.0, tk.END).strip()
            if new_content:
                entry.current_status = "edited"
                entry.edited_content = new_content
                self.populate_review_tree()
                # 手动编辑后也标记为已处理（淡绿）
                if hasattr(self, 'review_processed_numbers'):
                    self.review_processed_numbers.add(entry.number)
                self._mark_review_row_processed(entry.number)
                self.review_modified = True
                self.update_review_modified_status()
            dialog.destroy()
        
        def use_original():
            entry.current_status = "original"
            entry.edited_content = entry.original_content
            self.populate_review_tree()
            if hasattr(self, 'review_processed_numbers'):
                self.review_processed_numbers.add(entry.number)
            self._mark_review_row_processed(entry.number)
            self.review_modified = True
            self.update_review_modified_status()
            dialog.destroy()
        
        def use_corrected():
            entry.current_status = "corrected"
            entry.edited_content = entry.corrected_content
            self.populate_review_tree()
            if hasattr(self, 'review_processed_numbers'):
                self.review_processed_numbers.add(entry.number)
            self._mark_review_row_processed(entry.number)
            self.review_modified = True
            self.update_review_modified_status()
            dialog.destroy()
        
        ttk.Button(button_frame, text="保存编辑", command=save_edit).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="使用原文", command=use_original).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="使用AI纠错", command=use_corrected).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
    
    def save_review_modifications(self):
        """保存审核修改到纠错后文件"""
        if not self.review_entries or not hasattr(self, 'corrected_file_path'):
            messagebox.showerror("错误", "没有可保存的修改或未指定文件")
            return
            
        if not self.review_modified:
            messagebox.showinfo("提示", "没有需要保存的修改")
            return
        
        try:
            # 首先读取当前纠错后文件的所有条目
            corrected_dict = SubtitleDiffEngine.parse_srt_file(self.corrected_file_path)
            
            # 应用用户的修改
            for entry in self.review_entries:
                if entry.number in corrected_dict:
                    # 根据用户选择的状态更新内容
                    if entry.current_status == "original":
                        corrected_dict[entry.number]['content'] = entry.original_content
                    elif entry.current_status == "edited":
                        corrected_dict[entry.number]['content'] = entry.edited_content
                    elif entry.current_status == "corrected":
                        corrected_dict[entry.number]['content'] = entry.corrected_content
            
            # 重新写入文件
            self.write_srt_file(corrected_dict, self.corrected_file_path)
            
            # 重置修改状态
            self.review_modified = False
            self.update_review_modified_status()
            
            messagebox.showinfo("成功", f"修改已保存到文件：{self.corrected_file_path}")
            
        except Exception as e:
            messagebox.showerror("保存失败", f"保存修改时出错：{str(e)}")
    
    def write_srt_file(self, entries_dict: dict, file_path: str):
        """写入SRT文件"""
        with open(file_path, 'w', encoding='utf-8') as f:
            for number in sorted(entries_dict.keys()):
                entry = entries_dict[number]
                f.write(f"{number}\n")
                f.write(f"{entry['start_time']} --> {entry['end_time']}\n")
                f.write(f"{entry['content']}\n")
                if number != max(entries_dict.keys()):  # 不是最后一个条目
                    f.write("\n")

def main():
    root = tk.Tk()
    app = SRTGuiApp(root)
    root.mainloop()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main() 
