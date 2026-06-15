@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0..\backend\venv\Scripts\activate.bat"
echo ========================================
echo  东财 API 代理服务 :8199
echo  转发 push2.eastmoney.com 请求
echo ========================================
echo.
echo 窗口不要关闭。Ctrl+C 停止。
echo.
python em_proxy_server.py
pause
