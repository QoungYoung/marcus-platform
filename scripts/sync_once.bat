@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0..\backend\venv\Scripts\activate.bat"
echo [%time%] 执行资金流同步...
python sync_em_to_pg.py
echo [%time%] 完成
pause
