# -*- coding: utf-8 -*-
"""模型更新调度器 v3：月度定时重训 + 紧急触发器。

重训策略：
  - 月度定时：每月最后一个交易日 16:00 后触发，250 天数据，复用 Optuna 超参
  - 紧急重训：RiskManager 连续 3 日跑输信号触发，60 天数据，< 1 分钟
  - 频率限制：紧急重训 ≤ 1 次/5 个交易日
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ModelUpdateScheduler:
    """月度重训 + 紧急重训调度。

    不负责实际训练逻辑，只负责判断是否该重训。
    实际训练由 DirectionPredictionService.train_walk_forward() 执行。
    """

    def __init__(self, risk_manager=None):
        """
        Args:
            risk_manager: RiskManager 实例（用于 emergency_check）
        """
        self._risk_manager = risk_manager
        self._last_monthly_retrain: Optional[str] = None
        self._last_emergency_retrain: Optional[str] = None
        self._cached_optuna_params: Optional[dict] = None

    @property
    def risk_manager(self):
        if self._risk_manager is None:
            from app.services.risk_manager import RiskManager
            self._risk_manager = RiskManager()
        return self._risk_manager

    # ── 重训判断 ─────────────────────────────────────────

    def should_retrain(self, date: str, trading_days: Optional[list] = None) -> Tuple[bool, str]:
        """判断当前日期是否应触发重训。

        优先级：紧急重训 > 月度定时重训

        Args:
            date: 当前日期 YYYYMMDD
            trading_days: 当月所有交易日列表（用于判断月末），为 None 时使用简单日期判断

        Returns:
            (should_retrain, reason)
        """
        # 1. 检查紧急重训
        if self.risk_manager.emergency_check():
            if self._can_emergency_retrain(date):
                return True, "emergency"
            else:
                return False, "emergency_rate_limited"

        # 2. 检查月度定时
        if self._is_month_end(date, trading_days):
            return True, "monthly"

        return False, "not_scheduled"

    def _is_month_end(self, date: str, trading_days: Optional[list] = None) -> bool:
        """判断是否月末交易日。

        Args:
            date: 当前日期 YYYYMMDD
            trading_days: 当月交易日列表
        """
        if trading_days:
            return date == max(trading_days)

        # 简易判断：是否是当月最后一天
        try:
            dt = datetime.strptime(date, "%Y%m%d")
            next_day = dt + timedelta(days=1)
            return next_day.month != dt.month
        except ValueError:
            return False

    def _can_emergency_retrain(self, date: str) -> bool:
        """紧急重训频率限制：距上次至少 5 个交易日。"""
        return self.risk_manager.can_emergency_retrain(date)

    # ── 重训执行参数 ──────────────────────────────────────

    def get_retrain_params(self, reason: str) -> dict:
        """根据重训原因返回参数。

        Args:
            reason: "monthly" 或 "emergency"

        Returns:
            {train_days, n_trials, reuse_params, ...}
        """
        if reason == "emergency":
            return {
                "train_days": 60,
                "step": 5,
                "n_trials": 0,        # 复用已有超参，不重新搜索
                "use_cached_params": True,
                "description": "紧急重训: 60天数据, 60天滚动窗口, 复用Optuna超参",
            }
        else:
            return {
                "train_days": 250,
                "step": 10,
                "n_trials": 0,        # 月度复用已有超参
                "use_cached_params": True,
                "description": "月度定时重训: 250天数据, 120天滚动窗口, 复用Optuna超参",
            }

    # ── 参数缓存 ─────────────────────────────────────────

    def cache_optuna_params(self, params: dict):
        """缓存 Optuna 超参，供后续重训复用。"""
        self._cached_optuna_params = params
        logger.info(f"Optuna 超参已缓存: {list(params.keys())}")

    def get_cached_params(self) -> Optional[dict]:
        """获取缓存的 Optuna 超参。"""
        return self._cached_optuna_params

    # ── 状态记录 ─────────────────────────────────────────

    def mark_retrain_complete(self, date: str, reason: str):
        """记录重训完成。"""
        if reason == "emergency":
            self._last_emergency_retrain = date
            self.risk_manager.mark_emergency_retrain(date)
        else:
            self._last_monthly_retrain = date
        logger.info(f"重训完成 [{reason}] @ {date}")

    def get_status(self) -> dict:
        """返回调度状态。"""
        return {
            "last_monthly_retrain": self._last_monthly_retrain,
            "last_emergency_retrain": self._last_emergency_retrain,
            "has_cached_params": self._cached_optuna_params is not None,
            "risk_status": self.risk_manager.get_status(),
        }

    def get_days_since_last_emergency(self, date: str) -> Optional[int]:
        """距上次紧急重训的日历天数。"""
        if self._last_emergency_retrain is None:
            return None
        try:
            last = datetime.strptime(self._last_emergency_retrain, "%Y%m%d")
            curr = datetime.strptime(date, "%Y%m%d")
            return (curr - last).days
        except ValueError:
            return None
