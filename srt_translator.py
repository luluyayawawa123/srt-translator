#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import argparse
import requests
import sys
import glob
import threading
import concurrent.futures
from typing import List, Dict, Tuple, Optional, Union
import logging

# 设置日志 - 修复Unicode编码问题，兼容PyInstaller打包
if sys.platform == 'win32':
    # 在Windows上使用UTF-8编码，但需要检查stdout/stderr是否为None（PyInstaller打包时可能为None）
    import codecs
    
    # 检查stdout是否存在且不为None
    if sys.stdout is not None and hasattr(sys.stdout, 'buffer') and sys.stdout.buffer is not None:
        try:
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        except (AttributeError, TypeError):
            # 如果设置失败，保持原始stdout
            pass
    
    # 检查stderr是否存在且不为None  
    if sys.stderr is not None and hasattr(sys.stderr, 'buffer') and sys.stderr.buffer is not None:
        try:
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
        except (AttributeError, TypeError):
            # 如果设置失败，保持原始stderr
            pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("srt_translator.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SRT-Translator")

# 定义SRT条目的正则表达式
SRT_PATTERN = re.compile(
    r'(\d+)\s*\n'               # 字幕序号
    r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n'  # 时间码
    r'((?:.+(?:\n|$))+?)'       # 字幕内容（可能多行，最后一行可能没有换行符）
    r'(?:\n|$)',                # 空行或文件结尾
    re.MULTILINE
)

# API终端点和密钥 (默认值)
API_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "grok": "https://grokapi.johntitorblog.com/v1/chat/completions",
    "custom": None  # 添加自定义API端点支持
}
# 在这里设置您的API密钥作为默认值
#DEFAULT_API_KEY = "sk-67450155ec954b4c80c77c6d80993c5f"  # 请替换为您的实际API密钥
#DEFAULT_API_TYPE = "deepseek"  # 默认使用deepseek
DEFAULT_API_KEY = "xai-xxxxLvoBlIlqF1yJzKudIevzCHskTXKqabb9v7nxKwKq7TxccYwjBzTn1t0zoOhvm8BZ2u6ToJbo0WYS"  # 请替换为您的实际API密钥
DEFAULT_API_TYPE = "grok"  # 默认使用grok
DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "grok": "grok-3-fast",
    "custom": None  # 添加自定义模型支持
}  # 各API类型对应的默认模型名称

