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
import os
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

from app.api import portfolio, trades, market, news, strategy, agent, etf, db, scan, prompts, panel, indicator, backtest, pool, lt_pool
from app.api.scheduler import router as scheduler_router
from app.services.scheduler_service import scheduler_service
from app.services.qqbot_service import qqbot_service, get_qqbot_service
from app.services.stop_loss_monitor import get_monitor_status, start_monitor, stop_monitor as stop_sl_monitor
from app.services.position_tier_monitor import start_tier_monitor, stop_tier_monitor, get_tier_status
from app.services.candidate_pool_monitor import start_pool_monitor, stop_pool_monitor
from app.services.long_term_pool_monitor import start_lt_pool_monitor, stop_lt_pool_monitor
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

        # 种子 prompts（幂等，只插入不存在的；FORCE_RESEED_PROMPTS=true 时强制覆盖更新）
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            if os.environ.get('FORCE_RESEED_PROMPTS', '').lower() == 'true':
                from app.services.prompt_service import upsert_prompt
                for name, data in PROMPT_SEEDS.items():
                    upsert_prompt(db, name, data['content'], data.get('label'))
                print(f"[Main] 已强制刷新 {len(PROMPT_SEEDS)} 条 prompt（upsert）")
            else:
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

    # 启动止损监控器（自动关联 MarcusVNPyExecutor）
    try:
        from app.core.trading.marcus_trade import MarcusVNPyExecutor
        executor = MarcusVNPyExecutor()
        started = start_monitor(executor=executor)
        if started:
            print(f"[Main] ✅ 止损监控已启动 (executor=MarcusVNPyExecutor)")
        else:
            print(f"[Main] ⚠️ 止损监控启动返回 False（可能已在运行）")
    except Exception as e:
        print(f"[Main] ⚠️ 止损监控启动失败: {e}")
    
    # 启动加仓层级监控器（与止损监控并行）
    try:
        from app.core.trading.marcus_trade import MarcusVNPyExecutor
        executor = MarcusVNPyExecutor()
        started = start_tier_monitor(executor=executor)
        if started:
            print(f"[Main] ✅ 加仓层级监控已启动 (executor=MarcusVNPyExecutor)")
        else:
            print(f"[Main] ⚠️ 加仓层级监控启动返回 False（可能已在运行）")
    except Exception as e:
        print(f"[Main] ⚠️ 加仓层级监控启动失败: {e}")

    # 启动候选池监控器（与止损/加仓监控并行，30s轮询自动建仓）
    try:
        from app.core.trading.marcus_trade import MarcusVNPyExecutor
        executor = MarcusVNPyExecutor()
        started = start_pool_monitor(executor=executor)
        if started:
            print(f"[Main] ✅ 候选池监控已启动 (executor=MarcusVNPyExecutor)")
        else:
            print(f"[Main] ⚠️ 候选池监控启动返回 False（可能已在运行）")
    except Exception as e:
        print(f"[Main] ⚠️ 候选池监控启动失败: {e}")

    # 启动长期观察候选池监控器（5分钟轮询，无过期，日上限5笔）
    try:
        from app.core.trading.marcus_trade import MarcusVNPyExecutor
        executor_lt = MarcusVNPyExecutor()
        started = start_lt_pool_monitor(executor=executor_lt)
        if started:
            print(f"[Main] ✅ 长期候选池监控已启动 (executor=MarcusVNPyExecutor)")
        else:
            print(f"[Main] ⚠️ 长期候选池监控启动返回 False（可能已在运行）")
    except Exception as e:
        print(f"[Main] ⚠️ 长期候选池监控启动失败: {e}")

    # 预热 trades.db（建索引 + WAL 预热，避免首次 API 请求超时）
    try:
        import sqlite3
        db_path = settings.data_dir / "trades.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_dir_date ON trades(direction, created_at)")
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            conn.close()
            print(f"[Main] ✅ trades.db 索引预热完成")
    except Exception as e:
        print(f"[Main] ⚠️ trades.db 预热失败（非致命）: {e}")

    # 预热 K 线缓存（后台任务，避免首次 API 调用因 Tushare 超时）
    async def _warm_kline_cache():
        await asyncio.sleep(5)  # 等止损监控线程启动 + 首次持仓查询完成
        try:
            from app.services.stop_loss_monitor import get_stop_loss_monitor, _cached_fetch_kline
            from app.api.indicator import _normalize_to_ts_code
            monitor = get_stop_loss_monitor()
            if monitor.executor:
                positions = monitor.executor.get_positions()
                if positions:
                    for pos in positions:
                        try:
                            ts_code = _normalize_to_ts_code(pos.get('symbol', ''))
                            _cached_fetch_kline(ts_code)
                        except Exception:
                            pass
                    print(f"[Main] ✅ K线缓存预热完成 ({len(positions)} 只)")
        except Exception as e:
            print(f"[Main] ⚠️ K线缓存预热失败（非致命）: {e}")
    asyncio.create_task(_warm_kline_cache())

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
    try:
        stop_sl_monitor()
        print("[Main] 止损监控已停止")
    except Exception:
        pass
    try:
        stop_tier_monitor()
        print("[Main] 加仓层级监控已停止")
    except Exception:
        pass
    try:
        stop_pool_monitor()
        print("[Main] 候选池监控已停止")
    except Exception:
        pass
    try:
        stop_lt_pool_monitor()
        print("[Main] 长期候选池监控已停止")
    except Exception:
        pass
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
app.include_router(panel.router, prefix="/api/v1")
app.include_router(indicator.router, prefix="/api/v1")
app.include_router(backtest.router, prefix="/api/v1")
app.include_router(pool.router, prefix="/api/v1")
app.include_router(lt_pool.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "name": "Marcus AI Trading Platform",
        "version": "1.0.0",
        "docs": "/docs",
        "scheduler": "/api/v1/scheduler/status",
        "stop_loss_monitor": "/api/v1/scheduler/stop-loss-monitor",
        "stop_loss_distances": "/api/v1/scheduler/stop-loss-monitor/distances",
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
    try:
        monitor = get_monitor_status()
    except Exception:
        monitor = {"error": "unavailable"}
    try:
        tier = get_tier_status()
    except Exception:
        tier = {"error": "unavailable"}
    try:
        from app.services.candidate_pool_monitor import get_pool_monitor_status
        pool_monitor = get_pool_monitor_status()
    except Exception:
        pool_monitor = {"error": "unavailable"}
    try:
        from app.services.long_term_pool_monitor import get_lt_pool_monitor_status
        lt_pool_monitor = get_lt_pool_monitor_status()
    except Exception:
        lt_pool_monitor = {"error": "unavailable"}
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "scheduler": scheduler_service.get_scheduler_status(),
        "stop_loss_monitor": monitor,
        "position_tier_monitor": tier,
        "candidate_pool_monitor": pool_monitor,
        "long_term_pool_monitor": lt_pool_monitor,
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )
