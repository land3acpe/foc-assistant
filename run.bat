@echo off
REM FOC-Assistant V4 启动脚本 (CMD)
set PYTHON=D:\Python312\python.exe
set PYTHONIOENCODING=utf-8

if not exist "%PYTHON%" (
    echo Python not found at %PYTHON%
    pause
    exit /b 1
)

if "%DEEPSEEK_API_KEY%"=="" (
    echo ====================================
    echo Please set DeepSeek API Key:
    echo   set DEEPSEEK_API_KEY=sk-yourkey
    echo.
    echo Then run: run.bat
    echo ====================================
    pause
    exit /b 1
)

"%PYTHON%" -X utf8 agent.py %*
