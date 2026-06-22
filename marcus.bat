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
echo  [1] Start All (Production: Pi + Backend + QQ Bot + Frontend)
echo  [2] Start Local Dev (Backend:8000 + Pi:3001 + Frontend:3000)
echo  [3] Start Backend Only (port 8000)
echo  [4] Start Pi Server Only (port 3001)
echo  [5] Start Frontend Only (port 3000)
echo  [6] Stop All Services
echo  [7] Install Dependencies
echo  [0] Exit
echo.
echo.

set CHOICE=
set /p CHOICE="Select option: "

if "%CHOICE%"=="1" goto START_ALL
if "%CHOICE%"=="2" goto START_LOCAL
if "%CHOICE%"=="3" goto START_BACKEND
if "%CHOICE%"=="4" goto START_PI
if "%CHOICE%"=="5" goto START_FRONTEND
if "%CHOICE%"=="6" goto STOP_ALL
if "%CHOICE%"=="7" goto INSTALL
if "%CHOICE%"=="0" goto EXIT

echo.
echo [ERROR] Invalid option!
timeout /t 2 >nul
goto MENU

REM ============================================
REM Production Start
REM ============================================
:START_ALL
call scripts\start_all.bat
goto MENU

REM ============================================
REM Local Dev Start (Backend + Pi + Frontend)
REM ============================================
:START_LOCAL
cls
echo.
echo ================================================
echo Marcus - Local Dev Environment
echo ================================================
echo.
echo Starting services:
echo   [1] Backend   (FastAPI, port 8000)
echo   [2] Pi Server (Node.js, port 3001)
echo   [3] Frontend  (Vite,    port 3000)
echo.
echo ================================================
echo.

echo [1/3] Starting Backend on port 8000...
start "Marcus-Backend" cmd /c "cd /d %~dp0backend && title Marcus Backend && echo ===== Marcus Backend ===== && echo API docs: http://localhost:8000/docs && echo. && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
timeout /t 2 >nul

echo [2/3] Starting Pi Server on port 3001...
start "Marcus-PiServer" cmd /c "cd /d %~dp0servers\pi-server && title Marcus Pi Server && echo ===== Marcus Pi Server ===== && echo Port: 3001 && echo. && npx tsx src\index.ts"
timeout /t 2 >nul

echo [3/3] Starting Frontend on port 3000...
start "Marcus-Frontend" cmd /c "cd /d %~dp0frontend && title Marcus Frontend && echo ===== Marcus Frontend ===== && echo Dashboard: http://localhost:3000 && echo. && npm run dev"
timeout /t 2 >nul

echo.
echo ================================================
echo Services started in separate windows:
echo   http://localhost:3000 - Frontend
echo   http://localhost:3001 - Pi Server
echo   http://localhost:8000 - Backend API (/docs)
echo ================================================
echo.
echo Close each window to stop that service.
echo.
pause
goto MENU

REM ============================================
REM Individual Start
REM ============================================
:START_BACKEND
echo.
echo Starting Backend (port 8000)...
start "Marcus-Backend" cmd /c "cd /d %~dp0backend && title Marcus Backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 && pause"
echo Backend: http://localhost:8000/docs
pause
goto MENU

:START_PI
echo.
echo Starting Pi Server (port 3001)...
start "Marcus-PiServer" cmd /c "cd /d %~dp0servers\pi-server && title Marcus Pi Server && npx tsx src\index.ts && pause"
echo Pi Server: http://localhost:3001
pause
goto MENU

:START_FRONTEND
echo.
echo Starting Frontend (port 3000)...
start "Marcus-Frontend" cmd /c "cd /d %~dp0frontend && title Marcus Frontend && npm run dev && pause"
echo Frontend: http://localhost:3000
pause
goto MENU

REM ============================================
REM Stop / Install
REM ============================================
:STOP_ALL
call scripts\stop.bat
pause
goto MENU

:INSTALL
echo.
echo [1/3] Installing Pi Server dependencies...
cd /d "%~dp0servers\pi-server"
call npm install
cd /d "%~dp0"
echo.
echo [2/3] Installing Frontend dependencies...
cd /d "%~dp0frontend"
call npm install
cd /d "%~dp0"
echo.
echo [3/3] Installing Backend dependencies...
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
