#!/usr/bin/env python
# -*- coding: utf-8 -*-

# 版本号
VERSION = "0.1.2"

import os
import sys
import json
import time
import threading
import logging
import tkinter as tk
from tkinter import filedialog, messagebox, StringVar, IntVar, BooleanVar
import customtkinter as ctk
from typing import Dict, List, Optional, Tuple, Union, Any
import queue
import concurrent.futures
import re

# 导入原始翻译器和检查器模块
from srt_translator import SRTTranslator, TranslationAPI, API_ENDPOINTS, DEFAULT_API_KEY, DEFAULT_API_TYPE, DEFAULT_MODELS
import srt_checker

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("srt_translator_gui.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SRT-Translator-GUI")

# 设置GUI主题
ctk.set_appearance_mode("System")  # 系统主题
ctk.set_default_color_theme("blue")  # 默认蓝色主题

# 配置文件路径
CONFIG_FILE = "srt_translator_gui_config.json"

class ToolTip:
    """为控件添加鼠标悬停提示的工具类"""
    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tooltip_window = None
        self.widget.bind("<Enter>", self.on_enter)
        self.widget.bind("<Leave>", self.on_leave)
        self.widget.bind("<Motion>", self.on_motion)
        self.after_id = None

    def on_enter(self, event=None):
        """鼠标进入控件时的处理"""
        self.after_id = self.widget.after(self.delay, self.show_tooltip)

    def on_leave(self, event=None):
        """鼠标离开控件时的处理"""
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None
        self.hide_tooltip()

    def on_motion(self, event=None):
        """鼠标在控件内移动时的处理"""
        if self.tooltip_window:
            self.update_tooltip_position(event)

    def show_tooltip(self):
        """显示提示框"""
        if self.tooltip_window:
            return
        
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        
        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")
        
        # 设置提示框样式 - 增大字体
        label = tk.Label(
            self.tooltip_window,
            text=self.text,
            background="#ffffe0",
            foreground="#000000",
            relief="solid",
            borderwidth=1,
            font=("Microsoft YaHei UI", 11, "normal"),  # 使用微软雅黑字体，增大到11号
            wraplength=350,  # 增加换行宽度
            justify='left',
            padx=8,  # 增加内边距
            pady=5
        )
        label.pack()

    def update_tooltip_position(self, event):
        """更新提示框位置"""
        if self.tooltip_window:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
            self.tooltip_window.wm_geometry(f"+{x}+{y}")

    def hide_tooltip(self):
        """隐藏提示框"""
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None

class ScrollableTextFrame(ctk.CTkFrame):
    """可滚动的文本框框架"""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        # 创建文本框和滚动条 - 增加文本框高度以提供更好的日志显示体验
        self.textbox = ctk.CTkTextbox(self, wrap="word", height=600)  # 从400增加到600
        self.textbox.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        
    def insert_text(self, text):
        """插入文本到文本框"""
        self.textbox.configure(state="normal")
        self.textbox.insert("end", text + "\n")
        self.textbox.see("end")
        self.textbox.configure(state="disabled")
        
    def clear_text(self):
        """清空文本框"""
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")

class ConfigManager:
    """配置管理器，负责保存和加载配置"""
    def __init__(self, config_file=CONFIG_FILE):
        self.config_file = config_file
        self.default_config = {
            "api_key": "",
            "model": "deepseek-chat",
            "api_endpoint": "https://api.deepseek.com/v1/chat/completions",
            "batch_size": 5,
            "context_size": 2,
            "threads": 1,
            "last_input_dir": "",
            "last_output_dir": ""
        }
        self.config = self.load_config()
    
    def load_config(self) -> Dict:
        """加载配置文件"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                logger.info(f"已加载配置文件: {self.config_file}")
                return {**self.default_config, **config}  # 合并默认配置和加载的配置
            except Exception as e:
                logger.error(f"加载配置文件出错: {e}")
                return self.default_config.copy()
        return self.default_config.copy()
    
    def save_config(self, config: Dict) -> None:
        """保存配置到文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存配置到文件: {self.config_file}")
        except Exception as e:
            logger.error(f"保存配置文件出错: {e}")
    
    def get_config(self) -> Dict:
        """获取当前配置"""
        return self.config
    
    def update_config(self, new_config: Dict) -> None:
        """更新配置"""
        self.config.update(new_config)
        self.save_config(self.config)

class GUILogger:
    """GUI日志处理器，将日志重定向到GUI"""
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.queue = queue.Queue(maxsize=1000)  # 限制队列大小防止内存泄露
        self.running = True
        self.check_queue()
    
    def write(self, message):
        """写入消息到队列"""
        try:
            if message and not message.isspace():
                # 如果队列满了，丢弃最旧的消息
                if self.queue.full():
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        pass
                self.queue.put_nowait(message)
        except Exception as e:
            # 静默处理，避免影响主程序
            pass
    
    def flush(self):
        """刷新输出（必须实现以兼容logging模块）"""
        pass
    
    def check_queue(self):
        """检查队列并更新GUI"""
        try:
            processed_count = 0
            max_process_per_cycle = 10  # 每次最多处理10条消息，避免界面卡顿
            
            while not self.queue.empty() and processed_count < max_process_per_cycle:
                try:
                    message = self.queue.get_nowait()
                    if self.text_widget and hasattr(self.text_widget, 'insert_text'):
                        # 限制单条消息长度，避免界面卡顿
                        if len(message) > 1000:
                            message = message[:1000] + "...[消息过长，已截断]"
                        self.text_widget.insert_text(message.strip())
                    processed_count += 1
                except queue.Empty:
                    break
                except Exception as e:
                    # 记录错误但不影响程序运行
                    print(f"GUI日志更新错误: {e}")
                    break
        except Exception as e:
            print(f"GUI日志检查队列错误: {e}")
        
        if self.running:
            try:
                self.text_widget.after(100, self.check_queue)
            except Exception as e:
                print(f"GUI日志调度错误: {e}")
                self.running = False
    
    def stop(self):
        """停止队列检查"""
        self.running = False
        # 清空队列
        try:
            while not self.queue.empty():
                self.queue.get_nowait()
        except Exception:
            pass


