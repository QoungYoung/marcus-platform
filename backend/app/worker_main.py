# -*- coding: utf-8 -*-
"""Worker 进程入口 — 运行调度任务 + 各监控器 + QQ Bot，与 API 进程分离。

启动方式（与 API 同机，backend 目录下）：
    python -m app.worker_main

职责：
- 初始化数据库（幂等）+ 种子数据（与 API 相同）
- 启动 VNPy Bridge / set_bridge（供任务执行器使用）
- 启动 APScheduler 定时任务（config/tasks.yaml）
- 启动止损/加仓/候选池/长期候选池监控器
- 启动 QQ Bot 通知链路
- 每 5s 发布状态快照到 worker_status（API 进程只读）
- 每 1s 轮询 worker_commands，执行来自 API 的控制命令
"""
import os
import sys
import time
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _setup_syspath():
    """与 main.py 相同的 sys.path 引导（core/xueqiu/akshare/vnpy）。"""
    from app.config import get_settings
    settings = get_settings()
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
    if str(settings.xueqiu_dir) not in sys.path:
        sys.path.insert(0, str(settings.xueqiu_dir))
    for skill_dir in [settings.akshare_dir, settings.vnpy_dir]:
        if str(skill_dir) not in sys.path:
            sys.path.insert(0, str(skill_dir))
    return settings


def _start_bridge(settings):
    """与 main.py 相同的 bridge 初始化，返回 bridge。"""
    if settings.ENGINE_BACKEND == "vnpy":
        try:
            from app.core.trading.vnpy_bridge import VNPyBridge
            bridge = VNPyBridge(db_url=settings.DATABASE_URL)
            bridge.start()
            print("[Worker] ✅ VN.PY Bridge 已启动 (ENGINE_BACKEND=vnpy)")
            return bridge
        except Exception as e:
            print(f"[Worker] ⚠️ VN.PY Bridge 启动失败: {e}")
            return None
    print("[Worker] 使用 legacy paper engine (ENGINE_BACKEND=paper)")
    return None


def _start_services(settings):
    """初始化数据库 + bridge + 调度器 + 各监控器。"""
    from app.database import init_db
    init_db()

    from app.services.worker_control import ensure_tables
    ensure_tables()

    bridge = _start_bridge(settings)
    from app.core.trading.marcus_trade import MarcusVNPyExecutor, set_bridge
    set_bridge(bridge)

    from app.services.scheduler_service import scheduler_service
    scheduler_service.start()
    print(f"[Worker] Scheduler started - {len(scheduler_service.tasks)} tasks loaded")

    executor = MarcusVNPyExecutor(bridge=bridge, account_id="stock")

    from app.services.stop_loss_monitor import start_monitor
    start_monitor(executor=executor)

    from app.services.position_tier_monitor import start_tier_monitor
    start_tier_monitor(executor=executor)

    from app.services.candidate_pool_monitor import start_pool_monitor
    start_pool_monitor(executor=executor)

    from app.services.long_term_pool_monitor import start_lt_pool_monitor
    start_lt_pool_monitor(executor=executor)

    # 预热 PostgreSQL 连接池
    try:
        from app.database import SessionLocal
        from sqlalchemy import text as _text
        db = SessionLocal()
        db.execute(_text("SELECT 1"))
        db.close()
        print("[Worker] ✅ PostgreSQL 连接池预热完成")
    except Exception as e:
        print(f"[Worker] ⚠️ PostgreSQL 预热失败（非致命）: {e}")


def _stock_account_summary() -> dict:
    """纯 DB 的 stock 账户摘要（无网络报价），供 API 的加仓门控计算。"""
    from sqlalchemy import text as _text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(_text(
            "SELECT initial_capital, available_cash, frozen_cash "
            "FROM paper_account_info WHERE account_id = 'stock'"
        )).fetchone()
        positions = db.execute(_text(
            "SELECT volume, avg_price FROM paper_positions WHERE account_id = 'stock'"
        )).fetchall()
    finally:
        db.close()
    if not row:
        return {}
    initial = float(row.initial_capital or 0)
    available = float(row.available_cash or 0)
    frozen = float(row.frozen_cash or 0)
    position_value = sum(float(p.volume or 0) * float(p.avg_price or 0) for p in positions)
    total_asset = available + frozen + position_value
    return {
        "initial_capital": initial,
        "available_cash": available,
        "frozen_cash": frozen,
        "position_value": round(position_value, 2),
        "total_asset": round(total_asset, 2),
        "position_count": len(positions),
    }


def _publish_loop():
    """周期性把调度器/监控器状态写入 worker_status，供 API 读取。"""
    from app.services.worker_control import publish_status
    while True:
        try:
            from app.services.scheduler_service import scheduler_service
            from app.services.stop_loss_monitor import get_monitor_status, get_position_distances
            from app.services.position_tier_monitor import get_tier_status
            from app.services.candidate_pool_monitor import get_pool_monitor_status
            from app.services.long_term_pool_monitor import get_lt_pool_monitor_status
            snapshot = {
                "scheduler": scheduler_service.get_scheduler_status(),
                "tasks": scheduler_service.get_tasks(),
                "next_runs": scheduler_service.get_next_runs(),
                "stop_loss_monitor": get_monitor_status(),
                "stop_loss_distances": get_position_distances(),
                "tier_monitor": get_tier_status(),
                "candidate_pool_monitor": get_pool_monitor_status(),
                "long_term_pool_monitor": get_lt_pool_monitor_status(),
                "stock_account": _stock_account_summary(),
            }
            publish_status(snapshot)
        except Exception as e:
            print(f"[Worker] 状态快照发布失败: {e}", file=sys.stderr)
        time.sleep(5)


def _handle_command(cmd: str, payload: dict) -> dict:
    """执行来自 API 的控制命令，返回结果 dict。"""
    from app.core.trading.marcus_trade import MarcusVNPyExecutor
    from app.services.scheduler_service import scheduler_service
    from app.services.stop_loss_monitor import start_monitor, stop_monitor
    from app.services.position_tier_monitor import start_tier_monitor, stop_tier_monitor

    if cmd == "scheduler.trigger":
        return scheduler_service.trigger_task(payload.get("task_id", ""))
    if cmd == "scheduler.enable":
        return scheduler_service.enable_task(payload.get("task_id", ""))
    if cmd == "scheduler.disable":
        return scheduler_service.disable_task(payload.get("task_id", ""))
    if cmd == "scheduler.update":
        return scheduler_service.update_task(payload.get("task_id", ""), payload.get("updates") or {})
    if cmd == "scheduler.reload":
        scheduler_service.reload_config()
        return {"success": True, "message": "Configuration reloaded"}
    if cmd == "scheduler.start":
        scheduler_service.start()
        return {"success": True, "message": "Scheduler started"}
    if cmd == "scheduler.stop":
        scheduler_service.stop()
        return {"success": True, "message": "Scheduler stopped"}
    if cmd == "monitor.stop_loss.start":
        ok = start_monitor(executor=MarcusVNPyExecutor(account_id="stock"))
        return {"success": bool(ok), "message": "止损监控已启动" if ok else "启动失败"}
    if cmd == "monitor.stop_loss.stop":
        stop_monitor()
        return {"success": True, "message": "止损监控已停止"}
    if cmd == "monitor.tier.start":
        ok = start_tier_monitor(executor=MarcusVNPyExecutor(account_id="stock"))
        return {"success": bool(ok), "message": "加仓层级监控已启动" if ok else "启动失败"}
    if cmd == "monitor.tier.stop":
        stop_tier_monitor()
        return {"success": True, "message": "加仓层级监控已停止"}
    return {"success": False, "error": f"unknown command: {cmd}"}


def _command_loop():
    """轮询 worker_commands 并执行。"""
    from app.services.worker_control import cleanup_stale_commands, take_commands, finish_command
    while True:
        try:
            for row in take_commands():
                try:
                    result = _handle_command(row["cmd"], row["payload"])
                    finish_command(row["id"], bool(result.get("success")), result)
                except Exception as e:
                    finish_command(row["id"], False, {"error": str(e)})
            cleanup_stale_commands()
        except Exception as e:
            print(f"[Worker] 命令轮询异常: {e}", file=sys.stderr)
        time.sleep(1)


def _run_qq_bot(settings):
    """启动 QQ Bot 通知链路（自带重连循环）。"""
    import asyncio
    from app.services.qqbot_service import qqbot_service, send_qq_notification
    from app.services.scheduler_service import scheduler_service

    qqbot_service.set_pi_server_url(settings.PI_SERVER_URL)
    if settings.QQ_BOT_RECIPIENT:
        qqbot_service.set_default_recipient(settings.QQ_BOT_RECIPIENT)
    scheduler_service.set_qq_notifier(send_qq_notification, settings.QQ_BOT_RECIPIENT)
    while True:
        try:
            asyncio.run(qqbot_service.start(default_recipient=settings.QQ_BOT_RECIPIENT))
        except Exception as e:
            print(f"[Worker] QQ Bot 连接异常: {e}", file=sys.stderr)
            traceback_print_exc()
        time.sleep(5)


def traceback_print_exc():
    import traceback
    traceback.print_exc()


def main():
    os.chdir(_BACKEND_DIR)
    settings = _setup_syspath()

    print("[Worker] 启动 Marcus Worker 进程...")
    _start_services(settings)

    threading.Thread(target=_publish_loop, daemon=True, name="worker-status-publisher").start()
    threading.Thread(target=_command_loop, daemon=True, name="worker-command-poller").start()

    if settings.QQ_BOT_ENABLED:
        threading.Thread(target=_run_qq_bot, args=(settings,), daemon=True, name="worker-qq-bot").start()
        print("[Worker] QQ Bot 服务已调度启动")
    else:
        print("[Worker] QQ Bot 未启用（设置 QQ_BOT_ENABLED=true 以启用）")

    print("[Worker] ✅ Worker 进程就绪（调度/监控/通知在后台运行）")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
