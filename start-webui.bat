@echo off
cd /d "%~dp0webui"
echo 正在启动 mynews Web UI...
start "" http://localhost:8080
python3 server.py
pause
