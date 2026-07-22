# -*- coding: utf-8 -*-
"""长期观察候选池 — PostgreSQL 持久化，单例模式

与短期候选池的区别：
- 数据存 PostgreSQL long_term_candidates 表
- 不过期，不会自动淘汰
- 不含 maybe_capture 自动入池逻辑，仅手动管理
- 状态简化：active → promoted
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from app.database import SessionLocal
from app.models.paper_trade import LongTermCandidate

logger = logging.getLogger(__name__)


class LongTermPool:
    """长期观察候选池 — PostgreSQL 持久化，单例模式"""

    def _to_dict(self, row: LongTermCandidate) -> dict:
        return {
            "id": row.id,
            "symbol": row.symbol,
            "name": row.name,
            "status": row.status,
            "chain_name": row.chain_name,
            "chain_role": row.chain_role,
            "notes": row.notes,
            "added_at": row.added_at,
            "promoted_at": row.promoted_at,
            "last_checked_at": row.last_checked_at,
            "last_grade": row.last_grade,
            "checks_count": row.checks_count,
        }

    # ── CRUD ──

    def get_all(self) -> List[Dict[str, Any]]:
        try:
            db = SessionLocal()
            rows = db.query(LongTermCandidate).order_by(LongTermCandidate.added_at.desc()).all()
            db.close()
            return [self._to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[长期池] 查询失败: {e}")
            return []

    def get_active(self) -> List[Dict[str, Any]]:
        try:
            db = SessionLocal()
            rows = db.query(LongTermCandidate).filter(
                LongTermCandidate.status == "active"
            ).order_by(LongTermCandidate.added_at).all()
            db.close()
            return [self._to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[长期池] 查询active失败: {e}")
            return []

    def get_promoted(self) -> List[Dict[str, Any]]:
        try:
            db = SessionLocal()
            rows = db.query(LongTermCandidate).filter(
                LongTermCandidate.status == "promoted"
            ).order_by(LongTermCandidate.promoted_at.desc()).all()
            db.close()
            return [self._to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[长期池] 查询promoted失败: {e}")
            return []

    def get_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            db = SessionLocal()
            row = db.query(LongTermCandidate).filter(LongTermCandidate.symbol == symbol).first()
            db.close()
            return self._to_dict(row) if row else None
        except Exception as e:
            logger.error(f"[长期池] 查询{symbol}失败: {e}")
            return None

    def add(self, symbol: str, name: str = "", chain_name: str = "",
            chain_role: str = "", notes: str = "") -> bool:
        """添加候选标的。已存在则返回 False。"""
        try:
            db = SessionLocal()
            existing = db.query(LongTermCandidate).filter(LongTermCandidate.symbol == symbol).first()
            if existing:
                db.close()
                return False
            now = datetime.now().isoformat()
            db.add(LongTermCandidate(
                symbol=symbol,
                name=name,
                status="active",
                chain_name=chain_name,
                chain_role=chain_role,
                notes=notes,
                added_at=now,
            ))
            db.commit()
            db.close()
            return True
        except Exception as e:
            logger.error(f"[长期池] 添加{symbol}失败: {e}")
            return False

    def remove(self, symbol: str) -> bool:
        try:
            db = SessionLocal()
            result = db.query(LongTermCandidate).filter(LongTermCandidate.symbol == symbol).delete()
            db.commit()
            db.close()
            return result > 0
        except Exception as e:
            logger.error(f"[长期池] 删除{symbol}失败: {e}")
            return False

    def reset_to_active(self, symbol: str) -> bool:
        """清仓后将 promoted 重置为 active，恢复监控"""
        try:
            db = SessionLocal()
            row = db.query(LongTermCandidate).filter(
                LongTermCandidate.symbol == symbol,
                LongTermCandidate.status == "promoted"
            ).first()
            if row:
                row.status = "active"
                row.promoted_at = None
                db.commit()
                logger.info(f"[长期池] {symbol} promoted → active (仓位已清)")
            db.close()
            return row is not None
        except Exception as e:
            logger.error(f"[长期池] 重置active失败 {symbol}: {e}")
            return False

    def mark_promoted(self, symbol: str) -> bool:
        try:
            db = SessionLocal()
            row = db.query(LongTermCandidate).filter(LongTermCandidate.symbol == symbol).first()
            if row:
                row.status = "promoted"
                row.promoted_at = datetime.now().isoformat()
                db.commit()
            db.close()
            return row is not None
        except Exception as e:
            logger.error(f"[长期池] 标记promoted失败 {symbol}: {e}")
            return False

    def update_check(self, symbol: str, grade: str) -> bool:
        try:
            db = SessionLocal()
            row = db.query(LongTermCandidate).filter(LongTermCandidate.symbol == symbol).first()
            if row:
                row.last_checked_at = datetime.now().isoformat()
                row.last_grade = grade
                row.checks_count = (row.checks_count or 0) + 1
                db.commit()
            db.close()
            return row is not None
        except Exception as e:
            logger.error(f"[长期池] 更新check失败 {symbol}: {e}")
            return False

    def update_meta(self, symbol: str, notes: str = None, chain_name: str = None,
                    chain_role: str = None) -> bool:
        try:
            db = SessionLocal()
            row = db.query(LongTermCandidate).filter(LongTermCandidate.symbol == symbol).first()
            if row:
                if notes is not None:
                    row.notes = notes
                if chain_name is not None:
                    row.chain_name = chain_name
                if chain_role is not None:
                    row.chain_role = chain_role
                db.commit()
            db.close()
            return row is not None
        except Exception as e:
            logger.error(f"[长期池] 更新meta失败 {symbol}: {e}")
            return False

    def format_for_pi(self) -> str:
        """生成 Pi 策略链用的文本摘要"""
        active = self.get_active()
        promoted = self.get_promoted()
        lines = ["## 长期观察候选池"]
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
