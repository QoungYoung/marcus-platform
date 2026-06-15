@echo off
cd /d "%~dp0"
call "%~dp0..\backend\venv\Scripts\activate.bat"

echo ========================================
echo  Marcus EM Proxy Service
echo ========================================
echo.
echo Starting proxy on port 8199...
start "EM Proxy" python em_proxy_server.py
echo Started.
echo.
echo Proxy: http://localhost:8199
echo FRP:   81.70.44.68:8199
echo ========================================
pause
