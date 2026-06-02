@echo off
chcp 65001 >nul
echo ============================================
echo   Marcus QQ Bot - 一键启动
echo ============================================
echo.

:: 检查 .env 中的 QQ_BOT_ENABLED 是否为 true
findstr /C:"QQ_BOT_ENABLED=true" .env >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️  QQ_BOT_ENABLED 未启用，请在 .env 中设置:
    echo    QQ_BOT_ENABLED=true
    echo    QQ_APP_ID=你的QQ机器人AppID
    echo    QQ_APP_SECRET=你的QQ机器人AppSecret
    echo    QQ_BOT_RECIPIENT=你的QQ OpenID
    pause
    exit /b 1
)

echo [1/3] 启动 Pi Server (Node.js)...
echo       端口: 3001
start "Marcus-Pi-Server" cmd /c "cd servers\pi-server && npx tsx src\index.ts"
timeout /t 3 /nobreak >nul

echo [2/3] 启动 Backend API (Python FastAPI)...
echo       端口: 8000
start "Marcus-Backend" cmd /c "cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
timeout /t 5 /nobreak >nul

echo [3/3] 启动 Frontend (Vite)...
echo       端口: 5173
start "Marcus-Frontend" cmd /c "cd frontend && npm run dev"

echo.
echo ============================================
echo   ✅ 全部服务已启动！
echo.
echo   🌐 Web UI:   http://localhost:5173
echo   📡 Backend:   http://localhost:8000/docs
echo   🤖 Pi Server: http://localhost:3001/health
echo   💬 QQ Bot:    监听中...
echo.
echo   在 QQ 中搜索你的机器人并发送消息试试吧！
echo ============================================
pause
