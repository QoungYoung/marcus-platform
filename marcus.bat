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
echo  [1] Start All (Production: Backend + Worker + Frontend + DSH容器)
echo  [2] Start Local Dev (Backend:8000 + DSH容器:3001 + Frontend:3000)
echo  [3] Start Backend Only (port 8000)
echo  [4] Start DSH 容器 Only (Docker, port 3001 — 替代 Pi Server)
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
echo   [2] DSH 服务   (Docker, port 3001 — 替代 Pi Server，见 docker compose)
echo   [3] Frontend  (Vite,    port 3000)
echo.
echo ================================================
echo.

echo [1/4] Starting Backend on port 8000...
start "Marcus-Backend" cmd /c "cd /d %~dp0backend && title Marcus Backend && echo ===== Marcus Backend ===== && echo API docs: http://localhost:8000/docs && echo. && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
timeout /t 2 >nul

echo [2/4] Starting Worker (scheduler + monitors + QQ Bot)...
start "Marcus-Worker" cmd /c "cd /d %~dp0backend && title Marcus Worker && echo ===== Marcus Worker ===== && echo Scheduler + monitors + QQ Bot && echo. && python -m app.worker_main"
timeout /t 2 >nul

echo [3/4] DSH 服务已由 Docker 提供（docker compose up dsh，端口 3001）...
echo   Pi Server 已移除，AI 桥接由 DSH 容器承担

echo [4/4] Starting Frontend on port 3000...
start "Marcus-Frontend" cmd /c "cd /d %~dp0frontend && title Marcus Frontend && echo ===== Marcus Frontend ===== && echo Dashboard: http://localhost:3000 && echo. && npm run dev"
timeout /t 2 >nul

echo.
echo ================================================
echo Services started in separate windows:
echo   http://localhost:3000 - Frontend
echo   http://localhost:3001 - DSH 服务 (Docker)
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
echo Pi Server 已移除 — AI 桥接由 DSH 容器承担（docker compose up dsh，端口 3001）
echo 如需启动: docker compose -f docker/docker-compose.yml up -d dsh
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
echo [1/3] Pi Server 已移除（DSH 容器替代），跳过安装...
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
