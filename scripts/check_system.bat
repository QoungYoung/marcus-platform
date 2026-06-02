@echo off
REM ============================================
REM Marcus Platform - System Requirements Check
REM ============================================

echo.
echo ============================================
echo Marcus Platform - System Check
echo ============================================
echo.

set ALL_OK=1

REM Check Python
echo [CHECK] Python...
where python >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('python --version 2^>^&1') do set PY_VERSION=%%i
    echo   OK - %PY_VERSION%
) else (
    echo   MISSING - Please install Python 3.10+
    set ALL_OK=0
)

REM Check Node.js
echo [CHECK] Node.js...
where node >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('node --version 2^>^&1') do set NODE_VERSION=%%i
    echo   OK - v%NODE_VERSION%
) else (
    echo   MISSING - Please install Node.js 18+
    set ALL_OK=0
)

REM Check npm
echo [CHECK] npm...
where npm >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('npm --version 2^>^&1') do set NPM_VERSION=%%i
    echo   OK - v%NPM_VERSION%
) else (
    echo   MISSING - npm not found
    set ALL_OK=0
)

REM Check Virtual Environment
echo [CHECK] Virtual Environment...
if exist "backend\venv\Scripts\python.exe" (
    echo   OK - backend\venv exists
) else (
    echo   NOT FOUND - Run scripts\install_deps.bat first
)

REM Check Node Modules
echo [CHECK] Node Modules...
if exist "frontend\node_modules" (
    echo   OK - frontend\node_modules exists
) else (
    echo   NOT FOUND - Run scripts\install_deps.bat first
)

REM Check Workspace
echo [CHECK] Marcus Workspace...
if exist "F:\pythonProject\AITrade\workspace-marcus" (
    echo   OK - workspace-marcus found
) else if exist "..\workspace-marcus" (
    echo   OK - workspace-marcus found (relative path)
) else (
    echo   WARNING - workspace-marcus not found
    echo   Tasks may not execute properly
)

echo.
echo ============================================
if %ALL_OK% equ 1 (
    echo System Ready!
    echo Run scripts\start_all.bat to begin
) else (
    echo Some requirements missing
    echo Please install the missing components
)
echo ============================================
echo.
pause
