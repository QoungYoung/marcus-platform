@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0..\backend\venv\Scripts\activate.bat"
echo ========================================
echo  Marcus 资金流同步守护进程
echo  交易日 9-11,13-14 时的 17/28/33/48 分
echo ========================================
echo.
echo 窗口不要关闭，最小化即可。Ctrl+C 停止。
echo.
python sync_daemon.py
pause
