@echo off
REM ============================================
REM Marcus Platform - Install Dependencies
REM ============================================

REM Change to project root directory
cd /d "%~dp0\.."

echo.
echo ============================================
echo Marcus AI Trading Platform
echo Installing Dependencies
echo ============================================
echo.

REM Create venv if not exists
if not exist "backend\venv\Scripts\python.exe" (
    echo [1/3] Creating Python virtual environment...
    python -m venv backend\venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
)

echo [2/3] Installing Python packages...
cd backend
call venv\Scripts\pip.exe install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install Python packages!
    pause
    exit /b 1
)
cd ..

echo [3/3] Installing Node.js packages...
cd frontend
call npm install
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install Node.js packages!
    pause
    exit /b 1
)
cd ..

echo.
echo ============================================
echo Installation completed!
echo ============================================
echo.
if "%1"=="" (
    echo Run scripts\start_all.bat to start all services
    echo.
    pause
)
