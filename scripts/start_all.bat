@echo off
REM ============================================
REM Marcus - Start All Services
REM ============================================
setlocal enabledelayedexpansion

set ROOT=%~dp0\..

echo.
echo ================================================
echo   Marcus AI Trading Platform - Startup
echo ================================================
echo.

REM --- Kill existing services on known ports ---
echo [*] Stopping old processes...
for %%p in (3001 8000 5173) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p " ^| findstr "LISTENING"') do (
        taskkill /PID %%a /F >nul 2>&1
    )
)
timeout /t 2 >nul
echo [*] Old processes cleared
echo.

REM --- 1. Pi Server (Node.js) ---
echo [1/3] Starting Pi Server (port 3001)...
start "Marcus-PiServer" cmd /c "cd /d %ROOT%\servers\pi-server && npx tsx src\index.ts"
timeout /t 5 >nul

REM --- 2. Backend + QQ Bot (Python) ---
echo [2/3] Starting Backend + QQ Bot (port 8000)...
start "Marcus-Backend" cmd /c "cd /d %ROOT%\backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
timeout /t 5 >nul

REM --- 3. Frontend (Vite) ---
echo [3/3] Starting Frontend (Vite port 5173)...
start "Marcus-Frontend" cmd /c "cd /d %ROOT%\frontend && npm run dev"

timeout /t 3 >nul

echo.
echo ================================================
echo   All services launched!
echo.
echo   Pi Server : http://localhost:3001/health
echo   Backend   : http://localhost:8000/docs
echo   Frontend  : http://localhost:5173
echo   QQ Bot    : auto-connected
echo ================================================
echo.
pause
