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

# 设置日志 - 修复Unicode编码问题
if sys.platform == 'win32':
    # 在Windows上使用UTF-8编码
    import codecs
    try:
        if (
            sys.stdout is not None
            and not getattr(sys.stdout, "closed", False)
            and hasattr(sys.stdout, "buffer")
            and sys.stdout.buffer is not None
            and not getattr(sys.stdout.buffer, "closed", False)
        ):
            sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
        if (
            sys.stderr is not None
            and not getattr(sys.stderr, "closed", False)
            and hasattr(sys.stderr, "buffer")
            and sys.stderr.buffer is not None
            and not getattr(sys.stderr.buffer, "closed", False)
        ):
            sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")
    except Exception:
        pass

logger = logging.getLogger("SRT-Polisher")
# 避免重复添加处理器
if not logger.handlers:
    logger.setLevel(logging.INFO)
    # 文件日志（始终开启）
    file_handler = logging.FileHandler("srt_polisher.log", encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

    # 控制台日志：默认关闭；仅当未显式抑制且作为脚本运行时开启
    suppress_console = os.environ.get('SRT_SUPPRESS_CONSOLE_LOG', '0') == '1'
    if not suppress_console and (__name__ == '__main__'):
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logger.addHandler(stream_handler)
    # 禁止向root传播，避免被其他模块的StreamHandler重复输出到控制台
    logger.propagate = False

# 导入翻译器模块以复用API和数据结构
try:
    import srt_translator as st
    from srt_translator import SRTEntry, TranslationAPI, ProgressManager, SRT_PATTERN
except ImportError:
    # 如果导入失败，定义基础结构
    st = None
    logger.warning("无法导入翻译器模块，使用内置定义")

    # 定义SRT条目的正则表达式
    SRT_PATTERN = re.compile(
        r'(\d+)\s*\n'               # 字幕序号
        r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n'  # 时间码
        r'((?:.+(?:\n|$))+?)'       # 字幕内容（可能多行，最后一行可能没有换行符）
        r'(?:\n|$)',                # 空行或文件结尾
        re.MULTILINE
    )

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

class SRTPolisher:
    """SRT字幕润色器 - 优化字幕的阅读流畅性和字数分配"""

    def __init__(self, api_type: str, api_key: str, batch_size: int = 10, context_size: int = 2,
                 max_workers: int = 3, model_name: str = None, temperature: float = 0.3, user_prompt: str = "", api_endpoint: str = None,
                 length_policy: str = "cn_balanced", fallback_on_timecode: bool = False, corner_quotes: bool = False):
        """初始化润色器

        Args:
            api_type: API类型 (deepseek/grok/custom)
            api_key: API密钥
            batch_size: 每批处理的字幕条数
            context_size: 上下文大小
            max_workers: 最大并发工作线程数
            model_name: 模型名称
            temperature: 温度参数，润色建议使用较低值(0.1-0.4)
            user_prompt: 用户自定义提示词(从翻译器继承)
            api_endpoint: 自定义API端点(用于custom类型)
        """
        # 如果是custom类型且提供了端点，设置到API_ENDPOINTS中
        if api_type == "custom" and api_endpoint:
            if 'st' in globals() and st is not None:
                st.API_ENDPOINTS["custom"] = api_endpoint
                logger.info(f"设置自定义API端点: {api_endpoint}")
            else:
                # 兜底：实例化后直接覆写端点
                self._pending_custom_endpoint = api_endpoint
                logger.info(f"设置本地自定义API端点: {api_endpoint}")
        
        # 复用翻译器的API接口
        self.translation_api = TranslationAPI(api_type, api_key, model_name, temperature)
        # 如有兜底端点，直接覆写实例端点，确保与翻译器配置一致
        if hasattr(self, "_pending_custom_endpoint"):
            self.translation_api.endpoint = getattr(self, "_pending_custom_endpoint")
        self.batch_size = batch_size
        self.context_size = context_size
        self.max_workers = max_workers
        self.user_prompt = user_prompt  # 保存用户提示词
        # 字数分配策略
        self.length_policy = length_policy or "cn_balanced"
        self._policy = self._resolve_length_policy(self.length_policy)
        # 性能/稳定性开关：检测到时间码污染时是否单条回退
        self.fallback_on_timecode = bool(fallback_on_timecode)
        # 输出风格开关：将引号转换为折角引号（「」/『』）
        self.corner_quotes = bool(corner_quotes)
        self.last_verify_perfect: Optional[bool] = None
        self.last_verify_summary: Optional[dict] = None
        self._system_prompt_logged = False
        self._log_lock = threading.RLock()

        logger.info(f"字幕润色器初始化完成 - API: {api_type}, 批次大小: {batch_size}, 线程数: {max_workers}")
        if user_prompt:
            # 按要求完整输出用户提示词
            logger.info(f"使用自定义提示词: {user_prompt}")
        if api_endpoint:
            logger.info(f"使用API端点: {api_endpoint}")
        # 输出字数分配策略详情
        logger.info(
            "字数分配方案: %s | cps=%.2f, 短(≤%.1fs)max=%s, 中(≤%.1fs)max=%s, 长>%.1fs max=%s",
            self._policy['name'], float(self._policy['cps']), float(self._policy['short_threshold_s']),
            str(self._policy['short_max_total']), float(self._policy['mid_threshold_s']), str(self._policy['mid_max_total']),
            float(self._policy['mid_threshold_s']), str(self._policy['long_max_total'])
        )
        logger.info("时间码污染回退策略: %s", "开启(更稳)" if self.fallback_on_timecode else "关闭(更快)")
        logger.info("折角引号转换: %s", "开启" if self.corner_quotes else "关闭")

    def convert_quotes_to_corner(self, text: str) -> str:
        """将中文弯引号/直引号转换为折角引号（外层「」内层『』）。"""
        if not text:
            return ""

        pairs = (("「", "」"), ("『", "』"))

        def _pair_for_depth(depth: int) -> tuple:
            return pairs[(max(depth, 1) - 1) % 2]

        def _is_digit(ch: str) -> bool:
            return "0" <= ch <= "9"

        def _looks_like_inch_mark(prev_ch: str, next_ch: str) -> bool:
            if not prev_ch:
                return False
            if not _is_digit(prev_ch):
                return False
            # 5" 或 5"xxx 常见为英寸/单位，不当作引号处理
            if not next_ch:
                return True
            return next_ch.isspace() or next_ch.isalpha() or _is_digit(next_ch)

        out = []
        stack = []  # 元素: "curly_double" | "straight_double"
        n = len(text)
        i = 0
        while i < n:
            ch = text[i]
            prev_ch = text[i - 1] if i > 0 else ""
            next_ch = text[i + 1] if i + 1 < n else ""

            if ch == "“":
                depth = len(stack) + 1
                out.append(_pair_for_depth(depth)[0])
                stack.append("curly_double")
            elif ch == "”":
                if stack and stack[-1] == "curly_double":
                    depth = len(stack)
                    out.append(_pair_for_depth(depth)[1])
                    stack.pop()
                else:
                    out.append(ch)
            elif ch == '"':
                if _looks_like_inch_mark(prev_ch, next_ch) or prev_ch == "\\":
                    out.append(ch)
                else:
                    if stack and stack[-1] == "straight_double":
                        depth = len(stack)
                        out.append(_pair_for_depth(depth)[1])
                        stack.pop()
                    else:
                        depth = len(stack) + 1
                        out.append(_pair_for_depth(depth)[0])
                        stack.append("straight_double")
            else:
                out.append(ch)
            i += 1

        return "".join(out)

    def _resolve_length_policy(self, policy_key: str) -> Dict[str, Union[str, float, int]]:
        """根据键名返回长度策略参数集。"""
        # 两套方案：中文语速最佳实践 与 Netflix 中文规范
        policies = {
            # 较为宽松、贴近中文实际语速（经验值）：约5.5字/秒，放宽总字数上限
            "cn_balanced": {
                "key": "cn_balanced",
                "name": "中文语速最佳实践",
                "cps": 5.5,
                "short_threshold_s": 1.5,
                "mid_threshold_s": 3.5,
                "short_max_total": 24,
                "mid_max_total": 40,
                "long_max_total": 48,
                # 最小/最大比例系数
                "min_ratio_short": 0.6,
                "min_ratio_mid": 0.7,
                "min_ratio_long": 0.8,
                "max_ratio_short": 1.4,
                "max_ratio_mid": 1.3,
                "max_ratio_long": 1.2,
            },
            # 新·中文语速最佳实践：结合“无”的高质量润色提示词 + “中文语速最佳实践”的语速控制
            "cn_balanced_new": {
                "key": "cn_balanced_new",
                "name": "新·中文语速最佳实践",
                "cps": 5.5,
                "short_threshold_s": 1.5,
                "mid_threshold_s": 3.5,
                "short_max_total": 24,
                "mid_max_total": 40,
                "long_max_total": 48,
                "min_ratio_short": 0.6,
                "min_ratio_mid": 0.7,
                "min_ratio_long": 0.8,
                "max_ratio_short": 1.4,
                "max_ratio_mid": 1.3,
                "max_ratio_long": 1.2,
            },
            # 最新中文语速方案（实验）：基于最佳实践，但移除强制数字逐位读法
            "cn_speed_experimental": {
                "key": "cn_speed_experimental",
                "name": "最新中文语速方案（实验）",
                "cps": 5.5,
                "short_threshold_s": 1.5,
                "mid_threshold_s": 3.5,
                "short_max_total": 24,
                "mid_max_total": 40,
                "long_max_total": 48,
                "min_ratio_short": 0.6,
                "min_ratio_mid": 0.7,
                "min_ratio_long": 0.8,
                "max_ratio_short": 1.4,
                "max_ratio_mid": 1.3,
                "max_ratio_long": 1.2,
            },
            # 最新中文语速方案（实验2）：增强风格保护，严禁正规化俚语
            "cn_speed_experimental_2": {
                "key": "cn_speed_experimental_2",
                "name": "最新中文语速方案 (实验2)",
                "cps": 5.5,
                "short_threshold_s": 1.5,
                "mid_threshold_s": 3.5,
                "short_max_total": 24,
                "mid_max_total": 40,
                "long_max_total": 48,
                "min_ratio_short": 0.6,
                "min_ratio_mid": 0.7,
                "min_ratio_long": 0.8,
                "max_ratio_short": 1.4,
                "max_ratio_mid": 1.3,
                "max_ratio_long": 1.2,
            },
            # 不做长度限制：仅计算optimal供提示，不设max硬上限
            "none": {
                "key": "none",
                "name": "不限制字数",
                "cps": 9999.0,  # 极大化，避免下限干扰
                "short_threshold_s": 1.5,
                "mid_threshold_s": 3.5,
                "short_max_total": 10000,
                "mid_max_total": 10000,
                "long_max_total": 10000,
                "min_ratio_short": 0.0,
                "min_ratio_mid": 0.0,
                "min_ratio_long": 0.0,
                "max_ratio_short": 999.0,
                "max_ratio_mid": 999.0,
                "max_ratio_long": 999.0,
            },
        }
        return policies.get(policy_key, policies["cn_balanced"])

    def parse_time_to_seconds(self, time_str: str) -> float:
        """将SRT时间格式转换为秒数"""
        # 格式：00:01:30,500
        parts = time_str.replace(',', ':').split(':')
        hours, minutes, seconds, ms = map(int, parts)
        return hours * 3600 + minutes * 60 + seconds + ms / 1000

    def calculate_duration(self, entry: SRTEntry) -> float:
        """计算字幕条目的持续时间（秒）"""
        start_seconds = self.parse_time_to_seconds(entry.start_time)
        end_seconds = self.parse_time_to_seconds(entry.end_time)
        return end_seconds - start_seconds

    def calculate_optimal_char_count(self, duration_seconds: float) -> tuple:
        """根据时间长度计算最优字符数范围 (采用Netflix标准)

        Netflix字幕标准：
        - 阅读速度：成人每分钟160-180个中文字符 (约2.7-3个字符/秒)
        - 单行显示：≤20个字符
        - 双行显示：每行≤20个字符，总计≤40个字符
        - 最短显示：≥0.833秒 (20帧@24fps)
        - 最长显示：≤7秒

        Returns:
            (min_count, optimal_count, max_count)
        """
        # 使用所选策略的字符/秒
        cps = float(self._policy['cps'])
        optimal_count = int(duration_seconds * cps)

        short_th = self._policy['short_threshold_s']
        mid_th = self._policy['mid_threshold_s']

        if duration_seconds <= short_th:
            # 短字幕：单行显示
            min_count = max(3, int(optimal_count * self._policy['min_ratio_short']))
            max_count = min(self._policy['short_max_total'], int(optimal_count * self._policy['max_ratio_short']))
        elif duration_seconds <= mid_th:
            # 中等字幕
            min_count = max(5, int(optimal_count * self._policy['min_ratio_mid']))
            max_count = min(self._policy['mid_max_total'], int(optimal_count * self._policy['max_ratio_mid']))
        else:
            # 长字幕
            min_count = max(8, int(optimal_count * self._policy['min_ratio_long']))
            max_count = min(self._policy['long_max_total'], int(optimal_count * self._policy['max_ratio_long']))

        # 确保合理性
        optimal_count = max(min_count, min(optimal_count, max_count))

        return min_count, optimal_count, max_count

    def analyze_subtitle_context(self, entries: List[SRTEntry], target_idx: int) -> Dict[str, any]:
        """分析字幕条目的上下文信息，为润色提供参考"""
        target_entry = entries[target_idx]
        duration = self.calculate_duration(target_entry)
        min_chars, optimal_chars, max_chars = self.calculate_optimal_char_count(duration)
        current_chars = len(target_entry.content)

        context_info = {
            'duration': duration,
            'current_chars': current_chars,
            'min_chars': min_chars,
            'optimal_chars': optimal_chars,
            'max_chars': max_chars,
            'char_ratio': current_chars / optimal_chars if optimal_chars > 0 else 1.0,
            'needs_adjustment': current_chars < min_chars or current_chars > max_chars
        }

        # 分析前后文
        if target_idx > 0:
            prev_entry = entries[target_idx - 1]
            context_info['prev_content'] = prev_entry.content
            context_info['prev_duration'] = self.calculate_duration(prev_entry)
            context_info['prev_chars'] = len(prev_entry.content)

        if target_idx < len(entries) - 1:
            next_entry = entries[target_idx + 1]
            context_info['next_content'] = next_entry.content
            context_info['next_duration'] = self.calculate_duration(next_entry)
            context_info['next_chars'] = len(next_entry.content)

        return context_info

    def build_polish_system_message(self) -> str:
        """构建润色专用的系统提示词"""
        # 针对“无”策略（不限制字数）或“新·中文语速最佳实践”的专用优化提示词
        # 这两种模式共享核心润色逻辑（语义连贯优先），区别在于是否注入字数限制参数
        # 针对“无”策略（不限制字数）或“新·中文语速最佳实践”或“实验”系列的专用优化提示词
        # 这两种模式共享核心润色逻辑（语义连贯优先），区别在于是否注入字数限制参数
        if self._policy.get('key') in ['none', 'cn_balanced_new', 'cn_speed_experimental', 'cn_speed_experimental_2']:
            base_message = """你是专业的字幕润色专家。你的核心任务是优化字幕的**语义连贯性**与**断句自然度**，使其读起来像自然的对话。

最高优先级任务：语义连贯与断句优化
1. **自然拼接与拆分**：
   - 必须检测相邻字幕之间的语义断层。如果一句话被不自然地切分在两行，请务必将其合并或重新断句。
   - 如果某行字幕过长且包含多个语义点，请将其拆分（如果允许）。
   - **关键目标**：让每一行字幕都尽量是一个相对完整的语义单元，或者在符合人类说话停顿习惯的地方断开。
2. **流畅度优化**：
   - 调整语序，使其更符合中文口语习惯。
   - 保持原有的语言风格和表达特色。

严格约束（必须遵守）：
1. **不改变原意**：绝对禁止改变原始内容的含义。
2. **不改变风格**：保持原字幕的语言风格（语气、口吻、角色特质），不要随意替角色“加戏”或改变人设。"""

            base_message += """

中文字幕标点规范（必须遵守）：
- **禁止句末中文句号**：不要在字幕行末添加中文句号“。”；即使原文末尾有“。”也应去掉。
- **保留其他标点**：句中标点正常润色与保留；句末如为"？/！/……"等保留；句末出现"、"也必须保留（不要替换或删除）。
- **严禁逗号变空格**：绝对禁止将句中的逗号"，"替换为空格。如原文为"没问题，包在我身上"，严禁润色为"没问题 包在我身上"。逗号是重要的语义停顿标记，必须保留。
"""



            # 仅针对"新·中文语速最佳实践"模式，注入语速控制参数
            if self._policy.get('key') == 'cn_balanced_new':
                base_message += """

字数分配方案（必须遵守）：
- 当前方案：{policy_name}
- 阅读速度：约{cps}/秒
- 短时（≤{short_th}秒）：总字数≤{short_max}
- 中等（≤{mid_th}秒）：总字数≤{mid_max}
- 长时（>{mid_th}秒）：总字数≤{long_max}

字数控制优化策略（关键）：
1. **超长处理**：如果某行字数显著超过建议值，请优先尝试将非核心词汇精简，或将部分内容移至前后字数较少的行（前提是保持语义连贯）。
2. **过短处理**：如果某行字数过少（如仅1-2字），请务必尝试与前一行或后一行合并，避免细碎的短句。
3. **平衡原则**：在不破坏语义完整性的前提下，尽量让每行的字数接近“阅读速度”建议值，避免忽长忽短。"""
            
            # 针对“最新中文语速方案（实验）”及“实验2”模式，注入语速控制参数（同上，但不含数字强约束）
            if self._policy.get('key') in ['cn_speed_experimental', 'cn_speed_experimental_2']:
                base_message += """

字数分配方案（必须遵守）：
- 当前方案：{policy_name}
- 阅读速度：约{cps}/秒
- 短时（≤{short_th}秒）：总字数≤{short_max}
- 中等（≤{mid_th}秒）：总字数≤{mid_max}
- 长时（>{mid_th}秒）：总字数≤{long_max}

字数控制优化策略：
1. **优先语义**：优先保证语义完整和断句自然，字数是参考建议而非硬性限制。
2. **弹性范围**：允许字数在建议值的±30%范围内浮动，但需要避免：
   - 过短：单行少于3个字（除非是感叹词或特殊情况）
   - 过长：超过上限20%以上
3. **自然为王**：如果严格遵守字数限制会导致语义割裂，优先保证语义完整。"""

            # 针对“实验2”模式，以及“无”模式，注入最高优先级的风格保护指令
            # 目的：让“无”和“实验2”在风格保护层面一致，仅在字数约束策略上不同
            if self._policy.get('key') in ['cn_speed_experimental_2', 'none']:
                base_message += """

【核心指令 - 最高优先级】
3. **保留原味（风格保护）**：用户的核心需求是“原汁原味”。
   - **严禁正规化**：严禁将“回血”、“挂了”、“硬核”、“牛逼”、“崩了”等网络用语、游戏术语、流行梗或口语词汇“修正”为书面语（如“恢复”、“去世”、“强硬”、“厉害”、“崩溃”）。
   - **原样保留**：即使这看起来不符合“标准书面语规范”，也必须原样保留。
   - **例外**：只有当该词汇会导致严重的语义理解错误（如完全读不通）时才允许微调，否则一律不动。"""

            base_message += """

请注意：你必须严格保持分隔符格式，每个输入的字幕都必须在输出中有对应的润色结果。"""

            # 如果有用户提示词，则整合进去
            if self.user_prompt.strip():
                base_message += f"""

用户特殊要求（来自翻译器配置）：
{self.user_prompt.strip()}

请在润色时同时考虑以上用户要求，但最高优先级仍是语义连贯与断句优化。"""
            
            # 注入策略变量 (仅当需要时)
            if self._policy.get('key') in ['cn_balanced_new', 'cn_speed_experimental', 'cn_speed_experimental_2']:
                return base_message.format(
                    policy_name=self._policy['name'],
                    cps=f"{self._policy['cps']:.1f} 字",
                    short_th=f"{self._policy['short_threshold_s']:.1f}",
                    mid_th=f"{self._policy['mid_threshold_s']:.1f}",
                    short_max=self._policy['short_max_total'],
                    mid_max=self._policy['mid_max_total'],
                    long_max=self._policy['long_max_total']
                )
            else:
                return base_message

        # 以下是针对其他策略（如中文平衡、Netflix规范）的通用提示词
        base_message = """你是专业的字幕润色专家，专门负责优化字幕的阅读流畅性和字数分配。你的任务是让字幕读起来更加自然流畅，特别是为后期配音工作做准备。

核心润色原则：
1. **智能断句优化**：确保每行字幕语义相对完整，避免在关键词汇处断句，在合适的语法点断句，如标点符号、语气词、连接词等
2. **阅读节奏**：根据时长合理分配字数，短时字幕不宜过长，长时字幕不宜过短
3. **语义连贯**：相邻字幕应该语义连贯，适当调整内容分配
4. **保持原意**：在润色过程中绝不能改变原始内容的含义

中文字幕标点规范（必须遵守）：
- **禁止句末中文句号**：不要在字幕行末添加中文句号“。”；即使原文末尾有“。”也应去掉。
- **保留其他标点**：句中标点正常润色与保留；句末如为"？/！/……"等保留；句末出现"、"也必须保留（不要替换或删除）。
- **严禁逗号变空格**：绝对禁止将句中的逗号"，"替换为空格。如原文为"没问题，包在我身上"，严禁润色为"没问题 包在我身上"。逗号是重要的语义停顿标记，必须保留。

针对中文配音的断句优化：
1. **语义完整性优先**：确保每行字幕语义相对完整，避免在关键词汇处断句
2. **语音自然停顿**：在符合中文语音习惯的地方断句，如语气词后、连接词前
3. **语法点断句**：在合适的语法点断句，如标点符号、语气词、连接词等
4. **避免语义割裂**：确保断句后每行都有完整的语义表达
5. **语气词处理**：合理处理"啊"、"呢"、"吧"等语气词，保持口语化特点
6. **连接词优化**：适当处理"但是"、"然而"、"因为"、"所以"、"而且"、"不过"、"然而"等连接词，避免语义不完整
   - 避免连接词单独成行
   - 保持逻辑关系完整
   - 确保连接词前后语义连贯
   - 转折连接词（但是、然而、不过）：避免断句后单独成行
   - 因果连接词（因为、所以、由于）：保持因果关系完整
   - 递进连接词（而且、并且、甚至）：保持语义递进关系

字数分配方案（可选）：
- 当前方案：{policy_name}
- 阅读速度：约{cps}/秒
- 短时（≤{short_th}秒）：总字数≤{short_max}
- 中等（≤{mid_th}秒）：总字数≤{mid_max}
- 长时（>{mid_th}秒）：总字数≤{long_max}

润色策略：
- 如果某行字幕语义不完整（如以"但是"、"然而"、"因为"等开头），考虑与前一行合并
- 如果某行字幕结尾是半句话，考虑将后续内容移入当前行
- 保持对话的自然节奏，避免硬性切分完整语句
- 确保每个分隔符都有对应的字幕内容，数量必须完全匹配
- 特别注意连接词的处理，避免语义不完整的断句

断句优化策略：
- 优先在句号、问号、感叹号后断句
- 次优选择在逗号、分号后断句
- 避免在修饰语中间断句
- 避免在专有名词中间断句
- 保持口语表达的自然性
- 重点考虑相邻字幕的语义连贯性，确保断句后能够自然衔接
- 避免在一个完整语义表达中间断句，保持语义单元完整

上下文连贯性检查：
- 检查断句后相邻字幕是否语义连贯
- 确保断句不会造成语义割裂或理解困难
- 避免断句后出现不完整的语义表达
- 保持对话的自然流畅性

请注意：你必须严格保持分隔符格式，每个输入的字幕都必须在输出中有对应的润色结果。"""

        # 新增：风格保持与 TTS 可读化规范
        base_message += """

风格保持（重要）：默认不改变原字幕的语言风格（语气、口吻、角色特质、遣词造句）；只有在用户明确提供特殊要求时才进行风格性调整。

"""

        # TTS 可读化规范（仅当非"none"、非"cn_balanced_new"且非"cn_speed_experimental"且非"cn_speed_experimental_2"策略时启用）
        if self._policy.get('key') not in ['none', 'cn_balanced_new', 'cn_speed_experimental', 'cn_speed_experimental_2']:
            base_message += """
TTS 可读化规范（最重要 - 必须严格遵守）
- 将含符号的表达改写为贴合真实含义、便于朗读的纯中文，不保留易致误读的符号：
  - 乘法/相乘：x、×、* → “乘”。例：“4x4原则”→“四乘四原则”。
  - 范围/区间：-、~、–、— 表示范围时 → “到”。例：“5%-7%”→“百分之五到百分之七”。
  - 列举/分隔：用于并列罗列的“-”或其他符号 → 顿号“、”。例：“30-20-50分配法”→“三十、二十、五十分配法”。
  - 互择/或：/ 表示二选一/多选一 → “或”。例：“30/60分钟”→“三十或六十分钟”（结合上下文判断）。
  - 百分号：% → “百分之N”。例：“5%”→“百分之五”。"""

            # 阿拉伯数字处理策略（仅当非实验模式时启用）
            if self._policy.get('key') not in ['cn_speed_experimental', 'cn_speed_experimental_2']:
                base_message += """
- 阿拉伯数字处理策略（最高优先级 - 绝对禁止违反）：
  - 所有纯数字串均采用逐位读法：
    - 242 → "二四二"（强制）
    - 239 → "二三九"（强制）
    - 430 → "四三零"（强制）
    - 330 → "三三零"（强制）
    - 2023 → "二零二三"（强制）
    - 5% → "百分之五"（百分号仍按原规则处理）
  - 专有名词中的数字保持原样（如型号、代码等技术类场景）"""

            base_message += """
- 必须结合上下文判断符号真实含义再改写；若含义不明，优先选择不引起误读的中文表达。
- 专有名词例外：品牌名、型号、术语、专利名、剧中设定名等保持原样，不对其内部符号或大小写做改写。
"""
        else:
            # 对于"none", "cn_balanced_new", "cn_speed_experimental"策略，前面已经定义了完整的base_message
            pass

        # 数码/技术类例外：保持原样（避免误读）
        # 同样排除 'cn_speed_experimental' 和 'none'，让模型自由发挥
        if self._policy.get('key') not in ['none', 'cn_balanced_new', 'cn_speed_experimental', 'cn_speed_experimental_2']:
             base_message += """

数码/技术例外（强制保留原样）
- 错误码/状态码/HTTP码：如“404 Not Found”“500 Internal Server Error”，保持原文与阿拉伯数字，不转中文，不改大小写与连接符。
- 型号/硬件代号/产品名：如“RTX-5090”“iPhone 15 Pro”“USB 3.0”“Wi‑Fi 6”“PCIe 4.0”，保持原文（含数字与连字符/空格）。
- 版本号/构建号/规范号：如“v1.2.3”“ISO 27001”“RFC 7231”，保持原文。
- 代码标识/变量名/命令片段：如“reset --hard”“A/B 测试”，保持原样。
- 显示与读音：屏幕上保持原文，例如“404 not found”“RTX-5090”；阅读理解按“404”“RTX 5090”，不读出连接符。

注：阿拉伯数字中文化的规则不适用于以上例外场景。
"""

        # 如果有用户提示词，则整合进去 (仅针对非none/new/experimental策略)
        if self._policy.get('key') not in ['none', 'cn_balanced_new', 'cn_speed_experimental', 'cn_speed_experimental_2'] and self.user_prompt.strip():
            base_message += f"""

用户特殊要求（来自翻译器配置）：
{self.user_prompt.strip()}

请在润色时同时考虑以上用户要求，但主要任务仍是优化字幕的流畅性和字数分配。"""
        
        # 注入策略变量 (仅针对非none/new/experimental策略)
        if self._policy.get('key') not in ['none', 'cn_balanced_new', 'cn_speed_experimental', 'cn_speed_experimental_2']:
            return base_message.format(
                policy_name=self._policy['name'],
                cps=f"{self._policy['cps']:.1f} 字",
                short_th=f"{self._policy['short_threshold_s']:.1f}",
                mid_th=f"{self._policy['mid_threshold_s']:.1f}",
                short_max=self._policy['short_max_total'],
                mid_max=self._policy['mid_max_total'],
                long_max=self._policy['long_max_total']
            )
        else:
            return base_message

    def build_polish_prompt(self, entries_batch: List[SRTEntry], context_info: List[Dict]) -> str:
        """构建润色请求的用户提示词（JSON结构化返回版本）"""
        lines = []
        lines.append("请对以下字幕进行润色优化，使其更加流畅自然。")
        lines.append("严格要求：")
        lines.append("1) 只返回JSON数组(UTF-8)，数组长度必须等于本批条数N。")
        lines.append("2) 数组第i个元素(0基或1基均可)必须对应输入第i条字幕的润色文本。")
        lines.append("3) 绝对不要返回除JSON以外的任何文字、标记或注释。不要返回编号、时间码、分隔符。")
        lines.append("4) 禁止输出SRT时间码(如 00:00:10,000 --> 00:00:12,000)与类似格式；禁止输出'字幕#'、'#123'、纯数字行。")
        lines.append("5) 如果无法润色，请原样输出该条文本，但仍以JSON中对应位置返回。")
        lines.append("6) 专业中文字幕：每条输出的每一行末尾都不要使用中文句号“。”（包括“。”出现在引号/括号前的形式，如“。””）。")
        lines.append("")
        # 强化用户提示词的约束（不改变系统提示词，只在本批说明中再次强调）
        if self.user_prompt.strip():
            lines.append("重要：同时遵循以下用户提示词（不得与上面的输出约束冲突）：")
            lines.append(self.user_prompt.strip())
            lines.append("")
        lines.append("以下是本批条目的原文和参考信息(仅供你判断，不要复制时间码或编号)：")
        # 构造一个纯文本说明块，避免模型复制行内时间码
        for i, (entry, info) in enumerate(zip(entries_batch, context_info)):
            if self._policy.get('key') == 'none':
                # 无策略模式：不显示建议字数，避免误导
                lines.append(f"- 第{i+1}条：时长{info['duration']:.1f}s，当前{info['current_chars']}字；正文：{entry.content}")
            else:
                # 其他策略：显示建议字数
                lines.append(f"- 第{i+1}条：时长{info['duration']:.1f}s，当前{info['current_chars']}字，建议{info['optimal_chars']}字；正文：{entry.content}")
        lines.append("")
        lines.append("请现在仅输出JSON数组，例如：[""润色文本1"", ""润色文本2"", ...]，数组长度必须等于N。")
        return "\n".join(lines)

    def polish_subtitle_batch(self, entries: List[SRTEntry], start_idx: int, end_idx: int, retry_count: int = 0, batch_number: int = None) -> List[SRTEntry]:
        """润色一批字幕条目"""
        if start_idx >= end_idx or start_idx < 0 or end_idx > len(entries):
            return []

        batch_entries = entries[start_idx:end_idx]

        # 如果批次大小为1，进行单条润色
        if len(batch_entries) == 1:
            entry = batch_entries[0]
            try:
                context_info = self.analyze_subtitle_context(entries, start_idx)

                # 构建单条润色提示（强调当前任务边界）
                polish_prompt = f"""请润色以下指定的字幕条目，注意与前后文保持语义连贯但不重复：

【润色目标】第{entry.number}条字幕
时长：{context_info['duration']:.1f}秒
当前字数：{context_info['current_chars']}字
建议字数：{context_info['optimal_chars']}字

原始内容：{entry.content}

【要求】只返回润色后的字幕内容，确保与前后文语义不重复。"""

                if context_info['needs_adjustment']:
                    if context_info['char_ratio'] < 0.7:
                        polish_prompt += f"\n[提示：当前字数偏少，建议适当扩充内容]"
                    elif context_info['char_ratio'] > 1.3:
                        polish_prompt += f"\n[提示：当前字数偏多，建议适当精简]"

                # 添加上下文信息（精确标识，避免AI混淆）
                context = ""
                if 'prev_content' in context_info:
                    context += f"【前文参考】第{entry.number-1}条字幕：{context_info['prev_content']}\n\n"

                context += f"【当前任务】请润色第{entry.number}条字幕：{entry.content}\n\n"

                if 'next_content' in context_info:
                    context += f"【后文参考】第{entry.number+1}条字幕：{context_info['next_content']}\n\n"

                context += "【重要提醒】请确保润色后的内容与前后文在语义上不重复，保持独特性和连贯性。"

                # 调用API进行润色
                polished_content = self.translation_api.translate(
                    polish_prompt,
                    context,
                    self.user_prompt or "",
                    system_message_override=(
                        self.build_polish_system_message() + ("""

最终 TTS 简化说明（以本节为准）
- 目标：润色文本既好读，也不让 TTS 读错。
- 仅对符号/数字做中文表达：x/×/*→"乘"；范围 -/~/–/—→"到"；"/"择一→"或"；"%"→"百分之N"。
- 绝对禁止将纯数字串转换为中文数字表达（如"二百四十二"），所有数字必须采用逐位读法（如"二四二"）
- 数码/技术例外保持原样：错误/状态码（如"404 Not Found"）、型号/代号（如"RTX-5090"）、版本/规范号（如"v1.2.3""ISO 27001"）、命令/变量/路径等；显示保留原文，读音按"404""RTX 5090"，不读连接符。
""" if self._policy.get('key') not in ['none', 'cn_balanced_new', 'cn_speed_experimental', 'cn_speed_experimental_2'] else "")
                    )
                )

                # 清理可能的格式问题
                polished_content = self.clean_polished_content(polished_content)

                return [SRTEntry(entry.number, entry.start_time, entry.end_time, polished_content)]

            except Exception as e:
                logger.error(f"润色单个条目 {entry.number} 失败: {e}")
                return [entry]  # 失败时返回原始条目

        # 批量润色逻辑
        try:
            # 分析每个条目的上下文
            context_info_list = []
            for i in range(start_idx, end_idx):
                context_info_list.append(self.analyze_subtitle_context(entries, i))

            # 构建批量润色请求
            polish_prompt = self.build_polish_prompt(batch_entries, context_info_list)

            # 准备上下文信息
            context = ""
            if start_idx > 0:
                context += "前文上下文：\n"
                for i in range(max(0, start_idx - self.context_size), start_idx):
                    context += f"#{entries[i].number}: {entries[i].content}\n"
                context += "\n"

            if end_idx < len(entries):
                context += "后文上下文：\n"
                for i in range(end_idx, min(len(entries), end_idx + self.context_size)):
                    context += f"#{entries[i].number}: {entries[i].content}\n"

            # 调用API进行润色（JSON结构化返回）
            polished_combined = self.translation_api.translate(
                polish_prompt,
                context,
                self.user_prompt or "",
                system_message_override=(
                    self.build_polish_system_message() + ("""

最终 TTS 简化说明（以本节为准）
- 目标：润色文本既好读，也不让 TTS 读错。
- 仅对符号/数字做中文表达：x/×/*→"乘"；范围 -/~/–/—→"到"；"/"择一→"或"；"%"→"百分之N"。
- 绝对禁止将纯数字串转换为中文数字表达（如"二百四十二"），所有数字必须采用逐位读法（如"二四二"）
- 数码/技术例外保持原样：错误/状态码（如"404 Not Found"）、型号/代号（如"RTX-5090"）、版本/规范号（如"v1.2.3""ISO 27001"）、命令/变量/路径等；显示保留原文，读音按"404""RTX 5090"，不读连接符。
""" if self._policy.get('key') not in ['none', 'cn_balanced_new', 'cn_speed_experimental', 'cn_speed_experimental_2'] else "")
                )
            )

            def _parse_json_array(text: str, expected: int) -> Optional[List[str]]:
                try:
                    data = json.loads(text)
                    if isinstance(data, list) and len(data) == expected:
                        # 转为字符串并strip
                        arr = [str(x).strip() for x in data]
                        return arr
                    return None
                except Exception:
                    return None

            polished_contents = _parse_json_array(polished_combined, len(batch_entries))

            # 若首次非JSON或长度不符，进行一次严格重试（提示更严、温度更低）
            if polished_contents is None:
                strict_prompt = polish_prompt + "\n\n务必只返回JSON数组，长度==N，不要任何多余文字。"
                orig_temp = self.translation_api.temperature
                try:
                    self.translation_api.temperature = max(0.1, float(orig_temp) - 0.1)
                except Exception:
                    pass
                strict_resp = self.translation_api.translate(
                    strict_prompt,
                    context,
                    self.user_prompt or "",
                    system_message_override=(
                        self.build_polish_system_message() + ("""

最终 TTS 简化说明（以本节为准）
- 目标：润色文本既好读，也不让 TTS 读错。
- 仅对符号/数字做中文表达：x/×/*→"乘"；范围 -/~/–/—→"到"；"/"择一→"或"；"%"→"百分之N"。
- 绝对禁止将纯数字串转换为中文数字表达（如"二百四十二"），所有数字必须采用逐位读法（如"二四二"）
- 数码/技术例外保持原样：错误/状态码（如"404 Not Found"）、型号/代号（如"RTX-5090"）、版本/规范号（如"v1.2.3""ISO 27001"）、命令/变量/路径等；显示保留原文，读音按"404""RTX 5090"，不读连接符。
""" if self._policy.get('key') not in ['none', 'cn_balanced_new', 'cn_speed_experimental', 'cn_speed_experimental_2'] else "")
                    )
                )
                polished_contents = _parse_json_array(strict_resp, len(batch_entries))
                # 还原温度
                try:
                    self.translation_api.temperature = orig_temp
                except Exception:
                    pass

            # 如果依然不合格，则对不合格位点进行单条补救请求
            if polished_contents is None:
                polished_contents = [None] * len(batch_entries)

            def _bad(text: Optional[str]) -> bool:
                if text is None:
                    return True
                if re.search(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}", text):
                    return True
                if re.search(r"^\s*#?\s*\d+\s*$", text, flags=re.MULTILINE):
                    return True
                if re.search(r"字幕\s*#?\s*\d+", text):
                    return True
                return False

            if any(_bad(x) for x in polished_contents):
                # 如果有不合格元素，重新润色整个批次（最多重试10次）
                if retry_count < 10:
                    batch_info = f"批次{batch_number}" if batch_number is not None else "当前批次"
                    logger.warning(f"触发批次补救：重新润色{batch_info} (重试{retry_count + 1}/10)")
                    return self.polish_subtitle_batch(entries, start_idx, end_idx, retry_count + 1, batch_number)
                else:
                    batch_info = f"批次{batch_number}" if batch_number is not None else "当前批次"
                    logger.warning(f"{batch_info}补救失败达到最大重试次数，使用原文兜底")

            # 仍为空或不足位置，用原文兜底
            for i in range(len(polished_contents)):
                if polished_contents[i] is None or polished_contents[i] == "":
                    polished_contents[i] = batch_entries[i].content
            # 严格防守：如有超出/不足，进行自愈
            if len(polished_contents) > len(batch_entries):
                # 仅取前N条
                logger.warning("润色解析得到的条目多于期望，已截断多余部分")
                polished_contents = polished_contents[:len(batch_entries)]
            elif len(polished_contents) < len(batch_entries):
                # 末尾使用原文兜底扩充
                deficit = len(batch_entries) - len(polished_contents)
                logger.warning("润色解析得到的条目少于期望，使用原文内容兜底补足 %d 条", deficit)
                polished_contents += [e.content for e in batch_entries[len(polished_contents):]]

            # 确保结果数量匹配
            if len(polished_contents) != len(batch_entries):
                logger.warning(f"润色结果数量不匹配: 期望 {len(batch_entries)} 个, 得到 {len(polished_contents)} 个")

                # 如果数量不匹配，尝试拆分批次重新处理
                if len(batch_entries) > 2:
                    logger.info("拆分批次并重新润色...")
                    mid_point = (start_idx + end_idx) // 2
                    first_half = self.polish_subtitle_batch(entries, start_idx, mid_point, 0, batch_number)
                    second_half = self.polish_subtitle_batch(entries, mid_point, end_idx, 0, batch_number)
                    return first_half + second_half
                else:
                    # 逐个处理
                    logger.info("逐个润色条目...")
                    result = []
                    for i in range(start_idx, end_idx):
                        single_entry = self.polish_subtitle_batch(entries, i, i+1, 0, batch_number)
                        result.extend(single_entry)
                    return result

            # 创建润色后的条目
            polished_entries = []
            for i, entry in enumerate(batch_entries):
                polished_content = self.clean_polished_content(polished_contents[i])
                polished_entries.append(
                    SRTEntry(entry.number, entry.start_time, entry.end_time, polished_content)
                )

            return polished_entries

        except Exception as e:
            logger.error(f"润色批次失败: {e}")
            # 如果发生异常，尝试拆分批次
            if end_idx - start_idx > 1:
                logger.info("润色异常，尝试拆分批次...")
                mid_point = (start_idx + end_idx) // 2
                first_half = self.polish_subtitle_batch(entries, start_idx, mid_point, 0, batch_number)
                second_half = self.polish_subtitle_batch(entries, mid_point, end_idx, 0, batch_number)
                return first_half + second_half
            else:
                # 如果只有一个条目但润色失败，返回原始条目
                logger.warning(f"无法润色条目 {entries[start_idx].number}，使用原始内容")
                return [entries[start_idx]]

    def parse_polished_result(self, polished_text: str, expected_count: int) -> List[str]:
        """解析润色后的文本，分割成独立的字幕内容"""
        # 尝试使用分隔符分割
        separator_patterns = [
            r"===SUBTITLE_SEPARATOR_\d+===",
            r"===SUBTITLE_SEPARATOR===",
            r"---",
            r"##"
        ]

        for pattern in separator_patterns:
            parts = re.split(pattern, polished_text)
            # 删除空块
            parts = [p for p in parts if p.strip()]
            if len(parts) == expected_count:
                return [part.strip() for part in parts]

        # 如果分隔符失败，尝试按行数分割
        lines = polished_text.strip().split('\n')
        if len(lines) >= expected_count:
            # 尝试均匀分配行数
            lines_per_subtitle = len(lines) // expected_count
            result = []
            for i in range(expected_count):
                start_line = i * lines_per_subtitle
                end_line = (i + 1) * lines_per_subtitle if i < expected_count - 1 else len(lines)
                content = '\n'.join(lines[start_line:end_line]).strip()
                result.append(content)
            # 若存在空块，用上一非空块或原文整体兜底
            fallback = polished_text.strip()
            last_non_empty = None
            for idx, val in enumerate(result):
                if val:
                    last_non_empty = val
                else:
                    result[idx] = last_non_empty or fallback
            return result

        # 最后的备选方案，返回原始内容
        return [polished_text.strip()] * expected_count

    def _strip_terminal_chinese_periods(self, text: str) -> str:
        """去掉每一行行末的中文句号“。”（保留其他标点），避免字幕行末画蛇添足。"""
        if not text:
            return text

        closers = set('”"’\'」』）)】]〕》〉>')

        def strip_one_line(line: str) -> str:
            raw = line
            s = line.rstrip()
            if not s:
                return raw

            i = len(s)
            while i > 0 and s[i - 1] in closers:
                i -= 1

            if i > 0 and s[i - 1] == "。":
                candidate = (s[:i - 1] + s[i:]).rstrip()
                if re.search(r"[\w\u4e00-\u9fff]", candidate):
                    return candidate
            return s

        return "\n".join(strip_one_line(line) for line in text.splitlines())

    def clean_polished_content(self, content: str) -> str:
        """清理润色后的内容，移除不必要的格式"""
        if not content:
            return ""

        result = content.strip()

        # 移除可能残留的分隔符（更鲁棒）
        result = re.sub(r"===SUBTITLE_SEPARATOR[_\d]*===", "", result, flags=re.IGNORECASE)
        # 兼容带空格/不同=数量/大小写/行整段
        result = re.sub(r"^[=\-\s]*SUBTITLE_SEPARATOR[^\n]*$", "", result, flags=re.MULTILINE | re.IGNORECASE)
        result = re.sub(r"^---\s*", "", result, flags=re.MULTILINE)
        result = re.sub(r"\s*---$", "", result, flags=re.MULTILINE)

        # 移除润色说明性前缀（更鲁棒，覆盖“字幕#N (…): ”形态）
        prefixes = [
            r"^润色后[:：]?\s*",
            r"^润色结果[:：]?\s*",
            r"^优化后[:：]?\s*",
            r"^修改后[:：]?\s*",
        ]
        for prefix in prefixes:
            result = re.sub(prefix, "", result, flags=re.MULTILINE)

        # 移除形如“字幕#201 (时长x秒，当前y字，建议z字): ”或“字幕201：”的提示头
        result = re.sub(r"^\s*字幕\s*#?\s*\d+\s*(?:\([^\n\)]*\))?\s*[:：]\s*", "", result, flags=re.MULTILINE)
        # 兼容模型返回“#201 (时长…): …”或独立一行“#201 (…):”
        result = re.sub(r"^\s*#\s*\d+\s*(?:\([^\n\)]*\))?\s*[:：]\s*", "", result, flags=re.MULTILINE)
        result = re.sub(r"^\s*#\s*\d+\s*(?:\([^\n\)]*\))?\s*[:：]\s*$", "", result, flags=re.MULTILINE)

        # 强制移除模型夹带的SRT时间轴行或孤立时间戳，避免“一条里出现两个时间轴”
        timecode_line = r"^\s*\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}\s*$"
        single_time = r"^\s*\d{2}:\d{2}:\d{2},\d{3}\s*$"
        result = re.sub(timecode_line, "", result, flags=re.MULTILINE)
        result = re.sub(single_time, "", result, flags=re.MULTILINE)
        # 若时间码出现在同一行的中间，也一并清除
        inline_timecode = r"\s*\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}\s*"
        inline_single_time = r"\s*\d{2}:\d{2}:\d{2},\d{3}\s*"
        result = re.sub(inline_timecode, " ", result)
        result = re.sub(inline_single_time, " ", result)

        # 仅移除“整行外层包裹”的英文直引号，避免误删正文里真正的引号
        def _strip_wrapping_ascii_quotes(line: str) -> str:
            s = line.strip()
            if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
                return s[1:-1].strip()
            return s
        result = "\n".join(_strip_wrapping_ascii_quotes(l) for l in result.splitlines())

        # 移除“纯数字”垃圾行（模型偶发输出的序号）
        result = re.sub(r"^\s*\d+\s*$", "", result, flags=re.MULTILINE)

        # 清理多余的空白
        result = re.sub(r'\n\s*\n', '\n', result)  # 多个换行合并
        result = re.sub(r'^\s+|\s+$', '', result, flags=re.MULTILINE)  # 行首尾空白

        # 可选：引号转换为折角引号（默认关闭，避免影响现有输出）
        if getattr(self, "corner_quotes", False):
            result = self.convert_quotes_to_corner(result)

        # 专业中文字幕：去掉行末的中文句号“。”（保留其他标点）
        result = self._strip_terminal_chinese_periods(result)

        return result.strip()

    def parse_srt_file(self, srt_file_path: str) -> List[SRTEntry]:
        """解析SRT文件，返回字幕条目列表"""
        try:
            with open(srt_file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            # 确保文件末尾有换行符
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

    def process_batch(self, batch_number: int, entries: List[SRTEntry], output_base: str, progress_manager) -> bool:
        """处理单个批次（多线程执行）"""
        try:
            batch_start = (batch_number - 1) * self.batch_size
            batch_end = min(batch_start + self.batch_size, len(entries))

            thread_name = threading.current_thread().name
            logger.info(f"[{thread_name}] 润色批次 {batch_number} (条目 {batch_start+1}-{batch_end})")

            # 润色当前批次
            polished_batch = self.polish_subtitle_batch(entries, batch_start, batch_end, 0, batch_number)

            # 保存批次结果
            batch_output_file = f"{output_base}_polish_batch{batch_number}.srt"
            self.write_srt_entries(polished_batch, batch_output_file)
            logger.info(f"[{thread_name}] 已将润色批次 {batch_number} 写入 {batch_output_file}")

            # 标记批次完成
            progress_manager.mark_batch_completed(batch_number)

            return True
        except Exception as e:
            logger.error(f"处理润色批次 {batch_number} 出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def polish_srt_file(self, input_file: str, output_file: str, resume: bool = True, auto_verify: bool = True) -> bool:
        """润色整个SRT文件"""
        try:
            # 解析输入文件
            entries = self.parse_srt_file(input_file)
            if not entries:
                logger.error(f"在 {input_file} 中未找到字幕条目")
                return False

            logger.info(f"开始润色 {len(entries)} 个字幕条目")

            # 仅记录一次系统提示词，避免多线程重复
            with self._log_lock:
                if not self._system_prompt_logged:
                    system_message = self.build_polish_system_message()
                    logger.info("系统提示词(润色)：\n%s", system_message)
                    self._system_prompt_logged = True

            # 创建输出目录
            output_dir = os.path.dirname(output_file)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir)

            # 设置进度管理
            output_base = os.path.splitext(output_file)[0]
            total_batches = (len(entries) + self.batch_size - 1) // self.batch_size
            progress_manager = ProgressManager(output_base, total_batches, "_polish")

            # 断点续接处理
            if resume:
                progress_manager.recover_from_batch_files()
                if progress_manager.is_all_completed():
                    logger.info("所有批次已完成，正在合并输出文件...")
                    self.merge_batch_files(output_base, output_file, total_batches)
                    logger.info(f"润色已完成。输出在 {output_file}")

                    # 自动校验
                    if auto_verify:
                        self.last_verify_perfect = self.auto_verify_result(input_file, output_file)

                    return True
            else:
                # 清除旧的批次文件
                progress_manager = ProgressManager(output_base, total_batches, "_polish")
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

            # 多线程或单线程处理
            if self.max_workers > 1:
                logger.info(f"使用 {self.max_workers} 个线程并行润色")
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    future_to_batch = {
                        executor.submit(self.process_batch, batch_number, entries, output_base, progress_manager): batch_number
                        for batch_number in remaining_batches
                    }

                    for future in concurrent.futures.as_completed(future_to_batch):
                        batch_number = future_to_batch[future]
                        try:
                            success = future.result()
                            if not success:
                                logger.warning(f"润色批次 {batch_number} 处理失败")
                        except Exception as e:
                            logger.error(f"处理润色批次 {batch_number} 时出现异常: {e}")
            else:
                # 单线程处理
                for batch_number in remaining_batches:
                    self.process_batch(batch_number, entries, output_base, progress_manager)
                    time.sleep(0.5)  # 短暂休息避免API限制

            # 合并批次文件
            self.merge_batch_files(output_base, output_file, total_batches)
            logger.info(f"润色完成。输出在 {output_file}")

            # 自动校验结果
            if auto_verify:
                self.last_verify_perfect = self.auto_verify_result(input_file, output_file)

            return True

        except Exception as e:
            logger.error(f"润色SRT文件出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def merge_batch_files(self, output_base: str, output_file: str, total_batches: int) -> None:
        """合并所有批次文件到输出文件"""
        try:
            all_entries = []

            for batch_number in range(1, total_batches + 1):
                batch_file = f"{output_base}_polish_batch{batch_number}.srt"
                if os.path.exists(batch_file):
                    try:
                        batch_entries = self.parse_srt_file(batch_file)
                        all_entries.extend(batch_entries)
                    except Exception as e:
                        logger.error(f"读取润色批次文件 {batch_file} 出错: {e}")

            # 按字幕序号排序
            all_entries.sort(key=lambda entry: entry.number)

            # 写入输出文件
            self.write_srt_entries(all_entries, output_file)
            logger.info(f"已合并 {len(all_entries)} 个润色条目到 {output_file}")

        except Exception as e:
            logger.error(f"合并润色批次文件出错: {e}")
            raise

    def write_srt_entries(self, entries: List[SRTEntry], output_file: str) -> None:
        """将字幕条目列表写入SRT文件"""
        try:
            cleaned_entries: List[SRTEntry] = []
            junk_prefix = re.compile(r"^\s*(?:字幕\s*#?\s*\d+|#\s*\d+)\s*(?:\([^\n\)]*\))?\s*[:：]\s*", re.MULTILINE)
            inline_timecode = re.compile(r"\s*\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}\s*")
            inline_single_time = re.compile(r"\s*\d{2}:\d{2}:\d{2},\d{3}\s*")
            sep_line = re.compile(r"^[=\-\s]*SUBTITLE_SEPARATOR[^\n]*$", re.IGNORECASE | re.MULTILINE)
            for e in entries:
                text = e.content
                # 二次清洗，兜底模型夹带的提示头/编号
                text = junk_prefix.sub("", text)
                text = inline_timecode.sub(" ", text)
                text = inline_single_time.sub(" ", text)
                text = sep_line.sub("", text)
                text = re.sub(r"===SUBTITLE_SEPARATOR[_\d]*===", "", text, flags=re.IGNORECASE)
                text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
                text = re.sub(r"\n{3,}", "\n\n", text)
                text = text.strip()
                cleaned_entries.append(SRTEntry(e.number, e.start_time, e.end_time, text))

            with open(output_file, 'w', encoding='utf-8') as f:
                for i, entry in enumerate(cleaned_entries):
                    f.write(entry.to_string())
                    if i < len(cleaned_entries) - 1:
                        f.write("\n")

            logger.debug(f"已将 {len(entries)} 个条目写入 {output_file}")

        except Exception as e:
            logger.error(f"写入输出文件 {output_file} 出错: {e}")
            raise

    def auto_verify_result(self, original_file: str, polished_file: str) -> bool:
        """自动校验润色结果（内部实现，不依赖外部校验器）。

        校验项：
        - 条目总数是否一致
        - 每个编号的起止时间是否一致

        产出：
        - self.last_verify_summary 概要数据，供 GUI 展示（不生成报告文件）
        """
        try:
            logger.info(f"正在自动校验润色结果...")
            logger.info(f"原始文件: {original_file}")
            logger.info(f"润色文件: {polished_file}")

            source_entries = self.parse_srt_file(original_file)
            translated_entries = self.parse_srt_file(polished_file)

            source_dict = {e.number: e for e in source_entries}
            translated_dict = {e.number: e for e in translated_entries}

            source_numbers = set(source_dict.keys())
            translated_numbers = set(translated_dict.keys())

            missing_from_translated = sorted(list(source_numbers - translated_numbers))
            extra_in_translated = sorted(list(translated_numbers - source_numbers))

            # 时间码不匹配列表
            time_mismatches = []  # List[Tuple[number, issues]]
            for number in sorted(source_numbers & translated_numbers):
                src = source_dict[number]
                dst = translated_dict[number]
                issues = []
                if src.start_time != dst.start_time:
                    issues.append(f"起始时间码不匹配: 源={src.start_time}, 译={dst.start_time}")
                if src.end_time != dst.end_time:
                    issues.append(f"结束时间码不匹配: 源={src.end_time}, 译={dst.end_time}")
                if issues:
                    time_mismatches.append((number, issues))

            total_source = len(source_entries)
            total_translated = len(translated_entries)
            perfect = (
                total_source == total_translated
                and not missing_from_translated
                and not extra_in_translated
                and not time_mismatches
            )

            # 不再生成报告文件（按产品要求裁剪功能）

            # 保存摘要，供GUI使用
            self.last_verify_summary = {
                'perfect': perfect,
                'total_source': total_source,
                'total_translated': total_translated,
                'time_mismatch_count': len(time_mismatches),
                'missing_count': len(missing_from_translated),
                'extra_count': len(extra_in_translated)
            }

            if perfect:
                logger.info("✓ 润色结果校验通过！时间码和条目数量完全匹配。")
            else:
                logger.warning(
                    "⚠ 润色结果存在不匹配：" +
                    f" 源={total_source}, 润色={total_translated}, " +
                    f"时间码不匹配={len(time_mismatches)}, 缺少={len(missing_from_translated)}, 多余={len(extra_in_translated)}"
                )

            return perfect

        except Exception as e:
            logger.error(f"自动校验出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # 出错时不要阻断主流程，但标记为失败
            self.last_verify_summary = None
            return False

def main():
    parser = argparse.ArgumentParser(description="SRT字幕润色工具 - 优化字幕阅读流畅性")
    parser.add_argument("input_file", help="输入SRT文件路径")
    parser.add_argument("output_file", help="输出SRT文件路径")
    parser.add_argument("--api", choices=["deepseek", "grok", "custom"],
                      default=(st.DEFAULT_API_TYPE if ('st' in globals() and st is not None and hasattr(st, 'DEFAULT_API_TYPE')) else "grok"),
                      help="使用的API类型 (默认继承翻译器)")
    parser.add_argument("--api-key", default=(st.DEFAULT_API_KEY if ('st' in globals() and st is not None and hasattr(st, 'DEFAULT_API_KEY')) else ""), help="API密钥 (默认继承翻译器)")
    parser.add_argument("--model", help="模型名称 (默认: 根据API类型自动选择)")
    parser.add_argument("--api-endpoint", help="自定义API端点URL (仅当--api=custom时使用)")
    parser.add_argument("--batch-size", type=int, default=10, help="每批处理的字幕条数 (默认: 10)")
    parser.add_argument("--context-size", type=int, default=2, help="上下文条目数量 (默认: 2)")
    parser.add_argument("--temperature", type=float, default=0.3, help="采样温度(0~1，润色建议用低值，默认0.3)")
    parser.add_argument("--threads", type=int, default=3, help="并行处理的线程数 (默认: 3)")
    parser.add_argument("--no-resume", action="store_true", help="不使用断点续接，重新开始润色")
    parser.add_argument("--no-verify", action="store_true", help="不自动校验润色结果")

    args = parser.parse_args()

    try:
        # 检查文件是否存在
        if not os.path.exists(args.input_file):
            logger.error(f"输入文件不存在: {args.input_file}")
            return 1

        logger.info(f"开始润色字幕文件 {args.input_file}")
        logger.info(f"输出到 {args.output_file}")
        logger.info(f"使用 {args.api} API，批次大小 {args.batch_size}，温度 {args.temperature}")

        # 自定义API端点处理
        if args.api == "custom" and args.api_endpoint:
            if 'st' in globals() and st is not None:
                st.API_ENDPOINTS["custom"] = args.api_endpoint
            logger.info(f"使用自定义API端点: {args.api_endpoint}")

        if args.model:
            logger.info(f"使用自定义模型: {args.model}")
            if 'st' in globals() and st is not None:
                st.DEFAULT_MODELS[args.api] = args.model

        # 创建润色器
        polisher = SRTPolisher(
            api_type=args.api,
            api_key=args.api_key,
            batch_size=args.batch_size,
            context_size=args.context_size,
            max_workers=args.threads,
            model_name=args.model,
            temperature=args.temperature
        )

        # 执行润色
        success = polisher.polish_srt_file(
            args.input_file,
            args.output_file,
            resume=not args.no_resume,
            auto_verify=not args.no_verify
        )

        if success:
            logger.info("润色成功完成!")
            return 0
        else:
            logger.error("润色失败")
            return 1

    except Exception as e:
        logger.error(f"润色过程出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return 1

if __name__ == "__main__":
    exit(main())