class TranslationTab(ctk.CTkFrame):
    """翻译选项卡，包含翻译设置和控制"""
    def __init__(self, master, config_manager, **kwargs):
        super().__init__(master, **kwargs)
        self.config_manager = config_manager
        self.config = config_manager.get_config()
        self.translator = None
        self.translation_thread = None
        self.cancel_event = threading.Event()
        
        # 创建控件变量
        self.input_file_var = StringVar(value="")
        self.output_file_var = StringVar(value="")
        self.api_key_var = StringVar(value=self.config["api_key"])
        self.model_var = StringVar(value=self.config["model"])
        self.api_endpoint_var = StringVar(value=self.config["api_endpoint"])
        self.batch_size_var = IntVar(value=self.config["batch_size"])
        self.context_size_var = IntVar(value=self.config["context_size"])
        self.threads_var = IntVar(value=self.config["threads"])
        self.use_range_var = BooleanVar(value=False)
        self.start_num_var = StringVar(value="")
        self.end_num_var = StringVar(value="")
        self.resume_var = BooleanVar(value=True)
        self.show_api_key_var = BooleanVar(value=False)
        
        # 设置布局
        self.setup_ui()
        
        # 监听输入文件变化
        self.input_file_var.trace_add('write', self.on_input_file_change)
    
    def setup_ui(self):
        """设置用户界面"""
        # 设置网格布局 - 优化权重分配
        self.grid_columnconfigure(0, weight=1)      # 左侧设置面板
        self.grid_columnconfigure(1, weight=4)      # 右侧操作+日志区域，增加权重
        self.grid_rowconfigure(1, weight=1)         # 为日志区域所在行设置权重
        self.grid_rowconfigure(2, weight=2)         # 增加第二行的权重，主要给日志区域更多空间
        
        # ====== 左侧面板 - 设置 ======
        settings_frame = ctk.CTkFrame(self)
        settings_frame.grid(row=0, column=0, rowspan=3, padx=10, pady=10, sticky="nsew")
        
        settings_frame.grid_columnconfigure(0, weight=1)
        settings_frame.grid_rowconfigure(9, weight=1)
        
        # 标题 - 减小上下间距
        settings_title = ctk.CTkLabel(settings_frame, text="翻译设置", font=ctk.CTkFont(size=16, weight="bold"))
        settings_title.grid(row=0, column=0, padx=10, pady=(5, 10), sticky="w")
        
        # API设置框架
        api_frame = ctk.CTkFrame(settings_frame)
        api_frame.grid(row=1, column=0, padx=10, pady=(0, 5), sticky="ew")
        api_frame.grid_columnconfigure(1, weight=1)
        
        # API地址
        api_endpoint_label = ctk.CTkLabel(api_frame, text="API地址:")
        api_endpoint_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        api_endpoint_entry = ctk.CTkEntry(api_frame, textvariable=self.api_endpoint_var)
        api_endpoint_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        # 添加tooltip
        ToolTip(api_endpoint_label, "API服务的完整URL地址\n例如：https://api.deepseek.com/v1/chat/completions")
        ToolTip(api_endpoint_entry, "API服务的完整URL地址\n例如：https://api.deepseek.com/v1/chat/completions")
        
        # API密钥
        api_key_label = ctk.CTkLabel(api_frame, text="API密钥:")
        api_key_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        api_key_frame = ctk.CTkFrame(api_frame, fg_color="transparent")
        api_key_frame.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        api_key_frame.grid_columnconfigure(0, weight=1)
        
        self.api_key_entry = ctk.CTkEntry(api_key_frame, textvariable=self.api_key_var, show="*")
        self.api_key_entry.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        
        show_key_button = ctk.CTkButton(api_key_frame, text="👁", width=30, 
                                       command=self.toggle_api_key_visibility)
        show_key_button.grid(row=0, column=1)
        
        # 添加tooltip
        ToolTip(api_key_label, "用于访问AI翻译服务的密钥\n需要从API服务商处获取")
        ToolTip(self.api_key_entry, "用于访问AI翻译服务的密钥\n需要从API服务商处获取")
        ToolTip(show_key_button, "点击切换密钥显示/隐藏")
        
        # 模型名称
        model_label = ctk.CTkLabel(api_frame, text="模型名称:")
        model_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        model_entry = ctk.CTkEntry(api_frame, textvariable=self.model_var)
        model_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        
        # 添加tooltip
        ToolTip(model_label, "要使用的AI模型名称\n例如：deepseek-chat, gpt-4o, claude-3.5-sonnet等")
        ToolTip(model_entry, "要使用的AI模型名称\n例如：deepseek-chat, gpt-4o, claude-3.5-sonnet等")
        
        # 翻译参数框架
        params_frame = ctk.CTkFrame(settings_frame)
        params_frame.grid(row=2, column=0, padx=10, pady=(0, 5), sticky="ew")
        params_frame.grid_columnconfigure(1, weight=1)
        
        # 批次大小
        batch_size_label = ctk.CTkLabel(params_frame, text="批次大小:")
        batch_size_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        batch_size_entry = ctk.CTkEntry(params_frame, textvariable=self.batch_size_var)
        batch_size_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        # 添加tooltip
        ToolTip(batch_size_label, "每次请求翻译的字幕条数\n较大的值可以提高效率，但可能会增加翻译错误的概率\n建议值：5-30")
        ToolTip(batch_size_entry, "每次请求翻译的字幕条数\n较大的值可以提高效率，但可能会增加翻译错误的概率\n建议值：5-30")
        
        # 上下文大小
        context_size_label = ctk.CTkLabel(params_frame, text="上下文大小:")
        context_size_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        context_size_entry = ctk.CTkEntry(params_frame, textvariable=self.context_size_var)
        context_size_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        
        # 添加tooltip
        ToolTip(context_size_label, "提供给AI的前后文字幕条数\n有助于提高翻译的一致性和准确性\n建议值：2-5，设为0则不使用上下文")
        ToolTip(context_size_entry, "提供给AI的前后文字幕条数\n有助于提高翻译的一致性和准确性\n建议值：2-5，设为0则不使用上下文")
        
        # 线程数
        threads_label = ctk.CTkLabel(params_frame, text="线程数:")
        threads_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        threads_entry = ctk.CTkEntry(params_frame, textvariable=self.threads_var)
        threads_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        
        # 添加tooltip
        ToolTip(threads_label, "并发翻译的线程数\n增加线程数可以加快翻译速度，但会增加API调用频率\n建议值：1-10，根据API限制调整")
        ToolTip(threads_entry, "并发翻译的线程数\n增加线程数可以加快翻译速度，但会增加API调用频率\n建议值：1-10，根据API限制调整")
        
        # 范围选择框架
        range_frame = ctk.CTkFrame(settings_frame)
        range_frame.grid(row=3, column=0, padx=10, pady=(0, 5), sticky="ew")
        
        # 使用范围翻译复选框
        use_range_checkbox = ctk.CTkCheckBox(range_frame, text="翻译范围（可选）", variable=self.use_range_var, 
                                             command=self.toggle_range_inputs)
        use_range_checkbox.grid(row=0, column=0, columnspan=2, padx=10, pady=5, sticky="w")
        
        # 添加tooltip
        ToolTip(use_range_checkbox, "只翻译指定范围内的字幕条目\n可用于测试或翻译部分内容\n不勾选则翻译全部字幕")
        
        # 起始编号和结束编号（默认禁用）
        self.start_label = ctk.CTkLabel(range_frame, text="起始编号:")
        self.start_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.start_entry = ctk.CTkEntry(range_frame, textvariable=self.start_num_var, state="disabled")
        self.start_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        
        # 添加tooltip
        ToolTip(self.start_label, "翻译范围的起始字幕编号\n必须大于0且小于等于结束编号")
        ToolTip(self.start_entry, "翻译范围的起始字幕编号\n必须大于0且小于等于结束编号")
        
        self.end_label = ctk.CTkLabel(range_frame, text="结束编号:")
        self.end_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.end_entry = ctk.CTkEntry(range_frame, textvariable=self.end_num_var, state="disabled")
        self.end_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        
        # 添加tooltip
        ToolTip(self.end_label, "翻译范围的结束字幕编号\n必须大于等于起始编号")
        ToolTip(self.end_entry, "翻译范围的结束字幕编号\n必须大于等于起始编号")
        
        # 断点续接复选框
        resume_checkbox = ctk.CTkCheckBox(range_frame, text="断点续接（建议勾选）", variable=self.resume_var)
        resume_checkbox.grid(row=3, column=0, columnspan=2, padx=10, pady=5, sticky="w")
        
        # 添加tooltip
        ToolTip(resume_checkbox, "从上次中断的地方继续翻译\n避免重复翻译已完成的部分\n强烈建议保持勾选状态")
        
        # 添加保存设置按钮
        save_settings_button = ctk.CTkButton(settings_frame, text="保存设置", 
                                         command=self.update_config,
                                         fg_color="#17a2b8", hover_color="#138496")
        save_settings_button.grid(row=4, column=0, padx=10, pady=(10, 5), sticky="ew")
        
        # 添加tooltip
        ToolTip(save_settings_button, "保存当前设置到配置文件\n下次启动时会自动加载")
        
        # ====== 右侧面板 - 操作区域 ======
        operation_frame = ctk.CTkFrame(self)
        operation_frame.grid(row=0, column=1, rowspan=1, padx=10, pady=10, sticky="nsew")
        operation_frame.grid_columnconfigure(0, weight=1)
        
        # 标题 - 减小上下间距
        operation_title = ctk.CTkLabel(operation_frame, text="文件选择", font=ctk.CTkFont(size=16, weight="bold"))
        operation_title.grid(row=0, column=0, padx=10, pady=(5, 10), sticky="w")
        
        # 输入文件选择
        input_frame = ctk.CTkFrame(operation_frame)
        input_frame.grid(row=1, column=0, padx=10, pady=(0, 5), sticky="ew")
        input_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(input_frame, text="输入SRT文件:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        input_file_frame = ctk.CTkFrame(input_frame, fg_color="transparent")
        input_file_frame.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        input_file_frame.grid_columnconfigure(0, weight=1)
        
        input_file_entry = ctk.CTkEntry(input_file_frame, textvariable=self.input_file_var)
        input_file_entry.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        
        input_file_button = ctk.CTkButton(input_file_frame, text="浏览", width=60, 
                                         command=lambda: self.browse_file(self.input_file_var, "选择输入SRT文件", 
                                                                      filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]))
        input_file_button.grid(row=0, column=1)
        
        # 输出文件选择
        output_frame = ctk.CTkFrame(operation_frame)
        output_frame.grid(row=2, column=0, padx=10, pady=(0, 5), sticky="ew")
        output_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(output_frame, text="输出SRT文件:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        output_file_frame = ctk.CTkFrame(output_frame, fg_color="transparent")
        output_file_frame.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        output_file_frame.grid_columnconfigure(0, weight=1)
        
        output_file_entry = ctk.CTkEntry(output_file_frame, textvariable=self.output_file_var)
        output_file_entry.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        
        output_file_button = ctk.CTkButton(output_file_frame, text="浏览", width=60, 
                                          command=lambda: self.browse_file(self.output_file_var, "选择输出SRT文件", 
                                                                       filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")],
                                                                       save=True))
        output_file_button.grid(row=0, column=1)
        
        # 操作按钮
        buttons_frame = ctk.CTkFrame(operation_frame)
        buttons_frame.grid(row=3, column=0, padx=10, pady=(0, 5), sticky="ew")
        buttons_frame.grid_columnconfigure((0, 1), weight=1)
        
        self.translate_button = ctk.CTkButton(buttons_frame, text="开始翻译", 
                                     command=self.start_translation,
                                     fg_color="#28a745", hover_color="#218838")
        self.translate_button.grid(row=0, column=0, padx=10, pady=10, sticky="ew")

        self.cancel_button = ctk.CTkButton(buttons_frame, text="取消翻译", 
                                      command=self.cancel_translation,
                                      fg_color="#dc3545", hover_color="#c82333", state="disabled")
        self.cancel_button.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

        # 进度条
        progress_frame = ctk.CTkFrame(operation_frame)
        progress_frame.grid(row=4, column=0, padx=10, pady=(0, 5), sticky="ew")
        progress_frame.grid_columnconfigure(0, weight=1)

        self.progress_label = ctk.CTkLabel(progress_frame, text="准备就绪")
        self.progress_label.grid(row=0, column=0, padx=5, pady=(5, 2), sticky="w")

        self.progress_bar = ctk.CTkProgressBar(progress_frame)
        self.progress_bar.grid(row=1, column=0, padx=5, pady=(2, 5), sticky="ew")
        self.progress_bar.set(0)

        # ====== 日志区域 ======
        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=1, column=1, rowspan=2, padx=10, pady=5, sticky="nsew")
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=8)  # 大幅增加日志区域的比例权重

        # 日志区域标题和清空按钮框架
        log_header_frame = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_header_frame.grid(row=0, column=0, padx=10, pady=5, sticky="ew")
        log_header_frame.grid_columnconfigure(1, weight=1)

        log_title = ctk.CTkLabel(log_header_frame, text="日志输出", font=ctk.CTkFont(size=16, weight="bold"))
        log_title.grid(row=0, column=0, padx=0, pady=0, sticky="w")

        clear_log_button = ctk.CTkButton(log_header_frame, text="清空日志", width=80, 
                                     command=lambda: self.log_text.clear_text())
        clear_log_button.grid(row=0, column=1, padx=10, pady=0, sticky="e")

        self.log_text = ScrollableTextFrame(log_frame)
        self.log_text.grid(row=1, column=0, padx=10, pady=(0, 5), sticky="nsew")

        # 创建GUI日志处理器
        self.gui_logger = GUILogger(self.log_text)

        # 初始化界面设置
        self.on_input_file_change()

    def on_input_file_change(self, *args):
        """当输入文件变化时自动设置输出文件"""
        input_file = self.input_file_var.get().strip()
        if input_file and os.path.exists(input_file):
            # 获取输入文件的目录和基础名称
            input_dir = os.path.dirname(input_file)
            input_name = os.path.basename(input_file)
            
            # 生成输出文件名：输入文件名_cn.srt
            if input_name.lower().endswith('.srt'):
                output_name = input_name[:-4] + '_cn.srt'
            else:
                output_name = input_name + '_cn.srt'
            
            output_file = os.path.join(input_dir, output_name)
            self.output_file_var.set(output_file)
    
    def toggle_api_key_visibility(self):
        """切换API密钥输入框的可见性"""
        self.show_api_key_var.set(not self.show_api_key_var.get())
        if self.show_api_key_var.get():
            self.api_key_entry.configure(show="")
        else:
            self.api_key_entry.configure(show="*")
    
    def toggle_range_inputs(self):
        """切换范围输入框的启用状态"""
        state = "normal" if self.use_range_var.get() else "disabled"
        self.start_entry.configure(state=state)
        self.end_entry.configure(state=state)
    
    def browse_file(self, var, title, filetypes, save=False):
        """浏览选择文件"""
        try:
            initial_dir = self.config.get("last_input_dir", "") if not save else self.config.get("last_output_dir", "")
            
            # 验证初始目录
            if not initial_dir or not os.path.exists(initial_dir):
                initial_dir = os.getcwd()
            
            # 确保初始目录可访问
            try:
                os.listdir(initial_dir)
            except (PermissionError, OSError):
                initial_dir = os.path.expanduser("~")  # 使用用户主目录
                if not os.path.exists(initial_dir):
                    initial_dir = os.getcwd()
            
            file_path = None
            if save:
                file_path = filedialog.asksaveasfilename(
                    title=title,
                    filetypes=filetypes,
                    initialdir=initial_dir
                )
            else:
                file_path = filedialog.askopenfilename(
                    title=title,
                    filetypes=filetypes,
                    initialdir=initial_dir
                )
            
            if file_path:
                # 验证路径
                if save:
                    # 对于保存文件，检查目录是否可写
                    parent_dir = os.path.dirname(file_path)
                    if parent_dir and not os.path.exists(parent_dir):
                        try:
                            os.makedirs(parent_dir, exist_ok=True)
                        except Exception as e:
                            messagebox.showerror("错误", f"无法创建目录: {parent_dir}\n{e}")
                            return
                        
                        # 测试写入权限
                        try:
                            test_file = file_path + ".tmp"
                            with open(test_file, 'w') as f:
                                f.write("test")
                            os.remove(test_file)
                        except Exception as e:
                            messagebox.showerror("错误", f"目标位置不可写入: {file_path}\n{e}")
                            return
                else:
                    # 对于打开文件，检查文件是否存在和可读
                    if not os.path.exists(file_path):
                        messagebox.showerror("错误", f"文件不存在: {file_path}")
                        return
                    
                    # 测试文件读取权限和编码 - 尝试多种编码
                    try:
                        # 首先尝试UTF-8编码
                        with open(file_path, 'r', encoding='utf-8') as f:
                            f.read(100)  # 读取前100个字符测试
                    except UnicodeDecodeError:
                        # 如果UTF-8失败，尝试其他常见编码
                        encodings_to_try = ['gbk', 'gb2312', 'latin1', 'cp1252']
                        file_readable = False
                        for encoding in encodings_to_try:
                            try:
                                with open(file_path, 'r', encoding=encoding) as f:
                                    f.read(100)
                                file_readable = True
                                break
                            except (UnicodeDecodeError, UnicodeError):
                                continue
                        
                        if not file_readable:
                            # 如果所有编码都失败，尝试二进制读取测试权限
                            try:
                                with open(file_path, 'rb') as f:
                                    f.read(1)
                                # 文件可读但编码可能有问题，给出警告但不阻止
                                result = messagebox.askyesno("编码警告", 
                                    f"文件可能存在编码问题，但仍可以尝试翻译。\n文件: {os.path.basename(file_path)}\n\n是否继续选择此文件？")
                                if not result:
                                    return
                            except Exception as e:
                                messagebox.showerror("错误", f"文件不可读取: {file_path}\n{e}")
                                return
                    except Exception as e:
                        messagebox.showerror("错误", f"文件不可读取: {file_path}\n{e}")
                        return
                
                var.set(file_path)
                
                # 更新最后使用的目录
                try:
                    dir_path = os.path.dirname(file_path)
                    if dir_path and os.path.exists(dir_path):
                        if save:
                            self.config["last_output_dir"] = dir_path
                        else:
                            self.config["last_input_dir"] = dir_path
                        
                        self.config_manager.update_config(self.config)
                except Exception as e:
                    logger.warning(f"更新最后使用目录失败: {e}")
                    
        except Exception as e:
            logger.error(f"浏览文件时出错: {e}")
            messagebox.showerror("错误", f"浏览文件时出现错误: {e}")
    
    def validate_inputs(self):
        """验证输入的有效性"""
        try:
            # 检查输入文件
            input_file = self.input_file_var.get().strip()
            if not input_file:
                messagebox.showerror("错误", "请选择输入SRT文件")
                return False
            
            if not os.path.exists(input_file):
                messagebox.showerror("错误", f"输入文件不存在: {input_file}")
                return False
            
            # 检查文件扩展名
            if not input_file.lower().endswith('.srt'):
                result = messagebox.askyesno("警告", "选择的文件不是.srt格式，确定要继续吗？")
                if not result:
                    return False
            
            # 检查文件大小和可读性
            try:
                file_size = os.path.getsize(input_file)
                if file_size == 0:
                    messagebox.showerror("错误", "输入文件为空")
                    return False
                if file_size > 100 * 1024 * 1024:  # 100MB限制
                    result = messagebox.askyesno("警告", f"文件很大({file_size/1024/1024:.1f}MB)，翻译可能需要很长时间，确定要继续吗？")
                    if not result:
                        return False
                
                # 尝试读取文件 - 支持多种编码
                file_readable = False
                try:
                    # 首先尝试UTF-8编码
                    with open(input_file, 'r', encoding='utf-8') as f:
                        f.read(1024)  # 读取前1024字节测试可读性
                    file_readable = True
                except UnicodeDecodeError:
                    # 如果UTF-8失败，尝试其他常见编码
                    encodings_to_try = ['gbk', 'gb2312', 'latin1', 'cp1252']
                    for encoding in encodings_to_try:
                        try:
                            with open(input_file, 'r', encoding=encoding) as f:
                                f.read(1024)
                            file_readable = True
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue
                
                if not file_readable:
                    # 给出警告但允许继续
                    result = messagebox.askyesno("编码警告", 
                        f"文件编码可能有问题，但仍可以尝试翻译。\n是否继续？")
                    if not result:
                        return False
            except PermissionError:
                messagebox.showerror("错误", f"无法访问输入文件: {input_file}\n请检查文件权限")
                return False
            except Exception as e:
                messagebox.showerror("错误", f"读取输入文件时出错: {e}")
                return False
            
            # 检查输出文件
            output_file = self.output_file_var.get().strip()
            if not output_file:
                messagebox.showerror("错误", "请选择输出SRT文件")
                return False
            
            # 检查输出目录
            output_dir = os.path.dirname(output_file)
            if output_dir and not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir, exist_ok=True)
                except Exception as e:
                    messagebox.showerror("错误", f"无法创建输出目录: {output_dir}\n{e}")
                    return False
            
            # 检查输出文件写入权限
            try:
                # 测试写入权限
                test_file = output_file + ".tmp"
                with open(test_file, 'w', encoding='utf-8') as f:
                    f.write("test")
                os.remove(test_file)
            except Exception as e:
                messagebox.showerror("错误", f"无法写入输出文件: {output_file}\n{e}")
                return False
            
            # 检查API密钥
            api_key = self.api_key_var.get().strip()
            if not api_key:
                messagebox.showerror("错误", "请输入API密钥")
                return False
            
            if len(api_key) < 10:
                messagebox.showerror("错误", "API密钥长度太短，请检查是否正确")
                return False
            
            # 检查自定义API端点
            api_endpoint = self.api_endpoint_var.get().strip()
            if not api_endpoint:
                messagebox.showerror("错误", "请指定API地址")
                return False
            
            # 验证URL格式
            if not (api_endpoint.startswith('http://') or api_endpoint.startswith('https://')):
                messagebox.showerror("错误", "API地址必须以http://或https://开头")
                return False
            
            # 检查批次大小、上下文大小和线程数
            try:
                batch_size = int(self.batch_size_var.get())
                if batch_size <= 0:
                    messagebox.showerror("错误", "批次大小必须大于0")
                    return False
                if batch_size > 100:
                    result = messagebox.askyesno("警告", f"批次大小({batch_size})很大，可能会导致API请求失败，建议设为30以下，确定要继续吗？")
                    if not result:
                        return False
            except (ValueError, tk.TclError):
                messagebox.showerror("错误", "批次大小必须是有效的整数")
                return False
            
            try:
                context_size = int(self.context_size_var.get())
                if context_size < 0:
                    messagebox.showerror("错误", "上下文大小不能为负数")
                    return False
                if context_size > 20:
                    result = messagebox.askyesno("警告", f"上下文大小({context_size})很大，可能会影响翻译质量，建议设为10以下，确定要继续吗？")
                    if not result:
                        return False
            except (ValueError, tk.TclError):
                messagebox.showerror("错误", "上下文大小必须是有效的整数")
                return False
            
            try:
                threads = int(self.threads_var.get())
                if threads <= 0:
                    messagebox.showerror("错误", "线程数必须大于0")
                    return False
                if threads > 20:
                    result = messagebox.askyesno("警告", f"线程数({threads})很大，可能会导致API频率限制，建议设为10以下，确定要继续吗？")
                    if not result:
                        return False
            except (ValueError, tk.TclError):
                messagebox.showerror("错误", "线程数必须是有效的整数")
                return False
            
            # 检查范围翻译参数
            if self.use_range_var.get():
                start_num = self.start_num_var.get().strip()
                end_num = self.end_num_var.get().strip()
                
                if not start_num or not end_num:
                    messagebox.showerror("错误", "使用范围翻译时必须同时指定起始编号和结束编号")
                    return False
                
                try:
                    start_num = int(start_num)
                    end_num = int(end_num)
                    
                    if start_num <= 0 or end_num <= 0:
                        messagebox.showerror("错误", "字幕编号必须大于0")
                        return False
                    
                    if start_num > end_num:
                        messagebox.showerror("错误", "起始编号不能大于结束编号")
                        return False
                    
                    if end_num - start_num > 10000:
                        result = messagebox.askyesno("警告", f"翻译范围很大({start_num}-{end_num})，可能需要很长时间，确定要继续吗？")
                        if not result:
                            return False
                            
                except (ValueError, tk.TclError):
                    messagebox.showerror("错误", "字幕编号必须是有效的整数")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"验证输入时出现未预期的错误: {e}")
            messagebox.showerror("错误", f"验证输入时出现错误: {e}")
            return False
    
    def start_translation(self):
        """开始翻译任务"""
        if not self.validate_inputs():
            return
        
        if self.translation_thread and self.translation_thread.is_alive():
            messagebox.showinfo("提示", "翻译任务已在进行中")
            return
        
        # 静默更新配置（不显示提示消息）
        self.update_config_silent()
        
        # 准备翻译参数
        input_file = self.input_file_var.get().strip()
        output_file = self.output_file_var.get().strip()
        api_key = self.api_key_var.get().strip()
        model = self.model_var.get().strip() or None
        batch_size = int(self.batch_size_var.get())
        context_size = int(self.context_size_var.get())
        threads = int(self.threads_var.get())
        resume = self.resume_var.get()
        
        # 范围翻译参数
        start_num = None
        end_num = None
        if self.use_range_var.get():
            start_num = int(self.start_num_var.get().strip())
            end_num = int(self.end_num_var.get().strip())
        
        # 设置自定义API端点
        api_endpoint = self.api_endpoint_var.get().strip()
        API_ENDPOINTS["custom"] = api_endpoint
        if model:
            DEFAULT_MODELS["custom"] = model
        
        # 重置取消事件
        self.cancel_event.clear()
        
        # 更新UI状态
        self.translate_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_bar.set(0)
        self.progress_label.configure(text="准备翻译...")
        # 不再自动清空日志，而是添加分隔符标识新的翻译任务
        self.log_text.insert_text("\n" + "="*50)
        self.log_text.insert_text(f"开始新的翻译任务 - {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.log_text.insert_text("="*50)
        
        # 创建翻译器实例，使用custom API类型
        self.translator = SRTTranslator("custom", api_key, batch_size, context_size, threads, model)
        
        # 启动翻译线程
        self.translation_thread = threading.Thread(
            target=self.translation_task,
            args=(input_file, output_file, resume, start_num, end_num),
            daemon=True
        )
        self.translation_thread.start()
        
        # 启动进度更新
        self.after(100, self.update_progress)
    
    def update_config_silent(self):
        """静默更新配置（不显示提示消息）"""
        try:
            # 将当前设置保存到配置中
            self.config["api_key"] = self.api_key_var.get().strip()
            self.config["model"] = self.model_var.get().strip()
            self.config["api_endpoint"] = self.api_endpoint_var.get().strip()
            self.config["batch_size"] = int(self.batch_size_var.get())
            self.config["context_size"] = int(self.context_size_var.get())
            self.config["threads"] = int(self.threads_var.get())
            
            # 静默保存到配置文件
            self.config_manager.update_config(self.config)
            logger.debug("配置已静默更新")
        except Exception as e:
            logger.error(f"静默更新配置失败: {e}")
    
    def update_config(self):
        """更新配置（显示提示消息）"""
        try:
            # 将当前设置保存到配置中
            self.config["api_key"] = self.api_key_var.get().strip()
            self.config["model"] = self.model_var.get().strip()
            self.config["api_endpoint"] = self.api_endpoint_var.get().strip()
            self.config["batch_size"] = int(self.batch_size_var.get())
            self.config["context_size"] = int(self.context_size_var.get())
            self.config["threads"] = int(self.threads_var.get())
            
            # 保存到配置文件
            self.config_manager.update_config(self.config)
            
            # 显示保存成功消息
            messagebox.showinfo("成功", "设置已保存到配置文件！\n下次启动时将自动加载这些设置。")
        except Exception as e:
            error_msg = f"保存配置失败: {e}"
            logger.error(error_msg)
            messagebox.showerror("错误", f"保存配置时出现错误:\n{error_msg}")
    
    def translation_task(self, input_file, output_file, resume, start_num, end_num):
        """执行翻译任务（在单独的线程中运行）"""
        gui_handler = None
        try:
            # 创建自定义日志处理器
            gui_handler = logging.StreamHandler(self.gui_logger)
            gui_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
            translator_logger = logging.getLogger("SRT-Translator")
            translator_logger.addHandler(gui_handler)
            
            # 记录开始信息
            logger.info(f"开始翻译任务: {os.path.basename(input_file)} -> {os.path.basename(output_file)}")
            
            # 检查翻译器是否正确初始化
            if not self.translator:
                raise RuntimeError("翻译器未正确初始化")
            
            # 再次验证文件
            if not os.path.exists(input_file):
                raise FileNotFoundError(f"输入文件不存在: {input_file}")
            
            # 翻译文件
            self.translator.translate_srt_file(
                input_file, output_file,
                resume=resume,
                start_num=start_num,
                end_num=end_num,
                cancel_event=self.cancel_event
            )
            
            # 检查是否被取消
            if self.cancel_event.is_set():
                logger.info("翻译任务已被用户取消")
                return
            
            # 验证输出文件
            if os.path.exists(output_file):
                file_size = os.path.getsize(output_file)
                if file_size > 0:
                    logger.info(f"翻译完成，输出文件大小: {file_size} 字节")
                    # 计算翻译的字幕条目数量（从文件名或其他方式获取更友好的信息）
                    try:
                        # 读取输出文件获取条目数
                        with open(output_file, 'r', encoding='utf-8') as f:
                            content = f.read()
                        # 简单计算字幕条目数量（通过数字编号行）
                        import re
                        subtitle_count = len(re.findall(r'^\d+$', content, re.MULTILINE))
                        
                        success_message = (
                            f"🎉 翻译成功完成！\n\n"
                            f"📁 输出文件：{os.path.basename(output_file)}\n"
                            f"📝 翻译条目：{subtitle_count} 条字幕\n"
                            f"📍 保存位置：{os.path.dirname(output_file)}\n\n"
                            f"✨ 可以开始享受翻译后的字幕了！"
                        )
                    except:
                        # 如果无法计算条目数，使用简化版本
                        success_message = (
                            f"🎉 翻译成功完成！\n\n"
                            f"📁 输出文件：{os.path.basename(output_file)}\n"
                            f"📍 保存位置：{os.path.dirname(output_file)}\n\n"
                            f"✨ 可以开始享受翻译后的字幕了！"
                        )
                    
                    self.after(0, lambda: messagebox.showinfo("翻译完成", success_message))
                else:
                    logger.warning("输出文件为空")
                    self.after(0, lambda: messagebox.showwarning("⚠️ 翻译警告", 
                        "翻译已完成，但输出文件为空。\n\n可能原因：\n• 输入文件没有有效的字幕内容\n• 所选翻译范围无效\n\n请检查输入文件和设置。"))
            else:
                logger.error("翻译完成但未生成输出文件")
                self.after(0, lambda: messagebox.showerror("❌ 翻译错误", 
                    "翻译过程已完成，但未能生成输出文件。\n\n请检查：\n• 输出目录的写入权限\n• 磁盘空间是否充足\n• 输出路径是否有效"))
            
        except FileNotFoundError as e:
            error_msg = f"文件不存在: {e}"
            logger.error(error_msg)
            self.after(0, lambda: messagebox.showerror("文件错误", error_msg))
        except PermissionError as e:
            error_msg = f"文件权限错误: {e}"
            logger.error(error_msg)
            self.after(0, lambda: messagebox.showerror("权限错误", error_msg))
        except ConnectionError as e:
            error_msg = f"网络连接错误，请检查网络和API设置: {e}"
            logger.error(error_msg)
            self.after(0, lambda: messagebox.showerror("网络错误", error_msg))
        except TimeoutError as e:
            error_msg = f"API请求超时，请稍后重试: {e}"
            logger.error(error_msg)
            self.after(0, lambda: messagebox.showerror("超时错误", error_msg))
        except Exception as e:
            error_msg = str(e)
            logger.error(f"翻译出错: {error_msg}", exc_info=True)
            
            # 根据错误类型提供更具体的建议
            if "API key" in error_msg.lower():
                suggestion = "\n\n建议：请检查API密钥是否正确"
            elif "rate limit" in error_msg.lower() or "quota" in error_msg.lower():
                suggestion = "\n\n建议：API调用频率过高或配额不足，请稍后重试或检查账户余额"
            elif "model" in error_msg.lower():
                suggestion = "\n\n建议：请检查模型名称是否正确"
            elif "network" in error_msg.lower() or "connection" in error_msg.lower():
                suggestion = "\n\n建议：请检查网络连接和API地址"
            else:
                suggestion = "\n\n建议：请检查日志了解详细错误信息"
            
            self.after(0, lambda: messagebox.showerror("翻译错误", f"翻译过程中出现错误:\n{error_msg}{suggestion}"))
        
        finally:
            # 清理资源
            if gui_handler:
                try:
                    translator_logger = logging.getLogger("SRT-Translator")
                    translator_logger.removeHandler(gui_handler)
                except Exception as e:
                    logger.error(f"移除日志处理器失败: {e}")
            
            # 重置翻译器引用
            if hasattr(self, 'translator'):
                self.translator = None
            
            # 恢复UI状态
            self.after(0, self.reset_ui)
    
    def update_progress(self):
        """更新进度显示"""
        if self.translator and self.translation_thread and self.translation_thread.is_alive():
            # 获取进度信息
            progress_manager = getattr(self.translator, "_current_progress_manager", None)
            if progress_manager:
                total = progress_manager.total_batches
                completed = len(progress_manager.completed_batches)
                
                if total > 0:
                    progress = completed / total
                    self.progress_bar.set(progress)
                    self.progress_label.configure(text=f"进度: {completed}/{total} 批次 ({progress*100:.1f}%)")
            
            # 继续定期更新
            self.after(500, self.update_progress)
        else:
            # 如果翻译已完成但UI尚未重置
            if not self.translate_button.cget("state") == "normal":
                self.reset_ui()
    
    def cancel_translation(self):
        """取消正在进行的翻译任务"""
        if self.translation_thread and self.translation_thread.is_alive():
            result = messagebox.askyesno("确认取消", "确定要取消当前的翻译任务吗？\n已翻译的部分仍将保存。")
            if result:
                self.cancel_event.set()
                self.progress_label.configure(text="正在取消...")
                self.log_text.insert_text("用户已取消翻译任务，正在等待当前批次完成...")
    
    def reset_ui(self):
        """重置UI状态"""
        self.translate_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        
        # 如果是由于取消而重置
        if self.cancel_event.is_set():
            self.progress_label.configure(text="已取消")
        else:
            self.progress_label.configure(text="准备就绪")
            self.progress_bar.set(1.0)  # 完成时设为100%


class CheckerTab(ctk.CTkFrame):
    """检查器选项卡，用于检查SRT文件的匹配情况"""
    def __init__(self, master, config_manager, **kwargs):
        super().__init__(master, **kwargs)
        self.config_manager = config_manager
        self.config = config_manager.get_config()
        self.checker_thread = None
        
        # 创建控件变量
        self.source_file_var = StringVar(value="")
        self.translated_file_var = StringVar(value="")
        self.report_file_var = StringVar(value="")
        self.generate_report_var = BooleanVar(value=False)
        
        # 设置布局
        self.setup_ui()
    
    def setup_ui(self):
        """设置用户界面"""
        # 设置网格布局
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=5)  # 进一步增加日志区域的比例
        
        # ====== 上半部分 - 文件选择和操作 ======
        control_frame = ctk.CTkFrame(self)
        control_frame.grid(row=0, column=0, padx=10, pady=5, sticky="nsew")
        control_frame.grid_columnconfigure(0, weight=1)
        
        # 标题 - 减小上下间距
        control_title = ctk.CTkLabel(control_frame, text="字幕文件检查", font=ctk.CTkFont(size=16, weight="bold"))
        control_title.grid(row=0, column=0, padx=10, pady=(5, 10), sticky="w")
        
        # 文件选择框架
        files_frame = ctk.CTkFrame(control_frame)
        files_frame.grid(row=1, column=0, padx=10, pady=(0, 5), sticky="ew")
        files_frame.grid_columnconfigure(1, weight=1)
        
        # 源文件
        ctk.CTkLabel(files_frame, text="源SRT文件:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        source_file_frame = ctk.CTkFrame(files_frame, fg_color="transparent")
        source_file_frame.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        source_file_frame.grid_columnconfigure(0, weight=1)
        
        source_file_entry = ctk.CTkEntry(source_file_frame, textvariable=self.source_file_var)
        source_file_entry.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        
        source_file_button = ctk.CTkButton(source_file_frame, text="浏览", width=60, 
                                           command=lambda: self.browse_file(self.source_file_var, "选择源SRT文件", 
                                                                           filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]))
        source_file_button.grid(row=0, column=1)
        
        # 翻译文件
        ctk.CTkLabel(files_frame, text="翻译SRT文件:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        translated_file_frame = ctk.CTkFrame(files_frame, fg_color="transparent")
        translated_file_frame.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        translated_file_frame.grid_columnconfigure(0, weight=1)
        
        translated_file_entry = ctk.CTkEntry(translated_file_frame, textvariable=self.translated_file_var)
        translated_file_entry.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        
        translated_file_button = ctk.CTkButton(translated_file_frame, text="浏览", width=60, 
                                               command=lambda: self.browse_file(self.translated_file_var, "选择翻译SRT文件", 
                                                                               filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]))
        translated_file_button.grid(row=0, column=1)
        
        # 移除生成报告功能 - 简化界面
        # 原来的第三行内容（报告相关功能）已被移除
        
        # 操作按钮
        buttons_frame = ctk.CTkFrame(control_frame)
        buttons_frame.grid(row=2, column=0, padx=10, pady=(0, 5), sticky="ew")
        buttons_frame.grid_columnconfigure(0, weight=1)
        
        self.check_button = ctk.CTkButton(buttons_frame, text="开始检查", 
                                     command=self.start_check,
                                     fg_color="#28a745", hover_color="#218838")
        self.check_button.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        
        # ====== 下半部分 - 结果区域 ======
        result_frame = ctk.CTkFrame(self)
        result_frame.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        result_frame.grid_columnconfigure(0, weight=1)
        result_frame.grid_rowconfigure(1, weight=1)
        
        # 结果标题和清空按钮框架
        result_header_frame = ctk.CTkFrame(result_frame, fg_color="transparent")
        result_header_frame.grid(row=0, column=0, padx=10, pady=5, sticky="ew")
        result_header_frame.grid_columnconfigure(1, weight=1)
        
        result_title = ctk.CTkLabel(result_header_frame, text="检查结果", font=ctk.CTkFont(size=16, weight="bold"))
        result_title.grid(row=0, column=0, padx=0, pady=0, sticky="w")
        
        clear_result_button = ctk.CTkButton(result_header_frame, text="清空结果", width=80, 
                                       command=lambda: self.result_text.clear_text())
        clear_result_button.grid(row=0, column=1, padx=10, pady=0, sticky="e")
        
        self.result_text = ScrollableTextFrame(result_frame)
        self.result_text.grid(row=1, column=0, padx=10, pady=(0, 5), sticky="nsew")
    
    def browse_file(self, var, title, filetypes, save=False):
        """浏览选择文件"""
        try:
            initial_dir = self.config.get("last_input_dir", "") if not save else self.config.get("last_output_dir", "")
            
            # 验证初始目录
            if not initial_dir or not os.path.exists(initial_dir):
                initial_dir = os.getcwd()
            
            # 确保初始目录可访问
            try:
                os.listdir(initial_dir)
            except (PermissionError, OSError):
                initial_dir = os.path.expanduser("~")  # 使用用户主目录
                if not os.path.exists(initial_dir):
                    initial_dir = os.getcwd()
            
            file_path = None
            if save:
                file_path = filedialog.asksaveasfilename(
                    title=title,
                    filetypes=filetypes,
                    initialdir=initial_dir
                )
            else:
                file_path = filedialog.askopenfilename(
                    title=title,
                    filetypes=filetypes,
                    initialdir=initial_dir
                )
            
            if file_path:
                # 验证路径
                if save:
                    # 对于保存文件，检查目录是否可写
                    parent_dir = os.path.dirname(file_path)
                    if parent_dir and not os.path.exists(parent_dir):
                        try:
                            os.makedirs(parent_dir, exist_ok=True)
                        except Exception as e:
                            messagebox.showerror("错误", f"无法创建目录: {parent_dir}\n{e}")
                            return
                        
                        # 测试写入权限
                        try:
                            test_file = file_path + ".tmp"
                            with open(test_file, 'w') as f:
                                f.write("test")
                            os.remove(test_file)
                        except Exception as e:
                            messagebox.showerror("错误", f"目标位置不可写入: {file_path}\n{e}")
                            return
                else:
                    # 对于打开文件，检查文件是否存在和可读
                    if not os.path.exists(file_path):
                        messagebox.showerror("错误", f"文件不存在: {file_path}")
                        return
                    
                    # 测试文件读取权限和编码 - 尝试多种编码
                    try:
                        # 首先尝试UTF-8编码
                        with open(file_path, 'r', encoding='utf-8') as f:
                            f.read(100)  # 读取前100个字符测试
                    except UnicodeDecodeError:
                        # 如果UTF-8失败，尝试其他常见编码
                        encodings_to_try = ['gbk', 'gb2312', 'latin1', 'cp1252']
                        file_readable = False
                        for encoding in encodings_to_try:
                            try:
                                with open(file_path, 'r', encoding=encoding) as f:
                                    f.read(100)
                                file_readable = True
                                break
                            except (UnicodeDecodeError, UnicodeError):
                                continue
                        
                        if not file_readable:
                            # 如果所有编码都失败，尝试二进制读取测试权限
                            try:
                                with open(file_path, 'rb') as f:
                                    f.read(1)
                                # 文件可读但编码可能有问题，给出警告但不阻止
                                result = messagebox.askyesno("编码警告", 
                                    f"文件可能存在编码问题，但仍可以尝试翻译。\n文件: {os.path.basename(file_path)}\n\n是否继续选择此文件？")
                                if not result:
                                    return
                            except Exception as e:
                                messagebox.showerror("错误", f"文件不可读取: {file_path}\n{e}")
                                return
                    except Exception as e:
                        messagebox.showerror("错误", f"文件不可读取: {file_path}\n{e}")
                        return
                
                var.set(file_path)
                
                # 更新最后使用的目录
                try:
                    dir_path = os.path.dirname(file_path)
                    if dir_path and os.path.exists(dir_path):
                        if save:
                            self.config["last_output_dir"] = dir_path
                        else:
                            self.config["last_input_dir"] = dir_path
                        
                        self.config_manager.update_config(self.config)
                except Exception as e:
                    logger.warning(f"更新最后使用目录失败: {e}")
                    
        except Exception as e:
            logger.error(f"浏览文件时出错: {e}")
            messagebox.showerror("错误", f"浏览文件时出现错误: {e}")
    
    def validate_inputs(self):
        """验证输入的有效性"""
        # 检查源文件
        source_file = self.source_file_var.get().strip()
        if not source_file:
            messagebox.showerror("错误", "请选择源SRT文件")
            return False
        
        if not os.path.exists(source_file):
            messagebox.showerror("错误", f"源文件不存在: {source_file}")
            return False
        
        # 检查翻译文件
        translated_file = self.translated_file_var.get().strip()
        if not translated_file:
            messagebox.showerror("错误", "请选择翻译SRT文件")
            return False
        
        if not os.path.exists(translated_file):
            messagebox.showerror("错误", f"翻译文件不存在: {translated_file}")
            return False
        
        # 移除报告文件检查 - 已简化功能
        # 原来的报告文件验证逻辑已被移除
        
        return True
    
    def start_check(self):
        """开始检查任务"""
        if not self.validate_inputs():
            return
        
        if self.checker_thread and self.checker_thread.is_alive():
            messagebox.showinfo("提示", "检查任务已在进行中")
            return
        
        # 准备参数
        source_file = self.source_file_var.get().strip()
        translated_file = self.translated_file_var.get().strip()
        # 移除报告文件功能 - 简化检查
        
        # 更新UI状态
        self.check_button.configure(state="disabled")
        self.result_text.clear_text()
        self.result_text.insert_text("正在检查SRT文件，请稍候...")
        
        # 启动检查线程
        self.checker_thread = threading.Thread(
            target=self.check_task,
            args=(source_file, translated_file),  # 移除report_file参数
            daemon=True
        )
        self.checker_thread.start()
    
    def check_task(self, source_file, translated_file):
        """执行检查任务（在单独的线程中运行）"""
        try:
            # 创建一个自定义的输出重定向器，过滤ANSI颜色代码
            class CleanTextRedirector:
                def __init__(self, text_widget):
                    self.text_widget = text_widget
                    # ANSI颜色代码的正则表达式
                    self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                
                def write(self, string):
                    # 移除ANSI颜色代码
                    clean_string = self.ansi_escape.sub('', string)
                    if clean_string.strip():  # 只显示非空内容
                        self.text_widget.insert_text(clean_string)
                
                def flush(self):
                    pass
            
            # 重定向标准输出
            original_stdout = sys.stdout
            sys.stdout = CleanTextRedirector(self.result_text)
            
            # 调用检查器功能
            srt_checker.check_srt_files(source_file, translated_file)
            
        except Exception as e:
            error_msg = str(e)
            print(f"检查出错: {error_msg}")
            logger.error(f"检查出错: {error_msg}")
            self.after(0, lambda: messagebox.showerror("检查错误", f"检查过程中出现错误:\n{error_msg}"))
        
        finally:
            # 恢复标准输出
            sys.stdout = original_stdout
            
            # 恢复UI状态
            self.after(0, lambda: self.check_button.configure(state="normal"))


