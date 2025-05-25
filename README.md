# SRT字幕翻译工具

基于AI大语言模型的SRT字幕翻译工具，支持**断点续接**、**多线程并发**、**批量处理**等特性。提供现代化GUI界面（仅Windows）和跨平台命令行工具。

## 🚀 Python环境使用

### 安装依赖
```bash
pip install customtkinter requests colorama
```

### GUI界面（Windows系统）
```bash
# 双击运行
run_gui.bat

# 或命令行启动
python srt_translator_gui.py
```

GUI默认使用**自定义API模式**，预设为DeepSeek服务地址。只需填入API密钥即可使用。支持所有OpenAI兼容模式的API服务。

### 命令行使用（跨平台）
```bash
python srt_translator.py input.srt output_cn.srt --batch-size 30 --context-size 3 --threads 10
```

**主要参数：**
- `--api-key`: API密钥（或在代码中设置默认值）
- `--batch-size`: 批次大小，建议30（平衡速度和质量）
- `--context-size`: 上下文大小，建议3（提升翻译连贯性）
- `--threads`: 线程数，建议10（根据API限制调整）
- `--start/--end`: 翻译指定范围字幕

**支持的API：**
- DeepSeek: `--api deepseek`
- Grok: `--api grok`
- **自定义OpenAI兼容API**（推荐）: `--api custom --api-endpoint https://your-api.com/v1/chat/completions`

**使用示例：**
```bash
# 使用DeepSeek
python srt_translator.py input.srt output.srt --api deepseek --api-key sk-xxx

# 使用自定义API（OpenAI兼容）
python srt_translator.py input.srt output.srt --api custom \
  --api-endpoint https://api.deepseek.com/v1/chat/completions \
  --api-key sk-xxx --model deepseek-chat
```

## 📦 打包为Windows绿色软件

```bash
# 1. 安装打包工具
pip install pyinstaller

# 2. 检查环境
python check_build_env.py

# 3. 执行打包
python build_exe.py
```

打包后在 `SRT翻译工具/` 目录下生成可直接运行的exe文件，无需Python环境。

## 🛠️ 开发者信息

### 项目结构
```
├── srt_translator.py          # 核心翻译引擎
├── srt_translator_gui.py      # Windows GUI界面
├── srt_checker.py             # 字幕校验工具
├── build_exe.py               # 打包脚本
├── check_build_env.py         # 环境检查
├── run_gui.bat                # GUI启动脚本
├── requirements.txt           # 依赖列表
└── terminology.json           # 术语库文件
```

### 核心特性
- **断点续接**: 翻译中断后自动从断点继续
- **多线程**: 支持并发翻译，显著提升速度
- **术语库**: 保持专业术语翻译一致性（`terminology.json`）
- **上下文感知**: 提供前后文提升翻译质量
- **格式保护**: 完整保留SRT时间码和格式
- **OpenAI兼容**: 支持所有OpenAI Chat Completions API格式的服务

### 字幕校验
```bash
python srt_checker.py --source original.srt --translated translated.srt
```

验证翻译结果的完整性、时间码一致性和格式正确性。

## ❓ 常见问题

**Q: 翻译速度慢？**
A: 增加`--threads`和`--batch-size`参数，注意API调用限制。

**Q: 翻译质量不好？**
A: 减小`--batch-size`，增加`--context-size`，使用术语库。

**Q: 断点续接失败？**
A: 确保使用相同的输入输出路径，检查进度文件完整性。

**Q: GUI启动失败？**
A: 检查是否Windows系统，确认安装了所有依赖包。

**Q: API调用失败？**
A: 验证API密钥有效性，检查网络连接和API服务状态。

**Q: 想用其他AI服务？**
A: 大部分AI服务都兼容OpenAI API格式，使用自定义API模式即可。

---

💡 **提示**: 首次使用建议先用小范围测试：`--start 1 --end 50`
