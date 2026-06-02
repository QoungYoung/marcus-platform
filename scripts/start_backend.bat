@echo off
REM ============================================
REM Marcus Platform - Backend Startup Script
REM ============================================

REM Change to project root directory
cd /d "%~dp0\.."

echo.
echo ============================================
echo Marcus AI Trading Platform - Backend
echo ============================================
echo.

REM Check if venv exists
if not exist "backend\venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found!
    echo Please run: scripts\install.bat
    pause
    exit /b 1
)

REM Check if dependencies are installed
backend\venv\Scripts\pip.exe show fastapi >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Installing dependencies...
    call scripts\install_deps.bat
)

echo [INFO] Starting FastAPI backend...
echo [INFO] API docs: http://localhost:8000/docs
echo.

REM Start backend in new window
echo [INFO] Opening backend server in a new window...
cd backend
start "Marcus Backend" cmd /c "call venv\Scripts\activate.bat && python run.py"
