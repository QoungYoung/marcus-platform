# -*- coding: utf-8 -*-
"""峰值权益持久化 — PostgreSQL system_state 表。

## 键口径（2026-09-17 修复 P1：跨账户 / 跨 run 污染）

原实现是**全局单键** `peak_equity`：所有账户共用一条峰值，回放/克隆库里还会读到
**别的时期、别的账户**写下的陈旧峰值。实测克隆库 `system_state.peak_equity = 1,897,740.27`
（2026-08-26 写入）对比当前 `total_asset = 232,167` → 算出 **回撤 87.8%**
⇒ `position_tier_monitor` 第 2 道门（总回撤 ≥5% 硬禁止）首轮就把所有加仓拦掉。

现在键按 **账户**（调用方给）+ **run**（可选）分：
    `peak_equity:acct:<account_id>[:run:<run_id>]`

· 不传 `account_id`（老调用方）→ 仍用旧全局键 `peak_equity`（向后兼容，语义不变）；
· run 维度默认取环境变量 `WOLF_PEAK_EQUITY_RUN`：回测/回放启动时设它，**每个 run 从零起算**，
  不继承上一次运行的峰值；生产不设它 → 键只按账户分，跨重启稳定；
· `WOLF_PEAK_EQUITY_INHERIT_LEGACY=1` 时账户键缺失会回落读旧全局键（默认**关**：
  旧值已知是跨账户污染的脏值，只在明确要延续历史峰值时开）；
· `system_state.key` 是 `String(64)`，run id 过长（如沙箱绝对路径）会折叠成
  `run:<前12字符>~<md5前10位>` 以保键长 ≤64 且稳定。
"""
import hashlib
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_PEAK_KEY = 'peak_equity'          # 旧全局键（向后兼容）
_KEY_PREFIX = 'peak_equity'
_MAX_KEY_LEN = 64                  # system_state.key = String(64)


def _run_id(run_id=None) -> str:
    """run 维度：显式参数优先，其次环境变量 `WOLF_PEAK_EQUITY_RUN`。"""
    if run_id is not None:
        return str(run_id).strip()
    return str(os.getenv("WOLF_PEAK_EQUITY_RUN") or "").strip()


def _inherit_legacy() -> bool:
    return os.getenv("WOLF_PEAK_EQUITY_INHERIT_LEGACY", "0").strip().lower() in ("1", "true", "yes")


def peak_equity_key(account_id=None, run_id=None) -> str:
    """算 `system_state` 里的键：`peak_equity:acct:<账户>[:run:<run>]`；两者都无 → 旧全局键。"""
    acct = str(account_id or "").strip()
    run = _run_id(run_id)
    if not acct and not run:
        return _PEAK_KEY
    parts = [_KEY_PREFIX]
    if acct:
        parts.append("acct:" + acct)
    if run:
        parts.append("run:" + run)
    key = ":".join(parts)
    if len(key) > _MAX_KEY_LEN:
        # run id 可能是长路径（回测沙箱 DATA_DIR）→ 折叠成稳定短哈希
        h = hashlib.md5(run.encode("utf-8")).hexdigest()[:10]
        parts[-1] = "run:" + run[:12] + "~" + h
        key = ":".join(parts)
    return key[:_MAX_KEY_LEN]


def _read(key: str):
    """读一条键，返回 float 或 None（任何异常都视为"没有"）。"""
    try:
        from app.database import SessionLocal
        from app.models.system_state import SystemState
        db = SessionLocal()
        try:
            row = db.query(SystemState).filter(SystemState.key == key).first()
            if row is not None and row.value not in (None, ''):
                return float(row.value)
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[peak_equity] 加载失败({key}): {e}")
    return None


def _write_if_higher(key: str, equity: float) -> None:
    """权益创新高才写（同键内只增不减）。"""
    try:
        from app.database import SessionLocal
        from app.models.system_state import SystemState
        db = SessionLocal()
        try:
            row = db.query(SystemState).filter(SystemState.key == key).first()
            value_str = str(round(equity, 2))
            if row:
                prev = float(row.value) if row.value else 0.0
                if equity <= prev:
                    return  # 不是新高，不更新
                row.value = value_str
                row.updated_at = datetime.utcnow()
            else:
                db.add(SystemState(key=key, value=value_str))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[peak_equity] 保存失败({key}): {e}")


def load_peak_equity(fallback: float = 0.0, account_id=None, run_id=None) -> float:
    """加载该（账户, run）的历史峰值权益，无记录时返回 fallback。"""
    key = peak_equity_key(account_id, run_id)
    val = _read(key)
    if val is None and key != _PEAK_KEY and _inherit_legacy():
        legacy = _read(_PEAK_KEY)
        if legacy is not None:
            logger.warning(f"[peak_equity] {key} 无记录，按 WOLF_PEAK_EQUITY_INHERIT_LEGACY=1 "
                           f"回落旧全局键 {_PEAK_KEY}={legacy}")
            return legacy
    return fallback if val is None else val


def save_peak_equity(equity: float, account_id=None, run_id=None) -> None:
    """当该（账户, run）的当前权益超过其历史峰值时，更新 system_state。"""
    _write_if_higher(peak_equity_key(account_id, run_id), equity)
