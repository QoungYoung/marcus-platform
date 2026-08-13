# -*- coding: utf-8 -*-
"""API 进程 ↔ Worker 进程控制通道（PostgreSQL）。

拆分进程后：
- worker 进程：运行调度任务 + 各监控器 + QQ Bot，周期性发布状态快照到
  `worker_status`，并轮询 `worker_commands` 执行来自 API 的控制命令。
- API 进程：只做 HTTP。状态从 `worker_status` 读取，命令写入 `worker_commands`。

两个表由 `database.py::_apply_schema_patches()` 幂等创建。
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.database import SessionLocal

STATUS_ROW_ID = 1
# worker 心跳超过该秒数视为离线（API 不再展示其快照）
WORKER_OFFLINE_AFTER = 15


def ensure_tables() -> None:
    """幂等创建 worker 控制表（worker/API 启动时都会调用）。"""
    db = SessionLocal()
    try:
        db.execute(text(
            """
            CREATE TABLE IF NOT EXISTS worker_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                pid INTEGER,
                hostname TEXT DEFAULT '',
                heartbeat TIMESTAMPTZ NOT NULL DEFAULT now(),
                snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        ))
        db.execute(text(
            """
            CREATE TABLE IF NOT EXISTS worker_commands (
                id BIGSERIAL PRIMARY KEY,
                cmd TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                status TEXT NOT NULL DEFAULT 'pending',
                result JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                done_at TIMESTAMPTZ
            )
            """
        ))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_worker_commands_status ON worker_commands (status, id)"
        ))
        db.commit()
    finally:
        db.close()


def publish_status(snapshot: Dict[str, Any]) -> None:
    """worker 写入状态快照（单行 upsert）。"""
    db = SessionLocal()
    try:
        db.execute(text(
            """
            INSERT INTO worker_status (id, pid, hostname, heartbeat, snapshot)
            VALUES (:id, :pid, :hostname, now(), :snapshot)
            ON CONFLICT (id) DO UPDATE SET
                pid = EXCLUDED.pid,
                hostname = EXCLUDED.hostname,
                heartbeat = EXCLUDED.heartbeat,
                snapshot = EXCLUDED.snapshot
            """
        ), {
            "id": STATUS_ROW_ID,
            "pid": os.getpid(),
            "hostname": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", ""),
            "snapshot": json.dumps(snapshot, ensure_ascii=False, default=str),
        })
        db.commit()
    finally:
        db.close()


def read_status() -> Dict[str, Any]:
    """API 读取 worker 状态快照；离线时返回默认结构。"""
    db = SessionLocal()
    try:
        row = db.execute(text(
            """
            SELECT pid, hostname, heartbeat,
                   EXTRACT(EPOCH FROM (now() - heartbeat)) AS age_seconds,
                   snapshot
            FROM worker_status WHERE id = 1
            """
        )).fetchone()
    finally:
        db.close()
    if not row:
        return {"online": False, "pid": None, "heartbeat": None, "age_seconds": None, "snapshot": {}}
    age = float(row.age_seconds) if row.age_seconds is not None else None
    return {
        "online": age is not None and age < WORKER_OFFLINE_AFTER,
        "pid": row.pid,
        "hostname": row.hostname or "",
        "heartbeat": row.heartbeat.isoformat() if row.heartbeat else None,
        "age_seconds": age,
        "snapshot": dict(row.snapshot or {}),
    }


def send_command(cmd: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
    """API 写入控制命令，返回命令 id。"""
    db = SessionLocal()
    try:
        res = db.execute(text(
            "INSERT INTO worker_commands (cmd, payload) VALUES (:cmd, :payload) RETURNING id"
        ), {"cmd": cmd, "payload": json.dumps(payload or {}, ensure_ascii=False)})
        cmd_id = res.fetchone()[0]
        db.commit()
        return {"success": True, "command_id": cmd_id}
    finally:
        db.close()


def take_commands(limit: int = 10) -> List[Dict[str, Any]]:
    """worker 领取待处理命令（UPDATE 行锁，避免重复领取）。"""
    db = SessionLocal()
    try:
        rows = db.execute(text(
            """
            UPDATE worker_commands SET status = 'in_progress'
            WHERE id IN (
                SELECT id FROM worker_commands
                WHERE status = 'pending'
                ORDER BY id LIMIT :limit
            )
            RETURNING id, cmd, payload
            """
        ), {"limit": limit}).fetchall()
        db.commit()
        return [{"id": r.id, "cmd": r.cmd, "payload": dict(r.payload or {})} for r in rows]
    finally:
        db.close()


def finish_command(cmd_id: int, ok: bool, result: Dict[str, Any]) -> None:
    """worker 写回命令执行结果。"""
    db = SessionLocal()
    try:
        db.execute(text(
            """
            UPDATE worker_commands SET status = :status, result = :result, done_at = now()
            WHERE id = :id
            """
        ), {
            "status": "done" if ok else "failed",
            "result": json.dumps(result, ensure_ascii=False, default=str),
            "id": cmd_id,
        })
        db.commit()
    finally:
        db.close()


def cleanup_stale_commands() -> None:
    """清理历史命令（保留最近 1000 条，防止表无限增长）。"""
    db = SessionLocal()
    try:
        db.execute(text(
            """
            DELETE FROM worker_commands
            WHERE id NOT IN (
                SELECT id FROM worker_commands ORDER BY id DESC LIMIT 1000
            )
            """
        ))
        db.commit()
    finally:
        db.close()
