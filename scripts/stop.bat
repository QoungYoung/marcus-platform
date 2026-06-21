@echo off
echo Stopping all Marcus services...
for %%p in (3000 3001 5173 8000) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p " ^| findstr "LISTENING"') do (
        taskkill /F /T /PID %%a >nul 2>&1
        echo   Killed port %%p (PID %%a + tree)
    )
)
echo Done.
timeout /t 2 >nul
