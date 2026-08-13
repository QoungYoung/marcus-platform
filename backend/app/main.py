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
# 自适应探测项目根目录（兼容本地 mancus-platform/ 和 Docker /app/ 两种结构）
# 注意：不能用 core/__init__.py 检测，因为 backend/app/core/__init__.py 也会命中
platform_root = Path(__file__).resolve().parent
for _ in range(5):
    if (platform_root / "core" / "utils" / "trade_day_utils.py").exists():
        break
    platform_root = platform_root.parent
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

from app.api import accounts
from app.api import portfolio, trades, market, news, strategy, agent, etf, db, scan, prompts, panel, indicator, backtest, pool, lt_pool, direction, golden_pit, proxy
from app.api.scheduler import router as scheduler_router
from app.api.monitor_log import router as monitor_log_router
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

    # Startup — VN.PY Bridge 单例
    bridge = None
    if settings.ENGINE_BACKEND == "vnpy":
        try:
            from app.core.trading.vnpy_bridge import VNPyBridge
            db_url = settings.DATABASE_URL
            bridge = VNPyBridge(db_url=db_url)
            bridge.start()
            app.state.vnpy_bridge = bridge
            print(f"[Main] ✅ VN.PY Bridge 已启动 (ENGINE_BACKEND=vnpy)")
        except Exception as e:
            print(f"[Main] ⚠️ VN.PY Bridge 启动失败: {e}")
            app.state.vnpy_bridge = None
    else:
        app.state.vnpy_bridge = None
        print(f"[Main] 使用 legacy paper engine (ENGINE_BACKEND=paper)")

    from app.core.trading.marcus_trade import MarcusVNPyExecutor, set_bridge
    set_bridge(bridge)  # 供 API 手动交易/查询执行器使用（调度任务已移至 worker 进程）

    # 预热 PostgreSQL 连接池（Paper trading 已迁移至 PostgreSQL）
    try:
        from app.database import engine, SessionLocal
        db = SessionLocal()
        db.execute(__import__('sqlalchemy').text("SELECT 1"))
        db.close()
        print("[Main] ✅ PostgreSQL 连接池预热完成")
    except Exception as e:
        print(f"[Main] ⚠️ PostgreSQL 预热失败（非致命）: {e}")

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

    print("[Main] ✅ API 进程就绪（调度/监控/QQ Bot 已拆分到 worker 进程）")

    yield
    # Shutdown — 调度/监控/QQ Bot 由 worker 进程负责；这里只停 bridge
    if app.state.vnpy_bridge is not None:
        try:
            app.state.vnpy_bridge.stop()
            print("[Main] VN.PY Bridge 已停止")
        except Exception:
            pass
    print("API process stopped")


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
app.include_router(accounts.router, prefix="/api/v1")
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
app.include_router(monitor_log_router, prefix="/api/v1")
app.include_router(direction.router, prefix="/api/v1")
app.include_router(golden_pit.router, prefix="/api/v1")
app.include_router(proxy.router, prefix="/api/v1")

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
        "deepseek_api_host": settings.DEEPSEEK_API_HOST or None,
        "deepseek_model": settings.DEEPSEEK_MODEL or None,
        "xueqiu_token": settings.XUEQIU_TOKEN or None,
    }

@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint（调度/监控状态来自 worker 进程快照）。"""
    from app.services.worker_control import read_status
    st = read_status()
    snap = st.get("snapshot", {}) if st.get("online") else {}
    offline = {"running": False, "error": "worker offline"}
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "scheduler": snap.get("scheduler") or offline,
        "stop_loss_monitor": snap.get("stop_loss_monitor") or {"running": False},
        "position_tier_monitor": snap.get("tier_monitor") or {"running": False},
        "candidate_pool_monitor": snap.get("candidate_pool_monitor") or {"running": False},
        "long_term_pool_monitor": snap.get("long_term_pool_monitor") or {"running": False},
        "worker": {
            "online": st.get("online"),
            "pid": st.get("pid"),
            "hostname": st.get("hostname"),
            "age_seconds": st.get("age_seconds"),
        },
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )
