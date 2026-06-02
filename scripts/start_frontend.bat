@echo off
REM ============================================
REM Marcus Platform - Frontend Startup Script
REM ============================================

REM Change to project root directory
cd /d "%~dp0\.."

echo.
echo ============================================
echo Marcus AI Trading Platform - Frontend
echo ============================================
echo.

REM Check if Node.js is installed
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found!
    echo Please install Node.js from https://nodejs.org/
    pause
    exit /b 1
)

REM Check if npm is installed
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] npm not found!
    pause
    exit /b 1
)

REM Check if node_modules exists
if not exist "frontend\node_modules" (
    echo [INFO] Installing dependencies...
    cd frontend
    call npm install
    cd ..
)

echo [INFO] Starting React development server...
echo [INFO] Dashboard: http://localhost:3000
echo.

cd frontend
start "Marcus Frontend" cmd /c "npm run dev"
