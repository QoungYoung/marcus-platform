@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0..\backend\venv\Scripts\activate.bat"

echo ========================================
echo  Marcus 本地东财代理服务
echo ========================================
echo.
echo 启动东财 API 代理 :8199
start "EM Proxy" python em_proxy_server.py
echo 已启动（新窗口，不要关闭）
echo.
echo ========================================
echo  代理地址: http://localhost:8199
echo  服务器通过 FRP 隧道: 81.70.44.68:8199
echo ========================================
pause
