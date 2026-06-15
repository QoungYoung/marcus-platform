@echo off
cd /d %~dp0..

:: 1. FRP Client
start "FRP" "G:\soft\frp_0.69.0_windows_amd64\frpc.exe" -c "G:\soft\frp_0.69.0_windows_amd64\frpc.toml"

:: 2. EM Proxy Server
call "F:\pythonProject\AITrade\marcus-platform\backend\venv\Scripts\activate.bat"
start "EM Proxy" python "F:\pythonProject\AITrade\marcus-platform\scripts\em_proxy_server.py"
