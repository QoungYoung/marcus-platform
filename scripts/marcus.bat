@echo off
REM ============================================
REM Marcus AI Trading Platform - Main Menu
REM ============================================

:MENU
cls
echo.
echo ######################################################################
echo.
echo                    Marcus AI Trading Platform
echo.
echo ######################################################################
echo.
echo  [1] Start All Services (Backend + Frontend)
echo  [2] Start Backend Only
echo  [3] Start Frontend Only
echo  [4] Stop All Services
echo  [5] Check System Requirements
echo  [6] Install Dependencies
echo  [0] Exit
echo.
echo.

set CHOICE=
set /p CHOICE="Select option: "

if "%CHOICE%"=="1" goto START_ALL
if "%CHOICE%"=="2" goto START_BACKEND
if "%CHOICE%"=="3" goto START_FRONTEND
if "%CHOICE%"=="4" goto STOP_ALL
if "%CHOICE%"=="5" goto CHECK_SYSTEM
if "%CHOICE%"=="6" goto INSTALL
if "%CHOICE%"=="0" goto EXIT

echo.
echo [ERROR] Invalid option!
timeout /t 2 >nul
goto MENU

:START_ALL
call scripts\start_all.bat
goto MENU

:START_BACKEND
call scripts\start_backend.bat
goto MENU

:START_FRONTEND
call scripts\start_frontend.bat
goto MENU

:STOP_ALL
call scripts\stop.bat
goto MENU

:CHECK_SYSTEM
call scripts\check_system.bat
goto MENU

:INSTALL
call scripts\install_deps.bat
goto MENU

:EXIT
cls
echo.
echo Thank you for using Marcus AI Trading Platform!
echo.
pause
exit /b 0