class PromptManager:
    """提示词管理器，负责管理预设和自定义提示词"""
    
    DEFAULT_PROMPTS = {
        "预设1-通用翻译": "你是一名专业的字幕翻译专家。请将字幕翻译成自然流畅的中文，保持语言简洁明了，符合中文表达习惯。注意保持情感色彩和语气的准确性。单位换算：遇到国外单位时在括号内注明换算（如：5英尺(1.5米)、100美元(约700元)）。名词策略：著名人名地名可译中文，生僻的保持原文或音译，日韩文转英文罗马音。",
        
        "预设2-影视剧翻译": "你是专业的影视字幕翻译专家。请翻译成符合影视语言特色的中文，保持台词的戏剧性和人物性格特点。对话要自然生动，符合中国观众的观看习惯。单位换算：在括号内标注换算结果增强理解。人名策略：主要角色名可音译，配角或生僻名保持原文，日韩角色名转罗马音拼写。",
        
        "预设3-纪录片翻译": "你是专业的纪录片字幕翻译专家。请翻译成严谨准确的中文，保持信息的完整性和客观性。专业术语要翻译准确，叙述要条理清晰。单位换算：科学数据必须准确换算并标注（如：32°F(0°C)、1英里(1.6公里)）。地名人名：国际知名的可译中文，学术或专业领域的保持原文确保准确性。",
        
        "预设4-动画片翻译": "你是专业的动画字幕翻译专家。请翻译成活泼有趣的中文，适合各年龄段观众理解。语言要生动可爱，保持动画特有的幽默感和想象力。单位换算：用简单易懂的方式标注（如：10磅(约5公斤)）。角色名：主角可爱译名，配角保持原文或简单音译，确保小朋友也能理解。",
        
        "预设5-新闻翻译": "你是专业的新闻字幕翻译专家。请翻译成客观准确的中文，保持新闻的严肃性和时效性。专有名词、地名、人名要翻译准确，表达要简洁明确。单位换算：经济数据和统计信息必须准确换算。人名地名：国际政要和知名城市译中文，其他保持原文确保新闻准确性和国际通用性。",
        
        "预设6-教育学术翻译": "你是专业的教育内容字幕翻译专家。请翻译成准确严谨的中文，保持学术用语的专业性。概念要翻译准确，逻辑要清晰，便于学习理解。单位换算：学术数据必须精确换算并标注。专有名词：著名学者可译中文，专业术语、理论名称等保持原文或标准译名，确保学术严谨性。",
        
        "预设7-科技程序翻译": "你是专业的科技内容字幕翻译专家。请翻译成准确的中文，保持技术术语的专业性。编程概念、技术名词要翻译准确，或保留英文原文便于理解。单位换算：技术规格要准确换算（如：存储容量、网络速度等）。技术名词：编程语言、框架名称、API等保持英文，公司名、产品名保持原文，人名可简单音译。",
        
        "预设8-生活日常翻译": "你是专业的生活内容字幕翻译专家。请翻译成贴近生活的自然中文，保持日常对话的亲切感。用词要通俗易懂，符合日常交流习惯。单位换算：生活中的度量衡要贴心换算（如：烹饪用量、购物重量等）。人名地名：朋友邻居等可音译，商店、餐厅名可保持原文，让观众感受异国生活氛围。"
    }
    
    def __init__(self, config_file: str = "prompts_config.json"):
        self.config_file = config_file
        self.prompts = {}
        self.current_prompt = ""
        self.load_prompts()
    
    def load_prompts(self) -> None:
        """加载提示词配置"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 合并默认提示词和用户自定义提示词
                    self.prompts = {**self.DEFAULT_PROMPTS, **data.get("custom_prompts", {})}
                    self.current_prompt = data.get("current_prompt", "")
                logger.info(f"已加载提示词配置: {len(self.prompts)} 个提示词")
            else:
                # 如果配置文件不存在，使用默认提示词
                self.prompts = self.DEFAULT_PROMPTS.copy()
                self.current_prompt = ""
                self.save_prompts()
                logger.info("创建默认提示词配置文件")
        except Exception as e:
            logger.error(f"加载提示词配置出错: {e}")
            self.prompts = self.DEFAULT_PROMPTS.copy()
            self.current_prompt = ""
    
    def save_prompts(self) -> None:
        """保存提示词配置"""
        try:
            # 分离默认提示词和自定义提示词
            custom_prompts = {}
            for name, prompt in self.prompts.items():
                if name not in self.DEFAULT_PROMPTS or prompt != self.DEFAULT_PROMPTS[name]:
                    custom_prompts[name] = prompt
            
            data = {
                "custom_prompts": custom_prompts,
                "current_prompt": self.current_prompt
            }
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存提示词配置到: {self.config_file}")
        except Exception as e:
            logger.error(f"保存提示词配置出错: {e}")
    
    def get_prompts(self) -> Dict[str, str]:
        """获取所有提示词"""
        return self.prompts.copy()
    
    def get_prompt_names(self) -> List[str]:
        """获取所有提示词名称列表"""
        return list(self.prompts.keys())
    
    def get_prompt(self, name: str) -> str:
        """获取指定名称的提示词"""
        return self.prompts.get(name, "")
    
    def set_current_prompt(self, name: str) -> bool:
        """设置当前使用的提示词"""
        if name in self.prompts or name == "":
            self.current_prompt = name
            self.save_prompts()
            return True
        return False
    
    def get_current_prompt(self) -> str:
        """获取当前选中的提示词内容"""
        if self.current_prompt and self.current_prompt in self.prompts:
            return self.prompts[self.current_prompt]
        return ""
    
    def get_current_prompt_name(self) -> str:
        """获取当前选中的提示词名称"""
        return self.current_prompt
    
    def update_prompt(self, name: str, content: str) -> None:
        """更新提示词内容"""
        self.prompts[name] = content
        self.save_prompts()
    
    def add_custom_prompt(self, name: str, content: str) -> bool:
        """添加自定义提示词"""
        if name not in self.prompts:
            self.prompts[name] = content
            self.save_prompts()
            return True
        return False
    
    def delete_custom_prompt(self, name: str) -> bool:
        """删除自定义提示词（不能删除预设提示词）"""
        if name in self.prompts and name not in self.DEFAULT_PROMPTS:
            del self.prompts[name]
            if self.current_prompt == name:
                self.current_prompt = ""
            self.save_prompts()
            return True
        return False
    
    def is_default_prompt(self, name: str) -> bool:
        """判断是否为默认预设提示词"""
        return name in self.DEFAULT_PROMPTS

class SRTEntry:
    """表示SRT文件中的一个字幕条目"""
    def __init__(self, number: int, start_time: str, end_time: str, content: str):
        self.number = number
        self.start_time = start_time
        self.end_time = end_time
        self.content = content.strip()
    
    def to_string(self) -> str:
        """将字幕条目转换为SRT格式字符串"""
        return f"{self.number}\n{self.start_time} --> {self.end_time}\n{self.content}\n"
    
    def __str__(self) -> str:
        return self.to_string()
    
    def __repr__(self) -> str:
        return f"SRTEntry({self.number}, {self.start_time}, {self.end_time}, {self.content})"

class ProgressManager:
    """管理翻译进度，支持断点续接"""
    def __init__(self, output_base: str, total_batches: int = 0, range_tag: str = ""):
        self.output_base = output_base  # 输出文件基础名（不带扩展名）
        self.range_tag = range_tag      # 范围标记，用于区分不同范围的翻译任务
        self.progress_file = f"{output_base}_progress{range_tag}.json"
        self.total_batches = total_batches
        self.completed_batches = set()
        self.lock = threading.RLock()  # 添加线程锁以保证线程安全
        self.load_progress()
    
    def load_progress(self) -> None:
        """加载已保存的进度"""
        with self.lock:
            if os.path.exists(self.progress_file):
                try:
                    with open(self.progress_file, 'r', encoding='utf-8') as f:
                        progress_data = json.load(f)
                        self.total_batches = progress_data.get("total_batches", self.total_batches)
                        self.completed_batches = set(progress_data.get("completed_batches", []))
                    logger.info(f"加载进度文件: 总批次 {self.total_batches}, 已完成 {len(self.completed_batches)} 个批次")
                except Exception as e:
                    logger.error(f"加载进度文件出错: {e}")
                    self.completed_batches = set()
            else:
                logger.info("未找到进度文件，将从头开始翻译")
    
    def save_progress(self) -> None:
        """保存当前进度"""
        with self.lock:
            try:
                with open(self.progress_file, 'w', encoding='utf-8') as f:
                    progress_data = {
                        "total_batches": self.total_batches,
                        "completed_batches": sorted(list(self.completed_batches))
                    }
                    json.dump(progress_data, f, ensure_ascii=False, indent=2)
                logger.debug(f"已保存进度: 总批次 {self.total_batches}, 已完成 {len(self.completed_batches)} 个批次")
            except Exception as e:
                logger.error(f"保存进度文件出错: {e}")
    
    def mark_batch_completed(self, batch_number: int) -> None:
        """标记一个批次为已完成状态"""
        with self.lock:
            self.completed_batches.add(batch_number)
            self.save_progress()
    
    def is_batch_completed(self, batch_number: int) -> bool:
        """检查批次是否已完成"""
        with self.lock:
            return batch_number in self.completed_batches
    
    def update_total_batches(self, total_batches: int) -> None:
        """更新总批次数"""
        with self.lock:
            self.total_batches = total_batches
            self.save_progress()
    
    def get_remaining_batches(self) -> List[int]:
        """获取剩余未完成的批次编号列表"""
        with self.lock:
            all_batches = set(range(1, self.total_batches + 1))
            return sorted(list(all_batches - self.completed_batches))
    
    def is_all_completed(self) -> bool:
        """检查是否所有批次都已完成"""
        with self.lock:
            return len(self.completed_batches) >= self.total_batches
    
    def find_existing_batch_files(self) -> Dict[int, str]:
        """查找已存在的批次文件"""
        batch_file_pattern = f"{self.output_base}_batch{self.range_tag}*.srt"
        existing_files = {}
        
        for file_path in glob.glob(batch_file_pattern):
            try:
                # 尝试从文件名中提取批次编号
                batch_match = re.search(r'_batch' + re.escape(self.range_tag) + r'(\d+)\.srt$', file_path)
                if batch_match:
                    batch_number = int(batch_match.group(1))
                    existing_files[batch_number] = file_path
            except Exception as e:
                logger.warning(f"解析批次文件名出错: {file_path}, {e}")
        
        return existing_files
    
    def recover_from_batch_files(self) -> None:
        """从已存在的批次文件恢复进度"""
        existing_files = self.find_existing_batch_files()
        
        with self.lock:
            if existing_files:
                logger.info(f"发现 {len(existing_files)} 个已存在的批次文件")
                for batch_number in existing_files:
                    self.completed_batches.add(batch_number)
                self.save_progress()
                
                if self.total_batches == 0:
                    # 如果没有总批次信息，则使用最大批次号作为估计
                    max_batch = max(existing_files.keys())
                    self.total_batches = max_batch
                    self.save_progress()
                    logger.info(f"根据现有文件估计总批次数: {max_batch}")

class TranslationAPI:
    """翻译API接口"""
    def __init__(self, api_type: str, api_key: str, model_name: str = None, custom_prompt: str = ""):
        self.api_type = api_type.lower()
        self.api_key = api_key
        self.custom_prompt = custom_prompt
        
        if self.api_type not in API_ENDPOINTS:
            raise ValueError(f"不支持的API类型: {api_type}。支持的类型: {', '.join(API_ENDPOINTS.keys())}")
        
        # 检查API端点是否有效
        if API_ENDPOINTS[self.api_type] is None:
            raise ValueError(f"API类型 {api_type} 需要设置有效的API端点URL")
            
        # 使用用户指定的模型名称，如果未指定则使用默认模型
        self.model_name = model_name or DEFAULT_MODELS.get(self.api_type)
        if not self.model_name and self.api_type != "custom":  # 对于custom类型，允许model_name为None
            raise ValueError(f"未知的API类型: {api_type}，无法确定默认模型名称")
        
        self.endpoint = API_ENDPOINTS[self.api_type]
        logger.info(f"使用 {self.api_type} API ({self.model_name or '无指定模型'}) 进行翻译")
        logger.info(f"API端点: {self.endpoint}")
        if custom_prompt:
            logger.info(f"使用自定义提示词: {custom_prompt[:50]}...")  # 只显示前50字符
    
    def translate(self, text: str, context: Optional[str] = None) -> str:
        """翻译文本，可选提供上下文"""
        if not text.strip():
            return ""
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # 构建系统消息，如果有自定义提示词则使用，否则使用默认
        if self.custom_prompt:
            system_message = (
                f"{self.custom_prompt} "
                "请直接提供翻译结果，不要包含任何其他内容。不要添加'翻译如下'、'翻译结果'等前缀。"
                "不要添加说明、解释或额外的内容，只返回翻译后的文本。"
                "对于专业术语、人名、地名等要保持一致的翻译。"
                "如果有可能违反规定的内容，请用适当的替代词替换，而不是拒绝翻译。"
                "特别重要：请保持原文中的所有分隔符如'===SUBTITLE_SEPARATOR_X==='，不要修改它们。"
                "这些分隔符用于区分不同的字幕条目，必须在输出中保留。"
                "每条字幕必须在你的回复中都有对应的翻译，不多也不少。"
            )
        else:
            system_message = (
                "你是专业的字幕翻译专家，负责将字幕从外语翻译成中文。翻译质量要好，要信达雅，阅读起来要流畅。"
                "请直接提供翻译结果，不要包含任何其他内容。不要添加'翻译如下'、'翻译结果'等前缀。"
                "不要添加说明、解释或额外的内容，只返回翻译后的文本。"
                "对于专业术语、人名、地名等要保持一致的翻译。"
                "如果有可能违反规定的内容，请用适当的替代词替换，而不是拒绝翻译。"
                
                "【单位换算指导】在遇到国外单位时，请在括号内注明换算结果："
                "• 重量：盎司→克(如：5盎司(142克))、磅→公斤(如：10磅(4.5公斤))"
                "• 长度：英尺→米(如：6英尺(1.8米))、英寸→厘米(如：12英寸(30厘米))、英里→公里(如：5英里(8公里))"
                "• 温度：华氏度→摄氏度(如：75°F(24°C))"
                "• 体积：加仑→升(如：3加仑(11升))、品脱→毫升(如：1品脱(473毫升))"
                "• 货币：美元/英镑等→人民币概算(如：100美元(约700元)，汇率可能变动)"
                
                "【名词翻译策略】对于人名、地名、作品名等专有名词："
                "• 知名度高的可翻译成中文(如：纽约→纽约，莎士比亚→莎士比亚)"
                "• 非著名或较生僻的保持原文或音译(如：小地名、人名等)"
                "• 日文、韩文等小语种名词可转为英文罗马音拼写，便于理解"
                "• 避免生硬的强制中文翻译，保持国际通用性"
                
                "特别重要：请保持原文中的所有分隔符如'===SUBTITLE_SEPARATOR_X==='，不要修改它们。"
                "这些分隔符用于区分不同的字幕条目，必须在输出中保留。"
                "每条字幕必须在你的回复中都有对应的翻译，不多也不少。"
            )
        
        user_message = text
        if context:
            user_message = f"上下文信息：{context}\n\n要翻译的内容：{text}\n\n请直接提供翻译结果，不要添加任何前缀或说明，保持所有原始的分隔标记。"
        
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.8,  # 较低的温度以确保翻译的一致性
            "max_tokens": 8000   # 增加token数量以应对更多内容
        }
        
        max_retries = 20  # 增加到20次重试
        retry_delay = 2  # 初始延迟2秒
        max_delay = 60   # 最大延迟60秒
        
        for attempt in range(max_retries):
            try:
                response = requests.post(self.endpoint, headers=headers, json=payload, timeout=60)  # 增加超时时间
                response.raise_for_status()
                
                response_data = response.json()
                if self.api_type == "deepseek":
                    translated_text = response_data["choices"][0]["message"]["content"]
                else:  # grok
                    translated_text = response_data["choices"][0]["message"]["content"]
                
                # 清理模型可能生成的多余文本
                translated_text = self.clean_model_prefixes(translated_text)
                
                return translated_text.strip()
            
            except requests.exceptions.RequestException as e:
                # 计算当前尝试的延迟时间（采用指数退避策略，但设置上限）
                current_delay = min(retry_delay * (2 ** attempt), max_delay)
                
                logger.error(f"API请求错误 (尝试 {attempt+1}/{max_retries}): {e}")
                logger.info(f"将在 {current_delay:.1f} 秒后重试...")
                
                if attempt < max_retries - 1:
                    time.sleep(current_delay)
                else:
                    logger.error(f"达到最大重试次数 ({max_retries})，翻译失败")
                    raise Exception(f"翻译API在 {max_retries} 次尝试后失败: {e}")
            
        return text  # 如果所有尝试都失败，返回原始文本
    
    def clean_model_prefixes(self, text: str) -> str:
        """清理模型可能生成的多余前缀和说明性文本"""
        # 常见的模型生成的前缀模式
        prefixes = [
            r"^翻译如下[:：]?\s*",
            r"^翻译结果[:：]?\s*",
            r"^以下是翻译[:：]?\s*",
            r"^中文翻译[:：]?\s*",
            r"^要翻译的内容[:：]?\s*",
            r"^这是中文翻译[:：]?\s*",
            r"^翻译成中文[:：]?\s*",
            r"^翻译后的文本[:：]?\s*"
        ]
        
        result = text
        for prefix in prefixes:
            result = re.sub(prefix, "", result, flags=re.MULTILINE)
        
        return result.strip()

class SRTTranslator:
    """SRT字幕翻译器"""
    def __init__(self, api_type: str, api_key: str, batch_size: int = 5, context_size: int = 2, max_workers: int = 1, model_name: str = None, custom_prompt: str = ""):
        self.translation_api = TranslationAPI(api_type, api_key, model_name, custom_prompt)
        self.batch_size = batch_size  # 每批处理的字幕条数
        self.context_size = context_size  # 上下文大小（每侧的条目数）
        self.max_workers = max_workers  # 最大并发工作线程数
    
    def parse_srt_file(self, srt_file_path: str) -> List[SRTEntry]:
        """解析SRT文件，返回字幕条目列表"""
        try:
            with open(srt_file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            
            # 确保文件末尾有换行符，这样正则表达式更容易匹配最后一个条目
            if not content.endswith('\n'):
                content += '\n'
            
            entries = []
            for match in SRT_PATTERN.finditer(content):
                number = int(match.group(1))
                start_time = match.group(2)
                end_time = match.group(3)
                subtitle_content = match.group(4)
                
                entries.append(SRTEntry(number, start_time, end_time, subtitle_content))
            
            logger.info(f"已从 {srt_file_path} 解析 {len(entries)} 个字幕条目")
            return entries
        
        except Exception as e:
            logger.error(f"解析SRT文件 {srt_file_path} 出错: {e}")
            raise
    
    def translate_subtitle_batch(self, entries: List[SRTEntry], start_idx: int, end_idx: int) -> List[SRTEntry]:
        """翻译一批字幕条目，考虑上下文"""
        if start_idx >= end_idx or start_idx < 0 or end_idx > len(entries):
            return []
        
        # 获取当前批次的条目
        batch_entries = entries[start_idx:end_idx]
        
        # 如果批次大小为1，直接进行单条翻译以避免递归问题
        if len(batch_entries) == 1:
            entry = batch_entries[0]
            try:
                # 对单个条目进行简单翻译，不再进一步拆分
                translated_content = self.translation_api.translate(entry.content, "")
                
                # 创建翻译后的条目
                return [SRTEntry(entry.number, entry.start_time, entry.end_time, translated_content.strip())]
            except Exception as e:
                logger.error(f"翻译单个条目 {entry.number} 失败: {e}")
                # 如果翻译失败，返回原始条目
                return [entry]
        
        # 批量翻译逻辑
        # 准备上下文（前后各context_size个条目）
        context_before = []
        for i in range(max(0, start_idx - self.context_size), start_idx):
            context_before.append(entries[i].content)
        
        context_after = []
        for i in range(end_idx, min(len(entries), end_idx + self.context_size)):
            context_after.append(entries[i].content)
        
        # 组合上下文信息
        context = ""
        if context_before:
            context += "前文：\n" + "\n".join(context_before) + "\n\n"
        if context_after:
            context += "后文：\n" + "\n".join(context_after)
        
        # 提取要翻译的内容
        contents_to_translate = [entry.content for entry in batch_entries]
        
        # 使用更强大的分隔符，确保API能正确区分
        # 使用带有编号的分隔符，帮助模型理解分隔符的作用
        separator = "\n===SUBTITLE_SEPARATOR_{index}===\n"
        combined_content = ""
        
        for i, content in enumerate(contents_to_translate):
            if i > 0:
                combined_content += separator.format(index=i)
            combined_content += content
        
        try:
            # 翻译
            translated_combined = self.translation_api.translate(combined_content, context)
            
            # 分割翻译结果 - 使用与合并时相同的分隔符
            translated_contents = []
            for i in range(len(contents_to_translate)):
                if i == 0:
                    # 第一部分没有前置分隔符
                    parts = translated_combined.split(separator.format(index=1), 1)
                    if len(parts) > 0:
                        translated_contents.append(parts[0].strip())
                    
                    if len(parts) == 1:
                        # 如果只有一部分，说明分隔符没有正确使用，需要备用方法
                        break
                    
                    translated_combined = parts[1]  # 更新剩余内容
                elif i < len(contents_to_translate) - 1:
                    # 中间部分有前后分隔符
                    parts = translated_combined.split(separator.format(index=i+1), 1)
                    if len(parts) > 1:
                        translated_contents.append(parts[0].strip())
                        translated_combined = parts[1]  # 更新剩余内容
                    else:
                        # 如果分隔符没找到，退出循环
                        translated_contents.append(parts[0].strip())
                        break
                else:
                    # 最后一部分没有后置分隔符
                    translated_contents.append(translated_combined.strip())
            
            # 备用方法：如果分隔符方法失败，尝试使用默认分隔方法
            if len(translated_contents) != len(batch_entries):
                logger.warning(f"翻译结果数量不匹配: 期望 {len(batch_entries)} 个, 得到 {len(translated_contents)} 个")
                
                # 根据批次大小决定如何拆分和处理
                if len(batch_entries) > 2:
                    logger.info("拆分批次并重新翻译...")
                    # 直接拆分为两部分
                    mid_point = (start_idx + end_idx) // 2
                    first_half = self.translate_subtitle_batch(entries, start_idx, mid_point)
                    second_half = self.translate_subtitle_batch(entries, mid_point, end_idx)
                    return first_half + second_half
                else:
                    # 批次大小小于等于2时，逐个翻译
                    logger.info("逐个翻译条目...")
                    result = []
                    for i in range(start_idx, end_idx):
                        single_entry = self.translate_subtitle_batch(entries, i, i+1)
                        result.extend(single_entry)
                    return result
            
            # 调整结果数量以适应预期
            if len(translated_contents) > len(batch_entries):
                # 如果得到太多结果，截取前面部分
                translated_contents = translated_contents[:len(batch_entries)]
            
            # 创建新的翻译后的条目
            translated_entries = []
            for i, entry in enumerate(batch_entries):
                translated_content = translated_contents[i].strip()
                
                translated_entries.append(
                    SRTEntry(entry.number, entry.start_time, entry.end_time, translated_content)
                )
            
            return translated_entries
        
        except Exception as e:
            logger.error(f"翻译批次失败: {e}")
            # 如果发生异常，尝试拆分批次
            if end_idx - start_idx > 1:
                logger.info("翻译异常，尝试拆分批次...")
                mid_point = (start_idx + end_idx) // 2
                first_half = self.translate_subtitle_batch(entries, start_idx, mid_point)
                second_half = self.translate_subtitle_batch(entries, mid_point, end_idx)
                return first_half + second_half
            else:
                # 如果只有一个条目但翻译失败，返回原始条目
                logger.warning(f"无法翻译条目 {entries[start_idx].number}，使用原始内容")
                return [entries[start_idx]]
    
    def process_batch(self, batch_number: int, range_entries: List[SRTEntry], output_base: str, range_tag: str, progress_manager: ProgressManager) -> bool:
        """处理单个批次（用于多线程并行执行）"""
        try:
            # 计算批次的起始和结束索引
            batch_start = (batch_number - 1) * self.batch_size
            batch_end = min(batch_start + self.batch_size, len(range_entries))
            
            thread_name = threading.current_thread().name
            logger.info(f"[{thread_name}] 处理批次 {batch_number} (条目 {batch_start+1}-{batch_end})")
            
            # 翻译当前批次
            translated_batch = self.translate_subtitle_batch(range_entries, batch_start, batch_end)
            
            # 清理翻译内容中可能残留的分隔符
            for entry in translated_batch:
                entry.content = self.clean_separator_markers(entry.content)
            
            # 为每个批次创建一个文件
            batch_output_file = f"{output_base}_batch{range_tag}{batch_number}.srt"
            self.write_srt_entries(translated_batch, batch_output_file)
            logger.info(f"[{thread_name}] 已将批次 {batch_number} 写入 {batch_output_file}")
            
            # 标记批次已完成
            progress_manager.mark_batch_completed(batch_number)
            
            return True
        except Exception as e:
            logger.error(f"处理批次 {batch_number} 出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def translate_srt_file(self, input_file: str, output_file: str, resume: bool = True, start_num: int = None, end_num: int = None) -> None:
        """翻译整个SRT文件或指定范围，支持断点续接和多线程"""
        try:
            # 解析原始SRT文件
            entries = self.parse_srt_file(input_file)
            if not entries:
                logger.error(f"在 {input_file} 中未找到字幕条目")
                return
            
            # 创建输出目录（如果不存在）
            output_dir = os.path.dirname(output_file)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir)
            
            # 确定处理范围
            if start_num is not None and end_num is not None:
                # 将字幕编号转换为索引
                start_idx = None
                end_idx = None
                
                # 查找对应字幕编号的索引位置
                for idx, entry in enumerate(entries):
                    if entry.number == start_num and start_idx is None:
                        start_idx = idx
                    if entry.number == end_num and end_idx is None:
                        # end_idx需要加1以包含end_num
                        end_idx = idx + 1
                
                if start_idx is None or end_idx is None:
                    logger.error(f"无法找到指定的字幕编号范围 {start_num}-{end_num}")
                    return
                
                # 限制处理范围
                range_entries = entries[start_idx:end_idx]
                range_tag = f"_{start_num}_{end_num}"
                # 特定范围翻译时，直接修改输出文件名带上范围标记
                actual_output_file = f"{os.path.splitext(output_file)[0]}{range_tag}.srt"
                logger.info(f"将只翻译字幕编号 {start_num} 到 {end_num} (共 {len(range_entries)} 个条目)")
                logger.info(f"范围翻译结果将保存为: {actual_output_file}")
            else:
                # 处理全部条目
                range_entries = entries
                range_tag = ""
                actual_output_file = output_file
                logger.info(f"将翻译全部 {len(range_entries)} 个字幕条目")
            
            # 输出基础名称
            output_base = os.path.splitext(output_file)[0]
            
            # 设置进度管理
            total_batches = (len(range_entries) + self.batch_size - 1) // self.batch_size
            progress_manager = ProgressManager(output_base, total_batches, range_tag)
            
            # 当开启断点续接时，恢复进度
            if resume:
                # 首先从批次文件恢复进度
                progress_manager.recover_from_batch_files()
                
                # 如果所有批次都已完成，直接合并文件并返回
                if progress_manager.is_all_completed():
                    logger.info("所有批次已完成，正在合并输出文件...")
                    self.merge_batch_files(output_base, actual_output_file, total_batches, range_tag)
                    logger.info(f"翻译已完成。输出在 {actual_output_file}")
                    return
            else:
                # 如果不续接，清空进度记录
                progress_manager = ProgressManager(output_base, total_batches, range_tag)
                # 清除现有批次文件
                existing_files = progress_manager.find_existing_batch_files()
                for file_path in existing_files.values():
                    try:
                        os.remove(file_path)
                        logger.debug(f"已删除旧批次文件: {file_path}")
                    except Exception as e:
                        logger.warning(f"删除旧批次文件出错: {file_path}, {e}")
            
            # 获取需要处理的批次
            remaining_batches = progress_manager.get_remaining_batches()
            logger.info(f"总计 {total_batches} 个批次，剩余 {len(remaining_batches)} 个需要处理")
            
            # 检查是否开启多线程
            if self.max_workers > 1:
                logger.info(f"使用 {self.max_workers} 个线程并行翻译")
                # 使用线程池并行处理批次
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    # 提交所有任务
                    future_to_batch = {
                        executor.submit(self.process_batch, batch_number, range_entries, output_base, range_tag, progress_manager): batch_number
                        for batch_number in remaining_batches
                    }
                    
                    # 等待任务完成
                    for future in concurrent.futures.as_completed(future_to_batch):
                        batch_number = future_to_batch[future]
                        try:
                            success = future.result()
                            if not success:
                                logger.warning(f"批次 {batch_number} 处理失败")
                        except Exception as e:
                            logger.error(f"处理批次 {batch_number} 时出现异常: {e}")
                            import traceback
                            logger.error(traceback.format_exc())
            else:
                # 单线程顺序处理
                for batch_number in remaining_batches:
                    self.process_batch(batch_number, range_entries, output_base, range_tag, progress_manager)
                    # 小睡一下，避免API限制
                    time.sleep(1)
            
            # 合并所有批次文件生成输出
            self.merge_batch_files(output_base, actual_output_file, total_batches, range_tag)
            
            logger.info(f"翻译完成。输出在 {actual_output_file}")
            
        except Exception as e:
            logger.error(f"翻译SRT文件出错: {e}")
            raise
    
    def merge_batch_files(self, output_base: str, output_file: str, total_batches: int, range_tag: str = "") -> None:
        """合并所有批次文件到输出文件"""
        try:
            # 找到所有批次文件
            all_entries = []
            
            for batch_number in range(1, total_batches + 1):
                batch_file = f"{output_base}_batch{range_tag}{batch_number}.srt"
                if os.path.exists(batch_file):
                    try:
                        batch_entries = self.parse_srt_file(batch_file)
                        all_entries.extend(batch_entries)
                    except Exception as e:
                        logger.error(f"读取批次文件 {batch_file} 出错: {e}")
            
            # 按字幕序号排序
            all_entries.sort(key=lambda entry: entry.number)
            
            # 清理翻译内容中可能残留的分隔符
            for entry in all_entries:
                # 清除所有形式的分隔符
                entry.content = self.clean_separator_markers(entry.content)
            
            # 写入输出文件
            self.write_srt_entries(all_entries, output_file)
            logger.info(f"已合并 {len(all_entries)} 个条目到 {output_file}")
            
        except Exception as e:
            logger.error(f"合并批次文件出错: {e}")
            raise
    
    def clean_separator_markers(self, text: str) -> str:
        """清除文本中的所有分隔符标记和模型生成的多余文本"""
        # 移除编号分隔符
        patterns = [
            r"\n?===SUBTITLE_SEPARATOR_\d+===\n?",  # 带编号的分隔符
            r"\n?===SUBTITLE_SEPARATOR===\n?",      # 无编号的分隔符
            r"\n?---\n?",                          # 简单分隔符
            r"^_\d+===\s*"                         # 下划线数字格式的分隔符 (_45=== 等)
        ]
        
        result = text
        for pattern in patterns:
            if pattern.startswith('^'):  # 对以^开头的模式使用MULTILINE标志
                result = re.sub(pattern, " ", result, flags=re.MULTILINE)
            else:
                result = re.sub(pattern, " ", result)
        
        # 清理常见的模型生成的前缀
        prefixes = [
            r"^翻译如下[:：]?\s*",
            r"^翻译结果[:：]?\s*",
            r"^以下是翻译[:：]?\s*",
            r"^中文翻译[:：]?\s*",
            r"^要翻译的内容[:：]?\s*",
            r"^这是中文翻译[:：]?\s*",
            r"^翻译成中文[:：]?\s*",
            r"^翻译后的文本[:：]?\s*"
        ]
        
        for prefix in prefixes:
            result = re.sub(prefix, "", result, flags=re.MULTILINE)
        
        # 清理可能多余的空格
        result = re.sub(r"\s+", " ", result)
        return result.strip()
    
    def merge_partial_translation(self, original_file: str, partial_file: str, output_file: str, start_num: int, end_num: int) -> None:
        """将部分翻译结果与原始文件合并"""
        try:
            # 读取原始文件和部分翻译文件
            original_entries = self.parse_srt_file(original_file)
            partial_entries = self.parse_srt_file(partial_file)
            
            # 清理翻译内容中可能残留的分隔符
            for entry in partial_entries:
                entry.content = self.clean_separator_markers(entry.content)
            
            # 创建一个字典，用于快速查找翻译后的条目
            translated_dict = {entry.number: entry for entry in partial_entries}
            
            # 合并结果
            merged_entries = []
            for entry in original_entries:
                if start_num <= entry.number <= end_num and entry.number in translated_dict:
                    # 使用翻译后的内容，但保留原始编号
                    translated_entry = translated_dict[entry.number]
                    merged_entries.append(SRTEntry(
                        entry.number,
                        entry.start_time,
                        entry.end_time,
                        translated_entry.content
                    ))
                else:
                    # 保留原始条目
                    merged_entries.append(entry)
            
            # 写入合并后的文件
            self.write_srt_entries(merged_entries, output_file)
            logger.info(f"已将部分翻译 ({start_num}-{end_num}) 合并到 {output_file}")
            
        except Exception as e:
            logger.error(f"合并部分翻译出错: {e}")
            raise
    
    def write_srt_entries(self, entries: List[SRTEntry], output_file: str) -> None:
        """将字幕条目列表写入SRT文件"""
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                for i, entry in enumerate(entries):
                    # 确保写入前清理分隔符
                    clean_content = self.clean_separator_markers(entry.content)
                    clean_entry = SRTEntry(entry.number, entry.start_time, entry.end_time, clean_content)
                    f.write(clean_entry.to_string())
                    if i < len(entries) - 1:
                        f.write("\n")
            
            logger.debug(f"已将 {len(entries)} 个条目写入 {output_file}")
        
        except Exception as e:
            logger.error(f"写入输出文件 {output_file} 出错: {e}")
            raise

def main():
    parser = argparse.ArgumentParser(description="SRT字幕翻译工具")
    parser.add_argument("input_file", help="输入SRT文件路径")
    parser.add_argument("output_file", help="输出SRT文件路径")
    parser.add_argument("--api", choices=["deepseek", "grok", "custom"], default=DEFAULT_API_TYPE, help=f"使用的翻译API (默认: {DEFAULT_API_TYPE})")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API密钥 (如果不提供，将使用默认值)")
    parser.add_argument("--model", help=f"模型名称 (默认: 根据API类型自动选择)")
    parser.add_argument("--api-endpoint", help="自定义API端点URL (仅当--api=custom时使用)")
    parser.add_argument("--batch-size", type=int, default=5, help="每批处理的字幕条数")
    parser.add_argument("--context-size", type=int, default=2, help="上下文条目数量")
    parser.add_argument("--no-resume", action="store_true", help="不使用断点续接，重新开始翻译")
    parser.add_argument("--start", type=int, help="开始翻译的字幕编号")
    parser.add_argument("--end", type=int, help="结束翻译的字幕编号")
    parser.add_argument("--threads", type=int, default=1, help="并行处理的线程数 (大于1启用多线程)")
    parser.add_argument("--prompt", help="自定义翻译提示词")
    parser.add_argument("--prompt-name", help="使用指定名称的预设提示词（如：预设1-通用翻译）")
    
    args = parser.parse_args()
    
    try:
        # 检查是否提供了范围参数
        if (args.start is not None and args.end is None) or (args.start is None and args.end is not None):
            logger.error("必须同时提供 --start 和 --end 参数以指定翻译范围")
            return 1
        
        if args.start is not None and args.end is not None:
            if args.start > args.end:
                logger.error(f"开始编号 ({args.start}) 不能大于结束编号 ({args.end})")
                return 1
            
            logger.info(f"将只翻译字幕编号 {args.start} 到 {args.end}")
            logger.info(f"注意: 范围翻译结果将保存为带范围标记的独立文件")
        else:
            logger.info("将翻译全部字幕")
        
        # 检查并发线程数
        if args.threads < 1:
            logger.warning(f"无效的线程数: {args.threads}，将使用1个线程")
            args.threads = 1
        
        if args.threads > 1:
            logger.info(f"使用 {args.threads} 个线程并行翻译")
        
        logger.info(f"开始翻译 {args.input_file} 到 {args.output_file}")
        logger.info(f"使用 {args.api} API，批次大小 {args.batch_size}，上下文大小 {args.context_size}")
        
        # 自定义API端点处理
        api_endpoint = None
        if args.api == "custom" and args.api_endpoint:
            api_endpoint = args.api_endpoint
            logger.info(f"使用自定义API端点: {api_endpoint}")
            # 将自定义端点设置到API_ENDPOINTS字典中
            API_ENDPOINTS["custom"] = api_endpoint
        
        if args.model:
            logger.info(f"使用自定义模型: {args.model}")
            if args.api == "custom":
                # 将自定义模型设置到DEFAULT_MODELS字典中
                DEFAULT_MODELS["custom"] = args.model
        
        # 处理提示词
        custom_prompt = ""
        if args.prompt and args.prompt_name:
            logger.error("不能同时使用 --prompt 和 --prompt-name 参数")
            return 1
        elif args.prompt:
            custom_prompt = args.prompt
            logger.info(f"使用自定义提示词: {custom_prompt[:50]}...")
        elif args.prompt_name:
            prompt_manager = PromptManager()
            custom_prompt = prompt_manager.get_prompt(args.prompt_name)
            if custom_prompt:
                logger.info(f"使用预设提示词 '{args.prompt_name}': {custom_prompt[:50]}...")
            else:
                logger.error(f"未找到名为 '{args.prompt_name}' 的提示词")
                logger.info(f"可用的提示词: {', '.join(prompt_manager.get_prompt_names())}")
                return 1
        
        translator = SRTTranslator(args.api, args.api_key, args.batch_size, args.context_size, args.threads, args.model, custom_prompt)
        
        translator.translate_srt_file(args.input_file, args.output_file, 
                                      resume=not args.no_resume,
                                      start_num=args.start, 
                                      end_num=args.end)
        
        logger.info("翻译成功完成")
    
    except Exception as e:
        logger.error(f"翻译失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main()) 