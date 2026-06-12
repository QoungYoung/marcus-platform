# -*- coding: utf-8 -*-
"""
Marcus Platform Backend - FastAPI Application Entry Point
"""
# Load .env into os.environ before any config reading (must be FIRST)
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from contextlib import asynccontextmanager
from datetime import datetime
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings

# Ensure skills directories are in sys.path before any imports
settings = get_settings()
# Add platform root and core directory
platform_root = Path(__file__).parent.parent.parent
core_dir = platform_root / "core"
if str(platform_root) not in sys.path:
    sys.path.insert(0, str(platform_root))
if str(core_dir) not in sys.path:
    sys.path.insert(0, str(core_dir))
# xueqiu_dir MUST be first to avoid akshare's different xueqiu_engine shadowing it
if str(settings.xueqiu_dir) not in sys.path:
    sys.path.insert(0, str(settings.xueqiu_dir))
for skill_dir in [settings.akshare_dir, settings.vnpy_dir]:
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

from app.api import portfolio, trades, market, news, strategy, agent, etf, db, scan, prompts
from app.api.scheduler import router as scheduler_router
from app.services.scheduler_service import scheduler_service
from app.services.qqbot_service import qqbot_service, get_qqbot_service
from app.database import init_db
from app.services.prompt_service import seed_prompts
from app.db.prompt_seeds import PROMPT_SEEDS

import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - start/stop scheduler and QQ bot"""
    # Startup — 初始化数据库 + 种子数据
    try:
        print("[Main] 初始化 PostgreSQL 表结构...")
        init_db()
        print("[Main] 表结构初始化完成")

        # 种子 prompts（幂等，只插入不存在的）
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            seeded = seed_prompts(db, PROMPT_SEEDS)
            if seeded > 0:
                print(f"[Main] 已写入 {seeded} 条初始 prompt")
            else:
                print("[Main] Prompt 表已有数据，跳过种子写入")
        finally:
            db.close()
    except Exception as e:
        print(f"[Main] 数据库初始化警告（如无 PostgreSQL 可忽略）: {e}")

    # Startup
    scheduler_service.start()
    print(f"Scheduler started - {len(scheduler_service.tasks)} tasks loaded")

    # 启动 QQ Bot 监听器
    if settings.QQ_BOT_ENABLED:
        print(f"[Main] 启动 QQ Bot 服务...")
        qqbot_service.set_pi_server_url(settings.PI_SERVER_URL)
        if settings.QQ_BOT_RECIPIENT:
            qqbot_service.set_default_recipient(settings.QQ_BOT_RECIPIENT)
        # 在后台任务中启动 QQ Bot（不阻塞 lifespan）
        asyncio.create_task(qqbot_service.start(default_recipient=settings.QQ_BOT_RECIPIENT))
        print(f"[Main] QQ Bot 服务已调度启动")

        # 将 QQ 通知功能注入调度器
        from app.services.qqbot_service import send_qq_notification
        scheduler_service.set_qq_notifier(send_qq_notification, settings.QQ_BOT_RECIPIENT)
    else:
        print(f"[Main] QQ Bot 未启用（设置 QQ_BOT_ENABLED=true 以启用）")

    yield
    # Shutdown
    scheduler_service.stop()
    if settings.QQ_BOT_ENABLED:
        await qqbot_service.stop()
    print("Scheduler and QQ Bot stopped")


app = FastAPI(
    title="Marcus AI Trading Platform",
    description="大型 AI 自动交易平台 API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(portfolio.router, prefix="/api/v1")
app.include_router(trades.router, prefix="/api/v1")
app.include_router(market.router, prefix="/api/v1")
app.include_router(news.router, prefix="/api/v1")
app.include_router(strategy.router, prefix="/api/v1")
app.include_router(scheduler_router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")
app.include_router(etf.router, prefix="/api/v1")
app.include_router(db.router, prefix="/api/v1")
app.include_router(scan.router, prefix="/api/v1")
app.include_router(prompts.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "name": "Marcus AI Trading Platform",
        "version": "1.0.0",
        "docs": "/docs",
        "scheduler": "/api/v1/scheduler/status",
    }


@app.get("/api/v1/config")
async def get_config():
    """返回非敏感配置信息供前端使用"""
    return {
        "deepseek_api_key": settings.DEEPSEEK_API_KEY or None,
        "xueqiu_token": settings.XUEQIU_TOKEN or None,
    }

@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "scheduler": scheduler_service.get_scheduler_status(),
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )
