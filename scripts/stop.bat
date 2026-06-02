@echo off
echo Stopping all Marcus services...
for %%p in (3001 8000 5173) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p " ^| findstr "LISTENING"') do (
        taskkill /PID %%a /F >nul 2>&1
        echo   Killed port %%p (PID %%a)
    )
)
echo Done.
timeout /t 1 >nul
