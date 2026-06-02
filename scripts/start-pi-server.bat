@echo off
chcp 65001 >nul
echo ============================================
echo   Marcus Pi Server
echo ============================================
echo.
echo [检查依赖] 如果首次运行，可能需要先:
echo   cd servers\pi-server && npm install
echo.
cd /d "%~dp0\..\servers\pi-server"
echo [启动] Pi Server 启动中...
echo       端口: 3001
echo       模型: DeepSeek v4-flash
echo.
npx tsx src\index.ts
