@echo off
chcp 65001 >nul
echo 启动 SRT 字幕翻译工具 GUI...

rem 设置Python环境变量解决编码问题
set PYTHONIOENCODING=utf-8

python srt_translator_gui.py
if %errorlevel% neq 0 (
    echo.
    echo 启动失败，请检查是否安装了Python和必要的依赖。
    echo 可以运行以下命令安装依赖：
    echo pip install requests colorama tkinter
    pause
) 