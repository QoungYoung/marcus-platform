@echo off
REM ============================================
REM Marcus AI Trading Platform - Main Menu
REM ============================================

:MENU
cls
echo.
echo ================================================
echo.
echo          Marcus AI Trading Platform
echo.
echo ================================================
echo.
echo  [1] Start All (Pi + Backend + QQ Bot + Frontend)
echo  [2] Start Backend + QQ Bot Only
echo  [3] Start Pi Server Only
echo  [4] Start Frontend Only
echo  [5] Stop All Services
echo  [6] Install Dependencies
echo  [0] Exit
echo.
echo.

set CHOICE=
set /p CHOICE="Select option: "

if "%CHOICE%"=="1" goto START_ALL
if "%CHOICE%"=="2" goto START_BACKEND_QQ
if "%CHOICE%"=="3" goto START_PI
if "%CHOICE%"=="4" goto START_FRONTEND
if "%CHOICE%"=="5" goto STOP_ALL
if "%CHOICE%"=="6" goto INSTALL
if "%CHOICE%"=="0" goto EXIT

echo.
echo [ERROR] Invalid option!
timeout /t 2 >nul
goto MENU

:START_ALL
call scripts\start_all.bat
goto MENU

:START_BACKEND_QQ
echo.
echo Starting Backend + QQ Bot (port 8000)...
start "Marcus-Backend" cmd /c "cd /d %~dp0backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 && pause"
echo.
echo Backend window opened. Close it to stop.
pause
goto MENU

:START_PI
echo.
echo Starting Pi Server (port 3001)...
start "Marcus-PiServer" cmd /c "cd /d %~dp0servers\pi-server && npx tsx src\index.ts && pause"
echo.
echo Pi Server window opened. Close it to stop.
pause
goto MENU

:START_FRONTEND
echo.
echo Starting Frontend (port 5173)...
start "Marcus-Frontend" cmd /c "cd /d %~dp0frontend && npm run dev && pause"
echo.
echo Frontend window opened. Close it to stop.
pause
goto MENU

:STOP_ALL
call scripts\stop.bat
pause
goto MENU

:INSTALL
echo.
echo Installing Pi Server dependencies...
cd /d "%~dp0servers\pi-server"
call npm install
cd /d "%~dp0"
echo.
echo Installing Frontend dependencies...
cd /d "%~dp0frontend"
call npm install
cd /d "%~dp0"
echo.
echo Installing Backend dependencies...
cd /d "%~dp0backend"
pip install -r requirements.txt -q
cd /d "%~dp0"
echo.
echo All dependencies installed.
pause
goto MENU

:EXIT
cls
echo.
echo Thank you for using Marcus AI Trading Platform!
echo.
pause
exit /b 0
