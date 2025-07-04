@echo off
chcp 65001 >nul
echo 启动 SRT 字幕翻译工具 GUI...

rem 设置Python环境变量解决编码问题
set PYTHONIOENCODING=utf-8

rem 检查虚拟环境是否存在
if exist ".venv\Scripts\python.exe" (
    echo 使用项目虚拟环境启动...
    .venv\Scripts\python.exe srt_translator_gui.py
) else (
    echo 虚拟环境不存在，使用系统Python启动...
    python srt_translator_gui.py
)

if %errorlevel% neq 0 (
    echo.
    echo 启动失败！
    echo.
    if exist ".venv\Scripts\python.exe" (
        echo 建议检查虚拟环境中的依赖：
        echo .venv\Scripts\pip.exe install -r requirements.txt
    ) else (
        echo 建议安装依赖：
        echo pip install -r requirements.txt
    )
    echo.
    pause
) 