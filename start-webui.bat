@echo off
rem mynews Web UI 启动脚本
rem 优先使用 python，其次 python3（适配不同 Windows 环境）
cd /d "%~dp0webui"

where python >nul 2>nul
if %errorlevel%==0 (
    set PYCMD=python
) else (
    where python3 >nul 2>nul
    if %errorlevel%==0 (
        set PYCMD=python3
    ) else (
        echo [error] 未找到 python 或 python3，请先安装 Python
        pause
        exit /b 1
    )
)

echo 正在启动 mynews Web UI...
start "" http://localhost:8080
%PYCMD% server.py
pause