class SRTTranslatorApp(ctk.CTk):
    """主应用程序类"""
    def __init__(self):
        super().__init__()
        
        # 初始化配置管理器
        self.config_manager = ConfigManager()
        
        # 设置窗口
        self.title(f"SRT字幕翻译工具 v{VERSION}")
        self.geometry("1000x700")
        self.minsize(800, 600)
        
        # 设置图标
        try:
            self.iconbitmap("srt_icon.ico")
        except:
            pass  # 如果图标不存在，忽略
        
        # 主布局
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # 创建选项卡控件
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=0, padx=10, pady=5, sticky="nsew")
        
        # 添加选项卡
        self.tabview.add("字幕翻译")
        self.tabview.add("字幕校验")
        
        # 配置选项卡布局
        self.tabview.tab("字幕翻译").grid_columnconfigure(0, weight=1)
        self.tabview.tab("字幕翻译").grid_rowconfigure(0, weight=1)
        self.tabview.tab("字幕校验").grid_columnconfigure(0, weight=1)
        self.tabview.tab("字幕校验").grid_rowconfigure(0, weight=1)
        
        # 创建翻译选项卡
        self.translation_tab = TranslationTab(self.tabview.tab("字幕翻译"), self.config_manager)
        self.translation_tab.grid(row=0, column=0, sticky="nsew")
        
        # 创建检查选项卡
        self.checker_tab = CheckerTab(self.tabview.tab("字幕校验"), self.config_manager)
        self.checker_tab.grid(row=0, column=0, sticky="nsew")
        
        # 添加SRT翻译器模块对象上的cancel_event参数支持
        # 这个补丁修改可以不修改原始翻译器代码
        self._patch_translator_for_cancel()
    
    def _patch_translator_for_cancel(self):
        """为原始翻译器模块添加取消支持"""
        original_translate_srt_file = SRTTranslator.translate_srt_file
        
        def patched_translate_srt_file(self, input_file, output_file, resume=True, start_num=None, end_num=None, cancel_event=None):
            # 保存进度管理器实例以便于外部访问进度
            range_tag = f"_{start_num}_to_{end_num}" if start_num is not None and end_num is not None else ""
            output_base = os.path.splitext(output_file)[0]
            self._current_progress_manager = None
            
            if cancel_event is None:
                # 直接调用原始方法
                return original_translate_srt_file(self, input_file, output_file, resume, start_num, end_num)
            
            try:
                # 解析SRT文件
                entries = self.parse_srt_file(input_file)
                
                # 编号为1到N的条目列表
                numbered_entries = {entry.number: entry for entry in entries}
                max_number = max(numbered_entries.keys()) if numbered_entries else 0
                
                # 确定翻译范围
                if start_num is not None and end_num is not None:
                    if start_num > max_number or end_num > max_number:
                        logger.warning(f"指定的范围 ({start_num}-{end_num}) 超出文件中的最大编号 {max_number}")
                    
                    range_entries = [entry for entry in entries if start_num <= entry.number <= end_num]
                else:
                    range_entries = entries
                
                if not range_entries:
                    logger.warning(f"未找到指定范围内的字幕条目")
                    return
                
                # 分批处理
                total_entries = len(range_entries)
                num_batches = (total_entries + self.batch_size - 1) // self.batch_size
                
                # 创建进度管理器
                from srt_translator import ProgressManager
                progress_manager = self._current_progress_manager = ProgressManager(output_base, num_batches, range_tag)
                progress_manager.update_total_batches(num_batches)
                
                # 检查是否所有批次已完成
                if progress_manager.is_all_completed() and resume:
                    logger.info(f"所有批次已完成，直接合并结果")
                    self.merge_batch_files(output_base, output_file, num_batches, range_tag)
                    return
                
                # 确定要处理的批次
                if resume:
                    batches_to_process = progress_manager.get_remaining_batches()
                    logger.info(f"继续翻译，剩余 {len(batches_to_process)} 个批次")
                else:
                    batches_to_process = list(range(1, num_batches + 1))
                    logger.info(f"重新开始翻译，共 {len(batches_to_process)} 个批次")
                
                # 检查已存在的批次文件，如果不继续则删除
                if not resume:
                    existing_batch_files = progress_manager.find_existing_batch_files()
                    for batch_num, file_path in existing_batch_files.items():
                        try:
                            os.remove(file_path)
                            logger.debug(f"删除已存在的批次文件: {file_path}")
                        except Exception as e:
                            logger.error(f"删除批次文件失败: {e}")
                
                # 判断是否使用多线程
                if self.max_workers > 1 and len(batches_to_process) > 1:
                    # 使用线程池并行处理多个批次
                    import concurrent.futures
                    logger.info(f"使用 {min(self.max_workers, len(batches_to_process))} 个线程并行处理批次")
                    with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                        futures = {}
                        
                        for batch_num in batches_to_process:
                            if cancel_event and cancel_event.is_set():
                                logger.info("检测到取消信号，停止提交新的批次")
                                break
                            
                            # 计算批次的起始和结束索引
                            start_idx = (batch_num - 1) * self.batch_size
                            end_idx = min(start_idx + self.batch_size, total_entries)
                            
                            # 提交批次任务到线程池
                            future = executor.submit(
                                self.process_batch, batch_num, range_entries, output_base, range_tag, progress_manager
                            )
                            futures[future] = batch_num
                        
                        # 等待所有任务完成或取消
                        for future in concurrent.futures.as_completed(futures):
                            batch_num = futures[future]
                            try:
                                future.result()  # 获取结果，会抛出任何异常
                            except Exception as e:
                                logger.error(f"批次 {batch_num} 处理失败: {e}")
                                if cancel_event and cancel_event.is_set():
                                    logger.info("检测到取消信号，中止所有批次处理")
                                    break
                else:
                    # 单线程顺序处理批次
                    logger.info("使用单线程处理批次")
                    for batch_num in batches_to_process:
                        if cancel_event and cancel_event.is_set():
                            logger.info("检测到取消信号，停止处理后续批次")
                            break
                        
                        try:
                            self.process_batch(batch_num, range_entries, output_base, range_tag, progress_manager)
                        except Exception as e:
                            logger.error(f"批次 {batch_num} 处理失败: {e}")
                            if cancel_event and cancel_event.is_set():
                                logger.info("检测到取消信号，中止处理")
                                break
                
                # 检查是否已取消
                if cancel_event and cancel_event.is_set():
                    logger.info("翻译已被用户取消")
                    return
                
                # 合并所有批次文件
                logger.info("所有批次处理完成，开始合并结果")
                self.merge_batch_files(output_base, output_file, num_batches, range_tag)
                
                # 如果是部分翻译，则合并到原始文件
                if start_num is not None and end_num is not None:
                    # 生成带范围标记的输出文件名
                    range_output_file = f"{output_base}_range{range_tag}.srt"
                    # 重命名当前输出文件为带范围标记的文件
                    os.rename(output_file, range_output_file)
                    logger.info(f"将范围翻译结果保存为: {range_output_file}")
                    
                    # 合并部分翻译结果到完整输出文件
                    logger.info(f"将范围翻译结果合并到完整输出文件: {output_file}")
                    self.merge_partial_translation(input_file, range_output_file, output_file, start_num, end_num)
                
                logger.info(f"翻译完成，结果已保存到: {output_file}")
                
            except Exception as e:
                logger.error(f"翻译过程中出错: {e}")
                raise
        
        # 替换原始方法
        SRTTranslator.translate_srt_file = patched_translate_srt_file


def main():
    """主函数"""
    app = SRTTranslatorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
