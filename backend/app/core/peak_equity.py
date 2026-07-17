# -*- coding: utf-8 -*-
"""峰值权益持久化 — PostgreSQL system_state 表。"""
import logging
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger(__name__)

_PEAK_KEY = 'peak_equity'


def load_peak_equity(fallback: float = 0.0) -> float:
    """从 system_state 表加载历史峰值权益，无记录时返回 fallback。"""
    try:
        from app.database import SessionLocal
        from app.models.system_state import SystemState
        db = SessionLocal()
        try:
            row = db.query(SystemState).filter(SystemState.key == _PEAK_KEY).first()
            if row:
                return float(row.value)
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[peak_equity] 加载失败: {e}")
    return fallback


def save_peak_equity(equity: float) -> None:
    """当当前权益超过历史峰值时，更新 system_state 表。"""
    try:
        from app.database import SessionLocal
        from app.models.system_state import SystemState
        db = SessionLocal()
        try:
            row = db.query(SystemState).filter(SystemState.key == _PEAK_KEY).first()
            value_str = str(round(equity, 2))
            if row:
                prev = float(row.value) if row.value else 0.0
                if equity <= prev:
                    return  # 不是新高，不更新
                row.value = value_str
                row.updated_at = datetime.utcnow()
            else:
                db.add(SystemState(key=_PEAK_KEY, value=value_str))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[peak_equity] 保存失败: {e}")
