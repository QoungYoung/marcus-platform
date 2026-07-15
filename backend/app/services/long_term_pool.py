# -*- coding: utf-8 -*-
"""长期观察候选池 — 持久化标的管理，无过期，手动增删，自动建仓。

与短期候选池的区别：
- 数据存 trades.db (SQLite)，非 JSON 文件
- 不过期，不会自动淘汰
- 不含 maybe_capture 自动入池逻辑，仅手动管理
- 状态简化：active → promoted
"""

import sqlite3
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class LongTermPool:
    """长期观察候选池 — trades.db 持久化，单例模式"""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            candidates = [
                Path(__file__).resolve().parents[3] / "data",
                Path(os.getcwd()) / "data",
            ]
            data_dir = None
            for c in candidates:
                if c.exists():
                    data_dir = str(c)
                    break
            if data_dir is None:
                data_dir = str(Path(os.getcwd()) / "data")
        self.db_path = Path(data_dir) / "trades.db"
        self._ensure_table()

    # ── 内部 ──

    def _get_conn(self, timeout: int = 30):
        conn = sqlite3.connect(str(self.db_path), timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure_table(self):
        try:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS long_term_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    name TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    chain_name TEXT DEFAULT '',
                    chain_role TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    added_at TEXT NOT NULL,
                    promoted_at TEXT,
                    last_checked_at TEXT,
                    last_grade TEXT DEFAULT '',
                    checks_count INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[长期池] 建表失败: {e}")

    def _to_dict(self, row) -> dict:
        return dict(row) if row else {}

    # ── CRUD ──

    def get_all(self) -> List[Dict[str, Any]]:
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM long_term_candidates ORDER BY added_at DESC"
            ).fetchall()
            conn.close()
            return [self._to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[长期池] 查询失败: {e}")
            return []

    def get_active(self) -> List[Dict[str, Any]]:
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM long_term_candidates WHERE status='active' ORDER BY added_at"
            ).fetchall()
            conn.close()
            return [self._to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[长期池] 查询active失败: {e}")
            return []

    def get_promoted(self) -> List[Dict[str, Any]]:
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM long_term_candidates WHERE status='promoted' ORDER BY promoted_at DESC"
            ).fetchall()
            conn.close()
            return [self._to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[长期池] 查询promoted失败: {e}")
            return []

    def get_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM long_term_candidates WHERE symbol=?", (symbol,)
            ).fetchone()
            conn.close()
            return self._to_dict(row) if row else None
        except Exception as e:
            logger.error(f"[长期池] 查询{symbol}失败: {e}")
            return None

    def add(self, symbol: str, name: str = "", chain_name: str = "",
            chain_role: str = "", notes: str = "") -> bool:
        """添加候选标的。已存在则返回 False。"""
        try:
            conn = self._get_conn()
            now = datetime.now().isoformat()
            conn.execute("""
                INSERT OR IGNORE INTO long_term_candidates
                    (symbol, name, status, chain_name, chain_role, notes, added_at)
                VALUES (?, ?, 'active', ?, ?, ?, ?)
            """, (symbol, name, chain_name, chain_role, notes, now))
            affected = conn.total_changes
            conn.commit()
            conn.close()
            return affected > 0
        except Exception as e:
            logger.error(f"[长期池] 添加{symbol}失败: {e}")
            return False

    def remove(self, symbol: str) -> bool:
        try:
            conn = self._get_conn()
            conn.execute("DELETE FROM long_term_candidates WHERE symbol=?", (symbol,))
            affected = conn.total_changes
            conn.commit()
            conn.close()
            return affected > 0
        except Exception as e:
            logger.error(f"[长期池] 删除{symbol}失败: {e}")
            return False

    def reset_to_active(self, symbol: str) -> bool:
        """清仓后将 promoted 重置为 active，恢复监控"""
        try:
            conn = self._get_conn()
            conn.execute(
                "UPDATE long_term_candidates SET status='active', promoted_at=NULL WHERE symbol=? AND status='promoted'",
                (symbol,)
            )
            affected = conn.total_changes
            conn.commit()
            conn.close()
            if affected:
                logger.info(f"[长期池] {symbol} promoted → active (仓位已清)")
            return affected > 0
        except Exception as e:
            logger.error(f"[长期池] 重置active失败 {symbol}: {e}")
            return False

    def mark_promoted(self, symbol: str) -> bool:
        try:
            conn = self._get_conn()
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE long_term_candidates SET status='promoted', promoted_at=? WHERE symbol=?",
                (now, symbol)
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"[长期池] 标记promoted失败 {symbol}: {e}")
            return False

    def update_check(self, symbol: str, grade: str) -> bool:
        try:
            conn = self._get_conn()
            now = datetime.now().isoformat()
            conn.execute("""
                UPDATE long_term_candidates
                SET last_checked_at=?, last_grade=?, checks_count=checks_count+1
                WHERE symbol=?
            """, (now, grade, symbol))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"[长期池] 更新check失败 {symbol}: {e}")
            return False

    def update_meta(self, symbol: str, notes: str = None, chain_name: str = None,
                    chain_role: str = None) -> bool:
        try:
            conn = self._get_conn()
            sets = []
            params = []
            if notes is not None:
                sets.append("notes=?")
                params.append(notes)
            if chain_name is not None:
                sets.append("chain_name=?")
                params.append(chain_name)
            if chain_role is not None:
                sets.append("chain_role=?")
                params.append(chain_role)
            if not sets:
                return False
            params.append(symbol)
            conn.execute(
                f"UPDATE long_term_candidates SET {', '.join(sets)} WHERE symbol=?",
                params
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"[长期池] 更新meta失败 {symbol}: {e}")
            return False

    def format_for_pi(self) -> str:
        """生成 Pi 策略链用的文本摘要"""
        active = self.get_active()
        promoted = self.get_promoted()
        lines = [f"## 长期观察候选池"]
        lines.append(f"- 待建仓 (active): {len(active)} 只")
        for e in active:
            extra = []
            if e.get("chain_name"):
                extra.append(e["chain_name"])
            if e.get("chain_role"):
                extra.append(e["chain_role"])
            if e.get("last_grade"):
                extra.append(f"过滤={e['last_grade']}")
            tail = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"  - {e['symbol']} {e.get('name', '')}{tail}")
        lines.append(f"- 已建仓 (promoted): {len(promoted)} 只")
        for e in promoted:
            lines.append(f"  - {e['symbol']} {e.get('name', '')} (买入于 {e.get('promoted_at', '?')})")
        return "\n".join(lines)


# ── 全局单例 ──

_pool_instance: Optional[LongTermPool] = None


def get_long_term_pool() -> LongTermPool:
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = LongTermPool()
    return _pool_instance
