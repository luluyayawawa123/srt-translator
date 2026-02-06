@echo off
chcp 65001 >nul
echo ========================================
echo     SRT Translation Tool Launcher
echo ========================================
echo Checking system environment...
echo.

rem Set Python encoding
set PYTHONIOENCODING=utf-8

rem Check Python installation
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Python not found or not in PATH
    echo.
    echo Please install Python 3.8+ from: https://www.python.org/downloads/
    echo IMPORTANT: Check "Add Python to PATH" during installation
    echo.
    echo After installation, restart your computer and try again.
    echo.
    echo Press any key to exit...
    pause >nul
    exit /b 1
)

echo OK: Python found
python --version
echo.

rem Check pip availability
echo Checking pip availability...
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: pip not found, trying python -m pip
    python -m pip --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: pip not available
        echo Please reinstall Python with pip included
        echo.
        echo Press any key to exit...
        pause >nul
        exit /b 1
    )
    echo OK: pip found (using python -m pip)
    set PIP_CMD=python -m pip
) else (
    echo OK: pip ready
    set PIP_CMD=pip
)

rem Check dependencies
echo Checking dependencies...
python -c "import requests, colorama" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo Required packages not found. Installing now...
    echo This may take 1-3 minutes, please wait...
    echo Note: The application will start automatically after installation.
    echo.
    
    echo Installing requests and colorama...
    %PIP_CMD% install requests colorama
    
    rem Don't rely on pip exit code, check if packages are actually available
    timeout /t 2 /nobreak >nul
    
    rem Verify packages are actually importable
    python -c "import requests, colorama" >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo INFO: Packages installed but need environment refresh
        echo This is normal for first-time installation
        echo.
        echo Automatically restarting script to refresh environment...
        timeout /t 2 /nobreak >nul
        
        rem Restart the script
        echo Restarting...
        "%~f0"
        exit /b 0
    )
    
    echo OK: All packages verified and ready!
    echo Starting application in 3 seconds...
    timeout /t 3 /nobreak >nul
) else (
    echo OK: All dependencies ready
)

echo.
echo ========================================
echo     Launching SRT Translation Tool
echo ========================================
echo.

rem Start the GUI application
python srt_gui.py

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Application failed to start
    echo.
    echo Possible solutions:
    echo 1. Check if srt_gui.py exists in current directory
    echo 2. Ensure all dependencies are installed correctly
    echo 3. Try running as administrator
    echo 4. Check Python installation and PATH configuration
    echo.
    echo Press any key to exit...
    pause >nul
) else (
    echo.
    echo Application closed normally.
    echo Thank you for using SRT Translation Tool!
    timeout /t 3 >nul
)
